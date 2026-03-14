"""Integration tests for Wattson coordinator, sensors, and lifecycle."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.wattson.const import (
    CONF_ENTITY_ID,
    CONF_SOURCE_TYPE,
    CONF_START_THRESHOLD,
    DOMAIN,
    SOURCE_ENTITY,
)

MOCK_ENTRY_DATA = {
    "name": "Test Washer",
    CONF_SOURCE_TYPE: SOURCE_ENTITY,
    CONF_ENTITY_ID: "sensor.washer_power",
    CONF_START_THRESHOLD: 5.0,
}


@pytest.fixture
async def setup_integration(hass: HomeAssistant) -> MockConfigEntry:
    """Set up the Wattson integration with a mock config entry."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Test Washer",
        data=MOCK_ENTRY_DATA,
        unique_id="sensor.washer_power",
    )
    entry.add_to_hass(hass)

    # Set up a mock power sensor entity.
    hass.states.async_set("sensor.washer_power", "0.0")

    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()
    return entry


async def test_setup_creates_coordinator(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    """Setup entry creates a coordinator in hass.data."""
    entry = setup_integration
    assert entry.entry_id in hass.data[DOMAIN]
    assert "coordinator" in hass.data[DOMAIN][entry.entry_id]


async def test_unload_cleans_up(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    """Unload entry removes data and stops coordinator."""
    entry = setup_integration
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.entry_id not in hass.data[DOMAIN]


async def test_state_sensor_reflects_detector(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    """The state sensor reflects the detector's cycle state."""
    assert setup_integration is not None
    state = hass.states.get("sensor.test_washer_state")
    assert state is not None
    assert state.state == "off"


async def test_power_sensor_updates(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    """Power sensor updates when the source entity changes."""
    assert setup_integration is not None

    # Simulate a power reading change.
    hass.states.async_set("sensor.washer_power", "100.5")
    await hass.async_block_till_done()

    state = hass.states.get("sensor.test_washer_current_power")
    assert state is not None
    assert float(state.state) == pytest.approx(100.5, rel=0.1)


async def test_binary_sensor_running(hass: HomeAssistant) -> None:
    """Binary sensor turns on when the appliance starts running."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Test Washer",
        data=MOCK_ENTRY_DATA,
        unique_id="sensor.washer_power_bs",
    )
    entry.add_to_hass(hass)

    t = 1000000.0

    with patch("custom_components.wattson.coordinator.time") as mock_time:
        mock_time.time.return_value = t

        hass.states.async_set("sensor.washer_power", "0.0")
        assert await async_setup_component(hass, DOMAIN, {})
        await hass.async_block_till_done()

        bs = hass.states.get("binary_sensor.test_washer_running")
        assert bs is not None
        assert bs.state == "off"

        # Push power above start threshold.
        t += 1.0
        mock_time.time.return_value = t
        hass.states.async_set("sensor.washer_power", "100.0")
        await hass.async_block_till_done()

        # Advance time past start_duration (5s) + energy gate.
        t += 15.0
        mock_time.time.return_value = t
        hass.states.async_set("sensor.washer_power", "100.0", force_update=True)
        await hass.async_block_till_done()

    bs = hass.states.get("binary_sensor.test_washer_running")
    assert bs is not None
    assert bs.state == "on"


async def test_simulated_cycle_lifecycle(hass: HomeAssistant) -> None:
    """Simulate a full cycle: start, run, end — verify state transitions."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Test Washer",
        data=MOCK_ENTRY_DATA,
        unique_id="sensor.washer_power_lifecycle",
    )
    entry.add_to_hass(hass)

    t = 1000000.0

    with patch("custom_components.wattson.coordinator.time") as mock_time:
        mock_time.time.return_value = t

        hass.states.async_set("sensor.washer_power", "0.0")
        await async_setup_component(hass, "persistent_notification", {})
        assert await async_setup_component(hass, DOMAIN, {})
        await hass.async_block_till_done()

        coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

        # Phase 1: power up -> STARTING
        t += 1.0
        mock_time.time.return_value = t
        hass.states.async_set("sensor.washer_power", "50.0")
        await hass.async_block_till_done()
        assert coordinator.detector.state.value == "starting"

        # Phase 2: sustain -> RUNNING
        t += 15.0
        mock_time.time.return_value = t
        hass.states.async_set("sensor.washer_power", "50.0", force_update=True)
        await hass.async_block_till_done()
        assert coordinator.detector.state.value == "running"

        # Phase 3: power drops below off threshold for end_delay -> OFF
        t += 1.0
        mock_time.time.return_value = t
        hass.states.async_set("sensor.washer_power", "0.5")
        await hass.async_block_till_done()

        t += 200.0
        mock_time.time.return_value = t
        hass.states.async_set("sensor.washer_power", "0.5", force_update=True)
        await hass.async_block_till_done()
        assert coordinator.detector.state.value == "off"

    # Verify a cycle was stored.
    assert len(coordinator.store.cycles) == 1
    # Verify a profile was created (no existing profiles -> new one).
    assert len(coordinator.store.profiles) == 1


async def test_unavailable_entity_ignored(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    """Unavailable entity state should not crash the coordinator."""
    assert setup_integration is not None

    hass.states.async_set("sensor.washer_power", STATE_UNAVAILABLE)
    await hass.async_block_till_done()

    state = hass.states.get("sensor.test_washer_current_power")
    assert state is not None
    # Power should remain at 0 (the initial value, not crashed).
    assert float(state.state) == pytest.approx(0.0, abs=0.1)
