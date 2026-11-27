"""Home Assistant helpers via the Supervisor proxy.

* find the voice satellite (assist_satellite.*) on the same device as the
  speaker's pairing-token entity,
* watch satellites over the WebSocket API (subscribe_trigger), so the bridge
  can turn the speaker back down while the voice assistant listens or talks.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Callable

import aiohttp

log = logging.getLogger("ha")

CORE_API = "http://supervisor/core/api"
CORE_WS = "http://supervisor/core/websocket"


def _headers() -> dict[str, str] | None:
    token = os.environ.get("SUPERVISOR_TOKEN")
    return {"Authorization": f"Bearer {token}"} if token else None


async def render_template(template: str) -> str | None:
    headers = _headers()
    if headers is None:
        return None
    try:
        async with (
            aiohttp.ClientSession() as session,
            session.post(
                f"{CORE_API}/template",
                headers=headers,
                json={"template": template},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp,
        ):
            if resp.status != 200:
                log.warning("template: HA answered %s", resp.status)
                return None
            return (await resp.text()).strip()
    except (TimeoutError, aiohttp.ClientError) as err:
        log.warning("template: %s", err)
        return None


async def find_satellite(token_entity: str) -> str:
    """assist_satellite.* of the device the token entity belongs to ('' if none)."""
    if not token_entity:
        return ""
    entity = json.dumps(token_entity)
    result = await render_template(
        "{{ device_entities(device_id(" + entity + ")) "
        "| select('match', 'assist_satellite\\\\.') | list | first | default('') }}"
    )
    return result or ""


async def find_device_info(token_entity: str) -> dict:
    """Model and manufacturer of the device the token entity belongs to."""
    entity = json.dumps(token_entity)
    result = await render_template(
        "{% set d = device_id(" + entity + ") %}"
        "{{ {'model': device_attr(d, 'model') if d else none,"
        " 'manufacturer': device_attr(d, 'manufacturer') if d else none} | to_json }}"
    )
    try:
        return json.loads(result) if result else {}
    except ValueError:
        return {}


async def ha_config() -> dict:
    """HA's /api/config (time_zone, language, …); {} if not reachable."""
    headers = _headers()
    if headers is None:
        return {}
    try:
        async with (
            aiohttp.ClientSession() as session,
            session.get(
                f"{CORE_API}/config",
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp,
        ):
            return await resp.json()
    except (TimeoutError, aiohttp.ClientError, ValueError):
        return {}


TOKEN_SUFFIX = "_sendspin_pairing_token"


async def ws_commands(*commands: dict) -> list[dict]:
    """Run WebSocket commands one after another; their results (or [])."""
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        return []
    results = []
    try:
        async with (
            aiohttp.ClientSession() as session,
            session.ws_connect(
                CORE_WS, timeout=aiohttp.ClientWSTimeout(ws_close=10)
            ) as ws,
        ):
            await ws.receive_json(timeout=10)  # auth_required
            await ws.send_json({"type": "auth", "access_token": token})
            if (await ws.receive_json(timeout=10)).get("type") != "auth_ok":
                return []
            for i, cmd in enumerate(commands, start=1):
                await ws.send_json({"id": i, **cmd})
                while True:
                    reply = await ws.receive_json(timeout=15)
                    if reply.get("id") == i and reply.get("type") == "result":
                        break
                results.append(reply)
    except (TimeoutError, aiohttp.ClientError, ValueError, TypeError) as err:
        log.warning("HA WebSocket command: %s", err)
    return results


async def disabled_token_entities() -> list[dict]:
    """Sendspin speakers whose pairing-token entity is disabled in HA.

    Disabled entities have no state, so the template-based discovery cannot
    see them; the entity and device registries can.
    """
    replies = await ws_commands(
        {"type": "config/entity_registry/list"},
        {"type": "config/device_registry/list"},
        {"type": "config/area_registry/list"},
    )
    if len(replies) != 3 or not all(r.get("success") for r in replies):
        return []
    entities, devices, areas = (r.get("result") or [] for r in replies)
    dev_by_id = {d.get("id"): d for d in devices}
    area_names = {a.get("area_id"): a.get("name") for a in areas}
    out = []
    for e in entities:
        eid = e.get("entity_id", "")
        if not eid.startswith("sensor.") or not eid.endswith(TOKEN_SUFFIX):
            continue
        if not e.get("disabled_by"):
            continue
        dev = dev_by_id.get(e.get("device_id")) or {}
        out.append(
            {
                "token_entity": eid,
                "device": dev.get("name_by_user") or dev.get("name") or eid,
                "model": dev.get("model"),
                "area": area_names.get(e.get("area_id") or dev.get("area_id")),
                "disabled_by": e.get("disabled_by"),
            }
        )
    return out


async def enable_entity(entity_id: str) -> dict | None:
    """Enable an entity; HA reloads its integration after a short delay."""
    replies = await ws_commands(
        {
            "type": "config/entity_registry/update",
            "entity_id": entity_id,
            "disabled_by": None,
        }
    )
    if not replies or not replies[0].get("success"):
        log.warning("Enabling %s failed: %s", entity_id, replies and replies[0])
        return None
    result = replies[0].get("result") or {}
    log.info("Enabled %s in Home Assistant", entity_id)
    return {"reload_delay": result.get("reload_delay")}


class SatelliteWatcher:
    """Calls callback(entity_id, state) whenever a watched satellite changes."""

    def __init__(
        self, entity_ids: list[str], callback: Callable[[str, str], None]
    ) -> None:
        self.entity_ids = sorted(set(entity_ids))
        self.callback = callback

    async def run(self) -> None:
        if not self.entity_ids or _headers() is None:
            return
        backoff = 2.0
        while True:
            try:
                await self._session()
                backoff = 2.0
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001
                log.warning("HA WebSocket: %s (reconnecting)", err)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)

    async def _session(self) -> None:
        token = os.environ["SUPERVISOR_TOKEN"]
        async with (
            aiohttp.ClientSession() as session,
            session.ws_connect(CORE_WS, heartbeat=30) as ws,
        ):
            await ws.receive_json()  # auth_required
            await ws.send_json({"type": "auth", "access_token": token})
            reply = await ws.receive_json()
            if reply.get("type") != "auth_ok":
                raise RuntimeError(f"auth failed: {reply.get('type')}")
            await ws.send_json(
                {
                    "id": 1,
                    "type": "subscribe_trigger",
                    "trigger": {"platform": "state", "entity_id": self.entity_ids},
                }
            )
            log.info("Watching voice satellites: %s", ", ".join(self.entity_ids))
            async for msg in ws:
                if msg.type != aiohttp.WSMsgType.TEXT:
                    break
                data = json.loads(msg.data)
                if data.get("type") == "result" and not data.get("success", True):
                    raise RuntimeError(f"subscribe failed: {data.get('error')}")
                if data.get("type") != "event":
                    continue
                trigger = data.get("event", {}).get("variables", {}).get("trigger", {})
                entity = trigger.get("entity_id")
                to_state = (trigger.get("to_state") or {}).get("state")
                if entity and to_state:
                    self.callback(entity, to_state)
