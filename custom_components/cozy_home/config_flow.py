"""Config flow for the Cozy Home integration.

The config entry holds the master thermostat. Each room is a config
*subentry*, added from the integration page with "Add room".
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components.climate import DOMAIN as CLIMATE_DOMAIN
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    SubentryFlowResult,
)
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_CLIMATE_ENTITY,
    CONF_MASTER_ENTITY,
    CONF_NAME,
    CONF_OFF_BELOW,
    CONF_OFFSET,
    CONF_ROOM_NAME,
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


def _room_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
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
            vol.Optional(
                CONF_OFFSET, default=defaults.get(CONF_OFFSET, DEFAULT_OFFSET)
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=OFFSET_MIN,
                    max=OFFSET_MAX,
                    step=OFFSET_STEP,
                    mode=selector.NumberSelectorMode.BOX,
                    unit_of_measurement="°",
                )
            ),
            vol.Optional(
                CONF_OFF_BELOW, default=defaults.get(CONF_OFF_BELOW, DEFAULT_OFF_BELOW)
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=OFF_BELOW_MIN,
                    max=OFF_BELOW_MAX,
                    step=OFF_BELOW_STEP,
                    mode=selector.NumberSelectorMode.BOX,
                    unit_of_measurement="°",
                )
            ),
        }
    )


class CozyHomeConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the config flow for the master thermostat."""

    VERSION = 1
    MINOR_VERSION = 1

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Return the subentry flows this integration supports."""
        return {SUBENTRY_TYPE_ROOM: RoomSubentryFlowHandler}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Pick the master (boiler) thermostat."""
        errors: dict[str, str] = {}
        if user_input is not None:
            # One Cozy Home hub per master thermostat.
            await self.async_set_unique_id(user_input[CONF_MASTER_ENTITY])
            self._abort_if_unique_id_configured(reload_on_update=False)
            return self.async_create_entry(
                title=user_input.get(CONF_NAME) or DEFAULT_NAME,
                data={
                    CONF_MASTER_ENTITY: user_input[CONF_MASTER_ENTITY],
                    CONF_NAME: user_input.get(CONF_NAME) or DEFAULT_NAME,
                },
            )

        return self.async_show_form(step_id="user", data_schema=_master_schema(), errors=errors)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the master thermostat or the hub name."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
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
            step_id="reconfigure",
            data_schema=_master_schema(dict(entry.data)),
            errors=errors,
        )


class RoomSubentryFlowHandler(ConfigSubentryFlow):
    """Add or reconfigure a room that follows the master thermostat."""

    def _validate(
        self, user_input: dict[str, Any], *, current_subentry_id: str | None
    ) -> dict[str, str]:
        """Return form errors for a room submission."""
        entry = self._get_entry()
        climate = user_input[CONF_CLIMATE_ENTITY]
        if climate == entry.data[CONF_MASTER_ENTITY]:
            return {CONF_CLIMATE_ENTITY: "same_as_master"}
        for subentry_id, subentry in entry.subentries.items():
            if subentry_id == current_subentry_id:
                continue
            if subentry.data.get(CONF_CLIMATE_ENTITY) == climate:
                return {CONF_CLIMATE_ENTITY: "already_configured"}
        return {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Add a room."""
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = self._validate(user_input, current_subentry_id=None)
            if not errors:
                return self.async_create_entry(
                    title=user_input[CONF_ROOM_NAME],
                    data=user_input,
                    unique_id=user_input[CONF_CLIMATE_ENTITY],
                )

        return self.async_show_form(
            step_id="user",
            data_schema=_room_schema(user_input),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Reconfigure a room (name, thermostat, initial offset/cut-off)."""
        entry = self._get_entry()
        subentry = self._get_reconfigure_subentry()
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = self._validate(user_input, current_subentry_id=subentry.subentry_id)
            if not errors:
                return self.async_update_and_abort(
                    entry,
                    subentry,
                    title=user_input[CONF_ROOM_NAME],
                    unique_id=user_input[CONF_CLIMATE_ENTITY],
                    data_updates=user_input,
                )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_room_schema(user_input or dict(subentry.data)),
            errors=errors,
        )
