"""Edge-case tests for Wattson: error handling, boundaries, and simulator guards."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from homeassistant.exceptions import ServiceValidationError
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.wattson.const import (
    CONF_ENTITY_ID,
    CONF_SOURCE_TYPE,
    CONF_START_THRESHOLD,
    MAX_STORED_CYCLES,
    SOURCE_ENTITY,
)
from custom_components.wattson.const import DOMAIN as WATTSON_DOMAIN
from custom_components.wattson.cycle_recorder import CycleData, CycleRecorder
from custom_components.wattson.profile_matcher import Profile, ProfileMatcher
from custom_components.wattson.store import WattsonStore
from custom_components.wattson_simulator.const import DOMAIN as SIM_DOMAIN

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from custom_components.wattson.coordinator import WattsonCoordinator
    from custom_components.wattson_simulator.engine import SimulationEngine


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

POWER_ENTITY = "sensor.test_power"


@pytest.fixture
async def wattson_setup(hass: HomeAssistant):
    """Set up a minimal Wattson integration with a mock power sensor."""
    entry = MockConfigEntry(
        domain=WATTSON_DOMAIN,
        title="Test",
        data={
            "name": "Test",
            CONF_SOURCE_TYPE: SOURCE_ENTITY,
            CONF_ENTITY_ID: POWER_ENTITY,
            CONF_START_THRESHOLD: 5.0,
        },
        unique_id=POWER_ENTITY,
    )
    entry.add_to_hass(hass)

    with patch("custom_components.wattson.coordinator.time") as mock_time:
        mock_time.time.return_value = 1_000_000.0
        hass.states.async_set(POWER_ENTITY, "0.0")
        await async_setup_component(hass, "persistent_notification", {})
        assert await async_setup_component(hass, WATTSON_DOMAIN, {})
        await hass.async_block_till_done()

        coordinator: WattsonCoordinator = hass.data[WATTSON_DOMAIN][entry.entry_id][
            "coordinator"
        ]
        yield entry, coordinator


@pytest.fixture
async def sim_setup(hass: HomeAssistant):
    """Set up the Wattson Simulator integration."""
    entry = MockConfigEntry(
        domain=SIM_DOMAIN,
        title="SimTest",
        data={"name": "SimTest"},
        unique_id="sim_test",
    )
    entry.add_to_hass(hass)
    assert await async_setup_component(hass, SIM_DOMAIN, {})
    await hass.async_block_till_done()
    engine: SimulationEngine = hass.data[SIM_DOMAIN][entry.entry_id]["engine"]
    return entry, engine


# ===================================================================
# HIGH PRIORITY — Service error handling
# ===================================================================


class TestServiceErrors:
    """Service calls with invalid inputs should raise ServiceValidationError."""

    async def test_list_profiles_invalid_entry_id(
        self,
        hass: HomeAssistant,
        wattson_setup,  # noqa: ARG002
    ) -> None:
        """list_profiles with a bogus config_entry_id raises."""
        with pytest.raises(ServiceValidationError, match="No Wattson entry"):
            await hass.services.async_call(
                WATTSON_DOMAIN,
                "list_profiles",
                {"config_entry_id": "nonexistent_id"},
                blocking=True,
                return_response=True,
            )

    async def test_rename_with_non_wattson_entity(
        self,
        hass: HomeAssistant,
        wattson_setup,  # noqa: ARG002
    ) -> None:
        """rename_profile with an entity not in the Wattson registry raises."""
        hass.states.async_set("select.fake_entity", "something")
        with pytest.raises(ServiceValidationError, match="not a Wattson"):
            await hass.services.async_call(
                WATTSON_DOMAIN,
                "rename_profile",
                {"entity_id": "select.fake_entity", "name": "Test"},
                blocking=True,
            )

    async def test_rename_no_profile_selected(
        self,
        hass: HomeAssistant,
        wattson_setup,  # noqa: ARG002
    ) -> None:
        """rename_profile when no profile is selected (empty store) raises."""
        with pytest.raises(ServiceValidationError, match="No profile"):
            await hass.services.async_call(
                WATTSON_DOMAIN,
                "rename_profile",
                {"entity_id": "select.test_profile", "name": "Test"},
                blocking=True,
            )

    async def test_delete_no_profile_selected(
        self,
        hass: HomeAssistant,
        wattson_setup,  # noqa: ARG002
    ) -> None:
        """delete_profile when no profile is selected (empty store) raises."""
        with pytest.raises(ServiceValidationError, match="No profile"):
            await hass.services.async_call(
                WATTSON_DOMAIN,
                "delete_profile",
                {"entity_id": "select.test_profile"},
                blocking=True,
            )


# ===================================================================
# HIGH PRIORITY — Empty profile entity operations
# ===================================================================


class TestEmptyProfileEntities:
    """Entities should handle the zero-profiles state gracefully."""

    async def test_select_zero_profiles(
        self,
        hass: HomeAssistant,
        wattson_setup,  # noqa: ARG002
    ) -> None:
        """Profile select with no profiles: empty options, no attributes."""
        state = hass.states.get("select.test_profile")
        assert state is not None
        assert state.attributes["options"] == []

    async def test_delete_button_no_selection(
        self, hass: HomeAssistant, wattson_setup
    ) -> None:
        """Pressing delete with no profile selected is a safe no-op."""
        await hass.services.async_call(
            "button",
            "press",
            {"entity_id": "button.test_delete_profile"},
            blocking=True,
        )
        _, coordinator = wattson_setup
        assert len(coordinator.store.profiles) == 0

    async def test_text_set_value_no_selection(
        self, hass: HomeAssistant, wattson_setup
    ) -> None:
        """Setting the text entity with no profile selected is a safe no-op."""
        await hass.services.async_call(
            "text",
            "set_value",
            {"entity_id": "text.test_profile_name", "value": "Whatever"},
            blocking=True,
        )
        _, coordinator = wattson_setup
        assert len(coordinator.store.profiles) == 0


# ===================================================================
# HIGH PRIORITY — Store boundary conditions
# ===================================================================


class TestStoreBoundaries:
    """Store operations at the edges."""

    async def test_cycle_pruning_at_max(self, hass: HomeAssistant) -> None:
        """Adding more than MAX_STORED_CYCLES prunes the oldest."""
        store = WattsonStore(hass, "test_pruning")

        for i in range(MAX_STORED_CYCLES + 5):
            store.add_cycle(
                CycleData(
                    start_time=float(i),
                    end_time=float(i + 1),
                    duration_s=1.0,
                    energy_wh=0.1,
                    samples=[(0.0, 100.0)],
                )
            )

        assert len(store.cycles) == MAX_STORED_CYCLES
        assert store.cycles[0].start_time == 5.0

    async def test_get_profile_nonexistent(self, hass: HomeAssistant) -> None:
        """get_profile with unknown ID returns None."""
        store = WattsonStore(hass, "test_get")
        assert store.get_profile("no-such-id") is None

    async def test_delete_profile_nonexistent(self, hass: HomeAssistant) -> None:
        """delete_profile with unknown ID is a no-op."""
        store = WattsonStore(hass, "test_del")
        store.add_profile(
            Profile(
                id="keep-me",
                name="Keep",
                samples=[(0.0, 100.0)],
                avg_duration_s=60.0,
                avg_energy_wh=1.0,
                cycle_count=1,
                last_updated=0.0,
            )
        )
        store.delete_profile("no-such-id")
        assert len(store.profiles) == 1
        assert store.profiles[0].id == "keep-me"


# ===================================================================
# MEDIUM PRIORITY — Simulator edge cases
# ===================================================================


class TestSimulatorEdgeCases:
    """Engine guards against misuse."""

    async def test_double_start_ignored(self, sim_setup) -> None:
        """Calling start() twice does not reset the running cycle."""
        _, engine = sim_setup
        engine.start()
        assert engine.running
        power_after_first = engine.power_w

        engine.start()
        assert engine.running
        assert engine.power_w == power_after_first

    async def test_double_stop_safe(self, sim_setup) -> None:
        """Calling stop() twice does not crash."""
        _, engine = sim_setup
        engine.start()
        engine.stop()
        assert not engine.running

        engine.stop()
        assert not engine.running
        assert engine.power_w == 0.0

    async def test_invalid_program_key_ignored(self, sim_setup) -> None:
        """set_program with an invalid key leaves the program unchanged."""
        _, engine = sim_setup
        original = engine.program_key
        engine.set_program("nonexistent_program")
        assert engine.program_key == original

    async def test_set_program_while_running_no_effect(self, sim_setup) -> None:
        """set_program while running changes the key but not the current tick phase."""
        _, engine = sim_setup
        engine.set_program("quick_dry")
        engine.start()
        assert engine.running

        engine.set_program("delicate")
        assert engine.program_key == "delicate"
        engine.stop()


# ===================================================================
# MEDIUM PRIORITY — Rapid power changes
# ===================================================================


class TestRapidPowerChanges:
    """Fast-switching power should not create spurious cycles."""

    async def test_no_duplicate_cycles(
        self, hass: HomeAssistant, wattson_setup
    ) -> None:
        """Rapid 0->500->0->500->0 within a few seconds creates no cycle."""
        _, coordinator = wattson_setup
        for val in ["500", "0", "500", "0", "500", "0"]:
            hass.states.async_set(POWER_ENTITY, val)
            await hass.async_block_till_done()

        assert len(coordinator.store.cycles) == 0


# ===================================================================
# MEDIUM PRIORITY — Coordinator with missing entity_id
# ===================================================================


class TestMissingEntityId:
    """Setup with empty entity_id should not crash."""

    async def test_empty_entity_id_setup(self, hass: HomeAssistant) -> None:
        """Config with empty entity_id: setup succeeds, no listener."""
        entry = MockConfigEntry(
            domain=WATTSON_DOMAIN,
            title="NoEntity",
            data={
                "name": "NoEntity",
                CONF_SOURCE_TYPE: SOURCE_ENTITY,
                CONF_ENTITY_ID: "",
                CONF_START_THRESHOLD: 5.0,
            },
            unique_id="empty_entity",
        )
        entry.add_to_hass(hass)

        with patch("custom_components.wattson.coordinator.time") as mock_time:
            mock_time.time.return_value = 1_000_000.0
            assert await async_setup_component(hass, WATTSON_DOMAIN, {})
            await hass.async_block_till_done()

        coordinator: WattsonCoordinator = hass.data[WATTSON_DOMAIN][entry.entry_id][
            "coordinator"
        ]
        assert coordinator._unsub_listener is None  # noqa: SLF001


# ===================================================================
# LOW PRIORITY — Cycle recorder misuse
# ===================================================================


class TestCycleRecorderEdgeCases:
    """Recorder handles unusual call sequences."""

    def test_record_before_start(self) -> None:
        """record() before start() still appends samples (using default start_time=0)."""
        rec = CycleRecorder()
        rec.record(100.0, 5.0)
        rec.record(200.0, 10.0)
        data = rec.finish(15.0)
        assert data.duration_s == 15.0
        assert len(data.samples) >= 1

    def test_finish_negative_duration(self) -> None:
        """finish() with end_time before start_time yields negative duration."""
        rec = CycleRecorder()
        rec.start(100.0)
        rec.record(50.0, 101.0)
        data = rec.finish(90.0)
        assert data.duration_s < 0


# ===================================================================
# LOW PRIORITY — Profile matcher boundaries
# ===================================================================


class TestProfileMatcherBoundaries:
    """Matcher handles degenerate inputs."""

    def test_estimate_remaining_zero_duration_profile(self) -> None:
        """Profile with avg_duration_s=0 returns None."""
        matcher = ProfileMatcher()
        profile = Profile(
            id="zero",
            name="Zero",
            samples=[(0.0, 100.0), (0.0, 100.0)],
            avg_duration_s=0.0,
            avg_energy_wh=0.0,
            cycle_count=1,
            last_updated=0.0,
        )
        remaining, corr, progress = matcher.estimate_remaining(
            [(0.0, 100.0), (1.0, 100.0)], profile
        )
        assert remaining is None

    def test_match_single_sample_cycle(self) -> None:
        """Cycle with only 1 sample returns no match."""
        matcher = ProfileMatcher()
        cycle = CycleData(
            start_time=0.0,
            end_time=1.0,
            duration_s=1.0,
            energy_wh=0.01,
            samples=[(0.0, 100.0)],
        )
        profile = Profile(
            id="p1",
            name="P1",
            samples=[(0.0, 100.0), (10.0, 200.0), (20.0, 100.0)],
            avg_duration_s=20.0,
            avg_energy_wh=1.0,
            cycle_count=1,
            last_updated=0.0,
        )
        result = matcher.match(cycle, [profile])
        assert result is None

    def test_match_empty_profiles(self) -> None:
        """match() with no profiles returns None."""
        matcher = ProfileMatcher()
        cycle = CycleData(
            start_time=0.0,
            end_time=10.0,
            duration_s=10.0,
            energy_wh=1.0,
            samples=[(0.0, 100.0), (5.0, 200.0), (10.0, 100.0)],
        )
        result = matcher.match(cycle, [])
        assert result is None
