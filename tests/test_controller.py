"""End-to-end behaviour: master → rooms, offsets, cut-off, schedules, services."""

from __future__ import annotations

from datetime import datetime, timezone

from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED, STATE_UNAVAILABLE
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.cozy_home.const import DOMAIN

from .conftest import (
    BEDROOM,
    MASTER,
    OFFICE,
    room_device_id,
    set_master,
    set_trv,
    setup_integration,
)


def _temps(calls: dict[str, list], entity_id: str = BEDROOM) -> list[float]:
    return [
        c.data["temperature"] for c in calls["set_temperature"] if c.data["entity_id"] == entity_id
    ]


def _modes(calls: dict[str, list], entity_id: str = BEDROOM) -> list[str]:
    return [c.data["hvac_mode"] for c in calls["set_hvac_mode"] if c.data["entity_id"] == entity_id]


def _clear(calls: dict[str, list]) -> None:
    for lst in calls.values():
        lst.clear()


async def test_initial_sync_applies_offset(
    hass: HomeAssistant, config_entry: MockConfigEntry, climate_calls
) -> None:
    set_master(hass, temperature=20)
    set_trv(hass, BEDROOM, temperature=15)
    await setup_integration(hass, config_entry)

    assert _temps(climate_calls) == [18.0]
    assert _modes(climate_calls) == []  # already heating
    target = hass.states.get("sensor.bedroom_target_temperature")
    assert target.state == "18.0"
    assert target.attributes["mode"] == "follow"
    assert target.attributes["master_target"] == 20.0
    assert hass.states.get("sensor.bedroom_mode").state == "follow"
    assert hass.states.get("number.bedroom_offset").state == "-2.0"
    assert hass.states.get("number.bedroom_cut_off_temperature").state == "10.0"
    assert hass.states.get("switch.bedroom_follow_master").state == "on"
    assert hass.states.get("switch.heating_enabled") is None  # global on/off is a setting now


async def test_master_change_pushes_rooms(
    hass: HomeAssistant, config_entry: MockConfigEntry, climate_calls
) -> None:
    set_master(hass, temperature=20)
    set_trv(hass, BEDROOM, temperature=18)
    await setup_integration(hass, config_entry)
    assert _temps(climate_calls) == []  # TRV already at 18, nothing to do

    set_master(hass, temperature=21.5)
    await hass.async_block_till_done()
    assert _temps(climate_calls) == [19.5]

    # Same target again (e.g. hvac_mode changed, temperature unchanged): no call
    hass.states.async_set(MASTER, "auto", {**hass.states.get(MASTER).attributes})
    await hass.async_block_till_done()
    assert _temps(climate_calls) == [19.5]


async def test_rounding_and_clamping(
    hass: HomeAssistant, config_entry: MockConfigEntry, climate_calls
) -> None:
    set_master(hass, temperature=20.3)
    set_trv(hass, BEDROOM, temperature=15)
    await setup_integration(hass, config_entry)
    assert _temps(climate_calls) == [18.5]  # 18.3 rounded to the 0.5 step

    _clear(climate_calls)
    set_master(hass, temperature=31)
    await hass.async_block_till_done()
    assert _temps(climate_calls) == [29.0]

    _clear(climate_calls)
    set_master(hass, temperature=35)  # 33 > max_temp 30
    await hass.async_block_till_done()
    assert _temps(climate_calls) == [30.0]


async def test_turns_off_below_threshold_and_back_on(
    hass: HomeAssistant, config_entry: MockConfigEntry, climate_calls
) -> None:
    set_master(hass, temperature=20)
    set_trv(hass, BEDROOM, temperature=18)
    await setup_integration(hass, config_entry)

    set_master(hass, temperature=7)  # frost protection on the master -> 5 < 10
    await hass.async_block_till_done()
    assert _modes(climate_calls) == ["off"]
    assert hass.states.get("sensor.bedroom_mode").state == "off"
    assert (
        hass.states.get("sensor.bedroom_target_temperature").attributes["reason"]
        == "below_threshold"
    )

    # Thermostat is now off; master goes back up -> switch on (heat) then set temp
    set_trv(hass, BEDROOM, state="off", temperature=18)
    _clear(climate_calls)
    set_master(hass, temperature=20)
    await hass.async_block_till_done()
    assert _modes(climate_calls) == ["heat"]
    assert _temps(climate_calls) == []  # TRV already reports 18


async def test_master_off_turns_rooms_off(
    hass: HomeAssistant, config_entry: MockConfigEntry, climate_calls
) -> None:
    set_master(hass, temperature=20)
    set_trv(hass, BEDROOM, temperature=18)
    await setup_integration(hass, config_entry)

    set_master(hass, state="off", temperature=20)
    await hass.async_block_till_done()
    assert _modes(climate_calls) == ["off"]
    assert hass.states.get("sensor.bedroom_target_temperature").attributes["reason"] == "master_off"


async def test_offset_entity_changes_target(
    hass: HomeAssistant, config_entry: MockConfigEntry, climate_calls
) -> None:
    set_master(hass, temperature=20)
    set_trv(hass, BEDROOM, temperature=18)
    await setup_integration(hass, config_entry)

    await hass.services.async_call(
        "number", "set_value", {"entity_id": "number.bedroom_offset", "value": 1.5}, blocking=True
    )
    await hass.async_block_till_done()
    assert _temps(climate_calls) == [21.5]
    assert hass.states.get("number.bedroom_offset").state == "1.5"

    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": "number.bedroom_cut_off_temperature", "value": 25},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert _modes(climate_calls) == ["off"]


async def test_follow_switch(
    hass: HomeAssistant, config_entry: MockConfigEntry, climate_calls
) -> None:
    set_master(hass, temperature=20)
    set_trv(hass, BEDROOM, temperature=18)
    await setup_integration(hass, config_entry)

    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": "switch.bedroom_follow_master"}, blocking=True
    )
    await hass.async_block_till_done()
    assert hass.states.get("sensor.bedroom_mode").state == "disabled"
    set_master(hass, temperature=25)
    await hass.async_block_till_done()
    assert _temps(climate_calls) == []

    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": "switch.bedroom_follow_master"}, blocking=True
    )
    await hass.async_block_till_done()
    assert _temps(climate_calls) == [23.0]


async def test_options_flow_disables_and_resyncs(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    climate_calls,
    freezer: FrozenDateTimeFactory,
) -> None:
    """The Configure dialog holds the global on/off and the re-sync interval."""
    freezer.move_to(_utc(0, 9, 0))
    set_master(hass, temperature=20)
    set_trv(hass, BEDROOM, temperature=18)
    await setup_integration(hass, config_entry)

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"enabled": False, "resync_minutes": 0}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert config_entry.options == {"enabled": False, "resync_minutes": 0}
    await _tick(hass, freezer, _utc(0, 9, 2))  # debounced reload
    assert hass.states.get("sensor.bedroom_mode").state == "disabled"
    assert (
        hass.states.get("sensor.bedroom_target_temperature").attributes["reason"]
        == "global_disabled"
    )
    set_master(hass, temperature=25)
    await hass.async_block_till_done()
    assert _temps(climate_calls) == []

    # Re-enable with a 30-minute re-sync
    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"enabled": True, "resync_minutes": 30}
    )
    await _tick(hass, freezer, _utc(0, 9, 4))
    assert _temps(climate_calls) == [23.0]
    assert hass.states.get("sensor.bedroom_mode").state == "follow"

    # Someone turns the TRV down; nothing changes on our side...
    set_trv(hass, BEDROOM, temperature=15)
    _clear(climate_calls)
    await _tick(hass, freezer, _utc(0, 9, 20))
    assert _temps(climate_calls) == []
    # ...until the re-sync interval has passed, then the target is re-applied
    await _tick(hass, freezer, _utc(0, 9, 35))
    assert _temps(climate_calls) == [23.0]


async def test_stored_enabled_flag_migrates_to_options(
    hass: HomeAssistant, config_entry: MockConfigEntry, climate_calls, hass_storage
) -> None:
    """Pre-0.3 installs kept the global switch in storage (and as an entity)."""
    hass_storage[f"{DOMAIN}.{config_entry.entry_id}"] = {
        "version": 1,
        "data": {"enabled": False, "master_schedule": {}, "rooms": {}},
    }
    ent_reg = er.async_get(hass)
    ent_reg.async_get_or_create(
        "switch", DOMAIN, f"{config_entry.entry_id}_enabled", config_entry=config_entry
    )
    set_master(hass, temperature=20)
    set_trv(hass, BEDROOM, temperature=18)
    await setup_integration(hass, config_entry)

    assert config_entry.options["enabled"] is False
    assert hass.states.get("sensor.bedroom_mode").state == "disabled"
    assert ent_reg.async_get_entity_id("switch", DOMAIN, f"{config_entry.entry_id}_enabled") is None
    assert "enabled" not in hass_storage[f"{DOMAIN}.{config_entry.entry_id}"]["data"]


async def test_room_thermostat_unavailable_then_back(
    hass: HomeAssistant, config_entry: MockConfigEntry, climate_calls
) -> None:
    set_master(hass, temperature=20)
    set_trv(hass, BEDROOM, temperature=18)
    await setup_integration(hass, config_entry)

    hass.states.async_set(BEDROOM, STATE_UNAVAILABLE)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.bedroom_mode").state == "unavailable"
    assert hass.states.get("sensor.bedroom_target_temperature").state == "unknown"

    # Comes back reporting a stale target -> re-pushed
    set_trv(hass, BEDROOM, temperature=12)
    await hass.async_block_till_done()
    assert _temps(climate_calls) == [18.0]
    assert hass.states.get("sensor.bedroom_mode").state == "follow"


async def test_master_unavailable(
    hass: HomeAssistant, config_entry: MockConfigEntry, climate_calls
) -> None:
    set_trv(hass, BEDROOM, temperature=18)
    await setup_integration(hass, config_entry)  # master never existed
    assert hass.states.get("sensor.bedroom_mode").state == "unavailable"
    assert (
        hass.states.get("sensor.bedroom_target_temperature").attributes["reason"]
        == "master_unavailable"
    )

    set_master(hass, temperature=19)
    await hass.async_block_till_done()
    assert _temps(climate_calls) == [17.0]


async def test_waits_for_home_assistant_start(
    hass: HomeAssistant, config_entry: MockConfigEntry, climate_calls
) -> None:
    hass.set_state(CoreState.starting)
    set_master(hass, temperature=20)
    set_trv(hass, BEDROOM, temperature=15)
    await setup_integration(hass, config_entry)
    assert _temps(climate_calls) == []

    hass.set_state(CoreState.running)
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done()
    assert _temps(climate_calls) == [18.0]


async def test_settings_persist_across_reload(
    hass: HomeAssistant, config_entry: MockConfigEntry, climate_calls, hass_storage
) -> None:
    set_master(hass, temperature=20)
    set_trv(hass, BEDROOM, temperature=18)
    await setup_integration(hass, config_entry)

    await hass.services.async_call(
        "number", "set_value", {"entity_id": "number.bedroom_offset", "value": -3}, blocking=True
    )
    await hass.config_entries.async_reload(config_entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get("number.bedroom_offset").state == "-3.0"
    stored = hass_storage[f"{DOMAIN}.{config_entry.entry_id}"]["data"]
    assert stored["rooms"]["bedroom_sub"]["offset"] == -3.0

    await hass.config_entries.async_remove(config_entry.entry_id)
    await hass.async_block_till_done()
    assert f"{DOMAIN}.{config_entry.entry_id}" not in hass_storage


async def test_sync_service_forces_push(
    hass: HomeAssistant, config_entry: MockConfigEntry, climate_calls
) -> None:
    set_master(hass, temperature=20)
    set_trv(hass, BEDROOM, temperature=18)
    await setup_integration(hass, config_entry)
    assert _temps(climate_calls) == []

    set_trv(hass, BEDROOM, temperature=22)  # someone twiddled the TRV
    await hass.async_block_till_done()
    assert _temps(climate_calls) == []  # we don't fight manual changes on our own

    await hass.services.async_call(DOMAIN, "sync", {}, blocking=True)
    assert _temps(climate_calls) == [18.0]

    _clear(climate_calls)
    await hass.services.async_call(
        DOMAIN, "sync", {"device_id": room_device_id(hass, "bedroom_sub")}, blocking=True
    )
    assert _temps(climate_calls) == [18.0]


def _utc(day: int, hour: int, minute: int) -> datetime:
    # Europe/London is on GMT in November, so local == UTC. 2026-11-02 is a Monday.
    return datetime(2026, 11, 2 + day, hour, minute, tzinfo=timezone.utc)


async def _tick(hass: HomeAssistant, freezer: FrozenDateTimeFactory, when: datetime) -> None:
    freezer.move_to(when)
    async_fire_time_changed(hass, when)
    await hass.async_block_till_done()


async def test_room_schedule(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    climate_calls,
    freezer: FrozenDateTimeFactory,
) -> None:
    freezer.move_to(_utc(0, 6, 0))  # Monday 06:00
    set_master(hass, temperature=20)
    set_trv(hass, BEDROOM, temperature=18)
    await setup_integration(hass, config_entry)
    device_id = room_device_id(hass, "bedroom_sub")

    await hass.services.async_call(
        DOMAIN,
        "set_schedule",
        {
            "device_id": device_id,
            "schedule": {
                "weekdays": [
                    {"time": "06:30", "mode": "follow", "offset": 0},
                    {"time": "08:30", "mode": "off"},
                    {"time": "17:00", "mode": "fixed", "temperature": 21},
                    {"time": "22:00", "mode": "follow"},
                ]
            },
        },
        blocking=True,
    )
    await hass.async_block_till_done()
    # 06:00 Monday: nothing has started yet this week -> last week's Friday 22:00 "follow" block
    target = hass.states.get("sensor.bedroom_target_temperature")
    assert target.state == "18.0"
    assert target.attributes["active_block"] == "follow"
    assert target.attributes["active_block_day"] == "fri"

    await _tick(hass, freezer, _utc(0, 6, 30))  # follow with offset 0 -> 20
    assert _temps(climate_calls) == [20.0]
    assert (
        hass.states.get("sensor.bedroom_target_temperature").attributes["active_block"]
        == "follow +0°"
    )

    await _tick(hass, freezer, _utc(0, 8, 30))  # off
    assert _modes(climate_calls) == ["off"]
    assert hass.states.get("sensor.bedroom_mode").state == "off"
    assert hass.states.get("sensor.bedroom_target_temperature").attributes["reason"] == "schedule"

    set_trv(hass, BEDROOM, state="off", temperature=20)
    _clear(climate_calls)
    await _tick(hass, freezer, _utc(0, 12, 0))  # still off, nothing new
    assert climate_calls["set_hvac_mode"] == [] and climate_calls["set_temperature"] == []

    await _tick(hass, freezer, _utc(0, 17, 0))  # fixed 21, regardless of master
    assert _modes(climate_calls) == ["heat"]
    assert _temps(climate_calls) == [21.0]
    assert hass.states.get("sensor.bedroom_mode").state == "fixed"
    set_master(hass, temperature=25)  # master changes don't matter in a fixed block
    await hass.async_block_till_done()
    assert _temps(climate_calls) == [21.0]

    _clear(climate_calls)
    await _tick(hass, freezer, _utc(0, 22, 0))  # back to following master (25 - 2)
    assert _temps(climate_calls) == [23.0]

    # get_schedule reports the schedule and the active block
    response = await hass.services.async_call(
        DOMAIN, "get_schedule", {"device_id": device_id}, blocking=True, return_response=True
    )
    assert response["active"] == {"day": "mon", "time": "22:00", "mode": "follow"}
    assert [b["time"] for b in response["schedule"]["wed"]] == ["06:30", "08:30", "17:00", "22:00"]

    _clear(climate_calls)
    await hass.services.async_call(
        DOMAIN, "clear_schedule", {"device_id": device_id}, blocking=True
    )
    response = await hass.services.async_call(
        DOMAIN, "get_schedule", {"device_id": device_id}, blocking=True, return_response=True
    )
    assert response == {"schedule": {}, "active": None}
    assert hass.states.get("sensor.bedroom_target_temperature").attributes["active_block"] is None


async def test_master_schedule(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    climate_calls,
    freezer: FrozenDateTimeFactory,
) -> None:
    freezer.move_to(_utc(0, 6, 0))  # Monday 06:00
    set_master(hass, state="auto", temperature=16)
    set_trv(hass, BEDROOM, temperature=14)
    await setup_integration(hass, config_entry)
    hub_device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, config_entry.entry_id)})
    assert hub_device is not None
    _clear(climate_calls)

    await hass.services.async_call(
        DOMAIN,
        "set_schedule",
        {
            "device_id": hub_device.id,
            "schedule": {
                "all": [
                    {"time": "06:30", "mode": "fixed", "temperature": 20},
                    {"time": "08:30", "mode": "fixed", "temperature": 16},
                    {"time": "22:00", "mode": "off"},
                ]
            },
        },
        blocking=True,
    )
    await hass.async_block_till_done()
    # 06:00: yesterday's 22:00 "off" block is in force -> master switched off
    assert _modes(climate_calls, MASTER) == ["off"]

    set_master(hass, state="off", temperature=16)
    _clear(climate_calls)
    await _tick(hass, freezer, _utc(0, 6, 30))
    # Master is put into manual heat (not auto) and set to 20
    assert _modes(climate_calls, MASTER) == ["heat"]
    assert _temps(climate_calls, MASTER) == [20.0]

    # Pretend the master integration applied it -> rooms follow (20 - 2 = 18)
    set_master(hass, state="heat", temperature=20)
    await hass.async_block_till_done()
    assert _temps(climate_calls, BEDROOM) == [18.0]

    # Invalid master schedule (follow is meaningless for the master)
    import pytest
    from homeassistant.exceptions import ServiceValidationError

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "set_schedule",
            {
                "device_id": hub_device.id,
                "schedule": {"mon": [{"time": "07:00", "mode": "follow"}]},
            },
            blocking=True,
        )
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, "get_schedule", {"device_id": "nope"}, blocking=True, return_response=True
        )


async def test_config_entities_exist_with_defaults(
    hass: HomeAssistant, config_entry: MockConfigEntry, climate_calls
) -> None:
    set_master(hass, temperature=20)
    set_trv(hass, BEDROOM, temperature=18)
    await setup_integration(hass, config_entry)

    assert hass.states.get("select.bedroom_when_master_is_off").state == "off"
    assert hass.states.get("select.bedroom_off_action").state == "off"
    assert hass.states.get("number.bedroom_frost_protection_temperature").state == "7.5"


async def test_master_off_hold_keeps_last_target(
    hass: HomeAssistant, config_entry: MockConfigEntry, climate_calls
) -> None:
    set_master(hass, temperature=20)
    set_trv(hass, BEDROOM, temperature=18)
    await setup_integration(hass, config_entry)

    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": "select.bedroom_when_master_is_off", "option": "hold"},
        blocking=True,
    )
    await hass.async_block_till_done()

    set_master(hass, state="off", temperature=20)
    await hass.async_block_till_done()
    # Nothing sent: the TRV keeps whatever it had
    assert climate_calls["set_hvac_mode"] == [] and climate_calls["set_temperature"] == []
    assert hass.states.get("sensor.bedroom_mode").state == "hold"
    target = hass.states.get("sensor.bedroom_target_temperature")
    assert target.state == "18.0"
    assert target.attributes["reason"] == "master_off"

    # Master back on at a new target -> following resumes
    set_master(hass, state="heat", temperature=21)
    await hass.async_block_till_done()
    assert _temps(climate_calls) == [19.0]
    assert hass.states.get("sensor.bedroom_mode").state == "follow"

    # Switch back to "off" while the master is off -> room is turned off
    set_master(hass, state="off", temperature=21)
    await hass.async_block_till_done()
    assert _modes(climate_calls) == []
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": "select.bedroom_when_master_is_off", "option": "off"},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert _modes(climate_calls) == ["off"]


async def test_frost_protection_instead_of_off(
    hass: HomeAssistant, config_entry: MockConfigEntry, climate_calls
) -> None:
    set_master(hass, temperature=20)
    set_trv(hass, BEDROOM, temperature=18)
    await setup_integration(hass, config_entry)

    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": "select.bedroom_off_action", "option": "frost"},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert climate_calls["set_temperature"] == []  # still following at 18

    # Below the cut-off -> heat at 7.5 instead of switching off
    set_master(hass, temperature=7)
    await hass.async_block_till_done()
    assert _modes(climate_calls) == []
    assert _temps(climate_calls) == [7.5]
    assert hass.states.get("sensor.bedroom_mode").state == "frost"
    target = hass.states.get("sensor.bedroom_target_temperature")
    assert target.state == "7.5"
    assert target.attributes["reason"] == "below_threshold"

    # Frost temperature is adjustable, rounded to the TRV's step
    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": "number.bedroom_frost_protection_temperature", "value": 8},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert _temps(climate_calls) == [7.5, 8.0]

    # Next heating cycle: master back up -> normal target again, no mode change needed
    _clear(climate_calls)
    set_trv(hass, BEDROOM, temperature=8)
    set_master(hass, temperature=20)
    await hass.async_block_till_done()
    assert _modes(climate_calls) == []
    assert _temps(climate_calls) == [18.0]
    assert hass.states.get("sensor.bedroom_mode").state == "follow"

    # Frost also applies when the master is off (with the default "off" choice)
    _clear(climate_calls)
    set_trv(hass, BEDROOM, temperature=18)  # the TRV applied the 18 we sent
    set_master(hass, state="off", temperature=20)
    await hass.async_block_till_done()
    assert _temps(climate_calls) == [8.0]
    assert hass.states.get("sensor.bedroom_mode").state == "frost"
    assert hass.states.get("sensor.bedroom_target_temperature").attributes["reason"] == "master_off"


async def test_frost_protection_switches_a_cold_trv_back_on(
    hass: HomeAssistant, config_entry: MockConfigEntry, climate_calls
) -> None:
    """A TRV that is currently off gets switched to heat for the frost target."""
    set_master(hass, temperature=7)
    set_trv(hass, BEDROOM, state="off", temperature=18)
    await setup_integration(hass, config_entry)
    assert _modes(climate_calls) == []  # already off, cut-off applies

    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": "select.bedroom_off_action", "option": "frost"},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert _modes(climate_calls) == ["heat"]
    assert _temps(climate_calls) == [7.5]


async def test_schedule_off_block_uses_frost(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    climate_calls,
    freezer: FrozenDateTimeFactory,
) -> None:
    freezer.move_to(_utc(0, 6, 0))
    set_master(hass, temperature=20)
    set_trv(hass, BEDROOM, temperature=18)
    await setup_integration(hass, config_entry)
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": "select.bedroom_off_action", "option": "frost"},
        blocking=True,
    )
    await hass.services.async_call(
        DOMAIN,
        "set_schedule",
        {
            "device_id": room_device_id(hass, "bedroom_sub"),
            "schedule": {
                "all": [{"time": "08:00", "mode": "off"}, {"time": "17:00", "mode": "follow"}]
            },
        },
        blocking=True,
    )
    await hass.async_block_till_done()
    assert _temps(climate_calls) == []  # 06:00 is inside yesterday's 17:00 follow block

    await _tick(hass, freezer, _utc(0, 8, 0))
    assert _temps(climate_calls) == [7.5]
    assert hass.states.get("sensor.bedroom_mode").state == "frost"
    assert hass.states.get("sensor.bedroom_target_temperature").attributes["reason"] == "schedule"

    set_trv(hass, BEDROOM, temperature=7.5)  # the TRV applied it
    await _tick(hass, freezer, _utc(0, 17, 0))
    assert _temps(climate_calls) == [7.5, 18.0]


async def test_schedule_sensors_and_entity_id_targets(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    climate_calls,
    freezer: FrozenDateTimeFactory,
) -> None:
    """The card reads schedules from sensors and targets actions by entity_id."""
    freezer.move_to(_utc(0, 9, 0))  # Monday 09:00
    set_master(hass, temperature=20)
    set_trv(hass, BEDROOM, temperature=18)
    await setup_integration(hass, config_entry)

    room = hass.states.get("sensor.bedroom_schedule")
    assert room.state == "No schedule"
    assert room.attributes["integration"] == "cozy_home"
    assert room.attributes["kind"] == "room"
    assert room.attributes["modes"] == ["follow", "fixed", "off"]
    assert room.attributes["schedule"] == {}
    hub = hass.states.get("sensor.heating_schedule")
    assert hub.attributes["kind"] == "master"
    assert hub.attributes["modes"] == ["fixed", "off"]

    await hass.services.async_call(
        DOMAIN,
        "set_schedule",
        {
            "entity_id": "sensor.bedroom_schedule",
            "schedule": {"mon": [{"time": "08:00", "mode": "fixed", "temperature": 19}]},
        },
        blocking=True,
    )
    await hass.async_block_till_done()
    room = hass.states.get("sensor.bedroom_schedule")
    assert room.state == "Fixed 19° until Mon 08:00"  # only block in the week
    assert room.attributes["active_day"] == "mon"
    assert room.attributes["active_time"] == "08:00"
    assert room.attributes["schedule"]["mon"][0]["temperature"] == 19.0
    assert _temps(climate_calls) == [19.0]

    await hass.services.async_call(
        DOMAIN, "clear_schedule", {"entity_id": "sensor.bedroom_schedule"}, blocking=True
    )
    await hass.async_block_till_done()
    assert hass.states.get("sensor.bedroom_schedule").state == "No schedule"

    import pytest
    from homeassistant.exceptions import ServiceValidationError

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, "clear_schedule", {"entity_id": "sensor.not_ours"}, blocking=True
        )


async def test_card_is_served(
    hass: HomeAssistant, config_entry: MockConfigEntry, hass_client, climate_calls
) -> None:
    """The bundled dashboard card is served from the integration's static path."""
    set_master(hass)
    set_trv(hass, BEDROOM)
    await setup_integration(hass, config_entry)
    client = await hass_client()
    for name in ("cozy-home-schedule-card", "cozy-home-rooms-card"):
        resp = await client.get(f"/cozy_home/{name}.js")
        assert resp.status == 200
        assert name in await resp.text()
    hub = hass.states.get("sensor.heating_schedule")
    assert hub.attributes["master_entity"] == MASTER
    assert hub.attributes["enabled"] is True


async def test_room_with_two_radiators(hass: HomeAssistant, climate_calls) -> None:
    """Several thermostats in one room share its offset; each is clamped to its own range."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry as _Entry

    from custom_components.cozy_home.const import (
        CONF_CLIMATE_ENTITIES,
        CONF_MASTER_ENTITY,
        CONF_NAME,
        CONF_OFF_BELOW,
        CONF_OFFSET,
        CONF_ROOM_NAME,
        SUBENTRY_TYPE_ROOM,
    )

    entry = _Entry(
        domain=DOMAIN,
        title="Heating",
        unique_id=MASTER,
        data={CONF_MASTER_ENTITY: MASTER, CONF_NAME: "Heating"},
        subentries_data=[
            {
                "data": {
                    CONF_ROOM_NAME: "Landing",
                    CONF_CLIMATE_ENTITIES: ["climate.up_left", "climate.up_right"],
                    CONF_OFFSET: 1.0,
                    CONF_OFF_BELOW: 10.0,
                },
                "subentry_type": SUBENTRY_TYPE_ROOM,
                "title": "Landing",
                "unique_id": None,
                "subentry_id": "landing_sub",
            }
        ],
    )
    entry.add_to_hass(hass)
    set_master(hass, temperature=20.3)
    set_trv(hass, "climate.up_left", temperature=15)  # step 0.5, max 30
    set_trv(hass, "climate.up_right", temperature=15, target_temp_step=1, max_temp=21)
    await setup_integration(hass, entry)

    # 21.3 -> 21.5 on the left (0.5 step), 21 on the right (1° step, clamped to max 21)
    assert _temps(climate_calls, "climate.up_left") == [21.5]
    assert _temps(climate_calls, "climate.up_right") == [21.0]
    target = hass.states.get("sensor.landing_target_temperature")
    assert target.state == "21.5"
    assert target.attributes["thermostats"] == ["climate.up_left", "climate.up_right"]
    assert target.attributes["applied"] == {"climate.up_left": 21.5, "climate.up_right": 21.0}

    # One radiator going offline doesn't stop the other being controlled
    _clear(climate_calls)
    hass.states.async_set("climate.up_left", STATE_UNAVAILABLE)
    await hass.async_block_till_done()
    set_master(hass, temperature=18)
    await hass.async_block_till_done()
    assert _temps(climate_calls, "climate.up_right") == [19.0]
    assert _temps(climate_calls, "climate.up_left") == []
    assert hass.states.get("sensor.landing_mode").state == "follow"
    assert (
        hass.states.get("sensor.landing_target_temperature").attributes["applied"][
            "climate.up_left"
        ]
        == "unavailable"
    )

    # Back online -> it is brought up to date (forced re-sync of the room)
    set_trv(hass, "climate.up_left", temperature=15)
    await hass.async_block_till_done()
    assert _temps(climate_calls, "climate.up_left") == [19.0]

    # Cut-off switches both off
    _clear(climate_calls)
    set_master(hass, temperature=7)
    await hass.async_block_till_done()
    assert _modes(climate_calls, "climate.up_left") == ["off"]
    assert _modes(climate_calls, "climate.up_right") == ["off"]


async def test_old_single_thermostat_room_still_loads(hass: HomeAssistant, climate_calls) -> None:
    """Rooms created before 0.4 stored a single climate_entity."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry as _Entry

    from custom_components.cozy_home.const import (
        CONF_CLIMATE_ENTITY,
        CONF_MASTER_ENTITY,
        CONF_NAME,
        CONF_OFF_BELOW,
        CONF_OFFSET,
        CONF_ROOM_NAME,
        SUBENTRY_TYPE_ROOM,
    )

    entry = _Entry(
        domain=DOMAIN,
        title="Heating",
        unique_id=MASTER,
        data={CONF_MASTER_ENTITY: MASTER, CONF_NAME: "Heating"},
        subentries_data=[
            {
                "data": {
                    CONF_ROOM_NAME: "Bedroom",
                    CONF_CLIMATE_ENTITY: BEDROOM,
                    CONF_OFFSET: -2.0,
                    CONF_OFF_BELOW: 10.0,
                },
                "subentry_type": SUBENTRY_TYPE_ROOM,
                "title": "Bedroom",
                "unique_id": BEDROOM,
                "subentry_id": "bedroom_sub",
            }
        ],
    )
    entry.add_to_hass(hass)
    set_master(hass, temperature=20)
    set_trv(hass, BEDROOM, temperature=15)
    await setup_integration(hass, entry)
    assert _temps(climate_calls) == [18.0]
    assert entry.runtime_data.room("bedroom_sub").climate_entities == [BEDROOM]


async def test_legacy_single_thermostat_rooms_merge_by_area(
    hass: HomeAssistant, climate_calls
) -> None:
    """Rooms from 0.3 and earlier (one thermostat each) are grouped by HA area on upgrade."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry as _Entry

    from custom_components.cozy_home.const import (
        CONF_CLIMATE_ENTITIES,
        CONF_CLIMATE_ENTITY,
        CONF_MASTER_ENTITY,
        CONF_NAME,
        CONF_OFF_BELOW,
        CONF_OFFSET,
        CONF_ROOM_NAME,
        SUBENTRY_TYPE_ROOM,
    )

    ent_reg = er.async_get(hass)
    area_reg = ar.async_get(hass)
    upstairs = area_reg.async_get_or_create("Upstairs")
    for object_id in ("up_dining", "up_middle", "up_garage"):
        reg = ent_reg.async_get_or_create(
            "climate", "test", object_id, suggested_object_id=object_id
        )
        ent_reg.async_update_entity(reg.entity_id, area_id=upstairs.id)
    for e in ("climate.up_dining", "climate.up_middle", "climate.up_garage", "climate.hall"):
        set_trv(hass, e, temperature=15)
    set_master(hass, temperature=20)

    def legacy_room(sid: str, title: str, climate: str, offset: float) -> dict:
        return {
            "data": {
                CONF_ROOM_NAME: title,
                CONF_CLIMATE_ENTITY: climate,
                CONF_OFFSET: offset,
                CONF_OFF_BELOW: 10.0,
            },
            "subentry_type": SUBENTRY_TYPE_ROOM,
            "title": title,
            "unique_id": climate,
            "subentry_id": sid,
        }

    entry = _Entry(
        domain=DOMAIN,
        title="Heating",
        unique_id=MASTER,
        data={CONF_MASTER_ENTITY: MASTER, CONF_NAME: "Heating"},
        subentries_data=[
            legacy_room("s_middle", "Upstairs Middle", "climate.up_middle", -1.0),
            legacy_room("s_up", "Upstairs", "climate.up_dining", 1.0),
            legacy_room("s_garage", "Upstairs Garage", "climate.up_garage", 0.0),
            legacy_room("s_hall", "Hall", "climate.hall", -2.0),  # no area: untouched
        ],
    )
    entry.add_to_hass(hass)
    # The room devices already exist from the old version
    dev_reg = dr.async_get(hass)
    for sid in ("s_middle", "s_up", "s_garage", "s_hall"):
        dev_reg.async_get_or_create(
            config_entry_id=entry.entry_id,
            config_subentry_id=sid,
            identifiers={(DOMAIN, sid)},
        )
    await setup_integration(hass, entry)

    # Three "Upstairs" rooms became one, keeping the room named after the area
    assert set(entry.subentries) == {"s_up", "s_hall"}
    merged = entry.subentries["s_up"]
    assert merged.title == "Upstairs"
    assert merged.data[CONF_CLIMATE_ENTITIES] == [
        "climate.up_dining",
        "climate.up_middle",
        "climate.up_garage",
    ]
    assert CONF_CLIMATE_ENTITY not in merged.data
    assert merged.unique_id is None
    assert entry.subentries["s_hall"].data[CONF_CLIMATE_ENTITIES] == ["climate.hall"]

    # All three radiators follow with the surviving room's offset (+1 -> 21)
    for e in ("climate.up_dining", "climate.up_middle", "climate.up_garage"):
        assert _temps(climate_calls, e) == [21.0]
    assert _temps(climate_calls, "climate.hall") == [18.0]

    # The merged room's device now sits in the Upstairs area; the others are gone
    assert dev_reg.async_get_device(identifiers={(DOMAIN, "s_up")}).area_id == upstairs.id
    assert dev_reg.async_get_device(identifiers={(DOMAIN, "s_middle")}) is None
    assert hass.states.get("sensor.upstairs_target_temperature").attributes["thermostats"] == [
        "climate.up_dining",
        "climate.up_middle",
        "climate.up_garage",
    ]


async def test_new_room_device_is_suggested_into_its_area(
    hass: HomeAssistant, climate_calls
) -> None:
    from pytest_homeassistant_custom_component.common import MockConfigEntry as _Entry

    from custom_components.cozy_home.const import (
        CONF_CLIMATE_ENTITIES,
        CONF_MASTER_ENTITY,
        CONF_NAME,
        CONF_OFF_BELOW,
        CONF_OFFSET,
        CONF_ROOM_NAME,
        SUBENTRY_TYPE_ROOM,
    )

    ent_reg = er.async_get(hass)
    study = ar.async_get(hass).async_get_or_create("Study")
    reg = ent_reg.async_get_or_create("climate", "test", "office", suggested_object_id="office_trv")
    ent_reg.async_update_entity(reg.entity_id, area_id=study.id)
    set_trv(hass, OFFICE)
    set_master(hass)
    entry = _Entry(
        domain=DOMAIN,
        title="Heating",
        unique_id=MASTER,
        data={CONF_MASTER_ENTITY: MASTER, CONF_NAME: "Heating"},
        subentries_data=[
            {
                "data": {
                    CONF_ROOM_NAME: "Study",
                    CONF_CLIMATE_ENTITIES: [OFFICE],
                    CONF_OFFSET: 0.0,
                    CONF_OFF_BELOW: 10.0,
                },
                "subentry_type": SUBENTRY_TYPE_ROOM,
                "title": "Study",
                "unique_id": None,
                "subentry_id": "study_sub",
            }
        ],
    )
    entry.add_to_hass(hass)
    await setup_integration(hass, entry)
    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, "study_sub")})
    assert device is not None and device.area_id == study.id
