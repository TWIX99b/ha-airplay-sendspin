"""AirPlay Bridge: Home Assistant entities for the AirPlay 2 → Sendspin Bridge add-on.

The add-on announces itself to Home Assistant (Supervisor discovery); the
entities of each AirPlay speaker are attached to the Echo's own device.
"""

from __future__ import annotations

from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import BridgeClient
from .const import CONF_TOKEN
from .coordinator import AirPlayBridgeConfigEntry, AirPlayBridgeCoordinator

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.IMAGE,
    Platform.NUMBER,
    Platform.SENSOR,
    Platform.SWITCH,
]


async def async_setup_entry(
    hass: HomeAssistant, entry: AirPlayBridgeConfigEntry
) -> bool:
    client = BridgeClient(
        async_get_clientsession(hass),
        entry.data[CONF_HOST],
        entry.data[CONF_PORT],
        entry.data[CONF_TOKEN],
    )
    coordinator = AirPlayBridgeCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    entry.async_create_background_task(
        hass, coordinator.async_listen(), "airplay_bridge_websocket"
    )
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: AirPlayBridgeConfigEntry
) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
