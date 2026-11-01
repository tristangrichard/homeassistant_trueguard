"""Async HTTP client for Trueguard / Climax panels (/action/ API)."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp

from .parser import (
    ARM_AWAY_MODE,
    ARM_HOME_MODE,
    DISARM_MODE,
    SUPPORTED_VERSIONS,
    PanelParseError,
    TrueguardError,
    UnauthorizedError,
    VersionError,
    is_unauthorized,
    parse_command_result,
    parse_panel_state,
    parse_sensor_list,
    sensor_is_on,
    sensor_list_action,
)

_LOGGER = logging.getLogger(__name__)

REQUEST_TIMEOUT = 10
RETRY_DELAY = 0.5

__all__ = [
    "PanelParseError",
    "TrueguardClient",
    "TrueguardConnectionError",
    "TrueguardError",
    "UnauthorizedError",
    "VersionError",
]


class TrueguardConnectionError(TrueguardError):
    """The panel could not be reached or answered with an HTTP error."""


def _decode(body: bytes, charset: str | None) -> str:
    """Decode a panel answer. Without a declared charset, try UTF-8, then Latin-1."""
    if charset:
        try:
            return body.decode(charset, errors="replace")
        except LookupError:
            pass
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        return body.decode("latin-1")


class TrueguardClient:
    """Talk to the panel over its local HTTP API using a shared aiohttp session.

    Reads (GET) are retried once on network errors. Commands (POST) are never
    retried on network errors, because the panel may already have acted on
    them. Both are retried once if the panel answers "Unauthorized", which
    some panels do now and then; a rejected request has no effect.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        port: int,
        username: str,
        password: str,
        version: str,
    ) -> None:
        self._version = str(version).upper()
        if self._version not in SUPPORTED_VERSIONS:
            raise VersionError(f"Trueguard device version {self._version} is unsupported.")
        self._session = session
        self._host = host
        self._port = port
        self._auth = aiohttp.BasicAuth(username, password)
        self._base_url = f"http://{host}:{port}/action/"
        # Stable device identity; the integration overrides it after Reconfigure.
        self.device_key = f"{host}_{port}"

    @property
    def host(self) -> str:
        """Panel host."""
        return self._host

    @property
    def port(self) -> int:
        """Panel port."""
        return self._port

    @property
    def version(self) -> str:
        """Configured panel version."""
        return self._version

    # --- HTTP -----------------------------------------------------------------

    async def _request_once(
        self, method: str, action: str, data: dict[str, Any] | None
    ) -> str:
        url = self._base_url + action
        _LOGGER.debug("Trueguard %s %s %s", method, url, data or "")
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT):
                async with self._session.request(
                    method, url, auth=self._auth, data=data
                ) as response:
                    if response.status in (401, 403):
                        raise UnauthorizedError("The panel rejected the login")
                    body = await response.read()
                    if response.status >= 400:
                        raise TrueguardConnectionError(
                            f"Panel returned HTTP {response.status} for {action}"
                        )
                    text = _decode(body, response.charset)
        except TrueguardError:
            raise
        except (aiohttp.ClientError, TimeoutError, OSError) as err:
            raise TrueguardConnectionError(
                f"Error talking to panel at {self._host}:{self._port}: {err!r}"
            ) from err
        if is_unauthorized(text):
            raise UnauthorizedError("The panel rejected the login")
        return text

    async def _request(
        self, method: str, action: str, data: dict[str, Any] | None = None
    ) -> str:
        """Send a request with the retry rules described on the class."""
        retried_auth = False
        retried_network = False
        while True:
            try:
                return await self._request_once(method, action, data)
            except UnauthorizedError:
                if retried_auth:
                    raise
                retried_auth = True
            except TrueguardConnectionError:
                if method != "GET" or retried_network:
                    raise
                retried_network = True
                _LOGGER.debug("Retrying %s after a network error", action)
            await asyncio.sleep(RETRY_DELAY)

    # --- Reads --------------------------------------------------------------------

    async def async_get_state(self) -> str:
        """Return the alarm mode: ARM, HOME, DISARM, ... or UNKNOWN."""
        return parse_panel_state(await self._request("GET", "panelCondGet"), self._version)

    async def async_get_sensors(self) -> dict[Any, dict[str, Any]]:
        """Return the sensors keyed by id."""
        text = await self._request("GET", sensor_list_action(self._version))
        return parse_sensor_list(text, self._version)

    async def async_get_data(self) -> dict[str, Any]:
        """Read alarm state and sensors at the same time."""
        results = await asyncio.gather(
            self.async_get_state(), self.async_get_sensors(), return_exceptions=True
        )
        errors = [r for r in results if isinstance(r, BaseException)]
        if errors:
            # A login rejection matters most: it starts reauthentication.
            for err in errors:
                if isinstance(err, UnauthorizedError):
                    raise err
            raise errors[0]
        state, sensors = results
        return {"state": state, "sensors": sensors}

    def sensor_is_on(self, sensor: dict[str, Any] | None) -> bool | None:
        """Return whether a sensor is open/triggered for this panel version."""
        return sensor_is_on(sensor, self._version)

    # --- Commands -------------------------------------------------------------------

    async def _async_send_mode(self, mode: int, label: str) -> None:
        text = await self._request(
            "POST", "panelCondPost", {"area": "1", "mode": str(mode)}
        )
        _LOGGER.info("Trueguard %s, result: %s", label, parse_command_result(text))

    async def async_arm_away(self) -> None:
        """Arm the panel fully."""
        await self._async_send_mode(ARM_AWAY_MODE[self._version], "arming away")

    async def async_arm_home(self) -> None:
        """Arm the panel in home mode."""
        await self._async_send_mode(ARM_HOME_MODE[self._version], "arming home")

    async def async_disarm(self) -> None:
        """Disarm the panel."""
        await self._async_send_mode(DISARM_MODE[self._version], "disarming")
