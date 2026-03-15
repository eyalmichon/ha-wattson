"""Disruption and edge-case tests for Wattson integration."""

from __future__ import annotations

import random
from datetime import timedelta
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
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
    CycleState,
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

SIM_POWER_ENTITY = "sensor.disrupt_power"


class DisruptCtx:
    """Test context for disruption tests."""

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
        await self.advance(total_duration + 80)

    async def set_sensor(self, state: str) -> None:
        """Manually set the power sensor state."""
        self.hass.states.async_set(SIM_POWER_ENTITY, state)
        await self.hass.async_block_till_done()


@pytest.fixture
async def ctx(hass: HomeAssistant) -> DisruptCtx:
    """Set up Wattson + simulator for disruption tests."""
    sim_entry = MockConfigEntry(
        domain=SIM_DOMAIN,
        title="Disrupt",
        data={"name": "Disrupt"},
        unique_id="sim_disrupt",
    )
    sim_entry.add_to_hass(hass)

    wattson_entry = MockConfigEntry(
        domain=WATTSON_DOMAIN,
        title="Disrupt",
        data={
            "name": "Disrupt",
            CONF_SOURCE_TYPE: SOURCE_ENTITY,
            CONF_ENTITY_ID: SIM_POWER_ENTITY,
            CONF_START_THRESHOLD: 5.0,
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

        yield DisruptCtx(hass, coordinator, engine, mock_time, t, start_dt)  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 1. Power outage mid-cycle (sensor → unavailable)
# ---------------------------------------------------------------------------


async def test_power_outage_sensor_unavailable(ctx: DisruptCtx) -> None:
    """When the sensor becomes unavailable mid-cycle, the cycle should eventually end."""
    ctx.engine.set_program("normal_dry")
    ctx.engine.start()
    await ctx.hass.async_block_till_done()

    await ctx.advance(40)
    assert ctx.coordinator.detector.state == CycleState.RUNNING

    # Simulate power outage: sensor goes unavailable.
    ctx.engine.stop()
    await ctx.set_sensor(STATE_UNAVAILABLE)

    # The coordinator should feed 0W; after end_delay, cycle ends.
    await ctx.advance(80)

    assert ctx.coordinator.detector.state == CycleState.OFF
    assert ctx.coordinator.time_remaining is None


# ---------------------------------------------------------------------------
# 2. Door open / user stops appliance (sudden drop to 0W)
# ---------------------------------------------------------------------------


async def test_door_open_sudden_stop(ctx: DisruptCtx) -> None:
    """Sudden power drop to 0 should end the cycle after end_delay."""
    ctx.engine.set_program("normal_dry")
    ctx.engine.start()
    await ctx.hass.async_block_till_done()

    await ctx.advance(40)
    assert ctx.coordinator.detector.state == CycleState.RUNNING

    # Simulate door open: power drops to 0.
    ctx.engine.stop()
    await ctx.set_sensor("0")
    await ctx.advance(80)

    assert ctx.coordinator.detector.state == CycleState.OFF
    # Phase tracking should be reset.
    assert (
        ctx.coordinator.current_phase_name is None
        or ctx.coordinator.time_remaining is None
    )


# ---------------------------------------------------------------------------
# 3. Brief power dip (drops to 0, then resumes within end_delay)
# ---------------------------------------------------------------------------


async def test_brief_power_dip_continues(ctx: DisruptCtx) -> None:
    """A brief power dip (shorter than end_delay) should not end the cycle."""
    ctx.engine.set_program("normal_dry")
    ctx.engine.start()
    await ctx.hass.async_block_till_done()

    await ctx.advance(30)
    assert ctx.coordinator.detector.state == CycleState.RUNNING

    # Brief dip: stop simulator and set power to 0 for 10s.
    ctx.engine.stop()
    await ctx.set_sensor("0")
    await ctx.advance(10)

    # Still running — end_delay hasn't expired.
    assert ctx.coordinator.detector.state == CycleState.RUNNING

    # Resume power (simulate restart).
    await ctx.set_sensor("2000")
    await ctx.advance(4)

    assert ctx.coordinator.detector.state == CycleState.RUNNING


# ---------------------------------------------------------------------------
# 4. Door open then resume (long pause — cycle ends, new one starts)
# ---------------------------------------------------------------------------


async def test_long_pause_new_cycle(ctx: DisruptCtx) -> None:
    """A pause longer than end_delay ends the cycle; resuming starts a new one."""
    ctx.engine.set_program("normal_dry")
    ctx.engine.start()
    await ctx.hass.async_block_till_done()

    await ctx.advance(40)
    assert ctx.coordinator.detector.state == CycleState.RUNNING

    # Long pause: cycle ends.
    ctx.engine.stop()
    await ctx.set_sensor("0")
    await ctx.advance(80)

    assert ctx.coordinator.detector.state == CycleState.OFF

    # Resume — starts a new cycle.
    await ctx.set_sensor("2000")
    await ctx.advance(20)

    assert ctx.coordinator.detector.state in (
        CycleState.STARTING,
        CycleState.RUNNING,
    )


# ---------------------------------------------------------------------------
# 5. Sensor reports garbage / unknown mid-cycle
# ---------------------------------------------------------------------------


async def test_sensor_reports_unknown_mid_cycle(ctx: DisruptCtx) -> None:
    """Sensor going to 'unknown' mid-cycle should feed 0W and eventually end."""
    ctx.engine.set_program("normal_dry")
    ctx.engine.start()
    await ctx.hass.async_block_till_done()

    await ctx.advance(30)
    assert ctx.coordinator.detector.state == CycleState.RUNNING

    ctx.engine.stop()
    await ctx.set_sensor(STATE_UNKNOWN)
    await ctx.advance(80)

    assert ctx.coordinator.detector.state == CycleState.OFF


# ---------------------------------------------------------------------------
# 6. Multiple rapid start/stop cycles (false trigger avoidance)
# ---------------------------------------------------------------------------


async def test_rapid_start_stop_no_false_profile(ctx: DisruptCtx) -> None:
    """Rapid power spikes and drops should not create profiles."""
    for _ in range(3):
        await ctx.set_sensor("2000")
        await ctx.advance(2)
        await ctx.set_sensor("0")
        await ctx.advance(2)

    await ctx.advance(60)

    assert ctx.coordinator.detector.state == CycleState.OFF
    assert len(ctx.coordinator.store.profiles) == 0
    assert len(ctx.coordinator.store.cycles) == 0


# ---------------------------------------------------------------------------
# 7. Cycle runs much longer than known profile
# ---------------------------------------------------------------------------


async def test_cycle_longer_than_profile(ctx: DisruptCtx) -> None:
    """A cycle running 3x longer than the stored profile shouldn't crash."""
    await ctx.run_full_cycle("quick_dry")
    assert len(ctx.coordinator.store.profiles) == 1

    # Start a second cycle but run it much longer.
    ctx.engine.set_program("quick_dry")
    ctx.engine.start()
    await ctx.hass.async_block_till_done()

    # Quick dry is ~70s; run for 210s+.
    await ctx.advance(250)

    # time_remaining should be 0 or None, not negative or crashing.
    tr = ctx.coordinator.time_remaining
    if tr is not None:
        assert tr >= 0, f"time_remaining went negative: {tr}"

    ctx.engine.stop()
    await ctx.advance(80)

    assert ctx.coordinator.detector.state == CycleState.OFF


# ---------------------------------------------------------------------------
# 8. Cycle is much shorter than expected (aborted early)
# ---------------------------------------------------------------------------


async def test_aborted_cycle_no_profile_corruption(ctx: DisruptCtx) -> None:
    """Aborting a cycle early should not corrupt the stored profile."""
    # Learn the normal pattern first.
    await ctx.run_full_cycle("normal_dry")
    assert len(ctx.coordinator.store.profiles) == 1

    profile_before = ctx.coordinator.store.profiles[0]
    duration_before = profile_before.avg_duration_s

    # Start another cycle but abort after 10s.
    ctx.engine.set_program("normal_dry")
    ctx.engine.start()
    await ctx.hass.async_block_till_done()

    await ctx.advance(10)
    ctx.engine.stop()
    await ctx.set_sensor("0")
    await ctx.advance(80)

    assert ctx.coordinator.detector.state == CycleState.OFF

    # The short aborted cycle should be discarded (too short).
    profile_after = ctx.coordinator.store.profiles[0]
    assert profile_after.avg_duration_s == pytest.approx(duration_before, rel=0.1), (
        "Profile was corrupted by aborted cycle"
    )


# ---------------------------------------------------------------------------
# 9. Sensor goes unavailable then comes back (recovery)
# ---------------------------------------------------------------------------


async def test_sensor_unavailable_recovery(ctx: DisruptCtx) -> None:
    """Sensor going unavailable and then returning should not leave stale state."""
    ctx.engine.set_program("normal_dry")
    ctx.engine.start()
    await ctx.hass.async_block_till_done()

    await ctx.advance(20)
    assert ctx.coordinator.detector.state == CycleState.RUNNING

    # Sensor goes unavailable briefly.
    ctx.engine.stop()
    await ctx.set_sensor(STATE_UNAVAILABLE)
    await ctx.advance(10)

    # Then comes back with high power.
    await ctx.set_sensor("2000")
    await ctx.advance(20)

    # Should still be in an active state (RUNNING or STARTING from a new cycle).
    assert ctx.coordinator.detector.state in (
        CycleState.RUNNING,
        CycleState.STARTING,
    )


# ---------------------------------------------------------------------------
# 10. Two cycles back-to-back with no gap
# ---------------------------------------------------------------------------


async def test_back_to_back_cycles(ctx: DisruptCtx) -> None:
    """Two cycles back-to-back should each be processed correctly."""
    # Run the first full cycle normally.
    await ctx.run_full_cycle("quick_dry")

    assert ctx.coordinator.detector.state == CycleState.OFF
    assert len(ctx.coordinator.store.cycles) >= 1

    cycles_before = len(ctx.coordinator.store.cycles)

    # Immediately start a second cycle with minimal gap.
    await ctx.run_full_cycle("quick_dry")

    assert ctx.coordinator.detector.state == CycleState.OFF
    assert len(ctx.coordinator.store.cycles) >= cycles_before + 1
