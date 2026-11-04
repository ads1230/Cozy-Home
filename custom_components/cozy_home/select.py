"""Per-room select: what to do while the master thermostat is off."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import CozyHomeConfigEntry
from .const import MASTER_OFF_OPTIONS, OFF_MODE_OPTIONS, SUBENTRY_TYPE_ROOM
from .entity import CozyHomeRoomEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CozyHomeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the select entities for every room."""
    controller = entry.runtime_data
    for subentry in entry.subentries.values():
        if subentry.subentry_type != SUBENTRY_TYPE_ROOM:
            continue
        async_add_entities(
            [
                RoomMasterOffSelect(controller, entry, subentry),
                RoomOffModeSelect(controller, entry, subentry),
            ],
            config_subentry_id=subentry.subentry_id,
        )


class RoomMasterOffSelect(CozyHomeRoomEntity, SelectEntity):
    """Turn the room off with the master, or hold its last target."""

    _attr_translation_key = "master_off"
    _attr_options = list(MASTER_OFF_OPTIONS)
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:power-settings"

    @property
    def current_option(self) -> str:
        return self.room.master_off

    async def async_select_option(self, option: str) -> None:
        await self._controller.async_set_room_master_off(self._subentry_id, option)


class RoomOffModeSelect(CozyHomeRoomEntity, SelectEntity):
    """How "off" is realised: thermostat off, or heat at the frost temperature."""

    _attr_translation_key = "off_mode"
    _attr_options = list(OFF_MODE_OPTIONS)
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:snowflake-thermometer"

    @property
    def current_option(self) -> str:
        return self.room.off_mode

    async def async_select_option(self, option: str) -> None:
        await self._controller.async_set_room_off_mode(self._subentry_id, option)
