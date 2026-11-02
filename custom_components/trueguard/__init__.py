"""Trueguard alarm panels (Climax-based) for Home Assistant."""
from __future__ import annotations

import logging
import socket

import voluptuous as vol

from homeassistant.config_entries import SOURCE_IMPORT
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
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import ConfigEntryError, ConfigEntryNotReady
from homeassistant.helpers import entity_registry as er, issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .api import TrueguardClient, VersionError
from .coordinator import TrueguardCoordinator
from .receiver import ReportReceiver

_LOGGER = logging.getLogger(__name__)

# Some constants and CONFIG_SCHEMA are modified from Home Assistant's Egardia
# integration (Apache-2.0), see THIRD_PARTY_NOTICES.
CONF_REPORT_SERVER_ENABLED = "report_server_enabled"
CONF_REPORT_SERVER_PORT = "report_server_port"
# Optional account number the panel reports with; blank accepts any.
CONF_REPORT_ACCOUNT = "report_server_account"
CONF_VERSION = "version"
# Stable identity of the panel's device and alarm entity. Set when the
# address is changed with Reconfigure, so entity and device IDs stay the same.
CONF_DEVICE_KEY = "device_key"
# Panel MAC address, learned from DHCP discovery; used to follow IP changes.
CONF_MAC = "mac"
# Options that need the integration to restart when they change.
RELOAD_OPTIONS = (
    CONF_REPORT_SERVER_ENABLED,
    CONF_REPORT_SERVER_PORT,
    CONF_REPORT_ACCOUNT,
)

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
PLATFORMS = [Platform.ALARM_CONTROL_PANEL, Platform.BINARY_SENSOR, Platform.SENSOR]

# Keys in hass.data[DOMAIN][entry_id].
DATA_CLIENT = "client"
DATA_COORDINATOR = "coordinator"
DATA_RECEIVER = "receiver"

# YAML setup was removed in 2.6.0. A "trueguard:" block is imported into a
# config entry once, and a repair issue asks the user to remove it.
CONF_REPORT_SERVER_CODES = "report_server_codes"

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
                # No longer used; accepted so old configs still validate.
                vol.Optional(CONF_REPORT_SERVER_CODES): dict,
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
    """Import an old YAML configuration into a config entry, once."""
    if DOMAIN in config:
        hass.async_create_task(_async_import_yaml(hass, dict(config[DOMAIN])))
    return True


async def _async_import_yaml(hass: HomeAssistant, conf: dict) -> None:
    """Start the import flow and raise a repair issue about the YAML block."""
    if conf.get(CONF_REPORT_SERVER_CODES):
        _LOGGER.warning(
            "Trueguard report_server_codes is no longer used: the push receiver "
            "understands the panel's SIA / Contact ID reports by itself"
        )
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_IMPORT}, data=conf
    )
    imported = result.get("type") is FlowResultType.CREATE_ENTRY or (
        result.get("type") is FlowResultType.ABORT
        and result.get("reason") == "already_configured"
    )
    if imported:
        ir.async_create_issue(
            hass,
            DOMAIN,
            "deprecated_yaml",
            is_fixable=False,
            is_persistent=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="deprecated_yaml",
        )
    else:
        ir.async_create_issue(
            hass,
            DOMAIN,
            "yaml_import_failed",
            is_fixable=False,
            is_persistent=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key="yaml_import_failed",
            translation_placeholders={"reason": str(result.get("reason", "unknown"))},
        )


def device_key(conf: dict) -> str:
    """Return the panel's stable identity, originally host_port."""
    return conf.get(CONF_DEVICE_KEY) or f"{conf.get(CONF_HOST)}_{conf.get(CONF_PORT, DEFAULT_PORT)}"


def zone_unique_id(key: str, sensor_id, suffix: str = "") -> str:
    """Return the unique ID of a zone's entity on the panel with this device key."""
    return f"trueguard_{key}_zone_{sensor_id}{suffix}"


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate an older config entry."""
    if entry.version > 1:
        return False
    if entry.minor_version < 2:
        # Zone entities were trueguard_<zone>[_suffix], which two panels could
        # share. Entity IDs stay the same.
        key = device_key(entry.data)

        @callback
        def _migrate(entity: er.RegistryEntry) -> dict[str, str] | None:
            if entity.domain not in (Platform.BINARY_SENSOR, Platform.SENSOR):
                return None
            unique_id = entity.unique_id
            if unique_id.startswith(f"trueguard_{key}"):
                return None  # a panel-level entity
            zone = unique_id.removeprefix("trueguard_")
            return {"new_unique_id": zone_unique_id(key, zone)}

        await er.async_migrate_entries(hass, entry.entry_id, _migrate)
        hass.config_entries.async_update_entry(entry, minor_version=2)
    return True


def entry_setting(entry: ConfigEntry, key: str, default):
    """Return a setting from the entry's options, falling back to its data."""
    return entry.options.get(key, entry.data.get(key, default))


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Trueguard from a config entry."""
    conf = dict(entry.data)
    # Report server settings can be changed under Configure (options).
    conf[CONF_REPORT_SERVER_ENABLED] = entry_setting(
        entry, CONF_REPORT_SERVER_ENABLED, DEFAULT_REPORT_SERVER_ENABLED
    )
    conf[CONF_REPORT_SERVER_PORT] = entry_setting(
        entry, CONF_REPORT_SERVER_PORT, DEFAULT_REPORT_SERVER_PORT
    )
    conf[CONF_REPORT_ACCOUNT] = entry_setting(entry, CONF_REPORT_ACCOUNT, "")
    conf.setdefault(CONF_NAME, DEFAULT_NAME)
    try:
        client = _create_client(hass, conf)
    except VersionError as err:
        raise ConfigEntryError(str(err)) from err

    # The first refresh is the connection check: an unreachable panel raises
    # ConfigEntryNotReady (setup is retried), a rejected login starts
    # reauthentication, and a wrong panel version fails the entry.
    coordinator = TrueguardCoordinator(
        hass,
        client,
        config_entry=entry,
        interval_seconds=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
    )
    await coordinator.async_config_entry_first_refresh()

    try:
        server = await _async_start_report_server(hass, conf, coordinator)
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
        DATA_CLIENT: client,
        DATA_RECEIVER: server,
        DATA_COORDINATOR: coordinator,
        "conf": conf,
    }
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Apply changed options.

    Report server changes restart the integration; a new polling interval is
    applied without a restart.
    """
    entry_data = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if entry_data is None:
        return
    running = entry_data["conf"]
    if any(
        entry_setting(entry, key, running.get(key)) != running.get(key)
        for key in RELOAD_OPTIONS
    ):
        hass.config_entries.async_schedule_reload(entry.entry_id)
        return
    coordinator = entry_data[DATA_COORDINATOR]
    coordinator.set_interval(
        entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    )


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a Trueguard config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not unload_ok:
        return False

    entry_data = hass.data.get(DOMAIN, {}).pop(entry.entry_id, {})
    server = entry_data.get(DATA_RECEIVER)
    if server is not None:
        # Wait for the port to be released so a reload can bind it again.
        await _async_stop_server(hass, server)
    if DOMAIN in hass.data and not hass.data[DOMAIN]:
        hass.data.pop(DOMAIN)
    return True


def _create_client(hass: HomeAssistant, conf: dict) -> TrueguardClient:
    """Create the panel client on Home Assistant's shared HTTP session.

    Raises VersionError for an unsupported panel version.
    """
    client = TrueguardClient(
        async_get_clientsession(hass),
        conf.get(CONF_HOST),
        conf.get(CONF_PORT, DEFAULT_PORT),
        conf.get(CONF_USERNAME),
        conf.get(CONF_PASSWORD),
        conf.get(CONF_VERSION, DEFAULT_VERSION),
    )
    client.device_key = device_key(conf)
    return client


async def _async_start_report_server(hass: HomeAssistant, conf: dict, coordinator):
    """Start the push report receiver if enabled. Raises OSError if it cannot bind.

    Only connections from the panel's own address are accepted.
    """
    if not conf.get(CONF_REPORT_SERVER_ENABLED, DEFAULT_REPORT_SERVER_ENABLED):
        return None

    host = conf.get(CONF_HOST)
    allowed: set[str] = {host}
    try:
        infos = await hass.loop.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
        allowed |= {info[4][0] for info in infos}
    except OSError as err:
        _LOGGER.warning(
            "Could not resolve %s (%s); reports are ignored until it resolves "
            "and the integration is reloaded",
            host,
            err,
        )
    # IPv4 peers can show up as IPv4-mapped IPv6 addresses.
    allowed |= {f"::ffff:{ip}" for ip in list(allowed) if ":" not in ip}

    receiver = ReportReceiver(
        conf.get(CONF_REPORT_SERVER_PORT, DEFAULT_REPORT_SERVER_PORT),
        allowed,
        str(conf.get(CONF_REPORT_ACCOUNT) or ""),
        coordinator.async_handle_report,
    )
    await receiver.async_start()
    return receiver


async def _async_stop_server(hass: HomeAssistant, server) -> None:
    """Stop the report receiver and release its port."""
    await server.async_stop()
