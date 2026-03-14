"""Unit tests for CycleRecorder."""

from __future__ import annotations

import pytest

from custom_components.wattson.cycle_recorder import CycleData, CycleRecorder


@pytest.fixture
def recorder() -> CycleRecorder:
    """Return a fresh CycleRecorder with default settings."""
    return CycleRecorder()


class TestBasicLifecycle:
    """start -> record -> finish produces CycleData."""

    def test_basic_cycle(self, recorder: CycleRecorder) -> None:
        recorder.start(0.0)
        recorder.record(100.0, 0.0)
        recorder.record(100.0, 30.0)
        recorder.record(100.0, 60.0)
        data = recorder.finish(60.0)

        assert isinstance(data, CycleData)
        assert data.start_time == 0.0
        assert data.end_time == 60.0
        assert data.duration_s == 60.0
        assert len(data.samples) >= 2
        assert data.profile_id is None

    def test_finish_returns_energy(self, recorder: CycleRecorder) -> None:
        recorder.start(0.0)
        recorder.record(100.0, 0.0)
        recorder.record(100.0, 36.0)  # 100W * 36s / 3600 = 1.0 Wh
        data = recorder.finish(36.0)
        assert data.energy_wh == pytest.approx(1.0, rel=0.01)


class TestDownsampling:
    """Delta-based downsampling reduces stored samples."""

    def test_small_power_changes_skipped(self, recorder: CycleRecorder) -> None:
        recorder.start(0.0)
        recorder.record(100.0, 0.0)
        # Tiny changes — should be skipped
        recorder.record(100.3, 1.0)
        recorder.record(100.5, 2.0)
        recorder.record(100.2, 3.0)
        # Significant change
        recorder.record(200.0, 4.0)
        data = recorder.finish(4.0)

        # Should have first sample, the big change, and possibly the finish.
        # Definitely fewer than 5 samples.
        assert len(data.samples) < 5

    def test_time_based_forced_sample(self) -> None:
        """Even with no power change, a sample is forced every DOWNSAMPLE_TIME_DELTA."""
        rec = CycleRecorder(time_delta=60.0, power_delta=1.0)
        rec.start(0.0)
        rec.record(100.0, 0.0)
        rec.record(100.0, 30.0)  # skipped (within time delta, no power change)
        rec.record(100.0, 61.0)  # forced (> 60s since last stored)
        rec.record(100.0, 90.0)  # skipped
        rec.record(100.0, 125.0)  # forced (> 60s since last stored at t=61)
        data = rec.finish(125.0)

        # Expect: t=0, t=61, t=125 = 3 samples
        assert len(data.samples) == 3

    def test_large_power_change_always_stored(self, recorder: CycleRecorder) -> None:
        recorder.start(0.0)
        recorder.record(100.0, 0.0)
        recorder.record(200.0, 1.0)
        recorder.record(50.0, 2.0)
        recorder.record(300.0, 3.0)
        data = recorder.finish(3.0)

        # All changes are > 1W delta, so all should be stored.
        assert len(data.samples) == 4


class TestEnergyAccumulation:
    """Energy calculated via trapezoidal integration."""

    def test_constant_power(self, recorder: CycleRecorder) -> None:
        recorder.start(0.0)
        recorder.record(360.0, 0.0)
        recorder.record(360.0, 10.0)  # 360W * 10s / 3600 = 1.0 Wh
        data = recorder.finish(10.0)
        assert data.energy_wh == pytest.approx(1.0, rel=0.01)

    def test_ramp_up(self, recorder: CycleRecorder) -> None:
        recorder.start(0.0)
        recorder.record(0.0, 0.0)
        recorder.record(720.0, 10.0)  # (0+720)/2 * 10/3600 = 1.0 Wh
        data = recorder.finish(10.0)
        assert data.energy_wh == pytest.approx(1.0, rel=0.01)


class TestEmptyCycle:
    """Start then immediate finish."""

    def test_empty(self, recorder: CycleRecorder) -> None:
        recorder.start(5.0)
        data = recorder.finish(5.0)
        assert data.duration_s == 0.0
        assert data.energy_wh == 0.0
        assert data.samples == []


class TestMultipleCycles:
    """Recorder resets properly between cycles."""

    def test_sequential_cycles(self, recorder: CycleRecorder) -> None:
        # First cycle
        recorder.start(0.0)
        recorder.record(100.0, 0.0)
        recorder.record(100.0, 10.0)
        data1 = recorder.finish(10.0)

        # Second cycle
        recorder.start(20.0)
        recorder.record(200.0, 20.0)
        recorder.record(200.0, 30.0)
        data2 = recorder.finish(30.0)

        assert data1.start_time == 0.0
        assert data2.start_time == 20.0
        assert data2.energy_wh > data1.energy_wh
        assert data1.samples[0][1] == pytest.approx(100.0)
        assert data2.samples[0][1] == pytest.approx(200.0)
