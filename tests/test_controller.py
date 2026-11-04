"""End-to-end behaviour: master → rooms, offsets, cut-off, schedules, services."""

from __future__ import annotations

from datetime import datetime, timezone

from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED, STATE_UNAVAILABLE
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.cozy_home.const import DOMAIN

from .conftest import BEDROOM, MASTER, room_device_id, set_master, set_trv, setup_integration


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
    assert hass.states.get("number.bedroom_turn_off_below").state == "10.0"
    assert hass.states.get("switch.bedroom_follow_master").state == "on"
    assert hass.states.get("switch.heating_enabled").state == "on"


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
        {"entity_id": "number.bedroom_turn_off_below", "value": 25},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert _modes(climate_calls) == ["off"]


async def test_follow_switch_and_hub_switch(
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

    _clear(climate_calls)
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": "switch.heating_enabled"}, blocking=True
    )
    await hass.async_block_till_done()
    assert hass.states.get("sensor.bedroom_mode").state == "disabled"
    assert (
        hass.states.get("sensor.bedroom_target_temperature").attributes["reason"]
        == "global_disabled"
    )
    set_master(hass, temperature=20)
    await hass.async_block_till_done()
    assert _temps(climate_calls) == []


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
    assert hass.states.get("select.bedroom_off_mode").state == "off"
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
        {"entity_id": "select.bedroom_off_mode", "option": "frost"},
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
        {"entity_id": "select.bedroom_off_mode", "option": "frost"},
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
        {"entity_id": "select.bedroom_off_mode", "option": "frost"},
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
    assert room.state == "none"
    assert room.attributes["cozy_home"] is True
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
    assert room.state == "fixed 19°"
    assert room.attributes["active_day"] == "mon"
    assert room.attributes["active_time"] == "08:00"
    assert room.attributes["schedule"]["mon"][0]["temperature"] == 19.0
    assert _temps(climate_calls) == [19.0]

    await hass.services.async_call(
        DOMAIN, "clear_schedule", {"entity_id": "sensor.bedroom_schedule"}, blocking=True
    )
    await hass.async_block_till_done()
    assert hass.states.get("sensor.bedroom_schedule").state == "none"

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
    resp = await client.get("/cozy_home/cozy-home-schedule-card.js")
    assert resp.status == 200
    body = await resp.text()
    assert "cozy-home-schedule-card" in body
