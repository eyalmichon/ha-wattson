"""Sensor platform for Wattson."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, ClassVar

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import UnitOfPower, UnitOfTime

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
    """Set up Wattson sensor entities."""
    coordinator = get_coordinator(hass, entry)

    entities = [
        WattsonStateSensor(coordinator, entry),
        WattsonPowerSensor(coordinator, entry),
        WattsonElapsedSensor(coordinator, entry),
        WattsonProgramSensor(coordinator, entry),
        WattsonTimeRemainingSensor(coordinator, entry),
        WattsonPhaseSensor(coordinator, entry),
    ]

    for entity in entities:
        coordinator.register_entity(entity)

    async_add_entities(entities)


class WattsonBaseSensor(WattsonEntity, SensorEntity):
    """Base class for Wattson sensors."""


class WattsonStateSensor(WattsonBaseSensor):
    """Sensor that reports the current cycle state."""

    _attr_translation_key = "cycle_state"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options: ClassVar[list[str]] = [s.value for s in CycleState]

    def __init__(self, coordinator: WattsonCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "state")
        self._attr_name = "State"

    @property
    def native_value(self) -> str:
        return self.coordinator.detector.state.value


class WattsonPowerSensor(WattsonBaseSensor):
    """Sensor that reports the current power reading."""

    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 1

    def __init__(self, coordinator: WattsonCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "current_power")
        self._attr_name = "Current Power"

    @property
    def native_value(self) -> float:
        return self.coordinator.current_power


class WattsonElapsedSensor(WattsonBaseSensor):
    """Sensor that reports elapsed time in the current cycle."""

    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_suggested_display_precision = 0

    def __init__(self, coordinator: WattsonCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "elapsed_time")
        self._attr_name = "Elapsed Time"

    @property
    def native_value(self) -> float:
        start = self.coordinator.detector.cycle_start_time
        if start is None:
            return 0.0
        return time.time() - start


class WattsonProgramSensor(WattsonBaseSensor):
    """Sensor that reports the matched program name."""

    def __init__(self, coordinator: WattsonCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "program")
        self._attr_name = "Program"

    @property
    def native_value(self) -> str | None:
        result = self.coordinator.match_result
        if result is None:
            return None
        profile = self.coordinator.store.get_profile(result.profile_id)
        if profile is not None and profile.name:
            return profile.name
        profiles = self.coordinator.store.profiles
        for idx, p in enumerate(profiles, start=1):
            if p.id == result.profile_id:
                return f"Program #{idx}"
        return "Unknown program"

    @property
    def extra_state_attributes(self) -> dict[str, object] | None:
        """Expose profile details as entity attributes."""
        result = self.coordinator.match_result
        if result is None:
            return None
        attrs: dict[str, object] = {
            "profile_id": result.profile_id,
            "correlation": round(result.correlation, 3),
            "match_score": round(result.score, 3),
        }
        if result.dtw_distance is not None:
            attrs["dtw_distance"] = round(result.dtw_distance, 1)
        profile = self.coordinator.store.get_profile(result.profile_id)
        if profile is not None:
            attrs["cycle_count"] = profile.cycle_count
            attrs["avg_duration_s"] = round(profile.avg_duration_s, 1)
            attrs["avg_energy_wh"] = round(profile.avg_energy_wh, 3)
        return attrs


class WattsonTimeRemainingSensor(WattsonBaseSensor):
    """Sensor that reports estimated time remaining."""

    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_suggested_display_precision = 0

    def __init__(self, coordinator: WattsonCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "time_remaining")
        self._attr_name = "Time Remaining"

    @property
    def native_value(self) -> float | None:
        return self.coordinator.time_remaining


class WattsonPhaseSensor(WattsonBaseSensor):
    """Sensor that reports the current phase within the matched profile."""

    _attr_icon = "mdi:transit-connection-variant"

    def __init__(self, coordinator: WattsonCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "phase")
        self._attr_name = "Phase"

    @property
    def native_value(self) -> str | None:
        if self.coordinator.detector.state != CycleState.RUNNING:
            return None
        return self.coordinator.current_phase_name

    @property
    def extra_state_attributes(self) -> dict[str, object] | None:
        idx = self.coordinator.current_phase_index
        if idx is None:
            return None

        result = self.coordinator.match_result
        if result is None:
            return None

        profile = self.coordinator.store.get_profile(result.profile_id)
        if profile is None or not profile.phases:
            return None

        attrs: dict[str, object] = {
            "phase_index": idx,
            "total_phases": len(profile.phases),
        }
        if idx < len(profile.phases):
            phase = profile.phases[idx]
            attrs["phase_avg_power_w"] = round(phase.avg_power_w, 1)
            attrs["phase_pattern"] = phase.pattern
            attrs["marks_cycle_done"] = phase.marks_cycle_done
        return attrs
