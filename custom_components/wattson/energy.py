"""Shared energy and signal utilities."""

from __future__ import annotations

import numpy as np


def samples_to_arrays(
    samples: list[tuple[float, float]],
) -> tuple[np.ndarray, np.ndarray]:
    """Split (time, power) samples into separate times and powers arrays."""
    if not samples:
        return np.array([], dtype=float), np.array([], dtype=float)
    arr = np.asarray(samples, dtype=float)
    return arr[:, 0], arr[:, 1]


def trapezoidal_energy_wh(
    prev_power_w: float,
    prev_timestamp: float,
    power_w: float,
    timestamp: float,
) -> float:
    """Compute energy (Wh) between two consecutive readings via trapezoidal rule."""
    dt_h = (timestamp - prev_timestamp) / 3600.0
    avg_power = (prev_power_w + power_w) / 2.0
    return avg_power * dt_h
