"""Button entities for EVECCA maintenance actions."""

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ACTION_RECALIBRATE_TRAVEL, DPID_ACTION, MODEL_WINDOW_PREFIX
from .coordinator import EveccaCoordinator
from .device_info import evecca_device_info
from .models import EveccaDevice
from .runtime import EveccaConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EveccaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up EVECCA maintenance buttons."""
    coordinator = entry.runtime_data.coordinator
    async_add_entities(
        EveccaRecalibrateTravelButton(coordinator, device)
        for device in coordinator.data.devices.values()
        if device.model.startswith(MODEL_WINDOW_PREFIX)
    )


class EveccaRecalibrateTravelButton(
    CoordinatorEntity[EveccaCoordinator], ButtonEntity
):
    """Relearn one window actuator's travel."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True
    _attr_icon = "mdi:refresh"
    _attr_translation_key = "recalibrate_travel"

    def __init__(self, coordinator: EveccaCoordinator, device: EveccaDevice) -> None:
        """Initialize the relearn button."""
        super().__init__(coordinator)
        self.device_id = device.device_id
        self._attr_unique_id = f"evecca_recalibrate_travel_{device.device_id}"
        self._attr_device_info = evecca_device_info(device)

    @property
    def device(self) -> EveccaDevice:
        """Return the current device data."""
        return self.coordinator.data.devices[self.device_id]

    @property
    def available(self) -> bool:
        """Return whether the device is available."""
        return super().available and self.device.available

    async def async_press(self) -> None:
        """Start travel relearning."""
        await self.coordinator.async_action(
            self.device_id,
            ACTION_RECALIBRATE_TRAVEL,
            dpid=DPID_ACTION,
        )
