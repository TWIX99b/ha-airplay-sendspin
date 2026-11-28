"""Sync offset, volume trim and AirPlay volume per speaker."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import BridgeError
from .coordinator import AirPlayBridgeConfigEntry, AirPlayBridgeCoordinator
from .entity import AirPlayEntity, setup_zone_entities


@dataclass(frozen=True, kw_only=True)
class Spec:
    what: str
    setting: str  # option name in the add-on
    state: str  # field in the add-on's state
    min: float
    max: float
    step: float
    unit: str | None
    mode: NumberMode
    category: EntityCategory | None


SPECS = (
    Spec(
        what="offset",
        setting="latency_offset_ms",
        state="offset_ms",
        min=-1000,
        max=1000,
        step=5,
        unit=UnitOfTime.MILLISECONDS,
        mode=NumberMode.BOX,
        category=EntityCategory.CONFIG,
    ),
    Spec(
        what="trim",
        setting="volume_trim_db",
        state="trim_db",
        min=-30,
        max=0,
        step=0.1,
        unit="dB",
        mode=NumberMode.SLIDER,
        category=EntityCategory.CONFIG,
    ),
    Spec(
        what="airplay_volume",
        setting="speaker_volume",
        state="airplay_volume",
        min=1,
        max=100,
        step=1,
        unit=PERCENTAGE,
        mode=NumberMode.SLIDER,
        category=None,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AirPlayBridgeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    setup_zone_entities(
        entry,
        async_add_entities,
        lambda c, key: [AirPlayNumber(c, key, s) for s in SPECS],
    )


class AirPlayNumber(AirPlayEntity, NumberEntity):
    def __init__(
        self, coordinator: AirPlayBridgeCoordinator, key: str, spec: Spec
    ) -> None:
        super().__init__(coordinator, key, spec.what)
        self.spec = spec
        self._attr_native_min_value = spec.min
        self._attr_native_max_value = spec.max
        self._attr_native_step = spec.step
        self._attr_native_unit_of_measurement = spec.unit
        self._attr_mode = spec.mode
        self._attr_entity_category = spec.category

    @property
    def native_value(self) -> float | None:
        value: Any = self.zone.get(self.spec.state)
        if value is None and self.spec.what == "airplay_volume":
            value = self.zone.get("default_volume")
        return None if value is None else float(value)

    async def async_set_native_value(self, value: float) -> None:
        if self.spec.what == "trim":
            send: float | int = round(value, 1)
        else:
            send = round(value)
        try:
            await self.coordinator.client.set(self.index, **{self.spec.setting: send})
        except BridgeError as err:
            raise HomeAssistantError(f"AirPlay Bridge: {err}") from err
