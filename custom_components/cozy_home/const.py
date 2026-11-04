"""Constants for the Cozy Home integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "cozy_home"

# Config entry (master) keys
CONF_MASTER_ENTITY: Final = "master_entity"
CONF_NAME: Final = "name"

# Room subentry keys
SUBENTRY_TYPE_ROOM: Final = "room"
CONF_ROOM_NAME: Final = "name"
CONF_CLIMATE_ENTITIES: Final = "climate_entities"  # a room can have several thermostats
CONF_CLIMATE_ENTITY: Final = "climate_entity"  # pre-0.4 single-thermostat rooms
CONF_OFFSET: Final = "offset"
CONF_OFF_BELOW: Final = "off_below"
CONF_MASTER_OFF: Final = "master_off"

CONF_OFF_MODE: Final = "off_mode"
CONF_FROST_TEMPERATURE: Final = "frost_temperature"
CONF_ROOMS: Final = "rooms"  # multi-select of thermostats in the add-rooms forms

# Integration settings (config entry options, edited via "Configure")
CONF_ENABLED: Final = "enabled"
CONF_RESYNC_MINUTES: Final = "resync_minutes"
DEFAULT_RESYNC_MINUTES: Final = 0  # 0 = only push when something changes

# What a room does while the master thermostat is switched off
MASTER_OFF_TURN_OFF: Final = "off"  # switch the room thermostat off too
MASTER_OFF_HOLD: Final = "hold"  # leave the room thermostat as it is
MASTER_OFF_OPTIONS: Final = (MASTER_OFF_TURN_OFF, MASTER_OFF_HOLD)

# How "off" is realised on the room thermostat
OFF_MODE_OFF: Final = "off"  # hvac mode off
OFF_MODE_FROST: Final = "frost"  # keep heating, at the frost-protection temperature
OFF_MODE_OPTIONS: Final = (OFF_MODE_OFF, OFF_MODE_FROST)

DEFAULT_NAME: Final = "Cozy Home"
DEFAULT_OFFSET: Final = 0.0
DEFAULT_OFF_BELOW: Final = 10.0
DEFAULT_MASTER_OFF: Final = MASTER_OFF_TURN_OFF
DEFAULT_OFF_MODE: Final = (
    OFF_MODE_FROST  # new rooms hold frost protection rather than switching off
)
DEFAULT_FROST_TEMPERATURE: Final = 7.5

FROST_MIN: Final = 5.0
FROST_MAX: Final = 15.0
FROST_STEP: Final = 0.5

OFFSET_MIN: Final = -10.0
OFFSET_MAX: Final = 10.0
OFFSET_STEP: Final = 0.5
OFF_BELOW_MIN: Final = 0.0
OFF_BELOW_MAX: Final = 30.0
OFF_BELOW_STEP: Final = 0.5

# Schedule
DAYS: Final[tuple[str, ...]] = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
MODE_FOLLOW: Final = "follow"
MODE_FIXED: Final = "fixed"
MODE_OFF: Final = "off"
SCHEDULE_MODES: Final = (MODE_FOLLOW, MODE_FIXED, MODE_OFF)
MASTER_SCHEDULE_MODES: Final = (MODE_FIXED, MODE_OFF)

ATTR_DEVICE_ID: Final = "device_id"
ATTR_SCHEDULE: Final = "schedule"

SERVICE_SET_SCHEDULE: Final = "set_schedule"
SERVICE_GET_SCHEDULE: Final = "get_schedule"
SERVICE_CLEAR_SCHEDULE: Final = "clear_schedule"
SERVICE_SYNC: Final = "sync"
SERVICE_BOOST: Final = "boost"
SERVICE_CANCEL_BOOST: Final = "cancel_boost"
ATTR_TEMPERATURE_FIELD: Final = "temperature"
ATTR_MINUTES: Final = "minutes"
DEFAULT_BOOST_MINUTES: Final = 60
DEFAULT_BOOST_DELTA: Final = 2.0  # boost = current target + this, unless a temperature is given

# Room status values (sensor.<room>_mode)
STATUS_FOLLOW: Final = "follow"
STATUS_FIXED: Final = "fixed"
STATUS_OFF: Final = "off"
STATUS_FROST: Final = "frost"
STATUS_BOOST: Final = "boost"
STATUS_HOLD: Final = "hold"
STATUS_DISABLED: Final = "disabled"
STATUS_UNAVAILABLE: Final = "unavailable"
STATUSES: Final = (
    STATUS_FOLLOW,
    STATUS_FIXED,
    STATUS_OFF,
    STATUS_FROST,
    STATUS_BOOST,
    STATUS_HOLD,
    STATUS_DISABLED,
    STATUS_UNAVAILABLE,
)

# Reasons for an "off"/"unavailable"/"disabled" status
REASON_SCHEDULE: Final = "schedule"
REASON_BELOW_THRESHOLD: Final = "below_threshold"
REASON_MASTER_OFF: Final = "master_off"
REASON_MASTER_UNAVAILABLE: Final = "master_unavailable"
REASON_ROOM_UNAVAILABLE: Final = "room_unavailable"
REASON_ROOM_DISABLED: Final = "room_disabled"
REASON_GLOBAL_DISABLED: Final = "global_disabled"

STORAGE_VERSION: Final = 1
