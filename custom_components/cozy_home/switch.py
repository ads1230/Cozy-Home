"""Per-room "follow master" switches."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import CozyHomeConfigEntry
from .const import SUBENTRY_TYPE_ROOM
from .entity import CozyHomeRoomEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CozyHomeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add one follow switch per room."""
    controller = entry.runtime_data
    for subentry in entry.subentries.values():
        if subentry.subentry_type != SUBENTRY_TYPE_ROOM:
            continue
        async_add_entities(
            [RoomFollowSwitch(controller, entry, subentry)],
            config_subentry_id=subentry.subentry_id,
        )


class RoomFollowSwitch(CozyHomeRoomEntity, SwitchEntity):
    """Whether this room is controlled at all (off = leave its thermostat alone)."""

    _attr_translation_key = "follow_master"
    _attr_device_class = SwitchDeviceClass.SWITCH
    _attr_icon = "mdi:link-variant"

    @property
    def is_on(self) -> bool:
        return self.room.enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._controller.async_set_room_enabled(self._subentry_id, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._controller.async_set_room_enabled(self._subentry_id, False)
