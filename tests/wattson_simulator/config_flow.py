"""Config flow for Wattson Simulator."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow
from homeassistant.const import CONF_NAME
from homeassistant.helpers.selector import TextSelector

from .const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigFlowResult

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_NAME, default="Dryer"): TextSelector(),
    }
)


class WattsonSimulatorConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Wattson Simulator."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        if user_input is not None:
            name = user_input[CONF_NAME]
            await self.async_set_unique_id(f"sim_{name.lower().replace(' ', '_')}")
            self._abort_if_unique_id_configured()
            return self.async_create_entry(title=name, data=user_input)

        return self.async_show_form(step_id="user", data_schema=STEP_USER_SCHEMA)
