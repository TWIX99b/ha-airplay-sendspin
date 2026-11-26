"""Web UI (HA ingress) for automatic calibration.

Flow on the calibration page (in the browser):
  1. A click track (1 click per second) plays via AirPlay to the group
     "speaker + reference speaker" (HomePod/Sonos).
  2. The browser records through the microphone. The page first mutes the
     zone (phase A: reference speaker only), then unmutes it (phase B: both).
  3. Folding the recording onto 1 s gives the click position of the
     reference (A) and of the speaker (difference B − A) → offset in ms.
  4. "Apply" sets latency_offset_ms live and saves it in the add-on options.

Volume matching works the same way with a noise track (1.5 s pink noise,
3.5 s silence): during the noise the zone stays silent, in the silence the
bridge replays that noise on the zone. The page compares the two levels and
sets volume_trim_db.

Security: the add-on runs in the host network, so the port would be
reachable on the LAN. Only the Supervisor ingress proxy is allowed through.
"""

from __future__ import annotations

import io
import json
import logging
import math
import os
import random
import struct
import tempfile
import wave
from pathlib import Path
from typing import TYPE_CHECKING

import aiohttp
from aiohttp import web

if TYPE_CHECKING:
    from bridge import Zone

log = logging.getLogger("web")

INGRESS_PORT = 8099
ALLOWED_PEERS = {"172.30.32.2", "127.0.0.1", "::1"}  # Supervisor ingress proxy
STATIC = Path(__file__).parent / "static"
OPTIONS_FILE = Path("/data/options.json")

CLICK_RATE = 48_000
CLICK_SECONDS = 90


def build_click_wav() -> bytes:
    """90 s, one short broadband click per second (48 kHz, 16 bit, mono)."""
    n_click = int(CLICK_RATE * 0.004)  # 4 ms
    click = []
    for i in range(n_click):
        t = i / CLICK_RATE
        win = 0.5 - 0.5 * math.cos(2 * math.pi * i / (n_click - 1))  # Hann
        val = (
            math.sin(2 * math.pi * 2500 * t)
            + math.sin(2 * math.pi * 4100 * t)
            + math.sin(2 * math.pi * 6300 * t)
        ) / 3
        click.append(int(0.9 * 32767 * win * val))
    silence = b"\x00\x00" * (CLICK_RATE - n_click)
    click_bytes = struct.pack(f"<{n_click}h", *click)
    second = click_bytes + silence
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(CLICK_RATE)
        wav.writeframes(second * CLICK_SECONDS)
    return buf.getvalue()


LEVEL_CYCLE_S = 5.0
LEVEL_NOISE_S = 1.5


def _biquad(x: list[float], b: tuple, a: tuple) -> list[float]:
    b0, b1, b2 = b
    _, a1, a2 = a
    y, x1, x2, y1, y2 = [], 0.0, 0.0, 0.0, 0.0
    for v in x:
        out = b0 * v + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
        x2, x1, y2, y1 = x1, v, y1, out
        y.append(out)
    return y


def _rbj(kind: str, f0: float, sr: int, q: float = 0.7071) -> tuple:
    w = 2 * math.pi * f0 / sr
    alpha, cw = math.sin(w) / (2 * q), math.cos(w)
    if kind == "hp":
        b = ((1 + cw) / 2, -(1 + cw), (1 + cw) / 2)
    else:
        b = ((1 - cw) / 2, 1 - cw, (1 - cw) / 2)
    a0 = 1 + alpha
    return (
        tuple(v / a0 for v in b),
        (1.0, -2 * cw / a0, (1 - alpha) / a0),
    )


def build_level_wav() -> bytes:
    """90 s: 1.5 s pink noise (200 Hz – 6 kHz, −18 dBFS RMS), 3.5 s silence."""
    rnd = random.Random(20261126)
    n = int(CLICK_RATE * LEVEL_NOISE_S)
    b0 = b1 = b2 = b3 = b4 = b5 = b6 = 0.0
    pink = []
    for _ in range(n + 4800):  # Paul Kellet's pink-noise filter, 100 ms run-in
        w = rnd.gauss(0, 1)
        b0 = 0.99886 * b0 + w * 0.0555179
        b1 = 0.99332 * b1 + w * 0.0750759
        b2 = 0.96900 * b2 + w * 0.1538520
        b3 = 0.86650 * b3 + w * 0.3104856
        b4 = 0.55000 * b4 + w * 0.5329522
        b5 = -0.7616 * b5 - w * 0.0168980
        pink.append(b0 + b1 + b2 + b3 + b4 + b5 + b6 + w * 0.5362)
        b6 = w * 0.115926
    for kind, f0 in (("hp", 200), ("hp", 200), ("lp", 6000), ("lp", 6000)):
        pink = _biquad(pink, *_rbj(kind, f0, CLICK_RATE))
    pink = pink[4800:]
    rms = math.sqrt(sum(v * v for v in pink) / n)
    gain = 32767 * 10 ** (-18 / 20) / rms
    fade = int(CLICK_RATE * 0.02)
    samples = []
    for i, v in enumerate(pink):
        env = min(1.0, i / fade, (n - 1 - i) / fade)
        samples.append(max(-32767, min(32767, int(v * gain * env))))
    silence = b"\x00\x00" * (int(CLICK_RATE * LEVEL_CYCLE_S) - n)
    cycle = struct.pack(f"<{n}h", *samples) + silence
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(CLICK_RATE)
        wav.writeframes(cycle * int(CLICK_SECONDS / LEVEL_CYCLE_S))
    return buf.getvalue()


async def save_zone_option(index: int, key: str, value: float) -> bool:
    """Save one zone value permanently in the add-on options (Supervisor API)."""
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        return False
    try:
        opts = json.loads(OPTIONS_FILE.read_text(encoding="utf-8"))
        opts["zones"][index][key] = value
        async with (
            aiohttp.ClientSession() as session,
            session.post(
                "http://supervisor/addons/self/options",
                headers={"Authorization": f"Bearer {token}"},
                json={"options": opts},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp,
        ):
            if resp.status != 200:
                log.warning("Saving the options failed: HTTP %s", resp.status)
                return False
        OPTIONS_FILE.write_text(json.dumps(opts), encoding="utf-8")
        return True
    except (OSError, KeyError, IndexError, aiohttp.ClientError, TimeoutError) as err:
        log.warning("Saving the options failed: %s", err)
        return False


def create_app(zones: list[Zone]) -> web.Application:
    # As files, so FileResponse can answer Range requests (Safari needs them
    # to play audio in the page).
    track_dir = Path(tempfile.mkdtemp(prefix="ap2-tracks-"))
    click_file = track_dir / "sync-click.wav"
    level_file = track_dir / "level-noise.wav"
    click_file.write_bytes(build_click_wav())
    level_file.write_bytes(build_level_wav())

    @web.middleware
    async def only_ingress(request: web.Request, handler):
        if request.remote not in ALLOWED_PEERS:
            raise web.HTTPForbidden(text="only via Home Assistant ingress")
        return await handler(request)

    app = web.Application(middlewares=[only_ingress])

    def zone_or_404(request: web.Request) -> Zone:
        try:
            return zones[int(request.match_info["index"])]
        except (ValueError, IndexError):
            raise web.HTTPNotFound(text="no such zone") from None

    async def index(_request: web.Request) -> web.Response:
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        return web.Response(text=html, content_type="text/html")

    async def click(_request: web.Request) -> web.FileResponse:
        return web.FileResponse(click_file, headers={"Content-Type": "audio/wav"})

    async def level_track(_request: web.Request) -> web.FileResponse:
        return web.FileResponse(level_file, headers={"Content-Type": "audio/wav"})

    async def list_zones(_request: web.Request) -> web.Response:
        return web.json_response(
            [
                {
                    "index": z.cfg.index,
                    "name": z.cfg.name,
                    "offset_ms": z.cfg.latency_offset_ms,
                    "lead_ms": z.lead_ms(),
                    "trim_db": z.cfg.volume_trim_db,
                    "speaker_volume": z.echo_volume,
                    "speaker_volume_target": z.speaker_volume,
                    "playing": z.streaming,
                }
                for z in zones
            ]
        )

    async def set_mute(request: web.Request) -> web.Response:
        zone = zone_or_404(request)
        body = await request.json()
        if not zone.streaming:
            raise web.HTTPConflict(text="zone is not playing over AirPlay")
        zone.cal_mute = bool(body.get("mute"))
        return web.json_response({"mute": zone.cal_mute})

    async def set_offset(request: web.Request) -> web.Response:
        zone = zone_or_404(request)
        body = await request.json()
        offset = int(body["offset_ms"])
        if not -1000 <= offset <= 1000:
            raise web.HTTPBadRequest(text="offset must be within ±1000 ms")
        zone.set_offset(offset)
        saved = False
        if body.get("save"):
            saved = await save_zone_option(zone.cfg.index, "latency_offset_ms", offset)
        return web.json_response({"offset_ms": offset, "saved": saved})

    async def set_level(request: web.Request) -> web.Response:
        zone = zone_or_404(request)
        body = await request.json()
        on = bool(body.get("on"))
        if on and not zone.streaming:
            raise web.HTTPConflict(text="zone is not playing over AirPlay")
        zone.set_level_mode(on)
        return web.json_response({"on": zone.level_mode})

    async def set_trim(request: web.Request) -> web.Response:
        zone = zone_or_404(request)
        body = await request.json()
        trim = round(float(body["trim_db"]), 1)
        if not -30 <= trim <= 0:
            raise web.HTTPBadRequest(text="trim must be within −30 … 0 dB")
        zone.set_trim(trim)
        saved = False
        if body.get("save"):
            saved = await save_zone_option(zone.cfg.index, "volume_trim_db", trim)
        return web.json_response({"trim_db": trim, "saved": saved})

    app.router.add_get("/", index)
    app.router.add_get("/sync-click.wav", click)
    app.router.add_get("/level-noise.wav", level_track)
    app.router.add_get("/api/zones", list_zones)
    app.router.add_post("/api/zones/{index}/mute", set_mute)
    app.router.add_post("/api/zones/{index}/offset", set_offset)
    app.router.add_post("/api/zones/{index}/level", set_level)
    app.router.add_post("/api/zones/{index}/trim", set_trim)
    return app


async def start_web(zones: list[Zone]) -> web.AppRunner:
    runner = web.AppRunner(create_app(zones), access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=INGRESS_PORT)  # noqa: S104
    await site.start()
    log.info("Calibration page running (ingress, port %s)", INGRESS_PORT)
    return runner
