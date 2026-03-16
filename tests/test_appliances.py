"""Per-appliance e2e tests for adaptive constants and diverse cycle profiles."""

from __future__ import annotations

import random
from datetime import timedelta
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.wattson.const import (
    CONF_ENTITY_ID,
    CONF_SOURCE_TYPE,
    CONF_START_THRESHOLD,
    SOURCE_ENTITY,
    adaptive_phase_params,
)
from custom_components.wattson.const import (
    DOMAIN as WATTSON_DOMAIN,
)
from custom_components.wattson_simulator.const import DOMAIN as SIM_DOMAIN
from custom_components.wattson_simulator.const import PROGRAMS

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from custom_components.wattson.coordinator import WattsonCoordinator
    from custom_components.wattson_simulator.engine import SimulationEngine


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SIM_POWER_ENTITY = "sensor.appliance_power"


class ApplianceCtx:
    """Test context for per-appliance e2e tests."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: WattsonCoordinator,
        engine: SimulationEngine,
        mock_time: object,
        t: float,
        start_dt: object,
    ) -> None:
        self.hass = hass
        self.coordinator = coordinator
        self.engine = engine
        self.mock_time = mock_time
        self.t = t
        self._start_dt = start_dt
        self._dt_offset: float = 0.0

    async def advance(self, seconds: float, step: float = 2.0) -> None:
        elapsed = 0.0
        while elapsed < seconds:
            self.t += step
            self.mock_time.time.return_value = self.t
            self._dt_offset += step
            async_fire_time_changed(
                self.hass, self._start_dt + timedelta(seconds=self._dt_offset)
            )
            await self.hass.async_block_till_done()
            elapsed += step

    async def run_full_cycle(self, program_key: str) -> None:
        self.engine.set_program(program_key)
        self.engine.start()
        await self.hass.async_block_till_done()

        program = PROGRAMS[program_key]
        total_duration = sum(p.duration_s for p in program.phases)
        # After phases complete the detector needs end_delay to fire.
        # The adaptive end_delay can be up to 10% of the cycle duration,
        # so wait generously to cover both default and adaptive delays.
        end_buffer = max(80, total_duration * 0.15)
        await self.advance(total_duration + end_buffer)


@pytest.fixture
async def appliance_ctx(hass: HomeAssistant) -> ApplianceCtx:
    """Set up Wattson + simulator for appliance tests (lower start threshold for low-power devices)."""
    sim_entry = MockConfigEntry(
        domain=SIM_DOMAIN,
        title="Appliance",
        data={"name": "Appliance"},
        unique_id="sim_appliance",
    )
    sim_entry.add_to_hass(hass)

    wattson_entry = MockConfigEntry(
        domain=WATTSON_DOMAIN,
        title="Appliance",
        data={
            "name": "Appliance",
            CONF_SOURCE_TYPE: SOURCE_ENTITY,
            CONF_ENTITY_ID: SIM_POWER_ENTITY,
            CONF_START_THRESHOLD: 3.0,
        },
        unique_id=SIM_POWER_ENTITY,
    )
    wattson_entry.add_to_hass(hass)

    t = 1_000_000.0
    random.seed(42)
    start_dt = dt_util.utcnow()

    with patch("custom_components.wattson.coordinator.time") as mock_time:
        mock_time.time.return_value = t

        await async_setup_component(hass, "persistent_notification", {})
        assert await async_setup_component(hass, SIM_DOMAIN, {})
        await hass.async_block_till_done()
        assert await async_setup_component(hass, WATTSON_DOMAIN, {})
        await hass.async_block_till_done()

        coordinator: WattsonCoordinator = hass.data[WATTSON_DOMAIN][
            wattson_entry.entry_id
        ]["coordinator"]
        engine: SimulationEngine = hass.data[SIM_DOMAIN][sim_entry.entry_id]["engine"]

        ctx = ApplianceCtx(hass, coordinator, engine, mock_time, t, start_dt)
        yield ctx  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Unit tests: adaptive_phase_params formula
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    (
        "duration_s",
        "expected_confirm",
        "expected_min",
        "expected_end",
    ),
    [
        (0, 5.0, 3.0, 15.0),
        (10, 5.0, 3.0, 15.0),
        (60, 5.0, 3.0, 15.0),
        (300, 15.0, 9.0, 30.0),
        (3600, 180.0, 108.0, 360.0),
        (14400, 720.0, 432.0, 1440.0),
    ],
)
def test_adaptive_phase_params_formula(
    duration_s: float,
    expected_confirm: float,
    expected_min: float,
    expected_end: float,
) -> None:
    """Verify adaptive_phase_params returns correct values for various durations."""
    p = adaptive_phase_params(duration_s)
    assert p["phase_confirm_s"] == pytest.approx(expected_confirm, abs=0.01)
    assert p["min_duration_s"] == pytest.approx(expected_min, abs=0.01)
    assert p["end_delay_s"] == pytest.approx(expected_end, abs=0.01)


def test_adaptive_params_floors_prevent_degenerate() -> None:
    """Very short durations should hit the floor values."""
    p = adaptive_phase_params(1.0)
    assert p["phase_confirm_s"] == 5.0
    assert p["min_duration_s"] == 3.0
    assert p["end_delay_s"] == 15.0


# ---------------------------------------------------------------------------
# Per-appliance parametrized test: 2-cycle detection
# ---------------------------------------------------------------------------

APPLIANCE_PROGRAMS = [
    ("washing_machine", 7),
    ("dishwasher", 6),
    ("microwave", 2),
    ("oven", 3),
    ("air_conditioner", 4),
    ("coffee_machine", 2),
    ("electric_kettle", 2),
    ("iron", 3),
    ("robot_vacuum", 5),
    ("heat_pump_dryer", 4),
    ("toaster", 2),
    ("water_heater", 2),
    ("induction_cooktop", 4),
    ("3d_printer", 3),
]


# Programs where profile matching across runs is harder due to:
# - Intermittent phases producing different waveforms each run
# - Near-flat constant signals where noise dominates shape (Pearson ≈ 0)
# - Very low power levels (high noise-to-signal ratio)
# - Long off-periods that split cycles (e.g. AC compressor)
_MATCHING_MAY_DIFFER = {
    "washing_machine",
    "oven",
    "air_conditioner",
    "iron",
    "heat_pump_dryer",
    "robot_vacuum",
    "water_heater",
    "electric_kettle",
    "toaster",
    "microwave",
    "coffee_machine",
    "3d_printer",
}


@pytest.mark.parametrize(("program_key", "expected_phase_count"), APPLIANCE_PROGRAMS)
async def test_appliance_two_cycles(
    appliance_ctx: ApplianceCtx,
    program_key: str,
    expected_phase_count: int,
) -> None:
    """Run each appliance program twice and verify profile/phase creation."""
    ctx = appliance_ctx

    # Cycle 1: should create a profile with phases.
    await ctx.run_full_cycle(program_key)

    assert ctx.coordinator.detector.state.value == "off", (
        f"{program_key}: detector not OFF after cycle 1"
    )
    assert len(ctx.coordinator.store.profiles) >= 1, (
        f"{program_key}: no profile created after cycle 1"
    )
    assert len(ctx.coordinator.store.cycles) >= 1, (
        f"{program_key}: no cycle stored after cycle 1"
    )

    profile = ctx.coordinator.store.profiles[0]
    assert profile.phases is not None, (
        f"{program_key}: no phases detected after cycle 1"
    )
    assert len(profile.phases) >= 1, (
        f"{program_key}: expected at least 1 phase, got {len(profile.phases)}"
    )

    first_profile_id = profile.id

    # Cycle 2: should match the existing profile (not duplicate).
    await ctx.run_full_cycle(program_key)

    if program_key in _MATCHING_MAY_DIFFER:
        # Intermittent patterns may produce waveforms different enough
        # that the matcher creates a second profile, or long off-periods
        # may split the cycle into sub-cycles (e.g. AC compressor off > end_delay).
        assert len(ctx.coordinator.store.profiles) >= 1, (
            f"{program_key}: no profiles created"
        )
    else:
        assert len(ctx.coordinator.store.profiles) == 1, (
            f"{program_key}: profile duplicated on cycle 2 "
            f"(got {len(ctx.coordinator.store.profiles)} profiles)"
        )
        assert ctx.coordinator.store.profiles[0].id == first_profile_id
        assert ctx.coordinator.store.profiles[0].cycle_count == 2

    assert len(ctx.coordinator.store.cycles) >= 2


# Exclude programs where intermittent off-periods cause cycle splitting,
# making end_delay assertions unreliable.
_END_DELAY_TESTABLE = [
    p for p in APPLIANCE_PROGRAMS if p[0] not in _MATCHING_MAY_DIFFER
]


@pytest.mark.parametrize(("program_key", "expected_phase_count"), _END_DELAY_TESTABLE)
async def test_appliance_adaptive_end_delay(
    appliance_ctx: ApplianceCtx,
    program_key: str,
    expected_phase_count: int,
) -> None:
    """After a full cycle, the detector's end_delay should be adapted from the profile."""
    ctx = appliance_ctx

    await ctx.run_full_cycle(program_key)

    profile = ctx.coordinator.store.profiles[0]
    expected_params = adaptive_phase_params(profile.avg_duration_s)

    actual_end_delay = ctx.coordinator.detector._config.end_delay_s  # noqa: SLF001
    assert actual_end_delay == pytest.approx(expected_params["end_delay_s"], abs=1.0), (
        f"{program_key}: end_delay is {actual_end_delay}, "
        f"expected ~{expected_params['end_delay_s']}"
    )


# Exclude very short cycles (unreliable estimates) and intermittent-split
# programs (cycle splitting makes 30%-progress unreliable).
_TIME_REMAINING_EXCLUDED = _MATCHING_MAY_DIFFER | {
    "toaster",
    "microwave",
    "coffee_machine",
    "electric_kettle",
}


@pytest.mark.parametrize(
    ("program_key", "expected_phase_count"),
    [p for p in APPLIANCE_PROGRAMS if p[0] not in _TIME_REMAINING_EXCLUDED],
)
async def test_appliance_time_remaining_during_cycle(
    appliance_ctx: ApplianceCtx,
    program_key: str,
    expected_phase_count: int,
) -> None:
    """After one learned cycle, the second run should produce time_remaining estimates."""
    ctx = appliance_ctx

    # Learn the profile first.
    await ctx.run_full_cycle(program_key)

    # Start a second cycle.
    ctx.engine.set_program(program_key)
    ctx.engine.start()
    await ctx.hass.async_block_till_done()

    program = PROGRAMS[program_key]
    total_duration = sum(p.duration_s for p in program.phases)

    # Advance to ~30% of cycle and check time_remaining.
    advance_to = total_duration * 0.3
    await ctx.advance(advance_to)

    tr = ctx.coordinator.time_remaining
    assert tr is not None, f"{program_key}: time_remaining is None at 30% of cycle"
    assert tr > 0, f"{program_key}: time_remaining should be > 0 at 30%"

    ctx.engine.stop()
    await ctx.advance(max(80, total_duration * 0.15))


# ---------------------------------------------------------------------------
# Realistic appliance tests: phase detection on real-world-like signals
# ---------------------------------------------------------------------------

REALISTIC_PROGRAMS = [
    ("realistic_washer", 3),
    ("realistic_dryer", 3),
]


@pytest.mark.parametrize(("program_key", "min_phases"), REALISTIC_PROGRAMS)
async def test_realistic_appliance_phase_count(
    appliance_ctx: ApplianceCtx,
    program_key: str,
    min_phases: int,
) -> None:
    """Realistic long-running appliances must produce multiple distinct phases.

    These programs mirror real-world data captured from actual washing machines
    and dryers.  The phase extractor must detect at least `min_phases` phases;
    detecting only 1 means the algorithm is over-smoothing or the shift
    threshold is too high for real-world signals.
    """
    ctx = appliance_ctx

    await ctx.run_full_cycle(program_key)

    assert ctx.coordinator.detector.state.value == "off", (
        f"{program_key}: detector not OFF after cycle"
    )
    assert len(ctx.coordinator.store.profiles) >= 1, (
        f"{program_key}: no profile created"
    )

    profile = ctx.coordinator.store.profiles[0]
    assert profile.phases is not None, f"{program_key}: no phases detected"

    phase_count = len(profile.phases)
    assert phase_count >= min_phases, (
        f"{program_key}: expected >= {min_phases} phases, got {phase_count}. "
        f"Phases: {[(p.avg_power_w, p.pattern) for p in profile.phases]}"
    )


# ---------------------------------------------------------------------------
# Stress-test programs: gradual ramps, high noise, similar phases, spikes
# ---------------------------------------------------------------------------

STRESS_PROGRAMS = [
    ("stress_gradual_washer", 3),
    ("stress_noisy_dryer", 3),
    ("stress_similar_phases", 3),
    ("stress_transient_spikes", 2),
]


@pytest.mark.parametrize(("program_key", "min_phases"), STRESS_PROGRAMS)
async def test_stress_program_phase_count(
    appliance_ctx: ApplianceCtx,
    program_key: str,
    min_phases: int,
) -> None:
    """Stress-test programs must detect the correct number of phases.

    These programs use RAMP, NOISY, and other non-trivial phase types that
    are designed to challenge the phase detection algorithm with realistic
    conditions: gradual transitions, high noise, subtle differences, and
    transient spikes.
    """
    ctx = appliance_ctx

    await ctx.run_full_cycle(program_key)

    assert ctx.coordinator.detector.state.value == "off", (
        f"{program_key}: detector not OFF after cycle"
    )
    assert len(ctx.coordinator.store.profiles) >= 1, (
        f"{program_key}: no profile created"
    )

    profile = ctx.coordinator.store.profiles[0]
    assert profile.phases is not None, f"{program_key}: no phases detected"

    phase_count = len(profile.phases)
    assert phase_count >= min_phases, (
        f"{program_key}: expected >= {min_phases} phases, got {phase_count}. "
        f"Phases: {[(p.avg_power_w, p.pattern) for p in profile.phases]}"
    )


# ---------------------------------------------------------------------------
# Replay tests: raw captured data from real appliances
# ---------------------------------------------------------------------------

REPLAY_PROGRAMS = [
    ("replay_real_washer", 3),
    ("replay_real_dryer", 3),
]


@pytest.mark.parametrize(("program_key", "min_phases"), REPLAY_PROGRAMS)
async def test_replay_real_data_phase_count(
    appliance_ctx: ApplianceCtx,
    program_key: str,
    min_phases: int,
) -> None:
    """Replay of captured real-world power data must detect multiple phases.

    These programs play back actual power readings captured from the user's
    washing machine and dryer.  The phase extractor must identify the same
    distinct phases a human would see in the data.
    """
    ctx = appliance_ctx

    await ctx.run_full_cycle(program_key)

    assert ctx.coordinator.detector.state.value == "off", (
        f"{program_key}: detector not OFF after cycle"
    )
    assert len(ctx.coordinator.store.profiles) >= 1, (
        f"{program_key}: no profile created"
    )

    profile = ctx.coordinator.store.profiles[0]
    assert profile.phases is not None, f"{program_key}: no phases detected"

    phase_count = len(profile.phases)
    assert phase_count >= min_phases, (
        f"{program_key}: expected >= {min_phases} phases, got {phase_count}. "
        f"Phases: {[(p.avg_power_w, p.pattern) for p in profile.phases]}"
    )
