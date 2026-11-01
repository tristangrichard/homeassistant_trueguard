"""What a pushed Contact ID report means for the alarm entity.

Pure functions without Home Assistant imports, so they can be tested alone.
"""
from __future__ import annotations

from dataclasses import dataclass

# Contact ID event codes with a readable name.
CODE_TEXT: dict[str, str] = {
    "100": "Medical",
    "110": "Fire",
    "111": "Smoke",
    "114": "Heat",
    "120": "Panic",
    "121": "Duress",
    "122": "Silent panic",
    "123": "Audible panic",
    "130": "Burglary",
    "131": "Perimeter",
    "132": "Interior",
    "133": "24-hour burglary",
    "134": "Entry/exit",
    "137": "Tamper",
    "144": "Sensor tamper",
    "147": "Sensor supervision",
    "150": "24-hour non-burglary",
    "151": "Gas",
    "154": "Water leak",
    "158": "High temperature",
    "159": "Low temperature",
    "162": "Carbon monoxide",
    "301": "230 V power failure",
    "302": "Panel low battery",
    "309": "Panel battery test failure",
    "311": "Panel battery missing",
    "344": "Radio jamming",
    "374": "Armed with fault",
    "380": "Sensor fault",
    "383": "Sensor tamper",
    "384": "Sensor low battery",
    "401": "Arm/disarm by user",
    "407": "Arm/disarm by remote",
    "441": "Partial arm",
    "456": "Partial arm",
    "570": "Zone bypass",
    "602": "Periodic test report",
    "607": "Walk test",
    "628": "Programming finished",
}

# Codes that mean "an alarm is going on" (qualifier 1).
_ALARM_RANGE = range(100, 170)
# Shown as an event, but never as a triggered alarm: duress and silent panic
# are silent by design, and 143-149 are expansion-module, sensor-tamper, and
# supervision troubles rather than alarms.
_SILENT_ALARMS = {"121", "122", "143", "144", "145", "146", "147", "148", "149"}
# Burglary alarms stay triggered until the panel is armed or disarmed again;
# other alarms (fire, water, panic, ...) also clear when the zone restores.
_BURGLARY_RANGE = range(130, 140)

_ARM_AWAY_CODES = {"400", "401", "403", "407", "408", "409"}
_ARM_HOME_CODES = {"441", "456"}


@dataclass(frozen=True, slots=True)
class ReportAction:
    """What the alarm entity should do with a report."""

    kind: str  # "disarm", "arm_away", "arm_home", "alarm", "restore", or "none"
    code: str = ""
    zone: int | None = None
    description: str = ""
    burglary: bool = False


def code_text(code: str) -> str:
    """Readable name of a Contact ID event code."""
    return CODE_TEXT.get(code, f"Contact ID {code}" if code else "")


def action_for(qualifier: str, code: str, zone: int | None = None) -> ReportAction:
    """Map a Contact ID qualifier and code to an alarm entity action.

    qualifier "1" = new event / opening (disarm), "3" = restore / closing (arm),
    "6" = status report.
    """
    text = code_text(code)
    if not code or not code.isdigit():
        return ReportAction("none", code, zone, text)
    number = int(code)

    if code in _ARM_AWAY_CODES:
        if qualifier == "1":
            return ReportAction("disarm", code, zone, text)
        if qualifier == "3":
            return ReportAction("arm_away", code, zone, text)
    if code in _ARM_HOME_CODES:
        if qualifier == "1":
            return ReportAction("disarm", code, zone, text)
        if qualifier == "3":
            return ReportAction("arm_home", code, zone, text)

    if number in _ALARM_RANGE and code not in _SILENT_ALARMS:
        burglary = number in _BURGLARY_RANGE
        if qualifier == "1":
            return ReportAction("alarm", code, zone, text, burglary)
        if qualifier == "3":
            return ReportAction("restore", code, zone, text, burglary)

    return ReportAction("none", code, zone, text)
