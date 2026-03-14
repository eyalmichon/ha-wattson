"""Program selector for the Wattson Simulator."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.select import SelectEntity

from .const import DOMAIN, PROGRAMS

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .engine import SimulationEngine


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the simulator program selector."""
    engine: SimulationEngine = hass.data[DOMAIN][entry.entry_id]["engine"]
    async_add_entities([SimulatorProgramSelect(engine, entry)])


class SimulatorProgramSelect(SelectEntity):
    """Select entity for choosing the simulation program."""

    _attr_has_entity_name = True
    _attr_name = "Program"
    _attr_should_poll = False

    def __init__(self, engine: SimulationEngine, entry: ConfigEntry) -> None:
        self._engine = engine
        self._attr_unique_id = f"{entry.entry_id}_program"
        self._attr_options = [p.name for p in PROGRAMS.values()]
        self._attr_current_option = engine.program.name
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": entry.title,
            "manufacturer": "Wattson Simulator",
            "model": "Virtual Appliance",
        }
        self._key_by_name = {p.name: k for k, p in PROGRAMS.items()}

    async def async_select_option(self, option: str) -> None:
        """Change the selected program."""
        key = self._key_by_name.get(option)
        if key:
            self._engine.set_program(key)
            self._attr_current_option = option
            self.async_write_ha_state()
