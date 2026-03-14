"""Binary sensor platform for Wattson."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)

from .const import CycleState
from .entity import WattsonEntity, get_coordinator

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .coordinator import WattsonCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Wattson binary sensor entities."""
    coordinator = get_coordinator(hass, entry)

    entity = WattsonRunningSensor(coordinator, entry)
    coordinator.register_entity(entity)
    async_add_entities([entity])


class WattsonRunningSensor(WattsonEntity, BinarySensorEntity):
    """Binary sensor that is on when the appliance cycle is active."""

    _attr_device_class = BinarySensorDeviceClass.RUNNING

    def __init__(self, coordinator: WattsonCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "running")
        self._attr_name = "Running"

    @property
    def is_on(self) -> bool:
        if self.coordinator.detector.state != CycleState.RUNNING:
            return False
        return not self.coordinator.cycle_done_by_phase
