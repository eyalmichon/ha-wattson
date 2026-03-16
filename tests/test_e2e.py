"""End-to-end tests: Wattson + Wattson Simulator wired together."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from custom_components.wattson.const import (
    CONF_OFF_THRESHOLD,
    CONF_START_THRESHOLD,
)
from custom_components.wattson.const import DOMAIN as WATTSON_DOMAIN

from .conftest import WattsonTestContext, create_wattson_test_context

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from custom_components.wattson.coordinator import WattsonCoordinator

SIM_POWER_ENTITY = "sensor.dryer_power"


@pytest.fixture
async def e2e(hass: HomeAssistant) -> WattsonTestContext:
    """Set up both integrations wired together."""
    async for ctx in create_wattson_test_context(
        hass,
        name="Dryer",
        power_entity=SIM_POWER_ENTITY,
    ):
        yield ctx


# ---------------------------------------------------------------------------
# 1. Full cycle creates profile
# ---------------------------------------------------------------------------


async def test_full_cycle_creates_profile(e2e: WattsonTestContext) -> None:
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
    assert program_state.state == "Program #1"

    profile_select = hass.states.get("select.dryer_profile")
    assert profile_select is not None
    assert len(profile_select.attributes["options"]) == 1


# ---------------------------------------------------------------------------
# 2. Second cycle matches existing profile
# ---------------------------------------------------------------------------


async def test_second_cycle_matches_profile(e2e: WattsonTestContext) -> None:
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


async def test_different_program_creates_second_profile(
    e2e: WattsonTestContext,
) -> None:
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


async def test_mid_cycle_program_identification(e2e: WattsonTestContext) -> None:
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


async def test_time_remaining_monotonic(e2e: WattsonTestContext) -> None:
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


async def test_short_cycle_discarded(e2e: WattsonTestContext) -> None:
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


async def test_rename_profile_via_text_entity(e2e: WattsonTestContext) -> None:
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


async def test_delete_profile_via_button(e2e: WattsonTestContext) -> None:
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
        {"entity_id": "button.dryer_profile_delete"},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert len(e2e.coordinator.store.profiles) == 1
    profile_select = hass.states.get("select.dryer_profile")
    assert len(profile_select.attributes["options"]) == 1


# ---------------------------------------------------------------------------
# 9. Rename profile via service
# ---------------------------------------------------------------------------


async def test_rename_profile_via_service(e2e: WattsonTestContext) -> None:
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


async def test_delete_profile_via_service(e2e: WattsonTestContext) -> None:
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


async def test_list_profiles_service(e2e: WattsonTestContext) -> None:
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


async def test_anti_wrinkle_cycle_ends(e2e: WattsonTestContext) -> None:
    """The anti-wrinkle program (intermittent spikes) still ends the cycle."""
    await e2e.run_full_cycle("anti_wrinkle_test")

    assert e2e.coordinator.detector.state.value == "off"
    assert len(e2e.coordinator.store.cycles) >= 1
    assert len(e2e.coordinator.store.profiles) >= 1


# ---------------------------------------------------------------------------
# 13. Cycle end delay is fast (~30s, not 180s)
# ---------------------------------------------------------------------------


async def test_cycle_end_delay_is_fast(e2e: WattsonTestContext) -> None:
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


async def test_options_flow_changes_thresholds(e2e: WattsonTestContext) -> None:
    """Changing start_threshold via options makes detector ignore lower power."""
    hass = e2e.hass

    result = await hass.config_entries.options.async_init(e2e.wattson_entry.entry_id)
    assert result["type"] == "form"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            CONF_START_THRESHOLD: 5000.0,
            CONF_OFF_THRESHOLD: 1.0,
            "end_delay": 0,
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


async def test_unload_and_reload(e2e: WattsonTestContext) -> None:
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


# ---------------------------------------------------------------------------
# 16. Full cycle detects phases
# ---------------------------------------------------------------------------


async def test_full_cycle_detects_phases(e2e: WattsonTestContext) -> None:
    """A completed cycle with distinct power levels should detect phases."""
    await e2e.run_full_cycle("normal_dry")

    profile = e2e.coordinator.store.profiles[0]
    assert profile.phases is not None
    assert len(profile.phases) >= 2

    first_phase = profile.phases[0]
    assert first_phase.start_pct == 0.0
    last_phase = profile.phases[-1]
    assert last_phase.end_pct > 0.0


# ---------------------------------------------------------------------------
# 17. Anti-wrinkle cycle detects intermittent phase
# ---------------------------------------------------------------------------


async def test_anti_wrinkle_detects_intermittent_phase(e2e: WattsonTestContext) -> None:
    """The anti-wrinkle program should produce an intermittent phase at the end."""
    await e2e.run_full_cycle("anti_wrinkle_test")

    profile = e2e.coordinator.store.profiles[0]
    assert profile.phases is not None
    assert len(profile.phases) >= 2

    last = profile.phases[-1]
    assert last.avg_power_w < 100.0


# ---------------------------------------------------------------------------
# 18. Phase sensor entity exists and shows None when off
# ---------------------------------------------------------------------------


async def test_phase_sensor_entity_exists(e2e: WattsonTestContext) -> None:
    """Phase sensor exists and is None when no cycle is running."""
    hass = e2e.hass
    state = hass.states.get("sensor.dryer_phase")
    assert state is not None
    assert state.state in ("unknown", "unavailable", "None")


# ---------------------------------------------------------------------------
# 19. Phase select and phase name entities exist
# ---------------------------------------------------------------------------


async def test_phase_select_exists(e2e: WattsonTestContext) -> None:
    """Phase select entity exists (may have no options if no profiles yet)."""
    hass = e2e.hass
    state = hass.states.get("select.dryer_profile_phase")
    assert state is not None


# ---------------------------------------------------------------------------
# 20. Rename phase via text entity
# ---------------------------------------------------------------------------


async def test_rename_phase_via_text_entity(e2e: WattsonTestContext) -> None:
    """Editing the phase name text entity renames the selected phase."""
    hass = e2e.hass
    await e2e.run_full_cycle("normal_dry")

    profile = e2e.coordinator.store.profiles[0]
    assert profile.phases is not None
    assert len(profile.phases) >= 2

    phase_select_state = hass.states.get("select.dryer_profile_phase")
    assert phase_select_state is not None
    options = phase_select_state.attributes.get("options", [])
    assert len(options) >= 2

    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": "select.dryer_profile_phase", "option": options[0]},
        blocking=True,
    )
    await hass.async_block_till_done()

    await hass.services.async_call(
        "text",
        "set_value",
        {"entity_id": "text.dryer_profile_phase_name", "value": "Heating"},
        blocking=True,
    )
    await hass.async_block_till_done()

    updated_profile = e2e.coordinator.store.profiles[0]
    assert updated_profile.phases[0].name == "Heating"


# ---------------------------------------------------------------------------
# 21. Mark phase as cycle done via switch
# ---------------------------------------------------------------------------


async def test_mark_phase_done_via_switch(e2e: WattsonTestContext) -> None:
    """Toggling the phase-done switch marks the phase."""
    hass = e2e.hass
    await e2e.run_full_cycle("normal_dry")

    profile = e2e.coordinator.store.profiles[0]
    assert profile.phases is not None

    phase_select_state = hass.states.get("select.dryer_profile_phase")
    options = phase_select_state.attributes.get("options", [])

    # Select the last phase.
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": "select.dryer_profile_phase", "option": options[-1]},
        blocking=True,
    )
    await hass.async_block_till_done()

    await hass.services.async_call(
        "switch",
        "turn_on",
        {"entity_id": "switch.dryer_profile_phase_done"},
        blocking=True,
    )
    await hass.async_block_till_done()

    updated_profile = e2e.coordinator.store.profiles[0]
    assert updated_profile.phases[-1].marks_cycle_done is True

    switch_state = hass.states.get("switch.dryer_profile_phase_done")
    assert switch_state.state == "on"
