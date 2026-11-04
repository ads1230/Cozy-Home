"""One-time clean-ups when an older install is upgraded."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import CONF_CLIMATE_ENTITIES, CONF_CLIMATE_ENTITY, DOMAIN, SUBENTRY_TYPE_ROOM

_LOGGER = logging.getLogger(__name__)


@callback
def area_of(hass: HomeAssistant, entity_id: str) -> str | None:
    """The area id of an entity: its own, or its device's."""
    entry = er.async_get(hass).async_get(entity_id)
    if entry is None:
        return None
    if entry.area_id:
        return entry.area_id
    if entry.device_id and (device := dr.async_get(hass).async_get(entry.device_id)):
        return device.area_id
    return None


@callback
def shared_area(hass: HomeAssistant, entity_ids: list[str]) -> str | None:
    """The one area all these entities are in, or None."""
    areas = {area_of(hass, e) for e in entity_ids}
    if len(areas) == 1 and (area := areas.pop()):
        return area
    return None


@callback
def async_merge_legacy_rooms(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Group rooms created by 0.3 and earlier by Home Assistant area.

    Those versions made one room per thermostat, so three radiators in the
    "Upstairs" area became three rooms. Now that a room owns several
    thermostats, merge such rooms the way adding them today would: one room
    per area. The room named after the area (or else the first) survives with
    its settings and schedule; the others are removed. Rooms without an area
    are just converted to the current format.

    Runs before the update listener is registered, so it doesn't reload.
    """
    legacy = {
        subentry_id: subentry
        for subentry_id, subentry in entry.subentries.items()
        if subentry.subentry_type == SUBENTRY_TYPE_ROOM
        and subentry.data.get(CONF_CLIMATE_ENTITY)
        and not subentry.data.get(CONF_CLIMATE_ENTITIES)
    }
    if not legacy:
        return

    by_area: dict[str | None, list[str]] = {}
    for subentry_id, subentry in legacy.items():
        area = area_of(hass, subentry.data[CONF_CLIMATE_ENTITY])
        by_area.setdefault(area, []).append(subentry_id)

    area_reg = ar.async_get(hass)
    dev_reg = dr.async_get(hass)
    for area, subentry_ids in by_area.items():
        if area is None:
            groups = [[sid] for sid in subentry_ids]  # no area: leave as they are
        else:
            groups = [subentry_ids]
        for group in groups:
            area_name = None
            if area and (area_entry := area_reg.async_get_area(area)):
                area_name = area_entry.name
            keep = next((sid for sid in group if legacy[sid].title == area_name), group[0])
            entities = [legacy[keep].data[CONF_CLIMATE_ENTITY]] + [
                legacy[sid].data[CONF_CLIMATE_ENTITY] for sid in group if sid != keep
            ]
            kept = legacy[keep]
            data = {k: v for k, v in kept.data.items() if k != CONF_CLIMATE_ENTITY}
            data[CONF_CLIMATE_ENTITIES] = entities
            hass.config_entries.async_update_subentry(entry, kept, data=data, unique_id=None)
            for sid in group:
                if sid != keep:
                    _LOGGER.warning(
                        "Cozy Home: merged room '%s' into '%s' (both in area '%s'); "
                        "its own offset/schedule were dropped — adjust '%s' if needed",
                        legacy[sid].title,
                        kept.title,
                        area_name,
                        kept.title,
                    )
                    hass.config_entries.async_remove_subentry(entry, sid)
                    # Its device normally goes with the subentry; make sure.
                    if gone := dev_reg.async_get_device(identifiers={(DOMAIN, sid)}):
                        dev_reg.async_update_device(gone.id, remove_config_entry_id=entry.entry_id)
            if area and (device := dev_reg.async_get_device(identifiers={(DOMAIN, keep)})):
                dev_reg.async_update_device(device.id, area_id=area)
