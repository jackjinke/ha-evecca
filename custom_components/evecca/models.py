"""Data models for the EVECCA cloud API."""

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Self

from .const import (
    LOCK_STATE_BY_RUN_VALUE,
    MODEL_LOCK_PREFIX,
    MODEL_WINDOW_PREFIX,
    WINDOW_MODE_BY_RUN_VALUE,
)


@dataclass(frozen=True, slots=True)
class EveccaMqttConfig:
    """MQTT connection details returned after login."""

    host: str
    port: int
    username: str
    password: str
    topic: str

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> Self:
        """Build MQTT configuration from an API object."""
        return cls(
            host=data["ip"],
            port=int(data["port"]),
            username=data["user"],
            password=data["pwd"],
            topic=str(data["topic"]),
        )


@dataclass(frozen=True, slots=True)
class EveccaSession:
    """Authenticated EVECCA account session."""

    token: str
    user_id: int
    mqtt: EveccaMqttConfig

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> Self:
        """Build a session from a login response."""
        return cls(
            token=data["token"],
            user_id=int(data["userId"]),
            mqtt=EveccaMqttConfig.from_api(data["mqtt"]),
        )


@dataclass(frozen=True, slots=True)
class EveccaFamily:
    """EVECCA family/home."""

    family_id: int
    name: str
    mqtt: EveccaMqttConfig

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> Self:
        """Build a family from an API object."""
        return cls(
            family_id=int(data["fId"]),
            name=data.get("fName") or str(data["fId"]),
            mqtt=EveccaMqttConfig.from_api(data["mqtt"]),
        )


@dataclass(frozen=True, slots=True)
class EveccaDevice:
    """One EVECCA controller, window actuator, or lock."""

    device_id: int
    family_id: int
    room_id: int | None
    parent_id: int | None
    name: str
    room_name: str | None
    model: str
    firmware: str | None
    position: int | None
    run_value: int | None
    lock_value: int | None
    window_mode: str | None
    locked: bool | None
    is_ready: bool
    online: bool | None
    actions: MappingProxyType[str, int]
    position_min: int
    position_max: int

    @classmethod
    def from_api(cls, data: dict[str, Any], room_name: str | None = None) -> Self:
        """Build a device from the family device-list response."""
        actions: dict[str, int] = {}
        position_min = 0
        position_max = 100

        for source in (data.get("actions"), data.get("directives")):
            for action in source or ():
                command = action.get("cmd")
                value = action.get("value")
                if isinstance(command, str) and isinstance(value, int):
                    actions[command] = value
                if command == "oper":
                    number_range = action.get("numData") or {}
                    position_min = int(number_range.get("numMin", position_min))
                    position_max = int(number_range.get("numMax", position_max))

        model = data.get("devModel") or "unknown"
        position = _optional_int(data.get("positionValue"))
        lock_value = _optional_int(data.get("lockValue"))
        parent_id = _optional_int(data.get("parentId"))

        run_value = _optional_int(data.get("runValue"))
        window_mode = None
        if model.startswith(MODEL_WINDOW_PREFIX):
            window_mode = WINDOW_MODE_BY_RUN_VALUE.get(run_value)

        locked = None
        if model.startswith(MODEL_LOCK_PREFIX):
            locked = LOCK_STATE_BY_RUN_VALUE.get(run_value)
            if locked is None and lock_value in (0, 1):
                locked = bool(lock_value)

        return cls(
            device_id=int(data["devId"]),
            family_id=int(data["fId"]),
            room_id=int(data["rId"]) if data.get("rId") is not None else None,
            parent_id=parent_id if parent_id else None,
            name=data.get("devName") or f"EVECCA {data['devId']}",
            room_name=room_name,
            model=model,
            firmware=data.get("romVer"),
            position=position,
            run_value=run_value,
            lock_value=lock_value,
            window_mode=window_mode,
            locked=locked,
            is_ready=bool(data.get("isReady")),
            online=None,
            actions=MappingProxyType(actions),
            position_min=position_min,
            position_max=position_max,
        )

    @property
    def available(self) -> bool:
        """Return whether the device can currently be controlled."""
        return self.is_ready and self.online is not False


def _optional_int(value: Any) -> int | None:
    """Convert optional numeric API values."""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
