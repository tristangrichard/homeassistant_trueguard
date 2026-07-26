"""Diagnostic sensors for Trueguard/Egardia devices."""
from __future__ import annotations

from datetime import timedelta
import re

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType
from . import ATTR_DISCOVER_DEVICES, DOMAIN, EGARDIA_DEVICE

SCAN_INTERVAL = timedelta(seconds=1)


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Initialize the platform."""
    if discovery_info is None or discovery_info[ATTR_DISCOVER_DEVICES] is None:
        return

    disc_info = discovery_info[ATTR_DISCOVER_DEVICES]
    async_add_entities(_build_entities(hass, disc_info), False)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Trueguard signal sensors from config entry."""
    egardia_system = hass.data[DOMAIN][entry.entry_id][EGARDIA_DEVICE]
    sensors = await hass.async_add_executor_job(egardia_system.getsensors)
    async_add_entities(_build_entities(hass, sensors, egardia_system), False)


def _build_entities(hass: HomeAssistant, disc_info, egardia_system=None):
    """Build all signal strength entities from discovered sensor payloads."""
    system = egardia_system or hass.data[EGARDIA_DEVICE]
    return [
        EgardiaSignalStrengthSensor(
            sensor_id=disc_info[sensor]["id"],
            name=disc_info[sensor]["name"],
            egardia_system=system,
            sensor_data=disc_info[sensor],
        )
        for sensor in disc_info
    ]


class EgardiaSignalStrengthSensor(SensorEntity):
    """Represents signal strength diagnostics for an Egardia sensor."""

    def __init__(self, sensor_id, name, egardia_system, sensor_data):
        """Initialize the sensor."""
        self._id = sensor_id
        self._attr_name = "trueguard_" + name + " signal strength"
        self._attr_unique_id = f"trueguard_{sensor_id}_signal_strength"
        self._attr_native_value = None
        self._egardia_system = egardia_system
        self._sensor_data = sensor_data

    @property
    def icon(self):
        """Return icon based on signal strength value."""
        value = self._attr_native_value
        if value is None:
            return "mdi:signal-cellular-outline"
        if value <= 2:
            return "mdi:signal-cellular-1"
        if value <= 5:
            return "mdi:signal-cellular-2"
        return "mdi:signal-cellular-3"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information for device registry."""
        panel_host = getattr(self._egardia_system, "_host", "unknown")
        panel_port = getattr(self._egardia_system, "_port", "unknown")
        panel_version = getattr(self._egardia_system, "_version", None)
        return DeviceInfo(
            identifiers={(DOMAIN, f"{panel_host}_{panel_port}")},
            name="Trueguard",
            manufacturer="Trueguard / Woonveilig",
            model=panel_version,
        )

    @property
    def extra_state_attributes(self):
        """Return extra state attributes."""
        sensor = self._sensor_data or {}
        return {
            "panel_sensor_id": sensor.get("id"),
            "panel_sensor_type_name": sensor.get("type_f"),
            "rssi_text": sensor.get("rssi"),
        }

    def _parse_rssi_strength(self, value):
        """Parse panel rssi text like 'Stærk, 9' into integer 9."""
        if value is None:
            return None
        match = re.search(r"(\d+)", str(value))
        if not match:
            return None
        try:
            return int(match.group(1))
        except (TypeError, ValueError):
            return None

    def update(self) -> None:
        """Update the sensor value."""
        try:
            sensor = self._egardia_system.getsensor(self._id)
            self._sensor_data = sensor
            if sensor is None:
                self._attr_native_value = None
                return
            self._attr_native_value = self._parse_rssi_strength(sensor.get("rssi"))
        except Exception:
            self._attr_native_value = None
