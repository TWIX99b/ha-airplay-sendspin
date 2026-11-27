"""Base entity: one AirPlay speaker, attached to the Echo's own device."""

from __future__ import annotations

from collections.abc import Callable

from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import AirPlayBridgeConfigEntry, AirPlayBridgeCoordinator


class AirPlayEntity(CoordinatorEntity[AirPlayBridgeCoordinator]):
    _attr_has_entity_name = True

    def __init__(
        self, coordinator: AirPlayBridgeCoordinator, key: str, what: str
    ) -> None:
        super().__init__(coordinator)
        self.key = key
        self._attr_translation_key = what
        self._attr_unique_id = f"{key}_{what}"
        zone = coordinator.data[key]
        if (device := self._echo_device(zone)) is not None:
            # Same device as the Echo's voice assistant and media player
            self.device_entry = device
        else:
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, key)},
                name=f"{zone['name']} AirPlay",
                manufacturer=zone.get("manufacturer") or None,
                model=zone.get("model") or "AirPlay 2 receiver",
            )

    def _echo_device(self, zone: dict) -> dr.DeviceEntry | None:
        """Device of the (left) Echo, found via its pairing-token entity."""
        hass = self.coordinator.hass
        for speaker in zone.get("speakers", []):
            entity_id = speaker.get("token_entity")
            if not entity_id:
                continue
            entry = er.async_get(hass).async_get(entity_id)
            if entry and entry.device_id:
                return dr.async_get(hass).async_get(entry.device_id)
        return None

    @property
    def zone(self) -> dict:
        return self.coordinator.data.get(self.key, {})

    @property
    def index(self) -> int:
        return int(self.zone["index"])

    @property
    def available(self) -> bool:
        return super().available and self.key in self.coordinator.data


def setup_zone_entities(
    entry: AirPlayBridgeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
    factory: Callable[[AirPlayBridgeCoordinator, str], list[Entity]],
) -> None:
    """Add entities for every speaker, also for speakers added later."""
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def add_new() -> None:
        new = [key for key in coordinator.data if key not in known]
        if not new:
            return
        known.update(new)
        async_add_entities(
            [entity for key in new for entity in factory(coordinator, key)]
        )

    add_new()
    entry.async_on_unload(coordinator.async_add_listener(add_new))
