"""Bypass switches for Trueguard zones."""
from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import DATA_COORDINATOR, DOMAIN, zone_unique_id
from .api import TrueguardError
from .entity import TrueguardEntity
from .parser import has_open_state, zone_bypassed


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create a bypass switch for each zone that can open or trigger."""
    coordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    async_add_entities(
        TrueguardBypassSwitch(coordinator, sensor_id, sensor)
        for sensor_id, sensor in (coordinator.data or {}).get("sensors", {}).items()
        if has_open_state(sensor)
    )


class TrueguardBypassSwitch(TrueguardEntity, SwitchEntity):
    """Bypass a zone, like the Bypass button on the panel's status page.

    On means bypassed: the zone is left out when the panel is armed.
    Disabled by default; enable it for the zones you want to bypass from Home
    Assistant.
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator, sensor_id, sensor) -> None:
        self._sensor_id = sensor_id
        self._attr_name = f"{sensor.get('name') or sensor_id} bypass"
        super().__init__(coordinator)
        self._attr_unique_id = zone_unique_id(
            self._client.device_key, sensor_id, "_bypass"
        )

    def _refresh(self) -> None:
        sensor = self._data.get("sensors", {}).get(self._sensor_id) or {}
        if name := sensor.get("name"):
            self._set_name(f"{name} bypass")
        self._attr_is_on = zone_bypassed(sensor) if sensor else None
        self._attr_icon = "mdi:shield-off-outline" if self._attr_is_on else "mdi:shield-check"

    def _value(self):
        return (self._attr_is_on, self._attr_icon)

    async def _async_set(self, bypass: bool) -> None:
        try:
            await self._client.async_set_bypass(self._sensor_id, bypass)
        except TrueguardError as err:
            raise HomeAssistantError(
                f"Trueguard panel did not accept the "
                f"{'bypass' if bypass else 'end bypass'} command: {err}"
            ) from err
        await self.coordinator.async_request_refresh()

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Bypass the zone."""
        await self._async_set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """End the zone's bypass."""
        await self._async_set(False)
