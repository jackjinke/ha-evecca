"""Tests for the EVECCA HTTPS API client."""

import asyncio
import base64
import hashlib
import json
from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.evecca.api import (
    EveccaApi,
    EveccaApiError,
    EveccaAuthError,
    async_discover_base_url,
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

    async def get(self, url: str, **kwargs: Any) -> FakeResponse:
        """Capture a GET request."""
        self.requests.append({"url": url, **kwargs})
        return FakeResponse(self.body)


def test_discovers_current_api_endpoint() -> None:
    """Startup discovers the same service endpoint as the official app."""
    session = FakeSession(
        {
            "success": True,
            "result": {"list": ["https://whaleapp.evecca.cn:5707/test_v002"]},
        }
    )

    endpoint = asyncio.run(async_discover_base_url(session))

    assert endpoint == "https://whaleapp.evecca.cn:5707/test_v002"
    assert session.requests[0]["url"].endswith("/serList")


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://attacker.example/test_v002",
        "https://evecca.cn@attacker.example/test_v002",
        "https://whaleapp.evecca.cn/test_v002?redirect=attacker",
    ],
)
def test_rejects_untrusted_api_endpoints(endpoint: str) -> None:
    """Discovery cannot redirect credentials outside an EVECCA origin."""
    session = FakeSession({"success": True, "result": {"list": [endpoint]}})

    assert asyncio.run(async_discover_base_url(session)) is None


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


def test_reads_error_catalog_from_api() -> None:
    """The app error catalog is parsed into numeric device event codes."""
    session = FakeSession(
        {
            "success": True,
            "code": 200,
            "result": {
                "ver": "1",
                "codes": {"4404": "解锁失败", "4634": "需要手动复位"},
            },
        }
    )
    api = EveccaApi(session)
    auth = SimpleNamespace(token="token-value", user_id=12345)

    catalog = asyncio.run(api.async_error_codes(auth))

    assert catalog.version == "1"
    assert catalog.codes[4404] == "解锁失败"
    assert session.requests[0]["url"].endswith("/getErrs")


def test_set_property_uses_captured_endpoint() -> None:
    """Controller functions use the captured setProperties contract."""
    session = FakeSession({"success": True, "code": 200, "result": {}})
    api = EveccaApi(session)
    auth = SimpleNamespace(token="token-value", user_id=12345)

    asyncio.run(
        api.async_set_property(
            auth,
            58639119,
            49845623,
            2,
            dpid=50397241,
        )
    )

    request = session.requests[0]
    assert request["url"].endswith("/setProperties")
    assert json.loads(request["data"]) == {
        "fId": 58639119,
        "devId": 49845623,
        "dpid": 50397241,
        "value": 2,
    }


def test_token_failure_maps_to_auth_error() -> None:
    """Expired tokens trigger Home Assistant reauthentication."""
    with pytest.raises(EveccaAuthError):
        EveccaApi._result({"success": False, "code": 99, "msg": "Token失效"})


def test_domain_error_keeps_code() -> None:
    """Other API errors keep the EVECCA error code."""
    with pytest.raises(EveccaApiError, match="设备忙") as err:
        EveccaApi._result({"success": False, "code": 4203, "msg": "设备忙"})
    assert err.value.code == 4203
