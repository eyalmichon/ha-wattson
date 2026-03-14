"""Integration tests for Wattson config flow."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.wattson.const import (
    DOMAIN,
    SOURCE_ENTITY,
    SOURCE_MQTT,
)


@pytest.fixture(autouse=True)
def _bypass_setup(hass: HomeAssistant) -> None:
    """Prevent actual setup from running during config flow tests."""


async def test_step_user_shows_form(hass: HomeAssistant) -> None:
    """Step 1 shows a form with name and source type."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"


async def test_entity_flow_creates_entry(hass: HomeAssistant) -> None:
    """Full entity flow: user step -> source_entity step -> entry created."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    with patch("custom_components.wattson.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"name": "Washing Machine", "source_type": SOURCE_ENTITY},
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "source_entity"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"entity_id": "sensor.power_meter", "start_threshold": 10.0},
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Washing Machine"
    assert result["data"]["entity_id"] == "sensor.power_meter"
    assert result["data"]["source_type"] == SOURCE_ENTITY
    assert result["data"]["start_threshold"] == 10.0


async def test_mqtt_flow_creates_entry(hass: HomeAssistant) -> None:
    """Full MQTT flow: user step -> source_mqtt step -> entry created."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    with patch("custom_components.wattson.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"name": "Dryer", "source_type": SOURCE_MQTT},
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "source_mqtt"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"mqtt_topic": "home/dryer/power", "start_threshold": 5.0},
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Dryer"
    assert result["data"]["mqtt_topic"] == "home/dryer/power"
    assert result["data"]["source_type"] == SOURCE_MQTT


async def test_duplicate_entity_aborts(hass: HomeAssistant) -> None:
    """A second config entry with the same entity_id aborts."""
    # First entry
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    with patch("custom_components.wattson.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"name": "Washer", "source_type": SOURCE_ENTITY},
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"entity_id": "sensor.washer_power", "start_threshold": 5.0},
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY

    # Second entry with same entity
    result2 = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result2 = await hass.config_entries.flow.async_configure(
        result2["flow_id"],
        {"name": "Washer 2", "source_type": SOURCE_ENTITY},
    )
    result2 = await hass.config_entries.flow.async_configure(
        result2["flow_id"],
        {"entity_id": "sensor.washer_power", "start_threshold": 5.0},
    )
    assert result2["type"] is FlowResultType.ABORT
    assert result2["reason"] == "already_configured"
