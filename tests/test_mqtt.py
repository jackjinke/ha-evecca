"""Tests for EVECCA MQTT message parsing and subscription lifecycle."""

import asyncio
import json
from contextlib import asynccontextmanager
from dataclasses import replace
from importlib import import_module
from types import SimpleNamespace
from typing import Any

import aiomqtt
import pytest

from custom_components.evecca.const import MQTT_KEEPALIVE
from custom_components.evecca.models import EveccaMqttConfig
from custom_components.evecca.mqtt import (
    EveccaMqttClient,
    EveccaMqttUpdate,
    parse_mqtt_message,
)

mqtt_module = import_module("custom_components.evecca.mqtt")


def test_parse_online_message() -> None:
    """The observed retained online message updates availability."""
    payload = json.dumps(
        {
            "id": 1788039471,
            "method": "properties_changed",
            "params": [{"did": 87654321, "dpid": 50397298, "value": 1}],
            "sid": "30000001",
        }
    ).encode()

    update = parse_mqtt_message("12345678/87654321/online", payload, 12345678)

    assert update is not None
    assert update.device_id == 87654321
    assert update.online is True
    assert update.position is None


def test_parse_position_and_run_message() -> None:
    """Window position and run-state properties are normalized."""
    payload = json.dumps(
        {
            "method": "properties_changed",
            "params": [
                {"did": 87654321, "dpid": 50397285, "value": 54},
                {"did": 87654321, "dpid": 50397286, "value": 6},
            ],
        }
    ).encode()

    update = parse_mqtt_message("12345678/87654321/properties", payload, 12345678)

    assert update is not None
    assert update.position == 54
    assert update.run_value == 6


def test_parse_device_event_report() -> None:
    """Device event reports preserve the code for translated notifications."""
    payload = json.dumps(
        {
            "method": "event_report",
            "params": [{"did": 87654321, "dpid": 50462721, "value": 4404}],
        }
    ).encode()

    update = parse_mqtt_message("12345678/87654321/report", payload, 12345678)

    assert update is not None
    assert update.event_code == 4404


def test_parse_controller_function_property() -> None:
    """Controller normally-open/closed state is normalized."""
    payload = json.dumps(
        {
            "method": "properties_changed",
            "params": [{"did": 87654321, "dpid": 50397241, "value": 2}],
        }
    ).encode()

    update = parse_mqtt_message("12345678/87654321/report", payload, 12345678)

    assert update is not None
    assert update.normally_oc == 2


def test_ignore_other_families_and_invalid_payloads() -> None:
    """Malformed or unrelated MQTT messages are ignored."""
    assert parse_mqtt_message("1/87654321/online", b"{}", 12345678) is None
    assert parse_mqtt_message("12345678/87654321/online", b"not json", 12345678) is None


def test_ignore_non_utf8_and_non_object_payloads() -> None:
    """Malformed bytes and non-object JSON do not terminate the listener."""
    topic = "12345678/87654321/online"
    assert parse_mqtt_message(topic, b"\xff\xfe", 12345678) is None
    assert parse_mqtt_message(topic, b"[]", 12345678) is None
    assert (
        parse_mqtt_message(topic, b'{"method":"other","params":[]}', 12345678) is None
    )


class _BrokerConnection:
    """Controllable network lifecycle without real credentials or a broker."""

    def __init__(
        self,
        options: dict[str, Any],
        *,
        connect_gate: asyncio.Event | None = None,
        subscribe_gate: asyncio.Event | None = None,
        disconnect_gate: asyncio.Event | None = None,
        connect_error: aiomqtt.MqttError | None = None,
    ) -> None:
        self.options = options
        self.connect_gate = connect_gate
        self.subscribe_gate = subscribe_gate
        self.disconnect_gate = disconnect_gate
        self.connect_error = connect_error
        self.connecting = asyncio.Event()
        self.subscribing = asyncio.Event()
        self.subscribed = asyncio.Event()
        self.disconnecting = asyncio.Event()
        self.disconnected = asyncio.Event()
        self.connected = False
        self.topics: list[str] = []
        self.messages = self
        self.incoming: asyncio.Queue[Any] = asyncio.Queue()

    async def __aenter__(self):
        self.connected = True
        self.connecting.set()
        if self.connect_gate is not None:
            await self.connect_gate.wait()
        if self.connect_error is not None:
            raise self.connect_error
        return self

    async def __aexit__(self, *args):
        self.disconnecting.set()
        if self.disconnect_gate is not None:
            await self.disconnect_gate.wait()
        self.connected = False
        self.disconnected.set()

    async def subscribe(self, topic: str) -> None:
        self.topics.append(topic)
        self.subscribing.set()
        if self.subscribe_gate is not None:
            await self.subscribe_gate.wait()
        self.subscribed.set()

    def __aiter__(self):
        return self

    async def __anext__(self):
        item = await self.incoming.get()
        if isinstance(item, Exception):
            raise item
        return item

    def release(self) -> None:
        for gate in (self.connect_gate, self.subscribe_gate, self.disconnect_gate):
            if gate is not None:
                gate.set()


class _Broker:
    def __init__(self, plans: list[dict[str, Any]] | None = None) -> None:
        self.plans = plans or []
        self.connections: list[_BrokerConnection] = []
        self.attempts: asyncio.Queue[_BrokerConnection] = asyncio.Queue()

    def __call__(self, **options: Any) -> _BrokerConnection:
        index = len(self.connections)
        plan = self.plans[index] if index < len(self.plans) else {}
        connection = _BrokerConnection(options, **plan)
        self.connections.append(connection)
        self.attempts.put_nowait(connection)
        return connection

    async def next_attempt(self) -> _BrokerConnection:
        return await asyncio.wait_for(self.attempts.get(), 1)

    async def next_subscription(self) -> _BrokerConnection:
        connection = await self.next_attempt()
        await asyncio.wait_for(connection.subscribed.wait(), 1)
        assert connection.topics == ["12345678/#"]
        return connection


_CONFIG = EveccaMqttConfig(
    host="broker.example",
    port=8883,
    username="old-user",
    password="old-secret",
    topic="12345678",
)


@asynccontextmanager
async def _running(listener: EveccaMqttClient, broker: _Broker):
    existing_tasks = asyncio.all_tasks()
    task = asyncio.create_task(listener.run())
    try:
        yield task
    finally:
        for connection in broker.connections:
            connection.release()
        if not task.done():
            task.cancel()
        await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 1)
        assert all(not connection.connected for connection in broker.connections)
        assert asyncio.all_tasks() == existing_tasks


def _listener(updates: list[EveccaMqttUpdate]) -> EveccaMqttClient:
    return EveccaMqttClient(_CONFIG, 12345678, "ha-test", updates.append)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("host", "new-broker.example"),
        ("port", 8884),
        ("username", "new-user"),
        ("password", "new-secret"),
    ],
)
def test_rotated_connection_resubscribes_and_preserves_callback(
    monkeypatch: pytest.MonkeyPatch, field: str, value: str | int
) -> None:
    """Broker and credential renewals preserve the live listener and callback."""

    async def scenario() -> None:
        broker = _Broker()
        monkeypatch.setattr(mqtt_module.aiomqtt, "Client", broker)
        updates: list[EveccaMqttUpdate] = []
        received = asyncio.Event()

        def on_update(update: EveccaMqttUpdate) -> None:
            updates.append(update)
            received.set()

        listener = EveccaMqttClient(_CONFIG, 12345678, "ha-test", on_update)
        renewed = replace(_CONFIG, **{field: value})
        async with _running(listener, broker):
            old = await broker.next_subscription()
            listener.update_config(renewed)
            new = await broker.next_subscription()
            assert old.disconnected.is_set()
            option = "hostname" if field == "host" else field
            assert new.options[option] == value
            assert new.options["identifier"] == "ha-test"
            assert new.options["clean_session"] is False
            assert new.options["keepalive"] == MQTT_KEEPALIVE
            assert isinstance(new.options["tls_params"], aiomqtt.TLSParameters)
            await new.incoming.put(
                SimpleNamespace(
                    topic=SimpleNamespace(value="12345678/87654321/online"),
                    payload=b'{"params":[{"dpid":50397298,"value":1}]}',
                )
            )
            await asyncio.wait_for(received.wait(), 1)
            assert updates == [EveccaMqttUpdate(87654321, online=True)]

    asyncio.run(scenario())


def test_equal_config_does_not_reconnect(monkeypatch: pytest.MonkeyPatch) -> None:
    """Repeated equivalent login results do not churn MQTT sessions."""

    async def scenario() -> None:
        broker = _Broker()
        monkeypatch.setattr(mqtt_module.aiomqtt, "Client", broker)
        listener = _listener([])
        async with _running(listener, broker):
            original = await broker.next_subscription()
            listener.update_config(replace(_CONFIG))
            await asyncio.sleep(0.02)
            assert original.connected
            assert broker.attempts.empty()

    asyncio.run(scenario())


def test_rotation_before_start_uses_latest_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A renewal before task startup is not mistaken for a stale wakeup."""

    async def scenario() -> None:
        broker = _Broker()
        monkeypatch.setattr(mqtt_module.aiomqtt, "Client", broker)
        listener = _listener([])
        listener.update_config(replace(_CONFIG, password="new-secret"))
        async with _running(listener, broker):
            current = await broker.next_subscription()
            assert current.options["password"] == "new-secret"
            await asyncio.sleep(0.02)
            assert current.connected
            assert broker.attempts.empty()

    asyncio.run(scenario())


@pytest.mark.parametrize("phase", ["connect", "subscribe"])
def test_rotation_interrupts_pending_connection_work(
    monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    """Renewal cannot wait indefinitely for old connect or subscribe work."""

    async def scenario() -> None:
        gate = asyncio.Event()
        broker = _Broker([{f"{phase}_gate": gate}])
        monkeypatch.setattr(mqtt_module.aiomqtt, "Client", broker)
        listener = _listener([])
        async with _running(listener, broker):
            old = await broker.next_attempt()
            started = old.connecting if phase == "connect" else old.subscribing
            await asyncio.wait_for(started.wait(), 1)
            listener.update_config(replace(_CONFIG, password="new-secret"))
            new = await broker.next_subscription()
            assert old.disconnected.is_set()
            assert new.options["password"] == "new-secret"
            assert not gate.is_set()

    asyncio.run(scenario())


def test_rotation_during_disconnect_keeps_latest_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A second renewal during teardown is consumed by the next connection."""

    async def scenario() -> None:
        gate = asyncio.Event()
        broker = _Broker([{"disconnect_gate": gate}])
        monkeypatch.setattr(mqtt_module.aiomqtt, "Client", broker)
        listener = _listener([])
        async with _running(listener, broker):
            old = await broker.next_subscription()
            listener.update_config(replace(_CONFIG, password="intermediate"))
            await asyncio.wait_for(old.disconnecting.wait(), 1)
            listener.update_config(replace(_CONFIG, password="latest"))
            gate.set()
            new = await broker.next_subscription()
            assert new.options["password"] == "latest"
            await asyncio.sleep(0.02)
            assert new.connected
            assert broker.attempts.empty()

    asyncio.run(scenario())


@pytest.mark.parametrize("during_backoff", [False, True])
def test_rotation_wakes_failed_connection_without_backoff(
    monkeypatch: pytest.MonkeyPatch, during_backoff: bool
) -> None:
    """Renewal at failure or during the retry delay reconnects immediately."""

    async def scenario() -> None:
        broker = _Broker()
        monkeypatch.setattr(mqtt_module.aiomqtt, "Client", broker)
        listener = _listener([])
        async with _running(listener, broker):
            old = await broker.next_subscription()
            await old.incoming.put(aiomqtt.MqttError("connection lost"))
            await asyncio.wait_for(old.disconnected.wait(), 1)
            if during_backoff:
                await asyncio.sleep(0.02)
            listener.update_config(replace(_CONFIG, password="new-secret"))
            new = await broker.next_subscription()
            assert new.options["password"] == "new-secret"

    asyncio.run(scenario())


def test_network_retry_creates_fresh_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A normal network retry opens a new client and resubscribes."""

    async def scenario() -> None:
        broker = _Broker()
        monkeypatch.setattr(mqtt_module.aiomqtt, "Client", broker)
        monkeypatch.setattr(mqtt_module, "_RECONNECT_DELAY", 0.01)
        async with _running(_listener([]), broker):
            old = await broker.next_subscription()
            await old.incoming.put(aiomqtt.MqttError("connection lost"))
            new = await broker.next_subscription()
            assert old.disconnected.is_set()
            assert new.options["password"] == _CONFIG.password

    asyncio.run(scenario())


@pytest.mark.parametrize("phase", ["connect", "subscribe", "receive", "backoff"])
def test_cancellation_disconnects_and_joins_pending_work(
    monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    """Stopping at any lifecycle phase closes sockets and leaves no live tasks."""

    async def scenario() -> None:
        plans = (
            [{f"{phase}_gate": asyncio.Event()}]
            if phase in ("connect", "subscribe")
            else []
        )
        broker = _Broker(plans)
        monkeypatch.setattr(mqtt_module.aiomqtt, "Client", broker)
        async with _running(_listener([]), broker) as task:
            current = await broker.next_attempt()
            started = current.connecting if phase == "connect" else current.subscribing
            await asyncio.wait_for(started.wait(), 1)
            if phase == "backoff":
                await current.incoming.put(aiomqtt.MqttError("connection lost"))
                await asyncio.wait_for(current.disconnected.wait(), 1)
                await asyncio.sleep(0.02)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 1)
            assert current.disconnected.is_set()

    asyncio.run(scenario())


@pytest.mark.parametrize("cancel_twice", [False, True])
def test_cancellation_during_rotation_waits_for_disconnect(
    monkeypatch: pytest.MonkeyPatch, cancel_twice: bool
) -> None:
    """Stopping during a rotation does not cancel the socket cleanup itself."""

    async def scenario() -> None:
        gate = asyncio.Event()
        broker = _Broker([{"disconnect_gate": gate}])
        monkeypatch.setattr(mqtt_module.aiomqtt, "Client", broker)
        listener = _listener([])
        async with _running(listener, broker) as task:
            current = await broker.next_subscription()
            listener.update_config(replace(_CONFIG, password="new-secret"))
            await asyncio.wait_for(current.disconnecting.wait(), 1)
            task.cancel()
            await asyncio.sleep(0.02)
            if cancel_twice:
                task.cancel()
                await asyncio.sleep(0.02)
            assert not task.done()
            assert current.connected
            gate.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 1)
            assert current.disconnected.is_set()
            assert broker.attempts.empty()

    asyncio.run(scenario())


def test_callback_failure_disconnects_and_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unexpected callback failures stay visible and still close the connection."""

    async def scenario() -> None:
        broker = _Broker()
        monkeypatch.setattr(mqtt_module.aiomqtt, "Client", broker)

        def fail(update: EveccaMqttUpdate) -> None:
            raise ValueError("callback failed")

        listener = EveccaMqttClient(_CONFIG, 12345678, "ha-test", fail)
        async with _running(listener, broker) as task:
            current = await broker.next_subscription()
            await current.incoming.put(
                SimpleNamespace(
                    topic=SimpleNamespace(value="12345678/87654321/online"),
                    payload=b'{"params":[{"dpid":50397298,"value":1}]}',
                )
            )
            with pytest.raises(ValueError, match="callback failed"):
                await asyncio.wait_for(task, 1)
            assert current.disconnected.is_set()

    asyncio.run(scenario())


def test_failed_connect_disconnects_before_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed context entry cannot leave a partially opened socket behind."""

    async def scenario() -> None:
        broker = _Broker([{"connect_error": aiomqtt.MqttError("connection rejected")}])
        monkeypatch.setattr(mqtt_module.aiomqtt, "Client", broker)
        monkeypatch.setattr(mqtt_module, "_RECONNECT_DELAY", 0.01)
        async with _running(_listener([]), broker):
            failed = await broker.next_attempt()
            await broker.next_subscription()
            assert failed.disconnected.is_set()

    asyncio.run(scenario())
