"""Interfaces with Egardia/Woonveilig alarm control panel."""
from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import DOMAIN, EGARDIA_SENSOR_COORDINATOR
from .entity import TrueguardEntity
from .parser import flag_ok

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
    async_add_entities(
        _build_entities(coordinator)
        + _build_panel_entities(coordinator)
        + _build_zone_problem_entities(coordinator),
        False,
    )


def _build_entities(coordinator):
    """Build all binary entities from discovered sensor payloads."""
    sensors = (coordinator.data or {}).get("sensors", {})
    system = coordinator.egardia_system
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
        self._last_written = None
        self._attr_icon = _resolve_icon(
            self._sensor_data,
            self._attr_is_on,
            self._attr_device_class,
            self._attr_name,
        )
        self._sync_from_coordinator()

    def _sync_from_coordinator(self) -> None:
        """Apply latest cached sensor payload from coordinator."""
        latest = ((self.coordinator.data or {}).get("sensors", {})).get(
            self._id, self._sensor_data
        )
        self._sensor_data = latest
        is_on = self._egardia_system.sensor_is_on(latest)
        self._attr_is_on = bool(is_on) if is_on is not None else None
        self._attr_icon = _resolve_icon(
            self._sensor_data,
            self._attr_is_on,
            self._attr_device_class,
            self._attr_name,
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
    def device_info(self) -> DeviceInfo | None:
        """Return device information for device registry."""
        # Only config-entry entities may attach to a device (HA 2026.9+).
        if self.platform is None or self.platform.config_entry is None:
            return None
        return DeviceInfo(
            identifiers={(DOMAIN, self._egardia_system.device_key)},
            name="Trueguard",
            manufacturer="Trueguard / Woonveilig",
            model=self._egardia_system.version,
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

    def _snapshot(self):
        return (self.available, self._attr_is_on, dict(self._sensor_data or {}))

    async def async_added_to_hass(self) -> None:
        """Remember the state Home Assistant writes when the entity is added."""
        await super().async_added_to_hass()
        self._last_written = self._snapshot()

    def _handle_coordinator_update(self) -> None:
        """Handle coordinator updates.

        The coordinator only calls this when some sensor changed; write state
        only if this sensor's data or the availability changed.
        """
        self._sync_from_coordinator()
        snapshot = self._snapshot()
        if snapshot == self._last_written:
            return
        self._last_written = snapshot
        self.async_write_ha_state()

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
