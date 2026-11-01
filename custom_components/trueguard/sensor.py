"""Signal-strength sensors for the Trueguard panel and its zones."""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import DOMAIN, EGARDIA_SENSOR_COORDINATOR
from .entity import TrueguardEntity
from .parser import signal_level


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up signal sensors from a config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id][EGARDIA_SENSOR_COORDINATOR]
    data = coordinator.data or {}
    entities: list[SensorEntity] = []
    if "sig_gsm" in data.get("panel", {}):
        entities.append(TrueguardGsmSignal(coordinator))
    entities.extend(
        TrueguardZoneSignal(coordinator, sensor_id, sensor.get("name") or str(sensor_id))
        for sensor_id, sensor in data.get("sensors", {}).items()
        if "rssi" in sensor
    )
    async_add_entities(entities, False)


class TrueguardGsmSignal(TrueguardEntity, SensorEntity):
    """GSM signal level reported by the panel (needs a SIM card)."""

    _attr_name = "Trueguard GSM signal"
    _attr_icon = "mdi:signal-cellular-2"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"trueguard_{self._client.device_key}_panel_sig_gsm"

    def _refresh(self) -> None:
        self._attr_native_value = signal_level(self._data.get("panel", {}).get("sig_gsm"))

    def _value(self):
        return self._attr_native_value


class TrueguardZoneSignal(TrueguardEntity, SensorEntity):
    """Radio signal level of one zone (the number in e.g. "Stærk, 7").

    Disabled by default; the same value is also an attribute of the zone's
    main sensor.
    """

    _attr_icon = "mdi:signal"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator, sensor_id, zone_name) -> None:
        self._sensor_id = sensor_id
        self._attr_name = f"{zone_name} signal"
        super().__init__(coordinator)
        self._attr_unique_id = f"trueguard_{sensor_id}_signal"

    def _refresh(self) -> None:
        sensor = self._data.get("sensors", {}).get(self._sensor_id) or {}
        self._attr_native_value = signal_level(sensor.get("rssi"))
        self._attr_extra_state_attributes = {"signal_text": sensor.get("rssi")}

    def _value(self):
        return (self._attr_native_value, tuple(self._attr_extra_state_attributes.items()))
