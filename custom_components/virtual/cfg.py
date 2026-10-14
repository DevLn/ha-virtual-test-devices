"""
Handles the file based Virtual configuration.

Virtual seems to need a lot device config so rather than get rid of
the options or clutter up the config flow system I'm adding a text file
where the user can configure things.

There are 2 pieces:

- `BlendedCfg`; this class is responsible for loading the new file based
  configuration and merging it with the flow data and options.

- `UpgradeCfg`; A helper class to import configuration from the old YAML
  layout.
"""

import aiofiles
import asyncio
import copy
import logging
import os
import json
import voluptuous as vol
import uuid
from datetime import timedelta

from homeassistant.const import (
    ATTR_ENTITY_ID,
    CONF_PLATFORM,
    CONF_UNIT_OF_MEASUREMENT,
    Platform
)
from homeassistant.core import valid_entity_id
from homeassistant.helpers import config_validation as cv
from homeassistant.util import slugify
from homeassistant.util.yaml import parse_yaml, dump

from .const import *
from .entity import virtual_schema


_LOGGER = logging.getLogger(__name__)

BINARY_SENSOR_DEFAULT_INITIAL_VALUE = 'off'
BINARY_SENSOR_SCHEMA = vol.Schema(virtual_schema(BINARY_SENSOR_DEFAULT_INITIAL_VALUE, {
    vol.Optional(CONF_CLASS): cv.string,
}))

SENSOR_DEFAULT_INITIAL_VALUE = '0'
SENSOR_SCHEMA = vol.Schema(virtual_schema(SENSOR_DEFAULT_INITIAL_VALUE, {
    vol.Optional(CONF_CLASS): cv.string,
    vol.Optional(CONF_UNIT_OF_MEASUREMENT, default=""): cv.string,
}))

_meta_lock = asyncio.Lock()


def _fix_value(value):
    """ If needed, convert value into a type that can be stored in yaml.
    """
    if isinstance(value, timedelta):
        return max(value.seconds, 1)
    return value


async def _async_load_json(file_name):
    _LOGGER.debug("_async_load_yaml1 file_name for %s", file_name)
    try:
        async with aiofiles.open(file_name, 'r') as meta_file:
            _LOGGER.debug("_async_load_yaml2 file_name for %s", file_name)
            contents = await meta_file.read()
            _LOGGER.debug("_async_load_yaml3 file_name for %s", file_name)
            return json.loads(contents)
    except Exception as e:
        _LOGGER.debug("_async_load_yaml3 file_name for %s", file_name)
        return {}


async def _atomic_write(file_name, text):
    """Write via a temp file and rename, so a failure can never leave a
    half-written or empty file behind.

    PistonCore fix (2026-07-31), after losing every test device on this bench:
    these savers used to open the real file in 'w' — which TRUNCATES IT TO ZERO —
    and only then serialize. Serializing anything yaml/json couldn't represent
    therefore destroyed the existing file, and the exception was swallowed at
    debug level so nothing said so. The integration then failed to load at all,
    because reading an empty file yields None.

    Serializing happens before this is called, so a bad value raises with the
    old file still intact.
    """
    tmp = f"{file_name}.tmp"
    async with aiofiles.open(tmp, 'w') as f:
        await f.write(text)
    await asyncio.get_running_loop().run_in_executor(None, os.replace, tmp, file_name)


async def _async_save_json(file_name, data):
    try:
        text = json.dumps(data, indent=4)      # serialize FIRST
    except Exception:
        _LOGGER.exception("virtual: refusing to save unserialisable json to %s "
                          "(existing file left untouched)", file_name)
        raise
    try:
        await _atomic_write(file_name, text)
    except Exception:
        _LOGGER.exception("virtual: could not write %s", file_name)
        raise


async def _async_load_yaml(file_name):
    _LOGGER.debug("_async_load_yaml1 file_name for %s", file_name)
    try:
        async with aiofiles.open(file_name, 'r') as meta_file:
            _LOGGER.debug("_async_load_yaml2 file_name for %s", file_name)
            contents = await meta_file.read()
            _LOGGER.debug("_async_load_yaml3 file_name for %s", file_name)
            return parse_yaml(contents)
    except Exception as e:
        _LOGGER.debug("_async_load_yaml3 file_name for %s", file_name)
        return {}


async def _async_save_yaml(file_name, data):
    try:
        text = dump(data)                      # serialize FIRST — see _atomic_write
    except Exception:
        _LOGGER.exception("virtual: refusing to save unserialisable yaml to %s "
                          "(existing file left untouched)", file_name)
        raise
    try:
        await _atomic_write(file_name, text)
    except Exception:
        _LOGGER.exception("virtual: could not write %s", file_name)
        raise


async def _load_meta_data(hass, group_name: str):
    """Read in meta data for a particular group.
    """
    async with _meta_lock:
        data = await _async_load_json(default_meta_file(hass))
        return data.get(ATTR_DEVICES, {}).get(group_name, {})


async def _save_meta_data(hass, group_name, meta_data):
    """Save meta data for a particular group name.
    """
    async with _meta_lock:

        # Read in current meta data
        devices = await _async_load_json(default_meta_file(hass))
        devices = devices.get(ATTR_DEVICES, {})

        # Update (or add) the group piece.
        _LOGGER.debug(f"meta before {devices}")
        devices.update({
            group_name: meta_data
        })
        _LOGGER.debug(f"meta after {devices}")

        # Write it back out.
        await _async_save_json(default_meta_file(hass), {
            ATTR_VERSION: 1,
            ATTR_DEVICES: devices
        })


async def _delete_meta_data(hass, group_name):
    """Save meta data for a particular group name.
    """
    async with _meta_lock:

        # Read in current meta data
        devices = await _async_load_json(default_meta_file(hass))
        devices = devices.get(ATTR_DEVICES, {})

        # Delete the group piece.
        _LOGGER.debug(f"meta before {devices}")
        devices.pop(group_name)
        _LOGGER.debug(f"meta after {devices}")

        # Write it back out.
        await _async_save_json(default_meta_file(hass), {
            ATTR_VERSION: 1,
            ATTR_DEVICES: devices
        })


async def _save_user_data(file_name, devices):
    await _async_save_yaml(file_name, {
        ATTR_VERSION: 1,
        ATTR_DEVICES: devices
    })


async def _load_user_data(file_name):
    entities = await _async_load_yaml(file_name)
    # An empty or unparseable file yields None, which used to crash setup here
    # and take the whole integration down with it. Degrade to "no devices"
    # instead — the file is recoverable, an integration that won't load is not.
    if not isinstance(entities, dict):
        if entities is not None:
            _LOGGER.warning("virtual: %s is not a mapping; ignoring it", file_name)
        return {}
    # make devices: optional.
    return entities.get(ATTR_DEVICES, entities)


def _fix_config(config):
    """Find and return the virtual entries from any platform config.
    """
    _LOGGER.debug(f"config={config}")
    entries = []
    for entry in config:
        if entry[CONF_PLATFORM] == COMPONENT_DOMAIN:
            entry = copy.deepcopy(entry)
            entry.pop(CONF_PLATFORM)
            entries.append(entry)
    return entries


def _upgrade_name(name: str):
    """We're making the non virtual prefix the default so this flips the naming.
    """
    if name.startswith("!"):
        return name[1:]
    elif name.startswith("virtual_"):
        return f"+{name[8:]}"
    else:
        return f"+{name}"


def _parse_old_config(devices, configs, platform):
    """Parse out config into different devices.

    We do several things:
    - insert a platform key/value, i.e, this this is a switch
    - fix the naming
    - create and store the entity under a device, for imported config there
      will only be one entity per device
    """
    for config in configs:
        if not isinstance(config, dict):
            _LOGGER.debug(f"not dictionary={config}")
            continue
        if config[CONF_PLATFORM] != COMPONENT_DOMAIN:
            continue

        # Copy and fix up config.
        config = copy.deepcopy(config)
        config[CONF_PLATFORM] = platform
        config[CONF_NAME] = _upgrade_name(config[CONF_NAME])

        # Fix values that need to be saved in yaml
        config = {k: _fix_value(v) for k, v in config.items()}

        # Insert or create a device for it.
        if config[CONF_NAME] in devices:
            devices[config[CONF_NAME]].append(config)
        else:
            devices[config[CONF_NAME]] = [config]

    return devices


def _make_original_unique_id(name):
    if name.startswith("+"):
        return slugify(name[1:])
    else:
        return slugify(name)


def _make_name(name):
    if name.startswith("+"):
        return name[1:]
    return name


def _make_entity_id(platform, name):
    if name.startswith("+"):
        return f'{platform}.{COMPONENT_DOMAIN}_{slugify(name[1:])}'
    else:
        return f'{platform}.{slugify(name)}'


def _make_unique_id():
    return f'{uuid.uuid4()}.{COMPONENT_DOMAIN}'


def unique_in_device(seen: dict, name: str) -> str:
    """Number repeated names so each entity in a device keeps its own identity.

    An entity's identity is filed under its NAME. Two entities in one device that
    end up with the same name file under one key, so on every load the second one
    looks itself up, finds nothing, concludes it is new, and is registered again.
    Measured on a clean HA 2026-08-19: a device with three unnamed motion sensors
    grew by two dead rows on EVERY restart, without limit, until the install hit
    HA's 10,000-entity ceiling. Entities named uniquely were byte-stable across
    the same restarts.

    Repeats get numbered so the lookup stays one-to-one. The FIRST entity keeps
    the bare name, which is what makes this safe to ship: every identity that
    works today is left exactly as it is. Deriving identity from the name instead
    would change it for EVERY existing entity and orphan the lot on upgrade.
    """
    seen[name] = seen.get(name, 0) + 1
    return name if seen[name] == 1 else f"{name} {seen[name]}"


def explicit_identity(platform: str, entity_id, unique_id, seen: set):
    """The unique_id for an entity that names its own `entity_id`, or None to
    skip the entry.

    PistonCore addition (2026-10-14). An entry with an explicit `entity_id` IS
    that entity: the id is used as given and the unique_id is the YAML
    `unique_id` or, failing that, the entity_id itself. Both are deterministic,
    so a regenerated file maps onto the same registry rows every time, which is
    what lets one Home Assistant stand in for another. Such an entry needs no
    meta-file row at all, so it can never collide with the name-keyed entries
    that identify everything else; the name is display only.

    Two things are refused rather than guessed, because a wrong guess becomes a
    permanent identity: an id whose domain is not the entry's platform, and an
    id already claimed by an earlier entry in the same group (the first keeps
    it). Both are logged at ERROR and the entry is skipped.
    """
    if not isinstance(entity_id, str) or not valid_entity_id(entity_id) \
            or entity_id.split(".", 1)[0] != platform:
        _LOGGER.error("virtual: skipping %r: entity_id must be a valid %s.* id",
                      entity_id, platform)
        return None
    if entity_id in seen:
        _LOGGER.error("virtual: skipping a second entry for %s; the first one "
                      "keeps it", entity_id)
        return None
    seen.add(entity_id)
    return unique_id or entity_id


def _make_suffix(platform, device_class):
    """Make a suitable suffix for an unnamed entity.
    
    Binary sensors, covers and sensors have a class so we append that,
    everything else gets left as-is.
    """
    if platform in [Platform.BINARY_SENSOR, Platform.COVER, Platform.SENSOR]:
        if device_class is None:
            return platform
        else:
            return f"{device_class}"
    return ""


DEVICE_META_KEYS = ("manufacturer", "model", "sw_version", "area")


def _device_block(block):
    """(device metadata, entities) for one device in the file.

    PistonCore addition (2026-10-14). A device is `Name: [entities]`, or the
    mapping form `Name: {manufacturer: .., model: .., sw_version: .., area: ..,
    entities: [..]}` so a stand-in can carry what the real device reports.
    Lists keep working unchanged. Values are coerced to str because the device
    registry wants strings and yaml reads `sw_version: 1.2` as a float.
    """
    if isinstance(block, dict):
        meta = {k: str(block[k]) for k in DEVICE_META_KEYS if block.get(k) is not None}
        return meta, block.get(ATTR_ENTITIES) or []
    return {}, block or []


class BlendedCfg(object):
    """Helper class to get at Virtual configuration options.

    Reads in non config flow settings from the external config file and merges
    them with flow data and options.
    """

    def __init__(self, hass, flow_data):
        self._hass = hass
        self._group_name = flow_data[ATTR_GROUP_NAME]
        self._file_name = flow_data[ATTR_FILE_NAME]

        self._meta_data = {}
        self._orphaned_entities = {}
        self._devices = []
        self._entities = {}

    async def async_load(self):
        meta_data = await _load_meta_data(self._hass, self._group_name)
        devices = await _load_user_data(self._file_name)
        changed = False

        _LOGGER.debug(f"loaded-meta-data={meta_data}")
        _LOGGER.debug(f"loaded-devices={devices}")

        # Explicit entity ids must be unique across the whole group; see
        # explicit_identity().
        seen_ids = set()

        # Let's fix up the devices/entities
        for device_name, block in devices.items():
            device_meta, entities = _device_block(block)

            # Create device. One per all entities.
            self._devices.append({
                ATTR_DEVICE_ID: device_name,
                CONF_NAME: _make_name(device_name),
                **device_meta
            })

            # Names have to be unique WITHIN a device, because that is the key an
            # entity's identity is filed under. See unique_in_device().
            seen_names = {}

            for entity in entities:

                platform = entity.pop(CONF_PLATFORM)
                device_class = entity.get(CONF_CLASS, None)

                # Figure out the name. We use the one provided and if that isn't
                # there the device name and, optionally, the class.
                name = entity.get(CONF_NAME, None)
                if name is None:
                    name = f"{device_name} {_make_suffix(platform, device_class)}"
                name = unique_in_device(seen_names, name)

                # An explicit entity_id is the identity and needs no meta row.
                # See explicit_identity().
                explicit = entity.get(ATTR_ENTITY_ID)
                if explicit is not None:
                    unique_id = explicit_identity(
                        platform, explicit, entity.get(ATTR_UNIQUE_ID), seen_ids)
                    if unique_id is None:
                        continue
                    entity.update({
                        CONF_NAME: _make_name(name),
                        ATTR_UNIQUE_ID: unique_id,
                        ATTR_DEVICE_ID: device_name
                    })
                    self._entities.setdefault(platform, []).append(entity)
                    continue

                # Look up unique id for this device. If not there this is a new
                # device.
                unique_id = meta_data.get(name, {}).get(ATTR_UNIQUE_ID, None)
                if unique_id is None:
                    _LOGGER.debug(f"creating {name}")
                    unique_id = _make_unique_id()
                    meta_data.update({name: {
                        ATTR_UNIQUE_ID: unique_id,
                        ATTR_ENTITY_ID: _make_entity_id(platform, name),
                        ATTR_DEVICE_ID: device_name
                    }})
                    changed = True

                # Now copy over the entity id of the device. Not having this is a
                # bug.
                entity_id = meta_data.get(name, {}).get(ATTR_ENTITY_ID, None)
                if entity_id is None:
                    _LOGGER.info(f"problem creating {name}, no entity id")
                    continue

                # Add device entry
                if meta_data.get(name, {}).get(ATTR_DEVICE_ID, None) is None:
                    _LOGGER.info(f"problem creating {name}, no device id")
                    meta_data[name][ATTR_DEVICE_ID] = device_name
                    changed = True

                # Update the entity.
                entity.update({
                    CONF_NAME: _make_name(name),
                    ATTR_ENTITY_ID: entity_id,
                    ATTR_UNIQUE_ID: unique_id,
                    ATTR_DEVICE_ID: device_name
                })
                _LOGGER.debug(f"added entity {platform}/{entity}")

                # Now store in the correct place. Move off temporary meta
                # data list.
                # _LOGGER.debug(f"entities={self._entities}")
                if platform not in self._entities:
                    self._entities[platform] = []
                self._entities[platform].append(entity)
                self._meta_data.update({
                    name: meta_data.pop(name)
                })

        # Create orphaned list. If we have anything here we need to update
        # the saved meta data.
        for switch, values in meta_data.items():
            values[CONF_NAME] = switch
            self._orphaned_entities.update({
                values[ATTR_UNIQUE_ID]: values
            })
            changed = True

        # Make sure changes are kept.
        if changed:
            await _save_meta_data(self._hass, self._group_name, self._meta_data)

        _LOGGER.debug(f"meta-data={self._meta_data}")
        _LOGGER.debug(f"devices={self._devices}")
        _LOGGER.debug(f"entities={self._entities}")
        _LOGGER.debug(f"orphaned-entities={self._orphaned_entities}")

    async def async_delete(self):
        _LOGGER.debug(f"deleting {self._group_name}")
        await _delete_meta_data(self._hass, self._group_name)

    @property
    def devices(self):
        return self._devices

    @property
    def entities(self):
        return self._entities

    @property
    def orphaned_entities(self):
        return self._orphaned_entities

    @property
    def binary_sensor_config(self):
        return self._entities.get(Platform.BINARY_SENSOR, [])

    @property
    def sensor_config(self):
        return self._entities.get(Platform.SENSOR, [])

    @property
    def switch_config(self):
        return self._entities.get(Platform.SWITCH, [])


class UpgradeCfg(object):
    """Read in the old YAML config and convert it to the new format.
    """

    @staticmethod
    async def async_import_yaml(hass, config):
        """ Take the current virtual config and make the new yaml file.

        Virtual needs a lot of fine tuning so rather than get rid of the
        options or clutter up the config flow system I'm adding a text file
        where the user can configure things.
        """

        devices_meta_data = {}
        devices = {}

        # Add in the easily formatted devices.
        for platform in [Platform.BINARY_SENSOR, Platform.SENSOR,
                         Platform.FAN, Platform.LIGHT,
                         Platform.LOCK, Platform.SWITCH]:
            devices = _parse_old_config(devices, config.get(platform, []), str(platform))

        # Device tracker is awkward, we have to split it out and fake looking
        # like the other entities.
        all_device_trackers = config.get(Platform.DEVICE_TRACKER, [])
        for device_trackers in all_device_trackers:
            if device_trackers[CONF_PLATFORM] != COMPONENT_DOMAIN:
                continue
            for device_tracker_name in device_trackers.get("devices", []):
                if isinstance(device_tracker_name, dict):
                    device_tracker_name = device_tracker_name[CONF_NAME]
                devices = _parse_old_config(devices, [{
                    CONF_PLATFORM: COMPONENT_DOMAIN,
                    CONF_NAME: device_tracker_name
                }], str(Platform.DEVICE_TRACKER))

        _LOGGER.info(f"devices={devices}")

        # Here we have all the original devices we build the meta data.
        # For import
        #  - we can only have one entity per device, which means...
        #  - devices are their own parent
        for name, values in devices.items():
            unique_id = _make_original_unique_id(name)
            entity_id = _make_entity_id(values[0][CONF_PLATFORM], name)

            _LOGGER.debug(f"uid={unique_id}")
            _LOGGER.debug(f"eid={entity_id}")
            devices_meta_data.update({name: {
                ATTR_UNIQUE_ID: unique_id,
                ATTR_ENTITY_ID: entity_id
            }})

        _LOGGER.debug(f"devices-meta-data={devices_meta_data}")

        await _save_user_data(default_config_file(hass), devices)
        await _save_meta_data(hass, IMPORTED_GROUP_NAME, devices_meta_data)

    @staticmethod
    def create_flow_data(hass, _config):
        """ Take the current aarlo config and make the new flow configuration.
        """
        _LOGGER.debug(f"new-config-file={default_config_file(hass)}")
        _LOGGER.debug(f"new-meta-file={default_meta_file(hass)}")

        return {
            ATTR_GROUP_NAME: IMPORTED_GROUP_NAME,
            ATTR_FILE_NAME: default_config_file(hass)
        }
