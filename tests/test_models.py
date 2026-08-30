"""Tests for EVECCA API models."""

from custom_components.evecca.const import WINDOW_MODE_OPEN
from custom_components.evecca.models import EveccaDevice


def test_window_device_parses_commands_and_state() -> None:
    """The captured window metadata becomes a cover-capable model."""
    device = EveccaDevice.from_api(
        {
            "devId": 87654321,
            "fId": 12345678,
            "rId": 11223344,
            "parentId": 44556677,
            "devName": "内倒窗2",
            "devModel": "evecca.win.218",
            "romVer": "2009",
            "positionValue": 100,
            "runValue": 6,
            "lockValue": 0,
            "isReady": True,
            "actions": [
                {"cmd": "open", "dpid": 33619969, "value": 1},
                {"cmd": "close", "dpid": 33619969, "value": 2},
                {"cmd": "stop", "dpid": 33619969, "value": 3},
                {"cmd": "opentilt", "dpid": 33619969, "value": 5},
                {
                    "cmd": "oper",
                    "dpid": 33619970,
                    "value": 50,
                    "numData": {"numMin": 0, "numMax": 100},
                },
            ],
        },
        "测试房",
    )

    assert device.device_id == 87654321
    assert device.parent_id == 44556677
    assert device.room_name == "测试房"
    assert device.position == 100
    assert device.window_mode == WINDOW_MODE_OPEN
    assert device.available
    assert device.actions["open"] == 1
    assert device.actions["close"] == 2
    assert device.actions["stop"] == 3
    assert device.actions["opentilt"] == 5
    assert device.position_min == 0
    assert device.position_max == 100


def test_lock_device_parses_lock_state_and_parent() -> None:
    """The captured lock metadata becomes a controllable lock model."""
    device = EveccaDevice.from_api(
        {
            "devId": 22334455,
            "fId": 12345678,
            "rId": 11223344,
            "parentId": 44556677,
            "devName": "内倒锁2",
            "devModel": "evecca.lock.219",
            "romVer": "1008",
            "positionValue": -1,
            "runValue": 90,
            "lockValue": 0,
            "isReady": True,
            "actions": [
                {"cmd": "unlock", "dpid": 33619969, "value": 8},
                {"cmd": "lock", "dpid": 33619969, "value": 7},
            ],
        },
        "测试房",
    )

    assert device.parent_id == 44556677
    assert device.locked is False
    assert device.actions["lock"] == 7
    assert device.actions["unlock"] == 8

def test_run_value_overrides_fallback_lock_state() -> None:
    """MQTT-compatible run values provide confirmed lock state."""
    device = EveccaDevice.from_api(
        {
            "devId": 22334455,
            "fId": 12345678,
            "devName": "内倒锁2",
            "devModel": "evecca.lock.219",
            "runValue": 19,
            "lockValue": 0,
            "isReady": True,
        },
        "测试房",
    )

    assert device.locked is True


def test_controller_device_parses_directives() -> None:
    """Controller UI directives are kept for future controller entities."""
    device = EveccaDevice.from_api(
        {
            "devId": 44556677,
            "fId": 12345678,
            "rId": 11223344,
            "parentId": 0,
            "devName": "智能门窗控制器3C",
            "devModel": "evecca.ctrlbox.7",
            "romVer": "1027",
            "positionValue": -1,
            "runValue": -1,
            "lockValue": -1,
            "isReady": True,
            "actions": [],
            "directives": [
                {"cmd": "flash", "dpid": 33619969, "value": 16},
            ],
        },
        "测试房",
    )

    assert device.parent_id is None
    assert device.actions["flash"] == 16
    assert device.window_mode is None
    assert device.locked is None
