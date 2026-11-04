"""Shared fixtures."""

from __future__ import annotations

from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_mock_service,
)

from custom_components.cozy_home.const import (
    CONF_CLIMATE_ENTITIES,
    CONF_MASTER_ENTITY,
    CONF_NAME,
    CONF_OFF_BELOW,
    CONF_OFFSET,
    CONF_ROOM_NAME,
    DOMAIN,
    SUBENTRY_TYPE_ROOM,
)

MASTER = "climate.hive_thermostat"
BEDROOM = "climate.bedroom_trv"
OFFICE = "climate.office_trv"

TRV_ATTRS: dict[str, Any] = {
    "hvac_modes": ["auto", "heat", "off"],
    "min_temp": 5,
    "max_temp": 30,
    "target_temp_step": 0.5,
    "temperature": 15,
    "current_temperature": 18,
}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Let the test harness load custom_components/."""


@pytest.fixture(autouse=True)
async def london_time(hass: HomeAssistant) -> None:
    """Use a fixed, DST-aware time zone for schedule tests."""
    await hass.config.async_set_time_zone("Europe/London")


@pytest.fixture
def climate_calls(hass: HomeAssistant) -> dict[str, list]:
    """Mock the climate services and collect calls."""
    return {
        "set_temperature": async_mock_service(hass, "climate", "set_temperature"),
        "set_hvac_mode": async_mock_service(hass, "climate", "set_hvac_mode"),
    }


def set_master(hass: HomeAssistant, state: str = "heat", temperature: float = 20) -> None:
    hass.states.async_set(
        MASTER,
        state,
        {
            "hvac_modes": ["auto", "heat", "off"],
            "temperature": temperature,
            "min_temp": 5,
            "max_temp": 32,
        },
    )


def set_trv(
    hass: HomeAssistant,
    entity_id: str,
    state: str = "heat",
    temperature: float = 15,
    **extra: Any,
) -> None:
    hass.states.async_set(entity_id, state, {**TRV_ATTRS, "temperature": temperature, **extra})


@pytest.fixture
def config_entry(hass: HomeAssistant) -> MockConfigEntry:
    """A hub with one room (bedroom, offset -2, off below 10)."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Heating",
        unique_id=MASTER,
        data={CONF_MASTER_ENTITY: MASTER, CONF_NAME: "Heating"},
        subentries_data=[
            {
                "data": {
                    CONF_ROOM_NAME: "Bedroom",
                    CONF_CLIMATE_ENTITIES: [BEDROOM],
                    CONF_OFFSET: -2.0,
                    CONF_OFF_BELOW: 10.0,
                },
                "subentry_type": SUBENTRY_TYPE_ROOM,
                "title": "Bedroom",
                "unique_id": None,
                "subentry_id": "bedroom_sub",
            }
        ],
    )
    entry.add_to_hass(hass)
    return entry


async def setup_integration(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Load the entry and wait for everything to settle."""
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def room_device_id(hass: HomeAssistant, subentry_id: str) -> str:
    from homeassistant.helpers import device_registry as dr

    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, subentry_id)})
    assert device is not None
    return device.id
