"""Client for the API of the AirPlay 2 → Sendspin Bridge add-on.

The add-on pushes its state over a WebSocket (/int/ws) whenever something
changes (playing, track, offset, dropouts …); commands are small POSTs.
Every request carries the token the add-on handed over at discovery.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

import aiohttp

from .const import LOGGER


class BridgeError(Exception):
    """The add-on could not be reached or answered with an error."""


class BridgeAuthError(BridgeError):
    """The token was not accepted."""


class BridgeClient:
    def __init__(
        self, session: aiohttp.ClientSession, host: str, port: int, token: str
    ) -> None:
        self._session = session
        self._base = f"http://{host}:{port}/int"
        self._headers = {"Authorization": f"Bearer {token}"}

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            async with self._session.request(
                method,
                f"{self._base}{path}",
                headers=self._headers,
                timeout=aiohttp.ClientTimeout(total=15),
                **kwargs,
            ) as resp:
                if resp.status == 401:
                    raise BridgeAuthError("token rejected")
                if resp.status == 404 and path.endswith("/cover"):
                    return None
                if resp.status >= 400:
                    raise BridgeError(f"HTTP {resp.status}: {await resp.text()}")
                if resp.content_type == "application/json":
                    return await resp.json()
                return await resp.read()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise BridgeError(str(err)) from err

    async def state(self) -> dict:
        return await self._request("GET", "/state")

    async def set(self, index: int, **changes: Any) -> dict:
        return await self._request("POST", f"/zones/{index}/set", json=changes)

    async def test_tone(self, index: int) -> None:
        await self._request("POST", f"/zones/{index}/test")

    async def cover(self, index: int) -> bytes | None:
        return await self._request("GET", f"/zones/{index}/cover")

    async def listen(
        self,
        on_state: Callable[[list[dict]], None],
        on_lost: Callable[[Exception], None],
    ) -> None:
        """Follow the add-on's state; reconnects until cancelled."""
        backoff = 1.0
        while True:
            try:
                async with self._session.ws_connect(
                    f"{self._base}/ws", headers=self._headers, heartbeat=30
                ) as ws:
                    backoff = 1.0
                    async for msg in ws:
                        if msg.type != aiohttp.WSMsgType.TEXT:
                            break
                        data = json.loads(msg.data)
                        if data.get("type") == "state":
                            on_state(data.get("zones", []))
                on_lost(BridgeError("connection closed"))
            except asyncio.CancelledError:
                raise
            except (aiohttp.ClientError, TimeoutError, ValueError) as err:
                LOGGER.debug("AirPlay Bridge WebSocket: %s", err)
                on_lost(BridgeError(str(err)))
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)
