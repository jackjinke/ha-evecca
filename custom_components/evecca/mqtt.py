"""MQTT status channel for EVECCA devices."""

import asyncio
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import aiomqtt

from .const import (
    DPID_EVENT,
    DPID_NORMALLY_OC,
    DPID_ONLINE,
    DPID_POSITION_STATE,
    DPID_RUN_STATE,
    MQTT_KEEPALIVE,
)
from .models import EveccaMqttConfig

_LOGGER = logging.getLogger(__name__)

_RECONNECT_DELAY = 5


class EveccaMqttClient:
    """Maintain a resilient MQTT subscription for one EVECCA family."""

    def __init__(
        self,
        config: EveccaMqttConfig,
        family_id: int,
        client_id: str,
        on_update: Callable[["EveccaMqttUpdate"], None],
    ) -> None:
        """Initialize the MQTT status client."""
        self._config = config
        self._family_id = family_id
        self._client_id = client_id
        self._on_update = on_update
        self._config_changed = asyncio.Event()

    def update_config(self, config: EveccaMqttConfig) -> None:
        """Replace credentials on the HA event loop and wake the running listener."""
        if config == self._config:
            return
        self._config = config
        self._config_changed.set()

    async def run(self) -> None:
        """Reconnect and forward MQTT messages until cancelled."""
        while True:
            # Snapshot and clear without yielding, so renewals cannot lose their wakeup.
            self._config_changed.clear()
            connection = asyncio.create_task(self._run_connection(self._config))
            changed = asyncio.create_task(self._config_changed.wait())
            try:
                await asyncio.wait(
                    (connection, changed), return_when=asyncio.FIRST_COMPLETED
                )
            finally:
                await _cancel_and_wait(connection, changed)

            if connection.cancelled():
                continue
            try:
                connection.result()
            except aiomqtt.MqttError as err:
                _LOGGER.debug("EVECCA MQTT connection lost: %s", err)
                try:
                    async with asyncio.timeout(_RECONNECT_DELAY):
                        await self._config_changed.wait()
                except TimeoutError:
                    pass

    async def _run_connection(self, config: EveccaMqttConfig) -> None:
        """Open one subscription using the credentials for this attempt."""
        client = aiomqtt.Client(
            hostname=config.host,
            port=config.port,
            username=config.username,
            password=config.password,
            tls_params=aiomqtt.TLSParameters(),
            identifier=self._client_id,
            clean_session=False,
            keepalive=MQTT_KEEPALIVE,
        )
        # aiomqtt does not clean up a partially opened connection if entry is cancelled.
        try:
            await client.__aenter__()
            await client.subscribe(f"{self._family_id}/#")
            async for message in client.messages:
                update = parse_mqtt_message(
                    message.topic.value,
                    message.payload,
                    self._family_id,
                )
                if update is not None:
                    self._on_update(update)
        finally:
            await client.__aexit__(None, None, None)


async def _cancel_and_wait(*tasks: asyncio.Task[Any]) -> None:
    """Join cancelled work without letting repeated cancellation abort disconnect."""
    for task in tasks:
        if not task.done() and not task.cancelling():
            task.cancel()
    joined = asyncio.gather(*tasks, return_exceptions=True)
    cancelled = False
    while not joined.done():
        try:
            await asyncio.shield(joined)
        except asyncio.CancelledError:
            cancelled = True
    if cancelled:
        raise asyncio.CancelledError


@dataclass(frozen=True, slots=True)
class EveccaMqttUpdate:
    """Normalized state fields from one MQTT message."""

    device_id: int
    position: int | None = None
    run_value: int | None = None
    online: bool | None = None
    normally_oc: int | None = None
    event_code: int | None = None


def parse_mqtt_message(
    topic: str,
    payload: bytes,
    family_id: int,
) -> EveccaMqttUpdate | None:
    """Parse an EVECCA MQTT properties_changed message."""
    parts = topic.split("/")
    if len(parts) < 3 or parts[0] != str(family_id):
        return None
    try:
        device_id = int(parts[1])
        data = json.loads(payload)
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return None

    if not isinstance(data, dict):
        return None
    method = data.get("method")
    if method is not None and method not in ("properties_changed", "event_report"):
        return None

    params = data.get("params")
    if not isinstance(params, list):
        return None

    position: int | None = None
    run_value: int | None = None
    online: bool | None = None
    normally_oc: int | None = None
    event_code: int | None = None
    for param in params:
        if not isinstance(param, dict):
            continue
        did = _optional_int(param.get("did"))
        if did is not None and did != device_id:
            continue
        value = _optional_int(param.get("value"))
        dpid = _optional_int(param.get("dpid"))
        if value is None or dpid is None:
            continue
        if dpid == DPID_POSITION_STATE:
            position = value
        elif dpid == DPID_RUN_STATE:
            run_value = value
        elif dpid == DPID_ONLINE:
            online = value == 1
        elif dpid == DPID_NORMALLY_OC:
            normally_oc = value
        elif dpid == DPID_EVENT:
            event_code = value

    if (
        position is None
        and run_value is None
        and online is None
        and normally_oc is None
        and event_code is None
    ):
        return None
    return EveccaMqttUpdate(
        device_id,
        position=position,
        run_value=run_value,
        online=online,
        normally_oc=normally_oc,
        event_code=event_code,
    )


def _optional_int(value: Any) -> int | None:
    """Convert MQTT numeric values."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None
