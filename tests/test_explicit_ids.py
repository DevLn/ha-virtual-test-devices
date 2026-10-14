"""An entry that names its own entity_id IS that entity.

WHY. The point of a test bench is to run an automation unchanged against
stand-ins, and an automation names its entities by id. Identity here used to be
name-keyed with a random unique_id minted on first sight, so a file written on
one Home Assistant could never land on the same ids on another. Now an entry
with an explicit `entity_id` gets exactly that id, with a unique_id that is
either given or derived from the id — never random — so a regenerated file maps
onto the same registry rows every time.

Also guards the orphan rule that came with it: removing an entry from the file
removes THAT row and nothing else. The code this replaced removed the orphan's
whole device, which took the device's surviving entities with it.
"""

import json
import logging

import homeassistant.helpers.device_registry as dr
import homeassistant.helpers.entity_registry as er

from conftest import FakeEntry, FakeHass, run

from custom_components.virtual import _async_remove_orphans
from custom_components.virtual.cfg import BlendedCfg, _load_user_data, _save_user_data
from custom_components.virtual.const import COMPONENT_DOMAIN
from custom_components.virtual.pistoncore_manage import _async_create_device

GROUP = "Test Group"


class FakeCall:
    def __init__(self, **data):
        self.data = data


def _bench(tmp_path, devices):
    hass = FakeHass(config_dir=tmp_path)
    entry = FakeEntry(tmp_path / "virtual.yaml", group_name=GROUP)
    run(_save_user_data(entry.data["file_name"], devices))
    return hass, entry


def _load(hass, entry):
    cfg = BlendedCfg(hass, entry.data)
    run(cfg.async_load())
    return cfg


def _ids(cfg):
    """{entity_id: unique_id} of everything the load will provide."""
    return {e["entity_id"]: e["unique_id"]
            for entities in cfg.entities.values() for e in entities}


def _meta_rows(hass):
    """The group's rows in the meta file on disk, or {} if there is no file."""
    try:
        with open(hass.config.path(".storage/virtual.meta.json")) as f:
            return json.load(f)["devices"].get(GROUP, {})
    except FileNotFoundError:
        return {}


def _errors(caplog):
    return [r.message for r in caplog.records if r.levelno >= logging.ERROR]


# --- the old way keeps working ------------------------------------------------

def test_old_name_based_file_and_meta_load_unchanged(tmp_path):
    """The upgrade guard: a name-keyed file with settled meta is read, not rewritten."""
    hass, entry = _bench(tmp_path, {
        "Kitchen": [{"platform": "light", "name": "Kitchen Lamp"}],
    })
    meta_path = hass.config.path(".storage/virtual.meta.json")
    with open(meta_path, "w") as f:
        json.dump({"version": 1, "devices": {GROUP: {
            "Kitchen Lamp": {"unique_id": "1111.virtual",
                             "entity_id": "light.kitchen_lamp",
                             "device_id": "Kitchen"},
        }}}, f, indent=4)
    before = open(meta_path).read()

    assert _ids(_load(hass, entry)) == {"light.kitchen_lamp": "1111.virtual"}
    assert open(meta_path).read() == before, "a settled load must not touch the meta file"


# --- explicit ids ---------------------------------------------------------------

def test_explicit_id_is_used_and_stable_across_loads(tmp_path):
    hass, entry = _bench(tmp_path, {
        "Kitchen": [{"platform": "light", "name": "Kitchen Lamp",
                     "entity_id": "light.kitchen"}],
    })

    first = _ids(_load(hass, entry))

    assert first == {"light.kitchen": "light.kitchen"}
    assert first == _ids(_load(hass, entry)) == _ids(_load(hass, entry))
    assert "Kitchen Lamp" not in _meta_rows(hass), "an explicit id needs no meta row"


def test_explicit_unique_id_is_honoured(tmp_path):
    hass, entry = _bench(tmp_path, {
        "Kitchen": [{"platform": "light", "name": "Lamp",
                     "entity_id": "light.kitchen", "unique_id": "lamp-1"}],
    })
    assert _ids(_load(hass, entry)) == {"light.kitchen": "lamp-1"}


def test_same_object_id_in_two_domains(tmp_path):
    """light.kitchen and switch.kitchen, even with the same name, are two entities."""
    hass, entry = _bench(tmp_path, {
        "Kitchen": [
            {"platform": "light", "name": "Kitchen", "entity_id": "light.kitchen"},
            {"platform": "switch", "name": "Kitchen", "entity_id": "switch.kitchen"},
        ],
    })

    ids = _ids(_load(hass, entry))

    assert set(ids) == {"light.kitchen", "switch.kitchen"}
    assert len(set(ids.values())) == 2


def test_wrong_domain_is_refused_not_guessed(tmp_path, caplog):
    hass, entry = _bench(tmp_path, {
        "Kitchen": [
            {"platform": "switch", "name": "Bad", "entity_id": "light.kitchen"},
            {"platform": "switch", "name": "Good", "entity_id": "switch.kitchen"},
        ],
    })

    assert _ids(_load(hass, entry)) == {"switch.kitchen": "switch.kitchen"}
    assert any("light.kitchen" in m for m in _errors(caplog)), "a skip must be loud"


def test_duplicate_explicit_id_keeps_the_first(tmp_path, caplog):
    hass, entry = _bench(tmp_path, {
        "A": [{"platform": "light", "name": "First", "entity_id": "light.x"}],
        "B": [{"platform": "light", "name": "Second", "entity_id": "light.x"}],
    })

    cfg = _load(hass, entry)

    assert [e["name"] for e in cfg.entities["light"]] == ["First"]
    assert any("light.x" in m for m in _errors(caplog))


# --- removal takes one row, not the device --------------------------------------

def _register_everything(hass, cfg, device_name):
    """Put the loaded group into the (fake) registries the way a run leaves it."""
    device = dr.async_get(hass).add(device_name, {(COMPONENT_DOMAIN, device_name)})
    for entities in cfg.entities.values():
        for e in entities:
            er.async_get(hass).add(e["entity_id"], e["unique_id"], device_id=device.id)
    return device


def test_removing_one_entity_keeps_its_siblings_and_device(tmp_path):
    """Explicit and name-keyed entries alike: one rule, one row."""
    lamp = {"platform": "light", "name": "Lamp", "entity_id": "light.kitchen"}
    hass, entry = _bench(tmp_path, {"Kitchen": [
        lamp,
        {"platform": "switch", "name": "Plug", "entity_id": "switch.kitchen"},
        {"platform": "sensor", "name": "Kitchen Temp"},        # name-keyed
    ]})
    device = _register_everything(hass, _load(hass, entry), "Kitchen")
    ent_reg, dev_reg = er.async_get(hass), dr.async_get(hass)
    assert set(ent_reg.entities) == {"light.kitchen", "switch.kitchen", "sensor.kitchen_temp"}

    run(_save_user_data(entry.data["file_name"], {"Kitchen": [lamp]}))
    _async_remove_orphans(hass, entry, _load(hass, entry))

    assert set(ent_reg.entities) == {"light.kitchen"}
    assert dev_reg.async_get(device.id) is not None, (
        "the device still has an entity in the file, so it must survive untouched")


def test_a_device_with_nothing_left_is_removed(tmp_path):
    kitchen = {"Kitchen": [{"platform": "light", "name": "Lamp", "entity_id": "light.kitchen"}]}
    hass, entry = _bench(tmp_path, {
        **kitchen,
        "Hall": [{"platform": "switch", "name": "Plug", "entity_id": "switch.hall"}],
    })
    cfg = _load(hass, entry)
    dev_reg = dr.async_get(hass)
    kept = dev_reg.add("Kitchen", {(COMPONENT_DOMAIN, "Kitchen")})
    gone = dev_reg.add("Hall", {(COMPONENT_DOMAIN, "Hall")})
    ent_reg = er.async_get(hass)
    ent_reg.add("light.kitchen", "light.kitchen", device_id=kept.id)
    ent_reg.add("switch.hall", "switch.hall", device_id=gone.id)

    run(_save_user_data(entry.data["file_name"], kitchen))
    _async_remove_orphans(hass, entry, _load(hass, entry))

    assert set(ent_reg.entities) == {"light.kitchen"}
    assert dev_reg.async_get(kept.id) is not None
    assert dev_reg.async_get(gone.id) is None


# --- the file rewriters pass the keys through ------------------------------------

def test_create_device_keeps_explicit_keys(tmp_path):
    hass, entry = _bench(tmp_path, {})
    hass.config_entries.entries = [entry]

    run(_async_create_device(hass, FakeCall(device_name="Kitchen", entities=[
        {"platform": "light", "name": "Lamp",
         "entity_id": "light.kitchen", "unique_id": "lamp-1"},
    ])))

    written = run(_load_user_data(entry.data["file_name"]))["Kitchen"][0]
    assert written["entity_id"] == "light.kitchen" and written["unique_id"] == "lamp-1"
    assert _ids(_load(hass, entry)) == {"light.kitchen": "lamp-1"}
