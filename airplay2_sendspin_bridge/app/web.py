"""Web UI (HA ingress): speakers and automatic calibration.

Speakers tab: tiles with status, track, offset and volume; add speakers
suggested from Home Assistant (echo-dot-assist devices), edit and remove
them. They are stored in the add-on options (speakers.py).

Calibration page, mode "by ear": the click track plays via AirPlay to the
group "speaker + reference"; ± buttons change latency_offset_ms live, Save
stores it. Mode "with microphone" (best on a second device, 3 rounds):
  1. A click track (1 click per second, 10 min) plays via AirPlay to the group
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

import asyncio
import hashlib
import io
import json
import logging
import math
import os
import random
import secrets
import struct
import tempfile
import time
import wave
from pathlib import Path
from typing import TYPE_CHECKING

import aiohttp
import speakers
from aiohttp import web
from ha import TOKEN_SUFFIX, disabled_token_entities, enable_entity, render_template

if TYPE_CHECKING:
    from bridge import Zone

log = logging.getLogger("web")

INGRESS_PORT = 8099
ALLOWED_PEERS = {"172.30.32.2", "127.0.0.1", "::1"}  # Supervisor ingress proxy
INT_PREFIX = "/int/"  # API of the Home Assistant integration (token protected)
TOKEN_FILE = Path("/data/integration_token")
DISCOVERY_SERVICE = "airplay_bridge"  # = domain of the integration
STATIC = Path(__file__).parent / "static"
OPTIONS_FILE = Path("/data/options.json")

BOOT_ID = f"{time.time():.0f}-{os.getpid()}"  # changes with every (re)start
# 10 minutes without a loop: a loop restart makes iOS flush AirPlay, which
# used to disturb measurements. 22.05 kHz keeps the file small (≈26 MB,
# streamed) and still has the full click (2.5–6.3 kHz).
CLICK_RATE = 22_050
CLICK_SECONDS = 600


def build_click_wav() -> bytes:
    """10 min, one short broadband click per second (16 bit, mono)."""
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


LEVEL_RATE = 48_000  # the bridge analyses this track at 48 kHz
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
    n = int(LEVEL_RATE * LEVEL_NOISE_S)
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
        pink = _biquad(pink, *_rbj(kind, f0, LEVEL_RATE))
    pink = pink[4800:]
    rms = math.sqrt(sum(v * v for v in pink) / n)
    gain = 32767 * 10 ** (-18 / 20) / rms
    fade = int(LEVEL_RATE * 0.02)
    samples = []
    for i, v in enumerate(pink):
        env = min(1.0, i / fade, (n - 1 - i) / fade)
        samples.append(max(-32767, min(32767, int(v * gain * env))))
    silence = b"\x00\x00" * (int(LEVEL_RATE * LEVEL_CYCLE_S) - n)
    cycle = struct.pack(f"<{n}h", *samples) + silence
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(LEVEL_RATE)
        wav.writeframes(cycle * int(CLICK_SECONDS / LEVEL_CYCLE_S))
    return buf.getvalue()


IP_CONFLICT_FILE = Path("/run/ap2/ip_conflict")


def read_ip_conflict() -> dict | None:
    """Written at start by 10-network.sh if the AirPlay IP is not free."""
    try:
        ip, _, by = IP_CONFLICT_FILE.read_text(encoding="utf-8").strip().partition(" ")
    except OSError:
        return None
    return {"ip": ip, "by": by or "unknown"} if ip else None


def read_options() -> dict:
    return json.loads(OPTIONS_FILE.read_text(encoding="utf-8"))


async def save_options(opts: dict) -> bool:
    """Save the whole add-on options (Supervisor API)."""
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        return False
    try:
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
                log.warning(
                    "Saving the options failed: HTTP %s %s",
                    resp.status,
                    (await resp.text())[:300],
                )
                return False
        OPTIONS_FILE.write_text(json.dumps(opts), encoding="utf-8")
        return True
    except (OSError, aiohttp.ClientError, TimeoutError) as err:
        log.warning("Saving the options failed: %s", err)
        return False


async def save_zone_option(index: int, key: str, value: float) -> bool:
    """Save one zone value permanently in the add-on options."""
    try:
        opts = read_options()
        opts["zones"][index][key] = value
    except (OSError, ValueError, KeyError, IndexError) as err:
        log.warning("Saving the options failed: %s", err)
        return False
    return await save_options(opts)


async def restart_self(delay: float = 0.8) -> None:
    """Restart the add-on (new speakers need new AirPlay receivers)."""
    await asyncio.sleep(delay)  # let the HTTP answer go out first
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        return
    log.info("Speakers changed – restarting the add-on")
    try:
        async with (
            aiohttp.ClientSession() as session,
            session.post(
                "http://supervisor/addons/self/restart",
                headers={"Authorization": f"Bearer {token}"},
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp,
        ):
            log.info("Restart requested: HTTP %s", resp.status)
    except (aiohttp.ClientError, TimeoutError) as err:
        log.warning("Restart failed: %s", err)


def cover_key(cover: bytes | None) -> str | None:
    return hashlib.sha1(cover).hexdigest()[:12] if cover else None


def zone_state(z) -> dict:
    """Everything the page and the integration show about one speaker."""
    t = z.track
    return {
        "index": z.cfg.index,
        "name": z.cfg.name,
        "key": z.cfg.key,
        "speakers": [
            {
                "label": link.cfg.label,
                "host": link.cfg.host,
                "channel": link.cfg.channel,
                "paired": link.client_id is not None,
                "token_entity": link.cfg.token_entity,
            }
            for link in z.links
        ],
        "track": (
            {"title": t.title, "artist": t.artist, "album": t.album} if t else None
        ),
        "cover": cover_key(t.cover if t else None),
        "offset_ms": z.cfg.latency_offset_ms,
        "lead_ms": z.lead_ms(),
        "sync_locked": bool(getattr(z, "sync_locked", False)),
        "sync_error_ms": round(float(getattr(z, "sync_error_ms", 0.0)), 1),
        "trim_db": z.cfg.volume_trim_db,
        "speaker_volume": z.echo_volume,
        "speaker_volume_target": z.target_volume(),
        "airplay_volume": z.cfg.speaker_volume,
        "default_volume": z.default_volume,
        "night_volume": z.cfg.night_volume,
        "night_start": z.cfg.night_start,
        "night_end": z.cfg.night_end,
        "night_enabled": z.cfg.night_enabled,
        "night_active": z.night_active(),
        "model": z.model,
        "manufacturer": z.manufacturer,
        "stats": z.stats,
        "dropouts": int(
            z.stats.get("resyncs", 0) + z.stats.get("drops", 0) + z.stats.get("lost", 0)
        ),
        "playing": z.streaming,
    }


def load_token() -> str:
    """Secret the integration needs for /int/ (created once, kept in /data)."""
    try:
        token = TOKEN_FILE.read_text(encoding="utf-8").strip()
        if len(token) >= 32:
            return token
    except OSError:
        pass
    token = secrets.token_urlsafe(32)
    try:
        TOKEN_FILE.write_text(token, encoding="utf-8")
    except OSError as err:
        log.warning("Integration token not saved: %s", err)
    return token


async def announce_to_ha(token: str) -> None:
    """Tell Home Assistant about us: it offers to set up the integration."""
    log.info(
        "HA integration: normally found automatically. Manual setup: host "
        '127.0.0.1, port %d, token on the add-on page (bottom, "Home Assistant")',
        INGRESS_PORT,
    )
    sup = os.environ.get("SUPERVISOR_TOKEN")
    if not sup:
        return
    config = {"host": "127.0.0.1", "port": INGRESS_PORT, "token": token}
    try:
        async with (
            aiohttp.ClientSession() as session,
            session.post(
                "http://supervisor/discovery",
                headers={"Authorization": f"Bearer {sup}"},
                json={"service": DISCOVERY_SERVICE, "config": config},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp,
        ):
            if resp.status == 200:
                log.info(
                    "Announced to Home Assistant (integration '%s')", DISCOVERY_SERVICE
                )
            else:
                log.warning(
                    "Discovery failed: HTTP %s %s", resp.status, await resp.text()
                )
    except (aiohttp.ClientError, TimeoutError) as err:
        log.warning("Discovery failed: %s", err)


ALLOWED_SETTINGS = {
    "latency_offset_ms",
    "volume_trim_db",
    "speaker_volume",
    "night_enabled",
}


async def update_zone_settings(zone, changes: dict) -> dict:
    """Validate, save and apply live changes of one speaker (no restart)."""
    opts = read_options()
    old = opts["zones"][zone.cfg.index]
    new = dict(old)
    for key, value in changes.items():
        if key not in ALLOWED_SETTINGS:
            raise ValueError(f"unknown setting {key}")
        if value is None:
            new.pop(key, None)
        else:
            new[key] = value
    cleaned = speakers.clean_zone({**new, "orig": zone.cfg.index}, old)
    opts["zones"][zone.cfg.index] = cleaned
    if not await save_options(opts):
        raise RuntimeError("saving the options failed")
    zone.apply_settings(cleaned)
    return cleaned


def create_app(zones: list[Zone], token: str = "") -> web.Application:
    # As files, so FileResponse can answer Range requests (Safari needs them
    # to play audio in the page).
    track_dir = Path(tempfile.mkdtemp(prefix="ap2-tracks-"))
    click_file = track_dir / "sync-click.wav"
    level_file = track_dir / "level-noise.wav"
    click_file.write_bytes(build_click_wav())
    level_file.write_bytes(build_level_wav())

    @web.middleware
    async def only_ingress(request: web.Request, handler):
        if request.path.startswith(INT_PREFIX):
            given = request.headers.get("Authorization", "").removeprefix("Bearer ")
            if not token or not secrets.compare_digest(given, token):
                raise web.HTTPUnauthorized()
            return await handler(request)
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
        return web.json_response([zone_state(z) for z in zones])

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

    async def cover(request: web.Request) -> web.Response:
        zone = zone_or_404(request)
        data = zone.track.cover if zone.track else None
        if not data:
            raise web.HTTPNotFound()
        kind = "image/png" if data[:4] == b"\x89PNG" else "image/jpeg"
        return web.Response(
            body=data, content_type=kind, headers={"Cache-Control": "max-age=3600"}
        )

    async def get_config(_request: web.Request) -> web.Response:
        opts = read_options()
        return web.json_response(
            {
                "boot": BOOT_ID,
                "integration": {
                    "host": "127.0.0.1",
                    "port": INGRESS_PORT,
                    "token": token,
                },
                "ip_conflict": read_ip_conflict(),
                "zones": [speakers.public_zone(z) for z in opts.get("zones", [])],
            }
        )

    async def put_config(request: web.Request) -> web.Response:
        body = await request.json()
        opts = read_options()
        old = opts.get("zones", [])
        try:
            new = speakers.clean_all(body.get("zones"), old)
        except (ValueError, TypeError) as err:
            raise web.HTTPBadRequest(text=str(err)) from None
        restart = speakers.needs_restart(old, new)
        opts["zones"] = new
        if not await save_options(opts):
            raise web.HTTPBadGateway(text="saving the options failed")
        if restart:
            asyncio.ensure_future(restart_self())
        else:  # only offset / trim / volume settings changed: apply live
            for zone, cfg in zip(zones, new, strict=False):
                zone.apply_settings(cfg)
        return web.json_response({"restart": restart})

    async def discover(_request: web.Request) -> web.Response:
        rendered = await render_template(speakers.DISCOVERY_TEMPLATE)
        if rendered is None:
            raise web.HTTPBadGateway(text="Home Assistant not reachable")
        found = speakers.parse_discovery(rendered, read_options().get("zones", []))
        found = speakers.add_disabled(found, await disabled_token_entities())
        return web.json_response(found)

    async def enable_token(request: web.Request) -> web.Response:
        body = await request.json()
        entity_id = str(body.get("entity_id", ""))
        # only ever the Sendspin pairing-token sensors, nothing else
        if not (entity_id.startswith("sensor.") and entity_id.endswith(TOKEN_SUFFIX)):
            raise web.HTTPBadRequest(text="not a Sendspin pairing-token entity")
        result = await enable_entity(entity_id)
        if result is None:
            raise web.HTTPBadGateway(text="Home Assistant did not enable the entity")
        return web.json_response({"ok": True, **result})

    # ---- API of the Home Assistant integration (token, see middleware) ----
    sockets: set[web.WebSocketResponse] = set()
    pending = {"handle": None}

    def state_message() -> str:
        return json.dumps({"type": "state", "zones": [zone_state(z) for z in zones]})

    def broadcast() -> None:
        pending["handle"] = None
        msg = state_message()
        for ws in list(sockets):
            if ws.closed:
                sockets.discard(ws)
            else:
                asyncio.ensure_future(ws.send_str(msg))

    def on_change(_zone) -> None:
        if pending["handle"] is None and sockets:  # collect changes for 0.2 s
            pending["handle"] = asyncio.get_running_loop().call_later(0.2, broadcast)

    for z in zones:
        z.add_listener(on_change)

    async def int_state(_request: web.Request) -> web.Response:
        return web.json_response(
            {
                "version": os.environ.get("ADDON_VERSION", "dev"),
                "zones": [zone_state(z) for z in zones],
            }
        )

    async def int_ws(request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=30)
        await ws.prepare(request)
        sockets.add(ws)
        try:
            await ws.send_str(state_message())
            async for _msg in ws:  # the integration only listens
                pass
        finally:
            sockets.discard(ws)
        return ws

    async def int_set(request: web.Request) -> web.Response:
        zone = zone_or_404(request)
        body = await request.json()
        try:
            cleaned = await update_zone_settings(zone, body)
        except ValueError as err:
            raise web.HTTPBadRequest(text=str(err)) from None
        except RuntimeError as err:
            raise web.HTTPBadGateway(text=str(err)) from None
        return web.json_response(speakers.public_zone(cleaned))

    app.router.add_get("/", index)
    app.router.add_get("/api/config", get_config)
    app.router.add_put("/api/config", put_config)
    app.router.add_get("/api/discover", discover)
    app.router.add_post("/api/enable_token", enable_token)

    async def test_tone(request: web.Request) -> web.Response:
        zone = zone_or_404(request)
        if not await zone.test_tone():
            raise web.HTTPConflict(text="speaker busy or not paired")
        return web.json_response({"ok": True})

    app.router.add_get("/api/zones/{index}/cover", cover)
    app.router.add_post("/api/zones/{index}/test", test_tone)
    app.router.add_get("/sync-click.wav", click)
    app.router.add_get("/level-noise.wav", level_track)
    app.router.add_get("/api/zones", list_zones)
    app.router.add_post("/api/zones/{index}/mute", set_mute)
    app.router.add_post("/api/zones/{index}/offset", set_offset)
    app.router.add_post("/api/zones/{index}/level", set_level)
    app.router.add_post("/api/zones/{index}/trim", set_trim)
    app.router.add_get("/int/state", int_state)
    app.router.add_get("/int/ws", int_ws)
    app.router.add_post("/int/zones/{index}/set", int_set)
    app.router.add_post("/int/zones/{index}/test", test_tone)
    app.router.add_get("/int/zones/{index}/cover", cover)
    return app


async def start_web(zones: list[Zone]) -> web.AppRunner:
    token = load_token()
    runner = web.AppRunner(create_app(zones, token), access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=INGRESS_PORT)  # noqa: S104
    await site.start()
    log.info("Calibration page running (ingress, port %s)", INGRESS_PORT)
    asyncio.ensure_future(announce_to_ha(token))
    return runner
