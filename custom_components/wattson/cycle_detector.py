"""Cycle detection state machine for Wattson."""

from __future__ import annotations

from dataclasses import dataclass

from .const import (
    DEFAULT_END_DELAY_S,
    DEFAULT_OFF_THRESHOLD_W,
    DEFAULT_START_DURATION_S,
    DEFAULT_START_ENERGY_WH,
    DEFAULT_START_THRESHOLD_W,
    CycleState,
)
from .energy import trapezoidal_energy_wh


@dataclass
class CycleDetectorConfig:
    """Configuration for the cycle detector thresholds and timing."""

    start_threshold_w: float = DEFAULT_START_THRESHOLD_W
    off_threshold_w: float = DEFAULT_OFF_THRESHOLD_W
    start_duration_s: float = DEFAULT_START_DURATION_S
    start_energy_wh: float = DEFAULT_START_ENERGY_WH
    end_delay_s: float = DEFAULT_END_DELAY_S


class CycleDetector:
    """State machine that detects appliance on/off cycles from power readings.

    States: OFF -> STARTING -> RUNNING -> OFF.
    Pure logic — no Home Assistant dependencies.
    """

    def __init__(self, config: CycleDetectorConfig) -> None:
        self._config = config
        self._state = CycleState.OFF

        self._last_power: float | None = None
        self._last_timestamp: float | None = None

        self._cycle_start_time: float | None = None
        self._cycle_energy_wh: float = 0.0

        self._low_power_since: float | None = None

    # --- Public properties ---

    @property
    def state(self) -> CycleState:
        """Current cycle state."""
        return self._state

    @property
    def cycle_energy_wh(self) -> float:
        """Accumulated energy in the current cycle (Wh)."""
        return self._cycle_energy_wh

    @property
    def cycle_start_time(self) -> float | None:
        """Timestamp when the current cycle started, or None if OFF."""
        return self._cycle_start_time

    def update(self, power_w: float, timestamp: float) -> CycleState:
        """Feed a power reading and advance the state machine.

        Returns the new state after processing.
        """
        if self._state != CycleState.OFF:
            self._accumulate_energy(power_w, timestamp)

        match self._state:
            case CycleState.OFF:
                self._handle_off(power_w, timestamp)
            case CycleState.STARTING:
                self._handle_starting(power_w, timestamp)
            case CycleState.RUNNING:
                self._handle_running(power_w, timestamp)

        self._last_power = power_w
        self._last_timestamp = timestamp
        return self._state

    def update_end_delay(self, end_delay_s: float) -> None:
        """Update the end-delay dynamically (e.g. from adaptive params)."""
        self._config.end_delay_s = end_delay_s

    # --- State handlers ---

    def _handle_off(self, power_w: float, timestamp: float) -> None:
        if power_w >= self._config.start_threshold_w:
            self._state = CycleState.STARTING
            self._cycle_start_time = timestamp
            self._cycle_energy_wh = 0.0

    def _handle_starting(self, power_w: float, timestamp: float) -> None:
        if power_w < self._config.start_threshold_w:
            self._reset()
            return

        if self._cycle_start_time is None:
            return

        duration = timestamp - self._cycle_start_time
        if (
            duration >= self._config.start_duration_s
            and self._cycle_energy_wh >= self._config.start_energy_wh
        ):
            self._state = CycleState.RUNNING
            self._low_power_since = None

    def _handle_running(self, power_w: float, timestamp: float) -> None:
        if power_w < self._config.off_threshold_w:
            if self._low_power_since is None:
                self._low_power_since = timestamp
            elif timestamp - self._low_power_since >= self._config.end_delay_s:
                self._reset()
        else:
            self._low_power_since = None

    # --- Helpers ---

    def _accumulate_energy(self, power_w: float, timestamp: float) -> None:
        """Trapezoidal integration of energy between consecutive readings."""
        if self._last_power is not None and self._last_timestamp is not None:
            self._cycle_energy_wh += trapezoidal_energy_wh(
                self._last_power,
                self._last_timestamp,
                power_w,
                timestamp,
            )

    def _reset(self) -> None:
        self._state = CycleState.OFF
        self._cycle_start_time = None
        self._cycle_energy_wh = 0.0
        self._low_power_since = None
