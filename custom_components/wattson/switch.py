"""Phase 'marks cycle done' switch for Wattson."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.helpers.entity import EntityCategory

from .entity import WattsonEntity, get_coordinator

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .coordinator import WattsonCoordinator
    from .select import WattsonPhaseSelect

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Wattson phase-done switch."""
    coordinator = get_coordinator(hass, entry)
    phase_select = coordinator.get_phase_select()
    if phase_select is None:
        return

    switch = WattsonPhaseDoneSwitch(coordinator, entry, phase_select)
    phase_select.register_sibling(switch)
    async_add_entities([switch])


class WattsonPhaseDoneSwitch(WattsonEntity, SwitchEntity):
    """Toggle whether the selected phase marks the cycle as done.

    When enabled, entering this phase during a cycle turns the binary
    sensor off and fires a phase-changed event with marks_cycle_done=True.
    """

    _attr_translation_key = "phase_done"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:flag-checkered"

    def __init__(
        self,
        coordinator: WattsonCoordinator,
        entry: ConfigEntry,
        phase_select: WattsonPhaseSelect,
    ) -> None:
        super().__init__(coordinator, entry, "phase_done")
        self._phase_select = phase_select
        self._sync_from_select()

    def _sync_from_select(self) -> None:
        sel = self._phase_select.selected_phase
        self._attr_is_on = sel[2].marks_cycle_done if sel else False

    def on_phase_changed(self) -> None:
        """Called by the phase select when the selection changes."""
        self._sync_from_select()
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs: Any) -> None:  # noqa: ARG002, ANN401
        """Mark the selected phase as 'cycle done'."""
        sel = self._phase_select.selected_phase
        if sel is None:
            return
        profile, phase_index, _ = sel
        await self.coordinator.async_set_phase_done(
            profile.id,
            phase_index,
            done=True,
        )
        self._attr_is_on = True
        self.async_write_ha_state()
        _LOGGER.debug(
            "Marked phase %d of profile %s as cycle-done",
            phase_index,
            profile.id,
        )

    async def async_turn_off(self, **kwargs: Any) -> None:  # noqa: ARG002, ANN401
        """Clear the 'cycle done' mark from the selected phase."""
        sel = self._phase_select.selected_phase
        if sel is None:
            return
        profile, phase_index, _ = sel
        await self.coordinator.async_set_phase_done(
            profile.id,
            phase_index,
            done=False,
        )
        self._attr_is_on = False
        self.async_write_ha_state()
        _LOGGER.debug(
            "Unmarked phase %d of profile %s as cycle-done",
            phase_index,
            profile.id,
        )
