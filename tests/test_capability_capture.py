"""What a clone copies.

REGRESSION GUARD for the rule that makes cloning worth anything: the capture list
comes from HOME ASSISTANT's own `capability_attributes`, never from whatever
devices happen to be on one install. Deriving it from one house silently dropped
`target_humidity_step` and `swing_horizontal_modes`, because nothing in that
house reported them.

Also guards the two things that broke real files: a non-primitive reaching the
config (an HA enum emptied the device file), and the closed-schema contract (a
key the platform doesn't declare fails entity creation outright).
"""

import pytest

from custom_components.virtual import clone
from custom_components.virtual.clone import CLONE_ATTRS, CLONE_FEATURES, _plain


class FakeUnits:
    temperature_unit = "°C"


class FakeConfig:
    units = FakeUnits()


class FakeHass:
    config = FakeConfig()


HASS = FakeHass()


# ── the capture list must match HA's own definition ─────────────────────────

def _ha_capability_attributes(domain, entity_class):
    """The attribute names HA's own `capability_attributes` property produces.

    HA names them two ways and BOTH must be resolved, or this quietly returns
    nothing and the test skips — proving less than no test at all:
      * plain module constants, `ATTR_HVAC_MODES`
      * enum members, `ClimateEntityCapabilityAttribute.HVAC_MODES` (the current
        style, and the one that made this return empty at first)
    """
    import ast
    import enum
    import importlib
    import inspect

    module = importlib.import_module(f"homeassistant.components.{domain}")
    prop = getattr(getattr(module, entity_class), "capability_attributes", None)
    assert prop is not None, f"{entity_class} has no capability_attributes"
    fn = (getattr(prop, "fget", None) or getattr(prop, "func", None)
          or getattr(prop, "__wrapped__", None) or prop)
    source = inspect.getsource(fn)

    try:
        const = importlib.import_module(f"homeassistant.components.{domain}.const")
    except ImportError:
        const = module

    def resolve(name):
        return getattr(const, name, None) or getattr(module, name, None)

    def as_name(node):
        """Resolve one key expression to the string it produces, or None."""
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.Name):
            value = resolve(node.id)
            return value if isinstance(value, str) else None
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            owner = resolve(node.value.id)
            member = getattr(owner, node.attr, None) if owner is not None else None
            if isinstance(member, enum.Enum) and isinstance(member.value, str):
                return member.value
            return member if isinstance(member, str) else None
        return None

    # ONLY things that actually become KEYS of the returned dict. Collecting
    # every enum member mentioned anywhere instead picks up `ColorMode.COLOR_TEMP`
    # from an `if` and then demands we capture a colour mode as if it were a
    # capability attribute.
    found = set()
    tree = ast.parse(inspect.cleandoc(source))
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):                       # {KEY: value, ...}
            for key in node.keys:
                name = as_name(key)
                if name:
                    found.add(name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):  # data[KEY] = value
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Subscript):
                    name = as_name(target.slice)
                    if name:
                        found.add(name)

    assert found, (f"read no capability attributes out of "
                   f"{domain}.{entity_class} — the extractor is broken, not the code")
    return found


# Domains whose capability attributes we claim to copy in full.
@pytest.mark.parametrize("domain,entity_class", [
    ("climate", "ClimateEntity"),
    ("media_player", "MediaPlayerEntity"),
    ("vacuum", "StateVacuumEntity"),
    ("siren", "SirenEntity"),
    ("humidifier", "HumidifierEntity"),
    ("light", "LightEntity"),
    ("select", "SelectEntity"),
    ("text", "TextEntity"),
])
def test_capture_list_covers_what_ha_calls_a_capability(domain, entity_class):
    ha_attrs = _ha_capability_attributes(domain, entity_class)
    ours = set(CLONE_ATTRS.get(domain, ()))
    # Captured by other means, not via the per-domain table.
    ours |= {"supported_features", "device_class", "unit_of_measurement",
             "friendly_name", "state_class", "preset_modes", "percentage_step"}
    missing = sorted(a for a in ha_attrs - ours if not a.startswith("_"))
    assert not missing, (
        f"{domain}: HA calls these capability attributes but a clone drops them: "
        f"{missing} — add them to CLONE_ATTRS *and* to the platform's schema")


# ── nothing but primitives may reach the device file ────────────────────────

def test_plain_reduces_enums():
    """An HA enum in the config emptied the device file once. Never again."""
    from homeassistant.const import UnitOfTemperature
    assert _plain(UnitOfTemperature.CELSIUS) == "°C"
    assert isinstance(_plain(UnitOfTemperature.CELSIUS), str)
    assert type(_plain(UnitOfTemperature.CELSIUS)) is str


def test_plain_handles_containers():
    from homeassistant.components.light import ColorMode
    assert _plain([ColorMode.HS, ColorMode.RGB]) == ["hs", "rgb"]
    assert _plain({"a": ColorMode.HS}) == {"a": "hs"}
    assert _plain(("x", 1, True, None)) == ["x", 1, True, None]


def test_capture_output_is_yaml_safe():
    """Everything capability_config emits must survive a yaml round trip."""
    from homeassistant.util.yaml import dump
    from homeassistant.const import UnitOfTemperature
    attrs = {"supported_features": 155,
             "hvac_modes": ["off", "heat"],
             "min_temp": 4.4,
             "temperature_unit": UnitOfTemperature.FAHRENHEIT}
    spec = clone.capability_config(HASS, "climate", attrs)
    dump({"devices": {"X": [spec]}})          # raises if a non-primitive slipped in
    for key, value in spec.items():
        assert isinstance(value, (str, int, float, bool, list, dict)), (key, value)


# ── the closed-schema contract ──────────────────────────────────────────────

def _schema_keys(platform):
    """Keys the platform's voluptuous schema actually declares."""
    import ast
    import pathlib
    path = (pathlib.Path(clone.__file__).parent / f"{platform}.py")
    names = {"CONF_UNIT_OF_MEASUREMENT": "unit_of_measurement"}
    for source_path in (path, path.parent / "const.py"):
        for node in ast.walk(ast.parse(source_path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) \
                    and isinstance(node.value.value, str):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        names[target.id] = node.value.value
    keys = {"name", "initial_value", "initial_availability", "persistent",
            "device_id", "entity_id", "unique_id"}
    text = path.read_text(encoding="utf-8")
    if "FEATURES_SCHEMA" in text:
        keys.add("supported_features")
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr in ("Optional", "Required") and node.args:
            arg = node.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                keys.add(arg.value)
            elif isinstance(arg, ast.Name) and arg.id in names:
                keys.add(names[arg.id])
    return keys


@pytest.mark.parametrize("domain", sorted(set(CLONE_ATTRS) | CLONE_FEATURES))
def test_every_capturable_key_is_declared_by_its_platform(domain):
    """The platforms validate against CLOSED schemas: a key they don't declare
    fails entity creation outright, so the capture table and the schemas are one
    contract and must change together."""
    wanted = set(CLONE_ATTRS.get(domain, ()))
    if domain in CLONE_FEATURES:
        wanted.add("supported_features")
    wanted |= {"climate": {"temperature_unit"},
               "fan": {"modes", "speed_count"},
               "media_player": {"class"}}.get(domain, set())
    missing = sorted(wanted - _schema_keys(domain))
    assert not missing, (
        f"{domain}: capture emits {missing} but {domain}.py does not declare them — "
        f"entity creation would fail")


# ── menus and features stay consistent ──────────────────────────────────────

def test_absent_attributes_are_not_invented():
    """An attribute the original didn't report must stay absent, so the platform
    keeps its own default instead of being pinned to a guess."""
    spec = clone.capability_config(HASS, "vacuum", {"supported_features": 4})
    assert "fan_speed_list" not in spec


def test_fan_speed_count_derives_from_percentage_step():
    spec = clone.capability_config(HASS, "fan", {"percentage_step": 20.0})
    assert spec["speed_count"] == 5
    spec = clone.capability_config(HASS, "fan", {"percentage_step": 33.333333})
    assert spec["speed_count"] == 3


# ── the platform lists must agree ───────────────────────────────────────────

def test_every_platform_can_be_cloned():
    """A platform this integration provides must also be one a clone can copy.

    ADDING A PLATFORM TOUCHES THREE LISTS: `VIRTUAL_PLATFORMS` in __init__.py,
    `REPRODUCIBLE_DOMAINS` here, and `_VIRTUAL_PLATFORM_DOMAINS` in PistonCore.
    When the camera platform was added, two of the three were updated — so
    PistonCore offered cameras as cloneable while clone_device rejected them
    with "has nothing this integration can reproduce". Nothing caught it,
    because nothing compared the lists.

    The PistonCore copy lives in another repo and cannot be imported from here,
    so this guards the two that ship together; PistonCore's own list is checked
    against the add-on's on its side.
    """
    import pathlib
    import re
    integration = pathlib.Path(clone.__file__).parent
    init = (integration / "__init__.py").read_text(encoding="utf-8")
    block = init.split("VIRTUAL_PLATFORMS", 1)[1].split("]", 1)[0]
    provided = {name.lower() for name in re.findall(r"Platform\.([A-Z_]+)", block)}
    assert provided, "could not read VIRTUAL_PLATFORMS — the check is broken, not the code"

    missing = sorted(provided - set(clone.REPRODUCIBLE_DOMAINS))
    assert not missing, (
        f"\n\nThese platforms are provided but a clone refuses them: {missing}\n"
        f"Add them to REPRODUCIBLE_DOMAINS in clone.py — and remember the third "
        f"list, _VIRTUAL_PLATFORM_DOMAINS in PistonCore's shim/routes/pages.py.\n")

    stale = sorted(set(clone.REPRODUCIBLE_DOMAINS) - provided)
    assert not stale, (
        f"\n\nclone.py claims it can reproduce {stale}, but this integration "
        f"provides no such platform — cloning one would create nothing.\n")


# ── credentials must never reach a clone ────────────────────────────────────

@pytest.mark.parametrize("domain,attrs", [
    # A Hubitat-bridged LOCK, attributes exactly as HA reports them.
    ("lock", {"supported_features": 1, "code_format": r"\d{4}",
              "codes": {"1": {"name": "Jeremy"}, "2": {"name": "Thea"}},
              "last_code_name": "Jeremy", "code_length": 4, "max_codes": 30}),
    # A Hubitat-bridged ALARM PANEL — this one carries plaintext PINs.
    ("alarm_control_panel", {"supported_features": 7, "code_format": "number",
                             "code_arm_required": False,
                             "codes": {"1": {"code": "2217", "name": "Jeremy"}},
                             "code_length": 4, "max_codes": 20}),
])
def test_clone_never_captures_codes_or_household_names(domain, attrs):
    """Bridged locks and alarm panels expose user code tables — sometimes
    plaintext PINs with the names they belong to. A clone is meant to be
    shareable in a bug report, so none of it may ever be captured.

    This holds today because the capture is limited to HA's capability
    attributes. It must keep holding: anyone widening CLONE_ATTRS past that has
    to reckon with this test.
    """
    spec = repr(clone.capability_config(HASS, domain, attrs))
    for secret in ("Jeremy", "Thea", "2217", "codes", "last_code_name"):
        assert secret not in spec, f"{domain} clone leaked {secret!r}: {spec}"


def test_number_always_gets_a_range():
    """min/max are REQUIRED by the number platform — without them a clone
    containing a number entity can never be created."""
    spec = clone.capability_config(HASS, "number", {})
    assert "min" in spec and "max" in spec
