"""Data update coordinator for EVECCA devices."""

import logging
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from time import monotonic
from typing import Any

from homeassistant.components import persistent_notification
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import EveccaApi, EveccaApiError, EveccaAuthError, EveccaConnectionError
from .const import (
    ACTION_LABELS,
    CONTROLLER_FUNCTION_BY_VALUE,
    CONTROLLER_FUNCTION_LABELS,
    CONTROLLER_FUNCTION_VALUES,
    DOMAIN,
    DPID_NORMALLY_OC,
    DPID_POSITION_SET,
    LOCK_STATE_BY_RUN_VALUE,
    MODEL_CONTROLLER_PREFIX,
    MODEL_LOCK_PREFIX,
    MODEL_WINDOW_PREFIX,
    OPTIMISTIC_TIMEOUT,
    PUSH_PRECEDENCE,
    RUN_WINDOW_GEAR_SWITCH,
    RUN_WINDOW_MOVING,
    RUN_WINDOW_STOPPED,
    SCAN_INTERVAL,
)
from .device_info import device_display_name
from .models import EveccaDevice
from .mqtt import EveccaMqttUpdate
from .runtime import EveccaConfigEntry
from .session import EveccaSessionManager
from .targets import CommandTarget, observe, stopped_window_mode, target_reached

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class EveccaData:
    """Device telemetry as last reported by EVECCA; never optimistic."""

    devices: dict[int, EveccaDevice]


class EveccaCoordinator(DataUpdateCoordinator[EveccaData]):
    """Merge HTTPS snapshots and MQTT pushes, and track command targets."""

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: EveccaConfigEntry,
        api: EveccaApi,
        session: EveccaSessionManager,
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
        self.error_codes: dict[int, str] = {}
        self._error_store = Store[dict[str, Any]](
            hass,
            1,
            f"{DOMAIN}_{config_entry.entry_id}_error_codes",
        )
        self._targets: dict[int, CommandTarget] = {}
        self._target_timers: dict[int, Callable[[], None]] = {}
        self._window_pushed_at: dict[int, float] = {}
        config_entry.async_on_unload(self._cancel_all_timers)

    async def async_load_error_codes(self) -> None:
        """Load cached error texts, then refresh them like the official app."""
        cached = await self._error_store.async_load()
        if isinstance(cached, dict):
            raw_codes = cached.get("codes")
            if isinstance(raw_codes, dict):
                self.error_codes = {
                    int(code): str(text)
                    for code, text in raw_codes.items()
                    if str(code).isdigit()
                }

        try:
            catalog = await self.session.async_call(self.api.async_error_codes)
        except (EveccaApiError, EveccaAuthError, EveccaConnectionError) as err:
            _LOGGER.debug("Cannot refresh EVECCA error catalog: %s", err)
            return
        if not catalog.codes:
            return

        self.error_codes = dict(catalog.codes)
        await self._error_store.async_save(
            {
                "version": catalog.version,
                "codes": {str(code): text for code, text in catalog.codes.items()},
            }
        )

    async def _async_update_data(self) -> EveccaData:
        """Fetch a full device snapshot from EVECCA."""
        fetched_at = monotonic()
        try:
            devices = await self.session.async_call(
                self.api.async_devices, self.family_id
            )
        except EveccaAuthError as err:
            raise ConfigEntryAuthFailed("EVECCA session renewal rejected") from err
        except (EveccaApiError, EveccaConnectionError) as err:
            raise UpdateFailed(f"Cannot update EVECCA devices: {err}") from err

        previous = self.data.devices if self.data is not None else {}
        merged: dict[int, EveccaDevice] = {}
        for device in devices:
            if device.model.startswith(MODEL_WINDOW_PREFIX):
                _LOGGER.debug(
                    "EVECCA snapshot %s: position=%s run_value=%s",
                    device.device_id,
                    device.position,
                    device.run_value,
                )
            pushed_at = self._window_pushed_at.get(device.device_id)
            current = self._merge_snapshot(
                device,
                previous.get(device.device_id),
                recent_push=(
                    pushed_at is not None and fetched_at - pushed_at < PUSH_PRECEDENCE
                ),
            )
            self._confirm_target(current, pushed=False)
            merged[device.device_id] = current
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
        """Send a command, report failures, and refresh state."""
        device = self._device(device_id)
        action = self._action_label(dpid, value)
        try:
            await self.session.async_call(
                self.api.async_action,
                self.family_id,
                device_id,
                value,
                dpid=dpid,
            )
        except EveccaAuthError as err:
            self._notify_command(device, action, "认证已过期")
            self._entry.async_start_reauth(self.hass)
            raise HomeAssistantError("EVECCA authentication expired") from err
        except (EveccaApiError, EveccaConnectionError) as err:
            self._notify_command(device, action, str(err))
            raise HomeAssistantError(f"EVECCA command failed: {err}") from err

        await self.async_request_refresh()

    async def async_set_controller_function(
        self,
        device_id: int,
        function: str,
    ) -> None:
        """Set the controller to default, normally open, or normally closed."""
        device = self._device(device_id)
        if device is None or function not in CONTROLLER_FUNCTION_VALUES:
            raise HomeAssistantError("Unsupported EVECCA controller function")
        label = f"设置为{CONTROLLER_FUNCTION_LABELS[function]}"
        try:
            await self.session.async_call(
                self.api.async_set_property,
                self.family_id,
                device_id,
                CONTROLLER_FUNCTION_VALUES[function],
                dpid=DPID_NORMALLY_OC,
            )
        except EveccaAuthError as err:
            self._notify_command(device, label, "认证已过期")
            self._entry.async_start_reauth(self.hass)
            raise HomeAssistantError("EVECCA authentication expired") from err
        except (EveccaApiError, EveccaConnectionError) as err:
            self._notify_command(device, label, str(err))
            raise HomeAssistantError(f"EVECCA command failed: {err}") from err

        current = self._device(device_id)
        if current is not None:
            self._set_device(replace(current, controller_function=function))

        await self.async_request_refresh()

    def target(self, device_id: int) -> CommandTarget | None:
        """Return the command target still awaiting confirmation."""
        return self._targets.get(device_id)

    @callback
    def set_target(
        self,
        device_id: int,
        *,
        position: int | None = None,
        tilt: bool = False,
        locked: bool | None = None,
    ) -> None:
        """Mark a command as in progress until telemetry confirms it."""
        device = self._device(device_id)
        if device is None:
            return
        self._cancel_timer(device_id)
        self._targets[device_id] = CommandTarget(
            baseline=device, position=position, tilt=tilt, locked=locked
        )

        @callback
        def _expire(_now: datetime) -> None:
            self._target_timers.pop(device_id, None)
            self.clear_target(device_id)

        self._target_timers[device_id] = async_call_later(
            self.hass, OPTIMISTIC_TIMEOUT, _expire
        )
        self.async_update_listeners()

    @callback
    def clear_target(self, device_id: int) -> None:
        """Stop waiting for a command target."""
        self._cancel_timer(device_id)
        if self._targets.pop(device_id, None) is not None:
            self.async_update_listeners()

    def handle_mqtt_update(self, update: EveccaMqttUpdate) -> None:
        """Schedule one pushed MQTT update on Home Assistant's event loop."""
        try:
            self.hass.loop.call_soon_threadsafe(self._handle_mqtt_update, update)
        except RuntimeError:
            _LOGGER.debug(
                "Ignoring EVECCA MQTT update while Home Assistant is stopping"
            )

    @callback
    def _handle_mqtt_update(self, update: EveccaMqttUpdate) -> None:
        """Apply one pushed MQTT update."""
        _LOGGER.debug("EVECCA MQTT update: %s", update)
        device = self._device(update.device_id)
        if device is None:
            return

        updated = replace(
            device,
            position=(
                update.position if update.position is not None else device.position
            ),
            run_value=(
                update.run_value if update.run_value is not None else device.run_value
            ),
            online=update.online if update.online is not None else device.online,
        )
        if device.model.startswith(MODEL_WINDOW_PREFIX):
            if update.position is not None or update.run_value in (
                RUN_WINDOW_MOVING,
                RUN_WINDOW_GEAR_SWITCH,
                RUN_WINDOW_STOPPED,
            ):
                self._window_pushed_at[device.device_id] = monotonic()
            if update.run_value in (RUN_WINDOW_MOVING, RUN_WINDOW_GEAR_SWITCH):
                # The previous mode stops being true as soon as the sash moves.
                updated = replace(updated, moving=True, window_mode=None)
            elif update.run_value == RUN_WINDOW_STOPPED:
                updated = replace(updated, moving=False)
        elif device.model.startswith(MODEL_LOCK_PREFIX):
            updated = replace(
                updated,
                locked=LOCK_STATE_BY_RUN_VALUE.get(update.run_value, device.locked),
            )
        elif (
            device.model.startswith(MODEL_CONTROLLER_PREFIX)
            and update.normally_oc is not None
        ):
            updated = replace(
                updated,
                controller_function=CONTROLLER_FUNCTION_BY_VALUE.get(
                    update.normally_oc, device.controller_function
                ),
            )

        completed = self._confirm_target(updated, pushed=True)
        if (
            device.model.startswith(MODEL_WINDOW_PREFIX)
            and update.run_value == RUN_WINDOW_STOPPED
        ):
            updated = replace(
                updated,
                window_mode=stopped_window_mode(updated.position, completed),
            )
        self._set_device(updated)
        if update.event_code is not None:
            self._notify_device_event(updated, update.event_code)

    @staticmethod
    def _merge_snapshot(
        device: EveccaDevice,
        previous: EveccaDevice | None,
        *,
        recent_push: bool,
    ) -> EveccaDevice:
        """Fill fields a snapshot omits from the previously reported state."""
        if previous is None:
            return device
        merged = replace(device, online=previous.online)
        if device.model.startswith(MODEL_WINDOW_PREFIX):
            if recent_push:
                # The snapshot may predate that push, so it cannot move the window.
                return replace(
                    merged,
                    position=previous.position,
                    run_value=previous.run_value,
                    moving=previous.moving,
                    window_mode=previous.window_mode,
                )
            # Snapshots carry no motion; mid-move positions imply no mode.
            keep_mode = previous.moving or device.position == previous.position
            return replace(
                merged,
                moving=previous.moving,
                window_mode=previous.window_mode if keep_mode else device.window_mode,
            )
        if device.model.startswith(MODEL_LOCK_PREFIX) and device.locked is None:
            return replace(merged, locked=previous.locked)
        if (
            device.model.startswith(MODEL_CONTROLLER_PREFIX)
            and device.controller_function is None
        ):
            return replace(merged, controller_function=previous.controller_function)
        return merged

    def _confirm_target(
        self, device: EveccaDevice, *, pushed: bool
    ) -> CommandTarget | None:
        """Drop and return the device's target once telemetry proves it."""
        target = self._targets.get(device.device_id)
        if target is None:
            return None
        if pushed:
            target = observe(target, device)
            self._targets[device.device_id] = target
        if not target_reached(target, device, pushed=pushed):
            return None
        del self._targets[device.device_id]
        self._cancel_timer(device.device_id)
        return target

    @staticmethod
    def _action_label(dpid: int, value: int) -> str:
        """Return a user-readable command label."""
        if dpid == DPID_POSITION_SET:
            return f"设置开合百分比为 {value}%"
        return ACTION_LABELS.get(value, f"发送命令 {value}")

    @callback
    def _notify_command(
        self,
        device: EveccaDevice | None,
        action: str,
        error: str,
    ) -> None:
        """Notify only when a cloud command was rejected."""
        if device is None:
            return
        persistent_notification.async_create(
            self.hass,
            f"{action}失败: {error}",
            title=f"EVECCA · {device_display_name(device)}",
            notification_id=f"evecca_command_{device.device_id}",
        )

    @callback
    def _notify_device_event(self, device: EveccaDevice, code: int) -> None:
        """Translate and notify one device event reported over MQTT."""
        text = self.error_codes.get(code, "未知设备事件")
        persistent_notification.async_create(
            self.hass,
            f"{text} ({code})",
            title=f"EVECCA · {device_display_name(device)}",
            notification_id=f"evecca_event_{device.device_id}",
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

    def _cancel_timer(self, device_id: int) -> None:
        """Cancel one target expiry timer."""
        cancel = self._target_timers.pop(device_id, None)
        if cancel is not None:
            cancel()

    def _cancel_all_timers(self) -> None:
        """Cancel all target expiry timers during unload."""
        for cancel in self._target_timers.values():
            cancel()
        self._target_timers.clear()
