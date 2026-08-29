"""Failing loudly, and failing without taking everything else down.

REGRESSION GUARD for two bench failures on 2026-08-29.

ONE PLATFORM MUST NOT STRAND THE OTHERS. Unloading used to be a single batch, so
one platform failing failed the whole unload. A group being edited reloaded,
notify raised `Config entry was never loaded!`, and the entry was left half-down:
21 entities stuck in the registry as `restored`/`unavailable`, nothing short of a
full Home Assistant restart bringing them back. Every device-driven test on that
bench silently became meaningless, which looks exactly like a compiler bug.

A NO-OP MUST NOT REPORT SUCCESS. Removing a device that wasn't in the group
returned success and did nothing, so a bench stayed full of devices somebody
believed they had deleted.
"""

import pytest

from conftest import FakeEntry, FakeHass, run

from custom_components.virtual import VIRTUAL_PLATFORMS, async_unload_entry
from custom_components.virtual.cfg import _load_user_data, _save_user_data
from custom_components.virtual.const import COMPONENT_DOMAIN
from custom_components.virtual.pistoncore_manage import _async_remove_device

try:
    from homeassistant.exceptions import ServiceValidationError
except ImportError:  # pragma: no cover - HA always has this
    ServiceValidationError = Exception


GROUP = "Test Group"


def _loaded_bench(tmp_path):
    """A hass with one group loaded, the way a live entry leaves it."""
    hass = FakeHass(config_dir=tmp_path)
    entry = FakeEntry(tmp_path / "virtual.yaml", group_name=GROUP)
    hass.config_entries.entries = [entry]
    hass.data[COMPONENT_DOMAIN] = {GROUP: {"entities": {}, "devices": []}}
    return hass, entry


class FakeCall:
    def __init__(self, **data):
        self.data = data


# --- one platform must not strand the others ---------------------------------

def test_unload_survives_a_platform_that_was_never_loaded(tmp_path):
    """THE bug: notify raising must not keep the whole group down."""
    hass, entry = _loaded_bench(tmp_path)
    hass.config_entries.unload_raises = {"notify"}

    assert run(async_unload_entry(hass, entry)) is True, (
        "one platform failing reported the whole unload as failed — Home "
        "Assistant then leaves every entity stranded as `restored`"
    )
    assert GROUP not in hass.data[COMPONENT_DOMAIN], (
        "the group's data was left behind, so setting up again would stack on it"
    )


def test_every_other_platform_is_still_attempted(tmp_path):
    """A failure part-way must not abandon the platforms after it."""
    hass, entry = _loaded_bench(tmp_path)
    hass.config_entries.unload_raises = {"notify"}

    run(async_unload_entry(hass, entry))

    assert set(hass.config_entries.unloaded) == set(VIRTUAL_PLATFORMS), (
        "some platforms were never asked to unload: "
        f"{set(VIRTUAL_PLATFORMS) - set(hass.config_entries.unloaded)}"
    )


def test_unload_survives_a_platform_reporting_failure(tmp_path):
    """The quieter variant: a platform returns False rather than raising."""
    hass, entry = _loaded_bench(tmp_path)
    hass.config_entries.unload_returns_false = {"camera"}

    assert run(async_unload_entry(hass, entry)) is True
    assert GROUP not in hass.data[COMPONENT_DOMAIN]


def test_unloading_twice_does_not_explode(tmp_path):
    """Popping the group used to raise KeyError the second time round."""
    hass, entry = _loaded_bench(tmp_path)

    assert run(async_unload_entry(hass, entry)) is True
    assert run(async_unload_entry(hass, entry)) is True


# --- a no-op must not report success ------------------------------------------

def test_removing_an_unknown_device_is_refused(tmp_path):
    """Silent success is what let a bench stay polluted."""
    hass, entry = _loaded_bench(tmp_path)
    path = str(tmp_path / "virtual.yaml")
    run(_save_user_data(path, {"Keep Me": [{"platform": "switch", "name": "Keep Me"}]}))

    with pytest.raises(ServiceValidationError):
        run(_async_remove_device(hass, FakeCall(device_name="Never Existed")))


def test_a_refused_removal_does_not_touch_the_file(tmp_path):
    """Refusing must abort before the group's file is rewritten."""
    hass, entry = _loaded_bench(tmp_path)
    path = str(tmp_path / "virtual.yaml")
    devices = {"Keep Me": [{"platform": "switch", "name": "Keep Me"}]}
    run(_save_user_data(path, devices))
    before = open(path, encoding="utf-8").read()

    with pytest.raises(ServiceValidationError):
        run(_async_remove_device(hass, FakeCall(device_name="Never Existed")))

    assert open(path, encoding="utf-8").read() == before
    assert run(_load_user_data(path)) == devices


def test_removing_a_real_device_still_works(tmp_path):
    """The guard must not break the thing it guards."""
    hass, entry = _loaded_bench(tmp_path)
    path = str(tmp_path / "virtual.yaml")
    run(_save_user_data(path, {
        "Keep Me": [{"platform": "switch", "name": "Keep Me"}],
        "Drop Me": [{"platform": "switch", "name": "Drop Me"}],
    }))

    run(_async_remove_device(hass, FakeCall(device_name="Drop Me")))

    assert list(run(_load_user_data(path))) == ["Keep Me"]
    assert hass.config_entries.reloads == 1, "a real removal must still reload"
