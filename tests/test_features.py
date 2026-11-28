"""Tests for 0.5 features with simulated speakers (no Echo, no HA needed).

* stereo pair: both speakers get the same play time, left/right separated
* track info: shairport-sync metadata stream → TrackInfo → Sendspin roles
* voice assistant: volume back down while active, up again after idle
* state for the Home Assistant integration (web.zone_state)

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
    now = bridge.mono_us()
    # chunks arrive every 20 ms (the time base needs steady arrivals)
    for i in range(1, 40):
        queue.put_nowait((now + i * 20_000, stereo_chunk(111, 222)))
    await zone._session(queue, (now, stereo_chunk(111, 222)))
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

    # AirPlay 2: the cover often comes BEFORE the title block of its track
    now = [100.0]
    p = metadata.MetadataParser(clock=lambda: now[0])

    def block(title):
        return (
            meta_item("ssnc", "mdst")
            + meta_item("core", "minm", title.encode())
            + meta_item("core", "asar", b"Band")
            + meta_item("ssnc", "mden")
        )

    ups = p.feed(meta_item("ssnc", "PICT", b"\xff\xd8cover1") + block("Eins"))
    check(
        ups[-1].title == "Eins" and ups[-1].cover == b"\xff\xd8cover1",
        "metadata: cover before title is kept",
    )
    now[0] += 200  # next track, its cover only comes later
    ups = p.feed(block("Zwei"))
    check(
        ups[-1].title == "Zwei" and ups[-1].cover is None,
        "metadata: old cover not shown for the next title",
    )
    ups = p.feed(meta_item("ssnc", "PICT", b"\xff\xd8cover2"))
    check(ups[-1].cover == b"\xff\xd8cover2", "metadata: late cover arrives")
    now[0] += 1  # third track: cover first, title right after
    ups = p.feed(meta_item("ssnc", "PICT", b"\xff\xd8cover3") + block("Drei"))
    check(ups[-1].cover == b"\xff\xd8cover3", "metadata: cover-first again")


def test_zone_state():
    import web

    cfg = bridge.zone_config(
        0,
        {
            "name": "Stube Echo",
            "host": "A",
            "port": 1,
            "token_entity": "sensor.t",
            "night_volume": 40,
            "night_enabled": False,
        },
    )
    zone = bridge.Zone(cfg, FakeSide({"A": 1}), OPTS)
    zone.stats.update(resyncs=2, drops=1, lost=1)
    st = web.zone_state(zone)
    check(st["key"] == "stube_echo", "state: key for entity ids")
    check(st["dropouts"] == 4, "state: dropouts")
    check(
        st["night_enabled"] is False and not zone.night_active(23 * 60),
        "state: night mode switched off",
    )
    check(st["speakers"][0]["token_entity"] == "sensor.t", "state: token entity")
    z = speakers.clean_zone(
        {
            "name": "X",
            "host": "h",
            "token_entity": "s",
            "night_volume": 30,
            "night_enabled": False,
        },
        None,
    )
    check(z["night_enabled"] is False, "speakers: night_enabled kept")


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
    merged = speakers.add_disabled(
        found,
        [
            {"token_entity": found[0]["token_entity"], "device": "dup"},
            {
                "token_entity": "sensor.x_sendspin_pairing_token",
                "device": "X",
                "model": "echo-dot-3",
                "area": "Keller",
            },
        ],
    )
    extra = next(m for m in merged if m.get("disabled"))
    check(len(merged) == len(found) + 1, "discovery: disabled added once")
    first_done = next((k for k, m in enumerate(merged) if m["configured"]), len(merged))
    check(merged.index(extra) < first_done, "discovery: disabled before configured")
    check(
        extra["disabled"]
        and not extra["has_token"]
        and extra["model_name"] == "Echo Dot 3",
        "discovery: disabled entry marked",
    )


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

    # the Echo reports its own rounding (60 → 62): settings still apply
    zone._take_volume(link)
    zone.apply_settings(
        {
            "speaker_volume": 60,
            "night_volume": 30,
            "night_start": "00:00",
            "night_end": "00:00",
        }
    )
    check(side.players["A"].volume == 60, "night: per-speaker 60 applied")
    side.players["A"].volume = 62  # speaker's own steps
    zone.apply_settings(
        {
            "speaker_volume": 60,
            "night_volume": 30,
            "night_start": "00:00",
            "night_end": "23:59",
        }
    )
    check(side.players["A"].volume == 30, "night: applies despite speaker rounding")
    # automatic switch respects a real change by hand
    zone.apply_settings(
        {
            "speaker_volume": 60,
            "night_volume": 30,
            "night_start": "00:00",
            "night_end": "00:00",
        }
    )
    side.players["A"].volume = 85  # turned up by hand
    zone.cfg.night_end = "23:59"
    zone._follow_target()
    check(side.players["A"].volume == 85, "night: automatic switch keeps a hand change")


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

    # speaker turned almost all the way down: tone at 40 %, then back
    side = FakeSide({"A": 3})
    zone = bridge.Zone(
        bridge.zone_config(0, {"name": "L", "host": "A", "port": 1}), side, OPTS
    )
    zone.links[0].client_id = "id"
    check(await zone.test_tone(), "test tone: played at low volume")
    check(
        side.players["A"].history == [bridge.TONE_MIN_VOLUME, 3],
        "test tone: quiet speaker raised to 40 %, then restored",
    )


async def test_stats():
    cfg = bridge.zone_config(0, {"name": "W", "host": "A", "port": 1})
    side = FakeSide({"A": 50})
    zone = bridge.Zone(cfg, side, OPTS)
    zone.links[0].client_id = "x"
    queue = asyncio.Queue()
    now = bridge.mono_us()
    for i in range(1, 40):
        queue.put_nowait((now + i * 20_000, stereo_chunk(1, 1)))
    # a big jump in arrival time (skip, flush) = a new time line
    for i in range(40):
        queue.put_nowait((now + 3_000_000 + i * 20_000, stereo_chunk(1, 1)))
    await zone._session(queue, (now, stereo_chunk(1, 1)))
    check(zone.stats["sessions"] == 1, "stats: one session")
    check(zone.stats["resyncs"] >= 1, "stats: hiccup counted")
    check(zone.stats["audio_s"] > 0.1, "stats: audio time")


async def test_timing_steady():
    """Every chunk gets base + i·20 ms: no session-specific random anchor."""
    cfg = bridge.zone_config(0, {"name": "W", "host": "A", "port": 1})
    side = FakeSide({"A": 50})
    zone = bridge.Zone(cfg, side, OPTS)
    zone.links[0].client_id = "x"
    queue = asyncio.Queue()
    now = bridge.mono_us()
    # lead-in burst (back to back), then steady arrivals 0–8 ms late
    import random

    rng = random.Random(4)
    for i in range(1, 50):
        queue.put_nowait((now + i * 30, stereo_chunk(0, 0)))
    for i in range(50, 150):
        late = rng.uniform(0, 8_000)
        queue.put_nowait((now + 400_000 + i * 20_000 + int(late), stereo_chunk(5, 5)))
    await zone._session(queue, (now, stereo_chunk(0, 0)))
    commits = side.groups["A"].stream.commits
    times = [c[0] for c in commits]
    sizes = [len(c[1]) // bridge.CHUNK_BYTES for c in commits]
    # consecutive commits continue exactly where the previous one ended
    gaps = [
        t2 - (t1 + n * bridge.CHUNK_US) for t1, t2, n in zip(times, times[1:], sizes)
    ]
    check(max(abs(g) for g in gaps) <= 300, "timing: commits are contiguous")
    check(zone.stats["resyncs"] == 0, "timing: no restarts in a steady stream")


test_config()
test_speakers()
test_night_and_volume()
asyncio.run(test_follow_night())
asyncio.run(test_tone_pair())
asyncio.run(test_stats())
asyncio.run(test_timing_steady())
test_pick_channel()
asyncio.run(test_pair_session())
asyncio.run(test_voice())
test_metadata()
test_zone_state()
print("ALL OK" if not FAILS else f"{len(FAILS)} FAILED")
sys.exit(1 if FAILS else 0)
