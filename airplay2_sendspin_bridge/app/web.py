"""Web UI (HA ingress) for automatic calibration.

Flow on the calibration page (in the browser):
  1. A click track (1 click per second) plays via AirPlay to the group
     "speaker + reference speaker" (HomePod/Sonos).
  2. The browser records through the microphone. The page first mutes the
     zone (phase A: reference speaker only), then unmutes it (phase B: both).
  3. Folding the recording onto 1 s gives the click position of the
     reference (A) and of the speaker (difference B − A) → offset in ms.
  4. "Apply" sets latency_offset_ms live and saves it in the add-on options.

Security: the add-on runs in the host network, so the port would be
reachable on the LAN. Only the Supervisor ingress proxy is allowed through.
"""

from __future__ import annotations

import io
import json
import logging
import math
import os
import struct
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


async def save_offset_to_options(index: int, offset_ms: int) -> bool:
    """Save latency_offset_ms permanently in the add-on options (Supervisor API)."""
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        return False
    try:
        opts = json.loads(OPTIONS_FILE.read_text(encoding="utf-8"))
        opts["zones"][index]["latency_offset_ms"] = int(offset_ms)
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
    click_wav = build_click_wav()

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

    async def click(_request: web.Request) -> web.Response:
        return web.Response(
            body=click_wav,
            content_type="audio/wav",
            headers={"Content-Disposition": 'attachment; filename="sync-click.wav"'},
        )

    async def list_zones(_request: web.Request) -> web.Response:
        return web.json_response(
            [
                {
                    "index": z.cfg.index,
                    "name": z.cfg.name,
                    "offset_ms": z.cfg.latency_offset_ms,
                    "lead_ms": z.lead_ms(),
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
            saved = await save_offset_to_options(zone.cfg.index, offset)
        return web.json_response({"offset_ms": offset, "saved": saved})

    app.router.add_get("/", index)
    app.router.add_get("/sync-click.wav", click)
    app.router.add_get("/api/zones", list_zones)
    app.router.add_post("/api/zones/{index}/mute", set_mute)
    app.router.add_post("/api/zones/{index}/offset", set_offset)
    return app


async def start_web(zones: list[Zone]) -> web.AppRunner:
    runner = web.AppRunner(create_app(zones), access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=INGRESS_PORT)  # noqa: S104
    await site.start()
    log.info("Calibration page running (ingress, port %s)", INGRESS_PORT)
    return runner
