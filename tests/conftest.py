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

import pytest

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


class FakeHass:
    def __init__(self, config_dir=None):
        self.data = {}
        self.config_entries = FakeConfigEntries()
        # Only the tests that load a group's config need this.
        self.config = FakeConfig(config_dir) if config_dir else None


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
