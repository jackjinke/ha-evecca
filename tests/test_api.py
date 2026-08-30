"""Tests for the EVECCA HTTPS API client."""

import asyncio
import base64
import hashlib
import json
from typing import Any

import pytest

from custom_components.evecca.api import (
    EveccaApi,
    EveccaApiError,
    EveccaAuthError,
)


class FakeResponse:
    """Minimal aiohttp response test double."""

    status = 200

    def __init__(self, body: dict[str, Any]) -> None:
        """Initialize the response."""
        self._body = body

    async def text(self) -> str:
        """Return the JSON response body."""
        return json.dumps(self._body)


class FakeSession:
    """Minimal aiohttp session test double."""

    def __init__(self, body: dict[str, Any]) -> None:
        """Initialize the session."""
        self.body = body
        self.requests: list[dict[str, Any]] = []

    async def post(self, url: str, **kwargs: Any) -> FakeResponse:
        """Capture a POST request."""
        self.requests.append({"url": url, **kwargs})
        return FakeResponse(self.body)


def test_authorization_header_is_compact_base64_json() -> None:
    """Authorization matches the captured EVECCA app format."""
    api = EveccaApi(FakeSession({"success": True, "code": 200, "result": {}}))

    encoded = api._authorization("token-value", 12345)

    assert base64.b64decode(encoded).decode() == (
        '{"appId":50774420,"model":"ios","token":"token-value","userId":12345}'
    )


def test_password_login_posts_md5_and_returns_session() -> None:
    """Password login uses the captured API contract."""
    session = FakeSession(
        {
            "success": True,
            "code": 200,
            "result": {
                "token": "token-value",
                "userId": 12345,
                "mqtt": {
                    "ip": "mqtt2.evecca.cn",
                    "port": 5623,
                    "user": "mqtt-user",
                    "pwd": "mqtt-password",
                    "topic": "session-topic",
                },
            },
        }
    )
    api = EveccaApi(session)

    result = asyncio.run(
        api.async_password_login("13800000000", "correct horse", "hardware-id")
    )

    request = session.requests[0]
    payload = json.loads(request["data"])
    assert request["url"].endswith("/pwdLogin")
    assert payload["pwdMd5"] == hashlib.md5(b"correct horse").hexdigest()
    assert payload["hwId"] == "hardware-id"
    assert result.token == "token-value"
    assert result.user_id == 12345
    assert result.mqtt.host == "mqtt2.evecca.cn"


def test_token_failure_maps_to_auth_error() -> None:
    """Expired tokens trigger Home Assistant reauthentication."""
    with pytest.raises(EveccaAuthError):
        EveccaApi._result({"success": False, "code": 99, "msg": "Token失效"})


def test_domain_error_keeps_code() -> None:
    """Other API errors keep the EVECCA error code."""
    with pytest.raises(EveccaApiError, match="设备忙") as err:
        EveccaApi._result({"success": False, "code": 4203, "msg": "设备忙"})
    assert err.value.code == 4203
