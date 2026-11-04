"""Sensors: the computed target and the current mode for every room."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import CozyHomeConfigEntry
from .const import STATUSES, SUBENTRY_TYPE_ROOM
from .entity import CozyHomeRoomEntity
from .schedule import describe_block


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CozyHomeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the sensors for every room."""
    controller = entry.runtime_data
    for subentry in entry.subentries.values():
        if subentry.subentry_type != SUBENTRY_TYPE_ROOM:
            continue
        async_add_entities(
            [
                RoomTargetSensor(controller, entry, subentry),
                RoomModeSensor(controller, entry, subentry),
            ],
            config_subentry_id=subentry.subentry_id,
        )


class RoomTargetSensor(CozyHomeRoomEntity, SensorEntity):
    """The target temperature this integration wants the room thermostat at."""

    _attr_translation_key = "target"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_suggested_display_precision = 1

    @property
    def native_unit_of_measurement(self) -> str:
        return self.hass.config.units.temperature_unit

    @property
    def native_value(self) -> float | None:
        return self.room.target

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        room = self.room
        return {
            "mode": room.status,
            "reason": room.reason,
            "master_target": room.master_target,
            "offset": room.offset,
            "off_below": room.off_below,
            "active_block": describe_block(room.active_block),
            "active_block_day": room.active_day,
            "active_block_time": room.active_block["time"] if room.active_block else None,
            "last_synced": room.last_synced,
        }


class RoomModeSensor(CozyHomeRoomEntity, SensorEntity):
    """What the room is doing: following, fixed, off, disabled or unavailable."""

    _attr_translation_key = "mode"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = list(STATUSES)
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:thermostat"

    @property
    def native_value(self) -> str:
        return self.room.status

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"reason": self.room.reason}
