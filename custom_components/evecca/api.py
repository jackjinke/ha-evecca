"""Async client for the EVECCA cloud API."""

import asyncio
import base64
import hashlib
import json
from typing import Any

from aiohttp import ClientError, ClientResponse, ClientSession

from .const import (
    APP_ID,
    BASE_URL,
    CLIENT_MACHINE,
    CLIENT_MODEL,
    CLIENT_OS,
    DPID_ACTION,
    REQUEST_TIMEOUT,
)
from .models import EveccaDevice, EveccaFamily, EveccaSession

_UNSET = object()
_AUTH_ERROR_CODES = {99, 4008, 4014}


class EveccaError(Exception):
    """Base EVECCA API error."""


class EveccaConnectionError(EveccaError):
    """The EVECCA service could not be reached."""


class EveccaAuthError(EveccaError):
    """The EVECCA credentials or token are invalid."""


class EveccaApiError(EveccaError):
    """The EVECCA API returned an error."""

    def __init__(self, message: str, code: int | None = None) -> None:
        """Initialize the API error."""
        super().__init__(message)
        self.code = code


class EveccaApi:
    """Small async wrapper around EVECCA's HTTPS API."""

    def __init__(
        self,
        session: ClientSession,
        *,
        base_url: str = BASE_URL,
        app_id: int = APP_ID,
    ) -> None:
        """Initialize the API client."""
        self._session = session
        self._base_url = base_url.rstrip("/")
        self._app_id = app_id

    async def async_password_login(
        self,
        username: str,
        password: str,
        hw_id: str,
    ) -> EveccaSession:
        """Log in with an account password."""
        payload = {
            "userName": username,
            "pwdMd5": hashlib.md5(password.encode("utf-8")).hexdigest(),
            "hwId": hw_id,
            "machine": CLIENT_MACHINE,
            "OS": CLIENT_OS,
            "jgId": "",
        }
        result = await self._post("/pwdLogin", payload)
        return EveccaSession.from_api(result)

    async def async_send_login_code(self, username: str, hw_id: str) -> None:
        """Send an SMS code for login."""
        await self._post(
            "/sendCode",
            {"userName": username, "oper": 2, "hwId": hw_id},
        )

    async def async_code_login(
        self,
        username: str,
        code: int,
        hw_id: str,
    ) -> EveccaSession:
        """Log in with an SMS verification code."""
        payload = {
            "userName": username,
            "code": code,
            "hwId": hw_id,
            "machine": CLIENT_MACHINE,
            "OS": CLIENT_OS,
            "jgId": "",
        }
        result = await self._post("/codeLogin", payload)
        return EveccaSession.from_api(result)

    async def async_token_login(
        self,
        token: str,
        user_id: int,
        hw_id: str,
    ) -> EveccaSession:
        """Refresh an existing token session."""
        payload = {
            "token": token,
            "hwId": hw_id,
            "machine": CLIENT_MACHINE,
            "OS": CLIENT_OS,
        }
        result = await self._post(
            "/tokenLogin",
            payload,
            token=token,
            user_id=user_id,
        )
        return EveccaSession.from_api(result)

    async def async_families(self, session: EveccaSession) -> list[EveccaFamily]:
        """Return families available to the account."""
        result = await self._post(
            "/getFamilyList",
            None,
            token=session.token,
            user_id=session.user_id,
        )
        return [EveccaFamily.from_api(item) for item in result]

    async def async_devices(
        self,
        session: EveccaSession,
        family_id: int,
    ) -> list[EveccaDevice]:
        """Return devices in a family, including room names."""
        result = await self._post(
            "/getFamilyRoomDeviceList",
            {"fId": family_id},
            token=session.token,
            user_id=session.user_id,
        )
        devices: dict[int, EveccaDevice] = {}
        for room in result:
            if room.get("isGeneral"):
                continue
            room_name = room.get("rName")
            for device_data in room.get("devList", ()):
                device = EveccaDevice.from_api(device_data, room_name)
                devices[device.device_id] = device
        return list(devices.values())

    async def async_device_info(
        self,
        session: EveccaSession,
        family_id: int,
        device_id: int,
    ) -> EveccaDevice:
        """Return one device's current information."""
        result = await self._post(
            "/getDeviceInfo",
            {"fId": family_id, "devId": device_id},
            token=session.token,
            user_id=session.user_id,
        )
        return EveccaDevice.from_api(result)

    async def async_action(
        self,
        session: EveccaSession,
        family_id: int,
        device_id: int,
        value: int,
        *,
        dpid: int = DPID_ACTION,
    ) -> None:
        """Send a device command."""
        await self._post(
            "/actionDevice",
            {"fId": family_id, "devId": device_id, "dpid": dpid, "value": value},
            token=session.token,
            user_id=session.user_id,
        )

    async def _post(
        self,
        path: str,
        payload: Any = _UNSET,
        *,
        token: str | None = None,
        user_id: int | None = None,
    ) -> Any:
        """Post a JSON payload and return the API result."""
        headers = {
            "Accept": "application/json",
            "Authorization": self._authorization(token, user_id),
            "Content-Type": "application/json",
            "User-Agent": "ha-evecca/0.1",
        }
        request: dict[str, Any] = {"headers": headers}
        if payload is not _UNSET:
            request["data"] = json.dumps(payload, separators=(",", ":"))

        try:
            async with asyncio.timeout(REQUEST_TIMEOUT):
                response = await self._session.post(
                    f"{self._base_url}{path}",
                    **request,
                )
                body = await response.text()
        except (TimeoutError, ClientError) as err:
            raise EveccaConnectionError(f"Cannot reach EVECCA: {err}") from err

        self._raise_for_status(response, body)
        try:
            data = json.loads(body)
        except json.JSONDecodeError as err:
            raise EveccaApiError("EVECCA returned invalid JSON") from err
        return self._result(data)

    def _authorization(self, token: str | None, user_id: int | None) -> str:
        """Build EVECCA's base64 JSON Authorization header."""
        payload: dict[str, Any] = {"appId": self._app_id, "model": CLIENT_MODEL}
        if token is not None and user_id is not None:
            payload["token"] = token
            payload["userId"] = user_id
        raw = json.dumps(payload, separators=(",", ":")).encode()
        return base64.b64encode(raw).decode()

    @staticmethod
    def _raise_for_status(response: ClientResponse, body: str) -> None:
        """Raise a useful exception for transport-level API failures."""
        if response.status < 400:
            return
        if response.status in {401, 403}:
            raise EveccaAuthError(f"EVECCA authentication failed ({response.status})")
        raise EveccaApiError(
            f"EVECCA HTTP {response.status}: {body[:200]}",
            response.status,
        )

    @staticmethod
    def _result(data: dict[str, Any]) -> Any:
        """Return the result member or raise the API's domain error."""
        code = data.get("code")
        message = data.get("msg") or "EVECCA API error"
        if data.get("success") is True and code == 200:
            return data.get("result")
        if code in _AUTH_ERROR_CODES:
            raise EveccaAuthError(message)
        raise EveccaApiError(message, code)
