"""Constants for the virtual component. """

COMPONENT_DOMAIN = "virtual"
COMPONENT_SERVICES = "virtual-services"
COMPONENT_CONFIG = "virtual-config"
COMPONENT_MANUFACTURER = "twrecked"
COMPONENT_MODEL = "virtual"

ATTR_AVAILABLE = 'available'
ATTR_DEVICES = "devices"
ATTR_DEVICE_ID = "device_id"
ATTR_ENTITIES = "entities"
ATTR_FILE_NAME = "file_name"
ATTR_GROUP_NAME = "group_name"
ATTR_PARENT_ID = "parent_id"
ATTR_PERSISTENT = 'persistent'
ATTR_UNIQUE_ID = 'unique_id'
ATTR_VALUE = "value"
ATTR_VERSION = "version"

CONF_CLASS = "class"
CONF_INITIAL_AVAILABILITY = "initial_availability"
CONF_INITIAL_VALUE = "initial_value"
CONF_MAX = "max"
CONF_MIN = "min"
CONF_NAME = "name"
CONF_OPEN_CLOSE_DURATION = "open_close_duration"
CONF_OPEN_CLOSE_TICK = "open_close_tick"
CONF_PERSISTENT = "persistent"
CONF_YAML_CONFIG = "yaml_config"
# PistonCore (export.py): `virtual: {export_only: true}` makes a LIVE Home
# Assistant provide only the export action — no groups, entities or create/clone.
CONF_EXPORT_ONLY = "export_only"

# PistonCore full-fidelity cloning (FORK_NOTES.md). A real entity advertises its
# abilities as HA's `supported_features` bitmask; a faithful clone takes that
# number VERBATIM rather than guessing from booleans. Every platform accepts it
# and falls back to its own sensible default when it isn't given, so nothing
# that worked before this change behaves differently.
CONF_SUPPORTED_FEATURES = "supported_features"

DEFAULT_AVAILABILITY = True
DEFAULT_PERSISTENT = True

IMPORTED_GROUP_NAME = "imported"


def default_config_file(hass) -> str:
    return hass.config.path("virtual.yaml")


def default_meta_file(hass) -> str:
    return hass.config.path(".storage/virtual.meta.json")
