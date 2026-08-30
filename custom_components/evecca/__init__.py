"""EVECCA integration for Home Assistant."""

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    EveccaApi,
    EveccaApiError,
    EveccaAuthError,
    EveccaConnectionError,
    async_discover_base_url,
)
from .const import (
    BASE_URL,
    CONF_FAMILY_ID,
    CONF_HW_ID,
    CONF_MQTT_HOST,
    CONF_MQTT_PASSWORD,
    CONF_MQTT_PORT,
    CONF_MQTT_TOPIC,
    CONF_MQTT_USERNAME,
    CONF_TOKEN,
    CONF_USER_ID,
    MODEL_CONTROLLER_PREFIX,
)
from .coordinator import EveccaCoordinator
from .device_info import evecca_device_info
from .models import EveccaMqttConfig, EveccaSession
from .mqtt import EveccaMqttClient
from .runtime import EveccaConfigEntry, EveccaRuntimeData

PLATFORMS = [
    Platform.BUTTON,
    Platform.COVER,
    Platform.LOCK,
    Platform.SELECT,
    Platform.SENSOR,
]


async def async_setup_entry(hass: HomeAssistant, entry: EveccaConfigEntry) -> bool:
    """Set up EVECCA from a config entry."""
    http_session = async_get_clientsession(hass)
    base_url = await async_discover_base_url(http_session)
    api = EveccaApi(http_session, base_url=base_url or BASE_URL)
    session = await _async_refresh_session(hass, entry, api)
    family_id = entry.data[CONF_FAMILY_ID]

    coordinator = EveccaCoordinator(hass, entry, api, session, family_id)
    await coordinator.async_load_error_codes()
    mqtt = EveccaMqttClient(
        session.mqtt,
        family_id,
        client_id=f"ha-evecca-{entry.data[CONF_HW_ID]}",
        on_update=coordinator.handle_mqtt_update,
    )
    entry.runtime_data = EveccaRuntimeData(coordinator=coordinator, mqtt=mqtt)

    await coordinator.async_config_entry_first_refresh()
    _register_controller_devices(hass, entry, coordinator)
    entry.async_create_background_task(hass, mqtt.run(), name="evecca-mqtt")

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: EveccaConfigEntry) -> bool:
    """Unload an EVECCA config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


def _register_controller_devices(
    hass: HomeAssistant,
    entry: EveccaConfigEntry,
    coordinator: EveccaCoordinator,
) -> None:
    """Create parent registry entries for controllers without entities."""
    device_registry = dr.async_get(hass)
    for device in coordinator.data.devices.values():
        if device.model.startswith(MODEL_CONTROLLER_PREFIX):
            device_registry.async_get_or_create(
                config_entry_id=entry.entry_id,
                **evecca_device_info(device),
            )


async def _async_refresh_session(
    hass: HomeAssistant,
    entry: EveccaConfigEntry,
    api: EveccaApi,
) -> EveccaSession:
    """Refresh the saved token and MQTT credentials."""
    family_id = entry.data[CONF_FAMILY_ID]
    try:
        refreshed = await api.async_token_login(
            entry.data[CONF_TOKEN],
            entry.data[CONF_USER_ID],
            entry.data[CONF_HW_ID],
        )
    except EveccaAuthError as err:
        raise ConfigEntryAuthFailed("EVECCA token expired") from err
    except (EveccaApiError, EveccaConnectionError) as err:
        raise ConfigEntryNotReady(f"Cannot refresh EVECCA session: {err}") from err

    mqtt = EveccaMqttConfig(
        host=refreshed.mqtt.host,
        port=refreshed.mqtt.port,
        username=refreshed.mqtt.username,
        password=refreshed.mqtt.password,
        topic=str(family_id),
    )
    session = EveccaSession(
        token=refreshed.token,
        user_id=refreshed.user_id,
        mqtt=mqtt,
    )

    data = {
        **entry.data,
        CONF_TOKEN: session.token,
        CONF_USER_ID: session.user_id,
        CONF_MQTT_HOST: mqtt.host,
        CONF_MQTT_PORT: mqtt.port,
        CONF_MQTT_USERNAME: mqtt.username,
        CONF_MQTT_PASSWORD: mqtt.password,
        CONF_MQTT_TOPIC: mqtt.topic,
    }
    if data != dict(entry.data):
        hass.config_entries.async_update_entry(entry, data=data)
    return session
