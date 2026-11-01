"""Binary sensors for Trueguard zones and panel status."""
from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import DATA_COORDINATOR, DOMAIN
from .entity import TrueguardEntity
from .parser import flag_ok

TYPE_TEXT_TO_DEVICE_CLASS = {
    "PIR kamera": BinarySensorDeviceClass.MOTION,
    "Dørkontakt": BinarySensorDeviceClass.DOOR,
    "Røg alarm": BinarySensorDeviceClass.SMOKE,
    "IR Camera": BinarySensorDeviceClass.MOTION,
    "IR": BinarySensorDeviceClass.MOTION,
    "Door Contact": BinarySensorDeviceClass.DOOR,
    "Smoke Alarm": BinarySensorDeviceClass.SMOKE,
    "Power Switch Meter": BinarySensorDeviceClass.POWER,
}

TYPE_CODE_TO_DEVICE_CLASS = {
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

    if type_code in TYPE_CODE_TO_DEVICE_CLASS:
        return TYPE_CODE_TO_DEVICE_CLASS[type_code]

    type_name = _sensor_type_text(sensor_data)
    if type_name in TYPE_TEXT_TO_DEVICE_CLASS:
        return TYPE_TEXT_TO_DEVICE_CLASS[type_name]

    return None


def _resolve_icon(sensor_data, is_on, device_class, entity_name: str | None = None):
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
    display_name = str(entity_name or "").lower()
    sensor_type_code = _sensor_type_code(sensor)
    lookup_text = f"{sensor_type_name} {sensor_name} {display_name}"

    if sensor_type_code == 37 or "keypad" in lookup_text or "tastatur" in lookup_text:
        return "mdi:dialpad"
    if sensor_type_code == 2 or "remote" in lookup_text or "fjernbetjening" in lookup_text:
        return "mdi:remote"
    if sensor_type_code in (45, 46) or "sirene" in lookup_text or "siren" in lookup_text:
        return "mdi:bullhorn" if is_on else "mdi:bullhorn-outline"
    return (
        "mdi:checkbox-marked-circle-outline"
        if is_on
        else "mdi:checkbox-blank-circle-outline"
    )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Trueguard binary sensors from a config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    async_add_entities(
        _build_zone_entities(coordinator)
        + _build_panel_entities(coordinator)
        + _build_zone_problem_entities(coordinator)
    )


# --- Zones (door, motion, smoke, ...) --------------------------------------------

def _build_zone_entities(coordinator):
    """One sensor per zone in the panel's device list."""
    sensors = (coordinator.data or {}).get("sensors", {})
    return [
        TrueguardZoneSensor(coordinator, sensor_id, sensor)
        for sensor_id, sensor in sensors.items()
    ]


class TrueguardZoneSensor(TrueguardEntity, BinarySensorEntity):
    """A zone such as a door contact, motion detector, or smoke alarm."""

    def __init__(self, coordinator, sensor_id, sensor_data) -> None:
        self._id = sensor_id
        self._sensor_data = sensor_data
        self._attr_name = sensor_data.get("name")
        self._attr_unique_id = f"trueguard_{sensor_id}"
        self._attr_device_class = _resolve_device_class(sensor_data)
        super().__init__(coordinator)

    def _refresh(self) -> None:
        latest = self._data.get("sensors", {}).get(self._id, self._sensor_data)
        self._sensor_data = latest
        is_on = self._client.sensor_is_on(latest)
        self._attr_is_on = bool(is_on) if is_on is not None else None
        self._attr_icon = _resolve_icon(
            self._sensor_data, self._attr_is_on, self._attr_device_class, self._attr_name
        )

    def _value(self):
        return (self._attr_is_on, dict(self._sensor_data or {}))

    @property
    def extra_state_attributes(self):
        """Raw panel values for this zone."""
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


# --- Panel status (panelCondGet "updates") ------------------------------------

# key in the panel's status, name, device class, text key, on when the flag is OK
PANEL_FLAGS = (
    ("battery_ok", "Panel battery", BinarySensorDeviceClass.BATTERY, "battery", False),
    ("tamper_ok", "Panel tamper", BinarySensorDeviceClass.TAMPER, "tamper", False),
    (
        "interference_ok",
        "Radio interference",
        BinarySensorDeviceClass.PROBLEM,
        "interference",
        False,
    ),
    ("ac_activation_ok", "230 V power", BinarySensorDeviceClass.POWER, "ac_activation", True),
    ("sig_gsm_ok", "GSM problem", BinarySensorDeviceClass.PROBLEM, "sig_gsm", False),
)


def _build_panel_entities(coordinator):
    """Create panel status sensors for the flags this panel reports."""
    panel = (coordinator.data or {}).get("panel", {})
    return [
        TrueguardPanelFlag(coordinator, *flag)
        for flag in PANEL_FLAGS
        if flag[0] in panel
    ]


class TrueguardPanelFlag(TrueguardEntity, BinarySensorEntity):
    """A panel-level status flag such as battery, tamper, or 230 V power."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, key, name, device_class, text_key, on_when_ok):
        self._key = key
        self._text_key = text_key
        self._on_when_ok = on_when_ok
        self._attr_name = f"Trueguard {name}"
        self._attr_device_class = device_class
        super().__init__(coordinator)
        self._attr_unique_id = f"trueguard_{self._client.device_key}_panel_{key}"

    def _refresh(self) -> None:
        panel = self._data.get("panel", {})
        ok = flag_ok(panel.get(self._key))
        self._attr_is_on = None if ok is None else (ok if self._on_when_ok else not ok)
        self._attr_extra_state_attributes = {
            "panel_status": panel.get(self._text_key),
            "raw_value": panel.get(self._key),
        }

    def _value(self):
        return (self._attr_is_on, tuple(self._attr_extra_state_attributes.items()))


# --- Per-zone battery and tamper ------------------------------------------------

ZONE_FLAGS = (
    ("battery_ok", "battery", BinarySensorDeviceClass.BATTERY, "battery"),
    ("tamper_ok", "tamper", BinarySensorDeviceClass.TAMPER, "tamper"),
)


def _build_zone_problem_entities(coordinator):
    """Create a battery and a tamper sensor for each zone that reports them."""
    sensors = (coordinator.data or {}).get("sensors", {})
    return [
        TrueguardZoneFlag(coordinator, sensor_id, sensor.get("name") or str(sensor_id), *flag)
        for sensor_id, sensor in sensors.items()
        for flag in ZONE_FLAGS
        if flag[0] in sensor
    ]


class TrueguardZoneFlag(TrueguardEntity, BinarySensorEntity):
    """Low battery or tamper for one zone. On means there is a problem."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, sensor_id, zone_name, key, suffix, device_class, text_key):
        self._sensor_id = sensor_id
        self._key = key
        self._text_key = text_key
        self._attr_name = f"{zone_name} {suffix}"
        self._attr_device_class = device_class
        super().__init__(coordinator)
        self._attr_unique_id = f"trueguard_{sensor_id}_{suffix}"

    def _refresh(self) -> None:
        sensor = self._data.get("sensors", {}).get(self._sensor_id) or {}
        ok = flag_ok(sensor.get(self._key))
        self._attr_is_on = None if ok is None else not ok
        self._attr_extra_state_attributes = {
            "zone": sensor.get("zone"),
            "panel_status": sensor.get(self._text_key) or None,
        }

    def _value(self):
        return (self._attr_is_on, tuple(self._attr_extra_state_attributes.items()))
