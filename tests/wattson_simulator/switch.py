"""Start/stop switch for the Wattson Simulator."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity

from .const import DOMAIN

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
    """Set up the simulator start/stop switch."""
    engine: SimulationEngine = hass.data[DOMAIN][entry.entry_id]["engine"]
    switch = SimulatorSwitch(engine, entry)
    engine.set_switch_callback(switch.async_write_ha_state)
    async_add_entities([switch])


class SimulatorSwitch(SwitchEntity):
    """Switch to start and stop the simulation cycle."""

    _attr_device_class = SwitchDeviceClass.SWITCH
    _attr_has_entity_name = True
    _attr_name = "Cycle"
    _attr_should_poll = False

    def __init__(self, engine: SimulationEngine, entry: ConfigEntry) -> None:
        self._engine = engine
        self._attr_unique_id = f"{entry.entry_id}_switch"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": entry.title,
            "manufacturer": "Wattson Simulator",
            "model": "Virtual Appliance",
        }

    @property
    def is_on(self) -> bool:
        """Return true if the simulation is running."""
        return self._engine.running

    async def async_turn_on(self) -> None:
        """Start the simulation."""
        self._engine.start()
        self.async_write_ha_state()

    async def async_turn_off(self) -> None:
        """Stop the simulation."""
        self._engine.stop()
        self.async_write_ha_state()
