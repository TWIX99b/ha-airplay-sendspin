"""State of all AirPlay speakers, pushed by the add-on."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import BridgeAuthError, BridgeClient, BridgeError
from .const import DOMAIN, LOGGER

type AirPlayBridgeConfigEntry = ConfigEntry[AirPlayBridgeCoordinator]


class AirPlayBridgeCoordinator(DataUpdateCoordinator[dict[str, dict]]):
    """Data = {speaker key: state}; updated live over the WebSocket."""

    config_entry: AirPlayBridgeConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: AirPlayBridgeConfigEntry, client: BridgeClient
    ) -> None:
        super().__init__(
            hass,
            LOGGER,
            config_entry=entry,
            name=DOMAIN,
            # Fallback only: normally the add-on pushes every change at once.
            update_interval=timedelta(minutes=5),
        )
        self.client = client

    @staticmethod
    def _by_key(zones: list[dict]) -> dict[str, dict]:
        return {zone["key"]: zone for zone in zones}

    async def _async_update_data(self) -> dict[str, dict]:
        try:
            state = await self.client.state()
        except BridgeAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except BridgeError as err:
            raise UpdateFailed(f"AirPlay Bridge not reachable: {err}") from err
        return self._by_key(state.get("zones", []))

    async def async_listen(self) -> None:
        await self.client.listen(self._on_state, self._on_lost)

    @callback
    def _on_state(self, zones: list[dict]) -> None:
        self.async_set_updated_data(self._by_key(zones))

    @callback
    def _on_lost(self, err: Exception) -> None:
        self.async_set_update_error(err)
