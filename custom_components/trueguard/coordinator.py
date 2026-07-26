"""Data coordinator for Trueguard sensors."""
from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

_LOGGER = logging.getLogger(__name__)


class TrueguardSensorCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinate shared refreshes of Trueguard panel state and sensors."""

    def __init__(self, hass: HomeAssistant, egardia_system, interval_seconds: int = 1) -> None:
        """Initialize coordinator."""
        self._egardia_system = egardia_system
        super().__init__(
            hass,
            _LOGGER,
            name="trueguard_sensor_refresh",
            update_interval=timedelta(seconds=interval_seconds),
        )

    @property
    def egardia_system(self):
        """Expose underlying Egardia system."""
        return self._egardia_system

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch latest alarm state and sensors from panel."""
        try:
            def _fetch_panel_data():
                return {
                    "state": self._egardia_system.getstate(),
                    "sensors": self._egardia_system.getsensors() or {},
                }

            return await self.hass.async_add_executor_job(_fetch_panel_data)
        except Exception as err:
            raise UpdateFailed(f"Failed to refresh Trueguard panel data: {err}") from err
