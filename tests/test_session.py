"""Session renewal behavior against a local EVECCA HTTP service."""

import asyncio
import base64
import json
import socket
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Any

import pytest
from aiohttp import ClientSession, web

from custom_components.evecca import api as api_module
from custom_components.evecca import session as session_module
from custom_components.evecca.api import (
    EveccaApi,
    EveccaApiError,
    EveccaAuthError,
    EveccaConnectionError,
)
from custom_components.evecca.models import EveccaSession
from custom_components.evecca.session import EveccaSessionManager


class Cloud:
    """Local protocol endpoint with controlled renewal and action outcomes."""

    def __init__(self, *, same_token: bool = False) -> None:
        self.saved = {
            "token": "saved-token",
            "userId": 123,
            "mqtt": {
                "ip": "old-broker",
                "port": 1883,
                "user": "old-user",
                "pwd": "old-password",
                "topic": "old-topic",
            },
        }
        self.renewed = {
            "token": "saved-token" if same_token else "renewed-token",
            "userId": 456,
            "mqtt": {
                "ip": "new-broker",
                "port": 2883,
                "user": "new-user",
                "pwd": "new-password",
                "topic": "new-topic",
            },
        }
        self.requests: list[tuple[str, dict[str, Any], Any]] = []
        self.login_started = asyncio.Event()
        self.login_gate: asyncio.Event | None = None
        self.login_error: tuple[int, str] | None = None
        self.read_count = 0
        self.reject_initial = 0
        self.initial_arrived = asyncio.Event()
        self.persisted = asyncio.Event()
        self.action_started = asyncio.Event()
        self.action_gate: asyncio.Event | None = None
        self.action_error: str | None = None
        self.always_reject = False

    async def handle(self, request: web.Request) -> web.Response:
        auth = json.loads(base64.b64decode(request.headers["Authorization"]))
        payload = await request.json()
        self.requests.append((request.path, auth, payload))
        if request.path == "/tokenLogin":
            self.login_started.set()
            if self.login_gate is not None:
                await self.login_gate.wait()
            if self.login_error is not None:
                code, message = self.login_error
                return web.json_response(
                    {"success": False, "code": code, "msg": message}
                )
            result = self.renewed
        elif request.path == "/getErrs":
            self.read_count += 1
            ordinal = self.read_count
            if ordinal <= self.reject_initial:
                if ordinal == self.reject_initial:
                    self.initial_arrived.set()
                await self.initial_arrived.wait()
                # One rejection arrives only after renewal has been committed.
                if ordinal == self.reject_initial and self.reject_initial > 1:
                    await self.persisted.wait()
            if ordinal <= self.reject_initial or self.always_reject:
                return web.json_response(
                    {"success": False, "code": 99, "msg": "operation rejected"}
                )
            result = {"ver": auth["token"], "codes": {}}
        else:
            self.action_started.set()
            if self.action_gate is not None:
                await self.action_gate.wait()
            if self.action_error == "connection":
                assert request.transport is not None
                request.transport.close()
                return web.Response()
            if self.action_error == "domain":
                return web.json_response(
                    {"success": False, "code": 4203, "msg": "device busy"}
                )
            result = {}
        return web.json_response({"success": True, "code": 200, "result": result})

    def count(self, path: str) -> int:
        return sum(request[0] == path for request in self.requests)


@asynccontextmanager
async def local_api(cloud: Cloud):
    """Run the real client over an ephemeral loopback socket."""
    app = web.Application()
    app.router.add_post("/{endpoint}", cloud.handle)
    runner = web.AppRunner(app)
    await runner.setup()
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.setblocking(False)
    port = sock.getsockname()[1]
    site = web.SockSite(runner, sock)
    await site.start()
    try:
        async with ClientSession() as client:
            yield EveccaApi(client, base_url=f"http://127.0.0.1:{port}")
    finally:
        if cloud.login_gate is not None:
            cloud.login_gate.set()
        if cloud.action_gate is not None:
            cloud.action_gate.set()
        cloud.initial_arrived.set()
        cloud.persisted.set()
        await runner.cleanup()


def make_manager(api: EveccaApi, cloud: Cloud):
    """Capture the synchronous persistence contract, including publish order."""
    initial = EveccaSession.from_api(cloud.saved)
    saved = [asdict(initial)]
    callback_sessions = []
    manager = None

    def persist(refreshed: EveccaSession) -> None:
        assert manager.session is not refreshed
        saved[0] = asdict(refreshed)
        callback_sessions.append(refreshed)
        cloud.persisted.set()

    manager = EveccaSessionManager(api, initial, "hardware-id", persist)
    return manager, saved, callback_sessions


def test_refreshes_on_next_operation_after_six_hours(monkeypatch) -> None:
    """No background work occurs; the first due operation renews before use."""
    clock = [100.0]
    monkeypatch.setattr(session_module, "monotonic", lambda: clock[0])

    async def scenario():
        cloud = Cloud()
        async with local_api(cloud) as api:
            manager, saved, callbacks = make_manager(api, cloud)
            clock[0] += 6 * 60 * 60 - 1
            assert (
                await manager.async_call(api.async_error_codes)
            ).version == "saved-token"
            assert cloud.count("/tokenLogin") == 0
            clock[0] += 1
            assert cloud.count("/tokenLogin") == 0
            result = await manager.async_call(api.async_error_codes)
            assert result.version == "renewed-token"
            assert saved[0] == asdict(EveccaSession.from_api(cloud.renewed))
            assert callbacks == [manager.session]
            login = next(item for item in cloud.requests if item[0] == "/tokenLogin")
            assert login[1]["token"] == "saved-token"
            assert login[1]["userId"] == 123
            assert login[2]["hwId"] == "hardware-id"
            assert login[2]["token"] == "saved-token"
            clock[0] += 6 * 60 * 60 - 1
            await manager.async_call(api.async_error_codes)
            assert cloud.count("/tokenLogin") == 1

    asyncio.run(scenario())


def test_explicit_refresh_persists_rotated_credentials_before_use() -> None:
    """Every explicit renewal publishes all account and MQTT credentials."""

    async def scenario():
        cloud = Cloud()
        async with local_api(cloud) as api:
            manager, saved, callbacks = make_manager(api, cloud)
            refreshed = await manager.async_refresh()
            assert refreshed is manager.session
            assert refreshed.user_id == 456
            assert refreshed.mqtt.password == "new-password"
            assert saved[0] == asdict(refreshed)
            await manager.async_call(api.async_action, 1, 2, 3, dpid=4)
            action = cloud.requests[-1]
            assert action[1]["token"] == "renewed-token"
            assert action[1]["userId"] == 456
            assert action[2] == {"fId": 1, "devId": 2, "dpid": 4, "value": 3}
            await manager.async_refresh()
            assert len(callbacks) == 2
            assert cloud.count("/tokenLogin") == 2

    asyncio.run(scenario())


@pytest.mark.parametrize("same_token", [False, True])
def test_concurrent_due_operations_share_one_refresh(monkeypatch, same_token) -> None:
    """Concurrent proactive renewal is coalesced even without token rotation."""
    clock = [100.0]
    monkeypatch.setattr(session_module, "monotonic", lambda: clock[0])

    async def scenario():
        cloud = Cloud(same_token=same_token)
        cloud.login_gate = asyncio.Event()
        async with local_api(cloud) as api:
            manager, _, callbacks = make_manager(api, cloud)
            clock[0] += 6 * 60 * 60
            tasks = [
                asyncio.create_task(manager.async_call(api.async_error_codes))
                for _ in range(8)
            ]
            await cloud.login_started.wait()
            assert cloud.count("/getErrs") == 0
            cloud.login_gate.set()
            results = await asyncio.gather(*tasks)
            assert [item.version for item in results] == [cloud.renewed["token"]] * 8
            assert cloud.count("/tokenLogin") == 1
            assert cloud.count("/getErrs") == 8
            assert len(callbacks) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("same_token", [False, True])
def test_concurrent_stale_rejections_share_one_refresh(same_token) -> None:
    """Late rejection of an old generation cannot renew an already renewed session."""

    async def scenario():
        cloud = Cloud(same_token=same_token)
        cloud.reject_initial = 8
        async with local_api(cloud) as api:
            manager, _, callbacks = make_manager(api, cloud)
            results = await asyncio.gather(
                *(manager.async_call(api.async_error_codes) for _ in range(8))
            )
            assert [item.version for item in results] == [cloud.renewed["token"]] * 8
            assert cloud.count("/tokenLogin") == 1
            assert cloud.count("/getErrs") == 16
            assert len(callbacks) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("code", "error_type"), [(99, EveccaAuthError), (503, EveccaApiError)]
)
@pytest.mark.parametrize("trigger", ["explicit", "proactive", "rejection"])
def test_failed_refresh_preserves_saved_and_active_session(
    monkeypatch, code, error_type, trigger
) -> None:
    """A failed login is surfaced, never published or mistaken for action success."""
    clock = [100.0]
    monkeypatch.setattr(session_module, "monotonic", lambda: clock[0])

    async def scenario():
        cloud = Cloud()
        cloud.login_error = (code, "renewal failed")
        if trigger == "rejection":
            cloud.reject_initial = 1
        async with local_api(cloud) as api:
            manager, saved, callbacks = make_manager(api, cloud)
            original = manager.session
            if trigger == "proactive":
                clock[0] += 6 * 60 * 60
            with pytest.raises(error_type, match="renewal failed"):
                if trigger == "explicit":
                    await manager.async_refresh()
                else:
                    await manager.async_call(api.async_error_codes)
            assert manager.session is original
            assert saved[0] == asdict(original)
            assert callbacks == []
            assert cloud.count("/tokenLogin") == 1
            assert cloud.count("/getErrs") == (trigger == "rejection")
            cloud.login_error = None
            await manager.async_refresh()
            assert manager.session is not original

    asyncio.run(scenario())


def test_second_auth_rejection_is_not_retried() -> None:
    """One operation gets at most one renewal and one replay."""

    async def scenario():
        cloud = Cloud()
        cloud.always_reject = True
        async with local_api(cloud) as api:
            manager, _, callbacks = make_manager(api, cloud)
            with pytest.raises(EveccaAuthError, match="operation rejected"):
                await manager.async_call(api.async_error_codes)
            assert cloud.count("/tokenLogin") == 1
            assert cloud.count("/getErrs") == 2
            assert len(callbacks) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["connection", "timeout", "domain"])
def test_ambiguous_action_failures_are_never_replayed(monkeypatch, failure) -> None:
    """Only definitive authentication rejection permits command replay."""
    if failure == "timeout":
        monkeypatch.setattr(api_module, "REQUEST_TIMEOUT", 0.05)

    async def scenario():
        cloud = Cloud()
        cloud.action_error = failure
        if failure == "timeout":
            cloud.action_gate = asyncio.Event()
        async with local_api(cloud) as api:
            manager, _, callbacks = make_manager(api, cloud)
            error_type = (
                EveccaApiError if failure == "domain" else EveccaConnectionError
            )
            with pytest.raises(error_type):
                await manager.async_call(api.async_action, 1, 2, 3)
            assert cloud.count("/actionDevice") == 1
            assert cloud.count("/tokenLogin") == 0
            assert callbacks == []

    asyncio.run(scenario())


@pytest.mark.parametrize("cancel_waiter", [False, True])
def test_cancellation_does_not_publish_or_strand_refresh_lock(cancel_waiter) -> None:
    """Cancelling a renewal owner or waiter leaves the manager usable."""

    async def scenario():
        cloud = Cloud()
        cloud.login_gate = asyncio.Event()
        async with local_api(cloud) as api:
            manager, saved, callbacks = make_manager(api, cloud)
            original = manager.session
            owner = asyncio.create_task(manager.async_refresh())
            await cloud.login_started.wait()
            if cancel_waiter:
                cancelled = asyncio.create_task(manager.async_refresh())
                await asyncio.sleep(0)
            else:
                cancelled = owner
            cancelled.cancel()
            with pytest.raises(asyncio.CancelledError):
                await cancelled
            assert manager.session is original
            assert saved[0] == asdict(original)
            assert callbacks == []
            cloud.login_gate.set()
            if cancel_waiter:
                await owner
                assert cloud.count("/tokenLogin") == 1
            else:
                await manager.async_refresh()
                assert cloud.count("/tokenLogin") == 2
            assert len(callbacks) == 1
            assert (
                await manager.async_call(api.async_error_codes)
            ).version == "renewed-token"

    asyncio.run(scenario())


def test_cancelled_action_is_not_replayed() -> None:
    """Caller cancellation remains cancellation, with no refresh or replay."""

    async def scenario():
        cloud = Cloud()
        cloud.action_gate = asyncio.Event()
        async with local_api(cloud) as api:
            manager, _, callbacks = make_manager(api, cloud)
            task = asyncio.create_task(manager.async_call(api.async_action, 1, 2, 3))
            await cloud.action_started.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert cloud.count("/actionDevice") == 1
            assert cloud.count("/tokenLogin") == 0
            assert callbacks == []

    asyncio.run(scenario())


def test_callback_failure_keeps_active_session_and_propagates_same_exception() -> None:
    """The synchronous callback must finish before the new session is visible."""

    async def scenario():
        cloud = Cloud()
        async with local_api(cloud) as api:
            original = EveccaSession.from_api(cloud.saved)
            failure = RuntimeError("cannot persist")

            def persist(refreshed):
                assert manager.session is original
                raise failure

            manager = EveccaSessionManager(api, original, "hardware-id", persist)
            with pytest.raises(RuntimeError) as raised:
                await manager.async_refresh()
            assert raised.value is failure
            assert manager.session is original

    asyncio.run(scenario())


def test_scheduled_renewal_only_runs_when_due(monkeypatch) -> None:
    """An external timer can check age even when authenticated operations stop."""
    clock = [100.0]
    monkeypatch.setattr(session_module, "monotonic", lambda: clock[0])

    async def scenario():
        cloud = Cloud()
        async with local_api(cloud) as api:
            manager, _, callbacks = make_manager(api, cloud)
            original = manager.session
            clock[0] += 6 * 60 * 60 - 1
            assert await manager.async_renew_if_due() is original
            assert cloud.requests == []
            clock[0] += 1
            assert await manager.async_renew_if_due() is manager.session
            assert manager.session is not original
            assert cloud.count("/tokenLogin") == 1
            assert len(callbacks) == 1
            clock[0] += 6 * 60 * 60 - 1
            await manager.async_renew_if_due()
            assert cloud.count("/tokenLogin") == 1

    asyncio.run(scenario())


def test_concurrent_failed_renewals_share_failure_and_allow_later_retry(
    monkeypatch,
) -> None:
    """A failed overlapping renewal is not a serial login storm."""
    clock = [100.0]
    monkeypatch.setattr(session_module, "monotonic", lambda: clock[0])

    async def scenario():
        cloud = Cloud()
        cloud.login_gate = asyncio.Event()
        cloud.login_error = (503, "renewal unavailable")
        async with local_api(cloud) as api:
            manager, saved, callbacks = make_manager(api, cloud)
            original = manager.session
            clock[0] += 6 * 60 * 60
            tasks = [
                asyncio.create_task(manager.async_call(api.async_error_codes))
                for _ in range(8)
            ]
            await cloud.login_started.wait()
            cloud.login_gate.set()
            results = await asyncio.gather(*tasks, return_exceptions=True)
            assert all(isinstance(result, EveccaApiError) for result in results)
            assert all(result is results[0] for result in results)
            assert cloud.count("/tokenLogin") == 1
            assert cloud.count("/getErrs") == 0
            assert manager.session is original
            assert saved[0] == asdict(original)
            assert callbacks == []
            cloud.login_error = None
            await manager.async_renew_if_due()
            assert cloud.count("/tokenLogin") == 2
            assert len(callbacks) == 1

    asyncio.run(scenario())
