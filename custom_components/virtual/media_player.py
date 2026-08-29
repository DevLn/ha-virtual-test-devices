"""
Virtual media_player (speaker) — PistonCore addition to hass-virtual (FORK_NOTES.md).

A settable media player for testing webCoRE speaker pistons. State and volume are
driven through native `media_player.*` services (media_play/pause/stop,
volume_set, volume_mute, turn_on/off) — standalone-controllable, and PistonCore
uses the same. Modelled on switch.py with a richer state model.
"""

import logging
import voluptuous as vol
from collections.abc import Callable
from typing import Any

import homeassistant.helpers.config_validation as cv
from homeassistant.components.media_player import (
    DOMAIN as PLATFORM_DOMAIN,
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    RepeatMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.config_validation import PLATFORM_SCHEMA
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from . import get_entity_configs
from .const import *
from .entity import (
    FEATURES_SCHEMA,
    VirtualEntity,
    feature_flags,
    optional_list,
    virtual_schema,
)


_LOGGER = logging.getLogger(__name__)

DEPENDENCIES = [COMPONENT_DOMAIN]

DEFAULT_MEDIA_VALUE = "idle"

CONF_SOURCE_LIST = "source_list"
CONF_SOUND_MODE_LIST = "sound_mode_list"

# What the speaker could always do, before cloning existed.
DEFAULT_FEATURES = (
    MediaPlayerEntityFeature.PLAY
    | MediaPlayerEntityFeature.PAUSE
    | MediaPlayerEntityFeature.STOP
    | MediaPlayerEntityFeature.VOLUME_SET
    | MediaPlayerEntityFeature.VOLUME_MUTE
    | MediaPlayerEntityFeature.TURN_ON
    | MediaPlayerEntityFeature.TURN_OFF
    # PLAY_MEDIA is how anything is spoken on a speaker, so a test speaker that
    # cannot be sent audio is a trap: a cloned speaker carried it and a
    # hand-built one did not, which produced at least two wrong "the automation
    # is broken" conclusions before anyone checked the speaker. async_play_media
    # below has always been implemented — it simply was not advertised.
    | MediaPlayerEntityFeature.PLAY_MEDIA
)

BASE_SCHEMA = virtual_schema(DEFAULT_MEDIA_VALUE, {
    **FEATURES_SCHEMA,
    vol.Optional(CONF_CLASS): cv.string,
    vol.Optional(CONF_SOURCE_LIST): vol.All(cv.ensure_list, [cv.string]),
    vol.Optional(CONF_SOUND_MODE_LIST): vol.All(cv.ensure_list, [cv.string]),
})

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(BASE_SCHEMA)
MEDIA_SCHEMA = vol.Schema(BASE_SCHEMA)

_STATES = {s.value: s for s in MediaPlayerState}
_DEVICE_CLASSES = {c.value: c for c in MediaPlayerDeviceClass}


async def async_setup_platform(
        hass: HomeAssistant, config: ConfigType,
        async_add_entities: AddEntitiesCallback,
        _discovery_info: DiscoveryInfoType | None = None) -> None:
    if hass.data[COMPONENT_CONFIG].get(CONF_YAML_CONFIG, False):
        async_add_entities([VirtualMediaPlayer(config, True)], True)


async def async_setup_entry(
        hass: HomeAssistant, entry: ConfigEntry,
        async_add_entities: Callable[[list], None]) -> None:
    entities = []
    for entity in get_entity_configs(hass, entry.data[ATTR_GROUP_NAME], PLATFORM_DOMAIN):
        entities.append(VirtualMediaPlayer(MEDIA_SCHEMA(entity), False))
    async_add_entities(entities)


class VirtualMediaPlayer(VirtualEntity, MediaPlayerEntity):
    """Representation of a Virtual media player.

    Cloned speakers keep the real one's ability set: whether it has an input
    list, sound modes, grouping, track skipping, shuffle/repeat. Every declared
    ability has a handler here, so the clone is drivable — a speaker that says
    it can select a source but raises when you try is worse than useless as a
    bench.

    `play_media` records what it was asked to play. That is deliberate: it makes
    the clone the place to verify what PistonCore's media handling actually
    SENDS (the media-proxy URL rewrite, for instance) without needing a real
    speaker that can play it.
    """

    def __init__(self, config, old_style: bool):
        super().__init__(config, PLATFORM_DOMAIN, old_style)

        self._attr_supported_features = feature_flags(
            config, MediaPlayerEntityFeature, DEFAULT_FEATURES)

        # The two abilities a fake speaker cannot honestly claim. Jeremy's Sonos
        # reports both (supported_features 8321599, measured on the test HA);
        # declaring them here would put Browse/Search buttons in HA's UI that
        # raise the moment they're pressed, and there is no library behind a
        # virtual speaker to browse. Nothing PistonCore emits uses either.
        self._attr_supported_features &= ~(
            MediaPlayerEntityFeature.BROWSE_MEDIA
            | MediaPlayerEntityFeature.SEARCH_MEDIA)
        self._attr_device_class = _DEVICE_CLASSES.get(
            (config.get(CONF_CLASS) or "").lower())
        self._attr_source_list = optional_list(config, CONF_SOURCE_LIST)
        self._attr_sound_mode_list = optional_list(config, CONF_SOUND_MODE_LIST)

        # Never advertise a menu with nothing in it.
        for feature, menu in (
                (MediaPlayerEntityFeature.SELECT_SOURCE, self._attr_source_list),
                (MediaPlayerEntityFeature.SELECT_SOUND_MODE, self._attr_sound_mode_list)):
            if not menu:
                self._attr_supported_features &= ~feature

        _LOGGER.info(f"VirtualMediaPlayer: {self.name} created")

    def _create_state(self, config):
        super()._create_state(config)
        self._attr_state = _STATES.get(
            config.get(CONF_INITIAL_VALUE, DEFAULT_MEDIA_VALUE).lower(), MediaPlayerState.IDLE)
        self._attr_volume_level = 0.5
        self._attr_is_volume_muted = False
        self._attr_shuffle = False
        self._attr_repeat = RepeatMode.OFF
        self._attr_group_members = []
        if self._attr_source_list:
            self._attr_source = self._attr_source_list[0]
        if self._attr_sound_mode_list:
            self._attr_sound_mode = self._attr_sound_mode_list[0]

    def _restore_state(self, state, config):
        super()._restore_state(state, config)
        attrs = state.attributes
        self._attr_state = _STATES.get(state.state, MediaPlayerState.IDLE)
        self._attr_volume_level = attrs.get("volume_level", 0.5)
        self._attr_is_volume_muted = attrs.get("is_volume_muted", False)
        self._attr_shuffle = attrs.get("shuffle", False)
        self._attr_repeat = attrs.get("repeat", RepeatMode.OFF)
        self._attr_group_members = attrs.get("group_members", []) or []
        self._attr_media_content_id = attrs.get("media_content_id")
        self._attr_media_content_type = attrs.get("media_content_type")
        self._attr_media_title = attrs.get("media_title")
        if self._attr_source_list:
            self._attr_source = attrs.get("source") or self._attr_source_list[0]
        if self._attr_sound_mode_list:
            self._attr_sound_mode = attrs.get("sound_mode") or self._attr_sound_mode_list[0]

    def _set_state(self, new_state: MediaPlayerState) -> None:
        self._attr_state = new_state
        self.async_write_ha_state()

    async def async_media_play(self) -> None:
        self._set_state(MediaPlayerState.PLAYING)

    async def async_media_pause(self) -> None:
        self._set_state(MediaPlayerState.PAUSED)

    async def async_media_stop(self) -> None:
        self._set_state(MediaPlayerState.IDLE)

    async def async_turn_on(self) -> None:
        self._set_state(MediaPlayerState.ON)

    async def async_turn_off(self) -> None:
        self._set_state(MediaPlayerState.OFF)

    async def async_set_volume_level(self, volume: float) -> None:
        self._attr_volume_level = volume
        self.async_write_ha_state()

    async def async_mute_volume(self, mute: bool) -> None:
        self._attr_is_volume_muted = mute
        self.async_write_ha_state()

    async def async_volume_up(self) -> None:
        self._attr_volume_level = min(1.0, (self._attr_volume_level or 0) + 0.1)
        self.async_write_ha_state()

    async def async_volume_down(self) -> None:
        self._attr_volume_level = max(0.0, (self._attr_volume_level or 0) - 0.1)
        self.async_write_ha_state()

    async def async_play_media(self, media_type: str, media_id: str, **kwargs: Any) -> None:
        """Record exactly what was requested, then report playing."""
        _LOGGER.info(f"{self.name} play_media type={media_type} id={media_id}")
        self._attr_media_content_type = media_type
        self._attr_media_content_id = media_id
        self._attr_media_title = str(media_id).rsplit("/", 1)[-1]
        self._set_state(MediaPlayerState.PLAYING)

    async def async_media_next_track(self) -> None:
        self._set_state(MediaPlayerState.PLAYING)

    async def async_media_previous_track(self) -> None:
        self._set_state(MediaPlayerState.PLAYING)

    async def async_media_seek(self, position: float) -> None:
        self._attr_media_position = position
        self.async_write_ha_state()

    async def async_select_source(self, source: str) -> None:
        self._attr_source = source
        self.async_write_ha_state()

    async def async_select_sound_mode(self, sound_mode: str) -> None:
        self._attr_sound_mode = sound_mode
        self.async_write_ha_state()

    async def async_set_shuffle(self, shuffle: bool) -> None:
        self._attr_shuffle = shuffle
        self.async_write_ha_state()

    async def async_set_repeat(self, repeat: RepeatMode) -> None:
        self._attr_repeat = repeat
        self.async_write_ha_state()

    async def async_clear_playlist(self) -> None:
        self._attr_media_content_id = None
        self._attr_media_title = None
        self._set_state(MediaPlayerState.IDLE)

    async def async_join_players(self, group_members: list[str]) -> None:
        self._attr_group_members = [self.entity_id] + [
            m for m in group_members if m != self.entity_id]
        self.async_write_ha_state()

    async def async_unjoin_player(self) -> None:
        self._attr_group_members = []
        self.async_write_ha_state()
