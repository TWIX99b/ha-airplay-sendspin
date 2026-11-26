"""Home Assistant entities per AirPlay speaker via MQTT discovery.

Per speaker one device with:
  * binary_sensor  "AirPlay"            – plays via AirPlay right now
  * sensor         "Titel"              – current title (artist/album as attributes)
  * number         "Feinabgleich"       – latency_offset_ms, live + saved
  * number         "Lautstärke-Anpassung" – volume_trim_db, live + saved

The broker comes from the Supervisor (services: mqtt:want). Without a broker
the bridge simply runs without entities.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re

import aiohttp

log = logging.getLogger("mqtt")

DISCOVERY = "homeassistant"
BASE = "airplay_sendspin"
AVAILABILITY = f"{BASE}/status"

NAMES = {
    "de": {
        "playing": "AirPlay",
        "track": "Titel",
        "offset": "Feinabgleich",
        "trim": "Lautstärke-Anpassung",
    },
    "en": {
        "playing": "AirPlay",
        "track": "Now playing",
        "offset": "Sync offset",
        "trim": "Volume trim",
    },
}


def zone_key(name: str) -> str:
    key = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return key or "speaker"


async def fetch_broker() -> dict | None:
    """MQTT credentials from the Supervisor, or None."""
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        return None
    try:
        async with (
            aiohttp.ClientSession() as session,
            session.get(
                "http://supervisor/services/mqtt",
                headers={"Authorization": f"Bearer {token}"},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp,
        ):
            if resp.status != 200:
                return None
            return (await resp.json()).get("data") or None
    except (TimeoutError, aiohttp.ClientError, ValueError):
        return None


def discovery_messages(zone, version: str, lang: str) -> list[tuple[str, dict]]:
    """(topic, payload) of all discovery configs of one speaker."""
    names = NAMES.get(lang, NAMES["en"])
    key = zone_key(zone.cfg.name)
    base = f"{BASE}/{key}"
    device = {
        "identifiers": [f"ap2s_{key}"],
        "name": f"{zone.cfg.name} AirPlay",
        "manufacturer": "AirPlay 2 → Sendspin Bridge",
        "model": "AirPlay 2 receiver",
        "sw_version": version,
    }
    common = {"availability_topic": AVAILABILITY, "device": device}
    return [
        (
            f"{DISCOVERY}/binary_sensor/ap2s_{key}/playing/config",
            {
                **common,
                "name": names["playing"],
                "unique_id": f"ap2s_{key}_playing",
                "state_topic": f"{base}/playing",
                "payload_on": "ON",
                "payload_off": "OFF",
                "icon": "mdi:cast-audio",
            },
        ),
        (
            f"{DISCOVERY}/sensor/ap2s_{key}/track/config",
            {
                **common,
                "name": names["track"],
                "unique_id": f"ap2s_{key}_track",
                "state_topic": f"{base}/track",
                "value_template": "{{ value_json.title }}",
                "json_attributes_topic": f"{base}/track",
                "icon": "mdi:music",
            },
        ),
        (
            f"{DISCOVERY}/number/ap2s_{key}/offset/config",
            {
                **common,
                "name": names["offset"],
                "unique_id": f"ap2s_{key}_offset",
                "state_topic": f"{base}/offset",
                "command_topic": f"{base}/offset/set",
                "min": -1000,
                "max": 1000,
                "step": 5,
                "mode": "box",
                "unit_of_measurement": "ms",
                "entity_category": "config",
                "icon": "mdi:timer-sync-outline",
            },
        ),
        (
            f"{DISCOVERY}/number/ap2s_{key}/trim/config",
            {
                **common,
                "name": names["trim"],
                "unique_id": f"ap2s_{key}_trim",
                "state_topic": f"{base}/trim",
                "command_topic": f"{base}/trim/set",
                "min": -30,
                "max": 0,
                "step": 0.5,
                "mode": "slider",
                "unit_of_measurement": "dB",
                "entity_category": "config",
                "icon": "mdi:volume-equal",
            },
        ),
    ]


def state_messages(zone) -> list[tuple[str, str]]:
    base = f"{BASE}/{zone_key(zone.cfg.name)}"
    track = zone.track
    info = {
        "title": (track.title if track else "") or "",
        "artist": (track.artist if track else "") or "",
        "album": (track.album if track else "") or "",
    }
    return [
        (f"{base}/playing", "ON" if zone.streaming else "OFF"),
        (f"{base}/track", json.dumps(info, ensure_ascii=False)),
        (f"{base}/offset", str(zone.cfg.latency_offset_ms)),
        (f"{base}/trim", f"{zone.cfg.volume_trim_db:.1f}"),
    ]


class MqttBridge:
    def __init__(self, zones: list, save_option, version: str, lang: str) -> None:
        self.zones = zones
        self.by_key = {zone_key(z.cfg.name): z for z in zones}
        self.save_option = save_option  # async (index, key, value) -> bool
        self.version = version
        self.lang = lang
        self.client = None
        self.loop: asyncio.AbstractEventLoop | None = None

    async def start(self) -> bool:
        broker = await fetch_broker()
        if not broker:
            log.info("No MQTT broker in Home Assistant – no entities.")
            return False
        import paho.mqtt.client as mqtt  # in the image; optional elsewhere

        self.loop = asyncio.get_running_loop()
        client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2, client_id="airplay2-sendspin-bridge"
        )
        if broker.get("username"):
            client.username_pw_set(broker["username"], broker.get("password"))
        if broker.get("ssl"):
            client.tls_set()
        client.will_set(AVAILABILITY, "offline", qos=1, retain=True)
        client.on_connect = self._on_connect
        client.on_message = self._on_message
        client.reconnect_delay_set(2, 60)
        client.connect_async(broker["host"], int(broker.get("port", 1883)), 60)
        client.loop_start()
        self.client = client
        for zone in self.zones:
            zone.add_listener(self.publish_state)
        log.info("MQTT: connecting to %s:%s", broker["host"], broker.get("port"))
        return True

    def stop(self) -> None:
        if self.client is None:
            return
        self.client.publish(AVAILABILITY, "offline", qos=1, retain=True)
        self.client.disconnect()
        self.client.loop_stop()

    # --- paho thread ---------------------------------------------------------
    def _on_connect(self, client, _userdata, _flags, reason_code, _props=None):
        if getattr(reason_code, "is_failure", False):
            log.warning("MQTT connect failed: %s", reason_code)
            return
        log.info("MQTT connected")
        client.subscribe(
            [(f"{BASE}/+/offset/set", 1), (f"{BASE}/+/trim/set", 1)]
            + [(f"{DISCOVERY}/status", 1)]
        )
        self._announce()

    def _on_message(self, _client, _userdata, msg) -> None:
        payload = msg.payload.decode(errors="replace").strip()
        if self.loop is not None:
            self.loop.call_soon_threadsafe(self.handle, msg.topic, payload)

    # --- logic (event loop) --------------------------------------------------
    def _announce(self) -> None:
        for zone in self.zones:
            for topic, payload in discovery_messages(zone, self.version, self.lang):
                self.client.publish(topic, json.dumps(payload), qos=1, retain=True)
        self.client.publish(AVAILABILITY, "online", qos=1, retain=True)
        for zone in self.zones:
            self.publish_state(zone)

    def publish_state(self, zone) -> None:
        if self.client is None:
            return
        for topic, payload in state_messages(zone):
            self.client.publish(topic, payload, qos=0, retain=True)

    def handle(self, topic: str, payload: str) -> None:
        if topic == f"{DISCOVERY}/status":
            if payload == "online":  # HA restarted: announce again
                self._announce()
            return
        parts = topic.split("/")
        if len(parts) != 4 or parts[0] != BASE or parts[3] != "set":
            return
        zone = self.by_key.get(parts[1])
        if zone is None:
            return
        try:
            value = float(payload)
        except ValueError:
            return
        if parts[2] == "offset":
            offset = int(max(-1000, min(1000, round(value))))
            zone.set_offset(offset)
            asyncio.ensure_future(
                self.save_option(zone.cfg.index, "latency_offset_ms", offset)
            )
        elif parts[2] == "trim":
            trim = round(max(-30.0, min(0.0, value)), 1)
            zone.set_trim(trim)
            asyncio.ensure_future(
                self.save_option(zone.cfg.index, "volume_trim_db", trim)
            )
