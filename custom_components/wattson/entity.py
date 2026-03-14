"""Base entity and helpers for Wattson."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.helpers.entity import Entity

from .const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

    from .coordinator import WattsonCoordinator


def get_coordinator(hass: HomeAssistant, entry: ConfigEntry) -> WattsonCoordinator:
    """Look up the coordinator for a config entry."""
    return hass.data[DOMAIN][entry.entry_id]["coordinator"]


class WattsonEntity(Entity):
    """Base class for all Wattson entities — sets device_info and unique_id."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        coordinator: WattsonCoordinator,
        entry: ConfigEntry,
        key: str,
    ) -> None:
        self.coordinator = coordinator
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": entry.title,
            "manufacturer": "Wattson",
        }
