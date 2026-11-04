"""Small helpers shared by the config flow and the controller."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .const import CONF_CLIMATE_ENTITIES, CONF_CLIMATE_ENTITY


def room_entities(data: Mapping[str, Any]) -> list[str]:
    """The thermostats of a room subentry (handles the pre-0.4 single key)."""
    entities = data.get(CONF_CLIMATE_ENTITIES)
    if entities:
        return list(entities)
    if single := data.get(CONF_CLIMATE_ENTITY):
        return [single]
    return []
