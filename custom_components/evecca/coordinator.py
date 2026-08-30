"""Data update coordinator for EVECCA devices."""

import logging
from collections.abc import Callable
from dataclasses import dataclass, replace

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import EveccaApi, EveccaApiError, EveccaAuthError, EveccaConnectionError
from .const import (
    LOCK_STATE_BY_RUN_VALUE,
    MODEL_LOCK_PREFIX,
    MODEL_WINDOW_PREFIX,
    OPTIMISTIC_TIMEOUT,
    SCAN_INTERVAL,
    WINDOW_MODE_BY_RUN_VALUE,
    WINDOW_MODE_CLOSED,
    WINDOW_MODE_OPEN,
)
from .models import EveccaDevice, EveccaSession
from .mqtt import EveccaMqttUpdate
from .runtime import EveccaConfigEntry

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class EveccaData:
    """Current EVECCA integration data."""

    devices: dict[int, EveccaDevice]


@dataclass(frozen=True, slots=True)
class PendingState:
    """Optimistic target state waiting for device confirmation."""

    previous_position: int | None = None
    position: int | None = None
    previous_window_mode: str | None = None
    window_mode: str | None = None
    previous_locked: bool | None = None
    locked: bool | None = None


class EveccaCoordinator(DataUpdateCoordinator[EveccaData]):
    """Coordinate HTTPS reconciliation, MQTT updates, and optimistic targets."""

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: EveccaConfigEntry,
        api: EveccaApi,
        session: EveccaSession,
        family_id: int,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=f"EVECCA {family_id}",
            update_interval=SCAN_INTERVAL,
        )
        self.api = api
        self.session = session
        self.family_id = family_id
        self._entry = config_entry
        self._pending: dict[int, PendingState] = {}
        self._pending_cancels: dict[int, Callable[[], None]] = {}
        config_entry.async_on_unload(self._cancel_all_pending)

    async def _async_update_data(self) -> EveccaData:
        """Fetch a full device snapshot from EVECCA."""
        try:
            devices = await self.api.async_devices(self.session, self.family_id)
        except EveccaAuthError as err:
            raise ConfigEntryAuthFailed("EVECCA token expired") from err
        except (EveccaApiError, EveccaConnectionError) as err:
            raise UpdateFailed(f"Cannot update EVECCA devices: {err}") from err

        previous = self.data.devices if self.data is not None else {}
        merged = {
            device.device_id: self._merge_https_device(
                device,
                previous.get(device.device_id),
            )
            for device in devices
        }
        for device_id, device in previous.items():
            if device_id not in merged:
                merged[device_id] = replace(device, is_ready=False, online=False)
        return EveccaData(devices=merged)

    async def async_action(
        self,
        device_id: int,
        value: int,
        *,
        dpid: int,
    ) -> None:
        """Send a command and refresh state."""
        try:
            await self.api.async_action(
                self.session,
                self.family_id,
                device_id,
                value,
                dpid=dpid,
            )
        except EveccaAuthError as err:
            self._entry.async_start_reauth(self.hass)
            raise HomeAssistantError("EVECCA authentication expired") from err
        except (EveccaApiError, EveccaConnectionError) as err:
            raise HomeAssistantError(f"EVECCA command failed: {err}") from err
        await self.async_request_refresh()

    def set_window_target(
        self,
        device_id: int,
        *,
        position: int | None = None,
        window_mode: str | None = None,
    ) -> None:
        """Display a window command target until the device confirms it."""
        device = self._device(device_id)
        if device is None:
            return
        existing = self._pending.get(device_id)
        previous_position = device.position
        previous_window_mode = device.window_mode
        if existing is not None:
            if existing.position is not None:
                previous_position = existing.previous_position
            if existing.window_mode is not None:
                previous_window_mode = existing.previous_window_mode
        pending = PendingState(
            previous_position=previous_position,
            position=position,
            previous_window_mode=previous_window_mode,
            window_mode=window_mode,
        )
        self._set_pending(device_id, pending)

    def set_lock_target(self, device_id: int, locked: bool) -> None:
        """Display a lock command target until the device confirms it."""
        device = self._device(device_id)
        if device is None:
            return
        existing = self._pending.get(device_id)
        previous_locked = device.locked
        if existing is not None and existing.locked is not None:
            previous_locked = existing.previous_locked
        self._set_pending(
            device_id,
            PendingState(previous_locked=previous_locked, locked=locked),
        )

    def clear_target(self, device_id: int) -> None:
        """Discard an optimistic target and restore the last confirmed state."""
        pending = self._pending.pop(device_id, None)
        self._cancel_pending(device_id)
        if pending is None or self.data is None:
            return
        device = self.data.devices.get(device_id)
        if device is None:
            return
        self._set_device(
            replace(
                device,
                position=(
                    pending.previous_position
                    if pending.position is not None
                    else device.position
                ),
                window_mode=(
                    pending.previous_window_mode
                    if pending.window_mode is not None
                    else device.window_mode
                ),
                locked=(
                    pending.previous_locked
                    if pending.locked is not None
                    else device.locked
                ),
            )
        )

    def handle_mqtt_update(self, update: EveccaMqttUpdate) -> None:
        """Apply one pushed MQTT update."""
        if self.data is None or update.device_id not in self.data.devices:
            return

        device = self.data.devices[update.device_id]
        window_mode = device.window_mode
        locked = device.locked
        if device.model.startswith(MODEL_WINDOW_PREFIX):
            window_mode = WINDOW_MODE_BY_RUN_VALUE.get(
                update.run_value,
                device.window_mode,
            )
        elif device.model.startswith(MODEL_LOCK_PREFIX):
            locked = LOCK_STATE_BY_RUN_VALUE.get(update.run_value, device.locked)

        updated = replace(
            device,
            position=(
                update.position if update.position is not None else device.position
            ),
            run_value=(
                update.run_value if update.run_value is not None else device.run_value
            ),
            window_mode=window_mode,
            locked=locked,
            online=update.online if update.online is not None else device.online,
        )
        self._set_actual_device(
            updated,
            position_actual=update.position is not None,
            window_mode_actual=(
                device.model.startswith(MODEL_WINDOW_PREFIX)
                and update.run_value in WINDOW_MODE_BY_RUN_VALUE
            ),
            locked_actual=(
                device.model.startswith(MODEL_LOCK_PREFIX)
                and update.run_value in LOCK_STATE_BY_RUN_VALUE
            ),
        )

    def _merge_https_device(
        self,
        device: EveccaDevice,
        previous: EveccaDevice | None,
    ) -> EveccaDevice:
        """Merge an HTTPS snapshot without losing pushed or pending state."""
        merged = (
            replace(device, online=previous.online) if previous is not None else device
        )
        pending = self._pending.get(merged.device_id)
        if pending is not None:
            pending = self._reconcile_pending(
                merged,
                pending,
                position_actual=merged.position is not None and merged.position >= 0,
                window_mode_actual=merged.window_mode is not None,
                locked_actual=merged.locked is not None,
            )
            if pending is None:
                self._discard_pending(merged.device_id)
            else:
                self._pending[merged.device_id] = pending
                merged = self._apply_pending(merged, pending)

        if merged.model.startswith(MODEL_WINDOW_PREFIX) and merged.window_mode is None:
            window_mode = previous.window_mode if previous is not None else None
            if window_mode is None:
                if merged.position == 0:
                    window_mode = WINDOW_MODE_CLOSED
                elif merged.position == 100:
                    window_mode = WINDOW_MODE_OPEN
            merged = replace(merged, window_mode=window_mode)
        if merged.model.startswith(MODEL_LOCK_PREFIX) and merged.locked is None:
            merged = replace(
                merged,
                locked=previous.locked if previous is not None else None,
            )
        return merged

    def _set_pending(self, device_id: int, pending: PendingState) -> None:
        """Store and display an optimistic target."""
        self._cancel_pending(device_id)
        self._pending[device_id] = pending
        self._pending_cancels[device_id] = async_call_later(
            self.hass,
            OPTIMISTIC_TIMEOUT,
            lambda _: self._expire_pending(device_id),
        )
        device = self._device(device_id)
        if device is not None:
            self._set_device(self._apply_pending(device, pending))

    def _expire_pending(self, device_id: int) -> None:
        """Restore the last confirmed state after an optimistic timeout."""
        self.clear_target(device_id)

    def _set_actual_device(
        self,
        device: EveccaDevice,
        *,
        position_actual: bool,
        window_mode_actual: bool,
        locked_actual: bool,
    ) -> None:
        """Apply actual fields without treating optimistic values as confirmation."""
        pending = self._pending.get(device.device_id)
        if pending is not None:
            pending = self._reconcile_pending(
                device,
                pending,
                position_actual=position_actual,
                window_mode_actual=window_mode_actual,
                locked_actual=locked_actual,
            )
            if pending is None:
                self._discard_pending(device.device_id)
            else:
                self._pending[device.device_id] = pending
                device = self._apply_pending(device, pending)
        self._set_device(device)

    @staticmethod
    def _reconcile_pending(
        device: EveccaDevice,
        pending: PendingState,
        *,
        position_actual: bool,
        window_mode_actual: bool,
        locked_actual: bool,
    ) -> PendingState | None:
        """Clear only targets confirmed or contradicted by actual fields."""
        position = pending.position
        previous_position = pending.previous_position
        window_mode = pending.window_mode
        previous_window_mode = pending.previous_window_mode
        locked = pending.locked
        previous_locked = pending.previous_locked

        if (
            position is not None
            and position_actual
            and device.position is not None
            and (
                device.position == position
                or device.position != previous_position
            )
        ):
            position = None
            previous_position = None
        if (
            window_mode is not None
            and window_mode_actual
            and device.window_mode is not None
            and (
                device.window_mode == window_mode
                or device.window_mode != previous_window_mode
            )
        ):
            window_mode = None
            previous_window_mode = None
        if (
            locked is not None
            and locked_actual
            and device.locked is not None
            and (device.locked == locked or device.locked != previous_locked)
        ):
            locked = None
            previous_locked = None

        if position is None and window_mode is None and locked is None:
            return None
        return PendingState(
            previous_position=previous_position,
            position=position,
            previous_window_mode=previous_window_mode,
            window_mode=window_mode,
            previous_locked=previous_locked,
            locked=locked,
        )

    @staticmethod
    def _apply_pending(
        device: EveccaDevice,
        pending: PendingState,
    ) -> EveccaDevice:
        """Apply display-only optimistic fields."""
        return replace(
            device,
            position=(
                pending.position if pending.position is not None else device.position
            ),
            window_mode=(
                pending.window_mode
                if pending.window_mode is not None
                else device.window_mode
            ),
            locked=pending.locked if pending.locked is not None else device.locked,
        )

    def _set_device(self, device: EveccaDevice) -> None:
        """Publish one updated device."""
        if self.data is None:
            return
        self.async_set_updated_data(
            EveccaData(devices={**self.data.devices, device.device_id: device})
        )

    def _device(self, device_id: int) -> EveccaDevice | None:
        """Return one device from current coordinator data."""
        if self.data is None:
            return None
        return self.data.devices.get(device_id)

    def _discard_pending(self, device_id: int) -> None:
        """Drop an optimistic target without restoring old state."""
        self._pending.pop(device_id, None)
        self._cancel_pending(device_id)

    def _cancel_pending(self, device_id: int) -> None:
        """Cancel one pending-state timer."""
        cancel = self._pending_cancels.pop(device_id, None)
        if cancel is not None:
            cancel()

    def _cancel_all_pending(self) -> None:
        """Cancel all pending-state timers during unload."""
        for cancel in self._pending_cancels.values():
            cancel()
        self._pending_cancels.clear()
