"""End-to-end tests: Wattson + Wattson Simulator wired together."""

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
    CONF_OFF_THRESHOLD,
    CONF_PAUSE_THRESHOLD,
    CONF_SOURCE_TYPE,
    CONF_START_THRESHOLD,
    SOURCE_ENTITY,
)
from custom_components.wattson.const import DOMAIN as WATTSON_DOMAIN
from custom_components.wattson_simulator.const import DOMAIN as SIM_DOMAIN

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from custom_components.wattson.coordinator import WattsonCoordinator
    from custom_components.wattson_simulator.engine import SimulationEngine


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SIM_POWER_ENTITY = "sensor.dryer_power"


class E2EContext:
    """Bundle returned by the e2e_setup fixture."""

    def __init__(
        self,
        hass: HomeAssistant,
        wattson_entry: MockConfigEntry,
        sim_entry: MockConfigEntry,
        coordinator: WattsonCoordinator,
        engine: SimulationEngine,
        mock_time: object,
        t: float,
        start_dt: object,
    ) -> None:
        self.hass = hass
        self.wattson_entry = wattson_entry
        self.sim_entry = sim_entry
        self.coordinator = coordinator
        self.engine = engine
        self.mock_time = mock_time
        self.t = t
        self._start_dt = start_dt
        self._dt_offset: float = 0.0

    async def advance(self, seconds: float, step: float = 2.0) -> None:
        """Advance wall-clock and fire HA time events in *step*-second increments."""
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
        """Select a program, start the simulator, advance through the full cycle + end delay."""
        from custom_components.wattson_simulator.const import PROGRAMS

        self.engine.set_program(program_key)
        self.engine.start()
        await self.hass.async_block_till_done()

        program = PROGRAMS[program_key]
        total_duration = sum(p.duration_s for p in program.phases)
        await self.advance(total_duration + 60)


@pytest.fixture
async def e2e(hass: HomeAssistant) -> E2EContext:
    """Set up both integrations wired together and return an E2EContext."""
    sim_entry = MockConfigEntry(
        domain=SIM_DOMAIN,
        title="Dryer",
        data={"name": "Dryer"},
        unique_id="sim_dryer",
    )
    sim_entry.add_to_hass(hass)

    wattson_entry = MockConfigEntry(
        domain=WATTSON_DOMAIN,
        title="Dryer",
        data={
            "name": "Dryer",
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

        # persistent_notification is needed by the coordinator's _notify_new_profile.
        await async_setup_component(hass, "persistent_notification", {})

        # Set up the simulator first so its sensor.dryer_power entity exists.
        assert await async_setup_component(hass, SIM_DOMAIN, {})
        await hass.async_block_till_done()

        # Now set up Wattson — it will subscribe to the simulator's entity.
        assert await async_setup_component(hass, WATTSON_DOMAIN, {})
        await hass.async_block_till_done()

        coordinator: WattsonCoordinator = hass.data[WATTSON_DOMAIN][
            wattson_entry.entry_id
        ]["coordinator"]
        engine: SimulationEngine = hass.data[SIM_DOMAIN][sim_entry.entry_id]["engine"]

        ctx = E2EContext(
            hass,
            wattson_entry,
            sim_entry,
            coordinator,
            engine,
            mock_time,
            t,
            start_dt,
        )
        yield ctx  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 1. Full cycle creates profile
# ---------------------------------------------------------------------------


async def test_full_cycle_creates_profile(e2e: E2EContext) -> None:
    """First-ever cycle: Quick Dry creates one profile and one stored cycle."""
    hass = e2e.hass

    assert hass.states.get("sensor.dryer_state").state == "off"
    assert hass.states.get("binary_sensor.dryer_running").state == "off"

    await e2e.run_full_cycle("quick_dry")

    assert e2e.coordinator.detector.state.value == "off"
    assert hass.states.get("binary_sensor.dryer_running").state == "off"

    assert len(e2e.coordinator.store.cycles) == 1
    assert len(e2e.coordinator.store.profiles) == 1

    program_state = hass.states.get("sensor.dryer_program")
    assert program_state is not None
    assert program_state.state == "Pattern #1"

    profile_select = hass.states.get("select.dryer_profile")
    assert profile_select is not None
    assert len(profile_select.attributes["options"]) == 1


# ---------------------------------------------------------------------------
# 2. Second cycle matches existing profile
# ---------------------------------------------------------------------------


async def test_second_cycle_matches_profile(e2e: E2EContext) -> None:
    """Running the same program twice should match, not create a second profile."""
    await e2e.run_full_cycle("quick_dry")
    assert len(e2e.coordinator.store.profiles) == 1
    first_id = e2e.coordinator.store.profiles[0].id

    await e2e.run_full_cycle("quick_dry")

    assert len(e2e.coordinator.store.profiles) == 1
    assert len(e2e.coordinator.store.cycles) == 2
    assert e2e.coordinator.store.profiles[0].cycle_count == 2
    assert e2e.coordinator.store.profiles[0].id == first_id


# ---------------------------------------------------------------------------
# 3. Different program creates second profile
# ---------------------------------------------------------------------------


async def test_different_program_creates_second_profile(e2e: E2EContext) -> None:
    """Quick Dry then Delicate should produce two distinct profiles."""
    await e2e.run_full_cycle("quick_dry")
    assert len(e2e.coordinator.store.profiles) == 1

    await e2e.run_full_cycle("delicate")

    assert len(e2e.coordinator.store.profiles) == 2
    assert len(e2e.coordinator.store.cycles) == 2

    profile_select = e2e.hass.states.get("select.dryer_profile")
    assert len(profile_select.attributes["options"]) == 2


# ---------------------------------------------------------------------------
# 4. Mid-cycle program identification
# ---------------------------------------------------------------------------


async def test_mid_cycle_program_identification(e2e: E2EContext) -> None:
    """After one learned cycle, a second run shows the program name mid-cycle."""
    await e2e.run_full_cycle("quick_dry")
    assert len(e2e.coordinator.store.profiles) == 1

    e2e.engine.set_program("quick_dry")
    e2e.engine.start()
    await e2e.hass.async_block_till_done()

    await e2e.advance(30)

    program_state = e2e.hass.states.get("sensor.dryer_program")
    assert program_state is not None
    assert program_state.state != "unknown"

    e2e.engine.stop()
    await e2e.advance(60)


# ---------------------------------------------------------------------------
# 5. Time remaining is monotonic
# ---------------------------------------------------------------------------


async def test_time_remaining_monotonic(e2e: E2EContext) -> None:
    """Time remaining should never jump backwards during a cycle."""
    await e2e.run_full_cycle("quick_dry")

    e2e.engine.set_program("quick_dry")
    e2e.engine.start()
    await e2e.hass.async_block_till_done()

    readings: list[float] = []
    for _ in range(20):
        await e2e.advance(4)
        tr = e2e.coordinator.time_remaining
        if tr is not None:
            readings.append(tr)

    assert len(readings) >= 3, (
        f"Expected at least 3 time_remaining readings, got {len(readings)}"
    )
    for i in range(1, len(readings)):
        assert readings[i] <= readings[i - 1] + 1.0, (
            f"Time remaining jumped backwards: {readings[i - 1]:.1f} -> {readings[i]:.1f}"
        )

    e2e.engine.stop()
    await e2e.advance(60)


# ---------------------------------------------------------------------------
# 6. Short cycle discarded
# ---------------------------------------------------------------------------


async def test_short_cycle_discarded(e2e: E2EContext) -> None:
    """A very short cycle should not create a profile or store a cycle."""
    e2e.engine.set_program("quick_dry")
    e2e.engine.start()
    await e2e.hass.async_block_till_done()

    await e2e.advance(4)
    e2e.engine.stop()
    await e2e.hass.async_block_till_done()
    await e2e.advance(60)

    assert len(e2e.coordinator.store.profiles) == 0
    assert len(e2e.coordinator.store.cycles) == 0
    assert e2e.coordinator.detector.state.value == "off"


# ---------------------------------------------------------------------------
# 7. Rename profile via text entity
# ---------------------------------------------------------------------------


async def test_rename_profile_via_text_entity(e2e: E2EContext) -> None:
    """Editing the text entity renames the selected profile."""
    hass = e2e.hass
    await e2e.run_full_cycle("quick_dry")

    assert e2e.coordinator.store.profiles[0].name is None

    await hass.services.async_call(
        "text",
        "set_value",
        {"entity_id": "text.dryer_profile_name", "value": "My Quick Dry"},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert e2e.coordinator.store.profiles[0].name == "My Quick Dry"

    profile_select = hass.states.get("select.dryer_profile")
    assert "My Quick Dry" in profile_select.attributes["options"][0]


# ---------------------------------------------------------------------------
# 8. Delete profile via button
# ---------------------------------------------------------------------------


async def test_delete_profile_via_button(e2e: E2EContext) -> None:
    """Pressing the delete button removes the selected profile."""
    hass = e2e.hass
    await e2e.run_full_cycle("quick_dry")
    await e2e.run_full_cycle("delicate")
    assert len(e2e.coordinator.store.profiles) == 2

    select_state = hass.states.get("select.dryer_profile")
    first_option = select_state.attributes["options"][0]
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": "select.dryer_profile", "option": first_option},
        blocking=True,
    )
    await hass.async_block_till_done()

    await hass.services.async_call(
        "button",
        "press",
        {"entity_id": "button.dryer_delete_profile"},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert len(e2e.coordinator.store.profiles) == 1
    profile_select = hass.states.get("select.dryer_profile")
    assert len(profile_select.attributes["options"]) == 1


# ---------------------------------------------------------------------------
# 9. Rename profile via service
# ---------------------------------------------------------------------------


async def test_rename_profile_via_service(e2e: E2EContext) -> None:
    """The wattson.rename_profile service renames the selected profile."""
    hass = e2e.hass
    await e2e.run_full_cycle("quick_dry")

    await hass.services.async_call(
        WATTSON_DOMAIN,
        "rename_profile",
        {"entity_id": "select.dryer_profile", "name": "Renamed Via Service"},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert e2e.coordinator.store.profiles[0].name == "Renamed Via Service"


# ---------------------------------------------------------------------------
# 10. Delete profile via service
# ---------------------------------------------------------------------------


async def test_delete_profile_via_service(e2e: E2EContext) -> None:
    """The wattson.delete_profile service removes the selected profile."""
    hass = e2e.hass
    await e2e.run_full_cycle("quick_dry")
    assert len(e2e.coordinator.store.profiles) == 1

    await hass.services.async_call(
        WATTSON_DOMAIN,
        "delete_profile",
        {"entity_id": "select.dryer_profile"},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert len(e2e.coordinator.store.profiles) == 0


# ---------------------------------------------------------------------------
# 11. List profiles service
# ---------------------------------------------------------------------------


async def test_list_profiles_service(e2e: E2EContext) -> None:
    """The wattson.list_profiles service returns all profile data."""
    hass = e2e.hass
    await e2e.run_full_cycle("quick_dry")

    result = await hass.services.async_call(
        WATTSON_DOMAIN,
        "list_profiles",
        {"config_entry_id": e2e.wattson_entry.entry_id},
        blocking=True,
        return_response=True,
    )

    assert "profiles" in result
    assert len(result["profiles"]) == 1
    p = result["profiles"][0]
    assert "id" in p
    assert "avg_duration_s" in p
    assert "avg_energy_wh" in p
    assert "cycle_count" in p
    assert p["cycle_count"] == 1


# ---------------------------------------------------------------------------
# 12. Anti-wrinkle cycle ends
# ---------------------------------------------------------------------------


async def test_anti_wrinkle_cycle_ends(e2e: E2EContext) -> None:
    """The anti-wrinkle program (intermittent spikes) still ends the cycle."""
    await e2e.run_full_cycle("anti_wrinkle_test")

    assert e2e.coordinator.detector.state.value == "off"
    assert len(e2e.coordinator.store.cycles) >= 1
    assert len(e2e.coordinator.store.profiles) >= 1


# ---------------------------------------------------------------------------
# 13. Cycle end delay is fast (~30s, not 180s)
# ---------------------------------------------------------------------------


async def test_cycle_end_delay_is_fast(e2e: E2EContext) -> None:
    """After simulator stops, the detector should go OFF within ~40s, not 180s."""
    e2e.engine.set_program("quick_dry")
    e2e.engine.start()
    await e2e.hass.async_block_till_done()

    await e2e.advance(80)
    assert e2e.coordinator.detector.state.value in ("running", "off")

    e2e.engine.stop()
    await e2e.hass.async_block_till_done()

    t_before = e2e.t
    for _ in range(30):
        await e2e.advance(2)
        if e2e.coordinator.detector.state.value == "off":
            break

    elapsed = e2e.t - t_before
    assert e2e.coordinator.detector.state.value == "off"
    assert elapsed <= 50, f"End delay took {elapsed}s, expected <= 50s"


# ---------------------------------------------------------------------------
# 14. Options flow changes thresholds
# ---------------------------------------------------------------------------


async def test_options_flow_changes_thresholds(e2e: E2EContext) -> None:
    """Changing start_threshold via options makes detector ignore lower power."""
    hass = e2e.hass

    result = await hass.config_entries.options.async_init(e2e.wattson_entry.entry_id)
    assert result["type"] == "form"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            CONF_START_THRESHOLD: 5000.0,
            CONF_PAUSE_THRESHOLD: 2.0,
            CONF_OFF_THRESHOLD: 1.0,
        },
    )
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()

    assert e2e.wattson_entry.options[CONF_START_THRESHOLD] == 5000.0

    # Reload the entry to pick up new thresholds.
    await hass.config_entries.async_reload(e2e.wattson_entry.entry_id)
    await hass.async_block_till_done()

    coordinator: WattsonCoordinator = hass.data[WATTSON_DOMAIN][
        e2e.wattson_entry.entry_id
    ]["coordinator"]
    e2e.coordinator = coordinator

    e2e.engine.set_program("quick_dry")
    e2e.engine.start()
    await hass.async_block_till_done()
    await e2e.advance(80)

    assert coordinator.detector.state.value in ("off", "starting")
    assert coordinator.detector.state.value != "running"

    e2e.engine.stop()
    await e2e.advance(60)


# ---------------------------------------------------------------------------
# 15. Unload and reload persists profiles
# ---------------------------------------------------------------------------


async def test_unload_and_reload(e2e: E2EContext) -> None:
    """Profiles survive an unload / reload cycle."""
    hass = e2e.hass
    await e2e.run_full_cycle("quick_dry")

    profile_id = e2e.coordinator.store.profiles[0].id

    assert await hass.config_entries.async_unload(e2e.wattson_entry.entry_id)
    await hass.async_block_till_done()
    assert e2e.wattson_entry.entry_id not in hass.data.get(WATTSON_DOMAIN, {})

    assert await hass.config_entries.async_setup(e2e.wattson_entry.entry_id)
    await hass.async_block_till_done()

    coordinator: WattsonCoordinator = hass.data[WATTSON_DOMAIN][
        e2e.wattson_entry.entry_id
    ]["coordinator"]
    assert len(coordinator.store.profiles) == 1
    assert coordinator.store.profiles[0].id == profile_id

    state = hass.states.get("sensor.dryer_state")
    assert state is not None
    assert state.state == "off"
