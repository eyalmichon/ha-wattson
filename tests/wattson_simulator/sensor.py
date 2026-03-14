"""Power sensor for the Wattson Simulator."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import UnitOfPower

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
    """Set up the simulator power sensor."""
    engine: SimulationEngine = hass.data[DOMAIN][entry.entry_id]["engine"]
    sensor = SimulatorPowerSensor(engine, entry)
    engine.set_sensor_callback(sensor.async_write_ha_state)
    async_add_entities([sensor])


class SimulatorPowerSensor(SensorEntity):
    """Sensor that exposes the current simulated power draw."""

    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_has_entity_name = True
    _attr_name = "Power"
    _attr_should_poll = False

    def __init__(self, engine: SimulationEngine, entry: ConfigEntry) -> None:
        self._engine = engine
        self._attr_unique_id = f"{entry.entry_id}_power"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": entry.title,
            "manufacturer": "Wattson Simulator",
            "model": "Virtual Appliance",
        }

    @property
    def native_value(self) -> float:
        """Return the current power."""
        return round(self._engine.power_w, 1)
