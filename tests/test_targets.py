"""Telemetry that may and may not confirm a window command.

Report sequences mirror MQTT captured from a real window on 2026-10-18.
"""

from dataclasses import replace

import pytest

from custom_components.evecca.models import EveccaDevice
from custom_components.evecca.targets import CommandTarget, observe, target_reached

OPEN_WINDOW = EveccaDevice.from_api(
    {
        "devId": 1,
        "fId": 2,
        "devModel": "evecca.win.218",
        "positionValue": 100,
        "isReady": True,
    }
)


def report(position: int, run_value: int) -> EveccaDevice:
    """Build one pushed window report (4 moving, 6 gear switch, 9 stopped)."""
    return replace(
        OPEN_WINDOW, position=position, run_value=run_value, moving=run_value != 9
    )


def replay(target: CommandTarget, *reports: EveccaDevice) -> bool:
    """Feed pushed reports in order; return whether the last one confirms."""
    for item in reports:
        target = observe(target, item)
    return target_reached(target, reports[-1], pushed=True)


def test_tilt_needs_gear_switch_before_stop() -> None:
    """A stop at a partial opening is only tilt after the 0% gear switch."""
    target = CommandTarget(baseline=OPEN_WINDOW, tilt=True)

    assert not replay(target, report(100, 4), report(40, 9))
    assert replay(target, report(100, 4), report(0, 6), report(31, 9))


@pytest.mark.parametrize(
    ("last", "closed"),
    [(report(0, 6), False), (report(1, 9), False), (report(0, 9), True)],
)
def test_close_requires_stop_at_zero(last: EveccaDevice, closed: bool) -> None:
    """Curtain interlocks rely on closed meaning stopped at exactly 0%."""
    target = CommandTarget(baseline=OPEN_WINDOW, position=0)

    assert replay(target, report(100, 4), last) is closed


def test_snapshot_from_before_command_does_not_confirm() -> None:
    """A polled snapshot equal to the pre-command state proves nothing."""
    closed = replace(OPEN_WINDOW, position=0)
    target = CommandTarget(baseline=closed, position=0)

    assert not target_reached(target, closed, pushed=False)
