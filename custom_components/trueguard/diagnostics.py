"""Diagnostics download for Trueguard (credentials and MAC address redacted)."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from . import CONF_MAC, CONF_REPORT_ACCOUNT, DOMAIN, EGARDIA_SENSOR_COORDINATOR, EGARDIA_SERVER

TO_REDACT = {CONF_PASSWORD, CONF_USERNAME, CONF_MAC, CONF_REPORT_ACCOUNT}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return the entry settings and what the panel last reported."""
    entry_data = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
    coordinator = entry_data.get(EGARDIA_SENSOR_COORDINATOR)
    receiver = entry_data.get(EGARDIA_SERVER)
    data = (coordinator.data if coordinator else None) or {}
    return {
        "entry": {
            "version": entry.version,
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": async_redact_data(dict(entry.options), TO_REDACT),
        },
        "client": {
            "version": coordinator.client.version if coordinator else None,
            "device_key": coordinator.client.device_key if coordinator else None,
        },
        "coordinator": {
            "last_update_success": coordinator.last_update_success if coordinator else None,
            "update_interval": str(coordinator.update_interval) if coordinator else None,
        },
        "report_receiver": {
            "running": receiver is not None,
            "port": getattr(receiver, "bound_port", None),
        },
        "panel": {
            "state": data.get("state"),
            "status": data.get("panel"),
            "sensors": data.get("sensors"),
        },
    }
