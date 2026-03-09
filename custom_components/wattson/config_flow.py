"""Config flow for Wattson."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigFlow

from .const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigFlowResult


class WattsonConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Wattson."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        if user_input is not None:
            return self.async_create_entry(
                title="Wattson",
                data=user_input,
            )

        return self.async_show_form(step_id="user")
