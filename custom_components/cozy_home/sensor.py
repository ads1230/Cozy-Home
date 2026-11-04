"""Sensors: the computed target and the current mode for every room."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import CozyHomeConfigEntry
from .const import (
    DOMAIN,
    MASTER_SCHEDULE_MODES,
    SCHEDULE_MODES,
    STATUSES,
    SUBENTRY_TYPE_ROOM,
)
from .controller import Boost
from .entity import CozyHomeEntity, CozyHomeRoomEntity
from .schedule import Block, describe_block, summarize


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CozyHomeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the hub schedule sensor and the sensors for every room."""
    controller = entry.runtime_data
    async_add_entities([MasterScheduleSensor(controller, entry)])
    for subentry in entry.subentries.values():
        if subentry.subentry_type != SUBENTRY_TYPE_ROOM:
            continue
        async_add_entities(
            [
                RoomTargetSensor(controller, entry, subentry),
                RoomModeSensor(controller, entry, subentry),
                RoomScheduleSensor(controller, entry, subentry),
            ],
            config_subentry_id=subentry.subentry_id,
        )


def _boost_attr(boost: Boost | None) -> dict[str, Any] | None:
    if boost is None or not boost.active():
        return None
    return {"temperature": boost.temperature, "until": boost.until.isoformat()}


def _schedule_attributes(
    *,
    kind: str,
    schedule: dict[str, list[Block]],
    active: tuple[str, Block] | None,
    modes: tuple[str, ...],
) -> dict[str, Any]:
    """Attributes the schedule card reads (it finds these sensors by ``cozy_home``)."""
    return {
        "integration": DOMAIN,
        DOMAIN: True,  # older copies of the cards look for this one
        "kind": kind,
        "modes": list(modes),
        "schedule": schedule,
        "active_day": active[0] if active else None,
        "active_time": active[1]["time"] if active else None,
        "active_block": describe_block(active[1]) if active else None,
    }


class RoomScheduleSensor(CozyHomeRoomEntity, SensorEntity):
    """A per-room schedule (advanced; disabled by default).

    Normally there is one schedule, the heating schedule on the master, and
    rooms follow it with their offsets. Enable this entity to give a room a
    timeline of its own, editable with the schedule card's ``entity`` option.
    """

    _attr_translation_key = "schedule"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_icon = "mdi:calendar-clock"

    @property
    def native_value(self) -> str:
        return summarize(self.room.schedule, dt_util.now(), none_text="No schedule")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        room = self.room
        active = (room.active_day, room.active_block) if room.active_block else None
        return _schedule_attributes(
            kind="room", schedule=room.schedule, active=active, modes=SCHEDULE_MODES
        )


class MasterScheduleSensor(CozyHomeEntity, SensorEntity):
    """The schedule Home Assistant applies to the master thermostat, if any."""

    _attr_translation_key = "schedule"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:calendar-clock"

    @property
    def native_value(self) -> str:
        return summarize(
            self._controller.master_schedule, dt_util.now(), none_text="Not scheduled here"
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            **_schedule_attributes(
                kind="master",
                schedule=self._controller.master_schedule,
                active=self._controller.master_active_block,
                modes=MASTER_SCHEDULE_MODES,
            ),
            # For the dashboard cards
            "master_entity": self._controller.master_entity,
            "enabled": self._controller.enabled,
            "resync_minutes": self._controller.resync_minutes,
            "block_pending": self._controller.master_block_pending,
            "boost": _boost_attr(self._controller.master_boost),
        }


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
            "integration": DOMAIN,  # the dashboard cards find rooms by these two
            "kind": "room",
            "mode": room.status,
            "reason": room.reason,
            "thermostats": list(room.climate_entities),
            "applied": dict(room.applied),
            "boost": _boost_attr(room.boost),
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
