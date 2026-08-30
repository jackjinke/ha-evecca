"""Data update coordinator for EVECCA devices."""

import logging
from dataclasses import dataclass, replace

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import EveccaApi, EveccaApiError, EveccaAuthError, EveccaConnectionError
from .const import SCAN_INTERVAL
from .models import EveccaDevice, EveccaSession
from .mqtt import EveccaMqttUpdate
from .runtime import EveccaConfigEntry

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class EveccaData:
    """Current EVECCA integration data."""

    devices: dict[int, EveccaDevice]


class EveccaCoordinator(DataUpdateCoordinator[EveccaData]):
    """Coordinate HTTPS reconciliation and MQTT updates."""

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
            device.device_id: replace(
                device,
                online=previous.get(device.device_id, device).online,
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

    def handle_mqtt_update(self, update: EveccaMqttUpdate) -> None:
        """Apply one pushed MQTT update."""
        if self.data is None or update.device_id not in self.data.devices:
            return

        device = self.data.devices[update.device_id]
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
        self.async_set_updated_data(
            EveccaData(devices={**self.data.devices, update.device_id: updated})
        )
