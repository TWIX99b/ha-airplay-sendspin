"""AirPlay 2 → Sendspin bridge.

For every configured zone (= one AirPlay receiver):
  * starts a shairport-sync instance (AirPlay 2, output to stdout) inside the
    network namespace ``ap2``,
  * reads the raw PCM (48 kHz, S16_LE, stereo),
  * stamps each chunk with a play time in the Sendspin server clock and
    pushes it with aiosendspin to one Sendspin player (e.g. an Echo with
    echo-dot-assist), or to two as a stereo pair (left/right),
  * passes title, artist, album and cover on (speakers with a screen),
  * turns the speaker back down while its voice assistant is active.

Sync with HomePods works like this: shairport-sync hands each sample out
``latency_ms`` EARLY (negative ``audio_backend_latency_offset_in_seconds``),
and the bridge tells the Echo to play it exactly ``latency_ms`` after it
arrived. So it plays at the moment AirPlay intended.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import io
import json
import logging
import math
import os
import signal
import time
from array import array
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import aiohttp
from aiosendspin.audio.format import AudioFormat
from aiosendspin.models.types import ConnectionReason, PairMethod, PlayerCommand
from aiosendspin.noise import (
    FileServerPairingStore,
    Identity,
    b64url_decode,
    decode_psk_token,
)
from aiosendspin.noise.pairing import PairingAttempt
from ha import SatelliteWatcher, find_device_info, find_satellite, ha_config
from metadata import MetadataReader, TrackInfo
from speakers import model_name
from timebase import TimeBase
from web import start_web

try:  # the package may or may not re-export these
    from aiosendspin.server import SendspinServer
except ImportError:  # pragma: no cover
    from aiosendspin.server.server import SendspinServer

# The event classes live in different modules depending on the aiosendspin
# version, so they are matched by class name (see SendspinSide._on_event).

OPTIONS_FILE = Path("/data/options.json")
DATA_DIR = Path("/data")
ZONE_DIR = DATA_DIR / "zones"
IDENTITY_FILE = DATA_DIR / "sendspin_identity.key"
PAIRING_FILE = DATA_DIR / "sendspin_pairings.json"
NETNS_FILE = Path("/run/ap2/netns_exec")
META_DIR = Path("/tmp/ap2-meta")

SAMPLE_RATE = 48_000
CHANNELS = 2
BYTES_PER_FRAME = CHANNELS * 2  # S16_LE
CHUNK_FRAMES = 960  # 20 ms
CHUNK_BYTES = CHUNK_FRAMES * BYTES_PER_FRAME
CHUNK_US = CHUNK_FRAMES * 1_000_000 // SAMPLE_RATE
MIN_LEAD_US = 80_000  # anything due sooner is dropped
MAX_BATCH = 5  # up to 100 ms per commit
QUEUE_MAX = 1500  # 30 s buffer between reader and sender
AIRPLAY_BASE_PORT = 7000
# shairport-sync keeps this much audio "in the output buffer" ahead of
# the play time; the bridge adds it back on.
SPS_BUFFER_S = 1.0
# Volume matching (web UI): the level track alternates noise and silence.
# During noise the zone is silent (only the reference speaker plays), in
# the silence the bridge replays the noise it just received on the zone.
LEVEL_PEAK_SILENT = 100  # chunk peak below this (≈ −50 dBFS) counts as silence
LEVEL_INJECT_FROM = 25  # chunks (0.5 s) into the silence …
LEVEL_INJECT_TO = 125  # … until 2.5 s
LEVEL_BUF_MAX = 150  # keep at most 3 s of noise
# Voice assistant: back to the old volume while it listens/thinks/talks,
# and only up again once it has been idle for a moment (follow-up questions).
VOICE_IDLE_HOLD_S = 2.0
# Test tone (web UI): a short two-tone chime straight to the speaker(s)
TONE_GAP_S = 0.9  # stereo pair: left first, then right
TONE_LEAD_US = 400_000
MANUFACTURERS = {"echo": "Amazon"}  # model prefix → manufacturer if HA has none
PCM_FORMAT = AudioFormat(sample_rate=SAMPLE_RATE, bit_depth=16, channels=CHANNELS)

log = logging.getLogger("bridge")


@dataclass
class SpeakerConfig:
    """One Sendspin speaker. A zone has one, or two as a stereo pair."""

    label: str
    host: str
    port: int
    path: str
    pairing_token: str
    token_entity: str
    channel: str = "stereo"  # stereo | left | right
    satellite_entity: str = ""

    @property
    def url(self) -> str:
        path = self.path if self.path.startswith("/") else f"/{self.path}"
        return f"ws://{self.host}:{self.port}{path}"


@dataclass
class ZoneConfig:
    index: int
    name: str
    speakers: list[SpeakerConfig]
    latency_offset_ms: int
    volume_trim_db: float = 0.0
    speaker_volume: int | None = None  # None = global option
    night_volume: int | None = None  # None = no night mode
    night_start: str = "22:00"
    night_end: str = "07:00"
    night_enabled: bool = True  # switch in HA; keeps the times and volume

    @property
    def host(self) -> str:
        return " + ".join(s.host for s in self.speakers)

    @property
    def slug(self) -> str:
        safe = "".join(c if c.isalnum() else "-" for c in self.name.lower())
        return f"z{self.index}-{safe.strip('-') or 'zone'}"

    @property
    def key(self) -> str:
        """Stable id for Home Assistant (from the name, not the position)."""
        name = self.name.lower()
        for umlaut, plain in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
            name = name.replace(umlaut, plain)
        safe = "".join(c if c.isascii() and c.isalnum() else "_" for c in name)
        return "_".join(p for p in safe.split("_") if p) or f"speaker_{self.index}"


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def mono_us() -> int:
    return time.monotonic_ns() // 1000


def load_options() -> dict:
    return json.loads(OPTIONS_FILE.read_text(encoding="utf-8"))


def load_identity() -> Identity:
    """Load the server's own Noise identity, or create it once."""
    if IDENTITY_FILE.exists():
        raw = b64url_decode(IDENTITY_FILE.read_text().strip())
        return Identity.from_private_bytes(raw)
    identity = Identity.generate()
    IDENTITY_FILE.write_text(identity.private_b64u)
    IDENTITY_FILE.chmod(0o600)
    log.info("New Sendspin identity created: %s", identity.peer_id)
    return identity


async def fetch_token_from_ha(entity_id: str) -> str | None:
    """Read the pairing token from a Home Assistant entity (Supervisor API)."""
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token or not entity_id:
        return None
    url = f"http://supervisor/core/api/states/{entity_id}"
    headers = {"Authorization": f"Bearer {token}"}
    try:
        async with (
            aiohttp.ClientSession() as session,
            session.get(
                url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)
            ) as resp,
        ):
            if resp.status != 200:
                log.warning("%s: HA answered %s", entity_id, resp.status)
                return None
            state = (await resp.json()).get("state", "")
    except (TimeoutError, aiohttp.ClientError) as err:
        log.warning("%s: could not read pairing token: %s", entity_id, err)
        return None
    if not state or state in ("unknown", "unavailable"):
        log.warning("%s has no state – is the entity enabled?", entity_id)
        return None
    return state.strip()


def airplay_device_id(zone: ZoneConfig) -> str:
    """Stable, unique 48-bit device ID per zone (locally administered)."""
    digest = hashlib.sha256(f"ap2-sendspin-{zone.name}".encode()).digest()
    raw = bytearray(digest[:6])
    raw[0] = (raw[0] | 0x02) & 0xFE  # locally administered, unicast
    return "0x" + raw.hex().upper() + "L"


def chunk_peak(pcm: bytes) -> int:
    samples = array("h")
    samples.frombytes(pcm)
    return max(max(samples), -min(samples)) if samples else 0


def apply_gain(pcm: bytes, gain: float) -> bytes:
    """Scale S16 PCM (gain ≤ 1, so no clipping)."""
    samples = array("h")
    samples.frombytes(pcm)
    return array("h", [int(v * gain) for v in samples]).tobytes()


def pick_channel(pcm: bytes, channel: int) -> bytes:
    """Stereo S16 → the chosen channel on both sides (stereo pair)."""
    samples = array("h")
    samples.frombytes(pcm)
    one = samples[channel::2]
    out = array("h", bytes(len(pcm)))
    out[0::2] = one
    out[1::2] = one
    return out.tobytes()


def chime_pcm(offset_s: float = 0.0, total_s: float | None = None) -> bytes:
    """Stereo S16 two-tone chime (880 → 1320 Hz) starting at offset_s."""
    tone = []
    for freq, dur in ((880.0, 0.16), (1320.0, 0.26)):
        n = int(SAMPLE_RATE * dur)
        for i in range(n):
            env = min(1.0, i / 480, (n - i) / 2400)  # 10 ms in, 50 ms out
            tone.append(
                int(0.25 * 32767 * env * math.sin(2 * math.pi * freq * i / SAMPLE_RATE))
            )
    start = int(SAMPLE_RATE * offset_s)
    total = int(SAMPLE_RATE * (total_s or offset_s + 0.6))
    total += (-total) % CHUNK_FRAMES  # whole chunks
    mono = array("h", bytes(2 * total))
    part = array("h", tone)[: max(0, total - start)]
    mono[start : start + len(part)] = part
    out = array("h", bytes(4 * total))
    out[0::2] = mono
    out[1::2] = mono
    return out.tobytes()


def parse_hhmm(value: str, default: int) -> int:
    """'22:30' → minutes after midnight."""
    try:
        h, m = (int(x) for x in str(value).split(":"))
        if 0 <= h < 24 and 0 <= m < 60:
            return h * 60 + m
    except ValueError:
        pass
    return default


def in_window(now_min: int, start_min: int, end_min: int) -> bool:
    """True if now is within start…end (may wrap past midnight)."""
    if start_min == end_min:
        return False
    if start_min < end_min:
        return start_min <= now_min < end_min
    return now_min >= start_min or now_min < end_min


def cover_image(data: bytes | None):
    """Cover bytes → PIL image (or None)."""
    if not data:
        return None
    try:
        from PIL import Image  # comes with aiosendspin

        return Image.open(io.BytesIO(data)).convert("RGB")
    except Exception as err:  # noqa: BLE001
        log.debug("cover not readable: %s", err)
        return None


def player_role(client):
    """The Sendspin player role of a client (volume control), if any."""
    roles = client.roles_by_family("player") if client is not None else []
    return roles[0] if roles else None


def write_shairport_config(
    zone: ZoneConfig, latency_ms: int, opts: dict, meta_pipe: Path | None = None
) -> Path:
    # shairport-sync hands out audio exactly latency_ms early; the bridge
    # plays it latency_ms (+ the zone's fine offset) later.
    offset_s = -latency_ms / 1000.0
    ignore_volume = "yes" if opts.get("ignore_airplay_volume") else "no"
    name = zone.name.replace('"', "'")
    conf = f"""// generated by airplay2_sendspin_bridge – changes will be overwritten
general = {{
  name = "{name}";
  port = {AIRPLAY_BASE_PORT + zone.index};
  airplay_device_id = {airplay_device_id(zone)};
  output_backend = "stdout";
  mdns_backend = "avahi";
  interpolation = "auto";
  audio_backend_latency_offset_in_seconds = {offset_s:.3f};
  audio_backend_buffer_desired_length_in_seconds = {SPS_BUFFER_S:.3f};
  nqptp_shared_memory_interface_name = "/nqptp-{zone.slug}";
  volume_range_db = {int(opts.get("volume_range_db", 30))};
  ignore_volume_control = "{ignore_volume}";
}};
sessioncontrol = {{
  session_timeout = 60;
}};
stdout = {{
  output_rate = {SAMPLE_RATE};
  output_format = "S16_LE";
}};
"""
    if meta_pipe is not None:
        conf += f"""metadata = {{
  enabled = "yes";
  include_cover_art = "yes";
  cover_art_cache_directory = "";
  pipe_name = "{meta_pipe}";
  pipe_timeout = 5000;
}};
"""
    ZONE_DIR.mkdir(parents=True, exist_ok=True)
    path = ZONE_DIR / f"{zone.slug}.conf"
    path.write_text(conf, encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# Sendspin side
# --------------------------------------------------------------------------


class SendspinSide:
    """A Sendspin server that connects to the Echos only on demand."""

    def __init__(self, loop: asyncio.AbstractEventLoop, name: str, port: int) -> None:
        self._loop = loop
        self._name = name
        self._port = port
        self.server: SendspinServer | None = None
        self.store: FileServerPairingStore | None = None

    async def start(self) -> None:
        identity = load_identity()
        self.store = await FileServerPairingStore.open(PAIRING_FILE)
        self.server = SendspinServer(
            self._loop, identity, self._name, pairing_store=self.store
        )
        self.server.add_event_listener(self._on_event)
        # discover_clients=False: we only dial the configured Echos ourselves,
        # never the other Sendspin players in the house.
        await self.server.start_server(port=self._port, discover_clients=False)
        log.info("Sendspin server '%s' running on port %s", self._name, self._port)

    def _on_event(self, _server: SendspinServer, event: object) -> None:
        kind = type(event).__name__
        client_id = getattr(event, "client_id", None)
        log.debug("Sendspin event %s (%s)", kind, client_id)
        if client_id and kind == "ClientDisconnectedEvent":
            log.info(
                "Sendspin client %s disconnected (%s)",
                client_id[:12],
                getattr(event, "goodbye_reason", "?"),
            )

    async def ensure_paired(self, zone: SpeakerConfig, token_str: str) -> str | None:
        """Pair once with the token. Returns the client ID."""
        assert self.server and self.store
        try:
            token = decode_psk_token(token_str)
        except Exception as err:  # noqa: BLE001
            log.error("[%s] pairing token unreadable: %s", zone.label, err)
            return None
        client_id = token.client_id
        if await self.store.record_by_client_id(client_id):
            log.info("[%s] already paired (%s…)", zone.label, client_id[:12])
            return client_id

        log.info("[%s] pairing with %s …", zone.label, zone.url)
        attempt = PairingAttempt(
            method=PairMethod.PAIRING_PSK,
            pairing_psk=token.pairing_psk,
            client_id=client_id,
            owner="airplay2_sendspin_bridge",
        )
        try:
            await self.server.connect_to_client_and_wait(
                zone.url,
                connection_reason=ConnectionReason.PAIRING,
                pairing_attempt=attempt,
            )
            for _ in range(30):
                if await self.store.record_by_client_id(client_id):
                    break
                await asyncio.sleep(0.5)
        except Exception as err:  # noqa: BLE001
            log.error("[%s] pairing failed: %s", zone.label, err)
        finally:
            self.server.disconnect_from_client(zone.url)

        if await self.store.record_by_client_id(client_id):
            log.info("[%s] paired.", zone.label)
            return client_id
        log.error("[%s] no pairing record after the attempt.", zone.label)
        return None

    async def acquire(self, zone: SpeakerConfig, client_id: str):
        """Take the Echo over for playback and return (client, group)."""
        assert self.server
        await self.server.connect_to_client_and_wait(
            zone.url, connection_reason=ConnectionReason.PLAYBACK
        )
        # Ready once the client has sent its first state (is_connected).
        # ClientConnectedEvent comes earlier, so poll with a real time limit.
        loop = asyncio.get_running_loop()
        started = loop.time()
        while loop.time() - started < 10:
            client = self.server.get_client(client_id)
            if client is not None and client.is_connected:
                log.info(
                    "[%s] Echo ready after %.2f s", zone.label, loop.time() - started
                )
                return client, client.group
            await asyncio.sleep(0.1)
        raise RuntimeError("Echo did not report ready within 10 s")

    def release(self, zone: SpeakerConfig) -> None:
        """Hand the Echo back so Music Assistant can take it again."""
        if self.server:
            self.server.disconnect_from_client(zone.url)


# --------------------------------------------------------------------------
# Zone: shairport-sync → Sendspin
# --------------------------------------------------------------------------


@dataclass
class Link:
    """Runtime state of one speaker of a zone."""

    cfg: SpeakerConfig
    client_id: str | None = None
    group: object = None
    stream: object = None
    player: object = None
    volume_before: int | None = None  # set when we turned it up for AirPlay
    lowered: bool = False  # back at volume_before for the voice assistant
    volume_target: int = 0  # what we set for AirPlay (0 = untouched)
    restore_task: asyncio.Task | None = field(default=None, repr=False)


class Zone:
    def __init__(self, cfg: ZoneConfig, side: SendspinSide, opts: dict) -> None:
        self.cfg = cfg
        self.side = side
        self.opts = opts
        self.latency_ms = int(opts["latency_ms"])
        self.idle_timeout = float(opts["idle_timeout_s"])
        self.netns_exec = NETNS_FILE.read_text().split() if NETNS_FILE.exists() else []
        self.links = [Link(s) for s in cfg.speakers]
        self.active: list[Link] = []
        self.log = logging.getLogger(f"zone.{cfg.slug}")
        # Calibration (web UI): status and live intervention
        self.streaming = False
        self.cal_mute = False
        self.level_mode = False
        self.sync_locked = False  # time base settled and within ±2 ms
        self.sync_error_ms = 0.0
        self._lvl_buf: list[bytes] = []
        self._lvl_silent = 0
        # Speaker volume while AirPlay plays (0 = leave it alone)
        self.default_volume = int(opts.get("speaker_volume", 100))
        self.report_model = bool(opts.get("report_model", True))
        self.model = ""  # reported to AirPlay (icon), e.g. "Echo 2"
        self.manufacturer = ""
        self._session_lock = asyncio.Lock()
        # Connection quality since the add-on started (tile, integration)
        self.stats = {
            "sessions": 0,
            "audio_s": 0.0,
            "resyncs": 0,
            "drops": 0,
            "lost": 0,
        }
        self.lower_for_voice = bool(opts.get("lower_volume_during_voice", True))
        # Track info from shairport-sync
        self.use_metadata = bool(opts.get("metadata", True))
        self.track: TrackInfo | None = None
        self._cover_sent: bytes | None = None
        self._listeners: list[Callable[[Zone], None]] = []

    # --- listeners (web UI, integration) ---------------------------------
    def add_listener(self, callback: Callable[[Zone], None]) -> None:
        self._listeners.append(callback)

    def _notify(self) -> None:
        for callback in self._listeners:
            try:
                callback(self)
            except Exception:  # noqa: BLE001
                self.log.exception("listener failed")

    @property
    def echo_volume(self) -> int | None:
        """Current volume of the (first) speaker itself (only while playing)."""
        if not self.streaming:
            return None
        for link in self.active:
            if link.player is not None:
                return link.player.volume
        return None

    def set_trim(self, trim_db: float) -> None:
        """Change the volume trim immediately."""
        self.cfg.volume_trim_db = round(float(trim_db), 1)
        self.log.info("volume_trim_db is now %+.1f dB", self.cfg.volume_trim_db)
        self._notify()

    def set_level_mode(self, on: bool) -> None:  # noqa: FBT001
        self.level_mode = on
        self._lvl_buf = []
        self._lvl_silent = 0

    def _level_chunk(self, pcm: bytes) -> bytes:
        """Volume matching: silent during the track's noise, replay it in the gaps."""
        if chunk_peak(pcm) > LEVEL_PEAK_SILENT:
            if self._lvl_silent:  # a new noise block begins
                self._lvl_buf = []
                self._lvl_silent = 0
            if len(self._lvl_buf) < LEVEL_BUF_MAX:
                self._lvl_buf.append(pcm)
            return bytes(len(pcm))
        self._lvl_silent += 1
        k = self._lvl_silent - LEVEL_INJECT_FROM
        if self._lvl_buf and 0 <= k < LEVEL_INJECT_TO - LEVEL_INJECT_FROM:
            return self._lvl_buf[k % len(self._lvl_buf)]
        return pcm

    # --- speaker volume ------------------------------------------------------
    @property
    def speaker_volume(self) -> int:
        """Volume for AirPlay without night mode (per speaker, else global)."""
        if self.cfg.speaker_volume is not None:
            return int(self.cfg.speaker_volume)
        return self.default_volume

    def night_active(self, now_min: int | None = None) -> bool:
        if self.cfg.night_volume is None or not self.cfg.night_enabled:
            return False
        if now_min is None:
            t = time.localtime()
            now_min = t.tm_hour * 60 + t.tm_min
        return in_window(
            now_min,
            parse_hhmm(self.cfg.night_start, 22 * 60),
            parse_hhmm(self.cfg.night_end, 7 * 60),
        )

    def target_volume(self, now_min: int | None = None) -> int:
        """Volume to set while AirPlay plays (0 = leave the speaker alone)."""
        base = self.speaker_volume
        if base <= 0:
            return 0
        if self.night_active(now_min):
            return min(base, int(self.cfg.night_volume))
        return base

    def apply_settings(self, z: dict) -> None:
        """Live change of offset / trim / volume settings from the web UI."""
        if self.cfg.latency_offset_ms != z.get("latency_offset_ms", 0):
            self.set_offset(int(z.get("latency_offset_ms", 0)))
        if self.cfg.volume_trim_db != z.get("volume_trim_db", 0.0):
            self.set_trim(float(z.get("volume_trim_db", 0.0)))
        self.cfg.speaker_volume = z.get("speaker_volume")
        self.cfg.night_volume = z.get("night_volume")
        self.cfg.night_start = z.get("night_start") or "22:00"
        self.cfg.night_end = z.get("night_end") or "07:00"
        self.cfg.night_enabled = bool(z.get("night_enabled", True))
        self._follow_target()
        self._notify()

    def _take_volume(self, link: Link) -> None:
        """Set the speaker to its AirPlay volume, remember the old value."""
        link.volume_before = None
        link.lowered = False
        link.volume_target = 0
        target = self.target_volume()
        player = link.player
        if target <= 0 or player is None:
            return
        if PlayerCommand.VOLUME not in player.state_supported_commands:
            self.log.warning("[%s] does not accept volume commands", link.cfg.label)
            return
        before = player.volume
        link.volume_before = before
        link.volume_target = target
        if before != target:
            player.set_volume(target)
            self.log.info(
                "[%s] volume %d → %d for AirPlay%s",
                link.cfg.label,
                before,
                target,
                " (night mode)" if self.night_active() else "",
            )

    def _follow_target(self) -> None:
        """Night mode began/ended or settings changed while playing."""
        if not self.streaming:
            return
        target = self.target_volume()
        for link in self.active:
            player = link.player
            if player is None or link.volume_target <= 0 or link.lowered:
                continue
            if target == link.volume_target or target <= 0:
                continue
            if abs(player.volume - link.volume_target) > 1:
                continue  # changed on the speaker: leave it
            with contextlib.suppress(Exception):
                player.set_volume(target)
            self.log.info(
                "[%s] volume %d → %d%s",
                link.cfg.label,
                link.volume_target,
                target,
                " (night mode)" if self.night_active() else "",
            )
            link.volume_target = target

    async def _give_volume_back(self, link: Link) -> None:
        """Restore the old speaker volume, unless someone changed it meanwhile."""
        if link.restore_task is not None:
            link.restore_task.cancel()
            link.restore_task = None
        before, link.volume_before = link.volume_before, None
        lowered, link.lowered = link.lowered, False
        target, link.volume_target = link.volume_target, 0
        player = link.player
        if before is None or player is None or lowered or before == target:
            return  # nothing changed, or already back for the voice assistant
        if abs(player.volume - target) > 1:
            self.log.info(
                "[%s] volume was changed during AirPlay (%d), keeping it",
                link.cfg.label,
                player.volume,
            )
            return
        with contextlib.suppress(Exception):
            player.set_volume(before)
            self.log.info("[%s] volume back to %d", link.cfg.label, before)
            await asyncio.sleep(0.3)  # let the command go out before disconnecting

    def on_satellite(self, entity_id: str, state: str) -> None:
        """Voice assistant of one of our speakers changed state."""
        if not self.lower_for_voice or not self.streaming:
            return
        for link in self.active:
            if link.cfg.satellite_entity != entity_id or link.player is None:
                continue
            if link.volume_before is None or link.volume_before == link.volume_target:
                continue  # we did not turn it up, nothing to undo
            if state != "idle":
                if link.restore_task is not None:
                    link.restore_task.cancel()
                    link.restore_task = None
                if not link.lowered:
                    link.lowered = True
                    with contextlib.suppress(Exception):
                        link.player.set_volume(link.volume_before)
                    self.log.info(
                        "[%s] voice assistant %s → volume %d",
                        link.cfg.label,
                        state,
                        link.volume_before,
                    )
            elif link.lowered and link.restore_task is None:
                link.restore_task = asyncio.ensure_future(self._raise_again(link))

    async def _raise_again(self, link: Link) -> None:
        try:
            await asyncio.sleep(VOICE_IDLE_HOLD_S)
            if link.lowered and self.streaming and link.player is not None:
                link.lowered = False
                link.player.set_volume(link.volume_target)
                self.log.info(
                    "[%s] voice assistant idle → volume %d",
                    link.cfg.label,
                    link.volume_target,
                )
        except asyncio.CancelledError:
            pass
        finally:
            if link.restore_task is asyncio.current_task():
                link.restore_task = None

    # --- track info ------------------------------------------------------------
    def on_track(self, info: TrackInfo | None) -> None:
        if info is not None and info.empty:
            info = None
        if info == self.track:
            return
        self.track = info
        if info is not None:
            self.log.info("Now playing: %s – %s", info.artist, info.title)
        if self.streaming:
            asyncio.ensure_future(self._push_track(self.active))
        self._notify()

    async def _push_track(self, links: list[Link]) -> None:
        """Send title/artist/album and, if it changed, the cover."""
        track = self.track
        cover = track.cover if track else None
        new_cover = cover != self._cover_sent
        self._cover_sent = cover
        image = cover_image(cover) if new_cover else None
        for link in links:
            group = link.group
            if group is None:
                continue
            try:
                meta = group.group_role("metadata")
                if meta is not None:
                    if track is None:
                        meta.clear()
                    else:
                        meta.update(
                            title=track.title or None,
                            artist=track.artist or None,
                            album=track.album or None,
                            album_artist=track.album_artist or None,
                        )
                art = group.group_role("artwork")
                if art is not None and new_cover:
                    await art.set_album_artwork(image)
            except Exception as err:  # noqa: BLE001
                self.log.debug("[%s] track info not sent: %s", link.cfg.label, err)

    # --- offset ----------------------------------------------------------------
    def set_offset(self, offset_ms: int) -> None:
        """Change the fine offset immediately (takes effect at once)."""
        self.cfg.latency_offset_ms = int(offset_ms)
        self.log.info("latency_offset_ms is now %+d ms", offset_ms)
        self._notify()

    def lead_ms(self) -> int:
        """Delay from the bridge to the speaker (for the calibration page)."""
        return self._lead_us() // 1000

    def _lead_us(self) -> int:
        """Distance from arrival to play time.

        shairport-sync hands out audio (latency_ms + backend buffer) early,
        so the Echo has to play it that much later. Positive offset = Echo
        plays later, negative = earlier.
        """
        return (
            self.latency_ms + int(SPS_BUFFER_S * 1000) + self.cfg.latency_offset_ms
        ) * 1000

    # --- lifecycle -------------------------------------------------------------
    async def prepare(self) -> None:
        """Model/manufacturer for AirPlay and voice satellites (from HA)."""
        main = self.links[0].cfg
        if self.report_model and main.token_entity:
            info = await find_device_info(main.token_entity)
            raw = (info.get("model") or "").strip()
            if raw:
                self.model = model_name(raw)
                # echo-dot-assist reports its firmware ("hassmic") as the
                # manufacturer; for known hardware use the real maker.
                self.manufacturer = next(
                    (m for p, m in MANUFACTURERS.items() if raw.lower().startswith(p)),
                    (info.get("manufacturer") or "").strip(),
                )
                self.log.info(
                    "AirPlay reports model '%s' by '%s'", self.model, self.manufacturer
                )
        if not self.lower_for_voice:
            return
        for link in self.links:
            if not link.cfg.satellite_entity and link.cfg.token_entity:
                link.cfg.satellite_entity = await find_satellite(link.cfg.token_entity)
            if link.cfg.satellite_entity:
                self.log.info(
                    "[%s] voice satellite: %s",
                    link.cfg.label,
                    link.cfg.satellite_entity,
                )

    async def _pair(self, link: Link) -> None:
        token = link.cfg.pairing_token or await fetch_token_from_ha(
            link.cfg.token_entity
        )
        if not token:
            self.log.error(
                "[%s] no pairing token. Set pairing_token or token_entity "
                "(entity 'Sendspin pairing token' of the Echo, it must be enabled).",
                link.cfg.label,
            )
            return
        while link.client_id is None:
            link.client_id = await self.side.ensure_paired(link.cfg, token)
            if link.client_id is None:
                await asyncio.sleep(60)

    async def run(self) -> None:
        pairing = [asyncio.create_task(self._pair(link)) for link in self.links]
        # Start AirPlay as soon as one speaker is paired; the others follow.
        while not any(link.client_id for link in self.links):
            if all(t.done() for t in pairing):
                self.log.error("No speaker of this zone could be paired.")
                return
            await asyncio.sleep(0.5)

        meta_pipe = None
        if self.use_metadata:
            META_DIR.mkdir(parents=True, exist_ok=True)
            meta_pipe = META_DIR / f"{self.cfg.slug}.pipe"
            reader = MetadataReader(
                meta_pipe, asyncio.get_running_loop(), self.on_track
            )
            reader.ensure_fifo()
            reader.start()
        conf = write_shairport_config(self.cfg, self.latency_ms, self.opts, meta_pipe)
        backoff = 2.0
        while True:
            started = asyncio.get_running_loop().time()
            await self._run_shairport(conf)
            if asyncio.get_running_loop().time() - started > 60:
                backoff = 2.0
            self.log.warning("shairport-sync stopped, restarting in %.0f s", backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)

    async def _run_shairport(self, conf: Path) -> None:
        cmd = [*self.netns_exec, "shairport-sync", "-c", str(conf)]
        if logging.getLogger().level <= logging.DEBUG:
            cmd.append("-v")
        self.log.info("Starting AirPlay zone '%s' (%s)", self.cfg.name, " ".join(cmd))
        env = dict(os.environ)
        if self.model:  # read by the patched shairport-sync (icon in iOS)
            env["SPS_MODEL"] = self.model
            env["SPS_MANUFACTURER"] = self.manufacturer
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stderr_task = asyncio.create_task(self._pump_stderr(proc))
        try:
            await self._pump_audio(proc)
        finally:
            if proc.returncode is None:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), 5)
                except TimeoutError:
                    proc.kill()
            stderr_task.cancel()

    async def _pump_stderr(self, proc: asyncio.subprocess.Process) -> None:
        assert proc.stderr
        async for line in proc.stderr:
            self.log.info("sps: %s", line.decode(errors="replace").rstrip())

    async def _pump_audio(self, proc: asyncio.subprocess.Process) -> None:
        """Read audio continuously; per AirPlay session, take the Echo over."""
        queue: asyncio.Queue[tuple[int, bytes] | None]
        queue = asyncio.Queue(maxsize=QUEUE_MAX)
        reader = asyncio.create_task(self._reader(proc, queue))
        try:
            while True:
                item = await queue.get()
                if item is None:
                    return  # shairport-sync has exited
                await self._session(queue, item)
        finally:
            reader.cancel()

    async def _reader(
        self, proc: asyncio.subprocess.Process, queue: asyncio.Queue
    ) -> None:
        """Read PCM from shairport-sync and timestamp it on arrival.

        Reading never stops, not even while the Echo is being taken over.
        The arrival time is the reference for the play time, not the moment
        of sending.
        """
        assert proc.stdout
        try:
            while True:
                data = await proc.stdout.readexactly(CHUNK_BYTES)
                item = (mono_us(), data)
                if queue.full():  # nobody is reading (e.g. takeover failed)
                    queue.get_nowait()
                queue.put_nowait(item)
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(None)

    async def _next(self, queue: asyncio.Queue) -> tuple[int, bytes] | None:
        """Next chunk, or None on a pause / end."""
        try:
            item = await asyncio.wait_for(queue.get(), timeout=self.idle_timeout)
        except TimeoutError:
            return None
        if item is None:
            queue.put_nowait(None)  # leave the end marker for _pump_audio
        return item

    async def test_tone(self) -> bool:
        """Play a short chime on the speaker(s), without AirPlay.

        A stereo pair plays it left first, then right. Returns False if the
        zone is busy (AirPlay or another test running).
        """
        if self.streaming or self._session_lock.locked():
            return False
        async with self._session_lock:
            ready = [link for link in self.links if link.client_id]
            results = await asyncio.gather(*(self._acquire(link) for link in ready))
            active = [link for link, ok in zip(ready, results, strict=True) if ok]
            if not active:
                return False
            try:
                total = TONE_GAP_S * len(active) + 0.4
                for link in active:
                    link.stream = link.group.start_stream()
                start = active[0].stream.now_us() + TONE_LEAD_US
                step = CHUNK_FRAMES * 4
                for i, link in enumerate(active):
                    pcm = chime_pcm(TONE_GAP_S * i, total)
                    for k in range(0, len(pcm), step):
                        link.stream.prepare_audio(pcm[k : k + step], PCM_FORMAT)
                        await link.stream.commit_audio(
                            play_start_us=start + (k // step) * CHUNK_US
                        )
                self.log.info("Test tone on %s", self.cfg.host)
                await asyncio.sleep(total + TONE_LEAD_US / 1e6 + 0.3)
            finally:
                for link in active:
                    with contextlib.suppress(Exception):
                        await link.group.stop()
                    link.group = link.stream = link.player = None
                    self.side.release(link.cfg)
            return True

    async def _acquire(self, link: Link) -> bool:
        try:
            client, group = await self.side.acquire(link.cfg, link.client_id)
        except Exception as err:  # noqa: BLE001
            self.log.error("[%s] could not take over: %s", link.cfg.label, err)
            return False
        link.group = group
        link.player = player_role(client)
        return True

    async def _release(self, link: Link) -> None:
        with contextlib.suppress(Exception):
            await link.group.stop()
        await self._give_volume_back(link)
        link.group = link.stream = link.player = None
        self.side.release(link.cfg)

    def _pcm_for(self, link: Link, pcm: bytes) -> bytes:
        if link.cfg.channel == "left":
            return pick_channel(pcm, 0)
        if link.cfg.channel == "right":
            return pick_channel(pcm, 1)
        return pcm

    async def _session(self, queue: asyncio.Queue, first: tuple[int, bytes]) -> None:
        async with self._session_lock:  # waits for a running test tone
            await self._session_locked(queue, first)

    async def _session_locked(
        self, queue: asyncio.Queue, first: tuple[int, bytes]
    ) -> None:
        ready = [link for link in self.links if link.client_id]
        self.log.info("AirPlay playback started → taking over %s", self.cfg.host)
        results = await asyncio.gather(*(self._acquire(link) for link in ready))
        active = [link for link, ok in zip(ready, results, strict=True) if ok]
        if not active:
            while await self._next(queue) is not None:
                pass  # throw away until the pause
            return

        for link in active:
            self._take_volume(link)
            link.stream = link.group.start_stream()
        self.active = active
        self.streaming = True
        self.stats["sessions"] += 1
        self._cover_sent = None
        asyncio.ensure_future(self._push_track(active))
        self._notify()
        clock = active[0].stream
        tb = TimeBase(CHUNK_US)
        pending: list[tuple[int, bytes]] = []  # waiting for the time base
        sent = restarts = dropped = 0
        late = False
        loop = asyncio.get_running_loop()
        last_report = loop.time()
        item: tuple[int, bytes] | None = first
        try:
            while item is not None and self.active:
                # Everything that is already waiting goes onto the time base
                items = [item]
                while not queue.empty() and len(items) < 50:
                    nxt = queue.get_nowait()
                    if nxt is None:
                        queue.put_nowait(None)
                        break
                    items.append(nxt)
                for arrival, pcm in items:
                    index, started_over = tb.add(arrival)
                    if started_over:
                        if tb.jumps > restarts:  # pause, skip or flush
                            restarts = tb.jumps
                            self.stats["resyncs"] += 1
                            self.log.debug("time line starts over")
                        pending = []
                    pending.append((index, pcm))
                self.sync_locked = tb.locked
                self.sync_error_ms = tb.error_us / 1000

                if tb.settled and pending:
                    now_srv = clock.now_us()
                    shift = now_srv - mono_us() + self._lead_us()
                    # Too late (takeover, stall): drop from the front
                    k = 0
                    while (
                        k < len(pending)
                        and tb.time_of(pending[k][0]) + shift < now_srv + MIN_LEAD_US
                    ):
                        k += 1
                    if k:
                        dropped += k
                        if sent and not late:  # not the normal takeover gap
                            self.stats["drops"] += 1
                        late = True
                    else:
                        late = False
                    ready, pending = pending[k:], []
                    for j in range(0, len(ready), MAX_BATCH):
                        batch = ready[j : j + MAX_BATCH]
                        play_at = int(tb.time_of(batch[0][0]) + shift)
                        parts = [p for _, p in batch]
                        if self.level_mode:  # volume matching (web UI)
                            parts = [self._level_chunk(p) for p in parts]
                        pcm = b"".join(parts)
                        if self.cal_mute:  # calibration: silent, timing unchanged
                            pcm = bytes(len(pcm))
                        elif self.cfg.volume_trim_db < 0:
                            pcm = apply_gain(pcm, 10 ** (self.cfg.volume_trim_db / 20))
                        for link in list(self.active):
                            try:
                                link.stream.prepare_audio(
                                    self._pcm_for(link, pcm), PCM_FORMAT
                                )
                                await link.stream.commit_audio(play_start_us=play_at)
                            except Exception as err:  # noqa: BLE001
                                # one speaker of a pair gone: the other plays on
                                self.log.error("[%s] lost: %s", link.cfg.label, err)
                                self.active.remove(link)
                                self.stats["lost"] += 1
                                await self._release(link)
                        if not sent:
                            self.log.info(
                                "First audio is sent, plays in %d ms",
                                (play_at - now_srv) // 1000,
                            )
                        sent += len(batch)
                        self.stats["audio_s"] += CHUNK_US * len(batch) / 1e6

                if loop.time() - last_report >= 10:
                    self.log.info(
                        "streaming: %.1f s audio, %d restarts, %d dropped, "
                        "timing %+.1f ms (%s)",
                        sent * CHUNK_US / 1e6,
                        restarts,
                        dropped,
                        tb.error_us / 1000,
                        "locked" if tb.locked else "settling",
                    )
                    last_report = loop.time()
                    self._follow_target()  # night mode starting/ending
                    self._notify()
                item = await self._next(queue)
            self.log.info("AirPlay paused/ended → releasing the speaker(s)")
        except Exception as err:  # noqa: BLE001
            self.log.exception("Streaming error: %s", err)
        finally:
            self.streaming = False
            self.sync_locked = False
            self.cal_mute = False
            self.set_level_mode(False)
            links, self.active = self.active, []
            for link in links:
                await self._release(link)
            self._notify()


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------


def zone_config(index: int, z: dict) -> ZoneConfig:
    name = z["name"]
    main = SpeakerConfig(
        label=name,
        host=z["host"],
        port=int(z["port"]),
        path=z.get("path") or "/sendspin",
        pairing_token=(z.get("pairing_token") or "").strip(),
        token_entity=(z.get("token_entity") or "").strip(),
        satellite_entity=(z.get("satellite_entity") or "").strip(),
    )
    speakers = [main]
    if (z.get("partner_host") or "").strip():
        main.channel = "left"
        main.label = f"{name} L"
        speakers.append(
            SpeakerConfig(
                label=f"{name} R",
                host=z["partner_host"].strip(),
                port=int(z.get("partner_port") or main.port),
                path=z.get("partner_path") or main.path,
                pairing_token=(z.get("partner_pairing_token") or "").strip(),
                token_entity=(z.get("partner_token_entity") or "").strip(),
                channel="right",
                satellite_entity=(z.get("partner_satellite_entity") or "").strip(),
            )
        )
    return ZoneConfig(
        index=index,
        name=name,
        speakers=speakers,
        latency_offset_ms=int(z.get("latency_offset_ms") or 0),
        volume_trim_db=min(0.0, float(z.get("volume_trim_db") or 0)),
        speaker_volume=_opt_int(z.get("speaker_volume")),
        night_volume=_opt_int(z.get("night_volume")),
        night_start=z.get("night_start") or "22:00",
        night_end=z.get("night_end") or "07:00",
        night_enabled=bool(z.get("night_enabled", True)),
    )


def _opt_int(value) -> int | None:
    if value in (None, ""):
        return None
    return max(0, min(100, int(value)))


async def main() -> None:
    opts = load_options()
    level = getattr(logging, str(opts.get("log_level", "info")).upper(), logging.INFO)
    logging.basicConfig(
        level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    logging.getLogger("aiosendspin").setLevel(max(level, logging.INFO))

    loop = asyncio.get_running_loop()
    side = SendspinSide(loop, "AirPlay Bridge", int(opts["sendspin_port"]))
    await side.start()

    zones = [
        Zone(zone_config(i, z), side, opts) for i, z in enumerate(opts.get("zones", []))
    ]
    if not zones:
        log.error("No zones configured.")
        return

    stop = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    # HA's time zone (night mode)
    ha_cfg = await ha_config()
    if ha_cfg.get("time_zone"):
        os.environ["TZ"] = ha_cfg["time_zone"]
        time.tzset()
    # Model for the AirPlay icon, voice satellites
    await asyncio.gather(*(z.prepare() for z in zones))
    background: list[asyncio.Task] = []
    if opts.get("lower_volume_during_voice", True):
        by_entity: dict[str, Zone] = {
            link.cfg.satellite_entity: z
            for z in zones
            for link in z.links
            if link.cfg.satellite_entity
        }

        def on_satellite(entity_id: str, state: str) -> None:
            zone = by_entity.get(entity_id)
            if zone is not None:
                zone.on_satellite(entity_id, state)

        watcher = SatelliteWatcher(list(by_entity), on_satellite)
        background.append(asyncio.create_task(watcher.run(), name="satellites"))

    tasks = [asyncio.create_task(z.run(), name=z.cfg.slug) for z in zones]
    web_runner = await start_web(zones)

    await stop.wait()
    await web_runner.cleanup()
    for t in tasks + background:
        t.cancel()
    await asyncio.gather(*tasks, *background, return_exceptions=True)
    if side.server:
        await side.server.close()


if __name__ == "__main__":
    asyncio.run(main())
