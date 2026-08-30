"""Data models for the EVECCA cloud API."""

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Self


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
    """EVECCA window controller."""

    device_id: int
    family_id: int
    room_id: int | None
    name: str
    room_name: str | None
    model: str
    firmware: str | None
    position: int | None
    run_value: int | None
    lock_value: int | None
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

        for action in data.get("actions") or data.get("directives") or ():
            command = action.get("cmd")
            value = action.get("value")
            if isinstance(command, str) and isinstance(value, int):
                actions[command] = value
            if command == "oper":
                number_range = action.get("numData") or {}
                position_min = int(number_range.get("numMin", position_min))
                position_max = int(number_range.get("numMax", position_max))

        return cls(
            device_id=int(data["devId"]),
            family_id=int(data["fId"]),
            room_id=int(data["rId"]) if data.get("rId") is not None else None,
            name=data.get("devName") or f"EVECCA {data['devId']}",
            room_name=room_name,
            model=data.get("devModel") or "unknown",
            firmware=data.get("romVer"),
            position=_optional_int(data.get("positionValue")),
            run_value=_optional_int(data.get("runValue")),
            lock_value=_optional_int(data.get("lockValue")),
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
