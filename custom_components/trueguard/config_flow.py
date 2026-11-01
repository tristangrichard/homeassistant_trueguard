"""Config flow for Trueguard integration."""
from __future__ import annotations

from collections.abc import Mapping
import ipaddress
import logging
from typing import Any

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
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import format_mac

from . import (
    CONF_DEVICE_KEY,
    CONF_MAC,
    CONF_REPORT_ACCOUNT,
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
    device_key,
    entry_setting,
)
from .api import (
    PanelParseError,
    TrueguardClient,
    TrueguardConnectionError,
    UnauthorizedError,
    VersionError,
)

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
            client = TrueguardClient(
                async_get_clientsession(self.hass),
                data[CONF_HOST],
                data.get(CONF_PORT, DEFAULT_PORT),
                data[CONF_USERNAME],
                data[CONF_PASSWORD],
                data.get(CONF_VERSION, DEFAULT_VERSION),
            )
            await client.async_get_data()
        except UnauthorizedError:
            return "invalid_auth"
        except (VersionError, PanelParseError):
            # A panel that answers with something unexpected is usually set
            # to the wrong version.
            return "wrong_version"
        except TrueguardConnectionError:
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

    # --- DHCP discovery ----------------------------------------------------------

    async def async_step_dhcp(self, discovery_info):
        """Handle a panel seen on the network by its MAC address.

        A panel that is already set up has its address updated if it moved
        (only when it was set up with an IP address, not a hostname), and
        remembers its MAC the first time it is seen at its configured address.
        """
        mac = format_mac(discovery_info.macaddress)
        ip = discovery_info.ip
        for entry in self._async_current_entries(include_ignore=False):
            if entry.data.get(CONF_MAC) == mac:
                if entry.data.get(CONF_HOST) != ip and _is_ip(entry.data.get(CONF_HOST)):
                    _LOGGER.info(
                        "Trueguard panel %s moved from %s to %s",
                        mac,
                        entry.data.get(CONF_HOST),
                        ip,
                    )
                    port = entry.data.get(CONF_PORT, DEFAULT_PORT)
                    self.hass.config_entries.async_update_entry(
                        entry,
                        unique_id=f"{ip}:{port}",
                        data={
                            **entry.data,
                            CONF_HOST: ip,
                            CONF_DEVICE_KEY: device_key(entry.data),
                        },
                    )
                    self.hass.config_entries.async_schedule_reload(entry.entry_id)
                return self.async_abort(reason="already_configured")
            if entry.data.get(CONF_HOST) == ip and not entry.data.get(CONF_MAC):
                self.hass.config_entries.async_update_entry(
                    entry, data={**entry.data, CONF_MAC: mac}
                )
                return self.async_abort(reason="already_configured")
            if entry.data.get(CONF_HOST) == ip:
                return self.async_abort(reason="already_configured")

        await self.async_set_unique_id(f"{ip}:{DEFAULT_PORT}")
        self._abort_if_unique_id_configured()
        self._discovered = {CONF_HOST: ip, CONF_MAC: mac}
        self.context["title_placeholders"] = {"host": ip}
        return await self.async_step_dhcp_confirm()

    async def async_step_dhcp_confirm(self, user_input=None):
        """Ask for the login of a discovered panel."""
        errors = {}
        discovered = self._discovered
        if user_input is not None:
            data = {
                **user_input,
                CONF_HOST: discovered[CONF_HOST],
                CONF_MAC: discovered[CONF_MAC],
                CONF_REPORT_SERVER_ENABLED: DEFAULT_REPORT_SERVER_ENABLED,
                CONF_REPORT_SERVER_PORT: DEFAULT_REPORT_SERVER_PORT,
            }
            await self.async_set_unique_id(f"{data[CONF_HOST]}:{data[CONF_PORT]}")
            self._abort_if_unique_id_configured()
            if (error := await self._async_validate(data)) is None:
                return self.async_create_entry(
                    title=data.get(CONF_NAME, DEFAULT_NAME), data=data
                )
            errors["base"] = error

        current = user_input or {}
        schema = vol.Schema(
            {
                vol.Required(CONF_NAME, default=current.get(CONF_NAME, DEFAULT_NAME)): str,
                vol.Required(CONF_PORT, default=current.get(CONF_PORT, DEFAULT_PORT)): int,
                vol.Required(
                    CONF_USERNAME, default=current.get(CONF_USERNAME, vol.UNDEFINED)
                ): str,
                vol.Required(CONF_PASSWORD): str,
                vol.Required(
                    CONF_VERSION, default=current.get(CONF_VERSION, DEFAULT_VERSION)
                ): vol.In(SUPPORTED_VERSIONS),
            }
        )
        return self.async_show_form(
            step_id="dhcp_confirm",
            data_schema=schema,
            errors=errors,
            description_placeholders={"host": discovered[CONF_HOST]},
        )

    async def async_step_reconfigure(self, user_input=None):
        """Change the panel's address, version or login."""
        entry = self._get_reconfigure_entry()
        errors = {}

        if user_input is not None:
            new_unique_id = f"{user_input[CONF_HOST]}:{user_input[CONF_PORT]}"
            if any(
                other.unique_id == new_unique_id and other.entry_id != entry.entry_id
                for other in self._async_current_entries(include_ignore=False)
            ):
                return self.async_abort(reason="already_configured")

            data = {
                **entry.data,
                **user_input,
                # Keep the original identity so the device and the alarm
                # entity keep their IDs when the address changes.
                CONF_DEVICE_KEY: device_key(entry.data),
            }
            if (error := await self._async_validate(data)) is None:
                return self.async_update_reload_and_abort(
                    entry, unique_id=new_unique_id, data=data
                )
            errors["base"] = error

        current = {**entry.data, **(user_input or {})}
        schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default=current.get(CONF_HOST, vol.UNDEFINED)): str,
                vol.Required(CONF_PORT, default=current.get(CONF_PORT, DEFAULT_PORT)): int,
                vol.Required(
                    CONF_VERSION, default=current.get(CONF_VERSION, DEFAULT_VERSION)
                ): vol.In(SUPPORTED_VERSIONS),
                vol.Required(
                    CONF_USERNAME, default=current.get(CONF_USERNAME, vol.UNDEFINED)
                ): str,
                vol.Required(CONF_PASSWORD): str,
            }
        )
        return self.async_show_form(
            step_id="reconfigure", data_schema=schema, errors=errors
        )

    async def async_step_import(self, import_data: Mapping[str, Any]):
        """Import an old YAML "trueguard:" block into a config entry, once."""
        data = {
            CONF_NAME: import_data.get(CONF_NAME, DEFAULT_NAME),
            CONF_HOST: import_data[CONF_HOST],
            CONF_PORT: import_data.get(CONF_PORT, DEFAULT_PORT),
            CONF_USERNAME: import_data[CONF_USERNAME],
            CONF_PASSWORD: import_data[CONF_PASSWORD],
            CONF_VERSION: str(import_data.get(CONF_VERSION, DEFAULT_VERSION)).upper(),
            CONF_REPORT_SERVER_ENABLED: import_data.get(
                CONF_REPORT_SERVER_ENABLED, DEFAULT_REPORT_SERVER_ENABLED
            ),
            CONF_REPORT_SERVER_PORT: import_data.get(
                CONF_REPORT_SERVER_PORT, DEFAULT_REPORT_SERVER_PORT
            ),
        }
        await self.async_set_unique_id(f"{data[CONF_HOST]}:{data[CONF_PORT]}")
        self._abort_if_unique_id_configured()
        if (error := await self._async_validate(data)) is not None:
            return self.async_abort(reason=error)
        options = {}
        if CONF_SCAN_INTERVAL in import_data:
            options[CONF_SCAN_INTERVAL] = float(import_data[CONF_SCAN_INTERVAL])
        return self.async_create_entry(
            title=data[CONF_NAME], data=data, options=options
        )

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


def _is_ip(host) -> bool:
    try:
        ipaddress.ip_address(str(host))
    except ValueError:
        return False
    return True


class TrueguardOptionsFlow(config_entries.OptionsFlow):
    """Change polling and report server settings after setup."""

    async def async_step_init(self, user_input=None):
        """Show the options form."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        entry = self.config_entry
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SCAN_INTERVAL,
                    default=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                ): vol.All(
                    vol.Coerce(float),
                    vol.Range(min=MIN_SCAN_INTERVAL, max=MAX_SCAN_INTERVAL),
                ),
                vol.Required(
                    CONF_REPORT_SERVER_ENABLED,
                    default=entry_setting(
                        entry, CONF_REPORT_SERVER_ENABLED, DEFAULT_REPORT_SERVER_ENABLED
                    ),
                ): bool,
                vol.Required(
                    CONF_REPORT_SERVER_PORT,
                    default=entry_setting(
                        entry, CONF_REPORT_SERVER_PORT, DEFAULT_REPORT_SERVER_PORT
                    ),
                ): vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
                vol.Optional(
                    CONF_REPORT_ACCOUNT,
                    description={
                        "suggested_value": entry_setting(entry, CONF_REPORT_ACCOUNT, "")
                    },
                ): str,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
