"""
This component provides support for virtual components.

"""

import logging
import voluptuous as vol
import asyncio

import homeassistant.helpers.config_validation as cv
import homeassistant.helpers.device_registry as dr
import homeassistant.helpers.entity_registry as er
from homeassistant.helpers.config_validation import PLATFORM_SCHEMA
from homeassistant.helpers.issue_registry import (
    async_create_issue,
    IssueSeverity
)
from homeassistant.helpers.service import verify_domain_control
from homeassistant.helpers.typing import ConfigType
from homeassistant.config_entries import ConfigEntry, SOURCE_IMPORT
from homeassistant.const import (
    ATTR_ENTITY_ID, CONF_SOURCE, MAJOR_VERSION, MINOR_VERSION, Platform
)
from homeassistant.core import (
    DOMAIN as HOMEASSISTANT_DOMAIN,
    HomeAssistant,
    callback
)
from homeassistant.exceptions import ConfigEntryError, ServiceValidationError

from .const import *
from .cfg import BlendedCfg, UpgradeCfg


__version__ = '0.14.0-hts.5'

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = vol.Schema({
        COMPONENT_DOMAIN: vol.Schema({
            vol.Optional(CONF_YAML_CONFIG, default=False): cv.boolean,
            vol.Optional(CONF_EXPORT_ONLY, default=False): cv.boolean,
        }),
    },
    extra=vol.ALLOW_EXTRA,
)

SERVICE_AVAILABILE = 'set_available'
SERVICE_SCHEMA = vol.Schema({
    vol.Required(ATTR_ENTITY_ID): cv.comp_entity_ids,
    vol.Required('value'): cv.boolean,
})

VIRTUAL_PLATFORMS = [
    Platform.ALARM_CONTROL_PANEL,  # PistonCore addition (FORK_NOTES.md)
    Platform.BINARY_SENSOR,
    Platform.BUTTON,               # PistonCore addition
    Platform.CAMERA,               # PistonCore addition (serves real image bytes,
                                   # so HA's own camera.snapshot writes a real file)
    Platform.CLIMATE,              # PistonCore addition
    Platform.COVER,
    Platform.DEVICE_TRACKER,
    Platform.EVENT,                # PistonCore addition (button / scene / multi-tap)
    Platform.FAN,
    Platform.HUMIDIFIER,           # PistonCore addition
    Platform.LIGHT,
    Platform.LOCK,
    Platform.MEDIA_PLAYER,
    Platform.NOTIFY,               # PistonCore addition (somewhere for a
                                   # piston's notifications to land)
    Platform.NUMBER,
    Platform.SELECT,               # PistonCore addition
    Platform.SENSOR,
    Platform.SIREN,                # PistonCore addition
    Platform.SWITCH,
    Platform.TEXT,                 # PistonCore addition
    Platform.VACUUM,               # PistonCore addition
    Platform.VALVE
]


def str_to_bool(value) -> bool:
    value = value.lower()
    if value in ["y", "yes", "t", "true", "on", "1"]:
        return True
    if value in ["n", "no", "f", "false", "off", "0"]:
        return False
    raise ValueError


async def async_setup(hass, config):
    """Set up a virtual components.

    This uses the old mechanism and has to be enabled with 'yaml_config: True'
    """

    # Set up hass data if necessary
    if COMPONENT_DOMAIN not in hass.data:
        hass.data[COMPONENT_DOMAIN] = {}
        hass.data[COMPONENT_SERVICES] = {}
        hass.data[COMPONENT_CONFIG] = {}

    # --- PistonCore: export the live entities as stand-ins (additive; export.py) ---
    # Registered here, not per group, because on a live HA there is no group:
    # with export_only this action is all the integration provides.
    from .export import async_register_export_service
    async_register_export_service(hass)

    export_only = config.get(COMPONENT_DOMAIN, {}).get(CONF_EXPORT_ONLY, False)
    hass.data[COMPONENT_CONFIG][CONF_EXPORT_ONLY] = export_only
    if export_only:
        _LOGGER.info("virtual: export_only is set, so only virtual.export_devices "
                     "is available here and no group will be set up")
        return True

    # See if yaml support was enabled.
    if not config.get(COMPONENT_DOMAIN, {}).get(CONF_YAML_CONFIG, False):

        # New style. We import old config if needed.
        _LOGGER.debug("setting up new virtual components")
        hass.data[COMPONENT_CONFIG][CONF_YAML_CONFIG] = False

        # See if we have already imported the data. If we haven't then do it now.
        config_entry = _async_find_matching_config_entry(hass)
        if not config_entry:
            _LOGGER.debug('importing a YAML setup')
            hass.async_create_task(
                hass.config_entries.flow.async_init(
                    COMPONENT_DOMAIN,
                    context={CONF_SOURCE: SOURCE_IMPORT},
                    data=config
                )
            )

            async_create_issue(
                hass,
                HOMEASSISTANT_DOMAIN,
                f"deprecated_yaml_{COMPONENT_DOMAIN}",
                is_fixable=False,
                issue_domain=COMPONENT_DOMAIN,
                severity=IssueSeverity.WARNING,
                translation_key="deprecated_yaml",
                translation_placeholders={
                    "domain": COMPONENT_DOMAIN,
                    "integration_title": "Virtual",
                },
            )

        else:
            _LOGGER.debug('ignoring a YAML setup')

    else:

        # Original style. We just use the entities as now.
        _LOGGER.debug("setting up old virtual components")
        hass.data[COMPONENT_CONFIG][CONF_YAML_CONFIG] = True

        @verify_domain_control(COMPONENT_DOMAIN)
        async def async_virtual_service_set_available(call) -> None:
            """Call virtual service handler."""
            _LOGGER.info("{} service called".format(call.service))
            await async_virtual_set_availability_service(hass, call)

        hass.services.async_register(COMPONENT_DOMAIN, SERVICE_AVAILABILE, async_virtual_service_set_available)

    return True


@callback
def _async_find_matching_config_entry(hass):
    """ If we have anything in config_entries for virtual we consider it
    configured and will ignore the YAML.
    """
    for entry in hass.config_entries.async_entries(COMPONENT_DOMAIN):
        return entry


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    _LOGGER.debug(f'async setup {entry.data}')

    # Set up hass data if necessary
    if COMPONENT_DOMAIN not in hass.data:
        hass.data[COMPONENT_DOMAIN] = {}
        hass.data[COMPONENT_SERVICES] = {}
        hass.data[COMPONENT_CONFIG] = {}

    # A live HA in export_only mode must not run groups (see export.py).
    if hass.data[COMPONENT_CONFIG].get(CONF_EXPORT_ONLY):
        raise ConfigEntryError(
            "export_only is set under virtual: in configuration.yaml, so no "
            "virtual group runs on this Home Assistant. Remove it to load groups here.")

    # Get the config.
    _LOGGER.debug(f"creating new cfg")
    vcfg = BlendedCfg(hass, entry.data)
    await vcfg.async_load()

    # create the devices.
    _LOGGER.debug("creating the devices")
    for device in vcfg.devices:
        _LOGGER.debug(f"creating-device={device}")
        await _async_get_or_create_virtual_device_in_registry(hass, entry, device)
    await asyncio.sleep(1)

    # Remove whatever the file no longer describes.
    _async_remove_orphans(hass, entry, vcfg)

    # Update the component data.
    hass.data[COMPONENT_DOMAIN].update({
        entry.data[ATTR_GROUP_NAME]: {
            ATTR_ENTITIES: vcfg.entities,
            ATTR_DEVICES: vcfg.devices,
            ATTR_FILE_NAME: entry.data[ATTR_FILE_NAME]
        }
    })
    _LOGGER.debug(f"update hass data {hass.data[COMPONENT_DOMAIN]}")

    # Create the entities.
    _LOGGER.debug("creating the entities")
    await hass.config_entries.async_forward_entry_setups(entry, VIRTUAL_PLATFORMS)

    # Install service handler.
    @verify_domain_control(COMPONENT_DOMAIN)
    async def async_virtual_service_set_available(call) -> None:
        """Call virtual service handler."""
        _LOGGER.info(f"{call.service} service called")
        await async_virtual_set_availability_service(hass, call)

    if not hasattr(hass.data[COMPONENT_SERVICES], COMPONENT_DOMAIN):
        _LOGGER.debug("installing handlers")
        hass.data[COMPONENT_SERVICES][COMPONENT_DOMAIN] = 'installed'
        hass.services.async_register(COMPONENT_DOMAIN, SERVICE_AVAILABILE,
                                     async_virtual_service_set_available, schema=SERVICE_SCHEMA)

    # --- PistonCore: live create/remove-device services (additive; FORK_NOTES.md) ---
    from .pistoncore_manage import async_register_manage_services
    async_register_manage_services(hass)

    # --- PistonCore: full-fidelity device cloning (additive; see clone.py) ---
    from .clone import async_register_clone_service
    async_register_clone_service(hass)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry.

    ONE PLATFORM MUST NOT BE ABLE TO STRAND THE OTHERS. This used to unload all
    the platforms as a single batch, which fails as a whole if any one of them
    fails. Seen on a bench 2026-08-29: a group whose devices were being changed
    reloaded, notify raised `Config entry was never loaded!`, the batch reported
    failure, and the entry was left half-down — all 21 entities sat in the
    registry as `restored`/`unavailable`, and nothing short of a full Home
    Assistant restart brought them back. Every device-driven test on that bench
    silently became meaningless, which is indistinguishable from a compiler bug.

    So each platform is unloaded on its own and a failure is logged and stepped
    over. Reporting success here is deliberate: it lets Home Assistant carry on
    and set the entry up again, which is what actually returns the devices. A
    platform that would not unload is a warning in the log, not a dead bench.
    """
    group_name = entry.data[ATTR_GROUP_NAME]
    _LOGGER.debug(f"unloading virtual group {group_name}")

    results = await asyncio.gather(
        *(hass.config_entries.async_forward_entry_unload(entry, platform)
          for platform in VIRTUAL_PLATFORMS),
        return_exceptions=True
    )
    for platform, result in zip(VIRTUAL_PLATFORMS, results):
        if result is not True:
            _LOGGER.warning(
                "virtual: %s did not unload cleanly (%s) — continuing so the "
                "rest of the group still comes back", platform, result)

    # Drop our own copy whatever happened, and tolerate it already being gone:
    # an unguarded pop here raised KeyError on a second unload, which is another
    # way the whole entry used to die.
    hass.data[COMPONENT_DOMAIN].pop(group_name, None)

    return True


def get_entity_configs(hass, group_name, domain):
    return hass.data.get(COMPONENT_DOMAIN, {}).get(group_name, {}).get(ATTR_ENTITIES, {}).get(domain, [])


def get_entity_from_domain(hass, domain, entity_id):
    """Find a live entity, or say plainly why it is not there.

    ServiceValidationError rather than HomeAssistantError: this is a bad request,
    not a crash, and Home Assistant reports the two differently — the first as a
    one-line message, the second as a 500 with a full traceback. A missing entity
    used to read like the integration had fallen over when the entity had simply
    never been provided, which sent a bench session hunting the wrong bug.
    """
    component = hass.data.get(domain)
    if component is None:
        raise ServiceValidationError(
            f"virtual: no {domain} entities exist, so {entity_id} cannot be driven")

    entity = component.get_entity(entity_id)
    if entity is None:
        raise ServiceValidationError(
            f"virtual: {entity_id} is not currently provided by this integration. "
            "It may still be in the entity registry from an earlier run — check "
            "that the Virtual Test Devices entry is loaded.")

    return entity


async def async_virtual_set_availability_service(hass, call):
    value = call.data['value']
    if type(value) is not bool:
        value = str_to_bool(value)

    for entity_id in call.data['entity_id']:
        domain = entity_id.split(".")[0]
        _LOGGER.info("{} set_avilable(value={})".format(entity_id, value))
        get_entity_from_domain(hass, domain, entity_id).set_available(value)


async def _async_get_or_create_virtual_device_in_registry(
        hass: HomeAssistant, entry: ConfigEntry, device
) -> None:
    registry = dr.async_get(hass)
    # Metadata from the file's mapping form (cfg._device_block) wins and the
    # constants are the fallback. `area` is a suggestion HA honours when the
    # device is first created and ignores afterwards, as for a real device, so
    # moving one later is done in the UI.
    fields = dict(
        config_entry_id=entry.entry_id,
        identifiers={(COMPONENT_DOMAIN, device[ATTR_DEVICE_ID])},
        manufacturer=device.get("manufacturer", COMPONENT_MANUFACTURER),
        model=device.get("model", COMPONENT_MODEL),
        name=device[CONF_NAME],
        sw_version=device.get("sw_version", __version__),
        suggested_area=device.get("area"),
    )
    if mac := device.get("mac"):
        # Since 2026.8 a device is matched by MAC only within its own config
        # entry, so a stand-in can carry the real device's MAC without being
        # merged into it. Before that the match spanned every integration: the
        # stand-in would have joined the REAL device, and removing it as an
        # orphan later would have deleted the real one. So older HA skips it.
        if (MAJOR_VERSION, MINOR_VERSION) >= (2026, 8):
            fields["connections"] = {(dr.CONNECTION_NETWORK_MAC, mac)}
        else:
            _LOGGER.warning("virtual: not giving %s its MAC %s: before Home "
                            "Assistant 2026.8 that would merge it with any real "
                            "device that has the same MAC", device[CONF_NAME], mac)
    if url := device.get("configuration_url"):
        fields["configuration_url"] = url
    try:
        registry.async_get_or_create(**fields)
    except ValueError as err:
        # An unusable configuration_url is the one value HA refuses outright,
        # and refusing it would take the whole group down. Drop it and say so.
        if fields.pop("configuration_url", None) is None:
            raise
        _LOGGER.error("virtual: %s: %s; creating the device without it",
                      device[CONF_NAME], err)
        registry.async_get_or_create(**fields)


@callback
def _async_remove_orphans(hass: HomeAssistant, entry: ConfigEntry, vcfg) -> None:
    """Remove the registry rows the group's file no longer describes.

    The file is the source of truth: anything registered under this config
    entry that it does not list is an orphan — an entity row whose unique_id is
    not about to be provided, or a device whose identifier is not in the file.

    Entities go one row at a time. The code this replaces removed the orphan's
    whole DEVICE, which also took the device's surviving entities with it and
    recreated the device under a new id at platform setup, losing its area and
    customisations and breaking anything that referred to it by device id
    (PistonCore, 2026-10-14). A device now only goes when the file no longer
    has it, and a first load also clears the dead rows left behind by the
    duplicate-name bug that unique_in_device() fixed.
    """
    keep_ids = {e[ATTR_UNIQUE_ID]
                for entities in vcfg.entities.values() for e in entities}
    ent_reg = er.async_get(hass)
    for row in er.async_entries_for_config_entry(ent_reg, entry.entry_id):
        if row.unique_id not in keep_ids:
            _LOGGER.info("virtual: removing orphaned entity %s", row.entity_id)
            ent_reg.async_remove(row.entity_id)

    keep_devices = {(COMPONENT_DOMAIN, d[ATTR_DEVICE_ID]) for d in vcfg.devices}
    dev_reg = dr.async_get(hass)
    for device in dr.async_entries_for_config_entry(dev_reg, entry.entry_id):
        if not device.identifiers & keep_devices:
            _LOGGER.info("virtual: removing orphaned device %s", device.name)
            dev_reg.async_remove_device(device.id)
