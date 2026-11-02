"""Set up the integration against the panel's recorded answers."""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import State
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    mock_restore_cache_with_extra_data,
)

from custom_components.trueguard import DATA_COORDINATOR, DOMAIN

FIXTURES = Path(__file__).parents[1] / "tests" / "fixtures"
PANEL = (FIXTURES / "smarthome_panelcondget.txt").read_text(encoding="utf-8")
SENSORS = (FIXTURES / "smarthome_devicelistget.txt").read_text(encoding="utf-8")

# A panel's real answer when arming with a door open.
REFUSED = """{
  "result" : 0,
  "message" : "Kunne ikke skifte tilstand : Område1Zone4(Stue  Door  1) DC åbnet"
}"""

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


def answer(aioclient_mock, mode="Frakoblet", sensors=SENSORS, result="1"):
    """Make the mocked panel answer with this state."""
    aioclient_mock.clear_requests()
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
