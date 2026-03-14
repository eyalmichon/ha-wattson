"""Shared energy computation utilities."""

from __future__ import annotations


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
