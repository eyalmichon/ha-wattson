"""Profile matching and time estimation for Wattson.

Uses numpy for correlation-based matching with DTW as a tiebreaker.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from time import time
from typing import TYPE_CHECKING

import numpy as np

from .const import (
    ESTIMATE_MIN_CORRELATION,
    ESTIMATE_MIN_PARTIAL_FRAC,
    ESTIMATE_SHAPE_WEIGHT,
    MATCH_CORRELATION_AMBIGUOUS,
    MATCH_CORRELATION_THRESHOLD,
    MATCH_DTW_THRESHOLD,
    MIN_SAMPLES,
    PROFILE_UPDATE_ALPHA,
    RESAMPLE_POINTS,
    STD_EPSILON,
)

if TYPE_CHECKING:
    from .cycle_recorder import CycleData


@dataclass
class ProfilePhase:
    """A detected phase within a program's power profile."""

    name: str | None
    start_pct: float
    end_pct: float
    avg_power_w: float
    pattern: str = "constant"
    marks_cycle_done: bool = False

    def to_dict(self) -> dict[str, object]:
        """Serialize to a plain dict for storage."""
        return {
            "name": self.name,
            "start_pct": self.start_pct,
            "end_pct": self.end_pct,
            "avg_power_w": self.avg_power_w,
            "pattern": self.pattern,
            "marks_cycle_done": self.marks_cycle_done,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> ProfilePhase:
        """Deserialize from a plain dict."""
        return cls(
            name=data.get("name"),
            start_pct=float(data.get("start_pct", 0.0)),
            end_pct=float(data.get("end_pct", 1.0)),
            avg_power_w=float(data.get("avg_power_w", 0.0)),
            pattern=str(data.get("pattern", "constant")),
            marks_cycle_done=bool(data.get("marks_cycle_done", False)),
        )


@dataclass
class Profile:
    """A learned power profile for an appliance program."""

    id: str
    name: str | None
    samples: list[tuple[float, float]]
    avg_duration_s: float
    avg_energy_wh: float
    cycle_count: int
    last_updated: float
    phases: list[ProfilePhase] | None = None

    def to_dict(self) -> dict[str, object]:
        """Serialize to a plain dict for storage / API responses."""
        d: dict[str, object] = {
            "id": self.id,
            "name": self.name,
            "samples": self.samples,
            "avg_duration_s": self.avg_duration_s,
            "avg_energy_wh": self.avg_energy_wh,
            "cycle_count": self.cycle_count,
            "last_updated": self.last_updated,
        }
        if self.phases is not None:
            d["phases"] = [p.to_dict() for p in self.phases]
        return d

    @classmethod
    def from_dict(cls, data: dict) -> Profile:
        """Deserialize from a plain dict, handling nested phases."""
        raw_phases = data.get("phases")
        phases = (
            [ProfilePhase.from_dict(p) for p in raw_phases]
            if raw_phases is not None
            else None
        )
        return cls(
            id=data["id"],
            name=data.get("name"),
            samples=data.get("samples", []),
            avg_duration_s=float(data.get("avg_duration_s", 0.0)),
            avg_energy_wh=float(data.get("avg_energy_wh", 0.0)),
            cycle_count=int(data.get("cycle_count", 0)),
            last_updated=float(data.get("last_updated", 0.0)),
            phases=phases,
        )


@dataclass
class MatchResult:
    """Result of matching a cycle against profiles."""

    profile_id: str
    profile_name: str | None
    correlation: float
    dtw_distance: float | None


def _resample(samples: list[tuple[float, float]], n_points: int) -> np.ndarray:
    """Resample a power curve to a fixed number of evenly spaced points."""
    if len(samples) < MIN_SAMPLES:
        return np.zeros(n_points)

    times = np.array([s[0] for s in samples])
    powers = np.array([s[1] for s in samples])

    target_times = np.linspace(times[0], times[-1], n_points)
    return np.interp(target_times, times, powers)


def _correlation(a: np.ndarray, b: np.ndarray) -> float:
    """Pearson correlation between two equal-length arrays."""
    if np.std(a) < STD_EPSILON or np.std(b) < STD_EPSILON:
        # Flat signals — correlation is undefined; treat as no match.
        return 0.0
    return float(np.corrcoef(a, b)[0, 1])


def _dtw_distance(a: np.ndarray, b: np.ndarray) -> float:
    """Dynamic Time Warping distance (pure numpy, no extra deps)."""
    n, m = len(a), len(b)
    cost = np.full((n + 1, m + 1), np.inf)
    cost[0, 0] = 0.0

    for i in range(1, n + 1):
        for j in range(1, m + 1):
            d = abs(float(a[i - 1]) - float(b[j - 1]))
            cost[i, j] = d + min(cost[i - 1, j], cost[i, j - 1], cost[i - 1, j - 1])

    return float(cost[n, m]) / max(n, m)


def _merge_phases(
    old: list[ProfilePhase] | None,
    new: list[ProfilePhase] | None,
) -> list[ProfilePhase] | None:
    """Merge newly extracted phases with existing ones, preserving user config.

    User-assigned names and marks_cycle_done flags are carried forward
    positionally (phase index).  Power levels and boundaries come from
    the fresh extraction.
    """
    if new is None:
        return old
    if old is None or len(old) == 0:
        return new

    merged: list[ProfilePhase] = []
    for i, phase in enumerate(new):
        if i < len(old):
            merged.append(
                ProfilePhase(
                    name=old[i].name,
                    start_pct=phase.start_pct,
                    end_pct=phase.end_pct,
                    avg_power_w=phase.avg_power_w,
                    pattern=phase.pattern,
                    marks_cycle_done=old[i].marks_cycle_done,
                )
            )
        else:
            merged.append(phase)
    return merged


class ProfileMatcher:
    """Matches completed cycles against learned profiles and estimates time remaining."""

    def __init__(
        self,
        correlation_threshold: float = MATCH_CORRELATION_THRESHOLD,
        correlation_ambiguous: float = MATCH_CORRELATION_AMBIGUOUS,
        dtw_threshold: float = MATCH_DTW_THRESHOLD,
        resample_points: int = RESAMPLE_POINTS,
        update_alpha: float = PROFILE_UPDATE_ALPHA,
    ) -> None:
        self._corr_threshold = correlation_threshold
        self._corr_ambiguous = correlation_ambiguous
        self._dtw_threshold = dtw_threshold
        self._n_points = resample_points
        self._alpha = update_alpha

    def match(self, cycle: CycleData, profiles: list[Profile]) -> MatchResult | None:
        """Find the best matching profile for a completed cycle."""
        if not profiles or len(cycle.samples) < MIN_SAMPLES:
            return None

        cycle_curve = _resample(cycle.samples, self._n_points)

        best: MatchResult | None = None
        best_score = -1.0

        for profile in profiles:
            if len(profile.samples) < MIN_SAMPLES:
                continue

            prof_curve = _resample(profile.samples, self._n_points)
            corr = _correlation(cycle_curve, prof_curve)

            if corr >= self._corr_threshold:
                if corr > best_score:
                    best_score = corr
                    best = MatchResult(
                        profile_id=profile.id,
                        profile_name=profile.name,
                        correlation=corr,
                        dtw_distance=None,
                    )
            elif corr >= self._corr_ambiguous:
                dtw = _dtw_distance(cycle_curve, prof_curve)
                if dtw <= self._dtw_threshold and corr > best_score:
                    best_score = corr
                    best = MatchResult(
                        profile_id=profile.id,
                        profile_name=profile.name,
                        correlation=corr,
                        dtw_distance=dtw,
                    )

        return best

    def estimate_remaining(
        self,
        partial: list[tuple[float, float]],
        profile: Profile,
    ) -> tuple[float | None, float, float]:
        """Estimate time remaining given a partial power curve and a matched profile.

        Uses elapsed time for the estimate and correlation for scoring how
        well the partial curve matches this profile (used by the caller to
        pick the best-matching profile).

        Args:
            partial: The power samples recorded so far (relative_time, power_w).
            profile: The profile to estimate against.

        Returns:
            (remaining_seconds | None, match_score, progress_fraction).
            Returns (None, 0.0, 0.0) when estimation is not possible.
        """
        if not partial or len(profile.samples) < MIN_SAMPLES:
            return None, 0.0, 0.0

        profile_duration = profile.avg_duration_s
        if profile_duration <= 0:
            return None, 0.0, 0.0

        partial_duration = partial[-1][0] - partial[0][0]

        # Don't estimate until we have a meaningful fraction of the cycle.
        frac = partial_duration / profile_duration if profile_duration else 0.0
        if frac < ESTIMATE_MIN_PARTIAL_FRAC:
            return None, 0.0, 0.0

        # Time-based progress and remaining (simple, stable, monotonic).
        progress = min(frac, 1.0)
        remaining = max(0.0, profile_duration - partial_duration)

        # Compute a match score so the caller can choose the best profile.
        # Resample the partial to align with the expected profile segment.
        profile_curve = _resample(profile.samples, self._n_points)
        window_size = max(MIN_SAMPLES, round(self._n_points * progress))
        window_size = min(window_size, self._n_points)
        partial_curve = _resample(partial, window_size)

        offset = max(0, round(progress * self._n_points) - window_size)
        offset = min(offset, self._n_points - window_size)
        window = profile_curve[offset : offset + window_size]

        corr = _correlation(partial_curve, window)

        ref_power = max(float(np.mean(np.abs(profile_curve))), 1.0)
        level_sim = max(
            0.0,
            1.0
            - abs(float(np.mean(partial_curve)) - float(np.mean(window))) / ref_power,
        )

        if corr > STD_EPSILON:
            score = (
                ESTIMATE_SHAPE_WEIGHT * corr + (1 - ESTIMATE_SHAPE_WEIGHT) * level_sim
            )
        else:
            score = level_sim

        if score < ESTIMATE_MIN_CORRELATION:
            return None, score, 0.0

        return remaining, score, progress

    def create_profile(
        self,
        cycle: CycleData,
        name: str | None,
        phases: list[ProfilePhase] | None = None,
    ) -> Profile:
        """Create a new profile from a completed cycle."""
        return Profile(
            id=str(uuid.uuid4()),
            name=name,
            samples=list(cycle.samples),
            avg_duration_s=cycle.duration_s,
            avg_energy_wh=cycle.energy_wh,
            cycle_count=1,
            last_updated=time(),
            phases=phases,
        )

    def update_profile(
        self,
        profile: Profile,
        cycle: CycleData,
        new_phases: list[ProfilePhase] | None = None,
    ) -> Profile:
        """Blend a new cycle into an existing profile using exponential decay."""
        alpha = self._alpha

        new_duration = (1 - alpha) * profile.avg_duration_s + alpha * cycle.duration_s
        new_energy = (1 - alpha) * profile.avg_energy_wh + alpha * cycle.energy_wh

        # Blend the power curve samples.
        old_curve = _resample(profile.samples, self._n_points)
        new_curve = _resample(cycle.samples, self._n_points)
        blended = (1 - alpha) * old_curve + alpha * new_curve

        # Convert back to sample list using the profile's time scale.
        t_max = new_duration
        blended_samples = [
            (float(t_max * i / (self._n_points - 1)), float(blended[i]))
            for i in range(self._n_points)
        ]

        # Merge phases: keep user-assigned names and marks_cycle_done from the
        # old phases, but update avg_power_w and boundaries from the new extraction.
        phases = _merge_phases(profile.phases, new_phases)

        return Profile(
            id=profile.id,
            name=profile.name,
            samples=blended_samples,
            avg_duration_s=new_duration,
            avg_energy_wh=new_energy,
            cycle_count=profile.cycle_count + 1,
            last_updated=time(),
            phases=phases,
        )
