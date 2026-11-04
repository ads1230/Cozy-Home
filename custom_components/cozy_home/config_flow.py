"""Config flow for the Cozy Home integration.

The config entry holds the master thermostat. Each *room* is a config
subentry and owns one or more thermostats (several radiators in one room
share the room's offset, cut-off and schedule).

Adding rooms: tick thermostats in a multi-select, they are grouped by area,
and the flow then walks through each room — name, which thermostats, its
offset and cut-off — before anything is created. The same steps run during
initial setup and later from "Add rooms" on the integration page.
"""

from __future__ import annotations

import re
from types import MappingProxyType
from typing import Any

import voluptuous as vol
from homeassistant.components.climate import DOMAIN as CLIMATE_DOMAIN
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentry,
    ConfigSubentryData,
    ConfigSubentryFlow,
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import selector

from .const import (
    CONF_CLIMATE_ENTITIES,
    CONF_CLIMATE_ENTITY,
    CONF_ENABLED,
    CONF_MASTER_ENTITY,
    CONF_NAME,
    CONF_OFF_BELOW,
    CONF_OFFSET,
    CONF_RESYNC_MINUTES,
    CONF_ROOM_NAME,
    CONF_ROOMS,
    DEFAULT_NAME,
    DEFAULT_OFF_BELOW,
    DEFAULT_OFFSET,
    DEFAULT_RESYNC_MINUTES,
    DOMAIN,
    OFF_BELOW_MAX,
    OFF_BELOW_MIN,
    OFF_BELOW_STEP,
    OFFSET_MAX,
    OFFSET_MIN,
    OFFSET_STEP,
    SUBENTRY_TYPE_ROOM,
)
from .helpers import room_entities

CLIMATE_SELECTOR = selector.EntitySelector(selector.EntitySelectorConfig(domain=CLIMATE_DOMAIN))

# Words that are noise in a room name derived from a thermostat's name.
_NOISE = re.compile(
    r"\b(thermostat|trv|radiator|valve|climate|heating|heater|temperature|zone)\b",
    re.IGNORECASE,
)


def _number(min_value: float, max_value: float, step: float) -> selector.NumberSelector:
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=min_value,
            max=max_value,
            step=step,
            mode=selector.NumberSelectorMode.BOX,
            unit_of_measurement="°",
        )
    )


def _master_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    defaults = defaults or {}
    return vol.Schema(
        {
            vol.Required(
                CONF_MASTER_ENTITY, default=defaults.get(CONF_MASTER_ENTITY, vol.UNDEFINED)
            ): CLIMATE_SELECTOR,
            vol.Optional(
                CONF_NAME, default=defaults.get(CONF_NAME, DEFAULT_NAME)
            ): selector.TextSelector(),
        }
    )


def _pick_schema(candidates: list[str], exclude: list[str]) -> vol.Schema:
    """Step 1 of adding rooms: tick the thermostats."""
    return vol.Schema(
        {
            vol.Optional(CONF_ROOMS, default=candidates): selector.EntitySelector(
                selector.EntitySelectorConfig(
                    domain=CLIMATE_DOMAIN, multiple=True, exclude_entities=exclude
                )
            ),
        }
    )


def _room_schema(defaults: dict[str, Any], allowed: list[str]) -> vol.Schema:
    """One room: name, its thermostats, offset and cut-off."""
    return vol.Schema(
        {
            vol.Required(
                CONF_ROOM_NAME, default=defaults.get(CONF_ROOM_NAME, vol.UNDEFINED)
            ): selector.TextSelector(),
            vol.Required(
                CONF_CLIMATE_ENTITIES, default=defaults.get(CONF_CLIMATE_ENTITIES, [])
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(
                    domain=CLIMATE_DOMAIN, multiple=True, include_entities=allowed
                )
            ),
            vol.Optional(CONF_OFFSET, default=defaults.get(CONF_OFFSET, DEFAULT_OFFSET)): _number(
                OFFSET_MIN, OFFSET_MAX, OFFSET_STEP
            ),
            vol.Optional(
                CONF_OFF_BELOW, default=defaults.get(CONF_OFF_BELOW, DEFAULT_OFF_BELOW)
            ): _number(OFF_BELOW_MIN, OFF_BELOW_MAX, OFF_BELOW_STEP),
        }
    )


@callback
def _climate_entities(hass: HomeAssistant, exclude: set[str]) -> list[str]:
    """All climate entities except the excluded ones, sorted by name."""
    states = [s for s in hass.states.async_all(CLIMATE_DOMAIN) if s.entity_id not in exclude]
    states.sort(key=lambda s: s.name.lower())
    return [s.entity_id for s in states]


@callback
def _area_id(hass: HomeAssistant, entity_id: str) -> str | None:
    entry = er.async_get(hass).async_get(entity_id)
    if entry is None:
        return None
    if entry.area_id:
        return entry.area_id
    if entry.device_id and (device := dr.async_get(hass).async_get(entry.device_id)):
        return device.area_id
    return None


@callback
def _friendly(hass: HomeAssistant, entity_id: str) -> str:
    state = hass.states.get(entity_id)
    return (state and state.attributes.get("friendly_name")) or entity_id.split(".", 1)[1].replace(
        "_", " "
    ).title()


@callback
def room_name_for(hass: HomeAssistant, entity_id: str, taken: set[str]) -> str:
    """A sensible room name: the thermostat's area if it has one, else its name."""
    candidates: list[str] = []
    if (area_id := _area_id(hass, entity_id)) and (
        area := ar.async_get(hass).async_get_area(area_id)
    ):
        candidates.append(area.name)
    friendly = _friendly(hass, entity_id)
    cleaned = re.sub(r"\s{2,}", " ", _NOISE.sub("", friendly)).strip(" -–:")
    candidates.extend([cleaned or friendly, friendly])
    for name in candidates:
        if name and name not in taken:
            return name
    base = candidates[0] or friendly
    index = 2
    while f"{base} {index}" in taken:
        index += 1
    return f"{base} {index}"


@callback
def _next_group(hass: HomeAssistant, pending: list[str]) -> list[str]:
    """The next room to propose: all pending thermostats in the first one's area."""
    first = pending[0]
    area = _area_id(hass, first)
    if area is None:
        return [first]
    return [e for e in pending if _area_id(hass, e) == area]


class _RoomSteps:
    """The "tick thermostats, then one form per room" steps, shared by both flows."""

    hass: HomeAssistant
    _pending: list[str]
    _rooms: list[ConfigSubentryData]
    _taken: set[str]
    _excluded: set[str]

    def _start_rooms(self, *, excluded: set[str], taken: set[str]) -> None:
        self._pending = []
        self._rooms = []
        self._taken = set(taken)
        self._excluded = set(excluded)

    def _candidates(self) -> list[str]:
        return _climate_entities(self.hass, self._excluded)

    def _set_pending(self, selected: list[str]) -> None:
        """Queue the ticked thermostats, in name order so rooms come up predictably."""
        chosen = {e for e in selected if e not in self._excluded}
        self._pending = sorted(chosen, key=lambda e: _friendly(self.hass, e).lower())

    def _pick_form(self, step_id: str, errors: dict[str, str] | None = None) -> Any:
        candidates = self._candidates()
        return self.async_show_form(  # type: ignore[attr-defined]
            step_id=step_id,
            data_schema=_pick_schema(candidates, sorted(self._excluded)),
            errors=errors,
            description_placeholders={"count": str(len(candidates))},
        )

    def _room_form(self, errors: dict[str, str] | None = None) -> Any:
        group = _next_group(self.hass, self._pending)
        default_name = room_name_for(self.hass, group[0], self._taken)
        return self.async_show_form(  # type: ignore[attr-defined]
            step_id="room",
            data_schema=_room_schema(
                {CONF_ROOM_NAME: default_name, CONF_CLIMATE_ENTITIES: group}, self._pending
            ),
            errors=errors,
            description_placeholders={
                "room": default_name,
                "thermostats": ", ".join(_friendly(self.hass, e) for e in group),
                "remaining": str(len(self._pending)),
            },
        )

    def _take_room(self, user_input: dict[str, Any]) -> dict[str, str]:
        """Record one room from its form; returns form errors if any."""
        chosen = [e for e in user_input.get(CONF_CLIMATE_ENTITIES, []) if e in self._pending]
        if not chosen:
            return {CONF_CLIMATE_ENTITIES: "no_thermostats"}
        name = str(user_input.get(CONF_ROOM_NAME, "")).strip()
        if not name:
            return {CONF_ROOM_NAME: "name_required"}
        if name in self._taken:
            return {CONF_ROOM_NAME: "name_taken"}
        self._rooms.append(
            ConfigSubentryData(
                data={
                    CONF_ROOM_NAME: name,
                    CONF_CLIMATE_ENTITIES: chosen,
                    CONF_OFFSET: user_input.get(CONF_OFFSET, DEFAULT_OFFSET),
                    CONF_OFF_BELOW: user_input.get(CONF_OFF_BELOW, DEFAULT_OFF_BELOW),
                },
                subentry_type=SUBENTRY_TYPE_ROOM,
                title=name,
                unique_id=None,
            )
        )
        self._taken.add(name)
        self._pending = [e for e in self._pending if e not in chosen]
        return {}


class CozyHomeConfigFlow(_RoomSteps, ConfigFlow, domain=DOMAIN):
    """Handle the config flow for the master thermostat."""

    VERSION = 1
    MINOR_VERSION = 1

    def __init__(self) -> None:
        self._master: dict[str, Any] = {}

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Return the subentry flows this integration supports."""
        return {SUBENTRY_TYPE_ROOM: RoomSubentryFlowHandler}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """The "Configure" dialog: integration-level settings."""
        return CozyHomeOptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Pick the master (boiler) thermostat."""
        if user_input is not None:
            # One Cozy Home hub per master thermostat.
            await self.async_set_unique_id(user_input[CONF_MASTER_ENTITY])
            self._abort_if_unique_id_configured(reload_on_update=False)
            self._master = {
                CONF_MASTER_ENTITY: user_input[CONF_MASTER_ENTITY],
                CONF_NAME: user_input.get(CONF_NAME) or DEFAULT_NAME,
            }
            self._start_rooms(excluded={self._master[CONF_MASTER_ENTITY]}, taken=set())
            return await self.async_step_rooms()

        return self.async_show_form(step_id="user", data_schema=_master_schema())

    async def async_step_rooms(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Tick the thermostats that should follow the master."""
        if user_input is not None:
            self._set_pending(user_input.get(CONF_ROOMS, []))
            if not self._pending:
                return self._create()
            return await self.async_step_room()
        return self._pick_form("rooms")

    async def async_step_room(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """One form per room (grouped by area), until no thermostat is left."""
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = self._take_room(user_input)
            if not errors and not self._pending:
                return self._create()
        return self._room_form(errors)

    def _create(self) -> ConfigFlowResult:
        return self.async_create_entry(
            title=self._master[CONF_NAME], data=self._master, subentries=self._rooms
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the master thermostat or the hub name."""
        entry = self._get_reconfigure_entry()
        if user_input is not None:
            new_master = user_input[CONF_MASTER_ENTITY]
            existing = self.hass.config_entries.async_entry_for_domain_unique_id(DOMAIN, new_master)
            if existing is not None and existing.entry_id != entry.entry_id:
                return self.async_abort(reason="already_configured")
            # The update listener in __init__ reloads the entry, so use the
            # non-reloading variant here (reloading twice is deprecated).
            return self.async_update_and_abort(
                entry,
                unique_id=new_master,
                title=user_input.get(CONF_NAME) or entry.title,
                data_updates={
                    CONF_MASTER_ENTITY: new_master,
                    CONF_NAME: user_input.get(CONF_NAME) or DEFAULT_NAME,
                },
            )

        return self.async_show_form(
            step_id="reconfigure", data_schema=_master_schema(dict(entry.data))
        )


class CozyHomeOptionsFlow(OptionsFlow):
    """Integration settings: enabled, and the optional re-sync interval.

    Saving updates the entry options; the update listener in __init__ then
    reloads the entry so the controller picks them up.
    """

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(
                data={
                    CONF_ENABLED: bool(user_input[CONF_ENABLED]),
                    CONF_RESYNC_MINUTES: int(user_input[CONF_RESYNC_MINUTES]),
                }
            )
        options = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_ENABLED, default=options.get(CONF_ENABLED, True)
                ): selector.BooleanSelector(),
                vol.Required(
                    CONF_RESYNC_MINUTES,
                    default=options.get(CONF_RESYNC_MINUTES, DEFAULT_RESYNC_MINUTES),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0,
                        max=1440,
                        step=5,
                        mode=selector.NumberSelectorMode.BOX,
                        unit_of_measurement="min",
                    )
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)


class RoomSubentryFlowHandler(_RoomSteps, ConfigSubentryFlow):
    """Add rooms (several at once), or reconfigure one room."""

    def _assigned(self) -> dict[str, str]:
        """Map every thermostat that belongs to a room → its subentry id."""
        entry = self._get_entry()
        return {
            entity_id: subentry_id
            for subentry_id, subentry in entry.subentries.items()
            if subentry.subentry_type == SUBENTRY_TYPE_ROOM
            for entity_id in room_entities(subentry.data)
        }

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Tick the thermostats that aren't in a room yet."""
        entry = self._get_entry()
        if user_input is None:
            self._start_rooms(
                excluded={entry.data[CONF_MASTER_ENTITY], *self._assigned()},
                taken={s.title for s in entry.subentries.values()},
            )
            if not self._candidates():
                return self.async_abort(reason="no_candidates")
            return self._pick_form("user")

        self._set_pending(user_input.get(CONF_ROOMS, []))
        if not self._pending:
            return self._pick_form("user", {CONF_ROOMS: "no_rooms"})
        return await self.async_step_room()

    async def async_step_room(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """One form per room, then create them all."""
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = self._take_room(user_input)
            if not errors and not self._pending:
                return self._create()
        return self._room_form(errors)

    def _create(self) -> SubentryFlowResult:
        entry = self._get_entry()
        # A subentry flow can only *return* one new subentry; the others are
        # added directly. (The update listener's reload is debounced, so this
        # doesn't reload once per room.)
        for room in self._rooms[:-1]:
            self.hass.config_entries.async_add_subentry(
                entry,
                ConfigSubentry(
                    data=MappingProxyType(dict(room["data"])),
                    subentry_type=SUBENTRY_TYPE_ROOM,
                    title=room["title"],
                    unique_id=None,
                ),
            )
        last = self._rooms[-1]
        return self.async_create_entry(title=last["title"], data=last["data"])

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Reconfigure a room: name, thermostats, initial offset/cut-off."""
        entry = self._get_entry()
        subentry = self._get_reconfigure_subentry()
        mine = room_entities(subentry.data)
        assigned = self._assigned()
        others = {e for e, sid in assigned.items() if sid != subentry.subentry_id}
        allowed = _climate_entities(self.hass, {entry.data[CONF_MASTER_ENTITY], *others})
        allowed = sorted(set(allowed) | set(mine))
        errors: dict[str, str] = {}

        if user_input is not None:
            chosen = [e for e in user_input.get(CONF_CLIMATE_ENTITIES, []) if e in allowed]
            name = str(user_input.get(CONF_ROOM_NAME, "")).strip()
            taken = {s.title for sid, s in entry.subentries.items() if sid != subentry.subentry_id}
            if not chosen:
                errors[CONF_CLIMATE_ENTITIES] = "no_thermostats"
            elif not name:
                errors[CONF_ROOM_NAME] = "name_required"
            elif name in taken:
                errors[CONF_ROOM_NAME] = "name_taken"
            else:
                data = {k: v for k, v in subentry.data.items() if k != CONF_CLIMATE_ENTITY}
                data.update(
                    {
                        CONF_ROOM_NAME: name,
                        CONF_CLIMATE_ENTITIES: chosen,
                        CONF_OFFSET: user_input.get(CONF_OFFSET, DEFAULT_OFFSET),
                        CONF_OFF_BELOW: user_input.get(CONF_OFF_BELOW, DEFAULT_OFF_BELOW),
                    }
                )
                return self.async_update_and_abort(
                    entry, subentry, title=name, unique_id=None, data=data
                )

        defaults = user_input or {**dict(subentry.data), CONF_CLIMATE_ENTITIES: mine}
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_room_schema(defaults, allowed),
            errors=errors,
        )
