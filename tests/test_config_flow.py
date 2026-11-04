"""Config flow and room subentry flow tests."""

from __future__ import annotations

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.cozy_home.const import (
    CONF_CLIMATE_ENTITY,
    CONF_MASTER_ENTITY,
    CONF_NAME,
    CONF_OFF_BELOW,
    CONF_OFFSET,
    CONF_ROOM_NAME,
    DOMAIN,
    SUBENTRY_TYPE_ROOM,
)

from .conftest import BEDROOM, MASTER, OFFICE, set_master, set_trv, setup_integration


async def test_user_flow_creates_hub(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_MASTER_ENTITY: MASTER, CONF_NAME: "Heating"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Heating"
    assert result["data"] == {CONF_MASTER_ENTITY: MASTER, CONF_NAME: "Heating"}
    assert result["result"].unique_id == MASTER


async def test_user_flow_aborts_on_duplicate_master(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_MASTER_ENTITY: MASTER, CONF_NAME: "Again"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reconfigure_master(
    hass: HomeAssistant, config_entry: MockConfigEntry, climate_calls
) -> None:
    set_master(hass)
    set_trv(hass, BEDROOM)
    await setup_integration(hass, config_entry)

    result = await config_entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_MASTER_ENTITY: "climate.other", CONF_NAME: "Heating"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()
    assert config_entry.data[CONF_MASTER_ENTITY] == "climate.other"
    assert config_entry.unique_id == "climate.other"
    # Update listener reloaded the entry with the new master
    assert config_entry.runtime_data.master_entity == "climate.other"


async def test_add_room_subentry(
    hass: HomeAssistant, config_entry: MockConfigEntry, climate_calls
) -> None:
    set_master(hass, temperature=20)
    set_trv(hass, BEDROOM)
    set_trv(hass, OFFICE, temperature=15)
    await setup_integration(hass, config_entry)

    result = await hass.config_entries.subentries.async_init(
        (config_entry.entry_id, SUBENTRY_TYPE_ROOM),
        context={"source": config_entries.SOURCE_USER},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_ROOM_NAME: "Office",
            CONF_CLIMATE_ENTITY: OFFICE,
            CONF_OFFSET: 1.0,
            CONF_OFF_BELOW: 12.0,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Office"
    assert result["unique_id"] == OFFICE
    await hass.async_block_till_done()

    # Entry was reloaded with the new room and it was synced: 20 + 1 = 21
    assert len(config_entry.subentries) == 2
    controller = config_entry.runtime_data
    office = next(r for r in controller.rooms.values() if r.name == "Office")
    assert office.target == 21.0
    assert any(
        c.data["entity_id"] == OFFICE and c.data["temperature"] == 21.0
        for c in climate_calls["set_temperature"]
    )
    assert hass.states.get("number.office_offset").state == "1.0"
    assert hass.states.get("sensor.office_target_temperature").state == "21.0"


async def test_add_room_rejects_master_and_duplicates(
    hass: HomeAssistant, config_entry: MockConfigEntry, climate_calls
) -> None:
    set_master(hass)
    set_trv(hass, BEDROOM)
    await setup_integration(hass, config_entry)

    result = await hass.config_entries.subentries.async_init(
        (config_entry.entry_id, SUBENTRY_TYPE_ROOM),
        context={"source": config_entries.SOURCE_USER},
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_ROOM_NAME: "Oops", CONF_CLIMATE_ENTITY: MASTER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_CLIMATE_ENTITY: "same_as_master"}

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_ROOM_NAME: "Oops", CONF_CLIMATE_ENTITY: BEDROOM}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_CLIMATE_ENTITY: "already_configured"}


async def test_reconfigure_room(
    hass: HomeAssistant, config_entry: MockConfigEntry, climate_calls
) -> None:
    set_master(hass)
    set_trv(hass, BEDROOM)
    set_trv(hass, OFFICE)
    await setup_integration(hass, config_entry)

    subentry = next(iter(config_entry.subentries.values()))
    result = await hass.config_entries.subentries.async_init(
        (config_entry.entry_id, SUBENTRY_TYPE_ROOM),
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "subentry_id": subentry.subentry_id,
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_ROOM_NAME: "Main bedroom",
            CONF_CLIMATE_ENTITY: OFFICE,
            CONF_OFFSET: -2.0,
            CONF_OFF_BELOW: 10.0,
        },
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()

    subentry = config_entry.subentries[subentry.subentry_id]
    assert subentry.title == "Main bedroom"
    assert subentry.data[CONF_CLIMATE_ENTITY] == OFFICE
    assert subentry.unique_id == OFFICE
    room = config_entry.runtime_data.room(subentry.subentry_id)
    assert room.climate_entity == OFFICE
    assert room.name == "Main bedroom"
