"""Select entities for EVECCA controller functions."""

from typing import ClassVar

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONTROLLER_FUNCTIONS, MODEL_CONTROLLER_PREFIX
from .coordinator import EveccaCoordinator
from .device_info import evecca_device_info
from .models import EveccaDevice
from .runtime import EveccaConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EveccaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up EVECCA controller function selects."""
    coordinator = entry.runtime_data.coordinator
    async_add_entities(
        EveccaControllerFunctionSelect(coordinator, device)
        for device in coordinator.data.devices.values()
        if device.model.startswith(MODEL_CONTROLLER_PREFIX)
        and device.controller_function is not None
    )


class EveccaControllerFunctionSelect(
    CoordinatorEntity[EveccaCoordinator], SelectEntity
):
    """Set a controller to default, normally open, or normally closed."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:electric-switch"
    _attr_options: ClassVar[list[str]] = list(CONTROLLER_FUNCTIONS)
    _attr_translation_key = "controller_function"

    def __init__(self, coordinator: EveccaCoordinator, device: EveccaDevice) -> None:
        """Initialize the controller function select."""
        super().__init__(coordinator)
        self.device_id = device.device_id
        self._attr_unique_id = f"evecca_controller_function_{device.device_id}"
        self._attr_device_info = evecca_device_info(device)

    @property
    def device(self) -> EveccaDevice:
        """Return the current device data."""
        return self.coordinator.data.devices[self.device_id]

    @property
    def available(self) -> bool:
        """Return whether the controller is available."""
        return super().available and self.device.available

    @property
    def current_option(self) -> str | None:
        """Return the active controller function."""
        return self.device.controller_function

    async def async_select_option(self, option: str) -> None:
        """Apply one controller function."""
        await self.coordinator.async_set_controller_function(self.device_id, option)
