"""Set up the integration against the panel's recorded answers."""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import State
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMockResponse,
)
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
    async_fire_time_changed,
    mock_restore_cache_with_extra_data,
)

from custom_components.trueguard import DATA_COORDINATOR, DOMAIN
from custom_components.trueguard.receiver import PushReport

FIXTURES = Path(__file__).parents[1] / "tests" / "fixtures"
PANEL = (FIXTURES / "smarthome_panelcondget.txt").read_text(encoding="utf-8")
SENSORS = (FIXTURES / "smarthome_devicelistget.txt").read_text(encoding="utf-8")

# A panel's real answer when arming with a door open.
REFUSED = """{
  "result" : 0,
  "message" : "Kunne ikke skifte tilstand : Område1Zone4(Stue  Door  1) DC åbnet"
}"""




def report_row(uid, cid, zone=4, new_event="Trigger", message="Indbrud", status="Færdig"):
    """A row of the report list as the panel answers it."""
    return (
        f'{{"uid": {uid}, "device_id": "RF:00e5ec10", "area": 1, "zone_user": {zone}, '
        f'"new_event": "{new_event}", "cid_event": "{cid}", "message": "{message}", '
        f'"time": "2026-11-14 10:00:00", "status": "{status}", "comment": ""}}'
    )


def report_list(*rows):
    return '{\n  "rptrows": [\n' + ",\n".join(rows) + "]\n}"


# Area 1's settings and the fault list, as a SMARTHOME panel answers them.
AREA = """{
  "forms" : {
    "areaform" : {
      "entry_time1_1": 30,
      "exit_time_1": 30,
      "alarm_len": 180,
      "su_t": 0
    }
  }
}"""
FAULTS = '{\n  "faultrows": [\n]\n}'

# The report list when the integration starts: rows already in it are not handled.
REPORTS = report_list(report_row(10, "602", zone=0, message="Periodisk test"))

BASE = "http://192.0.2.10:80/action/"
KEY = "192.0.2.10_80"
ALARM = "alarm_control_panel.trueguard"
DOOR = "RF:00e5ec10"  # "Zone 04 dør"
KEYPAD = "RF:00a94770"
DATA = {
    "name": "Trueguard",
    "host": "192.0.2.10",
    "port": 80,
    "username": "user",
    "password": "pass",
    "version": "SMARTHOME",
}


def answer(
    aioclient_mock, mode="Frakoblet", sensors=SENSORS, result="1", reports=REPORTS
):
    """Make the mocked panel answer with this state."""
    aioclient_mock.clear_requests()
    aioclient_mock.post(f"{BASE}reportEventListGet", text=reports)
    aioclient_mock.get(f"{BASE}areaGet?area=1", text=AREA)
    aioclient_mock.get(f"{BASE}panelFaultGet", text=FAULTS)
    aioclient_mock.get(
        f"{BASE}panelCondGet",
        text=PANEL.replace('"mode_a1" : "Frakoblet"', f'"mode_a1" : "{mode}"'),
    )
    aioclient_mock.get(f"{BASE}deviceListGet", text=sensors)
    aioclient_mock.post(f"{BASE}panelCondPost", text=f'{{"result" : {result}}}')


async def setup(hass, data=DATA, minor_version=2) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=1,
        minor_version=minor_version,
        unique_id=f"{data['host']}:{data['port']}",
        title=data["name"],
        data=data,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def coordinator_of(hass, entry):
    return hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]


def entity_id_of(hass, platform, unique_id):
    return er.async_get(hass).async_get_entity_id(platform, DOMAIN, unique_id)


# --- Config flows ------------------------------------------------------------------

async def test_reauth_saves_and_reloads(hass, aioclient_mock, caplog):
    answer(aioclient_mock)
    entry = await setup(hass)
    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"username": "user", "password": "new"}
    )
    assert result["reason"] == "reauth_successful"
    await hass.async_block_till_done()
    assert entry.data["password"] == "new"
    assert entry.state is ConfigEntryState.LOADED
    assert "update listener" not in caplog.text


async def test_reconfigure_keeps_identity(hass, aioclient_mock, caplog):
    answer(aioclient_mock)
    entry = await setup(hass)
    door = entity_id_of(hass, "binary_sensor", f"trueguard_{KEY}_zone_{DOOR}")

    aioclient_mock.get("http://192.0.2.11:80/action/panelCondGet", text=PANEL)
    aioclient_mock.get("http://192.0.2.11:80/action/deviceListGet", text=SENSORS)
    aioclient_mock.post("http://192.0.2.11:80/action/reportEventListGet", text=REPORTS)
    aioclient_mock.get("http://192.0.2.11:80/action/areaGet?area=1", text=AREA)
    aioclient_mock.get("http://192.0.2.11:80/action/panelFaultGet", text=FAULTS)
    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "host": "192.0.2.11",
            "port": 80,
            "version": "SMARTHOME",
            "username": "user",
            "password": "pass",
        },
    )
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()
    assert entry.data["host"] == "192.0.2.11"
    assert entry.state is ConfigEntryState.LOADED
    assert "update listener" not in caplog.text
    assert entity_id_of(hass, "binary_sensor", f"trueguard_{KEY}_zone_{DOOR}") == door


# --- Alarm state --------------------------------------------------------------------

@pytest.mark.parametrize(
    ("mode", "state"),
    [
        ("Frakoblet", "disarmed"),
        ("Fuldsikring", "armed_away"),
        ("Deltilkobling 1", "armed_home"),
        ("Deltilkobling 2", "armed_night"),
        ("Deltilkobling 3", "armed_custom_bypass"),
        ("Alarm", "triggered"),
        ("Tilkobler", "arming"),
        ("Indgangstid", "pending"),
    ],
)
async def test_polled_alarm_state(hass, aioclient_mock, mode, state):
    answer(aioclient_mock, mode)
    await setup(hass)
    assert hass.states.get(ALARM).state == state


async def test_polled_alarm_ends_with_the_panel(hass, aioclient_mock):
    answer(aioclient_mock, "Fuldsikring")
    entry = await setup(hass)
    coordinator = coordinator_of(hass, entry)
    answer(aioclient_mock, "Alarm")
    await coordinator.async_refresh()
    assert hass.states.get(ALARM).state == "triggered"
    answer(aioclient_mock, "Fuldsikring")
    await coordinator.async_refresh()
    assert hass.states.get(ALARM).state == "armed_away"


async def test_rejected_command_shows_the_panels_reason(hass, aioclient_mock):
    aioclient_mock.get(f"{BASE}panelCondGet", text=PANEL)
    aioclient_mock.get(f"{BASE}deviceListGet", text=SENSORS)
    aioclient_mock.post(f"{BASE}panelCondPost", text=REFUSED)
    aioclient_mock.post(f"{BASE}reportEventListGet", text=REPORTS)
    aioclient_mock.get(f"{BASE}areaGet?area=1", text=AREA)
    aioclient_mock.get(f"{BASE}panelFaultGet", text=FAULTS)
    await setup(hass)
    with pytest.raises(HomeAssistantError, match="did not accept.*Zone4.*åbnet"):
        await hass.services.async_call(
            "alarm_control_panel",
            "alarm_arm_away",
            {"entity_id": ALARM},
            blocking=True,
        )


async def test_accepted_command(hass, aioclient_mock):
    answer(aioclient_mock)
    await setup(hass)
    await hass.services.async_call(
        "alarm_control_panel", "alarm_arm_away", {"entity_id": ALARM}, blocking=True
    )


def stored_alarm(mode, until):
    return {
        "triggered": {
            "code": "130",
            "zone": 4,
            "burglary": True,
            "mode": mode,
            "until": until.isoformat(),
        },
        "last_alarm": {"alarm_cause": "Burglary", "alarm_zone": 4},
    }


async def test_reported_alarm_survives_restart(hass, aioclient_mock):
    mock_restore_cache_with_extra_data(
        hass,
        [
            (
                State(ALARM, "triggered"),
                stored_alarm("armed_away", dt_util.utcnow() + timedelta(minutes=30)),
            )
        ],
    )
    answer(aioclient_mock, "Fuldsikring")
    entry = await setup(hass)
    state = hass.states.get(ALARM)
    assert state.state == "triggered"
    assert state.attributes["alarm_cause"] == "Burglary"

    # Disarming ends it, as without a restart.
    answer(aioclient_mock, "Frakoblet")
    await coordinator_of(hass, entry).async_refresh()
    assert hass.states.get(ALARM).state == "disarmed"


async def test_reported_alarm_survives_reload(hass, aioclient_mock):
    answer(aioclient_mock, "Fuldsikring")
    with patch("custom_components.trueguard.ReportReceiver") as receiver:
        receiver.return_value.async_start = AsyncMock()
        receiver.return_value.async_stop = AsyncMock()
        entry = await setup(hass, {**DATA, "report_server_enabled": True})
        coordinator_of(hass, entry).async_handle_report(
            PushReport(format="ADM-CID", qualifier="1", code="130", zone=4)
        )
        await hass.async_block_till_done()
        assert hass.states.get(ALARM).state == "triggered"

        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
    state = hass.states.get(ALARM)
    assert state.state == "triggered"
    assert state.attributes["alarm_zone"] == 4


@pytest.mark.parametrize(
    ("mode", "panel", "until", "state"),
    [
        ("armed_away", "Frakoblet", timedelta(minutes=30), "disarmed"),  # disarmed meanwhile
        ("armed_away", "Fuldsikring", timedelta(minutes=-1), "armed_away"),  # expired
    ],
)
async def test_reported_alarm_not_restored(hass, aioclient_mock, mode, panel, until, state):
    mock_restore_cache_with_extra_data(
        hass,
        [(State(ALARM, "triggered"), stored_alarm(mode, dt_util.utcnow() + until))],
    )
    answer(aioclient_mock, panel)
    await setup(hass)
    assert hass.states.get(ALARM).state == state


# --- Zones --------------------------------------------------------------------------

async def test_rename_does_not_reload(hass, aioclient_mock):
    answer(aioclient_mock)
    entry = await setup(hass)
    coordinator = coordinator_of(hass, entry)
    door = entity_id_of(hass, "binary_sensor", f"trueguard_{KEY}_zone_{DOOR}")
    battery = entity_id_of(hass, "binary_sensor", f"trueguard_{KEY}_zone_{DOOR}_battery")

    answer(aioclient_mock, sensors=SENSORS.replace("Zone 04 dør", "Hoveddør"))
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert coordinator_of(hass, entry) is coordinator
    assert hass.states.get(door).name.endswith("Hoveddør")
    assert hass.states.get(battery).name.endswith("Hoveddør battery")
    assert er.async_get(hass).async_get(door).original_name == "Hoveddør"


async def test_removed_zone_reloads_after_two_polls(hass, aioclient_mock):
    answer(aioclient_mock)
    entry = await setup(hass)
    coordinator = coordinator_of(hass, entry)
    lines = SENSORS.splitlines()
    fewer = "\n".join(lines[:2] + lines[6:])  # one zone is four lines

    answer(aioclient_mock, sensors=fewer)
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert coordinator_of(hass, entry) is coordinator

    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert coordinator_of(hass, entry) is not coordinator


async def test_keypad_keeps_only_diagnostics(hass, aioclient_mock):
    answer(aioclient_mock)
    await setup(hass)
    assert entity_id_of(hass, "binary_sensor", f"trueguard_{KEY}_zone_{KEYPAD}") is None
    assert entity_id_of(hass, "binary_sensor", f"trueguard_{KEY}_zone_{KEYPAD}_battery")
    assert entity_id_of(hass, "binary_sensor", f"trueguard_{KEY}_zone_{DOOR}")


async def test_migration_keeps_entity_ids(hass, aioclient_mock):
    answer(aioclient_mock)
    registry = er.async_get(hass)
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=1,
        minor_version=1,
        unique_id="192.0.2.10:80",
        title="Trueguard",
        data=DATA,
    )
    entry.add_to_hass(hass)

    def old(platform, unique_id, object_id):
        return registry.async_get_or_create(
            platform,
            DOMAIN,
            unique_id,
            config_entry=entry,
            suggested_object_id=object_id,
        ).entity_id

    door = old("binary_sensor", f"trueguard_{DOOR}", "my_door")
    battery = old("binary_sensor", f"trueguard_{DOOR}_battery", "my_door_battery")
    signal = old("sensor", f"trueguard_{DOOR}_signal", "my_door_signal")
    keypad = old("binary_sensor", f"trueguard_{KEYPAD}", "my_keypad")
    panel_flag = old("binary_sensor", f"trueguard_{KEY}_panel_battery_ok", "my_panel")
    alarm = old("alarm_control_panel", f"trueguard_{KEY}", "my_alarm")

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.minor_version == 2
    assert registry.async_get(door).unique_id == f"trueguard_{KEY}_zone_{DOOR}"
    assert registry.async_get(battery).unique_id == f"trueguard_{KEY}_zone_{DOOR}_battery"
    assert registry.async_get(signal).unique_id == f"trueguard_{KEY}_zone_{DOOR}_signal"
    assert registry.async_get(panel_flag).unique_id == f"trueguard_{KEY}_panel_battery_ok"
    assert registry.async_get(alarm).unique_id == f"trueguard_{KEY}"
    assert registry.async_get(keypad) is None
    assert hass.states.get(door) is not None


async def test_device_keeps_entry_name(hass, aioclient_mock):
    answer(aioclient_mock)
    entry = await setup(hass, {**DATA, "name": "Sommerhus"})
    registry = dr.async_get(hass)
    [device] = dr.async_entries_for_config_entry(registry, entry.entry_id)
    assert device.name == "Sommerhus"


# --- Report list --------------------------------------------------------------

# The door zone, opened: any change in the panel's answer reads the report list.
OPENED = SENSORS.replace(
    '"id": "RF:00e5ec10", "su": 1,"attr": 0, "st": 2',
    '"id": "RF:00e5ec10", "su": 1,"attr": 0, "st": 3',
)


def report_reads(aioclient_mock):
    return sum(1 for call in aioclient_mock.mock_calls if "reportEventListGet" in str(call[1]))


async def test_report_list_alarm_triggers(hass, aioclient_mock):
    answer(aioclient_mock, "Fuldsikring")
    entry = await setup(hass)
    events = async_capture_events(hass, "trueguard_report")
    coordinator = coordinator_of(hass, entry)

    answer(
        aioclient_mock,
        "Fuldsikring",
        sensors=OPENED,
        reports=report_list(
            report_row(11, "131", message="Indbrud (Perimeter)"),
            report_row(10, "602", zone=0, message="Periodisk test"),
        ),
    )
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    state = hass.states.get(ALARM)
    assert state.state == "triggered"
    assert state.attributes["alarm_zone"] == 4
    assert state.attributes["alarm_zone_name"] == "Zone 04 dør"
    assert [(e.data["format"], e.data["cid"]) for e in events] == [("LIST", "1131")]

    # Disarming at the panel ends it.
    answer(aioclient_mock, "Frakoblet", sensors=OPENED)
    await coordinator.async_refresh()
    assert hass.states.get(ALARM).state == "disarmed"


async def test_report_list_rows_at_startup_are_not_handled(hass, aioclient_mock):
    answer(aioclient_mock, "Fuldsikring", reports=report_list(report_row(10, "131")))
    await setup(hass)
    assert hass.states.get(ALARM).state == "armed_away"


async def test_report_list_panel_tamper_is_not_an_alarm(hass, aioclient_mock):
    answer(aioclient_mock, "Fuldsikring")
    entry = await setup(hass)
    events = async_capture_events(hass, "trueguard_report")
    answer(
        aioclient_mock,
        "Fuldsikring",
        sensors=OPENED,
        reports=report_list(report_row(11, "137", zone=0, message="Panel sabotage")),
    )
    await coordinator_of(hass, entry).async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(ALARM).state == "armed_away"
    assert [e.data["description"] for e in events] == ["Tamper"]


async def test_report_list_ignores_arming_rows(hass, aioclient_mock):
    answer(aioclient_mock, "Fuldsikring")
    entry = await setup(hass)
    answer(
        aioclient_mock,
        "Fuldsikring",
        sensors=OPENED,
        reports=report_list(report_row(11, "401", zone=1, message="Frakoblet")),
    )
    await coordinator_of(hass, entry).async_refresh()
    assert hass.states.get(ALARM).state == "armed_away"


async def test_report_list_read_on_change_or_interval(hass, aioclient_mock):
    answer(aioclient_mock)
    with patch("custom_components.trueguard.coordinator.monotonic", return_value=1000):
        entry = await setup(hass)
        coordinator = coordinator_of(hass, entry)
        assert report_reads(aioclient_mock) == 1

        # Nothing changed: not read again.
        await coordinator.async_refresh()
        assert report_reads(aioclient_mock) == 1

        # A zone changed: read at once.
        answer(aioclient_mock, sensors=OPENED)
        await coordinator.async_refresh()
        assert report_reads(aioclient_mock) == 1  # counted since answer() cleared

    with patch("custom_components.trueguard.coordinator.monotonic", return_value=1061):
        answer(aioclient_mock, sensors=OPENED)
        await coordinator.async_refresh()
        assert report_reads(aioclient_mock) == 1


async def test_report_list_not_read_on_signal_changes(hass, aioclient_mock):
    answer(aioclient_mock)
    with patch("custom_components.trueguard.coordinator.monotonic", return_value=1000):
        entry = await setup(hass)
        assert report_reads(aioclient_mock) == 1
        weaker = SENSORS.replace('"rssi": "Stærk, 7"', '"rssi": "Stærk, 3"', 1)
        assert weaker != SENSORS
        answer(aioclient_mock, sensors=weaker)
        await coordinator_of(hass, entry).async_refresh()
        assert report_reads(aioclient_mock) == 0


async def test_report_list_skips_pushed_reports(hass, aioclient_mock):
    answer(aioclient_mock, "Fuldsikring")
    entry = await setup(hass)
    coordinator = coordinator_of(hass, entry)
    events = async_capture_events(hass, "trueguard_report")

    coordinator.async_handle_report(
        PushReport(format="ADM-CID", qualifier="1", code="131", group=1, zone=4)
    )
    answer(
        aioclient_mock,
        "Fuldsikring",
        sensors=OPENED,
        reports=report_list(report_row(11, "131"), report_row(10, "602", zone=0)),
    )
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert [e.data["format"] for e in events] == ["ADM-CID"]
    assert hass.states.get(ALARM).state == "triggered"


async def test_report_list_unsupported(hass, aioclient_mock):
    answer(aioclient_mock)
    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{BASE}panelCondGet", text=PANEL)
    aioclient_mock.get(f"{BASE}deviceListGet", text=SENSORS)
    aioclient_mock.post(f"{BASE}reportEventListGet", status=404)
    aioclient_mock.get(f"{BASE}areaGet?area=1", text=AREA)
    aioclient_mock.get(f"{BASE}panelFaultGet", text=FAULTS)
    with patch("custom_components.trueguard.coordinator.monotonic", return_value=1000):
        entry = await setup(hass)
        assert entry.state is ConfigEntryState.LOADED
        reads = report_reads(aioclient_mock)

        # A failed read is not repeated on every change, only per interval.
        aioclient_mock.clear_requests()
        aioclient_mock.get(f"{BASE}panelCondGet", text=PANEL)
        aioclient_mock.get(f"{BASE}deviceListGet", text=OPENED)
        aioclient_mock.post(f"{BASE}reportEventListGet", status=404)
        aioclient_mock.get(f"{BASE}areaGet?area=1", text=AREA)
        aioclient_mock.get(f"{BASE}panelFaultGet", text=FAULTS)
        await coordinator_of(hass, entry).async_refresh()
        assert report_reads(aioclient_mock) == 0
    assert reads == 2  # the first read, retried once
    assert hass.states.get(ALARM).state == "disarmed"


# --- Reporting and zone faults -------------------------------------------------

REPORTING = f"trueguard_{KEY}_panel_reporting"


async def test_reporting_failed_sensor(hass, aioclient_mock):
    answer(aioclient_mock)
    entry = await setup(hass)
    reporting = entity_id_of(hass, "binary_sensor", REPORTING)
    assert hass.states.get(reporting).state == "off"

    answer(
        aioclient_mock,
        sensors=OPENED,
        reports=report_list(
            report_row(11, "602", zone=0, message="Periodisk test", status="Fejlede"),
            report_row(10, "602", zone=0, message="Periodisk test"),
        ),
    )
    await coordinator_of(hass, entry).async_refresh()
    state = hass.states.get(reporting)
    assert state.state == "on"
    assert state.attributes["status"] == "Fejlede"
    assert state.attributes["report"] == "Periodisk test"


async def test_no_reporting_sensor_without_report_list(hass, aioclient_mock):
    aioclient_mock.get(f"{BASE}panelCondGet", text=PANEL)
    aioclient_mock.get(f"{BASE}deviceListGet", text=SENSORS)
    aioclient_mock.post(f"{BASE}reportEventListGet", status=404)
    aioclient_mock.get(f"{BASE}areaGet?area=1", text=AREA)
    aioclient_mock.get(f"{BASE}panelFaultGet", text=FAULTS)
    await setup(hass)
    assert entity_id_of(hass, "binary_sensor", REPORTING) is None


async def test_zone_fault_sensor(hass, aioclient_mock):
    answer(aioclient_mock)
    entry = await setup(hass)
    fault = entity_id_of(hass, "binary_sensor", f"trueguard_{KEY}_zone_{DOOR}_fault")
    assert hass.states.get(fault).state == "off"
    assert hass.states.get(fault).name.endswith("Zone 04 dør fault")

    faulty = SENSORS.replace(
        '"name": "Zone 04 dør", \n"cond": "", "cond_ok": "1"',
        '"name": "Zone 04 dør", \n"cond": "Funktionsfejl", "cond_ok": "0"',
    )
    assert faulty != SENSORS
    answer(aioclient_mock, sensors=faulty)
    await coordinator_of(hass, entry).async_refresh()
    state = hass.states.get(fault)
    assert state.state == "on"
    assert state.attributes["panel_status"] == "Funktionsfejl"


# --- Night arming and session errors ---------------------------------------------

SESSION_EXPIRED = "Adgang nægtet: Sessionen er udløbet!"


def posted_modes(aioclient_mock):
    return [
        call[2]["mode"]
        for call in aioclient_mock.mock_calls
        if "panelCondPost" in str(call[1])
    ]


ARM_NIGHT = 4  # AlarmControlPanelEntityFeature
ARM_CUSTOM_BYPASS = 16


@pytest.mark.parametrize(
    ("service", "mode"),
    [("alarm_arm_night", "3"), ("alarm_arm_custom_bypass", "4")],
)
async def test_partial_arm_2_and_3(hass, aioclient_mock, service, mode):
    answer(aioclient_mock)
    await setup(hass)
    features = hass.states.get(ALARM).attributes["supported_features"]
    assert features & ARM_NIGHT and features & ARM_CUSTOM_BYPASS
    await hass.services.async_call(
        "alarm_control_panel", service, {"entity_id": ALARM}, blocking=True
    )
    assert posted_modes(aioclient_mock) == [mode]


async def test_no_partial_arm_2_and_3_on_other_versions(hass, aioclient_mock):
    answer(aioclient_mock)
    await setup(hass, {**DATA, "version": "GATE-03"})
    features = hass.states.get(ALARM).attributes["supported_features"]
    assert not features & (ARM_NIGHT | ARM_CUSTOM_BYPASS)


@pytest.mark.parametrize(
    ("mode", "state"),
    [("Deltilkobling 2", "armed_night"), ("Deltilkobling 3", "armed_custom_bypass")],
)
async def test_partial_arm_report_keeps_the_partial_mode(hass, aioclient_mock, mode, state):
    answer(aioclient_mock, mode)
    entry = await setup(hass)
    coordinator_of(hass, entry).async_handle_report(
        PushReport(format="ADM-CID", qualifier="3", code="456", group=1, zone=1)
    )
    await hass.async_block_till_done()
    assert hass.states.get(ALARM).state == state


def _session_then(answer_text):
    """A panel that answers with a session error once, then answer_text."""
    calls = []

    async def respond(method, url, data):
        calls.append(data)
        text = SESSION_EXPIRED if len(calls) == 1 else answer_text
        return AiohttpClientMockResponse(method, url, text=text)

    return respond, calls


async def test_session_error_is_retried(hass, aioclient_mock):
    answer(aioclient_mock)
    await setup(hass)
    respond, calls = _session_then('{"result" : 1, "message" : "Opdateret."}')
    aioclient_mock.clear_requests()
    aioclient_mock.post(f"{BASE}panelCondPost", side_effect=respond)
    aioclient_mock.get(f"{BASE}panelCondGet", text=PANEL)
    aioclient_mock.get(f"{BASE}deviceListGet", text=SENSORS)
    aioclient_mock.post(f"{BASE}reportEventListGet", text=REPORTS)
    aioclient_mock.get(f"{BASE}areaGet?area=1", text=AREA)
    aioclient_mock.get(f"{BASE}panelFaultGet", text=FAULTS)
    await hass.services.async_call(
        "alarm_control_panel", "alarm_arm_away", {"entity_id": ALARM}, blocking=True
    )
    assert len(calls) == 2


async def test_repeated_session_error_fails_the_command(hass, aioclient_mock):
    answer(aioclient_mock)
    entry = await setup(hass)
    aioclient_mock.clear_requests()
    aioclient_mock.post(f"{BASE}panelCondPost", text=SESSION_EXPIRED)
    aioclient_mock.get(f"{BASE}panelCondGet", text=PANEL)
    aioclient_mock.get(f"{BASE}deviceListGet", text=SENSORS)
    aioclient_mock.post(f"{BASE}reportEventListGet", text=REPORTS)
    aioclient_mock.get(f"{BASE}areaGet?area=1", text=AREA)
    aioclient_mock.get(f"{BASE}panelFaultGet", text=FAULTS)
    with pytest.raises(HomeAssistantError, match="did not accept.*Sessionen er udløbet"):
        await hass.services.async_call(
            "alarm_control_panel", "alarm_arm_away", {"entity_id": ALARM}, blocking=True
        )
    # A session error is not a wrong password: no reauthentication.
    assert not list(entry.async_get_active_flows(hass, {"reauth"}))


async def test_session_error_while_polling_is_not_a_login_problem(hass, aioclient_mock):
    answer(aioclient_mock)
    entry = await setup(hass)
    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{BASE}panelCondGet", text=SESSION_EXPIRED)
    aioclient_mock.get(f"{BASE}deviceListGet", text=SESSION_EXPIRED)
    aioclient_mock.post(f"{BASE}reportEventListGet", text=REPORTS)
    aioclient_mock.get(f"{BASE}areaGet?area=1", text=AREA)
    aioclient_mock.get(f"{BASE}panelFaultGet", text=FAULTS)
    coordinator = coordinator_of(hass, entry)
    await coordinator.async_refresh()
    assert not coordinator.last_update_success
    assert not list(entry.async_get_active_flows(hass, {"reauth"}))
    assert entry.state is ConfigEntryState.LOADED


async def test_wrong_password_answer_starts_reauth(hass, aioclient_mock):
    answer(aioclient_mock)
    entry = await setup(hass)
    aioclient_mock.clear_requests()
    wrong = "Adgang nægtet: Forkert password!"
    aioclient_mock.get(f"{BASE}panelCondGet", text=wrong)
    aioclient_mock.get(f"{BASE}deviceListGet", text=wrong)
    aioclient_mock.post(f"{BASE}reportEventListGet", text=wrong)
    aioclient_mock.get(f"{BASE}areaGet?area=1", text=AREA)
    aioclient_mock.get(f"{BASE}panelFaultGet", text=FAULTS)
    await coordinator_of(hass, entry).async_refresh()
    await hass.async_block_till_done()
    assert list(entry.async_get_active_flows(hass, {"reauth"}))


async def test_wrong_password_answer_fails_a_command(hass, aioclient_mock):
    answer(aioclient_mock)
    await setup(hass)
    aioclient_mock.clear_requests()
    aioclient_mock.post(f"{BASE}panelCondPost", text="Adgang nægtet: Forkert password!")
    aioclient_mock.get(f"{BASE}panelCondGet", text=PANEL)
    aioclient_mock.get(f"{BASE}deviceListGet", text=SENSORS)
    aioclient_mock.post(f"{BASE}reportEventListGet", text=REPORTS)
    aioclient_mock.get(f"{BASE}areaGet?area=1", text=AREA)
    aioclient_mock.get(f"{BASE}panelFaultGet", text=FAULTS)
    with pytest.raises(HomeAssistantError, match="did not accept"):
        await hass.services.async_call(
            "alarm_control_panel", "alarm_arm_away", {"entity_id": ALARM}, blocking=True
        )


# --- History, siren time, faults, and bypass ---------------------------------------

async def test_signal_changes_do_not_write_zone_state(hass, aioclient_mock):
    answer(aioclient_mock)
    entry = await setup(hass)
    door = entity_id_of(hass, "binary_sensor", f"trueguard_{KEY}_zone_{DOOR}")
    before = hass.states.get(door)
    assert "rssi" not in before.attributes

    weaker = SENSORS.replace('"rssi": "Stærk, 6"', '"rssi": "Stærk, 2"')
    assert weaker != SENSORS
    answer(aioclient_mock, sensors=weaker)
    await coordinator_of(hass, entry).async_refresh()
    assert hass.states.get(door).last_updated == before.last_updated


async def test_siren_time_limits_a_reported_alarm(hass, aioclient_mock, freezer):
    answer(aioclient_mock, "Fuldsikring")
    entry = await setup(hass)
    assert hass.states.get(ALARM).attributes["siren_time"] == 180
    coordinator_of(hass, entry).async_handle_report(
        PushReport(format="ADM-CID", qualifier="1", code="131", group=1, zone=4)
    )
    await hass.async_block_till_done()
    assert hass.states.get(ALARM).state == "triggered"

    freezer.tick(timedelta(seconds=182))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get(ALARM).state == "armed_away"


async def test_without_area_settings_an_alarm_lasts_an_hour(hass, aioclient_mock):
    answer(aioclient_mock, "Fuldsikring")
    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{BASE}panelCondGet", text=PANEL.replace('"Frakoblet"', '"Fuldsikring"', 1))
    aioclient_mock.get(f"{BASE}deviceListGet", text=SENSORS)
    aioclient_mock.post(f"{BASE}reportEventListGet", text=REPORTS)
    aioclient_mock.get(f"{BASE}areaGet?area=1", status=404)
    aioclient_mock.get(f"{BASE}panelFaultGet", text=FAULTS)
    entry = await setup(hass)
    assert "siren_time" not in hass.states.get(ALARM).attributes
    alarm = hass.data["entity_components"]["alarm_control_panel"].get_entity(ALARM)
    assert alarm._triggered_max() == timedelta(hours=1)


FAULTS_ACTIVE = (
    '{"faultrows": [\n{"id": "1", "reason": "Lavt batteri i central", "clear": 0}]}'
)


async def test_faults_sensor(hass, aioclient_mock):
    answer(aioclient_mock)
    entry = await setup(hass)
    faults = entity_id_of(hass, "binary_sensor", f"trueguard_{KEY}_panel_faults")
    assert hass.states.get(faults).state == "off"

    answer(aioclient_mock, sensors=OPENED)
    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{BASE}panelCondGet", text=PANEL)
    aioclient_mock.get(f"{BASE}deviceListGet", text=OPENED)
    aioclient_mock.post(f"{BASE}reportEventListGet", text=REPORTS)
    aioclient_mock.get(f"{BASE}areaGet?area=1", text=AREA)
    aioclient_mock.get(f"{BASE}panelFaultGet", text=FAULTS_ACTIVE)
    await coordinator_of(hass, entry).async_refresh()
    state = hass.states.get(faults)
    assert state.state == "on"
    assert state.attributes["faults"] == ["Lavt batteri i central"]


async def test_no_faults_sensor_without_fault_list(hass, aioclient_mock):
    aioclient_mock.get(f"{BASE}panelCondGet", text=PANEL)
    aioclient_mock.get(f"{BASE}deviceListGet", text=SENSORS)
    aioclient_mock.post(f"{BASE}reportEventListGet", text=REPORTS)
    aioclient_mock.get(f"{BASE}areaGet?area=1", text=AREA)
    aioclient_mock.get(f"{BASE}panelFaultGet", status=404)
    await setup(hass)
    assert entity_id_of(hass, "binary_sensor", f"trueguard_{KEY}_panel_faults") is None


BYPASS = f"trueguard_{KEY}_zone_{DOOR}_bypass"


async def test_bypass_switch_is_disabled_by_default(hass, aioclient_mock):
    answer(aioclient_mock)
    await setup(hass)
    entity_id = entity_id_of(hass, "switch", BYPASS)
    assert er.async_get(hass).async_get(entity_id).disabled_by is not None
    assert entity_id_of(hass, "switch", f"trueguard_{KEY}_zone_{KEYPAD}_bypass") is None


@pytest.fixture
def bypass_enabled():
    with patch(
        "custom_components.trueguard.switch.TrueguardBypassSwitch."
        "_attr_entity_registry_enabled_default",
        True,
    ):
        yield


async def test_bypass_switch(hass, aioclient_mock, bypass_enabled):
    answer(aioclient_mock)
    entry = await setup(hass)
    switch = entity_id_of(hass, "switch", BYPASS)
    assert hass.states.get(switch).state == "off"

    aioclient_mock.post(f"{BASE}deviceBypass", text='{"result" : 1}')
    aioclient_mock.post(f"{BASE}deviceUnbypass", text='{"result" : 1}')
    await hass.services.async_call("switch", "turn_on", {"entity_id": switch}, blocking=True)
    await hass.services.async_call("switch", "turn_off", {"entity_id": switch}, blocking=True)
    sent = [
        (str(call[1]).rsplit("/", 1)[-1], call[2])
        for call in aioclient_mock.mock_calls
        if "Bypass" in str(call[1]) or "bypass" in str(call[1])
    ]
    assert sent == [("deviceBypass", {"id": DOOR}), ("deviceUnbypass", {"id": DOOR})]

    bypassed = SENSORS.replace(
        '"bypass": "Nej", "temp_bypass": "0", "rssi": "Stærk, 6", \n"status": "Lås", "id": "RF:00e5ec10"',
        '"bypass": "Nej", "temp_bypass": "1", "rssi": "Stærk, 6", \n"status": "Lås", "id": "RF:00e5ec10"',
    )
    assert bypassed != SENSORS
    answer(aioclient_mock, sensors=bypassed)
    await coordinator_of(hass, entry).async_refresh()
    assert hass.states.get(switch).state == "on"


async def test_rejected_bypass_raises(hass, aioclient_mock, bypass_enabled):
    answer(aioclient_mock)
    await setup(hass)
    aioclient_mock.post(f"{BASE}deviceBypass", text='{"result" : 0, "message" : "Fejl"}')
    with pytest.raises(HomeAssistantError, match="did not accept the bypass.*Fejl"):
        await hass.services.async_call(
            "switch", "turn_on", {"entity_id": entity_id_of(hass, "switch", BYPASS)}, blocking=True
        )
