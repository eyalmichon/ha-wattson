"""Config flow for Wattson."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, OptionsFlow
from homeassistant.const import CONF_NAME
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
)

from .const import (
    CONF_END_DELAY,
    CONF_ENTITY_ID,
    CONF_MQTT_TOPIC,
    CONF_OFF_THRESHOLD,
    CONF_SOURCE_TYPE,
    CONF_START_THRESHOLD,
    DEFAULT_OFF_THRESHOLD_W,
    DEFAULT_START_THRESHOLD_W,
    DOMAIN,
    SOURCE_ENTITY,
    SOURCE_MQTT,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry, ConfigFlowResult

WATT_THRESHOLD_SELECTOR = NumberSelector(
    NumberSelectorConfig(
        min=0.1,
        max=10000,
        step=0.1,
        unit_of_measurement="W",
        mode=NumberSelectorMode.BOX,
    )
)

SECONDS_SELECTOR = NumberSelector(
    NumberSelectorConfig(
        min=0,
        max=600,
        step=1,
        unit_of_measurement="s",
        mode=NumberSelectorMode.BOX,
    )
)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_NAME): TextSelector(),
        vol.Required(CONF_SOURCE_TYPE, default=SOURCE_ENTITY): SelectSelector(
            SelectSelectorConfig(
                options=[
                    {"value": SOURCE_ENTITY, "label": "Home Assistant Entity"},
                    {"value": SOURCE_MQTT, "label": "MQTT Topic"},
                ],
                mode=SelectSelectorMode.DROPDOWN,
            )
        ),
    }
)

STEP_SOURCE_ENTITY_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_ENTITY_ID): EntitySelector(
            EntitySelectorConfig(domain="sensor")
        ),
        vol.Optional(
            CONF_START_THRESHOLD, default=DEFAULT_START_THRESHOLD_W
        ): WATT_THRESHOLD_SELECTOR,
    }
)

STEP_SOURCE_MQTT_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_MQTT_TOPIC): TextSelector(),
        vol.Optional(
            CONF_START_THRESHOLD, default=DEFAULT_START_THRESHOLD_W
        ): WATT_THRESHOLD_SELECTOR,
    }
)


class WattsonConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Wattson."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._user_data: dict[str, Any] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 1: Appliance name and source type."""
        if user_input is not None:
            self._user_data = user_input
            if user_input[CONF_SOURCE_TYPE] == SOURCE_MQTT:
                return await self.async_step_source_mqtt()
            return await self.async_step_source_entity()

        return self.async_show_form(step_id="user", data_schema=STEP_USER_SCHEMA)

    async def async_step_source_entity(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 2 (entity): Pick an HA entity and thresholds."""
        errors: dict[str, str] = {}

        if user_input is not None:
            entity_id = user_input[CONF_ENTITY_ID]

            await self.async_set_unique_id(entity_id)
            self._abort_if_unique_id_configured()

            return self.async_create_entry(
                title=self._user_data[CONF_NAME],
                data={**self._user_data, **user_input},
            )

        return self.async_show_form(
            step_id="source_entity",
            data_schema=STEP_SOURCE_ENTITY_SCHEMA,
            errors=errors,
        )

    async def async_step_source_mqtt(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 2 (MQTT): Pick an MQTT topic and thresholds."""
        errors: dict[str, str] = {}

        if user_input is not None:
            topic = user_input[CONF_MQTT_TOPIC]

            await self.async_set_unique_id(f"mqtt_{topic}")
            self._abort_if_unique_id_configured()

            return self.async_create_entry(
                title=self._user_data[CONF_NAME],
                data={**self._user_data, **user_input},
            )

        return self.async_show_form(
            step_id="source_mqtt",
            data_schema=STEP_SOURCE_MQTT_SCHEMA,
            errors=errors,
        )

    @staticmethod
    def async_get_options_flow(config_entry: ConfigEntry) -> WattsonOptionsFlow:
        """Get the options flow handler."""
        return WattsonOptionsFlow(config_entry)


class WattsonOptionsFlow(OptionsFlow):
    """Handle options for a Wattson config entry."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialize options flow."""
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage threshold options."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        current = self._config_entry.options

        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_START_THRESHOLD,
                    default=current.get(
                        CONF_START_THRESHOLD, DEFAULT_START_THRESHOLD_W
                    ),
                ): WATT_THRESHOLD_SELECTOR,
                vol.Optional(
                    CONF_OFF_THRESHOLD,
                    default=current.get(CONF_OFF_THRESHOLD, DEFAULT_OFF_THRESHOLD_W),
                ): WATT_THRESHOLD_SELECTOR,
                vol.Optional(
                    CONF_END_DELAY,
                    default=current.get(CONF_END_DELAY, 0),
                ): SECONDS_SELECTOR,
            }
        )

        return self.async_show_form(step_id="init", data_schema=schema)
