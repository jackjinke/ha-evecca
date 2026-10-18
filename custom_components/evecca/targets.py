"""Command targets and the telemetry that confirms them."""

from dataclasses import dataclass, replace

from .const import (
    RUN_WINDOW_GEAR_SWITCH,
    WINDOW_MODE_CLOSED,
    WINDOW_MODE_OPEN,
    WINDOW_MODE_TILT_OPEN,
    WINDOW_OPEN_MIN_POSITION,
)
from .models import EveccaDevice

POSITION_TOLERANCE = 100 - WINDOW_OPEN_MIN_POSITION


@dataclass(frozen=True, slots=True)
class CommandTarget:
    """A commanded state shown as in progress until telemetry confirms it."""

    baseline: EveccaDevice
    position: int | None = None
    tilt: bool = False
    locked: bool | None = None
    gear_switched: bool = False

    @property
    def is_closing(self) -> bool:
        """Return whether the target moves a window toward closed."""
        if self.position is None:
            return False
        start = self.baseline.position
        return self.position == 0 or (start is not None and self.position < start)


def observe(target: CommandTarget, report: EveccaDevice) -> CommandTarget:
    """Record a pushed report that later reports cannot reveal."""
    if target.tilt and report.run_value == RUN_WINDOW_GEAR_SWITCH:
        return replace(target, gear_switched=True)
    return target


def target_reached(
    target: CommandTarget, device: EveccaDevice, *, pushed: bool
) -> bool:
    """Return whether reported telemetry proves the target.

    Pushed MQTT reports are current. A polled HTTPS snapshot may predate the
    command, so it only counts once the confirming field differs from the
    state recorded when the command was sent.
    """
    if target.tilt:
        # Windows never report tilt mode. Entering tilt always pauses at 0% to
        # switch gear, then stops at the tilt opening.
        return (
            pushed
            and target.gear_switched
            and not device.moving
            and device.position is not None
            and device.position > 0
        )
    if target.position is not None:
        reported = device.position
        if reported is None or device.moving:
            return False
        if target.position == 0:
            reached = reported == 0
        else:
            reached = abs(reported - target.position) <= POSITION_TOLERANCE
        return reached and (pushed or reported != target.baseline.position)
    reached = device.locked is not None and device.locked == target.locked
    return reached and (pushed or device.locked != target.baseline.locked)


def stopped_window_mode(
    position: int | None, completed: CommandTarget | None
) -> str | None:
    """Return the mode of a window that just stopped, or None if unknowable."""
    if position == 0:
        return WINDOW_MODE_CLOSED
    if position is not None and position >= WINDOW_OPEN_MIN_POSITION:
        return WINDOW_MODE_OPEN
    if completed is None:
        return None
    return WINDOW_MODE_TILT_OPEN if completed.tilt else WINDOW_MODE_OPEN
