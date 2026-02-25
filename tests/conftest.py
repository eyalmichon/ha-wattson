"""Fixtures for Wattson tests."""

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):  # noqa: PT004
    """Enable custom integrations in Home Assistant tests."""
    yield
