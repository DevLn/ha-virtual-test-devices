"""
Virtual text — PistonCore addition to hass-virtual (FORK_NOTES.md).

A free-text setting, as devices expose for names, messages or codes (common on
ESPHome). Driven with Home Assistant's own `text.set_value`, so no service of
its own is needed.

`min`, `max`, `pattern` and `mode` are HA's capability attributes for text and
are copied verbatim when cloned or exported. Each is optional and falls back to
HA's own default. `initial_value` is the starting text.

A VALUE THAT BREAKS THOSE LIMITS IS DROPPED, not kept. HA's TextEntity raises
when asked for the state of a value that is too short, too long or off-pattern,
which would stop the entity being written at all. So a bad starting or restored
value becomes "no value" (unknown) with a warning instead.

A `password` text is never exported with its value — see export.py.
"""

import logging
import voluptuous as vol
from collections.abc import Callable

import homeassistant.helpers.config_validation as cv
from homeassistant.components.text import (
    DOMAIN as PLATFORM_DOMAIN,
    TextEntity,
    TextMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers.config_validation import PLATFORM_SCHEMA
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from . import get_entity_configs
from .const import *
from .entity import VirtualEntity, virtual_schema


_LOGGER = logging.getLogger(__name__)

DEPENDENCIES = [COMPONENT_DOMAIN]

CONF_PATTERN = "pattern"
CONF_MODE = "mode"

BASE_SCHEMA = virtual_schema("", {
    vol.Optional(CONF_MIN): vol.All(vol.Coerce(int), vol.Range(min=0)),
    vol.Optional(CONF_MAX): vol.All(vol.Coerce(int), vol.Range(min=0)),
    vol.Optional(CONF_PATTERN): vol.Any(None, cv.string),
    vol.Optional(CONF_MODE): vol.In([m.value for m in TextMode]),
})
PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(BASE_SCHEMA)
TEXT_SCHEMA = vol.Schema(BASE_SCHEMA)


async def async_setup_platform(
        hass: HomeAssistant, config: ConfigType,
        async_add_entities: AddEntitiesCallback,
        _discovery_info: DiscoveryInfoType | None = None) -> None:
    if hass.data[COMPONENT_CONFIG].get(CONF_YAML_CONFIG, False):
        async_add_entities([VirtualText(config, True)], True)


async def async_setup_entry(
        hass: HomeAssistant, entry: ConfigEntry,
        async_add_entities: Callable[[list], None]) -> None:
    entities = []
    for entity in get_entity_configs(hass, entry.data[ATTR_GROUP_NAME], PLATFORM_DOMAIN):
        entities.append(VirtualText(TEXT_SCHEMA(entity), False))
    async_add_entities(entities)


class VirtualText(VirtualEntity, TextEntity):
    """Representation of a Virtual text."""

    def __init__(self, config, old_style: bool):
        super().__init__(config, PLATFORM_DOMAIN, old_style)
        for attr, key in (("_attr_native_min", CONF_MIN), ("_attr_native_max", CONF_MAX),
                          ("_attr_pattern", CONF_PATTERN)):
            if (value := config.get(key)) is not None:
                setattr(self, attr, value)
        if (mode := config.get(CONF_MODE)) is not None:
            self._attr_mode = TextMode(mode)
        _LOGGER.info(f"VirtualText: {self.name} created")

    def _fits(self, value):
        """`value` if HA will accept it as this entity's state, else None."""
        if value is None:
            return None
        if self.min <= len(value) <= self.max and (
                self.pattern_cmp is None or self.pattern_cmp.match(value)):
            return value
        _LOGGER.warning("virtual: %s cannot hold %r (length %d-%d, pattern %r); "
                        "leaving it empty", self.name, value, self.min, self.max,
                        self.pattern)
        return None

    def _create_state(self, config):
        super()._create_state(config)
        self._attr_native_value = self._fits(config.get(CONF_INITIAL_VALUE) or None)

    def _restore_state(self, state, config):
        super()._restore_state(state, config)
        restored = None if state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE) else state.state
        self._attr_native_value = self._fits(restored)

    async def async_set_value(self, value: str) -> None:
        # HA has already checked the value against min, max and pattern.
        _LOGGER.debug(f"{self.name} set to {value!r}")
        self._attr_native_value = value
        self.async_write_ha_state()
