"""Interfaces with Trueguard/Woonveilig alarm control panel."""
from __future__ import annotations

from datetime import timedelta
import logging

import requests

import homeassistant.components.alarm_control_panel as alarm
from homeassistant.components.alarm_control_panel import (
    AlarmControlPanelEntity,
    AlarmControlPanelEntityFeature,
    AlarmControlPanelState,
)
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from . import (
    CONF_REPORT_SERVER_CODES,
    CONF_REPORT_SERVER_ENABLED,
    CONF_REPORT_SERVER_PORT,
    EGARDIA_DEVICE,
    EGARDIA_SERVER,
    REPORT_SERVER_CODES_IGNORE,
)

SCAN_INTERVAL = timedelta(seconds=1)

_LOGGER = logging.getLogger(__name__)

STATES = {
    "ARM": AlarmControlPanelState.ARMED_AWAY,
    "DAY HOME": AlarmControlPanelState.ARMED_HOME,
    "DISARM": AlarmControlPanelState.DISARMED,
    "ARMHOME": AlarmControlPanelState.ARMED_HOME,
    "HOME": AlarmControlPanelState.ARMED_HOME,
    "NIGHT HOME": AlarmControlPanelState.ARMED_NIGHT,
    "TRIGGERED": AlarmControlPanelState.TRIGGERED,
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

    host = discovery_info.get(CONF_HOST, "unknown")
    port = discovery_info.get(CONF_PORT, "")

    device = EgardiaAlarm(
        discovery_info["name"],
        hass.data[EGARDIA_DEVICE],
        discovery_info[CONF_REPORT_SERVER_ENABLED],
        discovery_info.get(CONF_REPORT_SERVER_CODES),
        discovery_info[CONF_REPORT_SERVER_PORT],
        unique_id=f"trueguard_{host}_{port}",
    )

    async_add_entities([device], True)


class EgardiaAlarm(alarm.AlarmControlPanelEntity):
    """Representation of a Trueguard alarm."""

    _attr_alarm_state: AlarmControlPanelState | None = None
    _attr_code_arm_required = False
    _attr_supported_features = (
        AlarmControlPanelEntityFeature.ARM_HOME
        | AlarmControlPanelEntityFeature.ARM_AWAY
    )

    def __init__(
        self,
        name,
        egardiasystem,
        rs_enabled=False,
        rs_codes=None,
        rs_port=52010,
        unique_id=None,
    ):
        """Initialize the Egardia alarm."""
        self._attr_name = name
        self._attr_unique_id = unique_id
        self._egardiasystem = egardiasystem
        self._rs_enabled = rs_enabled
        self._rs_codes = rs_codes
        self._rs_port = rs_port

    async def async_added_to_hass(self) -> None:
        """Add Egardiaserver callback if enabled."""
        if self._rs_enabled:
            _LOGGER.debug("Registering callback to Egardiaserver")
            self.hass.data[EGARDIA_SERVER].register_callback(self.handle_status_event)

    @property
    def should_poll(self) -> bool:
        """Poll if no report server is enabled."""
        if not self._rs_enabled:
            return True
        return False

    def handle_status_event(self, event):
        """Handle the Trueguard system status event."""
        if (statuscode := event.get("status")) is not None:
            status = self.lookupstatusfromcode(statuscode)
            self.parsestatus(status)
            self.schedule_update_ha_state()

    def lookupstatusfromcode(self, statuscode):
        """Look at the rs_codes and returns the status from the code."""
        status = next(
            (
                status_group.upper()
                for status_group, codes in self._rs_codes.items()
                for code in codes
                if statuscode == code
            ),
            "UNKNOWN Tristan",
        )
        return status

    def parsestatus(self, status):
        """Parse the status."""
        _LOGGER.debug("Parsing status %s", status)
        # Ignore the statuscode if it is IGNORE
        if status.lower().strip() != REPORT_SERVER_CODES_IGNORE:
            _LOGGER.debug("Not ignoring status %s", status)
            newstatus = STATES.get(status.upper())
            _LOGGER.debug("newstatus %s", newstatus)
            self._attr_alarm_state = newstatus
        else:
            _LOGGER.error("Ignoring status")

    def update(self) -> None:
        """Update the alarm status."""
        status = self._egardiasystem.getstate()
        self.parsestatus(status)

    async def async_alarm_disarm(self, code: str | None = None) -> None:
        """Send disarm command."""
        try:
            await self.hass.async_add_executor_job(
                self._egardiasystem.alarm_disarm
            )
        except requests.exceptions.RequestException as err:
            _LOGGER.error(
                "Trueguard device exception occurred when sending disarm command: %s",
                err,
            )

    async def async_alarm_arm_home(self, code: str | None = None) -> None:
        """Send arm home command."""
        try:
            await self.hass.async_add_executor_job(
                self._egardiasystem.alarm_arm_home
            )
        except requests.exceptions.RequestException as err:
            _LOGGER.error(
                "Trueguard device exception occurred when sending arm home command: %s",
                err,
            )

    async def async_alarm_arm_away(self, code: str | None = None) -> None:
        """Send arm away command."""
        try:
            await self.hass.async_add_executor_job(
                self._egardiasystem.alarm_arm_away
            )
        except requests.exceptions.RequestException as err:
            _LOGGER.error(
                "Trueguard device exception occurred when sending arm away command: %s",
                err,
            )
