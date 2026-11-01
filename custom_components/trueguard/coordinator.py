"""Data coordinator for Trueguard sensors."""
from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import TrueguardClient, TrueguardError, UnauthorizedError, VersionError

_LOGGER = logging.getLogger(__name__)


class TrueguardSensorCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinate shared refreshes of Trueguard panel state and sensors.

    Each poll reads the alarm state and the sensor list at the same time.
    Entities are only notified when something actually changed
    (always_update=False).
    """

    def __init__(
        self,
        hass: HomeAssistant,
        client: TrueguardClient,
        config_entry: ConfigEntry | None,
        interval_seconds: float = 5,
    ) -> None:
        """Initialize coordinator."""
        self._client = client
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name="trueguard_sensor_refresh",
            update_interval=timedelta(seconds=interval_seconds),
            always_update=False,
        )

    def set_interval(self, interval_seconds: float) -> None:
        """Change the polling interval; takes effect from the next poll."""
        _LOGGER.debug("Polling Trueguard panel every %s s", interval_seconds)
        self.update_interval = timedelta(seconds=interval_seconds)

    @property
    def client(self) -> TrueguardClient:
        """The panel client."""
        return self._client

    # Kept for entities written against the old name.
    egardia_system = client

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch latest alarm state and sensors from panel."""
        try:
            return await self._client.async_get_data()
        except UnauthorizedError as err:
            if self.config_entry is not None:
                # Starts the reauthentication flow.
                raise ConfigEntryAuthFailed("Wrong username or password") from err
            raise UpdateFailed(f"Trueguard panel rejected the login: {err}") from err
        except VersionError as err:
            if self.config_entry is not None:
                raise ConfigEntryError(str(err)) from err
            raise UpdateFailed(str(err)) from err
        except TrueguardError as err:
            raise UpdateFailed(f"Failed to refresh Trueguard panel data: {err}") from err
