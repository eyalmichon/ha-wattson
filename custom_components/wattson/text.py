"""Profile and phase name text entities for Wattson."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.components.text import TextEntity
from homeassistant.helpers.entity import EntityCategory

from .const import MAX_NAME_LENGTH
from .entity import WattsonEntity, get_coordinator

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .coordinator import WattsonCoordinator
    from .select import WattsonPhaseSelect, WattsonProfileSelect

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Wattson text entities."""
    coordinator = get_coordinator(hass, entry)
    entities: list[WattsonEntity] = []

    profile_select = coordinator.get_profile_select()
    if profile_select is not None:
        profile_text = WattsonProfileNameText(coordinator, entry, profile_select)
        profile_select.register_sibling(profile_text)
        entities.append(profile_text)

    phase_select = coordinator.get_phase_select()
    if phase_select is not None:
        phase_text = WattsonPhaseNameText(coordinator, entry, phase_select)
        phase_select.register_sibling(phase_text)
        entities.append(phase_text)

    async_add_entities(entities)


class WattsonProfileNameText(WattsonEntity, TextEntity):
    """Text entity to view/edit the name of the selected profile."""

    _attr_translation_key = "profile_name"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:rename"
    _attr_native_max = MAX_NAME_LENGTH

    def __init__(
        self,
        coordinator: WattsonCoordinator,
        entry: ConfigEntry,
        profile_select: WattsonProfileSelect,
    ) -> None:
        super().__init__(coordinator, entry, "profile_name")
        self._profile_select = profile_select
        self._sync_from_select()

    def _sync_from_select(self) -> None:
        """Pull the current profile name from the select entity."""
        profile = self._profile_select.selected_profile
        self._attr_native_value = (profile.name or "") if profile else ""

    def on_profile_changed(self) -> None:
        """Called by the select entity when the selection changes."""
        self._sync_from_select()
        self.async_write_ha_state()

    async def async_set_value(self, value: str) -> None:
        """Rename the selected profile when the user edits the text."""
        profile = self._profile_select.selected_profile
        if profile is None:
            return

        name = value.strip()
        await self.coordinator.async_rename_profile(profile.id, name)

        self._attr_native_value = name
        self.async_write_ha_state()
        _LOGGER.debug("Renamed profile %s to '%s'", profile.id, name)


class WattsonPhaseNameText(WattsonEntity, TextEntity):
    """Text entity to view/edit the name of the selected phase."""

    _attr_translation_key = "phase_name"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:rename"
    _attr_native_max = MAX_NAME_LENGTH

    def __init__(
        self,
        coordinator: WattsonCoordinator,
        entry: ConfigEntry,
        phase_select: WattsonPhaseSelect,
    ) -> None:
        super().__init__(coordinator, entry, "phase_name")
        self._phase_select = phase_select
        self._sync_from_select()

    def _sync_from_select(self) -> None:
        """Pull the current phase name from the select entity."""
        sel = self._phase_select.selected_phase
        self._attr_native_value = (sel[2].name or "") if sel else ""

    def on_phase_changed(self) -> None:
        """Called by the phase select entity when the selection changes."""
        self._sync_from_select()
        self.async_write_ha_state()

    async def async_set_value(self, value: str) -> None:
        """Rename the selected phase when the user edits the text."""
        sel = self._phase_select.selected_phase
        if sel is None:
            return

        profile, phase_index, _ = sel
        name = value.strip()
        await self.coordinator.async_rename_phase(profile.id, phase_index, name)

        self._attr_native_value = name
        self.async_write_ha_state()
        _LOGGER.debug(
            "Renamed phase %d of profile %s to '%s'",
            phase_index,
            profile.id,
            name,
        )
