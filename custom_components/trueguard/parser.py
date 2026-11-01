"""Parsers for the Trueguard / Climax panel's /action/ responses.

The panel answers with JavaScript-style objects wrapped in a comment, for
example::

    /*-secure-
    {	senrows : [{no : 1, type : "Door Contact", name : "Hall", cond : ""}]}
    */

Keys are unquoted, so the text is not valid JSON. ``loads_panel_json`` turns
it into JSON with a small tokenizer that only touches text outside strings,
so commas, colons, or quotes inside sensor names are left alone.

This module has no Home Assistant imports so it can be tested on its own.
"""
from __future__ import annotations

import json
import re
from typing import Any

DEVICE_SMART_HOME = "SMARTHOME"
SUPPORTED_VERSIONS = ("WV-1716", "GATE-01", "GATE-02", "GATE-03", DEVICE_SMART_HOME)

# Versions that use the older sensor list (sensorListGet) and condition codes.
LEGACY_VERSIONS = ("WV-1716", "GATE-01", "GATE-02")
# Versions that report sensor state through the numeric "st" field.
MODERN_VERSIONS = ("GATE-03", DEVICE_SMART_HOME)

SENSOR_TYPES_TO_IGNORE = ("Remote Controller", "Remote Keypad", "Keypad")
UNAUTHORIZED_MESSAGES = ("Unauthorized", "Access Denied")

GATE03_STATES_MAPPING = {
    "FULL ARM": "ARM",
    "HOME ARM 1": "HOME",
    "HOME ARM 2": "HOME",
    "HOME ARM 3": "HOME",
    "DISARM": "DISARM",
}
SMARTHOME_STATES_MAPPING = {
    "FRAKOBLET": "DISARM",
    "DELTILKOBLING 1": "HOME",
    "FULDSIKRING": "ARM",
}

# panelCondPost "mode" values per command and version.
ARM_AWAY_MODE = {"WV-1716": 0, "GATE-01": 0, "GATE-02": 0, "GATE-03": 1, DEVICE_SMART_HOME: 1}
ARM_HOME_MODE = {"WV-1716": 1, "GATE-01": 1, "GATE-02": 1, "GATE-03": 2, DEVICE_SMART_HOME: 2}
DISARM_MODE = {"WV-1716": 2, "GATE-01": 4, "GATE-02": 4, "GATE-03": 0, DEVICE_SMART_HOME: 0}

# Sensor type codes.
TYPE_REMOTE = 2
TYPE_DOOR = 4
TYPE_SMOKE = 11
TYPE_MOTION = 27
TYPE_KEYPAD = 37
TYPE_SIREN = 45
TYPE_SIREN_2 = 46

_TYPE_TEXT_TO_CODE = {
    "door contact": TYPE_DOOR,
    "dørkontakt": TYPE_DOOR,
    "smoke alarm": TYPE_SMOKE,
    "røg alarm": TYPE_SMOKE,
    "pir kamera": TYPE_MOTION,
    "pir camera": TYPE_MOTION,
    "ir": TYPE_MOTION,
    "ir camera": TYPE_MOTION,
    "keypad": TYPE_KEYPAD,
    "tastatur": TYPE_KEYPAD,
    "remote keypad": TYPE_KEYPAD,
    "remote": TYPE_REMOTE,
    "remote controller": TYPE_REMOTE,
    "fjernbetjening": TYPE_REMOTE,
    "sirene": TYPE_SIREN,
    "siren": TYPE_SIREN,
}


class TrueguardError(Exception):
    """Base error for panel communication."""


class UnauthorizedError(TrueguardError):
    """The panel rejected the login."""


class VersionError(TrueguardError):
    """The configured panel version does not match the panel."""


class PanelParseError(TrueguardError):
    """The panel answered with something that could not be understood."""


def is_unauthorized(text: str) -> bool:
    """Return True if the panel's answer is a login rejection."""
    return any(msg in text for msg in UNAUTHORIZED_MESSAGES)


# --- Tolerant JSON ---------------------------------------------------------

_IDENT_START = re.compile(r"[A-Za-z_$]")
_IDENT = re.compile(r"[A-Za-z0-9_$]*")
_BARE_VALUES = {"true": "true", "false": "false", "null": "null", "undefined": "null"}


def _strip_wrapper(text: str) -> str:
    text = text.strip()
    if text.startswith("/*-secure-"):
        text = text[len("/*-secure-"):]
    elif text.startswith("/*"):
        text = text[2:]
    text = text.rstrip()
    if text.endswith("*/"):
        text = text[:-2]
    return text.strip()


def _read_string(text: str, i: int) -> tuple[str, int]:
    """Read a quoted string starting at text[i]; return it as JSON and the end index."""
    quote = text[i]
    j = i + 1
    chars: list[str] = []
    while j < len(text):
        c = text[j]
        if c == "\\" and j + 1 < len(text):
            nxt = text[j + 1]
            if quote == "'" and nxt == "'":
                chars.append("'")
            else:
                chars.append(c + nxt)
            j += 2
            continue
        if c == quote:
            break
        if c == '"':  # only possible inside a single-quoted string
            chars.append('\\"')
        else:
            chars.append(c)
        j += 1
    return '"' + "".join(chars) + '"', j + 1


def _to_json(text: str) -> str:
    """Quote bare keys, normalise strings, and drop trailing commas."""
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c in "\"'":
            token, i = _read_string(text, i)
            out.append(token)
            continue
        if _IDENT_START.match(c):
            m = _IDENT.match(text, i + 1)
            word = c + m.group(0)
            i = m.end()
            k = i
            while k < n and text[k] in " \t\r\n":
                k += 1
            if k < n and text[k] == ":":
                out.append(json.dumps(word))
            else:
                out.append(_BARE_VALUES.get(word, word))
            continue
        if c in "}]":
            # Remove a trailing comma before a closing bracket.
            k = len(out) - 1
            while k >= 0 and out[k].isspace():
                k -= 1
            if k >= 0 and out[k] == ",":
                del out[k]
        out.append(c)
        i += 1
    return "".join(out)


def loads_panel_json(text: str) -> Any:
    """Parse the panel's JavaScript-style answer into Python objects."""
    try:
        return json.loads(_to_json(_strip_wrapper(text)), strict=False)
    except (ValueError, IndexError) as err:
        raise PanelParseError(f"Could not parse panel answer: {err}") from err


def find_key(data: Any, key: str) -> Any:
    """Return the first value for key anywhere in nested dicts and lists."""
    if isinstance(data, dict):
        if key in data:
            return data[key]
        values = data.values()
    elif isinstance(data, list):
        values = data
    else:
        return None
    for value in values:
        found = find_key(value, key)
        if found is not None:
            return found
    return None


# --- panelCondGet ------------------------------------------------------------

def _state_key(version: str) -> str:
    return "mode_st" if version == "WV-1716" else "mode_a1"


def parse_panel_state(text: str, version: str) -> str:
    """Return the alarm mode (ARM, HOME, DISARM, ...) from a panelCondGet answer."""
    if is_unauthorized(text):
        raise UnauthorizedError("The panel rejected the login")
    key = _state_key(version)
    raw: Any = None
    try:
        raw = find_key(loads_panel_json(text), key)
    except PanelParseError:
        raw = None
    if raw is None:
        # Fall back to a direct search if the object could not be parsed.
        match = re.search(rf'"?{key}"?\s*:\s*"([^"]*)"', text)
        if match is None:
            raise PanelParseError(f"No {key} in panel state answer")
        raw = match.group(1)

    status = str(raw).strip().upper()
    if version == "GATE-03":
        return GATE03_STATES_MAPPING.get(status, "UNKNOWN")
    if version == DEVICE_SMART_HOME:
        return SMARTHOME_STATES_MAPPING.get(status, "UNKNOWN")
    return status


# --- deviceListGet / sensorListGet -------------------------------------------

def sensor_list_action(version: str) -> str:
    """Return the /action/ name that lists sensors for this version."""
    return "sensorListGet" if version in ("WV-1716", "GATE-01") else "deviceListGet"


def parse_sensor_list(text: str, version: str) -> dict[Any, dict[str, Any]]:
    """Return the panel's sensors keyed by id, without remotes and keypads."""
    if is_unauthorized(text):
        raise UnauthorizedError("The panel rejected the login")
    if "is not defined" in text:
        raise VersionError(
            "Unable to communicate with the device. "
            "Did you configure your version correctly?"
        )
    rows = find_key(loads_panel_json(text), "senrows")
    if not isinstance(rows, list):
        raise PanelParseError("No senrows in panel sensor answer")

    type_field = "type_f" if version == "GATE-03" else "type"
    sensors: dict[Any, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get(type_field) in SENSOR_TYPES_TO_IGNORE:
            continue
        sensor = dict(row)
        if version in ("WV-1716", "GATE-01"):
            sensor["id"] = sensor.pop("no", None)
        elif version in MODERN_VERSIONS:
            # GATE-03 reports the readable type in type_f; use it as "type".
            sensor["type"] = sensor.pop(type_field, None)
        if sensor.get("id") is None:
            continue
        sensors[sensor["id"]] = sensor
    return sensors


# --- Sensor state --------------------------------------------------------------

def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def sensor_type_code(sensor: dict[str, Any]) -> int | None:
    """Return the numeric type code, derived from the type text if needed."""
    raw_type = sensor.get("type")
    code = _as_int(raw_type)
    if code is not None:
        return code
    if isinstance(raw_type, str):
        code = _TYPE_TEXT_TO_CODE.get(raw_type.strip().lower())
        if code is not None:
            return code
    type_name = str(sensor.get("type_f", "")).strip().lower()
    return _TYPE_TEXT_TO_CODE.get(type_name) if type_name else None


def sensor_is_on(sensor: dict[str, Any] | None, version: str) -> bool | None:
    """Return whether a sensor is open/triggered, or None if unknown."""
    if sensor is None:
        return None

    status = str(sensor.get("status", "")).upper()
    cond = str(sensor.get("cond", ""))
    st_value = _as_int(sensor.get("st"))
    sensor_type = sensor_type_code(sensor)

    if sensor_type == TYPE_DOOR:
        if status in ("DOOR OPEN", "LÅS OP", "OPEN") or st_value == 3:
            return True
        if status in ("DOOR CLOSE", "LÅS", "CLOSED") or st_value == 2:
            return False
        if st_value is not None:
            return st_value > 0
        return len(cond.strip()) > 0

    if sensor_type == TYPE_SMOKE:
        if st_value is not None:
            return st_value > 0
        return any(k in status for k in ("SMOKE", "RØG", "ALARM", "FIRE"))

    if sensor_type == TYPE_MOTION:
        if cond.strip():
            return True
        if st_value is not None:
            return st_value > 0
        return any(k in status for k in ("MOTION", "TRIGGER", "ALARM"))

    if sensor_type in (TYPE_REMOTE, TYPE_KEYPAD, TYPE_SIREN, TYPE_SIREN_2):
        return st_value > 0 if st_value is not None else False

    if version in LEGACY_VERSIONS:
        return len(cond) > 0

    if st_value is not None:
        return st_value > 0
    return None


# --- panelCondPost --------------------------------------------------------------

def parse_command_result(text: str) -> str:
    """Return the panel's result value from a panelCondPost answer."""
    if is_unauthorized(text):
        raise UnauthorizedError("The panel rejected the login")
    try:
        result = find_key(loads_panel_json(text), "result")
    except PanelParseError:
        result = None
    if result is None:
        match = re.search(r'"?result"?\s*:\s*"?([^,}"\s]*)', text)
        result = match.group(1) if match else ""
    return str(result)
