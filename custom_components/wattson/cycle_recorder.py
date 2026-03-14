"""Cycle power data recorder with delta-based downsampling."""

from __future__ import annotations

from dataclasses import dataclass

from .const import DOWNSAMPLE_POWER_DELTA, DOWNSAMPLE_TIME_DELTA
from .energy import trapezoidal_energy_wh


@dataclass
class CycleData:
    """Completed cycle data."""

    start_time: float
    end_time: float
    duration_s: float
    energy_wh: float
    samples: list[tuple[float, float]]
    profile_id: str | None = None

    def to_dict(self) -> dict[str, object]:
        """Serialize to a plain dict for storage."""
        return {
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration_s": self.duration_s,
            "energy_wh": self.energy_wh,
            "samples": self.samples,
            "profile_id": self.profile_id,
        }


class CycleRecorder:
    """Records downsampled power curves during an appliance cycle.

    Pure logic — no Home Assistant dependencies.
    """

    def __init__(
        self,
        power_delta: float = DOWNSAMPLE_POWER_DELTA,
        time_delta: float = DOWNSAMPLE_TIME_DELTA,
    ) -> None:
        self._power_delta = power_delta
        self._time_delta = time_delta
        self._start_time: float = 0.0
        self._samples: list[tuple[float, float]] = []
        self._energy_wh: float = 0.0
        self._last_stored_power: float | None = None
        self._last_stored_time: float | None = None
        self._last_power: float | None = None
        self._last_time: float | None = None

    def start(self, timestamp: float) -> None:
        """Begin recording a new cycle."""
        self._start_time = timestamp
        self._samples = []
        self._energy_wh = 0.0
        self._last_stored_power = None
        self._last_stored_time = None
        self._last_power = None
        self._last_time = None

    def record(self, power_w: float, timestamp: float) -> None:
        """Record a power reading, applying delta-based downsampling."""
        self._accumulate_energy(power_w, timestamp)

        should_store = False

        if (
            self._last_stored_power is None
            or abs(power_w - self._last_stored_power) >= self._power_delta
            or (
                self._last_stored_time is not None
                and (timestamp - self._last_stored_time) >= self._time_delta
            )
        ):
            should_store = True

        if should_store:
            self._samples.append((timestamp - self._start_time, power_w))
            self._last_stored_power = power_w
            self._last_stored_time = timestamp

        self._last_power = power_w
        self._last_time = timestamp

    def finish(self, end_time: float) -> CycleData:
        """Finish recording and return the completed cycle data."""
        return CycleData(
            start_time=self._start_time,
            end_time=end_time,
            duration_s=end_time - self._start_time,
            energy_wh=self._energy_wh,
            samples=list(self._samples),
        )

    def _accumulate_energy(self, power_w: float, timestamp: float) -> None:
        """Trapezoidal integration for energy."""
        if self._last_power is not None and self._last_time is not None:
            self._energy_wh += trapezoidal_energy_wh(
                self._last_power,
                self._last_time,
                power_w,
                timestamp,
            )
