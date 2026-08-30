"""Lock entities for EVECCA window locks."""

from typing import Any

from homeassistant.components.lock import LockEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ACTION_LOCK, ACTION_UNLOCK, DPID_ACTION, MODEL_LOCK_PREFIX
from .coordinator import EveccaCoordinator
from .device_info import evecca_device_info
from .models import EveccaDevice
from .runtime import EveccaConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EveccaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up EVECCA lock entities."""
    coordinator = entry.runtime_data.coordinator
    async_add_entities(
        EveccaLock(coordinator, device)
        for device in coordinator.data.devices.values()
        if device.model.startswith(MODEL_LOCK_PREFIX)
        and {"lock", "unlock"} <= device.actions.keys()
    )


class EveccaLock(CoordinatorEntity[EveccaCoordinator], LockEntity):
    """Representation of one EVECCA window lock."""

    _attr_has_entity_name = True
    _attr_name = None

    def __init__(self, coordinator: EveccaCoordinator, device: EveccaDevice) -> None:
        """Initialize the lock entity."""
        super().__init__(coordinator)
        self.device_id = device.device_id
        self._attr_unique_id = f"evecca_lock_{device.device_id}"
        self._attr_device_info = evecca_device_info(device)

    @property
    def device(self) -> EveccaDevice:
        """Return the current device data."""
        return self.coordinator.data.devices[self.device_id]

    @property
    def available(self) -> bool:
        """Return whether the lock is available."""
        return super().available and self.device.available

    @property
    def is_locked(self) -> bool | None:
        """Return whether the lock is locked."""
        return self.device.locked

    async def async_lock(self, **kwargs: Any) -> None:
        """Lock the window lock."""
        await self._async_set_locked(True)

    async def async_unlock(self, **kwargs: Any) -> None:
        """Unlock the window lock."""
        await self._async_set_locked(False)

    async def _async_set_locked(self, locked: bool) -> None:
        """Send a lock command with an optimistic target."""
        self.coordinator.set_lock_target(self.device_id, locked)
        try:
            await self.coordinator.async_action(
                self.device_id,
                ACTION_LOCK if locked else ACTION_UNLOCK,
                dpid=DPID_ACTION,
            )
        except HomeAssistantError:
            self.coordinator.clear_target(self.device_id)
            raise
