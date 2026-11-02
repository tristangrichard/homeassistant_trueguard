"""Tests for receiver.py (push frames) and reports.py (what they mean).

Both modules have no Home Assistant imports, so they are loaded straight
from their files and run with plain pytest.
"""
from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
import sys

import pytest

_DIR = Path(__file__).parents[1] / "custom_components" / "trueguard"


def _load(name):
    spec = importlib.util.spec_from_file_location(f"trueguard_{name}", _DIR / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses look the module up here
    spec.loader.exec_module(module)
    return module


R = _load("receiver")
A = _load("reports")


def dc09(message: str, crc_bytes: bool = False) -> bytes:
    """Build a SIA DC-09 frame the way a panel sends it."""
    body = message.encode()
    crc = R.crc16(body)
    crc_part = bytes.fromhex(crc) if crc_bytes else crc.encode()
    return b"\n" + crc_part + f"{len(body):04X}".encode() + body + b"\r"


# --- Frames ----------------------------------------------------------------------

def test_adm_cid_frame():
    report = R.parse_frame(dc09('"ADM-CID"0001L0#1234[#1234|1130 01 015]'))
    assert (report.format, report.account, report.cid, report.zone) == ("ADM-CID", "1234", "1130", 15)


def test_climax_binary_crc():
    report = R.parse_frame(dc09('"ADM-CID"0002L0#1234[#1234|3401 01 001]', crc_bytes=True))
    assert report.crc_format == "bin"
    assert report.cid == "3401"


def test_sia_dcs_frame_maps_to_contact_id():
    report = R.parse_frame(dc09('"SIA-DCS"0003L0#1234[#1234|Nri1/FA020]'))
    assert (report.sia_code, report.cid, report.zone) == ("FA", "1110", 20)


def test_null_heartbeat():
    assert R.parse_frame(dc09('"NULL"0004L0#1234[]')).format == "NULL"


def test_bad_crc_rejected():
    frame = bytearray(dc09('"ADM-CID"0001L0#1234[#1234|1130 01 015]'))
    frame[1:5] = b"0000"
    assert R.parse_frame(bytes(frame)) is None


def test_raw_contact_id():
    report = R.parse_frame(b"[1234 18 1130 01 015 C]")
    assert (report.format, report.cid, report.zone) == ("CID", "1130", 15)


def test_unknown_frame_ignored():
    assert R.parse_frame(b"[1234 ARM]") is None


def test_ack_echoes_sequence_and_account():
    report = R.parse_frame(dc09('"ADM-CID"0042L0#1234[#1234|1130 01 015]'))
    ack = R.dc09_ack(report)
    assert b'"ACK"0042L0#1234[]' in ack and ack.endswith(b"\r")


def test_receiver_accepts_panel_and_acks():
    async def run():
        got = []
        receiver = R.ReportReceiver(0, {"127.0.0.1", "::1"}, "1234", got.append)
        await receiver.async_start()
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", receiver.bound_port)
            writer.write(dc09('"ADM-CID"0001L0#1234[#1234|1130 01 015]'))
            await writer.drain()
            ack = await asyncio.wait_for(reader.read(100), 2)
            writer.close()
        finally:
            await receiver.async_stop()
        return got, ack

    got, ack = asyncio.run(run())
    assert b'"ACK"' in ack
    assert [r.cid for r in got] == ["1130"]


def test_receiver_without_allowed_hosts_accepts_nothing():
    async def run():
        got = []
        receiver = R.ReportReceiver(0, set(), "", got.append)
        await receiver.async_start()
        try:
            _, writer = await asyncio.open_connection("127.0.0.1", receiver.bound_port)
            writer.write(b"[1234 18 1130 01 015 C]")
            await writer.drain()
            await asyncio.sleep(0.1)
            writer.close()
        finally:
            await receiver.async_stop()
        return got

    assert asyncio.run(run()) == []


def test_receiver_ignores_other_hosts_and_accounts():
    async def run():
        got = []
        receiver = R.ReportReceiver(0, {"10.9.9.9"}, "", got.append)
        await receiver.async_start()
        try:
            _, writer = await asyncio.open_connection("127.0.0.1", receiver.bound_port)
            writer.write(b"[1234 18 1130 01 015 C]")
            await writer.drain()
            await asyncio.sleep(0.1)
            writer.close()
        finally:
            await receiver.async_stop()
        receiver = R.ReportReceiver(0, {"127.0.0.1", "::1"}, "9999", got.append)
        await receiver.async_start()
        try:
            _, writer = await asyncio.open_connection("127.0.0.1", receiver.bound_port)
            writer.write(dc09('"ADM-CID"0001L0#1234[#1234|1130 01 015]'))
            await writer.drain()
            await asyncio.sleep(0.1)
            writer.close()
        finally:
            await receiver.async_stop()
        return got

    assert asyncio.run(run()) == []


# --- Meaning -----------------------------------------------------------------------

@pytest.mark.parametrize(
    ("qualifier", "code", "kind", "burglary"),
    [
        ("1", "130", "alarm", True),
        ("3", "130", "restore", True),
        ("1", "110", "alarm", False),
        ("1", "154", "alarm", False),
        ("1", "121", "none", False),  # duress: event only
        ("1", "144", "none", False),  # sensor tamper: not an alarm
        ("1", "401", "disarm", False),
        ("3", "401", "arm_away", False),
        ("3", "441", "arm_home", False),
        ("3", "456", "arm_home", False),
        ("1", "384", "none", False),  # low battery
        ("1", "602", "none", False),
    ],
)
def test_action_for(qualifier, code, kind, burglary):
    action = A.action_for(qualifier, code, 15)
    assert action.kind == kind
    assert action.burglary is burglary


def test_code_text():
    assert A.code_text("130") == "Burglary"
    assert A.code_text("999") == "Contact ID 999"
