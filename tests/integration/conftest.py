"""Fixtures: a fake AirPlay Bridge add-on that speaks the real /int/ API."""

from __future__ import annotations

import asyncio
import copy
import json

import pytest
from aiohttp import web

pytest_plugins = "pytest_homeassistant_custom_component"

TOKEN = "test-token-0123456789abcdefghijklmnopqrstuvwxyz"

ZONE = {
    "index": 0,
    "name": "Stube Echo",
    "key": "stube_echo",
    "speakers": [
        {
            "label": "Stube Echo",
            "host": "192.0.2.10",
            "channel": "stereo",
            "paired": True,
            "token_entity": "sensor.stube_sendspin_pairing_token",
        }
    ],
    "track": None,
    "cover": None,
    "offset_ms": -225,
    "lead_ms": 1275,
    "trim_db": 0.0,
    "speaker_volume": None,
    "speaker_volume_target": 100,
    "airplay_volume": None,
    "default_volume": 100,
    "night_volume": 40,
    "night_start": "22:00",
    "night_end": "07:00",
    "night_enabled": True,
    "night_active": False,
    "model": "Echo 2",
    "manufacturer": "Amazon",
    "stats": {"sessions": 0, "audio_s": 0.0, "resyncs": 0, "drops": 0, "lost": 0},
    "dropouts": 0,
    "playing": False,
}
COVER = b"\x89PNG\r\n\x1a\n" + b"0" * 64


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


class FakeAddon:
    def __init__(self) -> None:
        self.zones = [copy.deepcopy(ZONE)]
        self.calls: list[tuple[str, int, dict]] = []
        self.sockets: set[web.WebSocketResponse] = set()
        self.runner: web.AppRunner | None = None
        self.port = 0

    async def push(self) -> None:
        msg = json.dumps({"type": "state", "zones": self.zones})
        for ws in list(self.sockets):
            await ws.send_str(msg)

    async def start(self) -> None:
        @web.middleware
        async def auth(request, handler):
            if request.headers.get("Authorization") != f"Bearer {TOKEN}":
                raise web.HTTPUnauthorized()
            return await handler(request)

        app = web.Application(middlewares=[auth])

        async def state(_r):
            return web.json_response({"version": "0.8.0", "zones": self.zones})

        async def ws_handler(request):
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            self.sockets.add(ws)
            await ws.send_str(json.dumps({"type": "state", "zones": self.zones}))
            async for _ in ws:
                pass
            self.sockets.discard(ws)
            return ws

        async def set_(request):
            i = int(request.match_info["index"])
            body = await request.json()
            self.calls.append(("set", i, body))
            zone = self.zones[i]
            mapping = {
                "latency_offset_ms": "offset_ms",
                "volume_trim_db": "trim_db",
                "speaker_volume": "airplay_volume",
                "night_enabled": "night_enabled",
            }
            for key, value in body.items():
                zone[mapping[key]] = value
            await self.push()
            return web.json_response({"ok": True})

        async def test_(request):
            i = int(request.match_info["index"])
            self.calls.append(("test", i, {}))
            if self.zones[i]["playing"]:
                raise web.HTTPConflict(text="busy")
            return web.json_response({"ok": True})

        async def cover(request):
            i = int(request.match_info["index"])
            if not self.zones[i]["cover"]:
                raise web.HTTPNotFound()
            return web.Response(body=COVER, content_type="image/png")

        app.router.add_get("/int/state", state)
        app.router.add_get("/int/ws", ws_handler)
        app.router.add_post("/int/zones/{index}/set", set_)
        app.router.add_post("/int/zones/{index}/test", test_)
        app.router.add_get("/int/zones/{index}/cover", cover)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        self.port = site._server.sockets[0].getsockname()[1]  # noqa: SLF001

    async def stop(self) -> None:
        for ws in list(self.sockets):
            await ws.close()
        if self.runner:
            await self.runner.cleanup()


@pytest.fixture
async def addon(socket_enabled):
    # The fake add-on is a real local server (127.0.0.1 only), so this
    # fixture lifts Home Assistant's socket block for these tests.
    fake = FakeAddon()
    await fake.start()
    yield fake
    await fake.stop()
    await asyncio.sleep(0)
