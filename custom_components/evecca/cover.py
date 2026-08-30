"""Cover entities for EVECCA window controllers."""

from typing import Any

from homeassistant.components.cover import (
    ATTR_POSITION,
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    ACTION_CLOSE,
    ACTION_OPEN,
    ACTION_STOP,
    ACTION_TILT_OPEN,
    DOMAIN,
    DPID_ACTION,
    DPID_POSITION_SET,
)
from .coordinator import EveccaCoordinator
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
        if device.model.startswith("evecca.win")
    )


class EveccaCover(CoordinatorEntity[EveccaCoordinator], CoverEntity):
    """Representation of one EVECCA window controller."""

    _attr_device_class = CoverDeviceClass.WINDOW
    _attr_has_entity_name = True
    _attr_name = None

    def __init__(self, coordinator: EveccaCoordinator, device: EveccaDevice) -> None:
        """Initialize the cover entity."""
        super().__init__(coordinator)
        self.device_id = device.device_id
        self._attr_unique_id = f"evecca_{device.device_id}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, str(device.device_id))},
            manufacturer="EVECCA",
            model=device.model,
            name=_device_display_name(device),
            suggested_area=device.room_name,
            sw_version=device.firmware,
        )

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
        if position is None:
            return None
        return max(0, min(100, position))

    @property
    def is_closed(self) -> bool | None:
        """Return whether the window is closed."""
        position = self.current_cover_position
        if position is None:
            return None
        return position == 0

    async def async_open_cover(self, **kwargs: Any) -> None:
        """Open the window."""
        await self._async_action(ACTION_OPEN)

    async def async_close_cover(self, **kwargs: Any) -> None:
        """Close the window."""
        await self._async_action(ACTION_CLOSE)

    async def async_stop_cover(self, **kwargs: Any) -> None:
        """Stop the window."""
        await self._async_action(ACTION_STOP)

    async def async_open_cover_tilt(self, **kwargs: Any) -> None:
        """Open the window in tilt/hung-open mode."""
        await self._async_action(ACTION_TILT_OPEN)

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        """Move the window to an opening percentage."""
        position = int(kwargs[ATTR_POSITION])
        await self.coordinator.async_action(
            self.device_id,
            position,
            dpid=DPID_POSITION_SET,
        )

    async def _async_action(self, value: int) -> None:
        """Send one fixed action command."""
        await self.coordinator.async_action(
            self.device_id,
            value,
            dpid=DPID_ACTION,
        )


def _device_display_name(device: EveccaDevice) -> str:
    """Return a unique, user-readable device name."""
    parts = [part for part in (device.room_name, device.name) if part]
    name = " ".join(parts) or f"EVECCA {device.device_id}"
    return f"{name} ({str(device.device_id)[-4:]})"
