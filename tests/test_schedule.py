"""Pure tests for the schedule model (no Home Assistant needed)."""

from __future__ import annotations

from datetime import datetime

import pytest
import voluptuous as vol

from custom_components.cozy_home.schedule import (
    active_block,
    describe_block,
    next_block,
    normalize,
    summarize,
    validate,
)

SCHED = validate(
    {
        "weekdays": [
            {"time": "06:30", "mode": "follow"},
            {"time": "08:30", "mode": "follow", "offset": -3},
            {"time": "17:00", "mode": "follow"},
            {"time": "22:00", "mode": "off"},
        ],
        "weekend": [
            {"time": "07:30", "mode": "follow"},
            {"time": "22:30", "mode": "fixed", "temperature": 17},
        ],
        "fri": [  # explicit day overrides the shortcut
            {"time": "07:00", "mode": "follow"},
            {"time": "23:00", "mode": "off"},
        ],
    }
)


def _dt(day: int, hour: int, minute: int = 0) -> datetime:
    # 2026-11-02 is a Monday
    return datetime(2026, 11, 2 + day, hour, minute)


def test_shortcuts_expand_and_explicit_day_wins() -> None:
    assert set(SCHED) == {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}
    assert [b["time"] for b in SCHED["mon"]] == ["06:30", "08:30", "17:00", "22:00"]
    assert [b["time"] for b in SCHED["fri"]] == ["07:00", "23:00"]
    assert SCHED["sat"][1] == {"time": "22:30", "mode": "fixed", "temperature": 17.0}


def test_active_block_within_day() -> None:
    day, block = active_block(SCHED, _dt(0, 9))  # Monday 09:00
    assert (day, block["time"], block["offset"]) == ("mon", "08:30", -3.0)
    day, block = active_block(SCHED, _dt(0, 17))  # exact boundary starts the block
    assert (day, block["time"]) == ("mon", "17:00")


def test_active_block_carries_over_from_previous_day() -> None:
    # Monday 03:00 -> Sunday's last block (fixed 17 from 22:30) is still in force
    day, block = active_block(SCHED, _dt(0, 3))
    assert (day, block["mode"], block["temperature"]) == ("sun", "fixed", 17.0)
    # Saturday 05:00 -> Friday's 23:00 "off" block carries over
    day, block = active_block(SCHED, _dt(5, 5))
    assert (day, block["time"], block["mode"]) == ("fri", "23:00", "off")


def test_active_block_wraps_whole_week() -> None:
    only_wed = validate({"wed": [{"time": "18:00", "mode": "fixed", "temperature": 19}]})
    # Wednesday 10:00: nothing earlier this week -> last week's Wednesday block
    day, block = active_block(only_wed, _dt(2, 10))
    assert (day, block["time"]) == ("wed", "18:00")
    assert active_block({}, _dt(2, 10)) is None


def test_normalize_sorts_and_dedupes() -> None:
    sched = normalize(
        {
            "mon": [
                {"time": "10:00", "mode": "off"},
                {"time": "08:00", "mode": "follow"},
                {"time": "10:00", "mode": "follow"},
            ]
        }
    )
    assert [b["time"] for b in sched["mon"]] == ["08:00", "10:00"]
    assert sched["mon"][1]["mode"] == "follow"  # later duplicate wins
    assert normalize({"mon": []}) == {}


@pytest.mark.parametrize(
    "bad",
    [
        {"mon": [{"time": "25:00", "mode": "off"}]},
        {"mon": [{"time": "8:00", "mode": "off"}]},
        {"mon": [{"time": "08:00", "mode": "fixed"}]},  # fixed without temperature
        {"mon": [{"time": "08:00", "mode": "boost"}]},
        {"monday": [{"time": "08:00", "mode": "off"}]},
        {"mon": [{"time": "08:00", "mode": "follow", "offset": 50}]},
    ],
)
def test_validate_rejects(bad: dict) -> None:
    with pytest.raises(vol.Invalid):
        validate(bad)


def test_master_schedule_rejects_follow() -> None:
    with pytest.raises(vol.Invalid):
        validate({"mon": [{"time": "08:00", "mode": "follow"}]}, master=True)
    assert validate({"mon": [{"time": "08:00", "mode": "fixed", "temperature": 20}]}, master=True)


def test_validate_strips_irrelevant_fields() -> None:
    sched = validate({"mon": [{"time": "08:00", "mode": "off", "temperature": 20, "offset": 1}]})
    assert sched["mon"][0] == {"time": "08:00", "mode": "off"}


def test_describe_block() -> None:
    assert describe_block(None) is None
    assert describe_block({"time": "08:00", "mode": "off"}) == "off"
    assert describe_block({"time": "08:00", "mode": "fixed", "temperature": 17.5}) == "fixed 17.5°"
    assert describe_block({"time": "08:00", "mode": "follow", "offset": -2.0}) == "follow -2°"
    assert describe_block({"time": "08:00", "mode": "follow"}) == "follow"


def test_next_block_and_summary() -> None:
    # Monday 09:00: in the 08:30 "follow −3" block, next is 17:00 today
    assert next_block(SCHED, _dt(0, 9))[1]["time"] == "17:00"
    assert summarize(SCHED, _dt(0, 9), none_text="No schedule") == "Master -3° until 17:00"
    # Sunday 23:00: fixed 17 until Monday 06:30
    assert summarize(SCHED, _dt(6, 23), none_text="No schedule") == "Fixed 17° until Mon 06:30"
    assert summarize({}, _dt(0, 9), none_text="No schedule") == "No schedule"
    only = validate({"wed": [{"time": "18:00", "mode": "off"}]})
    assert summarize(only, _dt(2, 10), none_text="x") == "Off until 18:00"
    assert summarize(only, _dt(2, 19), none_text="x") == "Off until Wed 18:00"
