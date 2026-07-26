"""Config flow for Trueguard integration."""
from __future__ import annotations

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PASSWORD, CONF_PORT, CONF_USERNAME

from . import (
    CONF_REPORT_SERVER_ENABLED,
    CONF_REPORT_SERVER_PORT,
    CONF_VERSION,
    DEFAULT_NAME,
    DEFAULT_PORT,
    DEFAULT_REPORT_SERVER_ENABLED,
    DEFAULT_REPORT_SERVER_PORT,
    DEFAULT_VERSION,
    DOMAIN,
)

SUPPORTED_VERSIONS = ["WV-1716", "GATE-01", "GATE-02", "GATE-03", "SMARTHOME"]


class TrueguardConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Trueguard."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        errors = {}

        if user_input is not None:
            await self.async_set_unique_id(f"{user_input[CONF_HOST]}:{user_input[CONF_PORT]}")
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=user_input.get(CONF_NAME, DEFAULT_NAME),
                data=user_input,
            )

        schema = vol.Schema(
            {
                vol.Required(CONF_NAME, default=DEFAULT_NAME): str,
                vol.Required(CONF_HOST): str,
                vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
                vol.Required(CONF_USERNAME): str,
                vol.Required(CONF_PASSWORD): str,
                vol.Required(CONF_VERSION, default=DEFAULT_VERSION): vol.In(SUPPORTED_VERSIONS),
                vol.Optional(
                    CONF_REPORT_SERVER_ENABLED, default=DEFAULT_REPORT_SERVER_ENABLED
                ): bool,
                vol.Optional(
                    CONF_REPORT_SERVER_PORT, default=DEFAULT_REPORT_SERVER_PORT
                ): int,
            }
        )

        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)
