"""Night mode on/off (times and volume are set on the add-on page)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
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
        entry,
        async_add_entities,
        lambda c, key: [AirPlayNightMode(c, key, "night_mode")],
    )


class AirPlayNightMode(AirPlayEntity, SwitchEntity):
    @property
    def available(self) -> bool:
        # Only usable once a night volume is set for this speaker
        return super().available and self.zone.get("night_volume") is not None

    @property
    def is_on(self) -> bool:
        return bool(self.zone.get("night_enabled", True))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "night_volume": self.zone.get("night_volume"),
            "start": self.zone.get("night_start"),
            "end": self.zone.get("night_end"),
            "active_now": bool(self.zone.get("night_active")),
        }

    async def _set(self, on: bool) -> None:  # noqa: FBT001
        try:
            await self.coordinator.client.set(self.index, night_enabled=on)
        except BridgeError as err:
            raise HomeAssistantError(f"AirPlay Bridge: {err}") from err

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set(False)
