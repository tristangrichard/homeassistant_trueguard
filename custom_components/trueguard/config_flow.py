"""Config flow for Trueguard integration."""
from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import requests
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import (
    CONF_HOST,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_SCAN_INTERVAL,
    CONF_USERNAME,
)
from homeassistant.core import callback

from . import (
    CONF_REPORT_SERVER_ENABLED,
    CONF_REPORT_SERVER_PORT,
    CONF_VERSION,
    DEFAULT_NAME,
    DEFAULT_PORT,
    DEFAULT_REPORT_SERVER_ENABLED,
    DEFAULT_REPORT_SERVER_PORT,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_VERSION,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)
from .depend import egardiadevice

_LOGGER = logging.getLogger(__name__)

SUPPORTED_VERSIONS = ["WV-1716", "GATE-01", "GATE-02", "GATE-03", "SMARTHOME"]


class TrueguardConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Trueguard."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Return the options flow."""
        return TrueguardOptionsFlow()

    async def _async_validate(self, data: Mapping[str, Any]) -> str | None:
        """Try to log in to the panel. Return an error key, or None if it worked."""
        try:
            await self.hass.async_add_executor_job(
                egardiadevice.EgardiaDevice,
                data[CONF_HOST],
                data.get(CONF_PORT, DEFAULT_PORT),
                data[CONF_USERNAME],
                data[CONF_PASSWORD],
                "",
                data.get(CONF_VERSION, DEFAULT_VERSION),
            )
        except egardiadevice.UnauthorizedError:
            return "invalid_auth"
        except egardiadevice.VersionError:
            return "wrong_version"
        except requests.exceptions.RequestException:
            return "cannot_connect"
        except Exception:  # pylint: disable=broad-except
            _LOGGER.exception("Unexpected error while connecting to Trueguard panel")
            return "unknown"
        return None

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        errors = {}

        if user_input is not None:
            await self.async_set_unique_id(f"{user_input[CONF_HOST]}:{user_input[CONF_PORT]}")
            self._abort_if_unique_id_configured()
            if (error := await self._async_validate(user_input)) is None:
                return self.async_create_entry(
                    title=user_input.get(CONF_NAME, DEFAULT_NAME),
                    data=user_input,
                )
            errors["base"] = error

        defaults = user_input or {}
        schema = vol.Schema(
            {
                vol.Required(CONF_NAME, default=defaults.get(CONF_NAME, DEFAULT_NAME)): str,
                vol.Required(CONF_HOST, default=defaults.get(CONF_HOST, vol.UNDEFINED)): str,
                vol.Required(CONF_PORT, default=defaults.get(CONF_PORT, DEFAULT_PORT)): int,
                vol.Required(
                    CONF_USERNAME, default=defaults.get(CONF_USERNAME, vol.UNDEFINED)
                ): str,
                vol.Required(CONF_PASSWORD): str,
                vol.Required(
                    CONF_VERSION, default=defaults.get(CONF_VERSION, DEFAULT_VERSION)
                ): vol.In(SUPPORTED_VERSIONS),
                vol.Optional(
                    CONF_REPORT_SERVER_ENABLED,
                    default=defaults.get(
                        CONF_REPORT_SERVER_ENABLED, DEFAULT_REPORT_SERVER_ENABLED
                    ),
                ): bool,
                vol.Optional(
                    CONF_REPORT_SERVER_PORT,
                    default=defaults.get(
                        CONF_REPORT_SERVER_PORT, DEFAULT_REPORT_SERVER_PORT
                    ),
                ): int,
            }
        )

        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]):
        """Start reauthentication after the panel rejected the login."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        """Ask for new credentials and update the config entry."""
        entry = self._get_reauth_entry()
        errors = {}

        if user_input is not None:
            data = {**entry.data, **user_input}
            if (error := await self._async_validate(data)) is None:
                return self.async_update_reload_and_abort(entry, data=data)
            errors["base"] = error

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_USERNAME, default=entry.data.get(CONF_USERNAME, vol.UNDEFINED)
                ): str,
                vol.Required(CONF_PASSWORD): str,
            }
        )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=schema,
            errors=errors,
            description_placeholders={"host": entry.data.get(CONF_HOST, "")},
        )


class TrueguardOptionsFlow(config_entries.OptionsFlow):
    """Change the polling interval after setup."""

    async def async_step_init(self, user_input=None):
        """Show the options form."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        current = self.config_entry.options.get(
            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
        )
        schema = vol.Schema(
            {
                vol.Required(CONF_SCAN_INTERVAL, default=current): vol.All(
                    vol.Coerce(float),
                    vol.Range(min=MIN_SCAN_INTERVAL, max=MAX_SCAN_INTERVAL),
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
