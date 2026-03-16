"""Unit tests for the BinSeg phase extraction algorithm."""

from __future__ import annotations

import random

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


# ---------------------------------------------------------------------------
# Realistic signal helpers
# ---------------------------------------------------------------------------


def _ramp_samples(
    start_w: float,
    end_w: float,
    duration_s: float,
    noise_w: float = 0.0,
    start_s: float = 0.0,
    step_s: float = 1.0,
) -> list[tuple[float, float]]:
    """Generate a linear ramp from start_w to end_w with optional noise."""
    samples: list[tuple[float, float]] = []
    t = start_s
    n_steps = int(duration_s / step_s)
    for i in range(n_steps + 1):
        progress = i / max(n_steps, 1)
        power = start_w + (end_w - start_w) * progress
        if noise_w > 0:
            power += random.gauss(0, noise_w)
        samples.append((t, max(0.0, power)))
        t += step_s
    return samples


def _noisy_samples(
    power_w: float,
    duration_s: float,
    noise_w: float,
    spike_pct: float = 0.0,
    start_s: float = 0.0,
    step_s: float = 1.0,
) -> list[tuple[float, float]]:
    """Generate constant power with high uniform noise and random spikes."""
    samples: list[tuple[float, float]] = []
    t = start_s
    while t <= start_s + duration_s:
        value = power_w + random.uniform(-noise_w, noise_w)
        if spike_pct > 0 and random.random() < spike_pct:
            value += power_w * random.uniform(0.5, 1.5)
        samples.append((t, max(0.0, value)))
        t += step_s
    return samples


def _gradual_transition_samples(
    phases: list[tuple[float, float, float]],
    transition_s: float = 10.0,
    step_s: float = 1.0,
) -> list[tuple[float, float]]:
    """Generate multi-phase signal with gradual transitions between levels.

    phases: list of (power_w, duration_s, noise_w) tuples.
    transition_s: ramp duration between adjacent phases.
    """
    samples: list[tuple[float, float]] = []
    t = 0.0
    for idx, (power_w, duration_s, noise_w) in enumerate(phases):
        end_t = t + duration_s
        while t < end_t:
            noise = random.gauss(0, noise_w) if noise_w > 0 else 0.0
            samples.append((t, max(0.0, power_w + noise)))
            t += step_s
        if idx < len(phases) - 1:
            next_power = phases[idx + 1][0]
            ramp = _ramp_samples(
                power_w, next_power, transition_s, noise_w, start_s=t, step_s=step_s
            )
            samples.extend(ramp)
            t += transition_s + step_s
    return samples


class TestRealisticSignals:
    """Test phase extraction on realistic, noisy, and ramping signals."""

    def test_ramp_up_then_constant(self) -> None:
        """A gradual ramp from 0 to 2000W then steady 2000W should detect at least 2 phases."""
        random.seed(100)
        ramp = _ramp_samples(0.0, 2000.0, 120.0, noise_w=30.0)
        steady = _constant_samples(2000.0, 300.0, start_s=121.0)
        samples = ramp + steady
        phases = extract_phases(samples, min_duration_s=20.0)
        assert len(phases) >= 2

    def test_gradual_three_phase_transition(self) -> None:
        """Three phases connected by gradual 10s ramps should still detect 3 phases."""
        random.seed(101)
        samples = _gradual_transition_samples(
            [
                (2000.0, 200.0, 40.0),
                (500.0, 200.0, 20.0),
                (100.0, 200.0, 10.0),
            ],
            transition_s=15.0,
        )
        phases = extract_phases(samples, min_duration_s=20.0)
        assert len(phases) >= 3

    def test_high_noise_two_phases(self) -> None:
        """Two phases with 40% noise should still be distinguishable."""
        random.seed(102)
        noisy_high = _noisy_samples(2000.0, 300.0, noise_w=800.0, start_s=0.0)
        noisy_low = _noisy_samples(200.0, 300.0, noise_w=80.0, start_s=301.0)
        samples = noisy_high + noisy_low
        phases = extract_phases(samples, min_duration_s=30.0)
        assert len(phases) >= 2

    def test_similar_adjacent_phases(self) -> None:
        """Phases at 800W, 600W, 450W (25-33% difference) should be detected."""
        random.seed(103)
        samples = _multi_phase_samples(
            [
                (800.0, 300.0),
                (600.0, 300.0),
                (450.0, 300.0),
            ]
        )
        phases = extract_phases(samples, min_duration_s=30.0)
        assert len(phases) >= 3

    def test_transient_spikes_no_oversegmentation(self) -> None:
        """Spikes should not cause extra phase splits in a single constant phase."""
        random.seed(104)
        samples = _noisy_samples(500.0, 600.0, noise_w=100.0, spike_pct=0.10)
        phases = extract_phases(samples, min_duration_s=30.0)
        assert len(phases) <= 2, (
            f"Expected at most 2 phases for a single noisy phase, got {len(phases)}"
        )

    def test_ramp_down_detected(self) -> None:
        """A ramp from 2000W down to 200W followed by a constant phase."""
        random.seed(105)
        ramp_down = _ramp_samples(2000.0, 200.0, 180.0, noise_w=30.0)
        steady_low = _constant_samples(200.0, 300.0, start_s=181.0)
        samples = ramp_down + steady_low
        phases = extract_phases(samples, min_duration_s=20.0)
        assert len(phases) >= 2
