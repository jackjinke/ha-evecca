"""Tests for EVECCA MQTT message parsing."""

import json

from custom_components.evecca.mqtt import parse_mqtt_message


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


def test_ignore_other_families_and_invalid_payloads() -> None:
    """Malformed or unrelated MQTT messages are ignored."""
    assert parse_mqtt_message("1/87654321/online", b"{}", 12345678) is None
    assert (
        parse_mqtt_message("12345678/87654321/online", b"not json", 12345678)
        is None
    )

def test_ignore_non_utf8_and_non_object_payloads() -> None:
    """Malformed bytes and non-object JSON do not terminate the listener."""
    topic = "12345678/87654321/online"
    assert parse_mqtt_message(topic, b"\xff\xfe", 12345678) is None
    assert parse_mqtt_message(topic, b"[]", 12345678) is None
    assert (
        parse_mqtt_message(topic, b'{"method":"other","params":[]}', 12345678)
        is None
    )
