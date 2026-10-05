"""EVECCA integration for Home Assistant."""

import logging
from dataclasses import replace
from datetime import datetime

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval

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
    SCAN_INTERVAL,
)
from .coordinator import EveccaCoordinator
from .device_info import evecca_device_info
from .models import EveccaMqttConfig, EveccaSession
from .mqtt import EveccaMqttClient
from .runtime import EveccaConfigEntry, EveccaRuntimeData
from .session import EveccaSessionManager

_LOGGER = logging.getLogger(__name__)

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
    family_id = entry.data[CONF_FAMILY_ID]
    mqtt: EveccaMqttClient | None = None

    def store_session(refreshed: EveccaSession) -> None:
        _store_session(hass, entry, refreshed)
        if mqtt is not None:
            mqtt.update_config(replace(refreshed.mqtt, topic=str(family_id)))

    session = EveccaSessionManager(
        api,
        EveccaSession(
            token=entry.data[CONF_TOKEN],
            user_id=entry.data[CONF_USER_ID],
            mqtt=EveccaMqttConfig(
                host=entry.data[CONF_MQTT_HOST],
                port=entry.data[CONF_MQTT_PORT],
                username=entry.data[CONF_MQTT_USERNAME],
                password=entry.data[CONF_MQTT_PASSWORD],
                topic=str(family_id),
            ),
        ),
        entry.data[CONF_HW_ID],
        store_session,
    )
    try:
        await session.async_refresh()
    except EveccaAuthError as err:
        raise ConfigEntryAuthFailed("EVECCA session renewal rejected") from err
    except (EveccaApiError, EveccaConnectionError) as err:
        raise ConfigEntryNotReady(f"Cannot refresh EVECCA session: {err}") from err

    coordinator = EveccaCoordinator(hass, entry, api, session, family_id)
    mqtt = EveccaMqttClient(
        replace(session.session.mqtt, topic=str(family_id)),
        family_id,
        client_id=f"ha-evecca-{entry.data[CONF_HW_ID]}",
        on_update=coordinator.handle_mqtt_update,
    )
    entry.runtime_data = EveccaRuntimeData(coordinator=coordinator, mqtt=mqtt)

    await coordinator.async_load_error_codes()
    await coordinator.async_config_entry_first_refresh()
    _register_controller_devices(hass, entry, coordinator)
    entry.async_create_background_task(hass, mqtt.run(), name="evecca-mqtt")

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _schedule_session_renewal(hass, entry, session)
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


def _store_session(
    hass: HomeAssistant,
    entry: EveccaConfigEntry,
    session: EveccaSession,
) -> None:
    """Persist renewed credentials before subsequent requests use them."""
    mqtt = session.mqtt
    data = {
        **entry.data,
        CONF_TOKEN: session.token,
        CONF_USER_ID: session.user_id,
        CONF_MQTT_HOST: mqtt.host,
        CONF_MQTT_PORT: mqtt.port,
        CONF_MQTT_USERNAME: mqtt.username,
        CONF_MQTT_PASSWORD: mqtt.password,
        CONF_MQTT_TOPIC: str(entry.data[CONF_FAMILY_ID]),
    }
    if data != dict(entry.data):
        hass.config_entries.async_update_entry(entry, data=data)


def _schedule_session_renewal(
    hass: HomeAssistant,
    entry: EveccaConfigEntry,
    session: EveccaSessionManager,
) -> None:
    """Renew even when frequent MQTT pushes postpone HTTPS polling."""

    async def refresh_if_due() -> None:
        try:
            await session.async_renew_if_due()
        except EveccaAuthError:
            cancel()
            entry.async_start_reauth(hass)
        except (EveccaApiError, EveccaConnectionError) as err:
            # Keep the session and retry on the next tick, not via a login prompt.
            _LOGGER.warning("Cannot renew EVECCA session: %s", err)

    async def renew_session(_now: datetime) -> None:
        await entry.async_create_background_task(
            hass, refresh_if_due(), name="evecca-session-renewal"
        )

    cancel = async_track_time_interval(hass, renew_session, SCAN_INTERVAL)
    entry.async_on_unload(cancel)
