"""Current title and dropouts."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.const import EntityCategory
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
        entry,
        async_add_entities,
        lambda c, key: [
            AirPlayTrack(c, key, "track"),
            AirPlayDropouts(c, key, "dropouts"),
        ],
    )


class AirPlayTrack(AirPlayEntity, SensorEntity):
    @property
    def native_value(self) -> str | None:
        track = self.zone.get("track") if self.zone.get("playing") else None
        return (track or {}).get("title") or None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        track = (self.zone.get("track") if self.zone.get("playing") else None) or {}
        return {
            "artist": track.get("artist") or None,
            "album": track.get("album") or None,
        }


class AirPlayDropouts(AirPlayEntity, SensorEntity):
    """Audible hiccups since the add-on started (resync, gap, lost speaker)."""

    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self) -> int:
        return int(self.zone.get("dropouts", 0))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        stats = self.zone.get("stats") or {}
        return {
            "music_hours": round(float(stats.get("audio_s", 0)) / 3600, 2),
            "sessions": stats.get("sessions", 0),
        }
