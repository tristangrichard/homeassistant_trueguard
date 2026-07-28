"""Interfaces with Trueguard/Woonveilig alarm control panel."""
from __future__ import annotations

import logging

import requests

import homeassistant.components.alarm_control_panel as alarm
from homeassistant.components.alarm_control_panel import (
    AlarmControlPanelEntity,
    AlarmControlPanelEntityFeature,
    AlarmControlPanelState,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import (
    CONF_REPORT_SERVER_CODES,
    CONF_REPORT_SERVER_ENABLED,
    CONF_REPORT_SERVER_PORT,
    DOMAIN,
    EGARDIA_DEVICE,
    EGARDIA_SENSOR_COORDINATOR,
    EGARDIA_SERVER,
    REPORT_SERVER_CODES_IGNORE,
)

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
        unique_id=f"trueguard_{host}_{port}",
    )

    async_add_entities([device], True)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Trueguard alarm from a config entry."""
    conf = hass.data[DOMAIN][entry.entry_id]["conf"]
    host = conf.get(CONF_HOST, "unknown")
    port = conf.get(CONF_PORT, "")
    coordinator = hass.data[DOMAIN][entry.entry_id][EGARDIA_SENSOR_COORDINATOR]
    device = EgardiaAlarm(
        coordinator,
        conf.get(CONF_NAME, "Trueguard"),
        hass.data[DOMAIN][entry.entry_id][EGARDIA_DEVICE],
        conf.get(CONF_REPORT_SERVER_ENABLED, False),
        conf.get(CONF_REPORT_SERVER_CODES),
        conf.get(CONF_REPORT_SERVER_PORT, 52010),
        unique_id=f"trueguard_{host}_{port}",
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
        self._apply_panel_state((self.coordinator.data or {}).get("state"))

    async def async_added_to_hass(self) -> None:
        """Add Egardiaserver callback if enabled."""
        await super().async_added_to_hass()
        if self._rs_enabled:
            _LOGGER.debug("Registering callback to Egardiaserver")
            server = self.hass.data.get(EGARDIA_SERVER)
            if server is not None:
                server.register_callback(self.handle_status_event)

    @property
    def should_poll(self) -> bool:
        """Coordinator handles refreshes."""
        return False

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information for device registry."""
        panel_host = getattr(self._egardiasystem, "_host", "unknown")
        panel_port = getattr(self._egardiasystem, "_port", "unknown")
        panel_version = getattr(self._egardiasystem, "_version", None)
        return DeviceInfo(
            identifiers={(DOMAIN, f"{panel_host}_{panel_port}")},
            name=self._attr_name,
            manufacturer="Trueguard / Woonveilig",
            model=panel_version,
        )

    def handle_status_event(self, event):
        """Handle the Trueguard system status event."""
        if (statuscode := event.get("status")) is not None:
            status = self.lookupstatusfromcode(statuscode)
            self._apply_panel_state(status)
            self.async_write_ha_state()

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

    def _apply_panel_state(self, status):
        """Parse the status."""
        if status is None:
            return
        _LOGGER.debug("Parsing status %s", status)
        # Ignore the statuscode if it is IGNORE
        if status.lower().strip() != REPORT_SERVER_CODES_IGNORE:
            _LOGGER.debug("Not ignoring status %s", status)
            newstatus = STATES.get(status.upper())
            _LOGGER.debug("newstatus %s", newstatus)
            self._attr_alarm_state = newstatus
        else:
            _LOGGER.error("Ignoring status")

    def _handle_coordinator_update(self) -> None:
        """Handle coordinator updates."""
        self._apply_panel_state((self.coordinator.data or {}).get("state"))
        super()._handle_coordinator_update()

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

    async def async_alarm_disarm(self, code: str | None = None) -> None:
        """Send disarm command."""
        try:
            await self.hass.async_add_executor_job(
                self._egardiasystem.alarm_disarm
            )
            await self.coordinator.async_request_refresh()
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
            await self.coordinator.async_request_refresh()
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
            await self.coordinator.async_request_refresh()
        except requests.exceptions.RequestException as err:
            _LOGGER.error(
                "Trueguard device exception occurred when sending arm away command: %s",
                err,
            )
