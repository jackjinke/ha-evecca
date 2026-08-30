"""Tests for EVECCA API models."""

from custom_components.evecca.models import EveccaDevice


def test_window_device_parses_commands_and_state() -> None:
    """The captured window metadata becomes a cover-capable model."""
    device = EveccaDevice.from_api(
        {
            "devId": 87654321,
            "fId": 12345678,
            "rId": 11223344,
            "devName": "内倒窗2",
            "devModel": "evecca.win.218",
            "romVer": "2009",
            "positionValue": 81,
            "runValue": 104,
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
    assert device.room_name == "测试房"
    assert device.position == 81
    assert device.available
    assert device.actions["open"] == 1
    assert device.actions["close"] == 2
    assert device.actions["stop"] == 3
    assert device.actions["opentilt"] == 5
    assert device.position_min == 0
    assert device.position_max == 100
