"""Tests for 0.5 features with simulated speakers (no Echo, no HA needed).

* stereo pair: both speakers get the same play time, left/right separated
* track info: shairport-sync metadata stream → TrackInfo → Sendspin roles
* voice assistant: volume back down while active, up again after idle
* MQTT: discovery payloads and number commands

    python3 tests/test_features.py
"""

import asyncio
import base64
import pathlib
import sys
import types
from array import array

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "airplay2_sendspin_bridge" / "app"))


def stub(name, **attrs):
    m = types.ModuleType(name)
    m.__dict__.update(attrs)
    sys.modules[name] = m
    return m


class _Any:
    def __init__(self, *a, **k):
        pass


stub(
    "aiohttp",
    ClientError=Exception,
    ClientSession=object,
    ClientTimeout=object,
    WSMsgType=types.SimpleNamespace(TEXT=1),
    web=stub("aiohttp.web"),
)
for n in ["aiosendspin", "aiosendspin.audio", "aiosendspin.models", "aiosendspin.server"]:
    stub(n)
stub("aiosendspin.audio.format", AudioFormat=_Any)
stub(
    "aiosendspin.models.types",
    ConnectionReason=types.SimpleNamespace(PAIRING=1, PLAYBACK=2),
    PairMethod=types.SimpleNamespace(PAIRING_PSK=1),
    PlayerCommand=types.SimpleNamespace(VOLUME="volume"),
)
stub(
    "aiosendspin.noise",
    FileServerPairingStore=_Any,
    Identity=_Any,
    b64url_decode=_Any,
    decode_psk_token=_Any,
)
stub("aiosendspin.noise.pairing", PairingAttempt=_Any)
sys.modules["aiosendspin.server"].SendspinServer = _Any

import bridge  # noqa: E402
import metadata  # noqa: E402
import mqtt_ha  # noqa: E402

FAILS = []


def check(cond, what):
    print(("OK   " if cond else "FAIL ") + what)
    if not cond:
        FAILS.append(what)


# ---------------------------------------------------------------- fakes
class FakePlayer:
    def __init__(self, volume):
        self.volume = volume
        self.state_supported_commands = ["volume"]
        self.history = []

    def set_volume(self, v):
        self.volume = v
        self.history.append(v)


class FakeMetaRole:
    def __init__(self):
        self.last = None

    def update(self, **kw):
        self.last = kw

    def clear(self):
        self.last = None


class FakeArtRole:
    def __init__(self):
        self.images = []

    async def set_album_artwork(self, image):
        self.images.append(image)


class FakeStream:
    def __init__(self, clock):
        self.clock = clock
        self.commits = []
        self._pcm = None

    def now_us(self):
        return self.clock()

    def prepare_audio(self, pcm, fmt):
        self._pcm = pcm

    async def commit_audio(self, play_start_us):
        self.commits.append((play_start_us, self._pcm))


class FakeGroup:
    def __init__(self, clock):
        self.stream = FakeStream(clock)
        self.meta = FakeMetaRole()
        self.art = FakeArtRole()
        self.stopped = False

    def start_stream(self):
        return self.stream

    def group_role(self, family):
        return {"metadata": self.meta, "artwork": self.art}.get(family)

    async def stop(self):
        self.stopped = True


class FakeSide:
    def __init__(self, volumes):
        self.volumes = volumes
        self.groups = {}
        self.players = {}
        self.released = []

    async def acquire(self, spk, client_id):
        group = FakeGroup(lambda: bridge.mono_us() + 5_000_000)
        player = FakePlayer(self.volumes[spk.host])
        self.groups[spk.host] = group
        self.players[spk.host] = player
        client = types.SimpleNamespace(roles_by_family=lambda fam: [player])
        return client, group

    def release(self, spk):
        self.released.append(spk.host)


OPTS = {"latency_ms": 500, "idle_timeout_s": 0.3, "speaker_volume": 100}


def stereo_chunk(left, right):
    a = array("h", [0]) * (bridge.CHUNK_FRAMES * 2)
    a[0::2] = array("h", [left]) * bridge.CHUNK_FRAMES
    a[1::2] = array("h", [right]) * bridge.CHUNK_FRAMES
    return a.tobytes()


# ---------------------------------------------------------------- tests
def test_config():
    cfg = bridge.zone_config(
        0,
        {
            "name": "Stube",
            "host": "10.0.0.1",
            "port": 28928,
            "token_entity": "sensor.a",
            "partner_host": "10.0.0.2",
            "partner_token_entity": "sensor.b",
        },
    )
    check(len(cfg.speakers) == 2, "pair: two speakers")
    check(
        [s.channel for s in cfg.speakers] == ["left", "right"], "pair: left + right"
    )
    check(cfg.speakers[1].port == 28928, "pair: partner inherits port")
    single = bridge.zone_config(1, {"name": "Bad", "host": "10.0.0.3", "port": 1})
    check(single.speakers[0].channel == "stereo", "single speaker plays stereo")


def test_pick_channel():
    pcm = stereo_chunk(1000, -2000)
    left = array("h")
    left.frombytes(bridge.pick_channel(pcm, 0))
    right = array("h")
    right.frombytes(bridge.pick_channel(pcm, 1))
    check(set(left) == {1000}, "left channel on both sides")
    check(set(right) == {-2000}, "right channel on both sides")


async def test_pair_session():
    cfg = bridge.zone_config(
        0,
        {"name": "Stube", "host": "A", "port": 1, "partner_host": "B"},
    )
    side = FakeSide({"A": 40, "B": 55})
    zone = bridge.Zone(cfg, side, OPTS)
    for link in zone.links:
        link.client_id = "id-" + link.cfg.host
    zone.track = metadata.TrackInfo(title="Song", artist="Band", cover=None)
    queue = asyncio.Queue()
    for _ in range(10):
        queue.put_nowait((bridge.mono_us(), stereo_chunk(111, 222)))
    await zone._session(queue, (bridge.mono_us(), stereo_chunk(111, 222)))
    a, b = side.groups["A"].stream.commits, side.groups["B"].stream.commits
    check(len(a) > 0 and len(a) == len(b), "pair: both speakers got every commit")
    check(
        all(x[0] == y[0] for x, y in zip(a, b, strict=True)), "pair: same play time"
    )
    la, lb = array("h"), array("h")
    la.frombytes(a[0][1])
    lb.frombytes(b[0][1])
    check(set(la) == {111} and set(lb) == {222}, "pair: A = left, B = right")
    check(side.players["A"].history[0] == 100, "volume up to 100 for AirPlay")
    check(side.players["A"].volume == 40, "volume A restored after the session")
    check(side.players["B"].volume == 55, "volume B restored after the session")
    check(sorted(side.released) == ["A", "B"], "both speakers released")
    await asyncio.sleep(0.05)
    check(
        side.groups["A"].meta.last and side.groups["A"].meta.last["title"] == "Song",
        "track info sent at session start",
    )


async def test_voice():
    bridge.VOICE_IDLE_HOLD_S = 0.05
    cfg = bridge.zone_config(0, {"name": "Bad", "host": "A", "port": 1})
    side = FakeSide({"A": 40})
    zone = bridge.Zone(cfg, side, OPTS)
    link = zone.links[0]
    link.cfg.satellite_entity = "assist_satellite.bad"
    link.client_id = "x"
    await zone._acquire(link)
    zone._take_volume(link)
    zone.active, zone.streaming = [link], True
    player = side.players["A"]
    check(player.volume == 100, "voice: AirPlay at 100")
    zone.on_satellite("assist_satellite.bad", "listening")
    check(player.volume == 40, "voice: listening → old volume")
    zone.on_satellite("assist_satellite.bad", "responding")
    zone.on_satellite("assist_satellite.bad", "idle")
    await asyncio.sleep(0.02)
    zone.on_satellite("assist_satellite.bad", "listening")  # follow-up question
    await asyncio.sleep(0.1)
    check(player.volume == 40, "voice: follow-up keeps it down")
    zone.on_satellite("assist_satellite.bad", "idle")
    await asyncio.sleep(0.1)
    check(player.volume == 100, "voice: idle → back to 100")
    zone.on_satellite("assist_satellite.bad", "listening")
    await zone._give_volume_back(link)
    check(player.volume == 40, "voice: session end while lowered stays at old value")


def meta_item(kind, code, data=None):
    head = f"<item><type>{kind.encode().hex()}</type><code>{code.encode().hex()}</code>"
    if data is None:
        return f"{head}<length>0</length></item>\n".encode()
    b64 = base64.b64encode(data).decode()
    return (
        f'{head}<length>{len(data)}</length>\n<data encoding="base64">\n{b64}</data></item>\n'
    ).encode()


def test_metadata():
    p = metadata.MetadataParser()
    stream = (
        meta_item("ssnc", "mdst", b"1")
        + meta_item("core", "minm", "Bohemian Rhapsody".encode())
        + meta_item("core", "asar", b"Queen")
        + meta_item("core", "asal", b"A Night at the Opera")
        + meta_item("ssnc", "mden", b"1")
        + meta_item("ssnc", "PICT", b"\xff\xd8jpegdata")
    )
    # feed in awkward pieces: items split across reads
    updates = []
    for i in range(0, len(stream), 37):
        updates += p.feed(stream[i : i + 37])
    check(len(updates) == 2, f"metadata: 2 updates (got {len(updates)})")
    check(updates[0].title == "Bohemian Rhapsody", "metadata: title")
    check(updates[0].artist == "Queen", "metadata: artist")
    check(updates[1].cover == b"\xff\xd8jpegdata", "metadata: cover")
    same = p.feed(
        meta_item("ssnc", "mdst")
        + meta_item("core", "minm", "Bohemian Rhapsody".encode())
        + meta_item("core", "asar", b"Queen")
        + meta_item("core", "asal", b"A Night at the Opera")
        + meta_item("ssnc", "mden")
    )
    check(same == [], "metadata: unchanged block → no update")
    check(p.feed(meta_item("ssnc", "pend")) == [None], "metadata: end → None")


def test_mqtt():
    cfg = bridge.zone_config(0, {"name": "Stube Echo", "host": "A", "port": 1})
    zone = bridge.Zone(cfg, FakeSide({"A": 1}), OPTS)
    msgs = mqtt_ha.discovery_messages(zone, "0.5.0", "de")
    topics = [t for t, _ in msgs]
    check(len(msgs) == 4, "mqtt: 4 entities")
    check(
        "homeassistant/number/ap2s_stube_echo/offset/config" in topics,
        "mqtt: number topic",
    )
    check(
        all(p["unique_id"].startswith("ap2s_stube_echo_") for _, p in msgs),
        "mqtt: unique ids",
    )
    check(msgs[2][1]["name"] == "Feinabgleich", "mqtt: German names")
    saved = []

    async def save(index, key, value):
        saved.append((index, key, value))
        return True

    async def run():
        m = mqtt_ha.MqttBridge([zone], save, "0.5.0", "de")
        m.handle("airplay_sendspin/stube_echo/offset/set", "-230")
        m.handle("airplay_sendspin/stube_echo/trim/set", "-3.5")
        m.handle("airplay_sendspin/stube_echo/trim/set", "12")  # clamped
        await asyncio.sleep(0.01)

    asyncio.run(run())
    check(zone.cfg.latency_offset_ms == -230, "mqtt: offset set live")
    check(zone.cfg.volume_trim_db == 0.0, "mqtt: trim clamped to 0")
    check(
        (0, "latency_offset_ms", -230) in saved and (0, "volume_trim_db", -3.5) in saved,
        "mqtt: values saved",
    )
    ext = mqtt_ha.broker_from_options(
        {"mqtt_host": " 10.0.0.9 ", "mqtt_port": 1884, "mqtt_username": "u"}
    )
    check(
        ext["host"] == "10.0.0.9" and ext["port"] == 1884 and ext["username"] == "u",
        "mqtt: external broker from options",
    )
    check(mqtt_ha.broker_from_options({"mqtt_host": ""}) is None, "mqtt: empty host")
    states = dict(mqtt_ha.state_messages(zone))
    check(states["airplay_sendspin/stube_echo/offset"] == "-230", "mqtt: state topic")


test_config()
test_pick_channel()
asyncio.run(test_pair_session())
asyncio.run(test_voice())
test_metadata()
test_mqtt()
print("ALL OK" if not FAILS else f"{len(FAILS)} FAILED")
sys.exit(1 if FAILS else 0)
