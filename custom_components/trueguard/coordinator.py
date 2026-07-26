"""Data coordinator for Trueguard sensors."""
from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from . import DOMAIN

_LOGGER = logging.getLogger(__name__)


class TrueguardSensorCoordinator(DataUpdateCoordinator[dict[str, dict[str, Any]]]):
    """Coordinate shared refreshes of Trueguard panel sensors."""

    def __init__(self, hass: HomeAssistant, egardia_system, interval_seconds: int = 2) -> None:
        """Initialize coordinator."""
        self._egardia_system = egardia_system
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_sensor_refresh",
            update_interval=timedelta(seconds=interval_seconds),
        )

    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        """Fetch latest sensors from panel."""
        try:
            sensors = await self.hass.async_add_executor_job(self._egardia_system.getsensors)
            return sensors or {}
        except Exception as err:
            raise UpdateFailed(f"Failed to refresh Trueguard sensors: {err}") from err
