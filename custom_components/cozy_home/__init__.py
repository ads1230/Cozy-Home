"""Cozy Home: room thermostats that follow a master (boiler) thermostat."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN
from .controller import HeatingController
from .services import async_setup_services

PLATFORMS: list[Platform] = [
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type CozyHomeConfigEntry = ConfigEntry[HeatingController]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the integration's services (once, not per entry)."""
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: CozyHomeConfigEntry) -> bool:
    """Set up one master thermostat and its rooms."""
    controller = HeatingController(hass, entry)
    await controller.async_setup()
    entry.runtime_data = controller

    # The hub device: rooms hang off it via ``via_device``.
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        manufacturer="Cozy Home",
        model="Master thermostat",
        name=entry.title,
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Rooms are subentries; adding/removing/reconfiguring one fires the update
    # listener, and reloading picks the change up. (Flows therefore use the
    # non-reloading ``async_update_and_abort``.)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    controller.async_start()
    return True


async def _async_update_listener(hass: HomeAssistant, entry: CozyHomeConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: CozyHomeConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    await entry.runtime_data.async_unload()
    return unload_ok


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Delete stored settings when the entry is removed."""
    await HeatingController(hass, entry).async_remove_storage()
