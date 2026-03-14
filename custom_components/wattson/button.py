"""Profile delete button for Wattson."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.components.button import ButtonEntity
from homeassistant.helpers.entity import EntityCategory

from .entity import WattsonEntity, get_coordinator

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .coordinator import WattsonCoordinator
    from .select import WattsonProfileSelect

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Wattson profile delete button."""
    coordinator = get_coordinator(hass, entry)
    profile_select = coordinator.get_profile_select()
    if profile_select is None:
        return

    button = WattsonDeleteProfileButton(coordinator, entry, profile_select)
    profile_select.register_sibling(button)
    async_add_entities([button])


class WattsonDeleteProfileButton(WattsonEntity, ButtonEntity):
    """Button to delete the currently selected profile."""

    _attr_translation_key = "delete_profile"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:delete-outline"

    def __init__(
        self,
        coordinator: WattsonCoordinator,
        entry: ConfigEntry,
        profile_select: WattsonProfileSelect,
    ) -> None:
        super().__init__(coordinator, entry, "delete_profile")
        self._profile_select = profile_select

    def on_profile_changed(self) -> None:
        """Called by the select entity when the selection changes."""
        self.async_write_ha_state()

    async def async_press(self) -> None:
        """Delete the currently selected profile."""
        profile = self._profile_select.selected_profile
        if profile is None:
            return

        await self.coordinator.async_delete_profile(profile.id)
        _LOGGER.debug("Deleted profile %s (%s)", profile.id, profile.name)
