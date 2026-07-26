"""Interfaces with Egardia/Woonveilig alarm control panel."""
from __future__ import annotations

from datetime import timedelta

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from . import ATTR_DISCOVER_DEVICES, EGARDIA_DEVICE

SCAN_INTERVAL = timedelta(seconds=1)

BATTERY_DEVICE_CLASS = getattr(BinarySensorDeviceClass, "BATTERY", None)
TAMPER_DEVICE_CLASS = getattr(BinarySensorDeviceClass, "TAMPER", None)
SOUND_DEVICE_CLASS = getattr(BinarySensorDeviceClass, "SOUND", None)

EGARDIA_TYPE_TO_DEVICE_CLASS = {
    "PIR kamera": BinarySensorDeviceClass.MOTION,
    "Dørkontakt": BinarySensorDeviceClass.DOOR,
    "Sirene": SOUND_DEVICE_CLASS,
    "Røg alarm": BinarySensorDeviceClass.SMOKE,
    "IR Camera": BinarySensorDeviceClass.MOTION,
    "IR": BinarySensorDeviceClass.MOTION,
    "Siren": SOUND_DEVICE_CLASS,
    "Door Contact": BinarySensorDeviceClass.DOOR,
    "Smoke Alarm": BinarySensorDeviceClass.SMOKE,
    "Power Switch Meter": BinarySensorDeviceClass.POWER,
}

EGARDIA_TYPE_CODE_TO_DEVICE_CLASS = {
    4: BinarySensorDeviceClass.DOOR,
    11: BinarySensorDeviceClass.SMOKE,
    27: BinarySensorDeviceClass.MOTION,
    45: SOUND_DEVICE_CLASS,
    46: SOUND_DEVICE_CLASS,
}


def _resolve_device_class(sensor_data):
    """Resolve a Home Assistant device class from panel sensor data."""
    try:
        type_code = int(sensor_data.get("type"))
    except (TypeError, ValueError):
        type_code = None

    if type_code in EGARDIA_TYPE_CODE_TO_DEVICE_CLASS:
        return EGARDIA_TYPE_CODE_TO_DEVICE_CLASS[type_code]

    type_name = sensor_data.get("type_f")
    if type_name in EGARDIA_TYPE_TO_DEVICE_CLASS:
        return EGARDIA_TYPE_TO_DEVICE_CLASS[type_name]

    return None


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

    async_add_entities(
        [
            EgardiaBinarySensor(
                sensor_id=disc_info[sensor]["id"],
                name=disc_info[sensor]["name"],
                egardia_system=hass.data[EGARDIA_DEVICE],
                sensor_data=disc_info[sensor],
                device_class=_resolve_device_class(disc_info[sensor]),
            )
            for sensor in disc_info
        ]
        + [
            EgardiaDiagnosticBinarySensor(
                sensor_id=disc_info[sensor]["id"],
                name=disc_info[sensor]["name"],
                egardia_system=hass.data[EGARDIA_DEVICE],
                sensor_data=disc_info[sensor],
                diagnostic_type="battery_low",
            )
            for sensor in disc_info
        ]
        + [
            EgardiaDiagnosticBinarySensor(
                sensor_id=disc_info[sensor]["id"],
                name=disc_info[sensor]["name"],
                egardia_system=hass.data[EGARDIA_DEVICE],
                sensor_data=disc_info[sensor],
                diagnostic_type="tamper",
            )
            for sensor in disc_info
        ],
        False,
    )


class EgardiaBinarySensor(BinarySensorEntity):
    """Represents a sensor based on an Egardia sensor (IR, Door Contact)."""

    def __init__(self, sensor_id, name, egardia_system, sensor_data, device_class):
        """Initialize the sensor device."""
        self._id = sensor_id
        self._attr_name = "trueguard_" + name
        self._attr_unique_id = f"trueguard_{sensor_id}"
        self._attr_device_class = device_class
        self._attr_is_on = None
        self._egardia_system = egardia_system
        self._sensor_data = sensor_data

    @property
    def icon(self):
        """Return icon when no native device class icon is available."""
        if self._attr_device_class == BinarySensorDeviceClass.DOOR:
            return "mdi:door-open" if self._attr_is_on else "mdi:door-closed"
        if self._attr_device_class == BinarySensorDeviceClass.MOTION:
            return "mdi:motion-sensor" if self._attr_is_on else "mdi:motion-sensor-off"
        if self._attr_device_class == BinarySensorDeviceClass.SMOKE:
            return "mdi:smoke-detector-alert" if self._attr_is_on else "mdi:smoke-detector-variant"
        if self._attr_device_class == BinarySensorDeviceClass.POWER:
            return "mdi:power-plug" if self._attr_is_on else "mdi:power-plug-off"
        sensor_type_name = str((self._sensor_data or {}).get("type_f", "")).lower()
        if "sirene" in sensor_type_name or "siren" in sensor_type_name:
            return "mdi:alarm-bell" if self._attr_is_on else "mdi:alarm-bell-off"
        return "mdi:checkbox-marked-circle-outline" if self._attr_is_on else "mdi:checkbox-blank-circle-outline"

    @property
    def extra_state_attributes(self):
        """Return extra state attributes."""
        sensor = self._sensor_data or {}
        return {
            "panel_sensor_id": sensor.get("id"),
            "panel_sensor_type": sensor.get("type"),
            "panel_sensor_type_name": sensor.get("type_f"),
            "zone": sensor.get("zone"),
            "area": sensor.get("area"),
            "status_text": sensor.get("status"),
            "raw_state_code": sensor.get("st"),
            "battery_ok": sensor.get("battery_ok"),
            "tamper_ok": sensor.get("tamper_ok"),
            "bypass": sensor.get("bypass"),
            "temp_bypass": sensor.get("temp_bypass"),
            "rssi": sensor.get("rssi"),
            "firmware_version": sensor.get("ver"),
        }

    def update(self) -> None:
        """Update the status."""
        try:
            sensor = self._egardia_system.getsensor(self._id)
            self._sensor_data = sensor
            egardia_input = self._egardia_system.getsensorstatefromsensor(sensor)
            self._attr_is_on = bool(egardia_input) if egardia_input is not None else None
        except Exception:
            self._attr_is_on = None


class EgardiaDiagnosticBinarySensor(BinarySensorEntity):
    """Represents a diagnostic binary sensor for an Egardia sensor."""

    def __init__(self, sensor_id, name, egardia_system, sensor_data, diagnostic_type):
        """Initialize the diagnostic sensor."""
        self._id = sensor_id
        self._name = name
        self._egardia_system = egardia_system
        self._sensor_data = sensor_data
        self._diagnostic_type = diagnostic_type
        self._attr_is_on = None
        self._attr_unique_id = f"trueguard_{sensor_id}_{diagnostic_type}"

        if diagnostic_type == "battery_low":
            self._attr_name = "trueguard_" + name + " battery low"
            self._attr_device_class = BATTERY_DEVICE_CLASS
        elif diagnostic_type == "tamper":
            self._attr_name = "trueguard_" + name + " tamper"
            self._attr_device_class = TAMPER_DEVICE_CLASS

    @property
    def icon(self):
        """Return icon based on diagnostic state."""
        if self._diagnostic_type == "battery_low":
            return "mdi:battery-alert" if self._attr_is_on else "mdi:battery"
        if self._diagnostic_type == "tamper":
            return "mdi:shield-alert" if self._attr_is_on else "mdi:shield-check"
        return "mdi:help-circle-outline"

    @property
    def extra_state_attributes(self):
        """Return diagnostic attributes."""
        sensor = self._sensor_data or {}
        return {
            "panel_sensor_id": sensor.get("id"),
            "panel_sensor_type_name": sensor.get("type_f"),
            "status_text": sensor.get("status"),
            "battery_ok": sensor.get("battery_ok"),
            "tamper_ok": sensor.get("tamper_ok"),
            "rssi": sensor.get("rssi"),
        }

    def _parse_ok_value(self, value):
        """Parse panel health flags where 1 means OK and non-1 means problem."""
        if value is None:
            return None
        val = str(value).strip().lower()
        if val in {"1", "true", "ok"}:
            return True
        if val in {"0", "false", "bad", "alert", "alarm"}:
            return False
        return None

    def update(self) -> None:
        """Update diagnostic status."""
        try:
            sensor = self._egardia_system.getsensor(self._id)
            self._sensor_data = sensor

            if sensor is None:
                self._attr_is_on = None
                return

            if self._diagnostic_type == "battery_low":
                is_ok = self._parse_ok_value(sensor.get("battery_ok"))
                if is_ok is None:
                    self._attr_is_on = None
                else:
                    self._attr_is_on = not is_ok
                return

            if self._diagnostic_type == "tamper":
                is_ok = self._parse_ok_value(sensor.get("tamper_ok"))
                if is_ok is None:
                    status_text = str(sensor.get("tamper", "")).strip()
                    self._attr_is_on = bool(status_text)
                else:
                    self._attr_is_on = not is_ok
        except Exception:
            self._attr_is_on = None
