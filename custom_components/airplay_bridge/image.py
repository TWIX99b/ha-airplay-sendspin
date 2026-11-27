"""Cover of the current AirPlay track."""

from __future__ import annotations

from homeassistant.components.image import ImageEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .api import BridgeError
from .coordinator import AirPlayBridgeConfigEntry, AirPlayBridgeCoordinator
from .entity import AirPlayEntity, setup_zone_entities


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AirPlayBridgeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    setup_zone_entities(
        entry, async_add_entities, lambda c, key: [AirPlayCover(c, key)]
    )


class AirPlayCover(AirPlayEntity, ImageEntity):
    def __init__(self, coordinator: AirPlayBridgeCoordinator, key: str) -> None:
        AirPlayEntity.__init__(self, coordinator, key, "cover")
        ImageEntity.__init__(self, coordinator.hass)
        self._cover_key: str | None = None
        self._update_cover_key()

    def _current_key(self) -> str | None:
        return self.zone.get("cover") if self.zone.get("playing") else None

    def _update_cover_key(self) -> None:
        key = self._current_key()
        if key != self._cover_key:
            self._cover_key = key
            self._attr_image_last_updated = dt_util.utcnow()

    @callback
    def _handle_coordinator_update(self) -> None:
        self._update_cover_key()
        super()._handle_coordinator_update()

    async def async_image(self) -> bytes | None:
        if self._cover_key is None:
            return None
        try:
            data = await self.coordinator.client.cover(self.index)
        except BridgeError:
            return None
        if data and data[:4] == b"\x89PNG":
            self._attr_content_type = "image/png"
        else:
            self._attr_content_type = "image/jpeg"
        return data
