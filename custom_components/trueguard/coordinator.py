"""Data coordinator for Trueguard sensors."""
from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
import logging
from time import monotonic
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import TrueguardClient, TrueguardError, UnauthorizedError, VersionError
from .parser import report_delivery
from .receiver import PushReport
from .reports import code_text

# Fired on the event bus for every report the panel pushes, and for every new
# row in its report list.
EVENT_TRUEGUARD_REPORT = "trueguard_report"

# PushReport.format of a row read from the panel's report list.
REPORT_LIST = "LIST"
# Rows read from the report list each time. The panel adds a row for every
# alarm, restore, and fault report; more new rows than this between two reads
# means the oldest of them are missed.
REPORT_ROWS = 20
# Seconds between reads of the report list when nothing else changed. It is
# also read at once whenever the alarm state, a zone, or a panel fault changes
# (see _report_signature); signal strength and other readings do not count.
REPORT_INTERVAL = 60
# A pushed report shows up in the report list too; within this many seconds,
# that row is not handled a second time.
PUSH_MATCH = 600

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
        # Zone ids the entities were created for.
        self._zones: set[Any] | None = None
        # A changed zone list must be seen on two polls in a row before
        # reloading, so a one-off glitch in the panel's answer is ignored.
        self._pending_zones: set[Any] | None = None
        self._reload_scheduled = False
        # Newest report list uid already handled; rows already in the list
        # when the integration starts are not handled.
        self._last_report_uid: int | None = None
        self._reports_read_at: float | None = None
        self._report_failures = 0
        self._report_signature: tuple | None = None
        # Delivery of the newest finished report (parser.report_delivery).
        self._report_delivery: dict[str, Any] | None = None
        # (monotonic time, (qualifier, code, zone)) of recent pushed reports.
        self._recent_pushes: list[tuple[float, tuple]] = []
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

    @property
    def last_report_uid(self) -> int | None:
        """Newest report list row handled, for diagnostics."""
        return self._last_report_uid

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
        if report.cid:
            self._recent_pushes.append((monotonic(), _report_key(report)))
        self._async_dispatch_report(report)
        if report.format != "NULL":
            # Read the panel at once so sensors and the mode catch up.
            self.hass.async_create_task(self.async_request_refresh())

    @callback
    def _async_dispatch_report(self, report: PushReport) -> None:
        """Fire the report event and tell the listeners."""
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
        await self._async_check_report_list(data)
        if self._report_delivery is not None:
            data["report_delivery"] = self._report_delivery
        return data

    async def _async_check_report_list(self, data: dict[str, Any]) -> None:
        """Handle new rows in the panel's report list like pushed reports.

        Read every REPORT_INTERVAL, and at once when the alarm state, a zone,
        or a panel fault changed, so an alarm shows up even without the push
        receiver. A panel that cannot answer is only asked again every
        REPORT_INTERVAL.
        """
        now = monotonic()
        signature = _report_signature(data)
        changed = (
            self._report_signature is not None and signature != self._report_signature
        )
        self._report_signature = signature
        due = (
            self._reports_read_at is None
            or now - self._reports_read_at >= REPORT_INTERVAL
        )
        if not due and not (changed and self._report_failures == 0):
            return
        self._reports_read_at = now
        try:
            reports = await self._client.async_get_reports(REPORT_ROWS)
        except TrueguardError as err:
            self._report_failures += 1
            _LOGGER.debug("Could not read the Trueguard report list: %s", err)
            return
        self._report_failures = 0
        self._report_delivery = report_delivery(reports)
        newest = reports[0]["uid"] if reports else 0
        if self._last_report_uid is None or newest < self._last_report_uid:
            # First read, or the panel's list was cleared.
            self._last_report_uid = newest
            return
        new = [row for row in reports if row["uid"] > self._last_report_uid]
        self._last_report_uid = newest
        for row in reversed(new):
            report = PushReport(
                format=REPORT_LIST,
                qualifier=row["qualifier"],
                code=row["code"],
                group=row["area"],
                zone=row["zone"],
                raw=row["message"],
            )
            if not self._take_recent_push(report):
                self._async_dispatch_report(report)

    def _take_recent_push(self, report: PushReport) -> bool:
        """Return True, once, if this report was pushed a short while ago."""
        cutoff = monotonic() - PUSH_MATCH
        self._recent_pushes = [
            (when, key) for when, key in self._recent_pushes if when >= cutoff
        ]
        key = _report_key(report)
        for index, (_, pushed) in enumerate(self._recent_pushes):
            if pushed == key:
                del self._recent_pushes[index]
                return True
        return False

    def _check_zones(self, data: dict[str, Any]) -> None:
        """Reload when zones were added or removed on the panel.

        Entities are created for the zones present at startup, so a changed
        zone list needs a reload. The change must be seen on two polls in a
        row. Entities follow a renamed zone by themselves.
        """
        zones = set(data.get("sensors", {}))
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
        added = sorted(map(str, zones - self._zones))
        removed = sorted(map(str, self._zones - zones))
        _LOGGER.info(
            "Trueguard zones changed on the panel (added %s, removed %s); reloading",
            added or "none",
            removed or "none",
        )
        self._reload_scheduled = True
        self.hass.config_entries.async_schedule_reload(self.config_entry.entry_id)


def _report_key(report: PushReport) -> tuple:
    """What identifies the same report pushed and in the report list."""
    return (report.qualifier, report.code, report.zone)


# Panel status flags whose change means the panel may have reported something.
_PANEL_REPORT_KEYS = (
    "mode_a1",
    "mode_a2",
    "mode_st",
    "battery_ok",
    "tamper_ok",
    "interference_ok",
    "ac_activation_ok",
)
# Zone fields whose change means the panel may have reported something.
_ZONE_REPORT_KEYS = (
    "st",
    "status",
    "cond",
    "cond_ok",
    "battery_ok",
    "tamper_ok",
    "bypass",
    "temp_bypass",
)


def _report_signature(data: dict[str, Any]) -> tuple:
    """The parts of the panel's answer that change when it reports something.

    Signal strength and other readings change on their own, so they are left
    out.
    """
    panel = data.get("panel", {})
    sensors = data.get("sensors", {})
    return (
        data.get("state"),
        tuple(panel.get(key) for key in _PANEL_REPORT_KEYS),
        tuple(
            (str(sensor_id), *(sensors[sensor_id].get(key) for key in _ZONE_REPORT_KEYS))
            for sensor_id in sorted(sensors, key=str)
        ),
    )
