"""Benchmarks for Wattson's CPU/memory-critical paths.

Run with:  pytest tests/test_benchmark.py -v -s
"""

# ruff: noqa: T201

from __future__ import annotations

import time
import tracemalloc
from contextlib import contextmanager
from dataclasses import dataclass

import numpy as np
import pytest

from custom_components.wattson.const import (
    RESAMPLE_POINTS,
)
from custom_components.wattson.cycle_detector import CycleDetector, CycleDetectorConfig
from custom_components.wattson.cycle_recorder import CycleData, CycleRecorder
from custom_components.wattson.phase_extractor import extract_phases
from custom_components.wattson.profile_matcher import (
    Profile,
    ProfileMatcher,
    _correlation,
    _dtw_distance,
    _resample,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@dataclass
class BenchResult:
    label: str
    wall_ms: float
    peak_mem_kb: float
    iterations: int

    @property
    def per_call_us(self) -> float:
        return (self.wall_ms * 1000) / self.iterations

    def __str__(self) -> str:
        return (
            f"{self.label:<45} "
            f"{self.wall_ms:>8.2f} ms total | "
            f"{self.per_call_us:>8.1f} µs/call | "
            f"{self.peak_mem_kb:>8.1f} KB peak | "
            f"{self.iterations} iterations"
        )


@contextmanager
def bench(label: str, iterations: int = 1):
    tracemalloc.start()
    result = BenchResult(label=label, wall_ms=0, peak_mem_kb=0, iterations=iterations)
    t0 = time.perf_counter()
    yield result
    t1 = time.perf_counter()
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    result.wall_ms = (t1 - t0) * 1000
    result.peak_mem_kb = peak / 1024


def _make_synthetic_samples(
    duration_s: float, n_points: int, phases: int = 3, noise: float = 5.0
) -> list[tuple[float, float]]:
    """Generate a realistic multi-phase power curve."""
    t = np.linspace(0, duration_s, n_points)
    power = np.zeros(n_points)
    phase_len = n_points // phases
    for i in range(phases):
        start = i * phase_len
        end = start + phase_len if i < phases - 1 else n_points
        base = 200 + i * 300  # each phase has different power level
        power[start:end] = base
    power += np.random.default_rng(42).normal(0, noise, n_points)
    power = np.clip(power, 0, None)
    return [(float(t[i]), float(power[i])) for i in range(n_points)]


def _make_profile(samples: list[tuple[float, float]], **kwargs) -> Profile:
    duration = samples[-1][0] - samples[0][0] if samples else 0
    defaults = {
        "id": "bench-profile",
        "name": "Benchmark",
        "samples": samples,
        "avg_duration_s": duration,
        "avg_energy_wh": duration * 500 / 3600,
        "cycle_count": 5,
        "last_updated": 0.0,
        **kwargs,
    }
    return Profile(**defaults)


# ---------------------------------------------------------------------------
# Budget constants — these are the "is it too slow?" thresholds
# ---------------------------------------------------------------------------

# Per-reading hot path: must complete in < 1ms (HA runs on event loop)
BUDGET_PER_READING_US = 1000

# Time estimate (every 5s, with up to 50 profiles): must complete in < 50ms
BUDGET_ESTIMATE_MS = 50

# DTW (worst case, per profile): must complete in < 20ms
BUDGET_DTW_MS = 20

# Phase extraction (cycle end, up to 3600 grid points): must complete in < 100ms
BUDGET_PHASE_EXTRACT_MS = 100

# Full cycle-end matching (all profiles): must complete in < 200ms
BUDGET_CYCLE_END_MS = 200

# Memory: peak allocation during any single operation should stay under 5 MB
BUDGET_PEAK_MEM_KB = 5 * 1024


# ---------------------------------------------------------------------------
# 1. Detector + Recorder: the per-reading hot path
# ---------------------------------------------------------------------------


class TestPerReadingHotPath:
    """The code that runs on EVERY power reading during a cycle."""

    def test_detector_update(self) -> None:
        """CycleDetector.update() — pure state machine, should be trivial."""
        det = CycleDetector(CycleDetectorConfig())
        n = 10_000
        with bench("detector.update() x10k", n) as result:
            for i in range(n):
                det.update(100.0 + (i % 50), float(i))
        print(f"\n  {result}")
        assert result.per_call_us < BUDGET_PER_READING_US

    def test_recorder_record(self) -> None:
        """CycleRecorder.record() — downsampled append."""
        rec = CycleRecorder()
        rec.start(0.0)
        n = 10_000
        with bench("recorder.record() x10k", n) as result:
            for i in range(n):
                rec.record(100.0 + (i % 50), float(i * 2))
        print(f"\n  {result}")
        assert result.per_call_us < BUDGET_PER_READING_US

    def test_combined_per_reading(self) -> None:
        """Detector + recorder + rolling average (phase tracking math)."""
        det = CycleDetector(CycleDetectorConfig())
        rec = CycleRecorder()
        rec.start(0.0)
        rolling: list[float] = []

        n = 5_000
        with bench("detector+recorder+rolling x5k", n) as result:
            for i in range(n):
                t = float(i * 2)
                power = 100.0 + (i % 50)
                det.update(power, t)
                rec.record(power, t)
                rolling.append(power)
                if len(rolling) > 20:
                    rolling.pop(0)
                _ = sum(rolling) / len(rolling)
        print(f"\n  {result}")
        assert result.per_call_us < BUDGET_PER_READING_US


# ---------------------------------------------------------------------------
# 2. Time estimation: numpy-heavy, runs every 5s during cycle
# ---------------------------------------------------------------------------


class TestTimeEstimation:
    """estimate_remaining() called for each profile, every 5 seconds."""

    @pytest.fixture
    def matcher(self) -> ProfileMatcher:
        return ProfileMatcher()

    def test_single_estimate(self, matcher: ProfileMatcher) -> None:
        """One call to estimate_remaining (resample + correlation)."""
        profile_samples = _make_synthetic_samples(3600, 200, phases=4)
        profile = _make_profile(profile_samples)
        partial = _make_synthetic_samples(1800, 100, phases=2)

        n = 1_000
        with bench("estimate_remaining() x1k", n) as result:
            for _ in range(n):
                matcher.estimate_remaining(partial, profile)
        print(f"\n  {result}")
        assert result.per_call_us < BUDGET_ESTIMATE_MS * 1000 / 50  # budget per profile

    def test_estimate_across_50_profiles(self, matcher: ProfileMatcher) -> None:
        """Simulate the full _update_time_estimate loop with 50 profiles."""
        profiles = [
            _make_profile(
                _make_synthetic_samples(1800 + i * 60, 200, phases=3),
                id=f"profile-{i}",
            )
            for i in range(50)
        ]
        partial = _make_synthetic_samples(900, 80, phases=2)

        n = 100
        with bench("estimate loop (50 profiles) x100", n) as result:
            for _ in range(n):
                for p in profiles:
                    matcher.estimate_remaining(partial, p)
        print(f"\n  {result}")
        per_loop_ms = result.wall_ms / n
        print(f"  -> {per_loop_ms:.2f} ms per full estimation loop (50 profiles)")
        assert per_loop_ms < BUDGET_ESTIMATE_MS
        assert result.peak_mem_kb < BUDGET_PEAK_MEM_KB


# ---------------------------------------------------------------------------
# 3. DTW distance: the pure-Python nested loop
# ---------------------------------------------------------------------------


class TestDTW:
    """_dtw_distance has a nested Python loop — potential bottleneck."""

    def test_dtw_100_points(self) -> None:
        """DTW with RESAMPLE_POINTS=100 (standard match size)."""
        rng = np.random.default_rng(42)
        a = rng.normal(500, 50, RESAMPLE_POINTS)
        b = rng.normal(500, 50, RESAMPLE_POINTS)

        n = 100
        with bench(f"_dtw_distance({RESAMPLE_POINTS}pt) x100", n) as result:
            for _ in range(n):
                _dtw_distance(a, b)
        print(f"\n  {result}")
        per_call_ms = result.wall_ms / n
        print(f"  -> {per_call_ms:.2f} ms per DTW call")
        assert per_call_ms < BUDGET_DTW_MS

    def test_dtw_worst_case_in_matching(self) -> None:
        """DTW called for multiple ambiguous profiles at cycle end."""
        rng = np.random.default_rng(42)
        curves = [rng.normal(500, 50, RESAMPLE_POINTS) for _ in range(10)]
        cycle_curve = rng.normal(500, 50, RESAMPLE_POINTS)

        n = 10
        with bench("DTW x10 ambiguous profiles x10 runs", n * 10) as result:
            for _ in range(n):
                for prof_curve in curves:
                    _dtw_distance(cycle_curve, prof_curve)
        print(f"\n  {result}")
        total_ms_per_run = result.wall_ms / n
        print(f"  -> {total_ms_per_run:.2f} ms for 10 DTW calls (worst-case cycle end)")
        assert total_ms_per_run < BUDGET_CYCLE_END_MS


# ---------------------------------------------------------------------------
# 4. Phase extraction: BinSeg recursion with numpy
# ---------------------------------------------------------------------------


class TestPhaseExtraction:
    def test_short_cycle_120s(self) -> None:
        """Short cycle (2 min, ~120 grid points)."""
        samples = _make_synthetic_samples(120, 60, phases=2)
        n = 500
        with bench("extract_phases(120s) x500", n) as result:
            for _ in range(n):
                extract_phases(samples)
        print(f"\n  {result}")
        per_call_ms = result.wall_ms / n
        assert per_call_ms < BUDGET_PHASE_EXTRACT_MS

    def test_medium_cycle_1800s(self) -> None:
        """Medium cycle (30 min, ~1800 grid points)."""
        samples = _make_synthetic_samples(1800, 300, phases=4)
        n = 100
        with bench("extract_phases(1800s) x100", n) as result:
            for _ in range(n):
                extract_phases(samples)
        print(f"\n  {result}")
        per_call_ms = result.wall_ms / n
        print(f"  -> {per_call_ms:.2f} ms per extraction")
        assert per_call_ms < BUDGET_PHASE_EXTRACT_MS

    def test_long_cycle_3600s_max_grid(self) -> None:
        """Long cycle (1 hour, hits PHASE_MAX_GRID_POINTS=3600 cap)."""
        samples = _make_synthetic_samples(3600, 600, phases=5)
        n = 50
        with bench("extract_phases(3600s) x50", n) as result:
            for _ in range(n):
                extract_phases(samples)
        print(f"\n  {result}")
        per_call_ms = result.wall_ms / n
        print(f"  -> {per_call_ms:.2f} ms per extraction")
        assert per_call_ms < BUDGET_PHASE_EXTRACT_MS
        assert result.peak_mem_kb < BUDGET_PEAK_MEM_KB

    def test_extreme_cycle_14400s(self) -> None:
        """4-hour cycle (still capped at 3600 grid points)."""
        samples = _make_synthetic_samples(14400, 1000, phases=6)
        n = 20
        with bench("extract_phases(14400s) x20", n) as result:
            for _ in range(n):
                extract_phases(samples)
        print(f"\n  {result}")
        per_call_ms = result.wall_ms / n
        print(f"  -> {per_call_ms:.2f} ms per extraction (4h cycle)")
        assert per_call_ms < BUDGET_PHASE_EXTRACT_MS


# ---------------------------------------------------------------------------
# 5. Full cycle-end matching: resample + correlate + possibly DTW for all profiles
# ---------------------------------------------------------------------------


class TestCycleEndMatching:
    def test_match_against_50_profiles(self) -> None:
        """Full match() with 50 stored profiles (no DTW fallback)."""
        matcher = ProfileMatcher()
        profiles = [
            _make_profile(
                _make_synthetic_samples(1800 + i * 120, 200, phases=3),
                id=f"p-{i}",
            )
            for i in range(50)
        ]

        cycle_samples = _make_synthetic_samples(1800, 200, phases=3)
        cycle = CycleData(
            start_time=0,
            end_time=1800,
            duration_s=1800,
            energy_wh=1800 * 500 / 3600,
            samples=cycle_samples,
        )

        n = 50
        with bench("match(cycle, 50 profiles) x50", n) as result:
            for _ in range(n):
                matcher.match(cycle, profiles)
        print(f"\n  {result}")
        per_call_ms = result.wall_ms / n
        print(f"  -> {per_call_ms:.2f} ms per full match")
        assert per_call_ms < BUDGET_CYCLE_END_MS
        assert result.peak_mem_kb < BUDGET_PEAK_MEM_KB


# ---------------------------------------------------------------------------
# 6. Memory: accumulated data structures during a long cycle
# ---------------------------------------------------------------------------


class TestMemoryAccumulation:
    def test_recorder_memory_4h_cycle(self) -> None:
        """How much memory does the recorder use for a 4-hour cycle?"""
        rec = CycleRecorder()
        rec.start(0.0)

        with bench("record 4h cycle (2s intervals)", 7200) as result:
            for i in range(7200):
                rec.record(500.0 + (i % 100), float(i * 2))
        print(f"\n  {result}")
        print(
            f"  -> stored {len(rec._samples)} samples (downsampled from 7200 readings)"  # noqa: SLF001
        )
        assert result.peak_mem_kb < BUDGET_PEAK_MEM_KB

    def test_store_memory_50_profiles(self) -> None:
        """Memory footprint of 50 profiles with 100 samples each."""
        profiles = [
            _make_profile(
                _make_synthetic_samples(1800, RESAMPLE_POINTS, phases=3),
                id=f"p-{i}",
            )
            for i in range(50)
        ]
        tracemalloc.start()
        _ = [p.to_dict() for p in profiles]
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        peak_kb = peak / 1024
        print(f"\n  50 profiles in memory: {peak_kb:.1f} KB")
        assert peak_kb < BUDGET_PEAK_MEM_KB


# ---------------------------------------------------------------------------
# 7. Numpy primitives: resample + correlation baseline
# ---------------------------------------------------------------------------


class TestNumpyPrimitives:
    def test_resample(self) -> None:
        samples = _make_synthetic_samples(1800, 200, phases=3)
        n = 5_000
        with bench(f"_resample({RESAMPLE_POINTS}pt) x5k", n) as result:
            for _ in range(n):
                _resample(samples, RESAMPLE_POINTS)
        print(f"\n  {result}")

    def test_correlation(self) -> None:
        rng = np.random.default_rng(42)
        a = rng.normal(500, 50, RESAMPLE_POINTS)
        b = rng.normal(500, 50, RESAMPLE_POINTS)
        n = 10_000
        with bench(f"_correlation({RESAMPLE_POINTS}pt) x10k", n) as result:
            for _ in range(n):
                _correlation(a, b)
        print(f"\n  {result}")
