"""Interfaces with Egardia/Woonveilig alarm control panel."""
from __future__ import annotations

import logging

from .depend import egardiadevice, egardiaserver
import requests
import voluptuous as vol

from homeassistant.const import (
    CONF_HOST,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_USERNAME,
    EVENT_HOMEASSISTANT_STOP,
    Platform,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import discovery
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.typing import ConfigType

_LOGGER = logging.getLogger(__name__)

ATTR_DISCOVER_DEVICES = "egardia_sensor"

CONF_REPORT_SERVER_CODES = "report_server_codes"
CONF_REPORT_SERVER_ENABLED = "report_server_enabled"
CONF_REPORT_SERVER_PORT = "report_server_port"
CONF_VERSION = "version"

DEFAULT_NAME = "Trueguard"
DEFAULT_PORT = 80
DEFAULT_REPORT_SERVER_ENABLED = False
DEFAULT_REPORT_SERVER_PORT = 52010
DEFAULT_VERSION = "GATE-01"
DOMAIN = "trueguard"
PLATFORMS = [Platform.ALARM_CONTROL_PANEL, Platform.BINARY_SENSOR, Platform.SENSOR]

EGARDIA_DEVICE = "trueguarddevice"
EGARDIA_NAME = "egardianame"
EGARDIA_REPORT_SERVER_CODES = "egardia_rs_codes"
EGARDIA_REPORT_SERVER_ENABLED = "egardia_rs_enabled"
EGARDIA_SERVER = "egardia_server"

NOTIFICATION_ID = "trueguard_notification"
NOTIFICATION_TITLE = "Trueguard"

REPORT_SERVER_CODES_IGNORE = "ignore"

SERVER_CODE_SCHEMA = vol.Schema(
    {
        vol.Optional("arm"): vol.All(cv.ensure_list_csv, [cv.string]),
        vol.Optional("disarm"): vol.All(cv.ensure_list_csv, [cv.string]),
        vol.Optional("armhome"): vol.All(cv.ensure_list_csv, [cv.string]),
        vol.Optional("triggered"): vol.All(cv.ensure_list_csv, [cv.string]),
        vol.Optional("ignore"): vol.All(cv.ensure_list_csv, [cv.string]),
    }
)

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Required(CONF_HOST): cv.string,
                vol.Required(CONF_PASSWORD): cv.string,
                vol.Required(CONF_USERNAME): cv.string,
                vol.Optional(CONF_VERSION, default=DEFAULT_VERSION): cv.string,
                vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
                vol.Optional(CONF_PORT, default=DEFAULT_PORT): cv.port,
                vol.Optional(CONF_REPORT_SERVER_CODES, default={}): SERVER_CODE_SCHEMA,
                vol.Optional(
                    CONF_REPORT_SERVER_ENABLED, default=DEFAULT_REPORT_SERVER_ENABLED
                ): cv.boolean,
                vol.Optional(
                    CONF_REPORT_SERVER_PORT, default=DEFAULT_REPORT_SERVER_PORT
                ): cv.port,
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the Trueguard platform."""
    if DOMAIN not in config:
        return True

    conf = dict(config[DOMAIN])
    await _async_setup_from_conf(hass, conf, config)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Trueguard from a config entry."""
    conf = dict(entry.data)
    conf.setdefault(CONF_NAME, DEFAULT_NAME)
    conf.setdefault(CONF_REPORT_SERVER_CODES, {})
    device, server = await _async_init_connection(hass, conf)
    if device is None:
        return False

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        EGARDIA_DEVICE: device,
        EGARDIA_SERVER: server,
        "conf": conf,
    }
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a Trueguard config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not unload_ok:
        return False

    entry_data = hass.data.get(DOMAIN, {}).pop(entry.entry_id, {})
    server = entry_data.get(EGARDIA_SERVER)
    if server is not None:
        server.stop()
    if DOMAIN in hass.data and not hass.data[DOMAIN]:
        hass.data.pop(DOMAIN)
    return True


async def _async_setup_from_conf(
    hass: HomeAssistant, conf: dict, full_config: ConfigType
) -> bool:
    """Initialize integration from a normalized configuration dict."""
    conf.setdefault(CONF_NAME, DEFAULT_NAME)
    conf.setdefault(CONF_REPORT_SERVER_CODES, {})
    device, server = await _async_init_connection(hass, conf)
    if device is None:
        return False
    hass.data[EGARDIA_DEVICE] = device
    if server is not None:
        hass.data[EGARDIA_SERVER] = server

    hass.async_create_task(
        discovery.async_load_platform(
            hass, Platform.ALARM_CONTROL_PANEL, DOMAIN, discovered=conf, hass_config=full_config
        )
    )

    # Get the sensors from the device and add those
    _LOGGER.debug("Getting sensors")

    sensors = await hass.async_add_executor_job(device.getsensors)
    hass.async_create_task(
        discovery.async_load_platform(
            hass, Platform.BINARY_SENSOR, DOMAIN, {ATTR_DISCOVER_DEVICES: sensors}, full_config
        )
    )
    hass.async_create_task(
        discovery.async_load_platform(
            hass, Platform.SENSOR, DOMAIN, {ATTR_DISCOVER_DEVICES: sensors}, full_config
        )
    )

    return True


async def _async_init_connection(hass: HomeAssistant, conf: dict):
    """Create device and optional report server from configuration."""
    username = conf.get(CONF_USERNAME)
    password = conf.get(CONF_PASSWORD)
    host = conf.get(CONF_HOST)
    port = conf.get(CONF_PORT, DEFAULT_PORT)
    version = conf.get(CONF_VERSION, DEFAULT_VERSION)
    rs_enabled = conf.get(CONF_REPORT_SERVER_ENABLED, DEFAULT_REPORT_SERVER_ENABLED)
    rs_port = conf.get(CONF_REPORT_SERVER_PORT, DEFAULT_REPORT_SERVER_PORT)

    try:
        device = await hass.async_add_executor_job(
            egardiadevice.EgardiaDevice,
            host, port, username, password, "", version,
        )
    except requests.exceptions.RequestException:
        _LOGGER.error(
            "An error occurred accessing your Trueguard device. "
            "Please check configuration"
        )
        return None, None
    except egardiadevice.UnauthorizedError:
        _LOGGER.error("Unable to authorize. Wrong password or username")
        return None, None

    server = None
    if rs_enabled:
        _LOGGER.debug("Setting up EgardiaServer")
        try:
            server = egardiaserver.EgardiaServer("", rs_port)
            bound = server.bind()
            if not bound:
                raise OSError(
                    "Binding error occurred while starting EgardiaServer."
                )
            server.start()

            def handle_stop_event(event):
                """Handle Home Assistant stop event."""
                server.stop()

            hass.bus.listen_once(EVENT_HOMEASSISTANT_STOP, handle_stop_event)
        except OSError:
            _LOGGER.error("Binding error occurred while starting EgardiaServer")
            return None, None

    return device, server

