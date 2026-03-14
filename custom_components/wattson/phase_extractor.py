"""Phase extraction from recorded power curves.

Analyzes a completed cycle's power samples to detect distinct phases
(e.g., heating, cooldown, anti-wrinkle) based on sustained power level shifts.
"""

from __future__ import annotations

import numpy as np

from .const import (
    MIN_SAMPLES,
    PHASE_INTERMITTENT_COV,
    PHASE_MIN_DURATION_S,
    PHASE_ROLLING_WINDOW_S,
    PHASE_SHIFT_PCT,
    PHASE_SMOOTHING_WINDOW_S,
)
from .profile_matcher import ProfilePhase


def extract_phases(
    samples: list[tuple[float, float]],
    smoothing_window_s: float = PHASE_SMOOTHING_WINDOW_S,
    rolling_window_s: float = PHASE_ROLLING_WINDOW_S,
    shift_pct: float = PHASE_SHIFT_PCT,
    min_duration_s: float = PHASE_MIN_DURATION_S,
    intermittent_cov: float = PHASE_INTERMITTENT_COV,
) -> list[ProfilePhase]:
    """Extract phases from a completed cycle's power samples.

    Args:
        samples: List of (relative_time_s, power_w) tuples.
        smoothing_window_s: Median filter window for noise removal.
        rolling_window_s: Rolling mean window for level detection.
        shift_pct: Fraction of peak power that constitutes a phase shift.
        min_duration_s: Minimum phase duration; shorter segments are merged.
        intermittent_cov: Coefficient-of-variation threshold for intermittent classification.

    Returns:
        List of detected ProfilePhase objects.
    """
    if len(samples) < MIN_SAMPLES:
        return []

    times = np.array([s[0] for s in samples])
    powers = np.array([s[1] for s in samples])

    total_duration = times[-1] - times[0]
    if total_duration <= 0:
        return []

    # Resample to a uniform 1-second grid.
    n_seconds = max(int(total_duration), 2)
    uniform_t = np.linspace(times[0], times[-1], n_seconds)
    uniform_p = np.interp(uniform_t, times, powers)

    # Median filter to remove noise spikes.
    smoothed = _median_filter(uniform_p, smoothing_window_s)

    # Rolling mean for level detection.
    rolling = _rolling_mean(smoothed, rolling_window_s)

    # Detect change points based on sustained level shifts.
    peak_power = np.max(rolling)
    if peak_power <= 0:
        return [
            ProfilePhase(
                name=None,
                start_pct=0.0,
                end_pct=1.0,
                avg_power_w=0.0,
                pattern="constant",
            )
        ]

    shift_threshold = peak_power * shift_pct
    change_points = _detect_change_points(rolling, shift_threshold, min_duration_s)

    # Build segment boundaries (indices into the uniform grid).
    boundaries = [0, *change_points, n_seconds]

    # Merge segments shorter than min_duration_s.
    boundaries = _merge_short_segments(boundaries, min_duration_s)

    # Build ProfilePhase objects from segments.
    phases: list[ProfilePhase] = []
    for i in range(len(boundaries) - 1):
        start_idx = boundaries[i]
        end_idx = boundaries[i + 1]
        segment = uniform_p[start_idx:end_idx]

        if len(segment) == 0:
            continue

        avg_power = float(np.mean(segment))
        std_power = float(np.std(segment))
        cov = std_power / avg_power if avg_power > 0 else 0.0
        pattern = "intermittent" if cov > intermittent_cov else "constant"

        phases.append(
            ProfilePhase(
                name=None,
                start_pct=start_idx / n_seconds,
                end_pct=end_idx / n_seconds,
                avg_power_w=avg_power,
                pattern=pattern,
            )
        )

    return phases or []


def _median_filter(data: np.ndarray, window_s: float) -> np.ndarray:
    """Apply a simple median filter with the given window size in samples."""
    window = max(1, int(window_s))
    if window % 2 == 0:
        window += 1
    n = len(data)
    if n <= window:
        return data.copy()

    result = np.empty(n)
    half = window // 2
    for i in range(n):
        lo = max(0, i - half)
        hi = min(n, i + half + 1)
        result[i] = np.median(data[lo:hi])
    return result


def _rolling_mean(data: np.ndarray, window_s: float) -> np.ndarray:
    """Compute a rolling mean with the given window size in samples."""
    window = max(1, int(window_s))
    n = len(data)
    if n <= window:
        return data.copy()

    cumsum = np.cumsum(data)
    result = np.empty(n)
    for i in range(n):
        lo = max(0, i - window // 2)
        hi = min(n, i + window // 2 + 1)
        result[i] = (cumsum[hi - 1] - (cumsum[lo - 1] if lo > 0 else 0)) / (hi - lo)
    return result


def _detect_change_points(
    rolling: np.ndarray,
    shift_threshold: float,
    min_duration_s: float,
) -> list[int]:
    """Find indices where the rolling mean shifts by more than the threshold."""
    change_points: list[int] = []
    n = len(rolling)
    min_samples = max(1, int(min_duration_s))

    current_level = rolling[0]
    since = 0

    for i in range(1, n):
        if abs(rolling[i] - current_level) > shift_threshold:
            if i - since >= min_samples:
                change_points.append(i)
                current_level = rolling[i]
                since = i
            elif len(change_points) == 0:
                # First segment too short; update the level reference.
                current_level = rolling[i]
                since = i
        else:
            # Level is stable; update the reference with exponential smoothing
            # to handle gradual drift within a phase.
            current_level = 0.95 * current_level + 0.05 * rolling[i]

    return change_points


def _merge_short_segments(boundaries: list[int], min_duration_s: float) -> list[int]:
    """Merge segments shorter than min_duration_s into their neighbors."""
    if len(boundaries) <= 2:  # noqa: PLR2004
        return boundaries

    min_samples = max(1, int(min_duration_s))
    merged = [boundaries[0]]

    for b in boundaries[1:]:
        if b - merged[-1] < min_samples and len(merged) > 1:
            merged[-1] = b
        else:
            merged.append(b)

    # Ensure the last boundary is always the end.
    if merged[-1] != boundaries[-1]:
        merged[-1] = boundaries[-1]

    return merged
