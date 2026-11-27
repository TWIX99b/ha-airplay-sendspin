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
for n in [
    "aiosendspin",
    "aiosendspin.audio",
    "aiosendspin.models",
    "aiosendspin.server",
]:
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
import speakers  # noqa: E402

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
    check([s.channel for s in cfg.speakers] == ["left", "right"], "pair: left + right")
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
    check(all(x[0] == y[0] for x, y in zip(a, b, strict=True)), "pair: same play time")
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
    check(len(msgs) == 5, "mqtt: 5 entities")
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
        (0, "latency_offset_ms", -230) in saved
        and (0, "volume_trim_db", -3.5) in saved,
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


def test_speakers():
    old = [
        {
            "name": "Stube",
            "host": "1.1.1.1",
            "port": 28928,
            "path": "/sendspin",
            "pairing_token": "SP:SECRET",
            "latency_offset_ms": -225,
            "volume_trim_db": 0,
        },
    ]
    pub = speakers.public_zone(old[0])
    check(
        "pairing_token" not in pub and pub["has_pairing_token"],
        "speakers: token hidden",
    )
    # page sends it back without the token → kept
    new = speakers.clean_all([{**pub, "orig": 0, "latency_offset_ms": -230}], old)
    check(new[0]["pairing_token"] == "SP:SECRET", "speakers: token kept")
    check(not speakers.needs_restart(old, new), "speakers: offset only → live")
    added = speakers.clean_all(
        [
            {**pub, "orig": 0},
            {"name": "Bad", "host": "1.1.1.2", "token_entity": "sensor.x"},
        ],
        old,
    )
    check(speakers.needs_restart(old, added), "speakers: new speaker → restart")
    for bad, why in (
        ([{"name": "", "host": "x", "token_entity": "s"}], "no name"),
        ([{"name": "A", "host": "", "token_entity": "s"}], "no host"),
        ([{"name": "A", "host": "x"}], "no token"),
        (
            [
                {"name": "A", "host": "x", "token_entity": "s"},
                {"name": "a", "host": "y", "token_entity": "t"},
            ],
            "duplicate name",
        ),
        (
            [
                {
                    "name": "A",
                    "host": "x",
                    "token_entity": "s",
                    "partner_host": "x",
                    "partner_token_entity": "t",
                }
            ],
            "speaker twice",
        ),
        (
            [
                {
                    "name": "A",
                    "host": "x",
                    "token_entity": "s",
                    "latency_offset_ms": 5000,
                }
            ],
            "offset",
        ),
    ):
        try:
            speakers.clean_all(bad, [])
            check(False, f"speakers: rejects {why}")
        except ValueError:
            check(True, f"speakers: rejects {why}")
    found = speakers.parse_discovery(
        '[{"token_entity":"sensor.a","device":"D","model":"echo-2","area":"Bad",'
        '"web":"http://10.0.0.7:28931","satellite":"assist_satellite.a","has_token":true}]',
        [],
    )
    check(found[0]["host"] == "10.0.0.7", "discovery: IP from web UI address")
    check(found[0]["model_name"] == "Echo 2", "discovery: model name")
    check(found[0]["suggested_name"] == "Bad Echo", "discovery: suggested name")
    check(speakers.parse_discovery("garbage", []) == [], "discovery: bad input")


def test_night_and_volume():
    cfg = bridge.zone_config(
        0,
        {
            "name": "Schlafzimmer",
            "host": "A",
            "port": 1,
            "speaker_volume": 70,
            "night_volume": 40,
            "night_start": "22:00",
            "night_end": "07:00",
        },
    )
    zone = bridge.Zone(cfg, FakeSide({"A": 30}), OPTS)
    check(zone.target_volume(12 * 60) == 70, "volume: per speaker by day")
    check(zone.target_volume(23 * 60) == 40, "volume: night mode at 23:00")
    check(zone.target_volume(6 * 60 + 59) == 40, "volume: night mode until 07:00")
    check(zone.target_volume(7 * 60) == 70, "volume: day again at 07:00")
    plain = bridge.Zone(
        bridge.zone_config(1, {"name": "Bad", "host": "B", "port": 1}),
        FakeSide({"B": 1}),
        {**OPTS, "speaker_volume": 90},
    )
    check(plain.target_volume(23 * 60) == 90, "volume: global default, no night mode")
    try:
        speakers.clean_zone(
            {
                "name": "X",
                "host": "h",
                "token_entity": "s",
                "night_volume": 30,
                "night_start": "25:00",
            },
            None,
        )
        check(False, "speakers: rejects bad night time")
    except ValueError:
        check(True, "speakers: rejects bad night time")
    z = speakers.clean_zone(
        {"name": "X", "host": "h", "token_entity": "s", "night_volume": 30}, None
    )
    check(z["night_start"] == "22:00" and z["night_end"] == "07:00", "night: defaults")
    check(not speakers.needs_restart([{**z, "night_volume": 50}], [z]), "night: live")


async def test_follow_night():
    cfg = bridge.zone_config(
        0,
        {
            "name": "S",
            "host": "A",
            "port": 1,
            "night_volume": 40,
            "night_start": "00:00",
            "night_end": "00:00",
        },
    )  # start == end: off
    side = FakeSide({"A": 30})
    zone = bridge.Zone(cfg, side, OPTS)
    link = zone.links[0]
    link.client_id = "x"
    await zone._acquire(link)
    zone._take_volume(link)
    zone.active, zone.streaming = [link], True
    check(side.players["A"].volume == 100, "night: off → 100")
    zone.apply_settings(
        {
            "latency_offset_ms": 0,
            "volume_trim_db": 0.0,
            "night_volume": 40,
            "night_start": "00:00",
            "night_end": "23:59",
        }
    )
    check(side.players["A"].volume == 40, "night: switched on live → 40")
    await zone._give_volume_back(link)
    check(side.players["A"].volume == 30, "night: old volume back after session")


async def test_tone_pair():
    cfg = bridge.zone_config(
        0, {"name": "Paar", "host": "A", "port": 1, "partner_host": "B"}
    )
    side = FakeSide({"A": 40, "B": 40})
    zone = bridge.Zone(cfg, side, OPTS)
    for link in zone.links:
        link.client_id = "id"
    bridge.TONE_LEAD_US = 1000
    ok = await zone.test_tone()
    check(ok, "test tone: played")
    a, b = side.groups["A"].stream.commits, side.groups["B"].stream.commits

    def first_sound(commits):
        for t, pcm in commits:
            arr = array("h")
            arr.frombytes(pcm)
            if max(map(abs, arr)) > 1000:
                return t
        return None

    ta, tb = first_sound(a), first_sound(b)
    check(
        ta is not None and tb is not None and tb > ta,
        "test tone: left first, then right",
    )
    check(abs((tb - ta) - bridge.TONE_GAP_S * 1e6) < 30_000, "test tone: 0.9 s apart")
    check(side.players["A"].history == [], "test tone: volume untouched")
    zone.streaming = True
    check(not await zone.test_tone(), "test tone: refused while AirPlay plays")


async def test_stats():
    cfg = bridge.zone_config(0, {"name": "W", "host": "A", "port": 1})
    side = FakeSide({"A": 50})
    zone = bridge.Zone(cfg, side, OPTS)
    zone.links[0].client_id = "x"
    queue = asyncio.Queue()
    now = bridge.mono_us()
    for _ in range(4):  # first batch: first + 4 = MAX_BATCH chunks
        queue.put_nowait((now, stereo_chunk(1, 1)))
    # a big jump in arrival time = an audible hiccup (resync)
    queue.put_nowait((now + 2_000_000, stereo_chunk(1, 1)))
    await zone._session(queue, (now, stereo_chunk(1, 1)))
    check(zone.stats["sessions"] == 1, "stats: one session")
    check(zone.stats["resyncs"] >= 1, "stats: hiccup counted")
    check(mqtt_ha.dropouts(zone.stats) >= 1, "stats: dropouts for MQTT")
    check(zone.stats["audio_s"] > 0.1, "stats: audio time")


test_config()
test_speakers()
test_night_and_volume()
asyncio.run(test_follow_night())
asyncio.run(test_tone_pair())
asyncio.run(test_stats())
test_pick_channel()
asyncio.run(test_pair_session())
asyncio.run(test_voice())
test_metadata()
test_mqtt()
print("ALL OK" if not FAILS else f"{len(FAILS)} FAILED")
sys.exit(1 if FAILS else 0)
