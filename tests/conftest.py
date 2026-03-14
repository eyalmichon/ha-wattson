"""Fixtures for Wattson tests."""

from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SIM_SRC = _REPO_ROOT / "tests" / "wattson_simulator"
_SIM_LINK = _REPO_ROOT / "custom_components" / "wattson_simulator"


def pytest_configure(config):
    """Ensure the simulator symlink exists before collection."""
    if _SIM_SRC.is_dir() and not _SIM_LINK.exists():
        _SIM_LINK.symlink_to(_SIM_SRC)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable custom integrations in Home Assistant tests."""
    return
