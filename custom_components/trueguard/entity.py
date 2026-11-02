"""Shared base for Trueguard entities."""
from __future__ import annotations

from typing import Any

from homeassistant.const import CONF_NAME
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import DEFAULT_NAME, DOMAIN


class TrueguardEntity(CoordinatorEntity):
    """Coordinator entity that only writes state when its value changed.

    Subclasses implement _refresh() to read their value from the coordinator
    data, and _value() to return what is shown (state and attributes).
    """

    _attr_should_poll = False

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._client = coordinator.client
        self._last_written: Any = None
        self._refresh()

    @property
    def _data(self) -> dict[str, Any]:
        return self.coordinator.data or {}

    def _refresh(self) -> None:
        """Read the latest value from coordinator data."""

    def _value(self) -> Any:
        """Return everything that is shown, to detect changes."""
        return None

    def _snapshot(self) -> Any:
        return (self.available, getattr(self, "_attr_name", None), self._value())

    def _set_name(self, name: str | None) -> None:
        """Follow a zone renamed on the panel, keeping the entity ID."""
        if not name or name == getattr(self, "_attr_name", None):
            return
        self._attr_name = name
        if self.registry_entry is not None:
            er.async_get(self.hass).async_update_entity(
                self.entity_id, original_name=name
            )

    async def async_added_to_hass(self) -> None:
        """Remember the state Home Assistant writes when the entity is added."""
        await super().async_added_to_hass()
        self._last_written = self._snapshot()

    def _handle_coordinator_update(self) -> None:
        self._refresh()
        snapshot = self._snapshot()
        if snapshot == self._last_written:
            return
        self._last_written = snapshot
        self.async_write_ha_state()

    @property
    def device_info(self) -> DeviceInfo:
        """Attach to the panel device, named after the integration entry."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._client.device_key)},
            name=self.coordinator.config_entry.data.get(CONF_NAME, DEFAULT_NAME),
            manufacturer="Trueguard",
            model=self._client.version,
        )
