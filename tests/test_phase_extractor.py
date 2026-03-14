"""Unit tests for the phase extraction algorithm."""

from __future__ import annotations

import pytest

from custom_components.wattson.phase_extractor import extract_phases
from custom_components.wattson.profile_matcher import ProfilePhase


def _constant_samples(
    power_w: float, duration_s: float, start_s: float = 0.0, step_s: float = 1.0
) -> list[tuple[float, float]]:
    """Generate constant-power samples."""
    samples = []
    t = start_s
    while t <= start_s + duration_s:
        samples.append((t, power_w))
        t += step_s
    return samples


def _multi_phase_samples(
    phases: list[tuple[float, float]], step_s: float = 1.0
) -> list[tuple[float, float]]:
    """Generate samples for multiple constant phases.

    phases: list of (power_w, duration_s) tuples.
    """
    samples: list[tuple[float, float]] = []
    t = 0.0
    for power_w, duration_s in phases:
        end_t = t + duration_s
        while t <= end_t:
            samples.append((t, power_w))
            t += step_s
    return samples


def _intermittent_samples(
    peak_w: float,
    on_s: float,
    off_s: float,
    total_s: float,
    start_s: float = 0.0,
    step_s: float = 1.0,
) -> list[tuple[float, float]]:
    """Generate intermittent on/off power samples."""
    samples = []
    t = start_s
    cycle = on_s + off_s
    while t <= start_s + total_s:
        pos = (t - start_s) % cycle
        power = peak_w if pos < on_s else 0.0
        samples.append((t, power))
        t += step_s
    return samples


class TestBasicExtraction:
    """Test phase extraction on clean, synthetic signals."""

    def test_single_constant_phase(self) -> None:
        samples = _constant_samples(1000.0, 120.0)
        phases = extract_phases(samples)
        assert len(phases) >= 1
        assert phases[0].avg_power_w > 0

    def test_two_distinct_phases(self) -> None:
        samples = _multi_phase_samples([(2000.0, 60.0), (200.0, 60.0)])
        phases = extract_phases(samples, min_duration_s=10.0)
        assert len(phases) >= 2
        high_phase = phases[0]
        low_phase = phases[-1]
        assert high_phase.avg_power_w > low_phase.avg_power_w

    def test_three_phase_dryer(self) -> None:
        """Simulate: Heating (2000W, 90s) -> Cooldown (200W, 30s) -> Low (50W, 30s)."""
        samples = _multi_phase_samples([(2000.0, 90.0), (200.0, 30.0), (50.0, 30.0)])
        phases = extract_phases(samples, min_duration_s=10.0)
        assert len(phases) >= 2
        assert phases[0].start_pct == pytest.approx(0.0, abs=0.05)
        assert phases[-1].end_pct == pytest.approx(1.0, abs=0.05)

    def test_phase_boundaries_cover_full_range(self) -> None:
        samples = _multi_phase_samples([(1500.0, 60.0), (100.0, 60.0)])
        phases = extract_phases(samples, min_duration_s=10.0)
        assert phases[0].start_pct == pytest.approx(0.0, abs=0.05)
        assert phases[-1].end_pct == pytest.approx(1.0, abs=0.05)


class TestIntermittentDetection:
    """Test that intermittent patterns are classified correctly."""

    def test_constant_then_intermittent(self) -> None:
        """Heating at 2000W then intermittent 80W/0W anti-wrinkle."""
        heating = _constant_samples(2000.0, 60.0)
        anti_wrinkle = _intermittent_samples(
            80.0, on_s=2.0, off_s=8.0, total_s=90.0, start_s=61.0
        )
        samples = heating + anti_wrinkle
        phases = extract_phases(samples, min_duration_s=10.0)

        assert len(phases) >= 2
        last_phase = phases[-1]
        assert last_phase.pattern == "intermittent"
        assert last_phase.avg_power_w < 100.0


class TestEdgeCases:
    """Edge cases and degenerate inputs."""

    def test_empty_samples(self) -> None:
        assert extract_phases([]) == []

    def test_single_sample(self) -> None:
        assert extract_phases([(0.0, 100.0)]) == []

    def test_two_identical_samples(self) -> None:
        phases = extract_phases([(0.0, 100.0), (1.0, 100.0)])
        assert len(phases) >= 1

    def test_zero_power(self) -> None:
        samples = _constant_samples(0.0, 60.0)
        phases = extract_phases(samples)
        assert len(phases) >= 1
        assert phases[0].avg_power_w == pytest.approx(0.0, abs=0.1)


class TestPhaseAttributes:
    """Verify phase objects have correct field types."""

    def test_fields_populated(self) -> None:
        samples = _multi_phase_samples([(1000.0, 60.0), (100.0, 60.0)])
        phases = extract_phases(samples, min_duration_s=10.0)
        for phase in phases:
            assert phase.name is None
            assert 0.0 <= phase.start_pct <= 1.0
            assert 0.0 <= phase.end_pct <= 1.0
            assert phase.start_pct < phase.end_pct
            assert phase.avg_power_w >= 0.0
            assert phase.pattern in ("constant", "intermittent")
            assert phase.marks_cycle_done is False

    def test_serialization_roundtrip(self) -> None:
        samples = _multi_phase_samples([(1000.0, 60.0), (100.0, 60.0)])
        phases = extract_phases(samples, min_duration_s=10.0)
        for phase in phases:
            d = phase.to_dict()
            restored = ProfilePhase.from_dict(d)
            assert restored.start_pct == phase.start_pct
            assert restored.end_pct == phase.end_pct
            assert restored.avg_power_w == phase.avg_power_w
            assert restored.pattern == phase.pattern
            assert restored.marks_cycle_done == phase.marks_cycle_done
