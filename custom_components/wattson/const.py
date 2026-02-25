"""Constants for Wattson."""

from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "wattson"

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
]
