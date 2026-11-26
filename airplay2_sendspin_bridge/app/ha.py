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


async def ha_language() -> str:
    """HA's language ('de', 'en', …), for entity names."""
    headers = _headers()
    if headers is None:
        return "en"
    try:
        async with (
            aiohttp.ClientSession() as session,
            session.get(
                f"{CORE_API}/config",
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp,
        ):
            data = await resp.json()
            return str(data.get("language") or "en").split("-")[0]
    except (TimeoutError, aiohttp.ClientError, ValueError):
        return "en"


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
