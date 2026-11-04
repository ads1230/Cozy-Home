"""Config flow and room subentry flow tests."""

from __future__ import annotations

from datetime import timedelta

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.cozy_home.const import (
    CONF_CLIMATE_ENTITY,
    CONF_MASTER_ENTITY,
    CONF_NAME,
    CONF_OFF_BELOW,
    CONF_OFFSET,
    CONF_ROOM_NAME,
    CONF_ROOMS,
    DOMAIN,
    SUBENTRY_TYPE_ROOM,
)

from .conftest import BEDROOM, MASTER, OFFICE, TRV_ATTRS, set_master, set_trv, setup_integration


async def test_user_flow_creates_hub_with_rooms(hass: HomeAssistant, climate_calls) -> None:
    """Master first, then a multi-select of every other thermostat (all pre-ticked)."""
    set_master(hass)
    set_trv(hass, BEDROOM, friendly_name="Bedroom TRV")
    hass.states.async_set("climate.hallway_trv", "heat", {"friendly_name": "Hallway TRV"})
    # The office TRV is registered and sits in an area -> its room is named after the area
    area = ar.async_get(hass).async_get_or_create("Study")
    ent_reg = er.async_get(hass)
    registered = ent_reg.async_get_or_create(
        "climate", "test", "office", suggested_object_id="office_trv"
    )
    assert registered.entity_id == OFFICE
    ent_reg.async_update_entity(OFFICE, area_id=area.id)
    set_trv(hass, OFFICE)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_MASTER_ENTITY: MASTER, CONF_NAME: "Heating"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "rooms"
    assert result["description_placeholders"] == {"count": "3"}
    # Everything but the master is offered, pre-ticked
    defaults = result["data_schema"]({})
    assert sorted(defaults[CONF_ROOMS]) == sorted([BEDROOM, OFFICE, "climate.hallway_trv"])

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_ROOMS: [BEDROOM, OFFICE], CONF_OFFSET: -1.0, CONF_OFF_BELOW: 12.0},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Heating"
    assert result["data"] == {CONF_MASTER_ENTITY: MASTER, CONF_NAME: "Heating"}
    entry = result["result"]
    assert entry.unique_id == MASTER
    rooms = {s.unique_id: s for s in entry.subentries.values()}
    assert set(rooms) == {BEDROOM, OFFICE}
    assert rooms[OFFICE].title == "Study"  # named after its area
    assert rooms[BEDROOM].title == "Bedroom"  # "Bedroom TRV" with the noise word stripped
    assert rooms[BEDROOM].data[CONF_OFFSET] == -1.0
    assert rooms[BEDROOM].data[CONF_OFF_BELOW] == 12.0
    await hass.async_block_till_done()
    assert hass.states.get("number.bedroom_offset").state == "-1.0"
    assert hass.states.get("number.study_offset").state == "-1.0"


async def test_user_flow_with_no_rooms(hass: HomeAssistant, climate_calls) -> None:
    set_master(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_MASTER_ENTITY: MASTER, CONF_NAME: "Heating"}
    )
    assert result["step_id"] == "rooms"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_ROOMS: []})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].subentries == {}


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
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=2))  # debounced reload
    await hass.async_block_till_done()
    assert config_entry.data[CONF_MASTER_ENTITY] == "climate.other"
    assert config_entry.unique_id == "climate.other"
    # Update listener reloaded the entry with the new master
    assert config_entry.runtime_data.master_entity == "climate.other"


async def test_add_rooms_subentry(
    hass: HomeAssistant, config_entry: MockConfigEntry, climate_calls
) -> None:
    """Bulk-add from the integration page: unconfigured thermostats only, one reload."""
    set_master(hass, temperature=20)
    set_trv(hass, BEDROOM)
    set_trv(hass, OFFICE, temperature=15)
    hass.states.async_set(
        "climate.kitchen_trv", "heat", {**TRV_ATTRS, "friendly_name": "Kitchen Radiator"}
    )
    await setup_integration(hass, config_entry)

    result = await hass.config_entries.subentries.async_init(
        (config_entry.entry_id, SUBENTRY_TYPE_ROOM),
        context={"source": config_entries.SOURCE_USER},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    # The master and the existing bedroom are not offered
    assert sorted(result["data_schema"]({})[CONF_ROOMS]) == [
        climate_kitchen := "climate.kitchen_trv",
        OFFICE,
    ]

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {CONF_ROOMS: [OFFICE, climate_kitchen], CONF_OFFSET: 1.0, CONF_OFF_BELOW: 12.0},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=2))  # debounced reload
    await hass.async_block_till_done()

    assert {s.title for s in config_entry.subentries.values()} == {"Bedroom", "Office", "Kitchen"}
    controller = config_entry.runtime_data
    office = next(r for r in controller.rooms.values() if r.name == "Office")
    assert office.target == 21.0  # 20 + 1, synced after the reload
    assert any(
        c.data["entity_id"] == OFFICE and c.data["temperature"] == 21.0
        for c in climate_calls["set_temperature"]
    )
    assert hass.states.get("number.office_offset").state == "1.0"
    assert hass.states.get("number.kitchen_offset").state == "1.0"


async def test_add_rooms_requires_a_selection_and_aborts_when_none_left(
    hass: HomeAssistant, config_entry: MockConfigEntry, climate_calls
) -> None:
    set_master(hass)
    set_trv(hass, BEDROOM)
    set_trv(hass, OFFICE)
    await setup_integration(hass, config_entry)

    result = await hass.config_entries.subentries.async_init(
        (config_entry.entry_id, SUBENTRY_TYPE_ROOM),
        context={"source": config_entries.SOURCE_USER},
    )
    # The master and existing rooms can't even be picked (selector excludes them);
    # submitting nothing is an error
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_ROOMS: []}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_ROOMS: "no_rooms"}

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_ROOMS: [OFFICE]}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=2))
    await hass.async_block_till_done()

    # Now every thermostat is a room: the flow aborts straight away
    result = await hass.config_entries.subentries.async_init(
        (config_entry.entry_id, SUBENTRY_TYPE_ROOM),
        context={"source": config_entries.SOURCE_USER},
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_candidates"


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
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=2))  # debounced reload
    await hass.async_block_till_done()

    subentry = config_entry.subentries[subentry.subentry_id]
    assert subentry.title == "Main bedroom"
    assert subentry.data[CONF_CLIMATE_ENTITY] == OFFICE
    assert subentry.unique_id == OFFICE
    room = config_entry.runtime_data.room(subentry.subentry_id)
    assert room.climate_entity == OFFICE
    assert room.name == "Main bedroom"
