"""Base entities for Cozy Home."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.core import callback
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import DOMAIN
from .controller import HeatingController, RoomState
from .migration import shared_area


class CozyHomeEntity(Entity):
    """Entity attached to the hub (master) device.

    Cozy Home's entities are hidden by default: the sidebar page and the
    cards are the UI, and hidden entities stay out of auto-generated
    dashboards (the Overview's "Favourites" row in particular). They still
    work in automations and can be unhidden per entity.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_entity_registry_visible_default = False

    def __init__(self, controller: HeatingController, entry: ConfigEntry) -> None:
        self._controller = controller
        self._attr_unique_id = f"{entry.entry_id}_{self._attr_translation_key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Cozy Home",
            model="Master thermostat",
        )

    async def async_added_to_hass(self) -> None:
        """Refresh whenever the controller re-evaluates."""
        await super().async_added_to_hass()
        self.async_on_remove(self._controller.async_add_listener(self._handle_update))

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()


class CozyHomeRoomEntity(CozyHomeEntity):
    """Entity attached to a room device (one per subentry)."""

    def __init__(
        self,
        controller: HeatingController,
        entry: ConfigEntry,
        subentry: ConfigSubentry,
    ) -> None:
        super().__init__(controller, entry)
        self._subentry_id = subentry.subentry_id
        self._attr_unique_id = f"{subentry.subentry_id}_{self._attr_translation_key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, subentry.subentry_id)},
            name=subentry.title,
            manufacturer="Cozy Home",
            model="Room",
            via_device=(DOMAIN, entry.entry_id),
        )

    @property
    def device_info(self) -> DeviceInfo | None:
        """The room device, suggested into the area its thermostats share."""
        info = self._attr_device_info
        if info is None or self.hass is None or self._subentry_id not in self._controller.rooms:
            return info
        area = shared_area(self.hass, self.room.climate_entities)
        if area and (area_entry := ar.async_get(self.hass).async_get_area(area)):
            return DeviceInfo(**info, suggested_area=area_entry.name)
        return info

    @property
    def room(self) -> RoomState:
        """The room's live settings and computed state."""
        return self._controller.room(self._subentry_id)

    @property
    def available(self) -> bool:
        return self._subentry_id in self._controller.rooms
