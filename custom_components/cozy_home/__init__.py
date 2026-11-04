"""Cozy Home: room thermostats that follow a master (boiler) thermostat."""

from __future__ import annotations

import logging
from functools import partial
from pathlib import Path

from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration

from .const import DOMAIN
from .controller import HeatingController
from .migration import async_merge_legacy_rooms
from .services import async_setup_services

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

CARD_FILES = ("cozy-home-schedule-card.js", "cozy-home-rooms-card.js")
RELOAD_DEBOUNCE_SECONDS = 1.0

type CozyHomeConfigEntry = ConfigEntry[HeatingController]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the services and the dashboard card (once, not per entry)."""
    async_setup_services(hass)
    await _async_register_cards(hass)
    return True


async def _async_register_cards(hass: HomeAssistant) -> None:
    """Serve the bundled dashboard cards and have the frontend load them.

    The cards are plain JS modules in ``www/``; registering them here means the
    user doesn't have to add dashboard resources by hand.
    """
    www = Path(__file__).parent / "www"
    files = [name for name in CARD_FILES if (www / name).is_file()]
    if not files:
        return
    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(f"/{DOMAIN}/{name}", str(www / name), cache_headers=True)
            for name in files
        ]
    )
    if "frontend" not in hass.config.components:
        return  # e.g. tests, or a headless install
    # Imported lazily so the integration also loads where the frontend isn't set up.
    from homeassistant.components.frontend import add_extra_js_url  # noqa: PLC0415

    integration = await async_get_integration(hass, DOMAIN)
    for name in files:
        add_extra_js_url(hass, f"/{DOMAIN}/{name}?v={integration.version}")


async def async_setup_entry(hass: HomeAssistant, entry: CozyHomeConfigEntry) -> bool:
    """Set up one master thermostat and its rooms."""
    # Rooms from 0.3 and earlier (one thermostat each) are grouped by area.
    async_merge_legacy_rooms(hass, entry)

    controller = HeatingController(hass, entry)
    await controller.async_setup()
    entry.runtime_data = controller

    # Pre-0.3 installs had a global on/off switch entity; it's an integration
    # setting now ("Configure"), so clean up the orphaned registry entry.
    ent_reg = er.async_get(hass)
    if stale := ent_reg.async_get_entity_id(Platform.SWITCH, DOMAIN, f"{entry.entry_id}_enabled"):
        ent_reg.async_remove(stale)

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
    # non-reloading ``async_update_and_abort``.) Adding several rooms at once
    # fires the listener once per room, so the reload is debounced.
    reload = Debouncer(
        hass,
        _LOGGER,
        cooldown=RELOAD_DEBOUNCE_SECONDS,
        immediate=False,
        function=partial(hass.config_entries.async_reload, entry.entry_id),
    )

    async def _async_update_listener(_hass: HomeAssistant, _entry: ConfigEntry) -> None:
        await reload.async_call()

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    entry.async_on_unload(reload.async_shutdown)

    controller.async_start()
    return True


async def async_unload_entry(hass: HomeAssistant, entry: CozyHomeConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    await entry.runtime_data.async_unload()
    return unload_ok


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Delete stored settings when the entry is removed."""
    await HeatingController(hass, entry).async_remove_storage()
