"""Receive alarm reports pushed by the panel (SIA DC-09 or raw Contact ID).

Adapted from receiver.py in MRDonnii/trueguard-mz-home-assistant
(MIT License, Copyright (c) 2026 Jan Thorn). Change: a failing report
callback no longer drops the connection.

Point a free slot under the panel's "Rapport indst." at Home Assistant:

    ip://ACCOUNT@HA-IP:PORT/CID_SIA   (SIA DC-09 frames with Contact ID data)
    ip://ACCOUNT@HA-IP:PORT/SIA       (SIA DC-09 frames with SIA codes)
    ip://ACCOUNT@HA-IP:PORT/CID       (raw Contact ID, answered with 0x06)

Encrypted frames ("*SIA-DCS", the "/SES" suffix) are not supported.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
import logging
import re
import socket

_LOGGER = logging.getLogger(__name__)

ACK = b"\x06"
READ_TIMEOUT = 15
MAX_FRAME = 1024

# SIA DC-09 message after the CRC: 4 hex length digits, then the quoted id.
_DC09 = re.compile(
    r'(?P<length>[0-9A-F]{4})"(?P<encrypted>\*)?'
    r'(?P<type>SIA-DCS|ADM-CID|NULL)"(?P<sequence>\d{4})'
    r"(?P<receiver>R[0-9A-F]{1,6})?(?P<line>L[0-9A-F]{1,6})?"
    r"#?(?P<account>[0-9A-F]{3,16})?\[(?P<content>[^\]]*)\]",
    re.IGNORECASE,
)
# ADM-CID content: "#1234|1130 01 015" (qualifier+code, group, zone or user).
_ADM_CID = re.compile(
    r"\|\s*(?P<q>[136])(?P<code>\d{3})\s+(?P<group>[0-9A-F]{2})\s+(?P<zone>[0-9A-F]{3})",
    re.IGNORECASE,
)
# SIA-DCS content: "#1234|Nri1/BA015" or "Nri1BA015" (event code + zone).
_SIA_DCS = re.compile(r"(?P<code>[A-Z]{2})(?P<zone>\d{1,4})?(?:[\^/|]|$)")
# Raw Contact ID over IP: "[1234 18 1130 01 015 C]" with or without spaces.
_RAW_CID = re.compile(
    r"(?P<account>[0-9A-F]{3,16})\s*18\s*(?P<q>[136])\s*(?P<code>\d{3})\s*"
    r"(?P<group>[0-9A-F]{2})\s*(?P<zone>[0-9A-F]{3})",
    re.IGNORECASE,
)

# SIA-DCS codes mapped to Contact ID (qualifier, code) for the state logic.
SIA_TO_CID: dict[str, tuple[str, str]] = {
    "BA": ("1", "130"), "BR": ("3", "130"),  # burglary alarm / restore
    "FA": ("1", "110"), "FR": ("3", "110"),  # fire
    "PA": ("1", "120"), "PR": ("3", "120"),  # panic
    "HA": ("1", "121"),  # duress
    "WA": ("1", "154"), "WH": ("3", "154"),  # water
    "GA": ("1", "151"), "GH": ("3", "151"),  # gas
    "TA": ("1", "383"), "TR": ("3", "383"),  # sensor tamper
    "XT": ("1", "384"), "XR": ("3", "384"),  # sensor low battery
    "AT": ("1", "301"), "AR": ("3", "301"),  # mains failure
    "YT": ("1", "302"), "YR": ("3", "302"),  # panel low battery
    "XQ": ("1", "344"), "XH": ("3", "344"),  # jamming
    "OP": ("1", "401"), "CL": ("3", "401"),  # disarm / arm
    "NL": ("3", "441"),  # partial arm
    "RP": ("1", "602"),  # automatic test report
}


@dataclass(frozen=True, slots=True)
class PushReport:
    """One alarm report received from the panel."""

    format: str  # "ADM-CID", "SIA-DCS", "NULL", or "CID"
    account: str = ""
    crc_format: str = "hex"  # DC-09 CRC as 4 hex digits, or "bin" (2 raw bytes, Climax)
    qualifier: str = ""
    code: str = ""
    group: int | None = None
    zone: int | None = None
    sia_code: str = ""
    sequence: str = ""
    raw: str = ""

    @property
    def cid(self) -> str:
        """Four-digit Contact ID, for example '1130'."""
        return f"{self.qualifier}{self.code}" if self.qualifier and self.code else ""


def crc16(data: bytes) -> str:
    """CRC-16/ARC as four upper-case hex digits, as used by SIA DC-09."""
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return f"{crc:04X}"


def dc09_ack(frame: PushReport) -> bytes:
    """Build the SIA DC-09 ACK for a received, unencrypted frame.

    Same layout as pysiaalarm (Home Assistant's SIA integration):
    LF, CRC, length, '"ACK"', sequence, receiver, line, '#account', '[]', CR.
    The CRC uses the same form as the panel's frame: Climax panels send it as
    two raw bytes instead of four hex digits.
    """
    match = _DC09.search(frame.raw)
    receiver = (match.group("receiver") or "") if match else ""
    line = (match.group("line") or "") if match else ""
    body = f'"ACK"{frame.sequence}{receiver}{line}#{frame.account}[]'.encode("ascii")
    crc = crc16(body)
    crc_bytes = bytes.fromhex(crc) if frame.crc_format == "bin" else crc.encode()
    return b"\n" + crc_bytes + f"{len(body):04X}".encode() + body + b"\r"


def _split_dc09(raw: bytes) -> tuple[str, str] | None:
    """Return (crc_format, text from the length field on) for a DC-09 frame.

    Accepts the standard four hex CRC digits and the two raw CRC bytes Climax
    panels send, and checks the CRC and length against the message.
    """
    quote = raw.find(b'"')
    if quote < 4:
        return None
    length = raw[quote - 4 : quote]
    crc = raw[: quote - 4].lstrip(b"\n")
    message = raw[quote:].rstrip(b"\r\n\x00 ")
    if len(crc) == 4 and re.fullmatch(rb"[0-9A-Fa-f]{4}", crc):
        crc_format, sent_crc = "hex", crc.decode().upper()
    elif len(crc) == 2:
        crc_format, sent_crc = "bin", crc.hex().upper()
    else:
        return None
    if sent_crc != crc16(message):
        _LOGGER.warning("Trueguard report with a bad CRC: %r", raw[:200])
        return None
    try:
        if int(length, 16) != len(message):
            _LOGGER.debug("Trueguard report length differs: %r", raw[:200])
    except ValueError:
        return None
    return crc_format, (length + message).decode("ascii", errors="replace")


def _int(value: str | None, base: int = 10) -> int | None:
    try:
        return int(value, base) if value else None
    except ValueError:
        return None


def parse_frame(raw: bytes) -> PushReport | None:
    """Parse one received frame; return None for unknown or encrypted data."""
    text = raw.decode("ascii", errors="replace").strip("\n\r\x00 ")
    crc_format = "hex"
    if (split := _split_dc09(raw)) is not None:
        crc_format, text = split
    if split is not None and (match := _DC09.match(text)) is not None:
        if match.group("encrypted"):
            _LOGGER.warning(
                "Trueguard sent an encrypted SIA frame; remove /SES from the report slot"
            )
            return None
        kind = match.group("type").upper()
        account = (match.group("account") or "").upper()
        content = match.group("content")
        base = {
            "format": kind,
            "account": account,
            "crc_format": crc_format,
            "sequence": match.group("sequence"),
            "raw": text,
        }
        if kind == "NULL":
            return PushReport(**base)
        if kind == "ADM-CID" and (cid := _ADM_CID.search(content)):
            return PushReport(
                **base,
                qualifier=cid.group("q"),
                code=cid.group("code"),
                group=_int(cid.group("group")),
                zone=_int(cid.group("zone")),
            )
        if kind == "SIA-DCS":
            body = content.split("|", 1)[-1]
            body = re.sub(r"^N(?:ri\d+)?/?", "", body)
            if sia := _SIA_DCS.search(body):
                qualifier, code = SIA_TO_CID.get(sia.group("code"), ("", ""))
                return PushReport(
                    **base,
                    qualifier=qualifier,
                    code=code,
                    zone=_int(sia.group("zone")),
                    sia_code=sia.group("code"),
                )
        return PushReport(**base)
    if split is None and b'"' not in raw and (cid := _RAW_CID.search(text)) is not None:
        return PushReport(
            format="CID",
            account=cid.group("account").upper(),
            qualifier=cid.group("q"),
            code=cid.group("code"),
            group=_int(cid.group("group")),
            zone=_int(cid.group("zone")),
            raw=text,
        )
    return None


class ReportReceiver:
    """TCP server that accepts reports from the panel's IP address only."""

    def __init__(
        self,
        port: int,
        allowed_hosts: set[str],
        account: str,
        on_report: Callable[[PushReport], None],
    ) -> None:
        self.port = port
        self._allowed = allowed_hosts
        self._account = account.upper()
        self._on_report = on_report
        self._server: asyncio.base_events.Server | None = None

    async def async_start(self) -> None:
        self._server = await asyncio.start_server(self._handle, port=self.port)
        _LOGGER.info("Trueguard report receiver listening on port %s", self.port)

    @property
    def bound_port(self) -> int | None:
        """The port actually listened on (differs from `port` when it is 0)."""
        if self._server is None or not self._server.sockets:
            return None
        sockets = sorted(
            self._server.sockets, key=lambda sock: sock.family != socket.AF_INET
        )
        return sockets[0].getsockname()[1]

    async def async_stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def _handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        peer = (writer.get_extra_info("peername") or ("",))[0]
        if peer not in self._allowed:
            _LOGGER.warning("Ignored a Trueguard report from unknown host %s", peer)
            writer.close()
            return
        try:
            while True:
                raw = await asyncio.wait_for(reader.read(MAX_FRAME), READ_TIMEOUT)
                if not raw:
                    break
                report = parse_frame(raw)
                if report is None:
                    _LOGGER.warning("Unknown Trueguard report frame: %r", raw[:200])
                    break
                if self._account and report.account and report.account != self._account:
                    _LOGGER.warning(
                        "Ignored a Trueguard report for account %s", report.account
                    )
                    break
                raw_frame = report.format == "CID"
                writer.write(ACK if raw_frame else dc09_ack(report))
                await writer.drain()
                try:
                    self._on_report(report)
                except Exception:  # pylint: disable=broad-except
                    _LOGGER.exception("Error handling a Trueguard report")
                if raw_frame:
                    break
        except (TimeoutError, ConnectionError):
            pass
        finally:
            writer.close()
