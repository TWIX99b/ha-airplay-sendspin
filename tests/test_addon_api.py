"""The add-on's API for the Home Assistant integration (/int/), with real aiohttp.

Fake speakers instead of Echos; checks token protection, state, live push,
settings (validated + saved) and that the ingress page stays reachable.

    pip install aiohttp && python3 tests/test_addon_api.py
"""

import asyncio
import json
import pathlib
import sys
import tempfile
import types

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "airplay2_sendspin_bridge" / "app"))

from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

import web  # noqa: E402

FAILS = []
TOKEN = "t" * 43


def check(cond, what):
    print(("OK   " if cond else "FAIL ") + what)
    if not cond:
        FAILS.append(what)


class FakeZone:
    def __init__(self, index, name):
        self.cfg = types.SimpleNamespace(
            index=index,
            name=name,
            slug=name.lower().replace(" ", "_"),
            latency_offset_ms=-225,
            volume_trim_db=0.0,
            speaker_volume=None,
            night_volume=40,
            night_start="22:00",
            night_end="07:00",
            night_enabled=True,
        )
        self.links = [
            types.SimpleNamespace(
                cfg=types.SimpleNamespace(
                    label=name, host="h", channel="stereo", token_entity="sensor.x"
                ),
                client_id="id",
            )
        ]
        self.track = None
        self.streaming = False
        self.model, self.manufacturer = "Echo 2", "Amazon"
        self.default_volume = 100
        self.stats = {
            "sessions": 0,
            "audio_s": 0.0,
            "resyncs": 1,
            "drops": 1,
            "lost": 0,
        }
        self.echo_volume = None
        self._listeners = []
        self.applied = None
        self.tested = 0

    def add_listener(self, cb):
        self._listeners.append(cb)

    def notify(self):
        for cb in self._listeners:
            cb(self)

    def lead_ms(self):
        return 1275

    def target_volume(self):
        return 100

    def night_active(self):
        return False

    def apply_settings(self, z):
        self.applied = z
        self.cfg.night_enabled = z.get("night_enabled", True)
        self.notify()

    async def test_tone(self):
        self.tested += 1
        return not self.streaming


async def main():
    tmp = pathlib.Path(tempfile.mkdtemp())
    web.OPTIONS_FILE = tmp / "options.json"
    web.OPTIONS_FILE.write_text(
        json.dumps(
            {
                "zones": [
                    {
                        "name": "Stube Echo",
                        "host": "h",
                        "port": 28928,
                        "path": "/sendspin",
                        "token_entity": "sensor.x",
                        "latency_offset_ms": -225,
                        "volume_trim_db": 0.0,
                        "night_volume": 40,
                    }
                ]
            }
        )
    )
    saved = []

    async def fake_save(opts):
        saved.append(opts)
        web.OPTIONS_FILE.write_text(json.dumps(opts))
        return True

    web.save_options = fake_save
    zone = FakeZone(0, "Stube Echo")
    app = web.create_app([zone], TOKEN)
    async with TestClient(TestServer(app)) as client:
        auth = {"Authorization": f"Bearer {TOKEN}"}
        r = await client.get("/int/state")
        check(r.status == 401, "int: no token → 401")
        r = await client.get("/int/state", headers={"Authorization": "Bearer nope"})
        check(r.status == 401, "int: wrong token → 401")
        r = await client.get("/int/state", headers=auth)
        data = await r.json()
        check(r.status == 200 and data["zones"][0]["key"] == "stube_echo", "int: state")
        z = data["zones"][0]
        check(z["dropouts"] == 2 and z["night_enabled"] is True, "int: dropouts, night")
        check(
            z["speakers"][0]["token_entity"] == "sensor.x", "int: token entity for HA"
        )

        ws = await client.ws_connect("/int/ws", headers=auth)
        first = json.loads((await ws.receive()).data)
        check(first["type"] == "state", "ws: state on connect")
        r = await client.post(
            "/int/zones/0/set", json={"night_enabled": False}, headers=auth
        )
        check(r.status == 200, "set: night mode off")
        pushed = json.loads((await asyncio.wait_for(ws.receive(), 2)).data)
        check(pushed["zones"][0]["night_enabled"] is False, "ws: change pushed")
        check(
            saved and saved[-1]["zones"][0].get("night_enabled") is False, "set: saved"
        )
        r = await client.post(
            "/int/zones/0/set", json={"latency_offset_ms": 5000}, headers=auth
        )
        check(r.status == 400, "set: invalid value rejected")
        r = await client.post(
            "/int/zones/0/set", json={"pairing_token": "x"}, headers=auth
        )
        check(r.status == 400, "set: unknown setting rejected")
        r = await client.post(
            "/int/zones/0/set", json={"speaker_volume": 70}, headers=auth
        )
        check(
            r.status == 200 and zone.applied["speaker_volume"] == 70, "set: volume live"
        )
        r = await client.post(
            "/int/zones/0/set", json={"speaker_volume": None}, headers=auth
        )
        check("speaker_volume" not in zone.applied, "set: volume back to default")
        r = await client.post("/int/zones/0/test", headers=auth)
        check(r.status == 200 and zone.tested == 1, "test tone via integration")
        r = await client.get("/int/zones/0/cover", headers=auth)
        check(r.status == 404, "cover: none while nothing plays")
        r = await client.get("/int/zones/9/set", headers=auth)
        check(r.status in (404, 405), "unknown speaker")
        await ws.close()
        # the web page keeps working as before (local/ingress, no token)
        r = await client.get("/api/config")
        check(r.status == 200, "web page API unchanged")
    print("ALL OK" if not FAILS else f"{len(FAILS)} FAILED")
    sys.exit(1 if FAILS else 0)


asyncio.run(main())
