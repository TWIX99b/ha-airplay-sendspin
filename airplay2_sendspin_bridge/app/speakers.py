"""Speaker list for the web UI: suggestions from HA, validation, saving.

The speakers stay in the add-on options (`zones`), so they are also visible
in the add-on's configuration tab. The page edits them through this module;
offset and volume trim apply live, anything else needs a restart.
"""

from __future__ import annotations

import json
from urllib.parse import urlparse

DEFAULT_PORT = 28928
DEFAULT_PATH = "/sendspin"

# Changing any of these means new AirPlay receivers / connections → restart
STRUCT_KEYS = (
    "name",
    "host",
    "port",
    "path",
    "pairing_token",
    "token_entity",
    "satellite_entity",
    "partner_host",
    "partner_port",
    "partner_path",
    "partner_pairing_token",
    "partner_token_entity",
    "partner_satellite_entity",
)
TOKENS = ("pairing_token", "partner_pairing_token")

MODEL_NAMES = {
    "echo-2": "Echo 2",
    "echo-dot-2": "Echo Dot 2",
    "echo-dot-3": "Echo Dot 3",
    "echo-dot-4": "Echo Dot 4",
    "echo-show-5": "Echo Show 5",
    "echo-show-8": "Echo Show 8",
    "echo-plus": "Echo Plus",
}

# Finds every echo-dot-assist (any Sendspin speaker with a pairing-token
# entity) together with its IP, voice satellite, model and area.
DISCOVERY_TEMPLATE = """
{%- set ns = namespace(out=[]) -%}
{%- for s in states.sensor
      if s.entity_id.endswith('_sendspin_pairing_token') -%}
  {%- set dev = device_id(s.entity_id) -%}
  {%- set ents = device_entities(dev) if dev else [] -%}
  {%- set web = ents | select('search', '_web_ui_address$')
                     | first | default('') -%}
  {%- set sat = ents | select('match', 'assist_satellite\\\\.')
                     | first | default('') -%}
  {%- set ns.out = ns.out + [{
      'token_entity': s.entity_id,
      'device': (device_attr(dev, 'name_by_user') or device_attr(dev, 'name'))
                if dev else s.name,
      'model': device_attr(dev, 'model') if dev else none,
      'area': area_name(dev) if dev else none,
      'web': states(web) if web else '',
      'satellite': sat,
      'has_token': s.state not in ['unknown', 'unavailable', '']}] -%}
{%- endfor -%}
{{ ns.out | to_json }}
"""


def model_name(model: str | None) -> str:
    if not model:
        return ""
    return MODEL_NAMES.get(model, model.replace("-", " ").title())


def public_zone(z: dict) -> dict:
    """A speaker as the page sees it (without the secret pairing tokens)."""
    out = {k: v for k, v in z.items() if k not in TOKENS}
    for key in TOKENS:
        out[f"has_{key}"] = bool(z.get(key))
    return out


def _text(raw: dict, key: str) -> str:
    return str(raw.get(key) or "").strip()


def _port(value, default: int) -> int:
    if value in (None, ""):
        return default
    port = int(value)
    if not 1 <= port <= 65535:
        raise ValueError(f"invalid port {port}")
    return port


def clean_zone(raw: dict, old: dict | None) -> dict:
    """Validate one speaker from the page. Raises ValueError with a reason."""
    old = old or {}
    name = _text(raw, "name")
    if not name:
        raise ValueError("name missing")
    if len(name) > 40:
        raise ValueError(f"{name}: name longer than 40 characters")
    host = _text(raw, "host")
    if not host:
        raise ValueError(f"{name}: IP address missing")
    zone: dict = {
        "name": name,
        "host": host,
        "port": _port(raw.get("port"), DEFAULT_PORT),
        "path": _text(raw, "path") or DEFAULT_PATH,
    }
    # A token the page doesn't show is kept unless it was cleared on purpose
    token = _text(raw, "pairing_token")
    if not token and raw.get("keep_pairing_token", True):
        token = old.get("pairing_token", "")
    if token:
        zone["pairing_token"] = token
    for key in ("token_entity", "satellite_entity"):
        if value := _text(raw, key):
            zone[key] = value
    if "pairing_token" not in zone and "token_entity" not in zone:
        raise ValueError(f"{name}: pairing token or token entity missing")
    offset = int(raw.get("latency_offset_ms") or 0)
    if not -1000 <= offset <= 1000:
        raise ValueError(f"{name}: offset must be within ±1000 ms")
    zone["latency_offset_ms"] = offset
    trim = round(float(raw.get("volume_trim_db") or 0), 1)
    if not -30 <= trim <= 0:
        raise ValueError(f"{name}: volume trim must be within −30 … 0 dB")
    zone["volume_trim_db"] = trim

    # Per-speaker AirPlay volume and night mode (live, no restart)
    for key in ("speaker_volume", "night_volume"):
        value = raw.get(key)
        if value in (None, ""):
            continue
        value = int(value)
        if not 0 <= value <= 100:
            raise ValueError(f"{name}: {key} must be within 0 … 100 %")
        zone[key] = value
    if raw.get("night_enabled") is not None and not raw.get("night_enabled"):
        zone["night_enabled"] = False  # night mode switched off in HA
    if "night_volume" in zone:
        for key, default in (("night_start", "22:00"), ("night_end", "07:00")):
            value = _text(raw, key) or default
            if not _hhmm(value):
                raise ValueError(f"{name}: {key} must be HH:MM")
            zone[key] = value

    partner = _text(raw, "partner_host")
    if partner:
        zone["partner_host"] = partner
        zone["partner_port"] = _port(raw.get("partner_port"), zone["port"])
        zone["partner_path"] = _text(raw, "partner_path") or zone["path"]
        token = _text(raw, "partner_pairing_token")
        if not token and raw.get("keep_partner_pairing_token", True):
            token = old.get("partner_pairing_token", "")
        if token:
            zone["partner_pairing_token"] = token
        for key in ("partner_token_entity", "partner_satellite_entity"):
            if value := _text(raw, key):
                zone[key] = value
        if "partner_pairing_token" not in zone and "partner_token_entity" not in zone:
            raise ValueError(f"{name}: token for the stereo partner missing")
    return zone


def _hhmm(value: str) -> bool:
    try:
        h, m = (int(x) for x in value.split(":"))
    except ValueError:
        return False
    return 0 <= h < 24 and 0 <= m < 60


def clean_all(raw_zones: list, old_zones: list) -> list[dict]:
    """Validate the whole list (unique names and speakers)."""
    if not isinstance(raw_zones, list):
        raise ValueError("zones must be a list")
    zones = []
    for raw in raw_zones:
        orig = raw.get("orig")
        old = (
            old_zones[orig]
            if isinstance(orig, int) and 0 <= orig < len(old_zones)
            else None
        )
        zones.append(clean_zone(raw, old))
    names = [z["name"].casefold() for z in zones]
    if len(set(names)) != len(names):
        raise ValueError("two speakers have the same name")
    hosts = [z["host"] for z in zones] + [
        z["partner_host"] for z in zones if "partner_host" in z
    ]
    if len(set(hosts)) != len(hosts):
        raise ValueError("a speaker is used twice")
    return zones


def needs_restart(old_zones: list, new_zones: list) -> bool:
    def shape(zones):
        return [{k: z.get(k) for k in STRUCT_KEYS} for z in zones]

    return shape(old_zones) != shape(new_zones)


def parse_discovery(rendered: str, zones: list) -> list[dict]:
    """Template result → speaker suggestions, marked if already configured."""
    try:
        found = json.loads(rendered)
    except (TypeError, ValueError):
        return []
    used_hosts = {z.get("host") for z in zones} | {z.get("partner_host") for z in zones}
    used_entities = {z.get("token_entity") for z in zones} | {
        z.get("partner_token_entity") for z in zones
    }
    out = []
    for item in found if isinstance(found, list) else []:
        host = urlparse(item.get("web") or "").hostname or ""
        area = item.get("area") or ""
        out.append(
            {
                "token_entity": item.get("token_entity", ""),
                "device": item.get("device") or "",
                "model": item.get("model") or "",
                "model_name": model_name(item.get("model")),
                "area": area,
                "host": host,
                "port": DEFAULT_PORT,
                "satellite_entity": item.get("satellite") or "",
                "has_token": bool(item.get("has_token")),
                "suggested_name": f"{area} Echo"
                if area
                else (item.get("device") or ""),
                "configured": host in used_hosts
                or item.get("token_entity") in used_entities,
            }
        )
    out.sort(key=lambda s: (s["configured"], s["area"] or s["device"]))
    return out


def add_disabled(found: list[dict], disabled: list[dict]) -> list[dict]:
    """Append speakers whose token entity is disabled (to be enabled)."""
    known = {f["token_entity"] for f in found}
    extra = [
        {
            "token_entity": d["token_entity"],
            "device": d.get("device") or "",
            "model": d.get("model") or "",
            "model_name": model_name(d.get("model")),
            "area": d.get("area") or "",
            "host": "",
            "port": DEFAULT_PORT,
            "satellite_entity": "",
            "has_token": False,
            "disabled": True,
            "suggested_name": "",
            "configured": False,
        }
        for d in disabled
        if d.get("token_entity") not in known
    ]
    extra.sort(key=lambda s: s["area"] or s["device"])
    # after the ready ones, before those already set up
    ready = [f for f in found if not f.get("configured")]
    done = [f for f in found if f.get("configured")]
    return ready + extra + done
