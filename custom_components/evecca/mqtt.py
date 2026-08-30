"""MQTT status channel for EVECCA devices."""

import asyncio
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import aiomqtt

from .const import (
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

    async def run(self) -> None:
        """Reconnect and forward MQTT messages until cancelled."""
        client = aiomqtt.Client(
            hostname=self._config.host,
            port=self._config.port,
            username=self._config.username,
            password=self._config.password,
            tls_params=aiomqtt.TLSParameters(),
            identifier=self._client_id,
            clean_session=False,
            keepalive=MQTT_KEEPALIVE,
        )
        while True:
            try:
                async with client:
                    await client.subscribe(f"{self._family_id}/#")
                    async for message in client.messages:
                        update = parse_mqtt_message(
                            message.topic.value,
                            message.payload,
                            self._family_id,
                        )
                        if update is not None:
                            self._on_update(update)
            except asyncio.CancelledError:
                raise
            except aiomqtt.MqttError as err:
                _LOGGER.debug("EVECCA MQTT connection lost: %s", err)
                await asyncio.sleep(_RECONNECT_DELAY)


@dataclass(frozen=True, slots=True)
class EveccaMqttUpdate:
    """Normalized state fields from one MQTT message."""

    device_id: int
    position: int | None = None
    run_value: int | None = None
    online: bool | None = None


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
    if method is not None and method != "properties_changed":
        return None

    params = data.get("params")
    if not isinstance(params, list):
        return None

    position: int | None = None
    run_value: int | None = None
    online: bool | None = None
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

    if position is None and run_value is None and online is None:
        return None
    return EveccaMqttUpdate(
        device_id,
        position=position,
        run_value=run_value,
        online=online,
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
