"""AirPlay 2 → Sendspin bridge.

For every configured zone:
  * starts a shairport-sync instance (AirPlay 2, output to stdout) inside the
    network namespace ``ap2``,
  * reads the raw PCM (48 kHz, S16_LE, stereo),
  * stamps each chunk with a play time in the Sendspin server clock and
    pushes it with aiosendspin to the Sendspin player (e.g. an Echo with
    echo-dot-assist).

Sync with HomePods works like this: shairport-sync hands each sample out
``latency_ms`` EARLY (negative ``audio_backend_latency_offset_in_seconds``),
and the bridge tells the Echo to play it exactly ``latency_ms`` after it
arrived. So it plays at the moment AirPlay intended.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import signal
import time
from dataclasses import dataclass
from pathlib import Path

import aiohttp
from aiosendspin.audio.format import AudioFormat
from aiosendspin.models.types import ConnectionReason, PairMethod
from aiosendspin.noise import (
    FileServerPairingStore,
    Identity,
    b64url_decode,
    decode_psk_token,
)
from aiosendspin.noise.pairing import PairingAttempt

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

SAMPLE_RATE = 48_000
CHANNELS = 2
BYTES_PER_FRAME = CHANNELS * 2  # S16_LE
CHUNK_FRAMES = 960  # 20 ms
CHUNK_BYTES = CHUNK_FRAMES * BYTES_PER_FRAME
CHUNK_US = CHUNK_FRAMES * 1_000_000 // SAMPLE_RATE
HARD_RESYNC_US = 250_000  # jump (skip/seek) → resync immediately
SOFT_RESYNC_US = 60_000  # lasting offset (smoothed) → resync
EMA_ALPHA = 0.02  # smoothing of the timing error
MIN_LEAD_US = 80_000  # anything due sooner is dropped
MAX_BATCH = 5  # up to 100 ms per commit
QUEUE_MAX = 1500  # 30 s buffer between reader and sender
AIRPLAY_BASE_PORT = 7000
# shairport-sync keeps this much audio "in the output buffer" ahead of
# the play time; the bridge adds it back on.
SPS_BUFFER_S = 1.0
PCM_FORMAT = AudioFormat(sample_rate=SAMPLE_RATE, bit_depth=16, channels=CHANNELS)

log = logging.getLogger("bridge")


@dataclass
class ZoneConfig:
    index: int
    name: str
    host: str
    port: int
    path: str
    pairing_token: str
    token_entity: str
    latency_offset_ms: int

    @property
    def url(self) -> str:
        path = self.path if self.path.startswith("/") else f"/{self.path}"
        return f"ws://{self.host}:{self.port}{path}"

    @property
    def slug(self) -> str:
        safe = "".join(c if c.isalnum() else "-" for c in self.name.lower())
        return f"z{self.index}-{safe.strip('-') or 'zone'}"


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


def write_shairport_config(zone: ZoneConfig, latency_ms: int, opts: dict) -> Path:
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

    async def ensure_paired(self, zone: ZoneConfig, token_str: str) -> str | None:
        """Pair once with the token. Returns the client ID."""
        assert self.server and self.store
        try:
            token = decode_psk_token(token_str)
        except Exception as err:  # noqa: BLE001
            log.error("[%s] pairing token unreadable: %s", zone.name, err)
            return None
        client_id = token.client_id
        if await self.store.record_by_client_id(client_id):
            log.info("[%s] already paired (%s…)", zone.name, client_id[:12])
            return client_id

        log.info("[%s] pairing with %s …", zone.name, zone.url)
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
            log.error("[%s] pairing failed: %s", zone.name, err)
        finally:
            self.server.disconnect_from_client(zone.url)

        if await self.store.record_by_client_id(client_id):
            log.info("[%s] paired.", zone.name)
            return client_id
        log.error("[%s] no pairing record after the attempt.", zone.name)
        return None

    async def acquire(self, zone: ZoneConfig, client_id: str):
        """Take the Echo over for playback and return its group."""
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
                    "[%s] Echo ready after %.2f s", zone.name, loop.time() - started
                )
                return client.group
            await asyncio.sleep(0.1)
        raise RuntimeError("Echo did not report ready within 10 s")

    def release(self, zone: ZoneConfig) -> None:
        """Hand the Echo back so Music Assistant can take it again."""
        if self.server:
            self.server.disconnect_from_client(zone.url)


# --------------------------------------------------------------------------
# Zone: shairport-sync → Sendspin
# --------------------------------------------------------------------------


class Zone:
    def __init__(self, cfg: ZoneConfig, side: SendspinSide, opts: dict) -> None:
        self.cfg = cfg
        self.side = side
        self.opts = opts
        self.latency_ms = int(opts["latency_ms"])
        self.idle_timeout = float(opts["idle_timeout_s"])
        self.netns_exec = NETNS_FILE.read_text().split() if NETNS_FILE.exists() else []
        self.client_id: str | None = None
        self.log = logging.getLogger(f"zone.{cfg.slug}")

    async def run(self) -> None:
        token = self.cfg.pairing_token or await fetch_token_from_ha(
            self.cfg.token_entity
        )
        if not token:
            self.log.error(
                "No pairing token. Set pairing_token or token_entity "
                "(entity 'Sendspin pairing token' of the Echo, it must be enabled)."
            )
            return
        while self.client_id is None:
            self.client_id = await self.side.ensure_paired(self.cfg, token)
            if self.client_id is None:
                await asyncio.sleep(60)

        conf = write_shairport_config(self.cfg, self.latency_ms, self.opts)
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
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
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

    async def _session(self, queue: asyncio.Queue, first: tuple[int, bytes]) -> None:
        assert self.client_id
        self.log.info("AirPlay playback started → taking over %s", self.cfg.host)
        try:
            group = await self.side.acquire(self.cfg, self.client_id)
        except Exception as err:  # noqa: BLE001
            self.log.error("Could not take over the Echo: %s", err)
            while await self._next(queue) is not None:
                pass  # throw away until the pause
            return

        stream = group.start_stream()
        # shairport-sync hands out audio (latency_ms + backend buffer) early,
        # so the Echo has to play it that much later.
        # Positive offset = Echo plays later, negative = earlier.
        lead_us = (
            self.latency_ms + int(SPS_BUFFER_S * 1000) + self.cfg.latency_offset_ms
        ) * 1000
        next_ts: int | None = None
        err_avg = 0.0
        sent = resyncs = dropped = 0
        loop = asyncio.get_running_loop()
        last_report = loop.time()
        item: tuple[int, bytes] | None = first
        try:
            while item is not None:
                # Gather what is already waiting (up to MAX_BATCH chunks)
                arrival, pcm = item
                parts = [pcm]
                while len(parts) < MAX_BATCH and not queue.empty():
                    nxt = queue.get_nowait()
                    if nxt is None:
                        queue.put_nowait(None)
                        break
                    parts.append(nxt[1])

                # Arrival time (monotonic) → Sendspin server clock
                now_srv = stream.now_us()
                target = arrival + (now_srv - mono_us()) + lead_us

                if target < now_srv + MIN_LEAD_US:
                    # Arrived during the takeover and is already too late
                    dropped += len(parts)
                    next_ts = None
                elif next_ts is None:
                    next_ts = target
                    err_avg = 0.0
                    self.log.info(
                        "First audio is sent, plays in %d ms",
                        (target - now_srv) // 1000,
                    )
                else:
                    err = target - next_ts
                    err_avg += (err - err_avg) * EMA_ALPHA
                    # Jitter of single chunks is ignored; resync only for real
                    # jumps (skip, pause) or a lasting offset.
                    if abs(err) > HARD_RESYNC_US or abs(err_avg) > SOFT_RESYNC_US:
                        resyncs += 1
                        self.log.debug(
                            "resync: err=%+d µs avg=%+d µs", err, int(err_avg)
                        )
                        next_ts = target
                        err_avg = 0.0

                if next_ts is not None:
                    stream.prepare_audio(b"".join(parts), PCM_FORMAT)
                    await stream.commit_audio(play_start_us=next_ts)
                    next_ts += CHUNK_US * len(parts)
                    sent += len(parts)

                if loop.time() - last_report >= 10:
                    self.log.info(
                        "streaming: %.1f s audio, %d resyncs, %d dropped, "
                        "avg offset %+.1f ms",
                        sent * CHUNK_US / 1e6,
                        resyncs,
                        dropped,
                        err_avg / 1000,
                    )
                    last_report = loop.time()

                item = await self._next(queue)
            self.log.info("AirPlay paused/ended → releasing the Echo")
        except Exception as err:  # noqa: BLE001
            self.log.exception("Streaming error: %s", err)
        finally:
            with contextlib.suppress(Exception):
                await group.stop()
            self.side.release(self.cfg)


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------


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

    zones = []
    for i, z in enumerate(opts.get("zones", [])):
        cfg = ZoneConfig(
            index=i,
            name=z["name"],
            host=z["host"],
            port=int(z["port"]),
            path=z.get("path") or "/sendspin",
            pairing_token=(z.get("pairing_token") or "").strip(),
            token_entity=(z.get("token_entity") or "").strip(),
            latency_offset_ms=int(z.get("latency_offset_ms") or 0),
        )
        zones.append(Zone(cfg, side, opts))
    if not zones:
        log.error("No zones configured.")
        return

    stop = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    tasks = [asyncio.create_task(z.run(), name=z.cfg.slug) for z in zones]
    await stop.wait()
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    if side.server:
        await side.server.close()


if __name__ == "__main__":
    asyncio.run(main())
