"""Test tone: a short chime on the speaker, without AirPlay."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import BridgeError
from .coordinator import AirPlayBridgeConfigEntry
from .entity import AirPlayEntity, setup_zone_entities


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AirPlayBridgeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    setup_zone_entities(
        entry, async_add_entities, lambda c, key: [AirPlayTestTone(c, key, "test_tone")]
    )


class AirPlayTestTone(AirPlayEntity, ButtonEntity):
    async def async_press(self) -> None:
        try:
            await self.coordinator.client.test_tone(self.index)
        except BridgeError as err:
            raise HomeAssistantError(
                "Test tone not possible – is AirPlay playing or the Echo not paired?"
            ) from err
