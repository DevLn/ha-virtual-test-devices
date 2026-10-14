"""Shared fixtures.

These tests run against a REAL Home Assistant install, because the integration's
behaviour depends on HA's own enums and yaml handling — the bugs these tests
exist to prevent were all in that seam. The easy way to get one is the HA
container itself:

    docker cp test-devices-integration/tests pc-testha:/config/tests
    docker exec pc-testha python -m pytest /config/tests -q

Deliberately NO pytest-asyncio: async cases call `asyncio.run` directly, so the
only requirements are HA and pytest, both of which any HA install already has.
"""

import asyncio
import pathlib
import sys
from types import SimpleNamespace

import pytest

import homeassistant.helpers.device_registry as dr
import homeassistant.helpers.entity_registry as er

# The integration is imported as `custom_components.virtual.*`, so its PARENT
# must be importable.
INTEGRATION_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(INTEGRATION_ROOT) not in sys.path:
    sys.path.insert(0, str(INTEGRATION_ROOT))


def run(coro):
    """Run one coroutine to completion — replaces pytest-asyncio."""
    return asyncio.run(coro)


class FakeConfigEntries:
    """Counts reloads, so a test can prove a burst collapses into one."""

    def __init__(self):
        self.reloads = 0
        self.entries = []
        # Platforms that refuse to unload: False for "reported failure",
        # ValueError for the one HA actually raises ("never loaded").
        self.unload_returns_false = set()
        self.unload_raises = set()
        self.unloaded = []

    async def async_reload(self, entry_id):
        self.reloads += 1
        # Yield, so a reload genuinely interleaves with other waiting callers —
        # without this the race being tested cannot occur.
        await asyncio.sleep(0)

    def async_entries(self, domain):
        return list(self.entries)

    async def async_forward_entry_unload(self, entry, platform):
        self.unloaded.append(platform)
        await asyncio.sleep(0)
        if platform in self.unload_raises:
            raise ValueError("Config entry was never loaded!")
        return platform not in self.unload_returns_false


class FakeConfig:
    """Just enough of hass.config for code that resolves a file under /config."""

    def __init__(self, config_dir):
        self.config_dir = pathlib.Path(config_dir)

    def path(self, *parts) -> str:
        target = self.config_dir.joinpath(*parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        return str(target)


class _Rows(dict):
    """A registry's row collection, with the index lookups that HA's own
    module-level helpers (`er`/`dr.async_entries_for_config_entry`) call on it.
    Those helpers are the real ones; only the store behind them is fake."""

    def get_entries_for_config_entry_id(self, entry_id):
        return [row for row in self.values() if row.config_entry_id == entry_id]

    get_devices_for_config_entry_id = get_entries_for_config_entry_id

    def get_entries_for_device_id(self, device_id, include_disabled_entities=False):
        return [row for row in self.values() if row.device_id == device_id
                and (include_disabled_entities or not row.disabled)]


class FakeEntityRegistry:
    """Just the surface this integration touches, holding plain rows."""

    def __init__(self):
        self.entities = _Rows()

    def add(self, entity_id, unique_id, config_entry_id="entry-1", device_id=None):
        row = SimpleNamespace(
            entity_id=entity_id, unique_id=unique_id, platform="virtual",
            config_entry_id=config_entry_id, device_id=device_id,
            disabled=False, hidden=False, name=None, original_name=None,
            device_class=None, original_device_class=None)
        self.entities[entity_id] = row
        return row

    def async_get(self, entity_id):
        return self.entities.get(entity_id)

    def async_remove(self, entity_id):
        del self.entities[entity_id]


class FakeDeviceRegistry:
    def __init__(self):
        self._devices = _Rows()   # the name HA's helper reaches for
        self.devices = self._devices

    def add(self, name, identifiers, config_entry_id="entry-1"):
        row = SimpleNamespace(
            id=f"dev-{len(self._devices) + 1}", name=name, name_by_user=None,
            identifiers=set(identifiers), config_entry_id=config_entry_id,
            manufacturer=None, model=None, sw_version=None, area_id=None)
        self._devices[row.id] = row
        return row

    def async_get(self, device_id):
        return self._devices.get(device_id)

    def async_get_or_create(self, *, config_entry_id, identifiers, **fields):
        """Create or update by identifiers, keeping every value passed so a test
        can read back exactly what the integration asked for."""
        identifiers = set(identifiers)
        row = next((r for r in self._devices.values() if r.identifiers & identifiers), None)
        if row is None:
            row = self.add(fields.pop("name", None), identifiers, config_entry_id)
        for key, value in fields.items():
            setattr(row, key, value)
        return row

    def async_remove_device(self, device_id):
        self._devices.pop(device_id)


class FakeStates:
    """hass.states with just get / set / async_all, holding real State objects."""

    def __init__(self):
        self._states = {}

    def set(self, entity_id, state, attributes=None):
        from homeassistant.core import State
        self._states[entity_id] = State(entity_id, state, attributes or {})

    def get(self, entity_id):
        return self._states.get(entity_id)

    def async_all(self):
        return list(self._states.values())


class FakeHass:
    def __init__(self, config_dir=None):
        self.data = {}
        self.states = FakeStates()
        self.config_entries = FakeConfigEntries()
        # Only the tests that load a group's config need this.
        self.config = FakeConfig(config_dir) if config_dir else None
        # er.async_get / dr.async_get are singletons that live in hass.data, so
        # parking the fakes there is all it takes for the real helpers to see them.
        self.data[er.DATA_REGISTRY] = FakeEntityRegistry()
        self.data[dr.DATA_REGISTRY] = FakeDeviceRegistry()


class FakeEntry:
    def __init__(self, file_name, group_name="Test Group", entry_id="entry-1"):
        self.entry_id = entry_id
        self.data = {"file_name": str(file_name), "group_name": group_name}


@pytest.fixture
def hass():
    return FakeHass()


@pytest.fixture
def device_file(tmp_path):
    """An empty path where the group's device yaml will be written."""
    return tmp_path / "virtual.yaml"


@pytest.fixture
def entry(device_file):
    return FakeEntry(device_file)
