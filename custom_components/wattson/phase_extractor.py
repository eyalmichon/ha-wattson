"""Phase extraction from recorded power curves using Binary Segmentation.

Analyzes a completed cycle's power samples to detect distinct phases
(e.g., heating, cooldown, anti-wrinkle) by recursively finding splits
that minimize within-segment variance, with a BIC-inspired penalty to
prevent over-segmentation.  Each sub-segment is re-normalised locally
so that fine structure is visible regardless of the global power range.
"""

from __future__ import annotations

import numpy as np

from .const import (
    MIN_SAMPLES,
    PHASE_FLAT_TOLERANCE,
    PHASE_INTERMITTENT_COV,
    PHASE_MIN_DURATION_S,
    PHASE_MIN_SMOOTH_WIN,
    PHASE_PENALTY_FACTOR,
    PHASE_PRE_SMOOTH_WINDOW_S,
)
from .profile_matcher import ProfilePhase


def extract_phases(
    samples: list[tuple[float, float]],
    min_duration_s: float = PHASE_MIN_DURATION_S,
    penalty_factor: float = PHASE_PENALTY_FACTOR,
    intermittent_cov: float = PHASE_INTERMITTENT_COV,
) -> list[ProfilePhase]:
    """Extract phases from a completed cycle's power samples.

    Uses Binary Segmentation with an L2 (variance) cost function and a
    BIC-inspired penalty of ``penalty_factor * log(N)`` to decide whether
    a split is worthwhile.  Each recursive call re-normalises its segment
    to [0, 1] so that both large and subtle power transitions are detected.

    Args:
        samples: List of (relative_time_s, power_w) tuples.
        min_duration_s: Minimum segment size in seconds (prevents tiny splits).
        penalty_factor: Multiplier for the BIC penalty ``c * log(N)``.
        intermittent_cov: Coefficient-of-variation threshold for classifying
            a segment as intermittent vs constant.

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
    n_seconds = max(int(total_duration), MIN_SAMPLES)
    uniform_t = np.linspace(times[0], times[-1], n_seconds)
    uniform_p = np.interp(uniform_t, times, powers)

    # Pre-filter: moving average to suppress transient noise.
    smooth_win = min(PHASE_PRE_SMOOTH_WINDOW_S, n_seconds)
    if smooth_win >= PHASE_MIN_SMOOTH_WIN:
        kernel = np.ones(smooth_win) / smooth_win
        smoothed = np.convolve(uniform_p, kernel, mode="same")
    else:
        smoothed = uniform_p.copy()

    # Flat signal => single phase.
    lo, hi = float(np.min(smoothed)), float(np.max(smoothed))
    if (hi - lo) < PHASE_FLAT_TOLERANCE:
        return [
            ProfilePhase(
                name=None,
                start_pct=0.0,
                end_pct=1.0,
                avg_power_w=float(np.mean(uniform_p)),
                pattern="constant",
            )
        ]

    min_seg = max(MIN_SAMPLES, int(min_duration_s))

    # Recursively segment using BinSeg with local normalisation.
    boundaries = [0]
    _binseg_recursive(smoothed, 0, n_seconds, min_seg, penalty_factor, boundaries)
    boundaries.append(n_seconds)
    boundaries.sort()

    # Build ProfilePhase objects from segments (using the *original*
    # resampled signal, not the smoothed one, for real power stats).
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


def _binseg_recursive(
    smoothed: np.ndarray,
    start: int,
    end: int,
    min_seg: int,
    penalty_factor: float,
    boundaries: list[int],
) -> None:
    """Binary Segmentation with local min-max normalisation per segment.

    After finding the best split in a segment, recurse into each half
    with fresh normalisation so that subtle transitions within
    low-dynamic-range regions are still detected.
    """
    seg_len = end - start
    if seg_len < 2 * min_seg:
        return

    seg = smoothed[start:end]

    # Local min-max normalisation.
    lo, hi = float(np.min(seg)), float(np.max(seg))
    span = hi - lo
    if span < PHASE_FLAT_TOLERANCE:
        return
    x = (seg - lo) / span

    n = len(x)
    penalty = penalty_factor * np.log(n)

    # Precompute cumulative sums for O(1) segment cost queries.
    cs_x = np.zeros(n + 1)
    cs_x2 = np.zeros(n + 1)
    np.cumsum(x, out=cs_x[1:])
    np.cumsum(x**2, out=cs_x2[1:])

    k = np.arange(min_seg, n - min_seg + 1)
    if len(k) == 0:
        return

    # Vectorised L2 cost for all candidate split points.
    n1 = k
    s1 = cs_x[k]
    cost1 = cs_x2[k] - (s1**2) / n1

    n2 = n - k
    s2 = cs_x[n] - cs_x[k]
    cost2 = (cs_x2[n] - cs_x2[k]) - (s2**2) / n2

    total_split = cost1 + cost2
    best_idx = int(np.argmin(total_split))
    min_cost_split = total_split[best_idx]
    best_k = int(k[best_idx])

    # Cost of not splitting.
    s0 = cs_x[n]
    cost0 = cs_x2[n] - (s0**2) / n

    if (cost0 - min_cost_split) > penalty:
        abs_k = start + best_k
        boundaries.append(abs_k)
        _binseg_recursive(smoothed, start, abs_k, min_seg, penalty_factor, boundaries)
        _binseg_recursive(smoothed, abs_k, end, min_seg, penalty_factor, boundaries)
