"""Services: set / get / clear schedules, and force a sync."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import (
    ATTR_DEVICE_ID,
    ATTR_SCHEDULE,
    DOMAIN,
    SERVICE_CLEAR_SCHEDULE,
    SERVICE_GET_SCHEDULE,
    SERVICE_SET_SCHEDULE,
    SERVICE_SYNC,
)
from .controller import HeatingController
from .schedule import validate

# A target is either a Cozy Home device or one of its entities (the card uses
# the schedule sensor's entity_id).
TARGET_SCHEMA = vol.All(
    vol.Schema(
        {
            vol.Optional(ATTR_DEVICE_ID): cv.string,
            vol.Optional(ATTR_ENTITY_ID): cv.entity_id,
        }
    ),
    cv.has_at_least_one_key(ATTR_DEVICE_ID, ATTR_ENTITY_ID),
)
SET_SCHEDULE_SCHEMA = vol.All(
    vol.Schema(
        {
            vol.Optional(ATTR_DEVICE_ID): cv.string,
            vol.Optional(ATTR_ENTITY_ID): cv.entity_id,
            vol.Required(ATTR_SCHEDULE): dict,
        }
    ),
    cv.has_at_least_one_key(ATTR_DEVICE_ID, ATTR_ENTITY_ID),
)
SYNC_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_DEVICE_ID): cv.string,
        vol.Optional(ATTR_ENTITY_ID): cv.entity_id,
    }
)


def _controllers(hass: HomeAssistant) -> list[HeatingController]:
    return [
        entry.runtime_data
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.state is ConfigEntryState.LOADED
    ]


def _resolve(hass: HomeAssistant, data: Mapping[str, Any]) -> tuple[HeatingController, str | None]:
    """Map a device (or one of its entities) to ``(controller, room_subentry_id | None)``.

    ``None`` for the room means the device is the hub (master thermostat).
    """
    device_id: str | None = data.get(ATTR_DEVICE_ID)
    if device_id is None and (entity_id := data.get(ATTR_ENTITY_ID)):
        entity_entry = er.async_get(hass).async_get(entity_id)
        device_id = entity_entry.device_id if entity_entry else None
    device = dr.async_get(hass).async_get(device_id) if device_id else None
    if device is not None:
        idents = {ident for domain, ident in device.identifiers if domain == DOMAIN}
        for controller in _controllers(hass):
            if controller.entry.entry_id in idents:
                return controller, None
            for subentry_id in controller.rooms:
                if subentry_id in idents:
                    return controller, subentry_id
    raise ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key="device_not_found",
        translation_placeholders={
            "device_id": str(data.get(ATTR_DEVICE_ID) or data.get(ATTR_ENTITY_ID))
        },
    )


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the integration's services."""

    async def set_schedule(call: ServiceCall) -> None:
        controller, room_id = _resolve(hass, call.data)
        try:
            schedule = validate(call.data[ATTR_SCHEDULE], master=room_id is None)
        except vol.Invalid as err:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="invalid_schedule",
                translation_placeholders={"error": str(err)},
            ) from err
        if room_id is None:
            await controller.async_set_master_schedule(schedule)
        else:
            await controller.async_set_room_schedule(room_id, schedule)

    async def get_schedule(call: ServiceCall) -> ServiceResponse:
        controller, room_id = _resolve(hass, call.data)
        if room_id is None:
            schedule: dict[str, Any] = controller.master_schedule
            active = controller.master_active_block
        else:
            room = controller.room(room_id)
            schedule = room.schedule
            active = (room.active_day, room.active_block) if room.active_block else None
        return {
            "schedule": schedule,
            "active": {"day": active[0], **active[1]} if active else None,
        }

    async def clear_schedule(call: ServiceCall) -> None:
        controller, room_id = _resolve(hass, call.data)
        if room_id is None:
            await controller.async_set_master_schedule({})
        else:
            await controller.async_set_room_schedule(room_id, {})

    async def sync(call: ServiceCall) -> None:
        if ATTR_DEVICE_ID not in call.data and ATTR_ENTITY_ID not in call.data:
            for controller in _controllers(hass):
                await controller.async_sync_all(force=True)
            return
        controller, room_id = _resolve(hass, call.data)
        if room_id is None:
            await controller.async_sync_all(force=True)
        else:
            await controller.async_sync_room(room_id, force=True)

    hass.services.async_register(
        DOMAIN, SERVICE_SET_SCHEDULE, set_schedule, schema=SET_SCHEDULE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_SCHEDULE,
        get_schedule,
        schema=TARGET_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_CLEAR_SCHEDULE, clear_schedule, schema=TARGET_SCHEMA
    )
    hass.services.async_register(DOMAIN, SERVICE_SYNC, sync, schema=SYNC_SCHEMA)
