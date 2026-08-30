"""Sensor entities for EVECCA windows."""

from typing import ClassVar

from homeassistant.components.sensor import RestoreSensor, SensorDeviceClass
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import MODEL_WINDOW_PREFIX, WINDOW_MODES
from .coordinator import EveccaCoordinator
from .device_info import evecca_device_info
from .models import EveccaDevice
from .runtime import EveccaConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EveccaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up EVECCA window mode sensors."""
    coordinator = entry.runtime_data.coordinator
    async_add_entities(
        EveccaWindowModeSensor(coordinator, device)
        for device in coordinator.data.devices.values()
        if device.model.startswith(MODEL_WINDOW_PREFIX)
    )


class EveccaWindowModeSensor(CoordinatorEntity[EveccaCoordinator], RestoreSensor):
    """Expose the confirmed or optimistic window mode."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_has_entity_name = True
    _attr_icon = "mdi:window-open-variant"
    _attr_options: ClassVar[list[str]] = list(WINDOW_MODES)
    _attr_translation_key = "window_mode"

    def __init__(self, coordinator: EveccaCoordinator, device: EveccaDevice) -> None:
        """Initialize the window mode sensor."""
        super().__init__(coordinator)
        self.device_id = device.device_id
        self._attr_unique_id = f"evecca_window_mode_{device.device_id}"
        self._attr_device_info = evecca_device_info(device)
        self._restored_mode: str | None = None

    async def async_added_to_hass(self) -> None:
        """Restore the last confirmed mode when the API has no mode snapshot."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_sensor_data()
        if last_state is not None and last_state.native_value in WINDOW_MODES:
            self._restored_mode = last_state.native_value

    @property
    def device(self) -> EveccaDevice:
        """Return the current device data."""
        return self.coordinator.data.devices[self.device_id]

    @property
    def available(self) -> bool:
        """Return whether the device is available."""
        return super().available and self.device.available

    @property
    def native_value(self) -> str | None:
        """Return closed, open, or tilt-open."""
        return self.device.window_mode or self._restored_mode
