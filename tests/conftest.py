"""Fixtures for Wattson tests."""

from __future__ import annotations

import random
from datetime import timedelta
from pathlib import Path
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
)
from custom_components.wattson.const import DOMAIN as WATTSON_DOMAIN
from custom_components.wattson_simulator.const import DOMAIN as SIM_DOMAIN
from custom_components.wattson_simulator.const import PROGRAMS

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from custom_components.wattson.coordinator import WattsonCoordinator
    from custom_components.wattson_simulator.engine import SimulationEngine

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SIM_SRC = _REPO_ROOT / "tests" / "wattson_simulator"
_SIM_LINK = _REPO_ROOT / "custom_components" / "wattson_simulator"


def pytest_configure(config):
    """Ensure the simulator symlink exists before collection."""
    if _SIM_SRC.is_dir() and not _SIM_LINK.exists():
        _SIM_LINK.symlink_to(_SIM_SRC)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable custom integrations in Home Assistant tests."""
    return


class WattsonTestContext:
    """Shared test context for Wattson + Simulator integration tests.

    Bundles the HA instance, coordinator, simulation engine, and mocked
    time, providing helpers to advance time and run full cycles.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: WattsonCoordinator,
        engine: SimulationEngine,
        mock_time: object,
        t: float,
        start_dt: object,
        *,
        wattson_entry: MockConfigEntry | None = None,
        sim_entry: MockConfigEntry | None = None,
        power_entity: str = "",
    ) -> None:
        self.hass = hass
        self.coordinator = coordinator
        self.engine = engine
        self.mock_time = mock_time
        self.t = t
        self._start_dt = start_dt
        self._dt_offset: float = 0.0
        self.wattson_entry = wattson_entry
        self.sim_entry = sim_entry
        self.power_entity = power_entity

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

    async def run_full_cycle(
        self, program_key: str, end_buffer: float | None = None
    ) -> None:
        """Select a program, start the simulator, advance through the full cycle."""
        self.engine.set_program(program_key)
        self.engine.start()
        await self.hass.async_block_till_done()

        program = PROGRAMS[program_key]
        total_duration = sum(p.duration_s for p in program.phases)
        if end_buffer is None:
            end_buffer = max(80, total_duration * 0.15)
        await self.advance(total_duration + end_buffer)

    async def set_sensor(self, state: str) -> None:
        """Manually set the power sensor state."""
        self.hass.states.async_set(self.power_entity, state)
        await self.hass.async_block_till_done()


async def create_wattson_test_context(
    hass: HomeAssistant,
    *,
    name: str = "Test",
    power_entity: str = "sensor.test_power",
    start_threshold: float = 5.0,
) -> WattsonTestContext:
    """Create a fully wired Wattson + Simulator test context.

    Yields a WattsonTestContext inside a mocked-time context manager.
    Must be used as an async context manager or inside a fixture that
    patches coordinator.time.
    """
    sim_entry = MockConfigEntry(
        domain=SIM_DOMAIN,
        title=name,
        data={"name": name},
        unique_id=f"sim_{name.lower()}",
    )
    sim_entry.add_to_hass(hass)

    wattson_entry = MockConfigEntry(
        domain=WATTSON_DOMAIN,
        title=name,
        data={
            "name": name,
            CONF_SOURCE_TYPE: SOURCE_ENTITY,
            CONF_ENTITY_ID: power_entity,
            CONF_START_THRESHOLD: start_threshold,
        },
        unique_id=power_entity,
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

        yield WattsonTestContext(
            hass,
            coordinator,
            engine,
            mock_time,
            t,
            start_dt,
            wattson_entry=wattson_entry,
            sim_entry=sim_entry,
            power_entity=power_entity,
        )
