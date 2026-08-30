"""Cover entities for EVECCA window controllers."""

from typing import Any

from homeassistant.components.cover import (
    ATTR_POSITION,
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    ACTION_CLOSE,
    ACTION_OPEN,
    ACTION_STOP,
    ACTION_TILT_OPEN,
    DPID_ACTION,
    DPID_POSITION_SET,
    MODEL_WINDOW_PREFIX,
    WINDOW_MODE_CLOSED,
    WINDOW_MODE_OPEN,
    WINDOW_MODE_TILT_OPEN,
)
from .coordinator import EveccaCoordinator
from .device_info import evecca_device_info
from .models import EveccaDevice
from .runtime import EveccaConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EveccaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up EVECCA cover entities."""
    coordinator = entry.runtime_data.coordinator
    async_add_entities(
        EveccaCover(coordinator, device)
        for device in coordinator.data.devices.values()
        if device.model.startswith(MODEL_WINDOW_PREFIX)
    )


class EveccaCover(CoordinatorEntity[EveccaCoordinator], CoverEntity):
    """Representation of one EVECCA window actuator."""

    _attr_device_class = CoverDeviceClass.WINDOW
    _attr_has_entity_name = True
    _attr_name = None

    def __init__(self, coordinator: EveccaCoordinator, device: EveccaDevice) -> None:
        """Initialize the cover entity."""
        super().__init__(coordinator)
        self.device_id = device.device_id
        self._attr_unique_id = f"evecca_{device.device_id}"
        self._attr_device_info = evecca_device_info(device)

        features = CoverEntityFeature(0)
        if "open" in device.actions:
            features |= CoverEntityFeature.OPEN
        if "close" in device.actions:
            features |= CoverEntityFeature.CLOSE
        if "stop" in device.actions:
            features |= CoverEntityFeature.STOP
        if "oper" in device.actions:
            features |= CoverEntityFeature.SET_POSITION
        if "opentilt" in device.actions:
            features |= CoverEntityFeature.OPEN_TILT
        self._attr_supported_features = features

    @property
    def device(self) -> EveccaDevice:
        """Return the current device data."""
        return self.coordinator.data.devices[self.device_id]

    @property
    def available(self) -> bool:
        """Return whether the device is available."""
        return super().available and self.device.available

    @property
    def current_cover_position(self) -> int | None:
        """Return current position, where 0 is closed and 100 is open."""
        position = self.device.position
        if position is None or position < 0:
            return None
        return max(0, min(100, position))

    @property
    def current_cover_tilt_position(self) -> int | None:
        """Return tilt feedback for the binary hung-open mode."""
        if not self.supported_features & CoverEntityFeature.OPEN_TILT:
            return None
        mode = self.device.window_mode
        if mode == WINDOW_MODE_TILT_OPEN:
            return 100
        if mode in (WINDOW_MODE_CLOSED, WINDOW_MODE_OPEN):
            return 0
        return None

    @property
    def is_closed(self) -> bool | None:
        """Return whether the window is closed."""
        position = self.current_cover_position
        if position is None:
            return None
        return position == 0

    async def async_open_cover(self, **kwargs: Any) -> None:
        """Open the window."""
        await self._async_window_action(
            ACTION_OPEN,
            position=100,
            window_mode=WINDOW_MODE_OPEN,
        )

    async def async_close_cover(self, **kwargs: Any) -> None:
        """Close the window."""
        await self._async_window_action(
            ACTION_CLOSE,
            position=0,
            window_mode=WINDOW_MODE_CLOSED,
        )

    async def async_stop_cover(self, **kwargs: Any) -> None:
        """Stop the window."""
        self.coordinator.clear_target(self.device_id)
        await self.coordinator.async_action(
            self.device_id,
            ACTION_STOP,
            dpid=DPID_ACTION,
        )

    async def async_open_cover_tilt(self, **kwargs: Any) -> None:
        """Open the window in tilt/hung-open mode."""
        await self._async_window_action(
            ACTION_TILT_OPEN,
            window_mode=WINDOW_MODE_TILT_OPEN,
        )

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        """Move the window to an opening percentage."""
        position = int(kwargs[ATTR_POSITION])
        await self._async_window_action(
            value=position,
            dpid=DPID_POSITION_SET,
            position=position,
            window_mode=(
                WINDOW_MODE_CLOSED if position == 0 else WINDOW_MODE_OPEN
            ),
        )

    async def _async_window_action(
        self,
        value: int,
        *,
        dpid: int = DPID_ACTION,
        position: int | None = None,
        window_mode: str,
    ) -> None:
        """Send one window command with an optimistic target."""
        self.coordinator.set_window_target(
            self.device_id,
            position=position,
            window_mode=window_mode,
        )
        try:
            await self.coordinator.async_action(
                self.device_id,
                value,
                dpid=dpid,
            )
        except HomeAssistantError:
            self.coordinator.clear_target(self.device_id)
            raise
