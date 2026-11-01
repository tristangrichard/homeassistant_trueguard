"""Interfaces with Egardia/Woonveilig alarm control panel."""
from __future__ import annotations

import logging

from .depend import egardiadevice, egardiaserver
from .coordinator import TrueguardSensorCoordinator
import requests
import voluptuous as vol

from homeassistant.const import (
    CONF_HOST,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_SCAN_INTERVAL,
    CONF_USERNAME,
    EVENT_HOMEASSISTANT_STOP,
    Platform,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryError,
    ConfigEntryNotReady,
)
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
# Seconds between polls of the panel. Each poll makes two HTTP requests.
DEFAULT_SCAN_INTERVAL = 5
MIN_SCAN_INTERVAL = 1
MAX_SCAN_INTERVAL = 3600
DOMAIN = "trueguard"
PLATFORMS = [Platform.ALARM_CONTROL_PANEL, Platform.BINARY_SENSOR]

EGARDIA_DEVICE = "trueguarddevice"
EGARDIA_NAME = "egardianame"
EGARDIA_REPORT_SERVER_CODES = "egardia_rs_codes"
EGARDIA_REPORT_SERVER_ENABLED = "egardia_rs_enabled"
EGARDIA_SERVER = "egardia_server"
EGARDIA_SENSOR_COORDINATOR = "egardia_sensor_coordinator"

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
                vol.Optional(
                    CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL
                ): vol.All(
                    vol.Coerce(float),
                    vol.Range(min=MIN_SCAN_INTERVAL, max=MAX_SCAN_INTERVAL),
                ),
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
    try:
        device = await _async_connect_device(hass, conf)
    except requests.exceptions.RequestException as err:
        # Panel offline or unreachable: Home Assistant retries setup later.
        raise ConfigEntryNotReady(
            f"Unable to reach Trueguard panel at {conf.get(CONF_HOST)}: {err}"
        ) from err
    except egardiadevice.UnauthorizedError as err:
        # Starts the reauthentication flow.
        raise ConfigEntryAuthFailed("Wrong username or password") from err
    except egardiadevice.VersionError as err:
        raise ConfigEntryError(str(err)) from err

    coordinator = TrueguardSensorCoordinator(
        hass,
        device,
        config_entry=entry,
        interval_seconds=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
    )
    await coordinator.async_config_entry_first_refresh()

    try:
        server = await _async_start_report_server(hass, conf)
    except OSError as err:
        raise ConfigEntryNotReady(
            f"Unable to start report server on port "
            f"{conf.get(CONF_REPORT_SERVER_PORT, DEFAULT_REPORT_SERVER_PORT)}: {err}"
        ) from err
    if server is not None:

        async def _async_handle_stop(event) -> None:
            await _async_stop_server(hass, server)

        entry.async_on_unload(
            hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _async_handle_stop)
        )

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        EGARDIA_DEVICE: device,
        EGARDIA_SERVER: server,
        EGARDIA_SENSOR_COORDINATOR: coordinator,
        "conf": conf,
    }
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Apply a new polling interval without reloading the integration."""
    entry_data = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if entry_data is None:
        return
    coordinator = entry_data[EGARDIA_SENSOR_COORDINATOR]
    coordinator.set_interval(
        entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    )


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a Trueguard config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not unload_ok:
        return False

    entry_data = hass.data.get(DOMAIN, {}).pop(entry.entry_id, {})
    server = entry_data.get(EGARDIA_SERVER)
    if server is not None:
        # Wait for the port to be released so a reload can bind it again.
        await _async_stop_server(hass, server)
    if DOMAIN in hass.data and not hass.data[DOMAIN]:
        hass.data.pop(DOMAIN)
    return True


async def _async_setup_from_conf(
    hass: HomeAssistant, conf: dict, full_config: ConfigType
) -> bool:
    """Initialize integration from YAML configuration."""
    conf.setdefault(CONF_NAME, DEFAULT_NAME)
    conf.setdefault(CONF_REPORT_SERVER_CODES, {})
    try:
        device = await _async_connect_device(hass, conf)
    except requests.exceptions.RequestException:
        _LOGGER.error(
            "An error occurred accessing your Trueguard device. "
            "Please check configuration"
        )
        return False
    except egardiadevice.UnauthorizedError:
        _LOGGER.error("Unable to authorize. Wrong password or username")
        return False
    except egardiadevice.VersionError as err:
        _LOGGER.error("%s", err)
        return False

    try:
        server = await _async_start_report_server(hass, conf)
    except OSError:
        _LOGGER.error("Binding error occurred while starting EgardiaServer")
        return False

    hass.data[EGARDIA_DEVICE] = device
    coordinator = TrueguardSensorCoordinator(
        hass,
        device,
        config_entry=None,
        interval_seconds=conf.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
    )
    # async_config_entry_first_refresh is only for coordinators with an entry.
    await coordinator.async_refresh()
    hass.data[EGARDIA_SENSOR_COORDINATOR] = coordinator
    if server is not None:
        hass.data[EGARDIA_SERVER] = server

        async def _async_handle_stop(event) -> None:
            await _async_stop_server(hass, server)

        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _async_handle_stop)

    hass.async_create_task(
        discovery.async_load_platform(
            hass, Platform.ALARM_CONTROL_PANEL, DOMAIN, discovered=conf, hass_config=full_config
        )
    )

    hass.async_create_task(
        discovery.async_load_platform(
            hass, Platform.BINARY_SENSOR, DOMAIN, {ATTR_DISCOVER_DEVICES: None}, full_config
        )
    )

    return True


async def _async_connect_device(hass: HomeAssistant, conf: dict):
    """Connect to the panel.

    Raises requests.exceptions.RequestException, egardiadevice.UnauthorizedError
    or egardiadevice.VersionError.
    """
    return await hass.async_add_executor_job(
        egardiadevice.EgardiaDevice,
        conf.get(CONF_HOST),
        conf.get(CONF_PORT, DEFAULT_PORT),
        conf.get(CONF_USERNAME),
        conf.get(CONF_PASSWORD),
        "",
        conf.get(CONF_VERSION, DEFAULT_VERSION),
    )


async def _async_start_report_server(hass: HomeAssistant, conf: dict):
    """Start the report server if enabled. Raises OSError if it cannot bind."""
    if not conf.get(CONF_REPORT_SERVER_ENABLED, DEFAULT_REPORT_SERVER_ENABLED):
        return None

    _LOGGER.debug("Setting up EgardiaServer")
    server = egardiaserver.EgardiaServer(
        "", conf.get(CONF_REPORT_SERVER_PORT, DEFAULT_REPORT_SERVER_PORT)
    )
    if not await hass.async_add_executor_job(server.bind):
        raise OSError("Binding error occurred while starting EgardiaServer")
    server.start()
    return server


async def _async_stop_server(hass: HomeAssistant, server) -> None:
    """Stop the report server and release its port.

    shutdown() blocks until the serve loop exits, so it runs in the executor.
    """
    await hass.async_add_executor_job(server.stop)

