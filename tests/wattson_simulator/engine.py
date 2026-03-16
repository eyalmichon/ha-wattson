"""Simulation engine that drives power output through program phases."""

from __future__ import annotations

import logging
import random
from datetime import timedelta
from typing import TYPE_CHECKING

from homeassistant.core import CALLBACK_TYPE, callback
from homeassistant.helpers.event import async_track_time_interval

from .const import PROGRAMS, TICK_INTERVAL_S, Phase, PhaseType, Program

if TYPE_CHECKING:
    from datetime import datetime

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


class SimulationEngine:
    """Drives a simulated appliance through power phases on a timer tick."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry

        self._program_key: str = "normal_dry"
        self._running: bool = False
        self._power_w: float = 0.0

        self._phase_index: int = 0
        self._phase_elapsed_s: float = 0.0
        self._intermittent_elapsed_s: float = 0.0

        self._unsub_timer: CALLBACK_TYPE | None = None
        self._sensor_callback: callback | None = None
        self._switch_callback: callback | None = None

    @property
    def power_w(self) -> float:
        """Current simulated power in watts."""
        return self._power_w

    @property
    def running(self) -> bool:
        """Whether the simulation is currently active."""
        return self._running

    @property
    def program_key(self) -> str:
        """Current program key."""
        return self._program_key

    @property
    def program(self) -> Program:
        """Current program definition."""
        return PROGRAMS[self._program_key]

    def set_program(self, key: str) -> None:
        """Change the selected program (only effective before starting)."""
        if key in PROGRAMS:
            self._program_key = key

    def set_sensor_callback(self, cb: callback) -> None:
        """Register the sensor entity's state-update callback."""
        self._sensor_callback = cb

    def set_switch_callback(self, cb: callback) -> None:
        """Register the switch entity's state-update callback."""
        self._switch_callback = cb

    def start(self) -> None:
        """Begin the simulation cycle."""
        if self._running:
            return

        _LOGGER.debug(
            "Simulator starting program '%s' for %s",
            self.program.name,
            self.entry.title,
        )

        self._running = True
        self._phase_index = 0
        self._phase_elapsed_s = 0.0
        self._intermittent_elapsed_s = 0.0
        self._power_w = 0.0

        self._unsub_timer = async_track_time_interval(
            self.hass,
            self._tick,
            timedelta(seconds=TICK_INTERVAL_S),
        )
        self._tick(None)

    def stop(self) -> None:
        """Stop the simulation and reset power to 0."""
        self._running = False
        self._power_w = 0.0
        if self._unsub_timer is not None:
            self._unsub_timer()
            self._unsub_timer = None
        self._notify_sensor()
        self._notify_switch()

    @callback
    def _tick(self, _now: datetime | None) -> None:
        """Advance the simulation by one tick interval."""
        if not self._running:
            return

        program = self.program
        if self._phase_index >= len(program.phases):
            self.stop()
            return

        phase = program.phases[self._phase_index]
        self._phase_elapsed_s += TICK_INTERVAL_S

        if phase.phase_type == PhaseType.INTERMITTENT:
            self._power_w = self._compute_intermittent(phase)
        elif phase.phase_type == PhaseType.RAMP:
            self._power_w = self._compute_ramp(phase)
        elif phase.phase_type == PhaseType.NOISY:
            self._power_w = self._compute_noisy(phase)
        elif phase.phase_type == PhaseType.REPLAY:
            self._power_w = self._compute_replay(phase)
        else:
            self._power_w = self._compute_constant(phase)

        if self._phase_elapsed_s >= phase.duration_s:
            self._phase_index += 1
            self._phase_elapsed_s = 0.0
            self._intermittent_elapsed_s = 0.0

        self._notify_sensor()

    def _compute_constant(self, phase: Phase) -> float:
        """Compute power for a constant phase with gaussian noise."""
        noise = random.gauss(0, phase.noise_w) if phase.noise_w > 0 else 0.0
        return max(0.0, phase.power_w + noise)

    def _compute_intermittent(self, phase: Phase) -> float:
        """Compute power for an intermittent (anti-wrinkle) phase."""
        cycle_period = phase.on_duration_s + phase.off_duration_s
        if cycle_period <= 0:
            return 0.0

        self._intermittent_elapsed_s += TICK_INTERVAL_S
        pos_in_cycle = self._intermittent_elapsed_s % cycle_period

        if pos_in_cycle <= phase.on_duration_s:
            noise = random.gauss(0, phase.noise_w) if phase.noise_w > 0 else 0.0
            return max(0.0, phase.power_w + noise)
        return 0.0

    def _compute_ramp(self, phase: Phase) -> float:
        """Compute power for a linear ramp from power_w to ramp_to_w."""
        progress = min(self._phase_elapsed_s / phase.duration_s, 1.0)
        base = phase.power_w + (phase.ramp_to_w - phase.power_w) * progress
        noise = random.gauss(0, phase.noise_w) if phase.noise_w > 0 else 0.0
        return max(0.0, base + noise)

    def _compute_noisy(self, phase: Phase) -> float:
        """Compute power with high non-Gaussian noise and occasional spikes."""
        base = phase.power_w
        noise = (
            random.uniform(-phase.noise_w, phase.noise_w) if phase.noise_w > 0 else 0.0
        )
        value = base + noise
        if phase.spike_pct > 0 and random.random() < phase.spike_pct:
            value += base * random.uniform(0.5, 1.5)
        return max(0.0, value)

    def _compute_replay(self, phase: Phase) -> float:
        """Play back pre-recorded power data tick by tick."""
        if not phase.replay_data:
            return 0.0
        idx = int(self._phase_elapsed_s / TICK_INTERVAL_S)
        idx = min(idx, len(phase.replay_data) - 1)
        return max(0.0, phase.replay_data[idx])

    @callback
    def _notify_sensor(self) -> None:
        if self._sensor_callback is not None:
            self._sensor_callback()

    @callback
    def _notify_switch(self) -> None:
        if self._switch_callback is not None:
            self._switch_callback()
