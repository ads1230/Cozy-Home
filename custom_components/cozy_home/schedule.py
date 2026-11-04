"""Weekly schedule model (Hive-style: per-day list of time blocks).

A schedule is a dict keyed by day (``mon`` .. ``sun``). Each day holds a list of
blocks, each ``{"time": "HH:MM", "mode": "follow"|"fixed"|"off", ...}``. A block
stays in effect until the next block starts, wrapping across days and across
the week, exactly like a thermostat's built-in schedule. If a day has no block
at 00:00, the last block of the previous day carries over.

Shortcut keys ``weekdays``, ``weekend`` and ``all`` are expanded on input;
an explicit day key always wins over a shortcut.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

import voluptuous as vol

from .const import (
    DAYS,
    MASTER_SCHEDULE_MODES,
    MODE_FIXED,
    MODE_FOLLOW,
    MODE_OFF,
    OFFSET_MAX,
    OFFSET_MIN,
    SCHEDULE_MODES,
)

TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")

SHORTCUTS: dict[str, tuple[str, ...]] = {
    "weekdays": ("mon", "tue", "wed", "thu", "fri"),
    "weekend": ("sat", "sun"),
    "all": DAYS,
}

Block = dict[str, Any]
Schedule = dict[str, list[Block]]


def _validate_time(value: Any) -> str:
    if not isinstance(value, str) or not TIME_RE.match(value):
        raise vol.Invalid(f"time must be HH:MM (24h), got {value!r}")
    return value


def _block_schema(modes: tuple[str, ...]) -> vol.Schema:
    def _check(block: Block) -> Block:
        mode = block["mode"]
        if mode == MODE_FIXED and "temperature" not in block:
            raise vol.Invalid("a 'fixed' block needs a 'temperature'")
        if mode != MODE_FIXED:
            block.pop("temperature", None)
        if mode != MODE_FOLLOW:
            block.pop("offset", None)
        return block

    return vol.All(
        vol.Schema(
            {
                vol.Required("time"): _validate_time,
                vol.Required("mode"): vol.In(modes),
                vol.Optional("offset"): vol.All(
                    vol.Coerce(float), vol.Range(min=OFFSET_MIN, max=OFFSET_MAX)
                ),
                vol.Optional("temperature"): vol.All(vol.Coerce(float), vol.Range(min=0, max=40)),
            }
        ),
        _check,
    )


def _schedule_schema(modes: tuple[str, ...]) -> vol.Schema:
    block = _block_schema(modes)
    keys = {vol.Optional(day): [block] for day in (*DAYS, *SHORTCUTS)}
    return vol.Schema(keys)


ROOM_SCHEDULE_SCHEMA = _schedule_schema(SCHEDULE_MODES)
MASTER_SCHEDULE_SCHEMA = _schedule_schema(MASTER_SCHEDULE_MODES)


def normalize(schedule: dict[str, Any] | None) -> Schedule:
    """Expand shortcuts, drop unknown days, sort blocks by time."""
    if not schedule:
        return {}
    result: Schedule = {}
    for key, days in SHORTCUTS.items():
        if key in schedule:
            for day in days:
                result[day] = [dict(b) for b in schedule[key]]
    for day in DAYS:
        if day in schedule:
            result[day] = [dict(b) for b in schedule[day]]
    for day, blocks in list(result.items()):
        # Later entries with the same time win; keep the list sorted.
        by_time = {b["time"]: b for b in blocks}
        result[day] = [by_time[t] for t in sorted(by_time)]
        if not result[day]:
            del result[day]
    return result


def validate(schedule: dict[str, Any] | None, *, master: bool = False) -> Schedule:
    """Validate and normalize a schedule; raises ``vol.Invalid`` if bad."""
    schema = MASTER_SCHEDULE_SCHEMA if master else ROOM_SCHEDULE_SCHEMA
    return normalize(schema(schedule or {}))


def active_block(schedule: Schedule, now: datetime) -> tuple[str, Block] | None:
    """Return ``(day, block)`` in effect at ``now`` (a local-time datetime).

    Looks at today's blocks that have already started, then walks back through
    previous days (and finally the whole of today again, i.e. last week's) so a
    block carries over until the next one starts anywhere in the week.
    """
    if not schedule:
        return None
    weekday = now.weekday()  # Monday == 0
    hhmm = now.strftime("%H:%M")
    for back in range(8):
        day = DAYS[(weekday - back) % 7]
        blocks = schedule.get(day)
        if not blocks:
            continue
        candidates = [b for b in blocks if b["time"] <= hhmm] if back == 0 else blocks
        if candidates:
            return day, candidates[-1]
    return None


def next_block(schedule: Schedule, now: datetime) -> tuple[str, Block, int] | None:
    """Return ``(day, block, days_ahead)`` for the next block to start after ``now``."""
    if not schedule:
        return None
    weekday = now.weekday()
    hhmm = now.strftime("%H:%M")
    for ahead in range(8):
        day = DAYS[(weekday + ahead) % 7]
        blocks = schedule.get(day)
        if not blocks:
            continue
        candidates = [b for b in blocks if b["time"] > hhmm] if ahead == 0 else blocks
        if candidates:
            return day, candidates[0], ahead
    return None


DAY_NAMES = {
    "mon": "Mon",
    "tue": "Tue",
    "wed": "Wed",
    "thu": "Thu",
    "fri": "Fri",
    "sat": "Sat",
    "sun": "Sun",
}


def summarize(schedule: Schedule, now: datetime, *, none_text: str) -> str:
    """Human text for a sensor state: "Master −2° until 17:00", "Off until Sat 07:30"."""
    found = active_block(schedule, now)
    if found is None:
        return none_text
    label = describe_block(found[1]) or ""
    label = (
        ("Master" + label[len("follow") :]) if label.startswith("follow") else label.capitalize()
    )
    nxt = next_block(schedule, now)
    if nxt is None:
        return label
    day, block, ahead = nxt
    until = block["time"] if ahead == 0 else f"{DAY_NAMES[day]} {block['time']}"
    return f"{label} until {until}"


def describe_block(block: Block | None) -> str | None:
    """Short human-readable label for a block (used in sensor attributes)."""
    if block is None:
        return None
    mode = block["mode"]
    if mode == MODE_OFF:
        return "off"
    if mode == MODE_FIXED:
        return f"fixed {block['temperature']:g}°"
    if block.get("offset") is not None:
        return f"follow {block['offset']:+g}°"
    return "follow"
