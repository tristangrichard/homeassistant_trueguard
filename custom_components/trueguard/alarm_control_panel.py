"""Alarm control panel for Trueguard panels."""
from __future__ import annotations

from datetime import timedelta
import logging

from homeassistant.components.alarm_control_panel import (
    AlarmControlPanelEntity,
    AlarmControlPanelEntityFeature,
    AlarmControlPanelState,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.restore_state import RestoredExtraData, RestoreEntity
from homeassistant.util import dt as dt_util

from . import (
    CONF_REPORT_SERVER_ENABLED,
    DATA_COORDINATOR,
    DEFAULT_NAME,
    DOMAIN,
    device_key,
)
from .api import TrueguardError
from .entity import TrueguardEntity
from .reports import action_for

_LOGGER = logging.getLogger(__name__)

# A reported alarm shows as triggered at most this long without a restore,
# arm, or disarm.
TRIGGERED_MAX = timedelta(hours=1)

ISSUE_TRACKER = "https://github.com/tristangrichard/homeassistant_trueguard/issues"

# Panel modes (see parser.parse_panel_state) to alarm states. Modified from Home
# Assistant's Egardia integration (Apache-2.0), see THIRD_PARTY_NOTICES.
STATES = {
    "ARM": AlarmControlPanelState.ARMED_AWAY,
    "DAY HOME": AlarmControlPanelState.ARMED_HOME,
    "DISARM": AlarmControlPanelState.DISARMED,
    "ARMHOME": AlarmControlPanelState.ARMED_HOME,
    "HOME": AlarmControlPanelState.ARMED_HOME,
    "NIGHT HOME": AlarmControlPanelState.ARMED_NIGHT,
    "TRIGGERED": AlarmControlPanelState.TRIGGERED,
    "ARMING": AlarmControlPanelState.ARMING,
    "PENDING": AlarmControlPanelState.PENDING,
}

# Passing states: shown while the panel reports them, but what counts as the
# panel's armed or disarmed mode is the last other state.
PASSING_STATES = (
    AlarmControlPanelState.TRIGGERED,
    AlarmControlPanelState.ARMING,
    AlarmControlPanelState.PENDING,
)

# Arm/disarm reports (see reports.action_for) to alarm states.
REPORT_STATES = {
    "disarm": AlarmControlPanelState.DISARMED,
    "arm_away": AlarmControlPanelState.ARMED_AWAY,
    "arm_home": AlarmControlPanelState.ARMED_HOME,
}

ICONS = {
    AlarmControlPanelState.DISARMED: "mdi:shield-off",
    AlarmControlPanelState.ARMED_HOME: "mdi:shield-home",
    AlarmControlPanelState.ARMED_NIGHT: "mdi:shield-home",
    AlarmControlPanelState.ARMED_AWAY: "mdi:shield-lock",
    AlarmControlPanelState.TRIGGERED: "mdi:bell-ring",
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Trueguard alarm from a config entry."""
    entry_data = hass.data[DOMAIN][entry.entry_id]
    conf = entry_data["conf"]
    async_add_entities(
        [
            TrueguardAlarm(
                entry_data[DATA_COORDINATOR],
                conf.get(CONF_NAME, DEFAULT_NAME),
                conf.get(CONF_REPORT_SERVER_ENABLED, False),
                unique_id=f"trueguard_{device_key(conf)}",
            )
        ]
    )


class TrueguardAlarm(TrueguardEntity, AlarmControlPanelEntity, RestoreEntity):
    """The panel's alarm: polled mode, plus pushed alarm reports.

    A reported alarm is only known here, so it is restored after a restart.
    """

    _attr_code_arm_required = False
    _attr_supported_features = (
        AlarmControlPanelEntityFeature.ARM_HOME
        | AlarmControlPanelEntityFeature.ARM_AWAY
    )

    def __init__(self, coordinator, name: str, reports_enabled: bool, unique_id: str):
        self._attr_name = name
        self._attr_unique_id = unique_id
        self._reports_enabled = reports_enabled
        self._attr_alarm_state: AlarmControlPanelState | None = None
        self._polled_state: AlarmControlPanelState | None = None
        self._passing_state: AlarmControlPanelState | None = None
        self._triggered: dict | None = None
        self._last_alarm: dict | None = None
        self._cancel_expiry = None
        self._warned_modes: set[str] = set()
        super().__init__(coordinator)

    async def async_added_to_hass(self) -> None:
        """Restore a reported alarm and listen for reports pushed by the panel."""
        await self._async_restore()
        await super().async_added_to_hass()
        if self._reports_enabled:
            self.async_on_remove(
                self.coordinator.async_add_report_listener(self._async_handle_report)
            )
        self.async_on_remove(self._cancel_expiry_timer)

    async def _async_restore(self) -> None:
        """Bring back a reported alarm that is still active."""
        if (stored := await self.async_get_last_extra_data()) is None:
            return
        stored = stored.as_dict()
        self._last_alarm = stored.get("last_alarm")
        triggered = stored.get("triggered")
        if not triggered:
            return
        until = dt_util.parse_datetime(triggered.get("until") or "")
        if until is None or dt_util.utcnow() >= until:
            return
        if self._polled_state not in (None, triggered.get("mode")):
            # Armed or disarmed since the alarm: the alarm is over.
            return
        self._triggered = {**triggered, "until": until}
        self._start_expiry_timer()
        self._update_state()

    @property
    def extra_restore_state_data(self) -> RestoredExtraData:
        """What is stored across restarts."""
        triggered = self._triggered and {
            **self._triggered,
            "until": self._triggered["until"].isoformat(),
        }
        return RestoredExtraData({"triggered": triggered, "last_alarm": self._last_alarm})

    @property
    def icon(self) -> str:
        """Icon for the current alarm state."""
        return ICONS.get(self._attr_alarm_state, "mdi:shield")

    @property
    def extra_state_attributes(self):
        """The panel's own mode text, and the newest reported alarm."""
        attributes = {"panel_mode": self._panel_mode()}
        if self._last_alarm:
            attributes.update(self._last_alarm)
        return attributes

    def _panel_mode(self) -> str | None:
        """The panel's mode text as it shows it, e.g. "Deltilkobling 2"."""
        panel = self._data.get("panel", {})
        return panel.get("mode_a1", panel.get("mode_st"))

    # --- Polled state ----------------------------------------------------------------

    def _refresh(self) -> None:
        """Apply the alarm mode read from the panel."""
        mode = str(self._data.get("state") or "").upper()
        polled = STATES.get(mode)
        if polled is None:
            raw = self._panel_mode()
            if mode and raw not in self._warned_modes:
                self._warned_modes.add(raw)
                _LOGGER.warning(
                    "Trueguard panel reports a mode that is not recognised: %r. "
                    "The alarm keeps its last known state. Please report this "
                    "mode text at %s",
                    raw,
                    ISSUE_TRACKER,
                )
            return
        if polled in PASSING_STATES:
            self._passing_state = polled
        else:
            self._passing_state = None
            if self._triggered is not None and polled != self._triggered["mode"]:
                # Armed or disarmed since the alarm: the alarm is over.
                self._clear_triggered()
            self._polled_state = polled
        self._update_state()

    def _value(self):
        return (
            self._attr_alarm_state,
            self._panel_mode(),
            tuple((self._last_alarm or {}).items()),
        )

    def _update_state(self) -> None:
        """Show triggered while a reported alarm is active, else the panel's state."""
        if self._triggered is not None and dt_util.utcnow() >= self._triggered["until"]:
            self._clear_triggered()
        if self._triggered is not None:
            self._attr_alarm_state = AlarmControlPanelState.TRIGGERED
        else:
            self._attr_alarm_state = self._passing_state or self._polled_state

    def _write_if_changed(self) -> None:
        snapshot = self._snapshot()
        if snapshot == self._last_written:
            return
        self._last_written = snapshot
        self.async_write_ha_state()

    # --- Push reports ----------------------------------------------------------------

    @callback
    def _async_handle_report(self, report, zone_name: str | None) -> None:
        """Apply a report pushed by the panel (runs on the event loop)."""
        action = action_for(report.qualifier, report.code, report.zone)
        if action.kind == "alarm":
            self._last_alarm = {
                "alarm_cause": action.description,
                "alarm_code": report.cid,
                "alarm_zone": report.zone,
                "alarm_zone_name": zone_name,
                "alarm_time": dt_util.utcnow().isoformat(),
            }
            self._triggered = {
                "code": action.code,
                "zone": action.zone,
                "burglary": action.burglary,
                "mode": self._polled_state,
                "until": dt_util.utcnow() + TRIGGERED_MAX,
            }
            self._start_expiry_timer()
        elif action.kind == "restore":
            if (
                self._triggered is not None
                and not self._triggered["burglary"]
                and self._triggered["code"] == action.code
                and self._triggered["zone"] == action.zone
            ):
                self._clear_triggered()
        elif action.kind in REPORT_STATES:
            self._clear_triggered()
            self._passing_state = None
            self._polled_state = REPORT_STATES[action.kind]
        else:
            return
        self._update_state()
        self._write_if_changed()

    def _clear_triggered(self) -> None:
        self._triggered = None
        self._cancel_expiry_timer()

    def _cancel_expiry_timer(self) -> None:
        if self._cancel_expiry is not None:
            self._cancel_expiry()
            self._cancel_expiry = None

    def _start_expiry_timer(self) -> None:
        """Clear a reported alarm after TRIGGERED_MAX even if nothing else changes."""
        self._cancel_expiry_timer()

        @callback
        def _expire(_now) -> None:
            self._cancel_expiry = None
            self._update_state()
            self._write_if_changed()

        remaining = self._triggered["until"] - dt_util.utcnow()
        self._cancel_expiry = async_call_later(
            self.hass, remaining.total_seconds() + 1, _expire
        )

    # --- Commands ----------------------------------------------------------------------

    async def _async_send(self, command, label: str) -> None:
        """Send an arm/disarm command, then read the new state at once."""
        try:
            await command()
        except TrueguardError as err:
            raise HomeAssistantError(
                f"Trueguard panel did not accept the {label} command: {err}"
            ) from err
        await self.coordinator.async_request_refresh()

    async def async_alarm_disarm(self, code: str | None = None) -> None:
        """Send disarm command."""
        await self._async_send(self._client.async_disarm, "disarm")

    async def async_alarm_arm_home(self, code: str | None = None) -> None:
        """Send arm home command."""
        await self._async_send(self._client.async_arm_home, "arm home")

    async def async_alarm_arm_away(self, code: str | None = None) -> None:
        """Send arm away command."""
        await self._async_send(self._client.async_arm_away, "arm away")
