"""What leaves the live Home Assistant, and what must not.

virtual.export_devices writes the live entities as stand-ins that carry the live
entity ids. These hold down: what is exported and how it is grouped, every
filter, how a state becomes a starting state, that an entry the target platform
would reject goes to the report and never to the file, that no lock or alarm
code survives, that the file loads back under the same ids, and that
export_only mode offers this one action and nothing else.
"""

import pathlib

import pytest

import homeassistant.helpers.area_registry as ar
import homeassistant.helpers.device_registry as dr
import homeassistant.helpers.entity_registry as er
from homeassistant.exceptions import ConfigEntryError, ServiceValidationError

from conftest import FakeEntry, FakeHass, run

from custom_components.virtual import async_setup, async_setup_entry
from custom_components.virtual.cfg import BlendedCfg, _load_user_data
from custom_components.virtual.clone import REPRODUCIBLE_DOMAINS
from custom_components.virtual.config_flow import VirtualFlowHandler
from custom_components.virtual.const import COMPONENT_CONFIG, CONF_EXPORT_ONLY
from custom_components.virtual.export import (
    EXPORT_SCHEMA,
    PLATFORM_SCHEMAS,
    _async_export_devices,
)


class FakeCall:
    def __init__(self, **data):
        self.data = EXPORT_SCHEMA(data)     # defaults applied, as HA does


def _live(hass, entity_id, state, attrs=None, *, device=None, integration="shelly", **row):
    """One live entity: a registry row (unless integration is None) plus a state."""
    if integration is not None:
        er.async_get(hass).add(entity_id, f"uid-{entity_id}", config_entry_id=integration,
                               device_id=device.id if device else None,
                               platform=integration, **row)
    hass.states.set(entity_id, state, attrs or {})


def _export(hass, **fields):
    response = run(_async_export_devices(hass, FakeCall(**fields)))
    text = open(response["file"], encoding="utf-8").read()
    return response, text, run(_load_user_data(response["file"]))


# --- what is written -------------------------------------------------------------

def test_groups_by_device_keeps_live_ids_and_changes_nothing(tmp_path):
    hass = FakeHass(config_dir=tmp_path)
    area = ar.async_get(hass).add("Kitchen")
    plug = dr.async_get(hass).add(
        "Kitchen Plug", {("shelly", "p1")}, config_entry_id="shelly",
        manufacturer="Shelly", model="Plus 1PM", sw_version="1.4.4", area_id=area.id,
        connections={("mac", "58:8c:81:a5:08:a0")},
        configuration_url="http://kitchen-plug.local")
    _live(hass, "switch.kitchen_plug", "on", {"friendly_name": "Kitchen Plug"}, device=plug)
    _live(hass, "sensor.kitchen_plug_power", "12.5",
          {"friendly_name": "Kitchen Plug Power", "unit_of_measurement": "W",
           "device_class": "power", "state_class": "measurement"}, device=plug,
          entity_category="diagnostic")
    _live(hass, "light.hall", "off", {"friendly_name": "Hall Light"})           # no device
    _live(hass, "binary_sensor.yaml_door", "on",                                # no registry row
          {"friendly_name": "Yaml Door", "device_class": "door"}, integration=None)
    before = (dict(er.async_get(hass).entities), dict(dr.async_get(hass)._devices),
              {s.entity_id: s.state for s in hass.states.async_all()})

    response, text, devices = _export(hass)

    assert devices["Kitchen Plug"] == {
        "manufacturer": "Shelly", "model": "Plus 1PM", "sw_version": "1.4.4",
        "configuration_url": "http://kitchen-plug.local", "mac": "58:8c:81:a5:08:a0",
        "area": "Kitchen",
        "entities": [
            {"platform": "switch", "name": "Kitchen Plug",
             "entity_id": "switch.kitchen_plug", "initial_value": "on"},
            {"platform": "sensor", "name": "Kitchen Plug Power",
             "entity_id": "sensor.kitchen_plug_power", "class": "power",
             "unit_of_measurement": "W", "state_class": "measurement",
             "entity_category": "diagnostic", "initial_value": "12.5"},
        ]}
    assert devices["Hall Light"] == [{"platform": "light", "name": "Hall Light",
                                      "entity_id": "light.hall", "initial_value": "off"}]
    assert devices["Yaml Door"] == [{"platform": "binary_sensor", "name": "Yaml Door",
                                     "entity_id": "binary_sensor.yaml_door",
                                     "class": "door", "initial_value": "on"}]
    assert text.startswith("# Exported by Virtual Test Devices")
    assert (response["devices"], response["entities"], response["skipped"]) == (3, 4, [])
    assert (dict(er.async_get(hass).entities), dict(dr.async_get(hass)._devices),
            {s.entity_id: s.state for s in hass.states.async_all()}) == before, \
        "the export must change nothing on the HA it runs on"


def test_filters_each_report_their_reason(tmp_path):
    hass = FakeHass(config_dir=tmp_path)
    _live(hass, "sensor.sun_next_dawn", "2026-10-15T05:00:00+00:00", integration="sun")
    _live(hass, "sensor.plug_power", "3", {"unit_of_measurement": "W"})
    _live(hass, "switch.disabled", "off", disabled=True)
    _live(hass, "switch.hidden", "off", hidden=True)
    _live(hass, "light.keep", "on", {"friendly_name": "Keep"})
    _live(hass, "automation.morning", "on")
    _live(hass, "automation.evening", "on")
    _live(hass, "script.night", "off")
    _live(hass, "cover.garage", "open")

    response, _, devices = _export(
        hass, exclude_entities=["sensor.*_power"],
        include_domains=["light", "switch", "sensor", "automation"], skip_hidden=True)

    assert set(devices) == {"Keep"}
    assert {s.get("entity_id") or s.get("domain"): s["reason"] for s in response["skipped"]} == {
        "sensor.sun_next_dawn": "integration sun excluded",
        "sensor.plug_power": "matched exclude_entities",
        "switch.disabled": "disabled",
        "switch.hidden": "hidden",
        "cover.garage": "domain not included",
        "automation": "domain not reproducible",
        "script": "domain not reproducible",
    }
    assert [s["count"] for s in response["skipped"] if s.get("domain") == "automation"] == [2]


def test_state_becomes_a_starting_state(tmp_path):
    hass = FakeHass(config_dir=tmp_path)
    _live(hass, "light.lamp", "on", {"friendly_name": "Lamp", "brightness": 128,
                                     "hs_color": [30.0, 50.0], "effect": "rainbow",
                                     "supported_color_modes": ["hs"]})
    _live(hass, "switch.gone", "unavailable", {"friendly_name": "Gone"})
    _live(hass, "sensor.blank", "unknown", {"friendly_name": "Blank"})
    _live(hass, "button.doorbell", "2026-10-14T10:00:00+00:00", {"friendly_name": "Doorbell"})
    _live(hass, "lock.front", "unlocked", {"friendly_name": "Front"})

    _, _, devices = _export(hass, persistent=False)

    lamp = devices["Lamp"][0]
    assert (lamp["initial_value"], lamp["initial_brightness"],
            lamp["initial_color"], lamp["initial_effect"]) == ("on", 128, [30.0, 50.0], "rainbow")
    assert devices["Gone"][0]["initial_availability"] is False
    assert "initial_value" not in devices["Gone"][0]
    assert "initial_value" not in devices["Blank"][0]
    assert "initial_value" not in devices["Doorbell"][0], "a button's state is a timestamp"
    assert devices["Front"][0]["initial_value"] == "unlocked"
    assert all(e["persistent"] is False for d in devices.values() for e in d)


def test_no_codes_survive(tmp_path):
    hass = FakeHass(config_dir=tmp_path)
    _live(hass, "lock.front", "locked", {
        "friendly_name": "Front Door", "code_format": "^\\d{4}$", "supported_features": 1,
        "codes": {"1": {"name": "Bob", "code": "1234"}}, "last_code_name": "Bob"})
    _live(hass, "alarm_control_panel.home", "disarmed", {
        "friendly_name": "Alarm", "code_format": "number", "code_arm_required": True,
        "supported_features": 3, "codes": ["1234"]})

    _, text, devices = _export(hass)

    assert "1234" not in text and "Bob" not in text
    assert devices["Front Door"][0] == {
        "platform": "lock", "name": "Front Door", "entity_id": "lock.front",
        "code_format": "^\\d{4}$", "supported_features": 1, "initial_value": "locked"}
    assert set(devices["Alarm"][0]) == {"platform", "name", "entity_id", "code_format",
                                        "code_arm_required", "supported_features",
                                        "initial_value"}


def test_an_entry_the_platform_rejects_goes_to_the_report_not_the_file(tmp_path):
    hass = FakeHass(config_dir=tmp_path)
    _live(hass, "siren.bad", "off", {"friendly_name": "Bad Siren",
                                     "available_tones": {"1": "beep"}})   # platform wants a list
    _live(hass, "siren.good", "off", {"friendly_name": "Good Siren",
                                      "available_tones": ["beep"]})

    response, text, devices = _export(hass)

    assert set(devices) == {"Good Siren"}
    [skip] = response["skipped"]
    assert skip["entity_id"] == "siren.bad" and skip["reason"].startswith("failed validation")
    assert "siren.bad" in text, "the report is in the file too"


def test_device_name_collisions_get_a_suffix_and_a_note(tmp_path):
    hass = FakeHass(config_dir=tmp_path)
    a = dr.async_get(hass).add("Plug", {("shelly", "a")}, config_entry_id="shelly")
    b = dr.async_get(hass).add("Plug", {("shelly", "b")}, config_entry_id="shelly")
    _live(hass, "switch.a", "on", {"friendly_name": "Plug"}, device=a)
    _live(hass, "switch.b", "off", {"friendly_name": "Plug"}, device=b)

    response, _, devices = _export(hass)

    assert set(devices) == {"Plug", "Plug 2"}
    assert response["skipped"] == [
        {"device": "Plug", "reason": "name already used, written as 'Plug 2'"}]


def test_file_must_be_an_allowed_path(tmp_path):
    hass = FakeHass(config_dir=tmp_path)
    with pytest.raises(ServiceValidationError):
        run(_async_export_devices(hass, FakeCall(file="/etc/virtual_export.yaml")))


def test_the_default_path_and_the_allowlist_are_writable(tmp_path):
    """The default lives in the config folder, which HA's own allowlist does
    not cover; refusing it broke the action on every real install."""
    hass = FakeHass(config_dir=tmp_path / "config")
    outside = tmp_path / "exports"
    outside.mkdir()
    hass.config.allowlist_external_dirs.add(str(outside))

    default = run(_async_export_devices(hass, FakeCall()))
    listed = run(_async_export_devices(hass, FakeCall(file=str(outside / "x.yaml"))))

    assert default["file"] == str(tmp_path / "config" / "virtual_export.yaml")
    assert pathlib.Path(listed["file"]).exists()


def test_every_reproducible_domain_has_a_schema():
    assert set(PLATFORM_SCHEMAS) == REPRODUCIBLE_DOMAINS


# --- the round trip ----------------------------------------------------------------

def test_exported_file_loads_back_under_the_same_ids(tmp_path):
    """Export one of everything, load it as a group, validate every entity
    against the platform that would create it."""
    hass = FakeHass(config_dir=tmp_path)
    plug = dr.async_get(hass).add("Plug", {("shelly", "p1")}, config_entry_id="shelly",
                                  manufacturer="Shelly")
    _live(hass, "switch.plug", "on", {"friendly_name": "Plug"}, device=plug)
    _live(hass, "sensor.plug_power", "12.5", {"friendly_name": "Plug Power",
          "unit_of_measurement": "W", "device_class": "power"}, device=plug)
    _live(hass, "light.lamp", "on", {"friendly_name": "Lamp", "brightness": 200,
                                     "supported_color_modes": ["brightness"]})
    _live(hass, "binary_sensor.door", "off", {"friendly_name": "Door", "device_class": "door"})
    _live(hass, "cover.garage", "closed", {"friendly_name": "Garage",
                                           "device_class": "garage", "supported_features": 3})
    _live(hass, "lock.front", "locked", {"friendly_name": "Front"})
    _live(hass, "number.volume", "7", {"friendly_name": "Volume", "min": 0, "max": 10,
                                       "step": 1, "mode": "slider"})
    _live(hass, "device_tracker.phone", "home", {"friendly_name": "Phone"})
    _live(hass, "media_player.tv", "off", {"friendly_name": "TV", "source_list": ["HDMI1"],
                                           "supported_features": 21})
    _live(hass, "climate.thermostat", "heat", {"friendly_name": "Thermostat",
          "hvac_modes": ["heat", "off"], "min_temp": 7, "max_temp": 35, "supported_features": 1})
    _live(hass, "fan.ceiling", "off", {"friendly_name": "Ceiling", "percentage_step": 25,
                                       "supported_features": 1})
    _live(hass, "vacuum.robot", "docked", {"friendly_name": "Robot", "fan_speed_list": ["low"],
                                           "supported_features": 4})
    _live(hass, "humidifier.humi", "off", {"friendly_name": "Humi", "available_modes": ["auto"],
          "min_humidity": 30, "max_humidity": 70, "supported_features": 1})
    _live(hass, "siren.alarm", "off", {"friendly_name": "Siren", "available_tones": ["beep"],
                                       "supported_features": 1})
    _live(hass, "valve.water", "open", {"friendly_name": "Water", "supported_features": 3})
    _live(hass, "alarm_control_panel.home", "disarmed", {"friendly_name": "Alarm",
                                                         "supported_features": 3})
    _live(hass, "button.bell", "2026-10-14T10:00:00+00:00", {"friendly_name": "Bell"})
    _live(hass, "button.esp_restart", "unknown", {"friendly_name": "ESP Restart",
                                                   "device_class": "restart"},
          entity_category="config")
    _live(hass, "event.remote", "2026-10-14T10:00:00+00:00", {"friendly_name": "Remote",
                                                              "event_types": ["press"]})
    _live(hass, "camera.door", "idle", {"friendly_name": "Door Cam", "supported_features": 0})
    _live(hass, "notify.sms", "2026-10-14T10:00:00+00:00", {"friendly_name": "SMS",
                                                            "supported_features": 0})
    _live(hass, "select.log_level", "DEBUG", {"friendly_name": "Log Level",
                                              "options": ["INFO", "DEBUG"]},
          entity_category="config")
    _live(hass, "text.greeting", "hello", {"friendly_name": "Greeting", "min": 0,
                                           "max": 20, "pattern": None, "mode": "text"})
    live_ids = {s.entity_id for s in hass.states.async_all()}

    response, _, _ = _export(hass)
    assert response["skipped"] == [], response["skipped"]

    cfg = BlendedCfg(hass, FakeEntry(response["file"], group_name="Imported").data)
    run(cfg.async_load())

    loaded = {e["entity_id"]: e for entities in cfg.entities.values() for e in entities}
    assert set(loaded) == live_ids
    assert {e["unique_id"] for e in loaded.values()} == live_ids
    assert set(cfg.entities) == REPRODUCIBLE_DOMAINS, "one of everything, nothing lost"
    for platform, entities in cfg.entities.items():
        for entity in entities:
            PLATFORM_SCHEMAS[platform](entity)      # exactly what the platform does
    assert loaded["button.esp_restart"]["class"] == "restart"
    assert loaded["button.esp_restart"]["entity_category"] == "config"
    assert loaded["select.log_level"]["options"] == ["INFO", "DEBUG"]
    assert loaded["select.log_level"]["initial_value"] == "DEBUG"
    assert loaded["text.greeting"]["initial_value"] == "hello"


def test_a_password_text_is_exported_without_its_value(tmp_path):
    hass = FakeHass(config_dir=tmp_path)
    _live(hass, "text.wifi_password", "hunter2", {"friendly_name": "WiFi Password",
                                                  "min": 0, "max": 64, "mode": "password"})

    _, text, devices = _export(hass)

    assert "hunter2" not in text
    assert devices["WiFi Password"][0]["mode"] == "password"
    assert "initial_value" not in devices["WiFi Password"][0]


def test_an_entity_lands_in_the_same_section_of_the_device_page():
    from homeassistant.helpers.entity import EntityCategory
    from custom_components.virtual.sensor import SENSOR_SCHEMA, VirtualSensor

    plain = VirtualSensor(SENSOR_SCHEMA({"name": "A"}), False)
    diag = VirtualSensor(SENSOR_SCHEMA({"name": "B", "entity_category": "diagnostic"}), False)

    assert plain.entity_category is None
    assert diag.entity_category is EntityCategory.DIAGNOSTIC


def test_a_button_keeps_its_device_class():
    """A restart button must come out as a restart button, not a plain one."""
    from custom_components.virtual.button import BUTTON_SCHEMA, VirtualButton

    button = VirtualButton(BUTTON_SCHEMA({"name": "Restart", "class": "restart"}), False)

    assert button.device_class == "restart"


# --- export_only -------------------------------------------------------------------

def test_export_only_offers_the_export_action_and_nothing_else(tmp_path):
    hass = FakeHass(config_dir=tmp_path)

    assert run(async_setup(hass, {"virtual": {"export_only": True}})) is True

    assert set(hass.services.registered) == {("virtual", "export_devices")}
    with pytest.raises(ConfigEntryError):
        run(async_setup_entry(hass, FakeEntry(tmp_path / "virtual.yaml")))
    flow = VirtualFlowHandler()
    flow.hass = hass
    result = run(flow.async_step_user(None))
    assert (result["type"], result["reason"]) == ("abort", "export_only")


def test_normal_mode_has_the_export_action_too(tmp_path):
    hass = FakeHass(config_dir=tmp_path)
    hass.config_entries.entries = [FakeEntry(tmp_path / "virtual.yaml")]   # so no import flow

    assert run(async_setup(hass, {})) is True

    assert ("virtual", "export_devices") in hass.services.registered
    assert hass.data[COMPONENT_CONFIG][CONF_EXPORT_ONLY] is False
