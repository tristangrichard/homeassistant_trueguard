"""Interfaces with Trueguard/Woonveilig alarm control panel."""
from __future__ import annotations

from datetime import timedelta
import logging

import homeassistant.components.alarm_control_panel as alarm
from homeassistant.components.alarm_control_panel import (
    AlarmControlPanelEntity,
    AlarmControlPanelEntityFeature,
    AlarmControlPanelState,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from . import (
    CONF_REPORT_SERVER_CODES,
    CONF_REPORT_SERVER_ENABLED,
    CONF_REPORT_SERVER_PORT,
    DOMAIN,
    EGARDIA_DEVICE,
    EGARDIA_SENSOR_COORDINATOR,
    REPORT_SERVER_CODES_IGNORE,
    device_key,
)
from .api import TrueguardError
from .reports import action_for

_LOGGER = logging.getLogger(__name__)

# A reported alarm shows as triggered at most this long without a restore,
# arm, or disarm.
TRIGGERED_MAX = timedelta(hours=1)

STATES = {
    "ARM": AlarmControlPanelState.ARMED_AWAY,
    "DAY HOME": AlarmControlPanelState.ARMED_HOME,
    "DISARM": AlarmControlPanelState.DISARMED,
    "ARMHOME": AlarmControlPanelState.ARMED_HOME,
    "HOME": AlarmControlPanelState.ARMED_HOME,
    "NIGHT HOME": AlarmControlPanelState.ARMED_NIGHT,
    "TRIGGERED": AlarmControlPanelState.TRIGGERED,
}

# Arm/disarm reports (see reports.action_for) to alarm states.
REPORT_STATES = {
    "disarm": AlarmControlPanelState.DISARMED,
    "arm_away": AlarmControlPanelState.ARMED_AWAY,
    "arm_home": AlarmControlPanelState.ARMED_HOME,
}


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Set up the Trueguard Alarm Control Panel platform."""
    if discovery_info is None:
        return

    coordinator = hass.data.get(EGARDIA_SENSOR_COORDINATOR)
    if coordinator is None:
        return

    device = EgardiaAlarm(
        coordinator,
        discovery_info["name"],
        hass.data[EGARDIA_DEVICE],
        discovery_info[CONF_REPORT_SERVER_ENABLED],
        discovery_info.get(CONF_REPORT_SERVER_CODES),
        discovery_info[CONF_REPORT_SERVER_PORT],
        unique_id=f"trueguard_{device_key(discovery_info)}",
    )

    async_add_entities([device], True)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Trueguard alarm from a config entry."""
    conf = hass.data[DOMAIN][entry.entry_id]["conf"]
    entry_data = hass.data[DOMAIN][entry.entry_id]
    device = EgardiaAlarm(
        entry_data[EGARDIA_SENSOR_COORDINATOR],
        conf.get(CONF_NAME, "Trueguard"),
        entry_data[EGARDIA_DEVICE],
        conf.get(CONF_REPORT_SERVER_ENABLED, False),
        conf.get(CONF_REPORT_SERVER_CODES),
        conf.get(CONF_REPORT_SERVER_PORT, 52010),
        unique_id=f"trueguard_{device_key(conf)}",
    )
    async_add_entities([device], False)


class EgardiaAlarm(CoordinatorEntity, alarm.AlarmControlPanelEntity):
    """Representation of a Trueguard alarm."""

    _attr_alarm_state: AlarmControlPanelState | None = None
    _attr_code_arm_required = False
    _attr_supported_features = (
        AlarmControlPanelEntityFeature.ARM_HOME
        | AlarmControlPanelEntityFeature.ARM_AWAY
    )

    def __init__(
        self,
        coordinator,
        name,
        egardiasystem,
        rs_enabled=False,
        rs_codes=None,
        rs_port=52010,
        unique_id=None,
    ):
        """Initialize the Egardia alarm."""
        super().__init__(coordinator)
        self._attr_name = name
        self._attr_unique_id = unique_id
        self._egardiasystem = egardiasystem
        self._rs_enabled = rs_enabled
        self._rs_codes = rs_codes or {}
        self._rs_port = rs_port
        self._last_written = None
        self._polled_state: AlarmControlPanelState | None = None
        self._triggered: dict | None = None
        self._last_alarm: dict | None = None
        self._cancel_expiry = None
        self._apply_panel_state((self.coordinator.data or {}).get("state"))

    async def async_added_to_hass(self) -> None:
        """Listen for reports pushed by the panel."""
        await super().async_added_to_hass()
        # Home Assistant writes the initial state when the entity is added.
        self._last_written = self._snapshot()
        if self._rs_enabled:
            self.async_on_remove(
                self.coordinator.async_add_report_listener(self._async_handle_report)
            )
        self.async_on_remove(self._cancel_expiry_timer)

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

        self._cancel_expiry = async_call_later(
            self.hass, TRIGGERED_MAX.total_seconds() + 1, _expire
        )

    @property
    def should_poll(self) -> bool:
        """Coordinator handles refreshes."""
        return False

    @property
    def device_info(self) -> DeviceInfo | None:
        """Return device information for device registry."""
        # Only config-entry entities may attach to a device (HA 2026.9+).
        if self.platform is None or self.platform.config_entry is None:
            return None
        return DeviceInfo(
            identifiers={(DOMAIN, self._egardiasystem.device_key)},
            name=self._attr_name,
            manufacturer="Trueguard / Woonveilig",
            model=self._egardiasystem.version,
        )

    @property
    def extra_state_attributes(self):
        """The newest alarm reported by the panel's push reports."""
        if self._last_alarm is None:
            return None
        return dict(self._last_alarm)

    # --- Push reports -------------------------------------------------------------

    @callback
    def _async_handle_report(self, report, zone_name: str | None) -> None:
        """Apply a report pushed by the panel (runs on the event loop)."""
        if report.format == "LEGACY":
            self._handle_legacy_status(report.legacy_status)
            return

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
                self._triggered = None
        elif action.kind in REPORT_STATES:
            self._triggered = None
            self._polled_state = REPORT_STATES[action.kind]
        else:
            return
        self._update_state()
        self._write_if_changed()

    def _handle_legacy_status(self, statuscode: str) -> None:
        """Old Egardia frames: map the status with the YAML report_server_codes."""
        status = self.lookupstatusfromcode(statuscode)
        new_state = STATES.get(status)
        if new_state is None:
            _LOGGER.debug("Report code %s not mapped", statuscode)
            return
        if new_state == AlarmControlPanelState.TRIGGERED:
            self._triggered = {
                "code": "",
                "zone": None,
                "burglary": True,
                "mode": self._polled_state,
                "until": dt_util.utcnow() + TRIGGERED_MAX,
            }
            self._start_expiry_timer()
        else:
            self._triggered = None
            self._polled_state = new_state
        self._update_state()
        self._write_if_changed()

    def lookupstatusfromcode(self, statuscode):
        """Look at the rs_codes and returns the status from the code."""
        status = next(
            (
                status_group.upper()
                for status_group, codes in self._rs_codes.items()
                for code in codes
                if statuscode == code
            ),
            "UNKNOWN",
        )
        return status

    # --- State -------------------------------------------------------------------------

    def _apply_panel_state(self, status):
        """Apply the alarm mode read from the panel."""
        if status is None:
            return
        if status.lower().strip() == REPORT_SERVER_CODES_IGNORE:
            return
        polled = STATES.get(status.upper())
        if (
            self._triggered is not None
            and polled is not None
            and polled != self._triggered["mode"]
        ):
            # Armed or disarmed since the alarm: the alarm is over.
            self._triggered = None
        self._polled_state = polled
        self._update_state()

    def _update_state(self) -> None:
        """Show triggered while a reported alarm is active, else the panel mode."""
        if self._triggered is not None and dt_util.utcnow() >= self._triggered["until"]:
            self._triggered = None
        if self._triggered is not None:
            self._attr_alarm_state = AlarmControlPanelState.TRIGGERED
        else:
            self._attr_alarm_state = self._polled_state

    def _snapshot(self):
        return (self.available, self._attr_alarm_state, tuple((self._last_alarm or {}).items()))

    def _write_if_changed(self) -> None:
        """Write state only when the alarm state or availability changed."""
        snapshot = self._snapshot()
        if snapshot == self._last_written:
            return
        self._last_written = snapshot
        self.async_write_ha_state()

    def _handle_coordinator_update(self) -> None:
        """Handle coordinator updates."""
        self._apply_panel_state((self.coordinator.data or {}).get("state"))
        self._write_if_changed()

    @property
    def icon(self) -> str:
        """Return icon based on alarm state."""
        state = self._attr_alarm_state
        if state == AlarmControlPanelState.DISARMED:
            return "mdi:shield-off"
        if state in (AlarmControlPanelState.ARMED_HOME, AlarmControlPanelState.ARMED_NIGHT):
            return "mdi:shield-home"
        if state == AlarmControlPanelState.ARMED_AWAY:
            return "mdi:shield-lock"
        if state == AlarmControlPanelState.TRIGGERED:
            return "mdi:bell-ring"
        return "mdi:shield"

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
        await self._async_send(self._egardiasystem.async_disarm, "disarm")

    async def async_alarm_arm_home(self, code: str | None = None) -> None:
        """Send arm home command."""
        await self._async_send(self._egardiasystem.async_arm_home, "arm home")

    async def async_alarm_arm_away(self, code: str | None = None) -> None:
        """Send arm away command."""
        await self._async_send(self._egardiasystem.async_arm_away, "arm away")
