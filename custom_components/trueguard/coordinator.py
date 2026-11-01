"""Data coordinator for Trueguard sensors."""
from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .depend.egardiadevice import UnauthorizedError

_LOGGER = logging.getLogger(__name__)


class TrueguardSensorCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinate shared refreshes of Trueguard panel state and sensors."""

    def __init__(
        self,
        hass: HomeAssistant,
        egardia_system,
        config_entry: ConfigEntry | None,
        interval_seconds: float = 5,
    ) -> None:
        """Initialize coordinator."""
        self._egardia_system = egardia_system
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name="trueguard_sensor_refresh",
            update_interval=timedelta(seconds=interval_seconds),
        )

    def set_interval(self, interval_seconds: float) -> None:
        """Change the polling interval; takes effect from the next poll."""
        _LOGGER.debug("Polling Trueguard panel every %s s", interval_seconds)
        self.update_interval = timedelta(seconds=interval_seconds)

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
        except UnauthorizedError as err:
            if self.config_entry is not None:
                # Starts the reauthentication flow.
                raise ConfigEntryAuthFailed("Wrong username or password") from err
            raise UpdateFailed(f"Trueguard panel rejected the login: {err}") from err
        except Exception as err:
            raise UpdateFailed(f"Failed to refresh Trueguard panel data: {err}") from err
