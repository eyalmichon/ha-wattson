"""Disruption and edge-case tests for Wattson integration."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN

from custom_components.wattson.const import CycleState

from .conftest import WattsonTestContext, create_wattson_test_context

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


SIM_POWER_ENTITY = "sensor.disrupt_power"


@pytest.fixture
async def ctx(hass: HomeAssistant) -> WattsonTestContext:
    """Set up Wattson + simulator for disruption tests."""
    async for c in create_wattson_test_context(
        hass,
        name="Disrupt",
        power_entity=SIM_POWER_ENTITY,
    ):
        yield c


# ---------------------------------------------------------------------------
# 1. Power outage mid-cycle (sensor → unavailable)
# ---------------------------------------------------------------------------


async def test_power_outage_sensor_unavailable(ctx: WattsonTestContext) -> None:
    """When the sensor becomes unavailable mid-cycle, the cycle should eventually end."""
    ctx.engine.set_program("normal_dry")
    ctx.engine.start()
    await ctx.hass.async_block_till_done()

    await ctx.advance(40)
    assert ctx.coordinator.detector.state == CycleState.RUNNING

    # Simulate power outage: sensor goes unavailable.
    ctx.engine.stop()
    await ctx.set_sensor(STATE_UNAVAILABLE)

    # The coordinator feeds 0W; after trailing energy clears + R_commit, cycle ends.
    await ctx.advance_to_off()

    assert ctx.coordinator.time_remaining is None


# ---------------------------------------------------------------------------
# 2. Door open / user stops appliance (sudden drop to 0W)
# ---------------------------------------------------------------------------


async def test_door_open_sudden_stop(ctx: WattsonTestContext) -> None:
    """Sudden power drop to 0 should end the cycle after end_delay + R_commit."""
    ctx.engine.set_program("normal_dry")
    ctx.engine.start()
    await ctx.hass.async_block_till_done()

    await ctx.advance(40)
    assert ctx.coordinator.detector.state == CycleState.RUNNING

    # Simulate door open: power drops to 0.
    ctx.engine.stop()
    await ctx.set_sensor("0")
    await ctx.advance_to_off()

    # Phase tracking should be reset.
    assert (
        ctx.coordinator.current_phase_name is None
        or ctx.coordinator.time_remaining is None
    )


# ---------------------------------------------------------------------------
# 3. Brief power dip (drops to 0, then resumes within end_delay)
# ---------------------------------------------------------------------------


async def test_brief_power_dip_continues(ctx: WattsonTestContext) -> None:
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


async def test_long_pause_new_cycle(ctx: WattsonTestContext) -> None:
    """A pause longer than end_delay + R_commit ends the cycle; resuming starts a new one."""
    ctx.engine.set_program("normal_dry")
    ctx.engine.start()
    await ctx.hass.async_block_till_done()

    await ctx.advance(40)
    assert ctx.coordinator.detector.state == CycleState.RUNNING

    # Long pause: cycle ends.
    ctx.engine.stop()
    await ctx.set_sensor("0")
    await ctx.advance_to_off()

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


async def test_sensor_reports_unknown_mid_cycle(ctx: WattsonTestContext) -> None:
    """Sensor going to 'unknown' mid-cycle should feed 0W and eventually end."""
    ctx.engine.set_program("normal_dry")
    ctx.engine.start()
    await ctx.hass.async_block_till_done()

    await ctx.advance(30)
    assert ctx.coordinator.detector.state == CycleState.RUNNING

    ctx.engine.stop()
    await ctx.set_sensor(STATE_UNKNOWN)
    await ctx.advance_to_off()


# ---------------------------------------------------------------------------
# 6. Multiple rapid start/stop cycles (false trigger avoidance)
# ---------------------------------------------------------------------------


async def test_rapid_start_stop_no_false_profile(ctx: WattsonTestContext) -> None:
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


async def test_cycle_longer_than_profile(ctx: WattsonTestContext) -> None:
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
    await ctx.advance_to_off()


# ---------------------------------------------------------------------------
# 8. Cycle is much shorter than expected (aborted early)
# ---------------------------------------------------------------------------


async def test_aborted_cycle_no_profile_corruption(ctx: WattsonTestContext) -> None:
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
    await ctx.advance_to_off()

    # The short aborted cycle should be discarded (too short).
    profile_after = ctx.coordinator.store.profiles[0]
    assert profile_after.avg_duration_s == pytest.approx(duration_before, rel=0.1), (
        "Profile was corrupted by aborted cycle"
    )


# ---------------------------------------------------------------------------
# 9. Sensor goes unavailable then comes back (recovery)
# ---------------------------------------------------------------------------


async def test_sensor_unavailable_recovery(ctx: WattsonTestContext) -> None:
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


async def test_back_to_back_cycles(ctx: WattsonTestContext) -> None:
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
