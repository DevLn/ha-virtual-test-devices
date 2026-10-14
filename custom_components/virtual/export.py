"""
Export the live entities as virtual stand-ins — PistonCore addition (FORK_NOTES.md).

Action: virtual.export_devices
  file:                  where to write; default <config>/virtual_export.yaml.
                         Must be inside the config folder or under
                         allowlist_external_dirs.
  exclude_integrations:  integrations to leave out. Default hassio, backup, sun,
                         hacs and virtual: the target HA has its own copies of
                         those, so exporting them would only make _2 duplicates.
  exclude_entities:      entity ids or globs (sensor.*_power) to leave out.
  exclude_devices:       device names or globs ("??:??:??:??:??:??") whose
                         entities are ALL left out. Matched against the name
                         shown in the UI. Entities with no device are not
                         affected; exclude_entities covers those.
  include_domains:       default every domain this integration can reproduce.
  persistent:            whether the test devices keep their last state when
                         the test HA restarts (on) or go back to the exported
                         state every time (off). Left out: the platform's
                         default, which is on.
  skip_hidden:           leave out entities hidden in the UI. Default false.

WHAT IT IS FOR. One Home Assistant stands in for another. This runs on the LIVE
one and writes a devices file that, loaded as a virtual group on a TEST one,
provides every reproducible entity under the SAME entity id with the live state
as its starting state. An automation copied across then runs unchanged against
stand-ins. The two instances are connected by nothing but the copied file.

WHAT IT DOES NOT DO. It changes nothing on the HA it runs on except writing the
file. It copies shape and state, not behaviour (see clone.py), and it never
copies lock or alarm codes: the per-entity capture is clone.py's own, so this
and clone_device cannot disagree about what a copy carries.

With `virtual: {export_only: true}` in configuration.yaml this action is the
ONLY thing the integration provides on that HA — no groups, no entities, no
create or clone — so the live home cannot grow test devices by accident.
"""

import fnmatch
import logging
import pathlib
from collections import Counter

import voluptuous as vol

from homeassistant.const import (
    ATTR_ENTITY_ID,
    CONF_PLATFORM,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    __version__ as HA_VERSION,
)
from homeassistant.core import HomeAssistant, SupportsResponse, callback
from homeassistant.exceptions import ServiceValidationError
import homeassistant.helpers.area_registry as ar
import homeassistant.helpers.config_validation as cv
import homeassistant.helpers.device_registry as dr
import homeassistant.helpers.entity_registry as er
from homeassistant.util import dt as dt_util
from homeassistant.util.yaml import dump

from . import (
    __version__,
    alarm_control_panel, binary_sensor, button, camera, climate, cover,
    device_tracker, event, fan, humidifier, light, lock, media_player, notify,
    number, select, sensor, siren, switch, text, vacuum, valve,
)
from .cfg import _atomic_write, unique_in_device
from .clone import REPRODUCIBLE_DOMAINS, _plain, entity_spec
from .const import *

_LOGGER = logging.getLogger(__name__)

SERVICE_EXPORT_DEVICES = "export_devices"

CONF_FILE = "file"
CONF_EXCLUDE_INTEGRATIONS = "exclude_integrations"
CONF_EXCLUDE_ENTITIES = "exclude_entities"
CONF_EXCLUDE_DEVICES = "exclude_devices"
CONF_INCLUDE_DOMAINS = "include_domains"
CONF_SKIP_HIDDEN = "skip_hidden"

DEFAULT_FILE = "virtual_export.yaml"
DEFAULT_EXCLUDE_INTEGRATIONS = ["hassio", "backup", "sun", "hacs", COMPONENT_DOMAIN]

EXPORT_SCHEMA = vol.Schema({
    vol.Optional(CONF_FILE): cv.string,
    vol.Optional(CONF_EXCLUDE_INTEGRATIONS, default=DEFAULT_EXCLUDE_INTEGRATIONS):
        vol.All(cv.ensure_list, [cv.string]),
    vol.Optional(CONF_EXCLUDE_ENTITIES, default=[]): vol.All(cv.ensure_list, [cv.string]),
    vol.Optional(CONF_EXCLUDE_DEVICES, default=[]): vol.All(cv.ensure_list, [cv.string]),
    vol.Optional(CONF_INCLUDE_DOMAINS, default=sorted(REPRODUCIBLE_DOMAINS)):
        vol.All(cv.ensure_list, [cv.string]),
    vol.Optional(CONF_PERSISTENT): cv.boolean,
    vol.Optional(CONF_SKIP_HIDDEN, default=False): cv.boolean,
})

# Each platform's own closed schema, so an exported entry is checked by exactly
# what will load it. Explicit rather than discovered, because the names are
# irregular; a rename shows up here as an import error, not a silent gap.
PLATFORM_SCHEMAS = {
    "alarm_control_panel": alarm_control_panel.ALARM_SCHEMA,
    "binary_sensor": binary_sensor.BINARY_SENSOR_SCHEMA,
    "button": button.BUTTON_SCHEMA,
    "camera": camera.CAMERA_SCHEMA,
    "climate": climate.CLIMATE_SCHEMA,
    "cover": cover.COVER_SCHEMA,
    "device_tracker": device_tracker.DEVICE_TRACKER_SCHEMA,
    "event": event.EVENT_SCHEMA,
    "fan": fan.FAN_SCHEMA,
    "humidifier": humidifier.HUMIDIFIER_SCHEMA,
    "light": light.LIGHT_SCHEMA,
    "lock": lock.LOCK_SCHEMA,
    "media_player": media_player.MEDIA_SCHEMA,
    "notify": notify.NOTIFY_SCHEMA,
    "number": number.NUMBER_SCHEMA,
    "select": select.SELECT_SCHEMA,
    "sensor": sensor.SENSOR_SCHEMA,
    "siren": siren.SIREN_SCHEMA,
    "switch": switch.SWITCH_SCHEMA,
    "text": text.TEXT_SCHEMA,
    "vacuum": vacuum.VACUUM_SCHEMA,
    "valve": valve.VALVE_SCHEMA,
}

# Their state is a timestamp or a stream status, not a setting to start from.
STATELESS_DOMAINS = {"button", "camera", "event", "notify"}

# Live attribute -> the light platform's initial_* key for it. Colour
# temperature is left out on purpose: the platform reads initial_color_temp as
# kelvin but its schema still validates it as a byte, so no real value passes.
LIGHT_INITIAL = (("brightness", "initial_brightness"),
                 ("hs_color", "initial_color"),
                 ("effect", "initial_effect"))

NOT_REPRODUCIBLE = "domain not reproducible"


def _entity_export(hass: HomeAssistant, row, state, persistent) -> dict:
    """One entity's entry for the file: identity, the capture clone.py makes,
    and the live state as the starting state."""
    entity_id = row.entity_id if row else state.entity_id
    domain = entity_id.split(".", 1)[0]
    attrs = dict(state.attributes) if state else {}
    name = (attrs.get("friendly_name")
            or (row and (row.name or row.original_name))
            or entity_id.split(".", 1)[1])
    spec = {CONF_PLATFORM: domain, CONF_NAME: str(name), ATTR_ENTITY_ID: entity_id}
    spec.update(entity_spec(hass, domain, row, attrs))
    if row and row.entity_category:
        spec[CONF_ENTITY_CATEGORY] = _plain(row.entity_category)
    if persistent is not None:
        spec[CONF_PERSISTENT] = persistent
    if state is None or domain in STATELESS_DOMAINS:
        return spec
    if domain == "text" and attrs.get("mode") == "password":
        # A password field's value is a secret. The file is made to be copied
        # to another machine, so it never carries one (see clone.py's codes).
        return spec
    if state.state == STATE_UNAVAILABLE:
        spec[CONF_INITIAL_AVAILABILITY] = False
    elif state.state != STATE_UNKNOWN:
        spec[CONF_INITIAL_VALUE] = state.state
        if domain == "light":
            spec.update({key: _plain(attrs[attr])
                         for attr, key in LIGHT_INITIAL if attrs.get(attr) is not None})
    return spec


def _validate(spec: dict):
    """None if the entry's platform accepts it, else why it would not."""
    body = {k: v for k, v in spec.items() if k != CONF_PLATFORM}  # cfg pops platform first
    try:
        PLATFORM_SCHEMAS[spec[CONF_PLATFORM]](body)
    except vol.Invalid as err:
        return str(err)
    return None


def _device_meta(device, row, area_reg) -> dict:
    """The mapping-form keys (cfg._device_block) a stand-in device carries."""
    meta = {}
    if device:
        meta = {key: str(value) for key, value in (
            ("manufacturer", device.manufacturer),
            ("model", device.model),
            ("sw_version", device.sw_version),
            ("configuration_url", device.configuration_url)) if value}
        mac = next((value for kind, value in device.connections
                    if kind == dr.CONNECTION_NETWORK_MAC), None)
        if mac:
            meta["mac"] = mac
    area_id = device.area_id if device else (row.area_id if row else None)
    area = area_reg.async_get_area(area_id) if area_id else None
    if area:
        meta["area"] = area.name
    return meta


def _collect(hass: HomeAssistant, data: dict) -> tuple[dict, list]:
    """(devices for the file, skipped) from the registries and live states.

    Reads only. Devices are bucketed by registry device id while collecting
    and get their display names, made unique, at the end.
    """
    ent_reg, dev_reg, area_reg = er.async_get(hass), dr.async_get(hass), ar.async_get(hass)
    excluded = set(data[CONF_EXCLUDE_INTEGRATIONS])
    patterns = data[CONF_EXCLUDE_ENTITIES]
    device_patterns = data[CONF_EXCLUDE_DEVICES]
    # A whole device out is ONE report line, not one per entity: a network
    # integration's unknown clients run to hundreds of entities.
    excluded_devices = Counter()
    domains = set(data[CONF_INCLUDE_DOMAINS])
    skipped = []

    def skip(entity_id, reason):
        skipped.append({ATTR_ENTITY_ID: entity_id, "reason": reason})

    # Every entity there is: registry rows, plus states with no row (yaml
    # entities with no unique_id). The latter carry no integration name, so
    # only exclude_entities can filter them.
    candidates = [(row, hass.states.get(row.entity_id))
                  for row in list(ent_reg.entities.values())]
    candidates += [(None, state) for state in hass.states.async_all()
                   if ent_reg.async_get(state.entity_id) is None]

    buckets = {}  # registry device id, or entity id -> label, meta, entities
    for row, state in candidates:
        entity_id = row.entity_id if row else state.entity_id
        domain = entity_id.split(".", 1)[0]
        if domain not in REPRODUCIBLE_DOMAINS:
            skip(entity_id, NOT_REPRODUCIBLE)
            continue
        if domain not in domains:
            skip(entity_id, "domain not included")
            continue
        if row and row.disabled:
            skip(entity_id, "disabled")
            continue
        if row and row.hidden and data[CONF_SKIP_HIDDEN]:
            skip(entity_id, "hidden")
            continue
        if row and row.platform in excluded:
            skip(entity_id, f"integration {row.platform} excluded")
            continue
        if any(fnmatch.fnmatchcase(entity_id, pattern) for pattern in patterns):
            skip(entity_id, "matched exclude_entities")
            continue
        device = dev_reg.async_get(row.device_id) if row and row.device_id else None
        label = (device.name_by_user or device.name) if device else None
        if label and any(fnmatch.fnmatchcase(label, p) for p in device_patterns):
            excluded_devices[label] += 1
            continue

        spec = _entity_export(hass, row, state, data.get(CONF_PERSISTENT))
        problem = _validate(spec)
        if problem:
            skip(entity_id, f"failed validation: {problem}")
            continue

        bucket = buckets.setdefault(device.id if device else entity_id, {
            "label": label or spec[CONF_NAME],
            "meta": _device_meta(device, row, area_reg),
            ATTR_ENTITIES: [],
        })
        bucket[ATTR_ENTITIES].append(spec)

    skipped += [{"device": label, "reason": f"matched exclude_devices ({count} entities)"}
                for label, count in sorted(excluded_devices.items())]

    devices, seen = {}, {}
    for bucket in buckets.values():
        label = bucket["label"] or "Device"
        key = unique_in_device(seen, label)
        if key != label:
            skipped.append({"device": label,
                            "reason": f"name already used, written as '{key}'"})
        devices[key] = ({**bucket["meta"], ATTR_ENTITIES: bucket[ATTR_ENTITIES]}
                        if bucket["meta"] else bucket[ATTR_ENTITIES])
    return devices, skipped


def _report(skipped: list) -> list:
    """Individual decisions as they are, and ONE line per non-reproducible
    domain rather than one per entity: a live HA has hundreds of automations,
    scripts and updates, and listing each would bury the rows that matter."""
    counts = Counter(item[ATTR_ENTITY_ID].split(".", 1)[0]
                     for item in skipped if item["reason"] == NOT_REPRODUCIBLE)
    report = [item for item in skipped if item["reason"] != NOT_REPRODUCIBLE]
    report += [{"domain": domain, "count": count, "reason": NOT_REPRODUCIBLE}
               for domain, count in sorted(counts.items())]
    return report


def _header() -> str:
    return (f"# Exported by Virtual Test Devices {__version__} from Home Assistant "
            f"{HA_VERSION} on {dt_util.now().isoformat(timespec='seconds')}.\n"
            "# Load this as a virtual group's devices file on another Home Assistant:\n"
            "# every entity keeps the id it has here.\n")


def _report_comment(report: list) -> str:
    if not report:
        return "\n# Skipped: nothing.\n"
    lines = ["", "# Skipped:"]
    for item in report:
        what = (item.get(ATTR_ENTITY_ID) or item.get("device")
                or f"{item['count']} x {item['domain']}.*")
        lines.append(f"#   {what}: {item['reason']}")
    return "\n".join(lines) + "\n"


def _may_write(hass: HomeAssistant, path: str) -> bool:
    """Inside the config folder, or somewhere HA lets integrations write.

    HA's own is_allowed_path does NOT cover the config folder itself: by
    default it allows only <config>/www and the media folders. Checking that
    alone refused the default <config>/virtual_export.yaml on a real install
    (2026-10-14). The config folder is where this integration keeps its
    devices file anyway. Blocking I/O: call it in the executor.
    """
    config_dir = pathlib.Path(hass.config.config_dir).resolve()
    if pathlib.Path(path).resolve().is_relative_to(config_dir):
        return True
    return hass.config.is_allowed_path(path)


async def _async_export_devices(hass: HomeAssistant, call) -> dict:
    path = hass.config.path(call.data.get(CONF_FILE) or DEFAULT_FILE)
    if not await hass.async_add_executor_job(_may_write, hass, path):
        raise ServiceValidationError(
            f"virtual: {path} is outside the config folder and allowlist_external_dirs")

    devices, skipped = _collect(hass, call.data)
    report = _report(skipped)
    # Serialise first, then write atomically: a value yaml cannot represent
    # raises here with any existing file still intact (see cfg._atomic_write).
    text = _header() + dump({ATTR_VERSION: 1, ATTR_DEVICES: devices}) + _report_comment(report)
    await _atomic_write(path, text)

    count = sum(len(d[ATTR_ENTITIES] if isinstance(d, dict) else d) for d in devices.values())
    _LOGGER.info("virtual: exported %d entities in %d devices to %s (%d skipped)",
                 count, len(devices), path, len(skipped))
    return {CONF_FILE: path, ATTR_DEVICES: len(devices), ATTR_ENTITIES: count,
            "skipped": report}


@callback
def async_register_export_service(hass: HomeAssistant) -> None:
    """Register export_devices once (idempotent)."""
    if hass.services.has_service(COMPONENT_DOMAIN, SERVICE_EXPORT_DEVICES):
        return

    async def export_devices(call):
        return await _async_export_devices(hass, call)

    hass.services.async_register(
        COMPONENT_DOMAIN, SERVICE_EXPORT_DEVICES, export_devices,
        schema=EXPORT_SCHEMA, supports_response=SupportsResponse.OPTIONAL)
    _LOGGER.debug("virtual: registered export_devices")
