"""Tests for custom_components/trueguard/parser.py.

parser.py has no Home Assistant imports, so it is loaded straight from its
file and these tests run with plain pytest:

    python -m pytest tests
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_PATH = Path(__file__).parents[1] / "custom_components" / "trueguard" / "parser.py"
_spec = importlib.util.spec_from_file_location("trueguard_parser", _PATH)
P = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(P)


# Older panels (WV-1716, GATE-01, GATE-02) use unquoted keys.
LEGACY_STATE = (
    '/*-secure-\n{\tupdates : {mode_a1 : "%s", mode_a2 : "Disarm", '
    'battery_ok : "1"}, result : 1}\n*/'
)
WV1716_STATE = '/*-secure-\n{\tupdates : {mode_st : "%s", battery_ok : "1"}, result : 1}\n*/'
# GATE-03 and SMARTHOME quote their keys.
MODERN_STATE = '/*-secure-\n{\t"updates" : {"mode_a1" : "%s", "mode_a2" : "Disarm"}, "result" : 1}\n*/'

LEGACY_SENSORS = (
    "/*-secure-\n{\tsenrows : ["
    '{no : 1, type : "Door Contact", area : 1, zone : 1, name : "Hall door", '
    'cond : "", battery_ok : "1", tamper_ok : "1", rssi : "Strong, 9", status : ""},\n'
    '{no : 2, type : "Remote Controller", area : 1, zone : 2, name : "Fob", cond : ""},\n'
    '{no : 3, type : "IR", area : 1, zone : 3, name : "Living: room", cond : "Open"}'
    "]}\n*/"
)
MODERN_SENSORS = (
    '/*-secure-\n{\t"senrows" : ['
    '{"id" : "RF:01", "type" : 4, "type_f" : "Dørkontakt", "name" : "Hoveddør", '
    '"status" : "Door Close", "st" : "2", "battery_ok" : 1, "tamper_ok" : 1},\n'
    '{"id" : "RF:02", "type" : "Keypad", "type_f" : "Tastatur", "name" : "Tastatur", "st" : "0"},\n'
    '{"id" : "RF:03", "type" : 27, "type_f" : "PIR kamera", "name" : "Stue", "cond" : "", "st" : "1"}'
    "]}\n*/"
)


# --- Tolerant JSON ---------------------------------------------------------

def test_unquoted_keys_and_wrapper():
    assert P.loads_panel_json('/*-secure-\n{a : 1, b_c : "x"}\n*/') == {"a": 1, "b_c": "x"}


def test_text_inside_strings_is_left_alone():
    data = P.loads_panel_json('{name : "Door, type : back", note : \'it\\\'s "ok"\'}')
    assert data == {"name": "Door, type : back", "note": 'it\'s "ok"'}


def test_trailing_commas_and_undefined():
    assert P.loads_panel_json("{rows : [1, 2,], x : undefined,}") == {"rows": [1, 2], "x": None}


def test_garbage_raises_parse_error():
    with pytest.raises(P.PanelParseError):
        P.loads_panel_json("<html>oops</html>")


# --- Panel state ---------------------------------------------------------------

@pytest.mark.parametrize(
    ("version", "template", "raw", "expected"),
    [
        ("WV-1716", WV1716_STATE, "Arm", "ARM"),
        ("GATE-01", LEGACY_STATE, "Home", "HOME"),
        ("GATE-02", LEGACY_STATE, "Disarm", "DISARM"),
        ("GATE-03", MODERN_STATE, "Full Arm", "ARM"),
        ("GATE-03", MODERN_STATE, "Home Arm 2", "HOME"),
        ("GATE-03", MODERN_STATE, "Something", "UNKNOWN"),
        ("SMARTHOME", MODERN_STATE, "Frakoblet", "DISARM"),
        ("SMARTHOME", MODERN_STATE, "Deltilkobling 1", "HOME"),
        ("SMARTHOME", MODERN_STATE, "Fuldsikring", "ARM"),
    ],
)
def test_parse_panel_state(version, template, raw, expected):
    assert P.parse_panel_state(template % raw, version) == expected


def test_panel_state_unauthorized():
    with pytest.raises(P.UnauthorizedError):
        P.parse_panel_state("401 Unauthorized", "SMARTHOME")


def test_panel_state_missing_mode():
    with pytest.raises(P.PanelParseError):
        P.parse_panel_state('{"result" : 1}', "SMARTHOME")


# --- Sensor list ----------------------------------------------------------------

def test_legacy_sensor_list_renames_no_to_id_and_skips_remotes():
    sensors = P.parse_sensor_list(LEGACY_SENSORS, "GATE-01")
    assert list(sensors) == [1, 3]
    assert sensors[1]["name"] == "Hall door"
    assert sensors[1]["rssi"] == "Strong, 9"
    assert sensors[3]["name"] == "Living: room"


def test_modern_sensor_list_skips_keypad():
    sensors = P.parse_sensor_list(MODERN_SENSORS, "SMARTHOME")
    assert list(sensors) == ["RF:01", "RF:03"]
    assert sensors["RF:01"]["name"] == "Hoveddør"


def test_gate03_uses_type_f_as_type():
    sensors = P.parse_sensor_list(MODERN_SENSORS, "GATE-03")
    assert sensors["RF:01"]["type"] == "Dørkontakt"
    assert "type_f" not in sensors["RF:01"]


def test_wrong_version_detected():
    with pytest.raises(P.VersionError):
        P.parse_sensor_list("ReferenceError: senrows is not defined", "GATE-02")


def test_sensor_list_unauthorized():
    with pytest.raises(P.UnauthorizedError):
        P.parse_sensor_list("Access Denied", "GATE-02")


# --- Sensor state ----------------------------------------------------------------

@pytest.mark.parametrize(
    ("sensor", "version", "expected"),
    [
        ({"type": 4, "status": "Door Open"}, "SMARTHOME", True),
        ({"type": 4, "st": "2"}, "SMARTHOME", False),
        ({"type": 4, "st": "3"}, "SMARTHOME", True),
        ({"type": "Door Contact", "cond": "Open"}, "GATE-01", True),
        ({"type": "Door Contact", "cond": ""}, "GATE-01", False),
        ({"type": 11, "st": "0"}, "SMARTHOME", False),
        ({"type": "Røg alarm", "status": "Røg"}, "SMARTHOME", True),
        ({"type": 27, "cond": "Trigger"}, "SMARTHOME", True),
        ({"type": "IR", "cond": ""}, "GATE-02", False),
        ({"type": 45, "st": "1"}, "SMARTHOME", True),
        ({"type": 37}, "SMARTHOME", False),
        ({"type": 99, "st": "1"}, "SMARTHOME", True),
        ({"type": 99}, "SMARTHOME", None),
        ({"type": "Power Switch Meter", "cond": "On"}, "GATE-02", True),
    ],
)
def test_sensor_is_on(sensor, version, expected):
    assert P.sensor_is_on(sensor, version) is expected


def test_sensor_is_on_none():
    assert P.sensor_is_on(None, "SMARTHOME") is None


# --- Commands -----------------------------------------------------------------------

def test_command_result():
    assert P.parse_command_result("/*-secure-\n{\tresult : 1, mode : 0}\n*/") == "1"


@pytest.mark.parametrize("version", P.SUPPORTED_VERSIONS)
def test_every_version_has_command_modes(version):
    assert version in P.ARM_AWAY_MODE
    assert version in P.ARM_HOME_MODE
    assert version in P.DISARM_MODE


# --- Real answers from a SMARTHOME panel (names anonymised) ---------------------

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(params=[False, True], ids=["plain", "secure-wrapper"])
def wrap(request):
    if request.param:
        return lambda text: f"/*-secure-\n{text}\n*/"
    return lambda text: text


def test_real_smarthome_state(wrap):
    text = (FIXTURES / "smarthome_panelcondget.txt").read_text(encoding="utf-8")
    assert P.parse_panel_state(wrap(text), "SMARTHOME") == "DISARM"


def test_real_smarthome_sensor_list(wrap):
    text = (FIXTURES / "smarthome_devicelistget.txt").read_text(encoding="utf-8")
    sensors = P.parse_sensor_list(wrap(text), "SMARTHOME")
    assert len(sensors) == 26
    door = sensors["RF:00e5ec10"]
    assert door["type"] == 4
    assert door["type_f"] == "Dørkontakt"
    assert door["rssi"] == "Stærk, 6"
    assert P.sensor_is_on(door, "SMARTHOME") is False
    assert P.sensor_is_on({**door, "st": 3, "status": "Lås op"}, "SMARTHOME") is True
    assert all(P.sensor_is_on(s, "SMARTHOME") is False for s in sensors.values())


def test_real_smarthome_panel_status():
    text = (FIXTURES / "smarthome_panelcondget.txt").read_text(encoding="utf-8")
    state, panel = P.parse_panel_status(text, "SMARTHOME")
    assert state == "DISARM"
    assert panel["battery_ok"] == "1"
    assert panel["ac_activation"] == "Normal"
    assert panel["sig_gsm"] == "9"
    assert "pcondform1" not in panel  # nested forms are left out


@pytest.mark.parametrize(
    ("value", "expected"),
    [("1", True), ("0", False), (1, True), ("", None), (None, None)],
)
def test_flag_ok(value, expected):
    assert P.flag_ok(value) is expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [("Stærk, 7", 7), ("God, 5", 5), ("N/A", None), ("9", 9), (3, 3), (None, None)],
)
def test_signal_level(value, expected):
    assert P.signal_level(value) == expected


@pytest.mark.parametrize(
    ("version", "raw", "expected"),
    [
        ("SMARTHOME", "Deltilkobling 2", "HOME"),
        ("SMARTHOME", "Deltilkobling 3", "HOME"),
        ("SMARTHOME", "Deltilkoblet 1", "HOME"),
        ("SMARTHOME", "Fuldtilkoblet", "ARM"),
        ("SMARTHOME", "Frakoblet", "DISARM"),
        ("SMARTHOME", "Noget nyt", "UNKNOWN"),
        ("GATE-03", "Home Arm 4", "HOME"),
    ],
)
def test_mode_variants(version, raw, expected):
    assert P.parse_panel_state(MODERN_STATE % raw, version) == expected
