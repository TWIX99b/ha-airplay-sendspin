"""AirPlay active."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import AirPlayBridgeConfigEntry
from .entity import AirPlayEntity, setup_zone_entities


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AirPlayBridgeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    setup_zone_entities(
        entry, async_add_entities, lambda c, key: [AirPlayActive(c, key, "airplay")]
    )


class AirPlayActive(AirPlayEntity, BinarySensorEntity):
    @property
    def is_on(self) -> bool:
        return bool(self.zone.get("playing"))
