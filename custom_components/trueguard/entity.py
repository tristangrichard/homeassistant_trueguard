"""Shared base for Trueguard entities."""
from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import DOMAIN


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
        return (self.available, self._value())

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
    def device_info(self) -> DeviceInfo | None:
        """Attach to the panel device (config-entry setups only, HA 2026.9+)."""
        if self.platform is None or self.platform.config_entry is None:
            return None
        return DeviceInfo(
            identifiers={(DOMAIN, self._client.device_key)},
            name="Trueguard",
            manufacturer="Trueguard",
            model=self._client.version,
        )
