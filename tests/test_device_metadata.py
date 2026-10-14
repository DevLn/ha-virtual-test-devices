"""A device in the file can say what it is.

`Name: [entities]` keeps working. `Name: {manufacturer, model, sw_version, area,
entities}` also loads, and what it says reaches the device registry instead of
the fixed "twrecked / virtual" constants, so a stand-in's device page reads like
the real device's. Everything that rewrites the file (create, remove, clone)
must leave a mapping-form device exactly as it found it.
"""

import homeassistant.helpers.device_registry as dr
import homeassistant.helpers.entity_registry as er

from conftest import FakeEntry, FakeHass, run

from custom_components.virtual import (
    __version__,
    _async_get_or_create_virtual_device_in_registry,
)
from custom_components.virtual.cfg import BlendedCfg, _load_user_data, _save_user_data
from custom_components.virtual.clone import _async_clone_device
from custom_components.virtual.const import COMPONENT_MANUFACTURER, COMPONENT_MODEL
from custom_components.virtual.pistoncore_manage import (
    _async_create_device,
    _async_remove_device,
)

GROUP = "Test Group"
LAMP = {"platform": "light", "name": "Lamp", "entity_id": "light.kitchen"}
HALL = [{"platform": "switch", "name": "Plug", "entity_id": "switch.hall"}]
KITCHEN = {
    "manufacturer": "Shelly", "model": "Plus 1",
    "sw_version": 1.2,          # yaml reads it as a float; the registry wants str
    "area": "Kitchen",
    "mac": "58:8c:81:a5:08:a0",
    "configuration_url": "http://kitchen.local",
    "entities": [LAMP],
}


class FakeCall:
    def __init__(self, **data):
        self.data = data


def _bench(tmp_path, devices):
    hass = FakeHass(config_dir=tmp_path)
    entry = FakeEntry(tmp_path / "virtual.yaml", group_name=GROUP)
    hass.config_entries.entries = [entry]
    run(_save_user_data(entry.data["file_name"], devices))
    return hass, entry


def _load(hass, entry):
    cfg = BlendedCfg(hass, entry.data)
    run(cfg.async_load())
    return cfg


def _file(entry):
    return run(_load_user_data(entry.data["file_name"]))


def test_list_form_loads_as_before(tmp_path):
    hass, entry = _bench(tmp_path, {"Kitchen": [LAMP]})

    cfg = _load(hass, entry)

    assert cfg.devices == [{"device_id": "Kitchen", "name": "Kitchen"}]
    assert [e["entity_id"] for e in cfg.entities["light"]] == ["light.kitchen"]


def test_mapping_form_loads_entities_and_metadata(tmp_path):
    hass, entry = _bench(tmp_path, {"Kitchen": KITCHEN})

    cfg = _load(hass, entry)

    assert cfg.devices == [{
        "device_id": "Kitchen", "name": "Kitchen",
        "manufacturer": "Shelly", "model": "Plus 1", "sw_version": "1.2",
        "area": "Kitchen", "mac": "58:8c:81:a5:08:a0",
        "configuration_url": "http://kitchen.local",
    }]
    assert [e["entity_id"] for e in cfg.entities["light"]] == ["light.kitchen"]
    assert cfg.entities["light"][0]["device_id"] == "Kitchen"


def test_name_is_shown_and_the_key_only_identifies(tmp_path):
    """An export keys devices by live registry id; `name` is what people see.
    Two devices may share a name, as two integrations' copies of one access
    point do on live."""
    hass, entry = _bench(tmp_path, {
        "4f1c9e2b": {"name": "ap-ln.omada", "entities": [LAMP]},
        "9a8b7c6d": {"name": "ap-ln.omada", "entities": HALL},
    })

    cfg = _load(hass, entry)

    assert [(d["device_id"], d["name"]) for d in cfg.devices] == \
        [("4f1c9e2b", "ap-ln.omada"), ("9a8b7c6d", "ap-ln.omada")]
    assert cfg.entities["light"][0]["device_id"] == "4f1c9e2b"


def test_both_forms_in_one_file(tmp_path):
    hass, entry = _bench(tmp_path, {"Kitchen": KITCHEN, "Hall": HALL})

    cfg = _load(hass, entry)

    assert {d["device_id"] for d in cfg.devices} == {"Kitchen", "Hall"}
    assert set(cfg.entities) == {"light", "switch"}


def test_metadata_reaches_the_device_registry(tmp_path):
    hass, entry = _bench(tmp_path, {"Kitchen": KITCHEN, "Hall": HALL})
    cfg = _load(hass, entry)

    for device in cfg.devices:
        run(_async_get_or_create_virtual_device_in_registry(hass, entry, device))

    rows = {r.name: r for r in dr.async_get(hass)._devices.values()}
    k, h = rows["Kitchen"], rows["Hall"]
    assert (k.manufacturer, k.model, k.sw_version, k.suggested_area) == \
        ("Shelly", "Plus 1", "1.2", "Kitchen")
    assert (h.manufacturer, h.model, h.sw_version, h.suggested_area) == \
        (COMPONENT_MANUFACTURER, COMPONENT_MODEL, __version__, None), \
        "a list-form device must get exactly what it always got"
    assert k.connections == {("mac", "58:8c:81:a5:08:a0")}
    assert k.configuration_url == "http://kitchen.local"
    assert not hasattr(h, "connections") or not h.connections


def test_an_unusable_configuration_url_does_not_take_the_device_down(tmp_path, caplog):
    hass, entry = _bench(tmp_path, {})
    calls = []
    real = dr.async_get(hass).async_get_or_create

    def strict(**fields):
        calls.append(dict(fields))
        if "configuration_url" in fields:
            raise ValueError("invalid configuration_url 'ftp://x'")
        return real(**fields)

    dr.async_get(hass).async_get_or_create = strict
    run(_async_get_or_create_virtual_device_in_registry(hass, entry, {
        "device_id": "D", "name": "D", "configuration_url": "ftp://x"}))

    assert len(calls) == 2 and "configuration_url" not in calls[1]
    assert any("ftp://x" in r.message for r in caplog.records)


def test_create_remove_and_clone_leave_a_mapping_device_alone(tmp_path):
    hass, entry = _bench(tmp_path, {"Kitchen": KITCHEN})
    expected = _file(entry)["Kitchen"]          # as yaml round-trips it

    run(_async_create_device(hass, FakeCall(device_name="Hall", entities=HALL)))
    assert _file(entry)["Kitchen"] == expected

    run(_async_remove_device(hass, FakeCall(device_name="Hall")))
    assert _file(entry)["Kitchen"] == expected

    real = dr.async_get(hass).add("Real Lamp", {("shelly", "abc")}, config_entry_id="shelly")
    er.async_get(hass).add("light.real_lamp", "abc-light",
                           config_entry_id="shelly", device_id=real.id)
    hass.states.set("light.real_lamp", "on",
                    {"friendly_name": "Real Lamp", "supported_color_modes": ["brightness"]})
    run(_async_clone_device(hass, FakeCall(device_id=real.id)))

    devices = _file(entry)
    assert devices["Kitchen"] == expected
    assert "Test — Real Lamp" in devices, "the clone itself must still have been written"
