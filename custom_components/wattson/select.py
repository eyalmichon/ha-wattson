"""Profile and phase selectors for Wattson."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.select import SelectEntity
from homeassistant.helpers.entity import EntityCategory

from .entity import WattsonEntity, get_coordinator

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .coordinator import WattsonCoordinator
    from .profile_matcher import Profile, ProfilePhase


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Wattson profile and phase selectors."""
    coordinator = get_coordinator(hass, entry)

    profile_select = WattsonProfileSelect(coordinator, entry)
    coordinator.register_entity(profile_select)

    phase_select = WattsonPhaseSelect(coordinator, entry, profile_select)
    profile_select.register_sibling(phase_select)
    coordinator.register_entity(phase_select)

    async_add_entities([profile_select, phase_select])


class WattsonProfileSelect(WattsonEntity, SelectEntity):
    """Select entity listing all learned profiles for an appliance."""

    _attr_translation_key = "profile"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:chart-timeline-variant-shimmer"

    def __init__(self, coordinator: WattsonCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "profile_select")
        self._siblings: list[object] = []
        self._attr_options = self._build_options()
        self._attr_current_option = (
            self._attr_options[0] if self._attr_options else None
        )

    @property
    def selected_profile(self) -> Profile | None:
        """Return the currently selected Profile object, or None."""
        if not self._attr_current_option:
            return None
        for i, opt in enumerate(self._attr_options):
            if opt == self._attr_current_option:
                profiles = self.coordinator.store.profiles
                if i < len(profiles):
                    return profiles[i]
                return None
        return None

    def register_sibling(self, entity: object) -> None:
        """Register a sibling entity to notify on selection changes."""
        self._siblings.append(entity)

    def _build_options(self) -> list[str]:
        """Build the options list from stored profiles."""
        profiles = self.coordinator.store.profiles
        options: list[str] = []
        for idx, p in enumerate(profiles, start=1):
            duration_min = round(p.avg_duration_s / 60, 1)
            label = p.name or f"Program #{idx}"
            options.append(f"{label} (~{duration_min} min)")
        return options

    def _notify_siblings(self) -> None:
        """Push state updates to sibling entities."""
        for entity in self._siblings:
            if hasattr(entity, "on_profile_changed"):
                entity.on_profile_changed()

    def refresh_options(self) -> None:
        """Rebuild options after profiles change (add/rename/delete)."""
        old_selection = self._attr_current_option
        self._attr_options = self._build_options()
        if old_selection not in self._attr_options:
            self._attr_current_option = (
                self._attr_options[0] if self._attr_options else None
            )
        self._notify_siblings()

    async def async_select_option(self, option: str) -> None:
        """Handle the user selecting a profile."""
        self._attr_current_option = option
        self.async_write_ha_state()
        self._notify_siblings()

    @property
    def extra_state_attributes(self) -> dict[str, object] | None:
        """Expose the selected profile's ID for use in automations."""
        profile = self.selected_profile
        if profile is None:
            return None
        return {
            "profile_id": profile.id,
            "profile_name": profile.name,
            "cycle_count": profile.cycle_count,
            "avg_duration_s": round(profile.avg_duration_s, 1),
            "avg_energy_wh": round(profile.avg_energy_wh, 3),
        }


class WattsonPhaseSelect(WattsonEntity, SelectEntity):
    """Select entity listing detected phases for the selected profile."""

    _attr_translation_key = "phase"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:transit-connection-variant"

    def __init__(
        self,
        coordinator: WattsonCoordinator,
        entry: ConfigEntry,
        profile_select: WattsonProfileSelect,
    ) -> None:
        super().__init__(coordinator, entry, "phase_select")
        self._profile_select = profile_select
        self._siblings: list[object] = []
        self._attr_options = self._build_options()
        self._attr_current_option = (
            self._attr_options[0] if self._attr_options else None
        )

    @property
    def selected_phase(self) -> tuple[Profile, int, ProfilePhase] | None:
        """Return (profile, phase_index, phase) for the current selection."""
        profile = self._profile_select.selected_profile
        if profile is None or not profile.phases or not self._attr_current_option:
            return None
        for i, opt in enumerate(self._attr_options):
            if opt == self._attr_current_option and i < len(profile.phases):
                return profile, i, profile.phases[i]
        return None

    def register_sibling(self, entity: object) -> None:
        """Register a sibling entity to notify on selection changes."""
        self._siblings.append(entity)

    def _build_options(self) -> list[str]:
        """Build phase options from the selected profile."""
        profile = self._profile_select.selected_profile
        if profile is None or not profile.phases:
            return []
        options: list[str] = []
        for i, phase in enumerate(profile.phases):
            label = phase.name or f"Phase {i + 1}"
            options.append(f"{label} (~{round(phase.avg_power_w)}W)")
        return options

    def _notify_siblings(self) -> None:
        for entity in self._siblings:
            if hasattr(entity, "on_phase_changed"):
                entity.on_phase_changed()

    def on_profile_changed(self) -> None:
        """Called by the profile select when the selection changes."""
        self.refresh_options()
        self.async_write_ha_state()

    def refresh_options(self) -> None:
        """Rebuild options after phases or profile selection change."""
        old_options = self._attr_options
        old_selection = self._attr_current_option
        self._attr_options = self._build_options()

        if old_selection in self._attr_options:
            pass
        elif old_selection and old_options:
            old_idx = next(
                (i for i, o in enumerate(old_options) if o == old_selection), None
            )
            if old_idx is not None and old_idx < len(self._attr_options):
                self._attr_current_option = self._attr_options[old_idx]
            else:
                self._attr_current_option = (
                    self._attr_options[0] if self._attr_options else None
                )
        else:
            self._attr_current_option = (
                self._attr_options[0] if self._attr_options else None
            )
        self._notify_siblings()

    async def async_select_option(self, option: str) -> None:
        """Handle the user selecting a phase."""
        self._attr_current_option = option
        self.async_write_ha_state()
        self._notify_siblings()

    @property
    def extra_state_attributes(self) -> dict[str, object] | None:
        """Expose selected phase details."""
        sel = self.selected_phase
        if sel is None:
            return None
        profile, idx, phase = sel
        return {
            "profile_id": profile.id,
            "phase_index": idx,
            "phase_name": phase.name,
            "avg_power_w": round(phase.avg_power_w, 1),
            "pattern": phase.pattern,
            "marks_cycle_done": phase.marks_cycle_done,
        }
