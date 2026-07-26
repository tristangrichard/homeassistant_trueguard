"""Interfaces with Egardia/Woonveilig alarm control panel."""
from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import DOMAIN, EGARDIA_SENSOR_COORDINATOR

EGARDIA_TYPE_TO_DEVICE_CLASS = {
    "PIR kamera": BinarySensorDeviceClass.MOTION,
    "Dørkontakt": BinarySensorDeviceClass.DOOR,
    "Røg alarm": BinarySensorDeviceClass.SMOKE,
    "IR Camera": BinarySensorDeviceClass.MOTION,
    "IR": BinarySensorDeviceClass.MOTION,
    "Door Contact": BinarySensorDeviceClass.DOOR,
    "Smoke Alarm": BinarySensorDeviceClass.SMOKE,
    "Power Switch Meter": BinarySensorDeviceClass.POWER,
}

EGARDIA_TYPE_CODE_TO_DEVICE_CLASS = {
    4: BinarySensorDeviceClass.DOOR,
    11: BinarySensorDeviceClass.SMOKE,
    27: BinarySensorDeviceClass.MOTION,
}


def _sensor_type_text(sensor_data) -> str:
    """Return normalized sensor type text from available fields."""
    type_name = sensor_data.get("type_f")
    if isinstance(type_name, str) and type_name.strip():
        return type_name.strip()
    type_value = sensor_data.get("type")
    if isinstance(type_value, str):
        return type_value.strip()
    return ""


def _sensor_type_code(sensor_data):
    """Return numeric sensor type code when available."""
    try:
        return int(sensor_data.get("type"))
    except (TypeError, ValueError):
        return None


def _resolve_device_class(sensor_data):
    """Resolve a Home Assistant device class from panel sensor data."""
    type_code = _sensor_type_code(sensor_data)

    if type_code in EGARDIA_TYPE_CODE_TO_DEVICE_CLASS:
        return EGARDIA_TYPE_CODE_TO_DEVICE_CLASS[type_code]

    type_name = _sensor_type_text(sensor_data)
    if type_name in EGARDIA_TYPE_TO_DEVICE_CLASS:
        return EGARDIA_TYPE_TO_DEVICE_CLASS[type_name]

    return None


def _resolve_icon(sensor_data, is_on, device_class):
    """Resolve icon from device class and sensor metadata."""
    if device_class == BinarySensorDeviceClass.DOOR:
        return "mdi:door-open" if is_on else "mdi:door-closed"
    if device_class == BinarySensorDeviceClass.MOTION:
        return "mdi:motion-sensor" if is_on else "mdi:motion-sensor-off"
    if device_class == BinarySensorDeviceClass.SMOKE:
        return "mdi:smoke-detector-alert" if is_on else "mdi:smoke-detector-variant"
    if device_class == BinarySensorDeviceClass.POWER:
        return "mdi:power-plug" if is_on else "mdi:power-plug-off"

    sensor = sensor_data or {}
    sensor_type_name = _sensor_type_text(sensor).lower()
    sensor_name = str(sensor.get("name", "")).lower()
    sensor_type_code = _sensor_type_code(sensor)
    lookup_text = f"{sensor_type_name} {sensor_name}"

    if sensor_type_code == 37 or "keypad" in lookup_text or "tastatur" in lookup_text:
        return "mdi:dialpad"
    if sensor_type_code == 2 or "remote" in lookup_text or "fjernbetjening" in lookup_text:
        return "mdi:remote"
    if sensor_type_code in (45, 46) or "sirene" in lookup_text or "siren" in lookup_text:
        return "mdi:alarm-bell" if is_on else "mdi:alarm-bell-off"
    return (
        "mdi:checkbox-marked-circle-outline"
        if is_on
        else "mdi:checkbox-blank-circle-outline"
    )


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Initialize the platform."""
    if discovery_info is None:
        return

    coordinator = hass.data.get(EGARDIA_SENSOR_COORDINATOR)
    if coordinator is None:
        return
    async_add_entities(_build_entities(coordinator), False)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Trueguard binary sensors from config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id][EGARDIA_SENSOR_COORDINATOR]
    async_add_entities(_build_entities(coordinator), False)


def _build_entities(coordinator):
    """Build all binary entities from discovered sensor payloads."""
    sensors = coordinator.data or {}
    system = coordinator._egardia_system
    return [
        EgardiaBinarySensor(
            coordinator=coordinator,
            sensor_id=sensors[sensor]["id"],
            name=sensors[sensor]["name"],
            egardia_system=system,
            sensor_data=sensors[sensor],
            device_class=_resolve_device_class(sensors[sensor]),
        )
        for sensor in sensors
    ]


class EgardiaBinarySensor(CoordinatorEntity, BinarySensorEntity):
    """Represents a sensor based on an Egardia sensor (IR, Door Contact)."""

    def __init__(
        self,
        coordinator,
        sensor_id,
        name,
        egardia_system,
        sensor_data,
        device_class,
    ):
        """Initialize the sensor device."""
        super().__init__(coordinator)
        self._id = sensor_id
        self._attr_name = name
        self._attr_unique_id = f"trueguard_{sensor_id}"
        self._attr_device_class = device_class
        self._attr_is_on = None
        self._egardia_system = egardia_system
        self._sensor_data = sensor_data
        self._attr_icon = _resolve_icon(
            self._sensor_data,
            self._attr_is_on,
            self._attr_device_class,
        )
        self._sync_from_coordinator()

    def _sync_from_coordinator(self) -> None:
        """Apply latest cached sensor payload from coordinator."""
        latest = (self.coordinator.data or {}).get(self._id, self._sensor_data)
        self._sensor_data = latest
        egardia_input = self._egardia_system.getsensorstatefromsensor(latest)
        self._attr_is_on = bool(egardia_input) if egardia_input is not None else None
        self._attr_icon = _resolve_icon(
            self._sensor_data,
            self._attr_is_on,
            self._attr_device_class,
        )

    @property
    def icon(self):
        """Return icon when no native device class icon is available."""
        return self._attr_icon

    @property
    def should_poll(self) -> bool:
        """Coordinator handles refreshes."""
        return False

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

    def _handle_coordinator_update(self) -> None:
        """Handle coordinator updates."""
        self._sync_from_coordinator()
        super()._handle_coordinator_update()