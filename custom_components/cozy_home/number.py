"""Per-room number entities: temperature offset and the off-below cut-off."""

from __future__ import annotations

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import CozyHomeConfigEntry
from .const import (
    FROST_MAX,
    FROST_MIN,
    FROST_STEP,
    OFF_BELOW_MAX,
    OFF_BELOW_MIN,
    OFF_BELOW_STEP,
    OFFSET_MAX,
    OFFSET_MIN,
    OFFSET_STEP,
    SUBENTRY_TYPE_ROOM,
)
from .entity import CozyHomeRoomEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CozyHomeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the number entities for every room."""
    controller = entry.runtime_data
    for subentry in entry.subentries.values():
        if subentry.subentry_type != SUBENTRY_TYPE_ROOM:
            continue
        async_add_entities(
            [
                RoomOffsetNumber(controller, entry, subentry),
                RoomOffBelowNumber(controller, entry, subentry),
                RoomFrostTemperatureNumber(controller, entry, subentry),
            ],
            config_subentry_id=subentry.subentry_id,
        )


class RoomOffsetNumber(CozyHomeRoomEntity, NumberEntity):
    """Degrees added to the master's target for this room (can be negative)."""

    _attr_translation_key = "offset"
    _attr_icon = "mdi:plus-minus-variant"
    _attr_mode = NumberMode.BOX
    _attr_native_min_value = OFFSET_MIN
    _attr_native_max_value = OFFSET_MAX
    _attr_native_step = OFFSET_STEP

    @property
    def native_unit_of_measurement(self) -> str:
        return self.hass.config.units.temperature_unit

    @property
    def native_value(self) -> float:
        return self.room.offset

    async def async_set_native_value(self, value: float) -> None:
        await self._controller.async_set_room_offset(self._subentry_id, value)


class RoomOffBelowNumber(CozyHomeRoomEntity, NumberEntity):
    """The cut-off: targets below this aren't followed; the room's Off action applies."""

    _attr_translation_key = "off_below"
    _attr_icon = "mdi:thermometer-off"
    _attr_device_class = NumberDeviceClass.TEMPERATURE
    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX
    _attr_native_min_value = OFF_BELOW_MIN
    _attr_native_max_value = OFF_BELOW_MAX
    _attr_native_step = OFF_BELOW_STEP

    @property
    def native_unit_of_measurement(self) -> str:
        return self.hass.config.units.temperature_unit

    @property
    def native_value(self) -> float:
        return self.room.off_below

    async def async_set_native_value(self, value: float) -> None:
        await self._controller.async_set_room_off_below(self._subentry_id, value)


class RoomFrostTemperatureNumber(CozyHomeRoomEntity, NumberEntity):
    """Target used instead of "off" when the room's off mode is frost protection."""

    _attr_translation_key = "frost_temperature"
    _attr_icon = "mdi:snowflake"
    _attr_device_class = NumberDeviceClass.TEMPERATURE
    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX
    _attr_native_min_value = FROST_MIN
    _attr_native_max_value = FROST_MAX
    _attr_native_step = FROST_STEP

    @property
    def native_unit_of_measurement(self) -> str:
        return self.hass.config.units.temperature_unit

    @property
    def native_value(self) -> float:
        return self.room.frost_temperature

    async def async_set_native_value(self, value: float) -> None:
        await self._controller.async_set_room_frost_temperature(self._subentry_id, value)
