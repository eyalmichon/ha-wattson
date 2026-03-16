"""Constants for Wattson Simulator."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from homeassistant.const import Platform

DOMAIN = "wattson_simulator"

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.SELECT,
    Platform.SWITCH,
]

TICK_INTERVAL_S = 2
DEFAULT_NOISE_PCT = 0.05


class PhaseType(StrEnum):
    """Types of simulation phases."""

    CONSTANT = "constant"
    INTERMITTENT = "intermittent"
    RAMP = "ramp"
    NOISY = "noisy"
    REPLAY = "replay"


@dataclass(frozen=True)
class Phase:
    """A single phase in a simulation program."""

    duration_s: float
    power_w: float
    noise_w: float = 0.0
    phase_type: PhaseType = PhaseType.CONSTANT
    on_duration_s: float = 0.0
    off_duration_s: float = 0.0
    ramp_to_w: float = 0.0
    spike_pct: float = 0.0
    replay_data: tuple[float, ...] = ()


@dataclass(frozen=True)
class Program:
    """A dryer simulation program."""

    name: str
    phases: tuple[Phase, ...] = field(default_factory=tuple)


def _noise(base: float) -> float:
    return base * DEFAULT_NOISE_PCT


_FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"


def load_fixture(filename: str) -> tuple[float, ...]:
    """Load a JSON power-data fixture as a tuple of floats."""
    data = json.loads((_FIXTURES_DIR / filename).read_text())
    return tuple(float(v) for v in data)


PROGRAMS: dict[str, Program] = {
    # ── Original dryer programs ──────────────────────────────────────
    "normal_dry": Program(
        name="Normal Dry",
        phases=(
            Phase(duration_s=90, power_w=2000, noise_w=_noise(2000)),
            Phase(duration_s=60, power_w=1800, noise_w=_noise(1800)),
            Phase(duration_s=30, power_w=200, noise_w=_noise(200)),
        ),
    ),
    "quick_dry": Program(
        name="Quick Dry",
        phases=(
            Phase(duration_s=50, power_w=2000, noise_w=_noise(2000)),
            Phase(duration_s=20, power_w=200, noise_w=_noise(200)),
        ),
    ),
    "delicate": Program(
        name="Delicate",
        phases=(
            Phase(duration_s=60, power_w=1200, noise_w=_noise(1200)),
            Phase(duration_s=40, power_w=1000, noise_w=_noise(1000)),
            Phase(duration_s=20, power_w=150, noise_w=_noise(150)),
        ),
    ),
    "anti_wrinkle_test": Program(
        name="Anti-Wrinkle Test",
        phases=(
            Phase(duration_s=30, power_w=2000, noise_w=_noise(2000)),
            Phase(duration_s=15, power_w=200, noise_w=_noise(200)),
            Phase(
                duration_s=180,
                power_w=80,
                noise_w=_noise(80),
                phase_type=PhaseType.INTERMITTENT,
                on_duration_s=2,
                off_duration_s=8,
            ),
        ),
    ),
    # ── Washing Machine (Cotton 60°C) — ~90 min ─────────────────────
    "washing_machine": Program(
        name="Washing Machine Cotton 60C",
        phases=(
            Phase(duration_s=600, power_w=2100, noise_w=_noise(2100)),
            Phase(
                duration_s=1800,
                power_w=500,
                noise_w=_noise(500),
                phase_type=PhaseType.INTERMITTENT,
                on_duration_s=10,
                off_duration_s=5,
            ),
            Phase(duration_s=300, power_w=400, noise_w=_noise(400)),
            Phase(duration_s=600, power_w=800, noise_w=_noise(800)),
            Phase(duration_s=300, power_w=200, noise_w=_noise(200)),
            Phase(duration_s=600, power_w=800, noise_w=_noise(800)),
            Phase(duration_s=300, power_w=10, noise_w=_noise(10)),
        ),
    ),
    # ── Dishwasher (Normal) — ~120 min ───────────────────────────────
    "dishwasher": Program(
        name="Dishwasher Normal",
        phases=(
            Phase(duration_s=300, power_w=100, noise_w=_noise(100)),
            Phase(duration_s=900, power_w=1800, noise_w=_noise(1800)),
            Phase(duration_s=1200, power_w=200, noise_w=_noise(200)),
            Phase(duration_s=600, power_w=300, noise_w=_noise(300)),
            Phase(duration_s=1800, power_w=1200, noise_w=_noise(1200)),
            Phase(duration_s=600, power_w=50, noise_w=_noise(50)),
        ),
    ),
    # ── Microwave — ~5 min (very short cycle) ────────────────────────
    "microwave": Program(
        name="Microwave",
        phases=(
            Phase(duration_s=240, power_w=1200, noise_w=_noise(1200)),
            Phase(duration_s=60, power_w=30, noise_w=_noise(30)),
        ),
    ),
    # ── Electric Oven (Bake 180°C) — ~60 min (thermostat cycling) ───
    "oven": Program(
        name="Electric Oven Bake 180C",
        phases=(
            Phase(duration_s=480, power_w=2500, noise_w=_noise(2500)),
            Phase(
                duration_s=2700,
                power_w=800,
                noise_w=_noise(800),
                phase_type=PhaseType.INTERMITTENT,
                on_duration_s=30,
                off_duration_s=60,
            ),
            Phase(duration_s=420, power_w=50, noise_w=_noise(50)),
        ),
    ),
    # ── Air Conditioner — ~180 min (long, intermittent compressor) ───
    "air_conditioner": Program(
        name="Air Conditioner",
        phases=(
            Phase(duration_s=120, power_w=3000, noise_w=_noise(3000)),
            Phase(
                duration_s=9000,
                power_w=1200,
                noise_w=_noise(1200),
                phase_type=PhaseType.INTERMITTENT,
                on_duration_s=600,
                off_duration_s=300,
            ),
            Phase(duration_s=1200, power_w=200, noise_w=_noise(200)),
            Phase(duration_s=300, power_w=5, noise_w=2),
        ),
    ),
    # ── Coffee Machine (Espresso) — ~2 min (very short, high power) ─
    "coffee_machine": Program(
        name="Coffee Machine Espresso",
        phases=(
            Phase(duration_s=90, power_w=1400, noise_w=_noise(1400)),
            Phase(duration_s=30, power_w=70, noise_w=_noise(70)),
        ),
    ),
    # ── Electric Kettle — ~3 min (single dominant phase) ─────────────
    "electric_kettle": Program(
        name="Electric Kettle",
        phases=(
            Phase(duration_s=170, power_w=2800, noise_w=_noise(2800)),
            Phase(duration_s=10, power_w=100, noise_w=_noise(100)),
        ),
    ),
    # ── Iron — ~30 min (thermostat cycling) ──────────────────────────
    "iron": Program(
        name="Iron",
        phases=(
            Phase(duration_s=180, power_w=2400, noise_w=_noise(2400)),
            Phase(
                duration_s=1500,
                power_w=1000,
                noise_w=_noise(1000),
                phase_type=PhaseType.INTERMITTENT,
                on_duration_s=15,
                off_duration_s=30,
            ),
            Phase(duration_s=120, power_w=5, noise_w=2),
        ),
    ),
    # ── Robot Vacuum — ~90 min (low power, mode changes) ─────────────
    "robot_vacuum": Program(
        name="Robot Vacuum",
        phases=(
            Phase(duration_s=120, power_w=60, noise_w=_noise(60)),
            Phase(duration_s=3000, power_w=40, noise_w=_noise(40)),
            Phase(duration_s=1200, power_w=70, noise_w=_noise(70)),
            Phase(duration_s=300, power_w=30, noise_w=_noise(30)),
            Phase(duration_s=900, power_w=25, noise_w=_noise(25)),
        ),
    ),
    # ── Heat Pump Dryer — ~150 min (long, lower power) ───────────────
    "heat_pump_dryer": Program(
        name="Heat Pump Dryer",
        phases=(
            Phase(duration_s=180, power_w=900, noise_w=_noise(900)),
            Phase(duration_s=6000, power_w=600, noise_w=_noise(600)),
            Phase(duration_s=1800, power_w=200, noise_w=_noise(200)),
            Phase(
                duration_s=1200,
                power_w=50,
                noise_w=_noise(50),
                phase_type=PhaseType.INTERMITTENT,
                on_duration_s=5,
                off_duration_s=30,
            ),
        ),
    ),
    # ── Toaster — ~3 min (extremely short, single phase) ─────────────
    "toaster": Program(
        name="Toaster",
        phases=(
            Phase(duration_s=150, power_w=850, noise_w=_noise(850)),
            Phase(duration_s=10, power_w=0, noise_w=0),
        ),
    ),
    # ── Electric Water Heater — ~45 min (simple on/off thermostat) ───
    "water_heater": Program(
        name="Electric Water Heater",
        phases=(
            Phase(duration_s=2400, power_w=3000, noise_w=_noise(3000)),
            Phase(duration_s=300, power_w=5, noise_w=2),
        ),
    ),
    # ── Induction Cooktop — ~20 min (rapid power changes) ────────────
    "induction_cooktop": Program(
        name="Induction Cooktop",
        phases=(
            Phase(duration_s=300, power_w=2200, noise_w=_noise(2200)),
            Phase(duration_s=480, power_w=1200, noise_w=_noise(1200)),
            Phase(duration_s=300, power_w=500, noise_w=_noise(500)),
            Phase(duration_s=120, power_w=200, noise_w=_noise(200)),
        ),
    ),
    # ── 3D Printer — ~240 min (very long, distinct heat phases) ──────
    "3d_printer": Program(
        name="3D Printer",
        phases=(
            Phase(duration_s=300, power_w=350, noise_w=_noise(350)),
            Phase(duration_s=13200, power_w=200, noise_w=_noise(200)),
            Phase(duration_s=900, power_w=30, noise_w=_noise(30)),
        ),
    ),
    # ── Realistic Washing Machine — ~54 min (from real-world data) ─
    # Observed: heat 2200W 13min -> wash/agitate 150W intermittent 32min
    # -> rinse/spin 350W 5min -> drain 50W 4min
    "realistic_washer": Program(
        name="Realistic Washing Machine",
        phases=(
            Phase(duration_s=780, power_w=2200, noise_w=_noise(2200)),
            Phase(
                duration_s=1920,
                power_w=150,
                noise_w=80,
                phase_type=PhaseType.INTERMITTENT,
                on_duration_s=8,
                off_duration_s=5,
            ),
            Phase(duration_s=300, power_w=350, noise_w=_noise(350)),
            Phase(duration_s=240, power_w=50, noise_w=_noise(50)),
        ),
    ),
    # ── Realistic Dryer — ~105 min (from real-world data) ──────────
    # Observed: high heat 2500W 17min -> medium heat 850W/50W oscillating
    # 50min -> cooldown 60W intermittent 30min -> anti-wrinkle 40W 8min
    "realistic_dryer": Program(
        name="Realistic Dryer",
        phases=(
            Phase(duration_s=1020, power_w=2500, noise_w=_noise(2500)),
            Phase(
                duration_s=3000,
                power_w=850,
                noise_w=100,
                phase_type=PhaseType.INTERMITTENT,
                on_duration_s=20,
                off_duration_s=15,
            ),
            Phase(
                duration_s=1800,
                power_w=60,
                noise_w=30,
                phase_type=PhaseType.INTERMITTENT,
                on_duration_s=5,
                off_duration_s=10,
            ),
            Phase(duration_s=480, power_w=40, noise_w=20),
        ),
    ),
    # ── Stress: Gradual Washer — uses RAMP phases ────────────────────
    # Gradual heat ramp 0→2000W (5min), drop to 400W wash (2min),
    # constant wash (20min), ramp down to 50W drain (3min).
    "stress_gradual_washer": Program(
        name="Stress Gradual Washer",
        phases=(
            Phase(
                duration_s=300,
                power_w=0,
                noise_w=_noise(1000),
                phase_type=PhaseType.RAMP,
                ramp_to_w=2000,
            ),
            Phase(
                duration_s=120,
                power_w=2000,
                noise_w=_noise(1200),
                phase_type=PhaseType.RAMP,
                ramp_to_w=400,
            ),
            Phase(duration_s=1200, power_w=400, noise_w=_noise(400)),
            Phase(
                duration_s=180,
                power_w=400,
                noise_w=_noise(200),
                phase_type=PhaseType.RAMP,
                ramp_to_w=50,
            ),
        ),
    ),
    # ── Stress: Noisy Dryer — high noise, spikes ────────────────────
    "stress_noisy_dryer": Program(
        name="Stress Noisy Dryer",
        phases=(
            Phase(
                duration_s=600,
                power_w=2200,
                noise_w=880,
                phase_type=PhaseType.NOISY,
                spike_pct=0.05,
            ),
            Phase(
                duration_s=1800,
                power_w=700,
                noise_w=280,
                phase_type=PhaseType.NOISY,
                spike_pct=0.08,
            ),
            Phase(
                duration_s=600,
                power_w=100,
                noise_w=40,
                phase_type=PhaseType.NOISY,
                spike_pct=0.02,
            ),
        ),
    ),
    # ── Stress: Similar Phases — subtle 25-33% differences ──────────
    "stress_similar_phases": Program(
        name="Stress Similar Phases",
        phases=(
            Phase(duration_s=600, power_w=800, noise_w=_noise(800)),
            Phase(duration_s=600, power_w=600, noise_w=_noise(600)),
            Phase(duration_s=600, power_w=450, noise_w=_noise(450)),
        ),
    ),
    # ── Stress: Transient Spikes — should NOT create extra phases ───
    "stress_transient_spikes": Program(
        name="Stress Transient Spikes",
        phases=(
            Phase(
                duration_s=900,
                power_w=500,
                noise_w=200,
                phase_type=PhaseType.NOISY,
                spike_pct=0.10,
            ),
            Phase(duration_s=900, power_w=1500, noise_w=_noise(1500)),
        ),
    ),
    # ── Replay: Real Washer — captured fixture data ─────────────────
    "replay_real_washer": Program(
        name="Replay Real Washer",
        phases=(
            Phase(
                duration_s=3240,
                power_w=0,
                phase_type=PhaseType.REPLAY,
                replay_data=load_fixture("washer_power.json"),
            ),
        ),
    ),
    # ── Replay: Real Dryer — captured fixture data ──────────────────
    "replay_real_dryer": Program(
        name="Replay Real Dryer",
        phases=(
            Phase(
                duration_s=6300,
                power_w=0,
                phase_type=PhaseType.REPLAY,
                replay_data=load_fixture("dryer_power.json"),
            ),
        ),
    ),
}
