"""Data coordinator for Trueguard sensors."""
from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import TrueguardClient, TrueguardError, UnauthorizedError, VersionError
from .receiver import PushReport
from .reports import code_text

# Fired on the event bus for every report the panel pushes.
EVENT_TRUEGUARD_REPORT = "trueguard_report"

_LOGGER = logging.getLogger(__name__)


class TrueguardCoordinator(DataUpdateCoordinator[dict[str, Any]]):
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
        self._report_listeners: list[Callable[[PushReport, str | None], None]] = []
        # Zone ids and names the entities were created for.
        self._zones: dict[Any, Any] | None = None
        # A changed zone list must be seen on two polls in a row before
        # reloading, so a one-off glitch in the panel's answer is ignored.
        self._pending_zones: dict[Any, Any] | None = None
        self._reload_scheduled = False
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

    def zone_name(self, zone: int | None) -> str | None:
        """Name of the sensor in a zone number, if known."""
        if zone is None:
            return None
        for sensor in (self.data or {}).get("sensors", {}).values():
            try:
                if int(sensor.get("zone")) == zone:
                    return sensor.get("name")
            except (TypeError, ValueError):
                continue
        return None

    @callback
    def async_add_report_listener(
        self, listener: Callable[[PushReport, str | None], None]
    ) -> CALLBACK_TYPE:
        """Call listener(report, zone_name) for each pushed report."""
        self._report_listeners.append(listener)

        def _remove() -> None:
            if listener in self._report_listeners:
                self._report_listeners.remove(listener)

        return _remove

    @callback
    def async_handle_report(self, report: PushReport) -> None:
        """Handle a report pushed by the panel (runs on the event loop)."""
        zone_name = self.zone_name(report.zone)
        _LOGGER.debug("Trueguard report %s (zone %s %s)", report, report.zone, zone_name)
        self.hass.bus.async_fire(
            EVENT_TRUEGUARD_REPORT,
            {
                "format": report.format,
                "account": report.account,
                "cid": report.cid,
                "qualifier": report.qualifier,
                "code": report.code,
                "description": code_text(report.code),
                "sia_code": report.sia_code,
                "group": report.group,
                "zone": report.zone,
                "zone_name": zone_name,
                "config_entry_id": self.config_entry.entry_id if self.config_entry else None,
            },
        )
        for listener in list(self._report_listeners):
            listener(report, zone_name)
        if report.format != "NULL":
            # Read the panel at once so sensors and the mode catch up.
            self.hass.async_create_task(self.async_request_refresh())

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch latest alarm state and sensors from panel."""
        try:
            data = await self._client.async_get_data()
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
        self._check_zones(data)
        return data

    def _check_zones(self, data: dict[str, Any]) -> None:
        """Reload when zones were added, removed, or renamed on the panel.

        Entities are created for the zones present at startup, so a changed
        zone list needs a reload to add, remove, or rename them. The change
        must be seen on two polls in a row.
        """
        zones = {
            sensor_id: sensor.get("name")
            for sensor_id, sensor in data.get("sensors", {}).items()
        }
        if self._zones is None:
            self._zones = zones
            return
        if zones == self._zones:
            self._pending_zones = None
            return
        if self._reload_scheduled or self.config_entry is None:
            return
        if zones != self._pending_zones:
            self._pending_zones = zones
            return
        added = sorted(map(str, zones.keys() - self._zones.keys()))
        removed = sorted(map(str, self._zones.keys() - zones.keys()))
        renamed = sorted(
            str(zone_id)
            for zone_id in zones.keys() & self._zones.keys()
            if zones[zone_id] != self._zones[zone_id]
        )
        _LOGGER.info(
            "Trueguard zones changed on the panel (added %s, removed %s, renamed %s); "
            "reloading",
            added or "none",
            removed or "none",
            renamed or "none",
        )
        self._reload_scheduled = True
        self.hass.config_entries.async_schedule_reload(self.config_entry.entry_id)
