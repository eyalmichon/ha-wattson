"""Unit tests for CycleDetector state machine."""

from __future__ import annotations

import pytest

from custom_components.wattson.const import (
    DEFAULT_END_DELAY_S,
    CycleState,
)
from custom_components.wattson.cycle_detector import CycleDetector, CycleDetectorConfig


@pytest.fixture
def default_config() -> CycleDetectorConfig:
    """Return a CycleDetectorConfig with default thresholds."""
    return CycleDetectorConfig()


@pytest.fixture
def detector(default_config: CycleDetectorConfig) -> CycleDetector:
    """Return a fresh CycleDetector."""
    return CycleDetector(default_config)


def _enter_running(det: CycleDetector, start_power: float = 100.0) -> float:
    """Drive the detector through OFF -> STARTING -> RUNNING.

    Returns the timestamp at which RUNNING is reached.
    """
    det.update(start_power, 0.0)
    det.update(start_power, 10.0)
    assert det.state == CycleState.RUNNING
    return 10.0


def _run_to_off(det: CycleDetector, t: float, low_power: float = 0.0) -> float:
    """Drive from RUNNING to OFF via end_delay expiry. Returns final timestamp."""
    t += 1.0
    det.update(low_power, t)
    t += DEFAULT_END_DELAY_S + 1.0
    det.update(low_power, t)
    assert det.state == CycleState.OFF
    return t


class TestInitialState:
    """Detector starts in OFF state."""

    def test_starts_off(self, detector: CycleDetector) -> None:
        assert detector.state == CycleState.OFF

    def test_initial_energy_is_zero(self, detector: CycleDetector) -> None:
        assert detector.cycle_energy_wh == 0.0

    def test_no_cycle_start_time(self, detector: CycleDetector) -> None:
        assert detector.cycle_start_time is None


class TestOffToStarting:
    """OFF -> STARTING when power >= start_threshold."""

    def test_power_above_threshold_transitions_to_starting(
        self, detector: CycleDetector
    ) -> None:
        state = detector.update(10.0, 0.0)
        assert state == CycleState.STARTING

    def test_power_below_threshold_stays_off(self, detector: CycleDetector) -> None:
        state = detector.update(2.0, 0.0)
        assert state == CycleState.OFF

    def test_power_exactly_at_threshold(self, detector: CycleDetector) -> None:
        state = detector.update(5.0, 0.0)
        assert state == CycleState.STARTING


class TestStartingToRunning:
    """STARTING -> RUNNING when duration and energy gates are met."""

    def test_full_transition(self, detector: CycleDetector) -> None:
        detector.update(100.0, 0.0)  # -> STARTING
        assert detector.state == CycleState.STARTING

        # 100W for 10s = 100*10/3600 ~ 0.278 Wh > 0.2 Wh gate
        state = detector.update(100.0, 10.0)
        assert state == CycleState.RUNNING

    def test_duration_met_but_energy_insufficient(self) -> None:
        """Very low power for long enough should NOT transition if energy gate fails."""
        config = CycleDetectorConfig(
            start_threshold_w=1.0,
            start_energy_wh=0.2,
            start_duration_s=5.0,
        )
        det = CycleDetector(config)
        det.update(1.5, 0.0)  # -> STARTING
        # 1.5W for 6s = 0.0025 Wh << 0.2 Wh
        state = det.update(1.5, 6.0)
        assert state != CycleState.RUNNING


class TestStartingToOff:
    """STARTING -> OFF when power drops before confirmation."""

    def test_power_drops_back(self, detector: CycleDetector) -> None:
        detector.update(10.0, 0.0)  # -> STARTING
        state = detector.update(0.5, 1.0)
        assert state == CycleState.OFF


class TestRunningToOff:
    """RUNNING -> OFF when power stays below off_threshold for end_delay."""

    def test_off_after_end_delay(self, detector: CycleDetector) -> None:
        """Power below off_threshold for end_delay transitions to OFF."""
        t = _enter_running(detector)
        t += 1.0
        detector.update(0.5, t)
        t += DEFAULT_END_DELAY_S + 1.0
        state = detector.update(0.5, t)
        assert state == CycleState.OFF

    def test_full_cycle_off(self, detector: CycleDetector) -> None:
        """Complete RUNNING -> OFF with enough silence."""
        t = _enter_running(detector)
        t = _run_to_off(detector, t)
        assert detector.cycle_start_time is None

    def test_no_off_if_power_recovers_during_running(
        self, detector: CycleDetector
    ) -> None:
        t = _enter_running(detector)
        t += 1.0
        detector.update(0.5, t)
        t += 10.0
        state = detector.update(50.0, t)
        assert state == CycleState.RUNNING


class TestEnergyIntegration:
    """Trapezoidal energy integration accuracy."""

    def test_constant_power(self, detector: CycleDetector) -> None:
        detector.update(100.0, 0.0)  # -> STARTING
        detector.update(100.0, 36.0)  # -> RUNNING, 100W * 36s / 3600 = 1.0 Wh
        assert detector.cycle_energy_wh == pytest.approx(1.0, rel=0.01)

    def test_varying_power(self, detector: CycleDetector) -> None:
        detector.update(50.0, 0.0)  # -> STARTING
        # Trapezoidal: (50+100)/2 * 36 / 3600 = 0.75 Wh
        detector.update(100.0, 36.0)
        assert detector.cycle_energy_wh == pytest.approx(0.75, rel=0.01)


class TestCycleStartTime:
    """cycle_start_time tracks when the cycle began."""

    def test_set_on_starting(self, detector: CycleDetector) -> None:
        detector.update(100.0, 42.0)
        assert detector.cycle_start_time == 42.0

    def test_cleared_on_off(self, detector: CycleDetector) -> None:
        detector.update(100.0, 0.0)
        detector.update(0.5, 1.0)  # back to OFF
        assert detector.cycle_start_time is None


class TestRapidFluctuations:
    """Rapid power fluctuations shouldn't cause erratic state changes."""

    def test_brief_spike_from_off(self, detector: CycleDetector) -> None:
        detector.update(10.0, 0.0)  # -> STARTING
        detector.update(0.0, 0.5)  # immediate drop -> OFF
        assert detector.state == CycleState.OFF

    def test_oscillating_around_threshold(self, detector: CycleDetector) -> None:
        """Power oscillates around the start threshold — shouldn't reach RUNNING."""
        detector.update(6.0, 0.0)  # -> STARTING
        detector.update(3.0, 1.0)  # drop -> OFF
        detector.update(6.0, 2.0)  # -> STARTING
        detector.update(3.0, 3.0)  # drop -> OFF
        assert detector.state == CycleState.OFF


class TestIntermittentPatterns:
    """Intermittent power patterns during RUNNING."""

    def _make_detector(self, **overrides: float) -> CycleDetector:
        defaults = {
            "start_threshold_w": 5.0,
            "off_threshold_w": 1.0,
            "end_delay_s": 30.0,
        }
        return CycleDetector(CycleDetectorConfig(**{**defaults, **overrides}))

    def test_intermittent_keeps_running(self) -> None:
        """Brief bursts every 10s keep the cycle alive (each resets end_delay)."""
        det = self._make_detector()
        t = _enter_running(det, start_power=2000.0)

        for _ in range(15):
            t += 8.0
            det.update(0.0, t)
            t += 2.0
            det.update(80.0, t)

        assert det.state == CycleState.RUNNING

    def test_cycle_ends_after_bursts_stop(self) -> None:
        """Cycle ends once intermittent bursts stop and end_delay passes."""
        det = self._make_detector()
        t = _enter_running(det, start_power=2000.0)

        for _ in range(5):
            t += 8.0
            det.update(0.0, t)
            t += 2.0
            det.update(80.0, t)

        assert det.state == CycleState.RUNNING

        _run_to_off(det, t)

    def test_sustained_power_prevents_end(self) -> None:
        """Sustained high power resets the end timer."""
        det = self._make_detector()
        t = _enter_running(det, start_power=2000.0)

        t += 5.0
        det.update(0.0, t)

        t += 15.0
        det.update(200.0, t)

        t += 15.0
        det.update(200.0, t)

        assert det.state == CycleState.RUNNING

    def test_post_cycle_spike_delays_but_still_ends(self) -> None:
        """A pump-out spike resets the end countdown, but cycle ends after enough silence."""
        det = self._make_detector(end_delay_s=30.0)
        det.update(1500.0, 0.0)
        det.update(1500.0, 10.0)
        assert det.state == CycleState.RUNNING
        t = 10.0

        t += 1.0
        det.update(0.0, t)
        t += 20.0
        det.update(0.0, t)

        t += 1.0
        det.update(200.0, t)

        _run_to_off(det, t)


class TestAdaptiveEndDelay:
    """End delay can be updated dynamically to bridge longer gaps."""

    def test_update_end_delay_bridges_longer_gap(self) -> None:
        det = CycleDetector(CycleDetectorConfig(end_delay_s=30.0))
        t = _enter_running(det, start_power=2000.0)

        det.update_end_delay(120.0)

        t += 1.0
        det.update(0.0, t)
        t += 60.0
        det.update(0.0, t)
        assert det.state == CycleState.RUNNING

        t += 70.0
        det.update(0.0, t)
        assert det.state == CycleState.OFF

    def test_short_default_then_adaptive(self) -> None:
        """First cycle uses default, subsequent uses adapted delay."""
        det = CycleDetector(CycleDetectorConfig(end_delay_s=30.0))
        t = _enter_running(det, start_power=2000.0)

        # 40s gap with 30s end_delay -> cycle ends
        t += 1.0
        det.update(0.0, t)
        t += 40.0
        det.update(0.0, t)
        assert det.state == CycleState.OFF

        # Simulate adaptive update
        det.update_end_delay(120.0)

        t += 10.0
        det.update(2000.0, t)
        t += 10.0
        det.update(2000.0, t)
        assert det.state == CycleState.RUNNING

        # Same 40s gap now bridged by 120s delay
        t += 1.0
        det.update(0.0, t)
        t += 40.0
        det.update(0.0, t)
        assert det.state == CycleState.RUNNING
