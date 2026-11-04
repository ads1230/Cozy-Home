"""Config flow for the Cozy Home integration.

The config entry holds the master thermostat. Each room is a config
*subentry*. Rooms are added in bulk: a filterable multi-select of every
thermostat Home Assistant already knows about, both when the integration is
first set up and later from "Add rooms" on the integration page. Each room can
still be reconfigured individually afterwards.
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
    SubentryFlowResult,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import selector

from .const import (
    CONF_CLIMATE_ENTITY,
    CONF_MASTER_ENTITY,
    CONF_NAME,
    CONF_OFF_BELOW,
    CONF_OFFSET,
    CONF_ROOM_NAME,
    CONF_ROOMS,
    DEFAULT_NAME,
    DEFAULT_OFF_BELOW,
    DEFAULT_OFFSET,
    DOMAIN,
    OFF_BELOW_MAX,
    OFF_BELOW_MIN,
    OFF_BELOW_STEP,
    OFFSET_MAX,
    OFFSET_MIN,
    OFFSET_STEP,
    SUBENTRY_TYPE_ROOM,
)

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


def _rooms_schema(candidates: list[str], exclude: list[str]) -> vol.Schema:
    """Multi-select of thermostats to add, with offset/cut-off applied to all."""
    return vol.Schema(
        {
            vol.Optional(CONF_ROOMS, default=candidates): selector.EntitySelector(
                selector.EntitySelectorConfig(
                    domain=CLIMATE_DOMAIN, multiple=True, exclude_entities=exclude
                )
            ),
            vol.Optional(CONF_OFFSET, default=DEFAULT_OFFSET): _number(
                OFFSET_MIN, OFFSET_MAX, OFFSET_STEP
            ),
            vol.Optional(CONF_OFF_BELOW, default=DEFAULT_OFF_BELOW): _number(
                OFF_BELOW_MIN, OFF_BELOW_MAX, OFF_BELOW_STEP
            ),
        }
    )


def _room_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    """Single-room form, used when reconfiguring a room."""
    defaults = defaults or {}
    return vol.Schema(
        {
            vol.Required(
                CONF_ROOM_NAME, default=defaults.get(CONF_ROOM_NAME, vol.UNDEFINED)
            ): selector.TextSelector(),
            vol.Required(
                CONF_CLIMATE_ENTITY,
                default=defaults.get(CONF_CLIMATE_ENTITY, vol.UNDEFINED),
            ): CLIMATE_SELECTOR,
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
def room_name_for(hass: HomeAssistant, entity_id: str, taken: set[str]) -> str:
    """A sensible room name: the entity's area if it has one, else its name."""
    ent_reg = er.async_get(hass)
    entry = ent_reg.async_get(entity_id)
    area_id = entry.area_id if entry else None
    if entry and not area_id and entry.device_id:
        device = dr.async_get(hass).async_get(entry.device_id)
        area_id = device.area_id if device else None
    candidates: list[str] = []
    if area_id and (area := ar.async_get(hass).async_get_area(area_id)):
        candidates.append(area.name)
    state = hass.states.get(entity_id)
    friendly = (state and state.attributes.get("friendly_name")) or entity_id.split(".", 1)[
        1
    ].replace("_", " ").title()
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
def _build_rooms(
    hass: HomeAssistant,
    entity_ids: list[str],
    *,
    offset: float,
    off_below: float,
    taken_names: set[str],
) -> list[ConfigSubentryData]:
    rooms: list[ConfigSubentryData] = []
    taken = set(taken_names)
    for entity_id in entity_ids:
        name = room_name_for(hass, entity_id, taken)
        taken.add(name)
        rooms.append(
            ConfigSubentryData(
                data={
                    CONF_ROOM_NAME: name,
                    CONF_CLIMATE_ENTITY: entity_id,
                    CONF_OFFSET: offset,
                    CONF_OFF_BELOW: off_below,
                },
                subentry_type=SUBENTRY_TYPE_ROOM,
                title=name,
                unique_id=entity_id,
            )
        )
    return rooms


class CozyHomeConfigFlow(ConfigFlow, domain=DOMAIN):
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
            return await self.async_step_rooms()

        return self.async_show_form(step_id="user", data_schema=_master_schema())

    async def async_step_rooms(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Tick the thermostats that should follow the master."""
        master = self._master[CONF_MASTER_ENTITY]
        candidates = _climate_entities(self.hass, {master})
        if user_input is not None:
            selected = [e for e in user_input.get(CONF_ROOMS, []) if e != master]
            rooms = _build_rooms(
                self.hass,
                selected,
                offset=user_input.get(CONF_OFFSET, DEFAULT_OFFSET),
                off_below=user_input.get(CONF_OFF_BELOW, DEFAULT_OFF_BELOW),
                taken_names=set(),
            )
            return self.async_create_entry(
                title=self._master[CONF_NAME], data=self._master, subentries=rooms
            )

        return self.async_show_form(
            step_id="rooms",
            data_schema=_rooms_schema(candidates, [master]),
            description_placeholders={"count": str(len(candidates))},
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


class RoomSubentryFlowHandler(ConfigSubentryFlow):
    """Add rooms in bulk, or reconfigure one room."""

    def _configured(self) -> dict[str, str]:
        """Map configured climate entity → subentry id."""
        entry = self._get_entry()
        return {
            subentry.data[CONF_CLIMATE_ENTITY]: subentry_id
            for subentry_id, subentry in entry.subentries.items()
            if subentry.subentry_type == SUBENTRY_TYPE_ROOM
        }

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Add one or more rooms from the thermostats not yet configured."""
        entry = self._get_entry()
        master = entry.data[CONF_MASTER_ENTITY]
        exclude = {master, *self._configured()}
        candidates = _climate_entities(self.hass, exclude)
        errors: dict[str, str] = {}

        if user_input is not None:
            selected = [e for e in user_input.get(CONF_ROOMS, []) if e not in exclude]
            if not selected:
                errors[CONF_ROOMS] = "no_rooms"
            else:
                rooms = _build_rooms(
                    self.hass,
                    selected,
                    offset=user_input.get(CONF_OFFSET, DEFAULT_OFFSET),
                    off_below=user_input.get(CONF_OFF_BELOW, DEFAULT_OFF_BELOW),
                    taken_names={s.title for s in entry.subentries.values()},
                )
                # A subentry flow can only *return* one new subentry; the
                # others are added directly. (The update listener's reload is
                # debounced, so this doesn't reload once per room.)
                for room in rooms[:-1]:
                    self.hass.config_entries.async_add_subentry(
                        entry,
                        ConfigSubentry(
                            data=MappingProxyType(dict(room["data"])),
                            subentry_type=SUBENTRY_TYPE_ROOM,
                            title=room["title"],
                            unique_id=room["unique_id"],
                        ),
                    )
                last = rooms[-1]
                return self.async_create_entry(
                    title=last["title"], data=last["data"], unique_id=last["unique_id"]
                )

        if not candidates and user_input is None:
            return self.async_abort(reason="no_candidates")

        return self.async_show_form(
            step_id="user",
            data_schema=_rooms_schema(candidates, sorted(exclude)),
            errors=errors,
            description_placeholders={"count": str(len(candidates))},
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Reconfigure a room (name, thermostat, initial offset/cut-off)."""
        entry = self._get_entry()
        subentry = self._get_reconfigure_subentry()
        errors: dict[str, str] = {}
        if user_input is not None:
            climate = user_input[CONF_CLIMATE_ENTITY]
            configured = self._configured()
            if climate == entry.data[CONF_MASTER_ENTITY]:
                errors[CONF_CLIMATE_ENTITY] = "same_as_master"
            elif configured.get(climate, subentry.subentry_id) != subentry.subentry_id:
                errors[CONF_CLIMATE_ENTITY] = "already_configured"
            else:
                return self.async_update_and_abort(
                    entry,
                    subentry,
                    title=user_input[CONF_ROOM_NAME],
                    unique_id=climate,
                    data_updates=user_input,
                )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_room_schema(user_input or dict(subentry.data)),
            errors=errors,
        )
