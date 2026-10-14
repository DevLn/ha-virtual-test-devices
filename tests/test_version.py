"""The version is one string, written twice, and Home Assistant must accept it.

`manifest.json` is what HACS and HA read; `__version__` in `__init__.py` is
what every device's sw_version shows and what the export file header names.
They had drifted (0.9.4 against 0.13.3) before this test existed. The format
check mirrors the one HA's loader applies to a custom integration's manifest:
a version it rejects stops the integration loading at all.
"""

import json
import pathlib

from awesomeversion import AwesomeVersion, AwesomeVersionStrategy

from custom_components.virtual import __version__

MANIFEST = pathlib.Path(__file__).resolve().parent.parent / "custom_components" / "virtual" / "manifest.json"


def test_init_and_manifest_agree():
    assert __version__ == json.loads(MANIFEST.read_text(encoding="utf-8"))["version"]


def test_home_assistant_accepts_the_version_format():
    # The strategies HA's loader insists on for custom integrations.
    AwesomeVersion(__version__, ensure_strategy=[
        AwesomeVersionStrategy.CALVER,
        AwesomeVersionStrategy.SEMVER,
        AwesomeVersionStrategy.SIMPLEVER,
        AwesomeVersionStrategy.BUILDVER,
        AwesomeVersionStrategy.PEP440,
    ])
