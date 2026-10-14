"""
Virtual select — PistonCore addition to hass-virtual (FORK_NOTES.md).

A pick-one-of-these setting, as devices expose for modes and presets (ESPHome,
Zigbee and Z-Wave devices are full of them). Driven with Home Assistant's own
`select.select_option` / `select_next` / `select_previous`, so no service of
its own is needed.

`options` is the menu, copied verbatim when cloned or exported: it is the one
capability attribute HA defines for a select. `initial_value` is the starting
option; one that is not on the menu falls back to the first option, because HA
would otherwise report the entity as unknown.
"""

import logging
import voluptuous as vol
from collections.abc import Callable

import homeassistant.helpers.config_validation as cv
from homeassistant.components.select import (
    DOMAIN as PLATFORM_DOMAIN,
    SelectEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.config_validation import PLATFORM_SCHEMA
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from . import get_entity_configs
from .const import *
from .entity import VirtualEntity, virtual_schema


_LOGGER = logging.getLogger(__name__)

DEPENDENCIES = [COMPONENT_DOMAIN]

CONF_OPTIONS = "options"

BASE_SCHEMA = virtual_schema("", {
    vol.Optional(CONF_OPTIONS, default=[]): vol.All(cv.ensure_list, [cv.string]),
})
PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(BASE_SCHEMA)
SELECT_SCHEMA = vol.Schema(BASE_SCHEMA)


async def async_setup_platform(
        hass: HomeAssistant, config: ConfigType,
        async_add_entities: AddEntitiesCallback,
        _discovery_info: DiscoveryInfoType | None = None) -> None:
    if hass.data[COMPONENT_CONFIG].get(CONF_YAML_CONFIG, False):
        async_add_entities([VirtualSelect(config, True)], True)


async def async_setup_entry(
        hass: HomeAssistant, entry: ConfigEntry,
        async_add_entities: Callable[[list], None]) -> None:
    entities = []
    for entity in get_entity_configs(hass, entry.data[ATTR_GROUP_NAME], PLATFORM_DOMAIN):
        entities.append(VirtualSelect(SELECT_SCHEMA(entity), False))
    async_add_entities(entities)


class VirtualSelect(VirtualEntity, SelectEntity):
    """Representation of a Virtual select."""

    def __init__(self, config, old_style: bool):
        super().__init__(config, PLATFORM_DOMAIN, old_style)
        self._attr_options = list(config.get(CONF_OPTIONS, []))
        _LOGGER.info(f"VirtualSelect: {self.name} created")

    def _on_menu(self, option):
        """`option` if it is on the menu, else the first option (or None)."""
        if option in self._attr_options:
            return option
        if option:
            _LOGGER.warning("virtual: %s has no option %r; using %r", self.name,
                            option, self._attr_options[0] if self._attr_options else None)
        return self._attr_options[0] if self._attr_options else None

    def _create_state(self, config):
        super()._create_state(config)
        self._attr_current_option = self._on_menu(config.get(CONF_INITIAL_VALUE))

    def _restore_state(self, state, config):
        super()._restore_state(state, config)
        restored = state.state if state.state in self._attr_options else None
        self._attr_current_option = restored or self._on_menu(config.get(CONF_INITIAL_VALUE))

    async def async_select_option(self, option: str) -> None:
        # HA has already checked the option is on the menu.
        _LOGGER.debug(f"{self.name} set to {option}")
        self._attr_current_option = option
        self.async_write_ha_state()
