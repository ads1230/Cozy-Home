"""The heating controller: makes room thermostats follow the master.

One controller per config entry (one master thermostat). It

* watches the master climate entity and every room climate entity,
* recomputes each room's desired state whenever the master changes, a room
  setting changes, a schedule block boundary passes, or a room thermostat
  comes back online,
* pushes ``climate.set_hvac_mode`` / ``climate.set_temperature`` to the room
  thermostat only when the desired state actually changed, and
* persists per-room settings and schedules in ``.storage``.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from homeassistant.components.climate import (
    ATTR_HVAC_MODE,
    ATTR_HVAC_MODES,
    ATTR_MAX_TEMP,
    ATTR_MIN_TEMP,
    ATTR_TARGET_TEMP_STEP,
    SERVICE_SET_HVAC_MODE,
    SERVICE_SET_TEMPERATURE,
    HVACMode,
)
from homeassistant.components.climate import (
    DOMAIN as CLIMATE_DOMAIN,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_TEMPERATURE,
    EVENT_HOMEASSISTANT_STARTED,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import (
    CALLBACK_TYPE,
    CoreState,
    Event,
    EventStateChangedData,
    HomeAssistant,
    State,
    callback,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_change,
)
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    CONF_CLIMATE_ENTITY,
    CONF_FROST_TEMPERATURE,
    CONF_MASTER_ENTITY,
    CONF_MASTER_OFF,
    CONF_OFF_BELOW,
    CONF_OFF_MODE,
    CONF_OFFSET,
    DEFAULT_FROST_TEMPERATURE,
    DEFAULT_MASTER_OFF,
    DEFAULT_OFF_BELOW,
    DEFAULT_OFF_MODE,
    DEFAULT_OFFSET,
    DOMAIN,
    MASTER_OFF_HOLD,
    MASTER_OFF_OPTIONS,
    MODE_FIXED,
    MODE_FOLLOW,
    MODE_OFF,
    OFF_MODE_FROST,
    OFF_MODE_OPTIONS,
    REASON_BELOW_THRESHOLD,
    REASON_GLOBAL_DISABLED,
    REASON_MASTER_OFF,
    REASON_MASTER_UNAVAILABLE,
    REASON_ROOM_DISABLED,
    REASON_ROOM_UNAVAILABLE,
    REASON_SCHEDULE,
    STATUS_DISABLED,
    STATUS_FIXED,
    STATUS_FOLLOW,
    STATUS_FROST,
    STATUS_HOLD,
    STATUS_OFF,
    STATUS_UNAVAILABLE,
    STORAGE_VERSION,
    SUBENTRY_TYPE_ROOM,
)
from .schedule import Block, Schedule, active_block, normalize

_LOGGER = logging.getLogger(__name__)

BAD_STATES = (STATE_UNAVAILABLE, STATE_UNKNOWN)
HEATING_MODE_PREFERENCE = (HVACMode.HEAT, HVACMode.HEAT_COOL, HVACMode.AUTO)


@dataclass(frozen=True)
class Command:
    """What a thermostat should be doing."""

    hvac_off: bool
    temperature: float | None = None


@dataclass
class RoomState:
    """Settings and computed state for one room."""

    subentry_id: str
    name: str
    climate_entity: str
    offset: float
    off_below: float
    master_off: str = DEFAULT_MASTER_OFF
    off_mode: str = DEFAULT_OFF_MODE
    frost_temperature: float = DEFAULT_FROST_TEMPERATURE
    enabled: bool = True
    schedule: Schedule = field(default_factory=dict)

    # Computed on every sync
    status: str = STATUS_UNAVAILABLE
    reason: str | None = None
    target: float | None = None
    master_target: float | None = None
    active_day: str | None = None
    active_block: Block | None = None
    last_command: Command | None = None
    last_synced: datetime | None = None


def _is_bad(state: State | None) -> bool:
    return state is None or state.state in BAD_STATES


def _option(value: Any, options: tuple[str, ...], default: str) -> str:
    return value if value in options else default


class HeatingController:
    """Keep the room thermostats in step with the master thermostat."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.master_entity: str = entry.data[CONF_MASTER_ENTITY]
        self.rooms: dict[str, RoomState] = {}
        self.master_schedule: Schedule = {}
        self.enabled = True
        self.master_active_block: tuple[str, Block] | None = None

        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}"
        )
        self._dirty = False
        self._listeners: set[CALLBACK_TYPE] = set()
        self._unsubs: list[CALLBACK_TYPE] = []
        self._lock = asyncio.Lock()
        self._last_master_block_key: tuple[str, str] | None = None

    # ------------------------------------------------------------------ setup

    async def async_setup(self) -> None:
        """Load stored settings and start watching entities and the clock."""
        stored = await self._store.async_load() or {}
        self.enabled = bool(stored.get("enabled", True))
        self.master_schedule = normalize(stored.get("master_schedule"))
        stored_rooms: dict[str, dict[str, Any]] = stored.get("rooms", {})

        for subentry_id, subentry in self.entry.subentries.items():
            if subentry.subentry_type != SUBENTRY_TYPE_ROOM:
                continue
            saved = stored_rooms.get(subentry_id, {})
            self.rooms[subentry_id] = RoomState(
                subentry_id=subentry_id,
                name=subentry.title,
                climate_entity=subentry.data[CONF_CLIMATE_ENTITY],
                offset=float(saved.get("offset", subentry.data.get(CONF_OFFSET, DEFAULT_OFFSET))),
                off_below=float(
                    saved.get("off_below", subentry.data.get(CONF_OFF_BELOW, DEFAULT_OFF_BELOW))
                ),
                master_off=_option(
                    saved.get("master_off", subentry.data.get(CONF_MASTER_OFF)),
                    MASTER_OFF_OPTIONS,
                    DEFAULT_MASTER_OFF,
                ),
                off_mode=_option(
                    saved.get("off_mode", subentry.data.get(CONF_OFF_MODE)),
                    OFF_MODE_OPTIONS,
                    DEFAULT_OFF_MODE,
                ),
                frost_temperature=float(
                    saved.get(
                        "frost_temperature",
                        subentry.data.get(CONF_FROST_TEMPERATURE, DEFAULT_FROST_TEMPERATURE),
                    )
                ),
                enabled=bool(saved.get("enabled", True)),
                schedule=normalize(saved.get("schedule")),
            )

        if set(stored_rooms) - set(self.rooms):
            # Rooms that were removed: drop their stored settings.
            self._mark_dirty()

        tracked = [self.master_entity, *(r.climate_entity for r in self.rooms.values())]
        self._unsubs.append(
            async_track_state_change_event(self.hass, tracked, self._async_state_changed)
        )
        self._unsubs.append(async_track_time_change(self.hass, self._async_minute_tick, second=0))

    @callback
    def async_start(self) -> None:
        """Run the first sync once Home Assistant is fully started."""
        if self.hass.state is CoreState.running:
            self.entry.async_create_task(self.hass, self.async_sync_all())
        else:
            self._unsubs.append(
                self.hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, self._async_on_started)
            )

    async def _async_on_started(self, _event: Event) -> None:
        await self.async_sync_all()

    async def async_unload(self) -> None:
        """Stop listening and flush pending settings."""
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        self._listeners.clear()
        if self._dirty:
            await self._store.async_save(self._data_to_save())
            self._dirty = False

    async def async_remove_storage(self) -> None:
        """Delete persisted data (config entry removed)."""
        await self._store.async_remove()

    # --------------------------------------------------------------- listeners

    @callback
    def async_add_listener(self, update_callback: CALLBACK_TYPE) -> CALLBACK_TYPE:
        """Register an entity callback; returns a function to unregister."""
        self._listeners.add(update_callback)

        @callback
        def _remove() -> None:
            self._listeners.discard(update_callback)

        return _remove

    @callback
    def _notify(self) -> None:
        for update_callback in list(self._listeners):
            update_callback()

    # ------------------------------------------------------------- persistence

    def _data_to_save(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "master_schedule": self.master_schedule,
            "rooms": {
                room.subentry_id: {
                    "offset": room.offset,
                    "off_below": room.off_below,
                    "master_off": room.master_off,
                    "off_mode": room.off_mode,
                    "frost_temperature": room.frost_temperature,
                    "enabled": room.enabled,
                    "schedule": room.schedule,
                }
                for room in self.rooms.values()
            },
        }

    @callback
    def _mark_dirty(self) -> None:
        self._dirty = True
        self._store.async_delay_save(self._data_to_save, 1)

    # ---------------------------------------------------------------- settings

    def room(self, subentry_id: str) -> RoomState:
        """Return a room (KeyError if unknown)."""
        return self.rooms[subentry_id]

    async def async_set_room_offset(self, subentry_id: str, value: float) -> None:
        room = self.rooms[subentry_id]
        room.offset = float(value)
        self._mark_dirty()
        await self.async_sync_room(subentry_id)

    async def async_set_room_off_below(self, subentry_id: str, value: float) -> None:
        room = self.rooms[subentry_id]
        room.off_below = float(value)
        self._mark_dirty()
        await self.async_sync_room(subentry_id)

    async def async_set_room_enabled(self, subentry_id: str, enabled: bool) -> None:
        room = self.rooms[subentry_id]
        room.enabled = enabled
        self._mark_dirty()
        await self.async_sync_room(subentry_id)

    async def async_set_room_master_off(self, subentry_id: str, option: str) -> None:
        """Choose what the room does while the master is off ("off" or "hold")."""
        if option not in MASTER_OFF_OPTIONS:
            raise ValueError(f"Unknown master-off option {option!r}")
        room = self.rooms[subentry_id]
        room.master_off = option
        self._mark_dirty()
        await self.async_sync_room(subentry_id)

    async def async_set_room_off_mode(self, subentry_id: str, option: str) -> None:
        """Choose how "off" is realised: hvac off, or heat at the frost temperature."""
        if option not in OFF_MODE_OPTIONS:
            raise ValueError(f"Unknown off-mode option {option!r}")
        room = self.rooms[subentry_id]
        room.off_mode = option
        self._mark_dirty()
        await self.async_sync_room(subentry_id)

    async def async_set_room_frost_temperature(self, subentry_id: str, value: float) -> None:
        room = self.rooms[subentry_id]
        room.frost_temperature = float(value)
        self._mark_dirty()
        await self.async_sync_room(subentry_id)

    async def async_set_room_schedule(self, subentry_id: str, schedule: Schedule | None) -> None:
        room = self.rooms[subentry_id]
        room.schedule = normalize(schedule)
        self._mark_dirty()
        await self.async_sync_room(subentry_id)

    async def async_set_master_schedule(self, schedule: Schedule | None) -> None:
        self.master_schedule = normalize(schedule)
        self._last_master_block_key = None
        self._mark_dirty()
        await self.async_sync_all()

    async def async_set_enabled(self, enabled: bool) -> None:
        self.enabled = enabled
        self._last_master_block_key = None
        self._mark_dirty()
        await self.async_sync_all()

    # ------------------------------------------------------------------ events

    async def _async_state_changed(self, event: Event[EventStateChangedData]) -> None:
        entity_id = event.data["entity_id"]
        if entity_id == self.master_entity:
            await self.async_sync_all()
            return
        # A room thermostat going away or coming back: re-sync it, forcing a
        # push when it returns so it picks up the current target again.
        old_bad = _is_bad(event.data["old_state"])
        new_bad = _is_bad(event.data["new_state"])
        if old_bad == new_bad:
            return
        for room in self.rooms.values():
            if room.climate_entity == entity_id:
                await self.async_sync_room(room.subentry_id, force=not new_bad)

    async def _async_minute_tick(self, _now: datetime) -> None:
        await self.async_sync_all()

    # -------------------------------------------------------------------- sync

    async def async_sync_all(self, *, force: bool = False) -> None:
        """Re-evaluate the master schedule and every room."""
        await self._async_apply_master_schedule(force=force)
        for subentry_id in list(self.rooms):
            await self.async_sync_room(subentry_id, force=force)

    async def async_sync_room(self, subentry_id: str, *, force: bool = False) -> None:
        """Recompute one room and push to its thermostat if anything changed."""
        room = self.rooms.get(subentry_id)
        if room is None:
            return
        async with self._lock:
            command = self._compute(room)
            changed = command != room.last_command
            room.last_command = command
            if command is not None and (changed or force):
                await self._async_apply(room, command)
                room.last_synced = dt_util.utcnow()
        self._notify()

    def _compute(self, room: RoomState) -> Command | None:
        """Work out what the room thermostat should do right now.

        Returns ``None`` when we should leave the thermostat alone (room or
        integration disabled, or something unavailable). Also updates the
        room's status fields for the sensors.
        """
        previous_target = room.target
        room.master_target = None
        room.active_day = None
        room.active_block = None
        room.target = None

        if not self.enabled:
            room.status, room.reason = STATUS_DISABLED, REASON_GLOBAL_DISABLED
            return None
        if not room.enabled:
            room.status, room.reason = STATUS_DISABLED, REASON_ROOM_DISABLED
            return None

        room_state = self.hass.states.get(room.climate_entity)
        if _is_bad(room_state):
            room.status, room.reason = STATUS_UNAVAILABLE, REASON_ROOM_UNAVAILABLE
            return None
        assert room_state is not None

        found = active_block(room.schedule, dt_util.now())
        if found is not None:
            room.active_day, room.active_block = found
        mode = room.active_block["mode"] if room.active_block else MODE_FOLLOW

        if mode == MODE_OFF:
            return self._off(room, room_state, REASON_SCHEDULE)

        if mode == MODE_FIXED:
            target = float(room.active_block["temperature"])  # type: ignore[index]
            status = STATUS_FIXED
        else:
            master = self.hass.states.get(self.master_entity)
            if _is_bad(master):
                room.status, room.reason = STATUS_UNAVAILABLE, REASON_MASTER_UNAVAILABLE
                return None
            assert master is not None
            if master.state == HVACMode.OFF:
                if room.master_off == MASTER_OFF_HOLD:
                    # Leave the thermostat exactly as it is until the master
                    # comes back; keep showing the target it was last given.
                    room.status, room.reason = STATUS_HOLD, REASON_MASTER_OFF
                    room.target = previous_target
                    return None
                return self._off(room, room_state, REASON_MASTER_OFF)
            master_target = master.attributes.get(ATTR_TEMPERATURE)
            if master_target is None:
                room.status, room.reason = STATUS_UNAVAILABLE, REASON_MASTER_UNAVAILABLE
                return None
            room.master_target = float(master_target)
            offset = room.offset
            if room.active_block and room.active_block.get("offset") is not None:
                offset = float(room.active_block["offset"])
            target = room.master_target + offset
            status = STATUS_FOLLOW

        if target < room.off_below:
            return self._off(room, room_state, REASON_BELOW_THRESHOLD)

        target = self._clamp(room_state, target)
        room.status, room.reason, room.target = status, None, target
        return Command(hvac_off=False, temperature=target)

    def _off(self, room: RoomState, room_state: State, reason: str) -> Command:
        """The command for "this room should be off", honouring the room's off mode.

        With frost protection the thermostat keeps heating at the frost
        temperature instead of being switched off, so the valve still opens if
        the room gets properly cold. Either way, when the room's target comes
        back above the cut-off the thermostat is switched back to heat (if it
        was off) and given the new target.
        """
        room.reason = reason
        if room.off_mode == OFF_MODE_FROST:
            room.status = STATUS_FROST
            room.target = self._clamp(room_state, room.frost_temperature)
            return Command(hvac_off=False, temperature=room.target)
        room.status = STATUS_OFF
        return Command(hvac_off=True)

    @staticmethod
    def _clamp(state: State, target: float) -> float:
        """Round to the thermostat's step and keep within its min/max."""
        step = state.attributes.get(ATTR_TARGET_TEMP_STEP) or 0.5
        target = round(target / step) * step
        min_temp = state.attributes.get(ATTR_MIN_TEMP)
        max_temp = state.attributes.get(ATTR_MAX_TEMP)
        if min_temp is not None:
            target = max(target, float(min_temp))
        if max_temp is not None:
            target = min(target, float(max_temp))
        return round(target, 2)

    @staticmethod
    def _heating_mode(state: State) -> HVACMode | None:
        """The mode to use to switch a thermostat on (manual heat preferred)."""
        modes = state.attributes.get(ATTR_HVAC_MODES) or []
        for mode in HEATING_MODE_PREFERENCE:
            if mode in modes:
                return mode
        return None

    async def _async_apply(self, room: RoomState, command: Command) -> None:
        """Push a command to the room thermostat (skips calls already satisfied)."""
        await self._async_apply_to(room.climate_entity, command, label=room.name)

    async def _async_apply_to(
        self, entity_id: str, command: Command, *, label: str, force_manual: bool = False
    ) -> None:
        state = self.hass.states.get(entity_id)
        if _is_bad(state):
            return
        assert state is not None
        try:
            if command.hvac_off:
                if state.state != HVACMode.OFF:
                    await self._call(
                        SERVICE_SET_HVAC_MODE,
                        {ATTR_ENTITY_ID: entity_id, ATTR_HVAC_MODE: HVACMode.OFF},
                    )
                return

            heating_mode = self._heating_mode(state)
            if heating_mode is not None and (
                state.state == HVACMode.OFF or (force_manual and state.state != heating_mode)
            ):
                await self._call(
                    SERVICE_SET_HVAC_MODE,
                    {ATTR_ENTITY_ID: entity_id, ATTR_HVAC_MODE: heating_mode},
                )

            current = state.attributes.get(ATTR_TEMPERATURE)
            if command.temperature is not None and (
                current is None or abs(float(current) - command.temperature) > 0.01
            ):
                await self._call(
                    SERVICE_SET_TEMPERATURE,
                    {ATTR_ENTITY_ID: entity_id, ATTR_TEMPERATURE: command.temperature},
                )
        except HomeAssistantError as err:
            _LOGGER.warning("Could not update %s (%s): %s", label, entity_id, err)

    async def _call(self, service: str, data: dict[str, Any]) -> None:
        _LOGGER.debug("climate.%s %s", service, data)
        await self.hass.services.async_call(CLIMATE_DOMAIN, service, data, blocking=True)

    # --------------------------------------------------------- master schedule

    async def _async_apply_master_schedule(self, *, force: bool = False) -> None:
        """Drive the master thermostat from its own schedule, if one is set."""
        self.master_active_block = None
        if not self.master_schedule or not self.enabled:
            self._last_master_block_key = None
            return
        found = active_block(self.master_schedule, dt_util.now())
        if found is None:
            return
        self.master_active_block = found
        day, block = found
        key = (day, block["time"])
        if key == self._last_master_block_key and not force:
            return
        if _is_bad(self.hass.states.get(self.master_entity)):
            # Try again on the next tick.
            self._last_master_block_key = None
            return
        self._last_master_block_key = key

        if block["mode"] == MODE_OFF:
            command = Command(hvac_off=True)
        else:
            state = self.hass.states.get(self.master_entity)
            assert state is not None
            command = Command(
                hvac_off=False,
                temperature=self._clamp(state, float(block["temperature"])),
            )
        # Put the master into manual heat so its own schedule doesn't override.
        await self._async_apply_to(self.master_entity, command, label="master", force_manual=True)
