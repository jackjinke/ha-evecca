"""Config flow for EVECCA."""

import logging
import uuid
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import SOURCE_REAUTH, ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_CODE, CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

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
    CONF_FAMILY_NAME,
    CONF_HW_ID,
    CONF_MQTT_HOST,
    CONF_MQTT_PASSWORD,
    CONF_MQTT_PORT,
    CONF_MQTT_TOPIC,
    CONF_MQTT_USERNAME,
    CONF_TOKEN,
    CONF_USER_ID,
    DOMAIN,
)
from .models import EveccaFamily, EveccaSession

_LOGGER = logging.getLogger(__name__)


class EveccaConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle EVECCA setup and reauthentication."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize flow state."""
        self._hw_id = str(uuid.uuid4())
        self._base_url = BASE_URL
        self._username: str | None = None
        self._session: EveccaSession | None = None
        self._families: dict[str, EveccaFamily] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the preferred login method."""
        self._base_url = (
            await async_discover_base_url(async_get_clientsession(self.hass))
            or BASE_URL
        )
        return self.async_show_menu(
            step_id="user",
            menu_options=["password", "sms"],
        )

    async def async_step_password(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Log in with a password."""
        errors: dict[str, str] = {}
        if user_input is not None:
            username = user_input[CONF_USERNAME].strip()
            try:
                self._session = await self._api().async_password_login(
                    username,
                    user_input[CONF_PASSWORD],
                    self._hw_id,
                )
            except EveccaAuthError:
                errors["base"] = "invalid_auth"
            except EveccaConnectionError:
                errors["base"] = "cannot_connect"
            except EveccaApiError:
                _LOGGER.exception("Unexpected EVECCA password login failure")
                errors["base"] = "unknown"
            else:
                self._username = username
                return await self._async_after_login()

        return self.async_show_form(
            step_id="password",
            data_schema=_password_schema(self._username),
            errors=errors,
        )

    async def async_step_sms(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Send an SMS login code."""
        errors: dict[str, str] = {}
        if user_input is not None:
            username = user_input[CONF_USERNAME].strip()
            try:
                await self._api().async_send_login_code(username, self._hw_id)
            except EveccaAuthError:
                errors["base"] = "invalid_auth"
            except EveccaConnectionError:
                errors["base"] = "cannot_connect"
            except EveccaApiError:
                _LOGGER.exception("Unexpected EVECCA SMS request failure")
                errors["base"] = "unknown"
            else:
                self._username = username
                return self.async_show_form(
                    step_id="sms_code",
                    data_schema=_sms_code_schema(),
                    errors={},
                )

        return self.async_show_form(
            step_id="sms",
            data_schema=_sms_schema(self._username),
            errors=errors,
        )

    async def async_step_sms_code(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Log in with the received SMS code."""
        errors: dict[str, str] = {}
        if user_input is not None and self._username is not None:
            code = user_input[CONF_CODE].strip()
            if not code.isdigit():
                errors[CONF_CODE] = "invalid_code"
            else:
                try:
                    self._session = await self._api().async_code_login(
                        self._username,
                        int(code),
                        self._hw_id,
                    )
                except EveccaAuthError:
                    errors["base"] = "invalid_auth"
                except EveccaConnectionError:
                    errors["base"] = "cannot_connect"
                except EveccaApiError:
                    _LOGGER.exception("Unexpected EVECCA SMS login failure")
                    errors["base"] = "unknown"
                else:
                    return await self._async_after_login()

        return self.async_show_form(
            step_id="sms_code",
            data_schema=_sms_code_schema(),
            errors=errors,
        )

    async def async_step_family(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select a family when the account has multiple homes."""
        errors: dict[str, str] = {}
        if user_input is not None:
            family = self._families.get(user_input[CONF_FAMILY_ID])
            if family is None:
                errors["base"] = "unknown"
            else:
                return await self._async_complete(family)

        return self.async_show_form(
            step_id="family",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_FAMILY_ID): SelectSelector(
                        SelectSelectorConfig(
                            mode=SelectSelectorMode.DROPDOWN,
                            options=[
                                {"value": family_id, "label": family.name}
                                for family_id, family in self._families.items()
                            ],
                        )
                    )
                }
            ),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start reauthentication for an existing entry."""
        self._base_url = (
            await async_discover_base_url(async_get_clientsession(self.hass))
            or BASE_URL
        )
        self._username = str(entry_data.get(CONF_USERNAME, ""))
        self._hw_id = str(entry_data.get(CONF_HW_ID, self._hw_id))
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask how to reauthenticate."""
        return self.async_show_menu(
            step_id="reauth_confirm",
            menu_options=["password", "sms"],
        )

    async def _async_after_login(self) -> ConfigFlowResult:
        """Load families after a successful login."""
        if self._session is None:
            return self.async_abort(reason="unknown")

        try:
            families = await self._api().async_families(self._session)
        except EveccaAuthError:
            return self.async_show_form(
                step_id="password",
                data_schema=_password_schema(self._username),
                errors={"base": "invalid_auth"},
            )
        except (EveccaApiError, EveccaConnectionError):
            _LOGGER.exception("Cannot load EVECCA families")
            return self.async_abort(reason="cannot_connect")

        if not families:
            return self.async_abort(reason="no_families")

        if self.source == SOURCE_REAUTH:
            entry = self._get_reauth_entry()
            family_id = entry.data[CONF_FAMILY_ID]
            family = next(
                (item for item in families if item.family_id == family_id),
                None,
            )
            if family is None:
                return self.async_abort(reason="family_not_found")
            return await self._async_complete(family)

        if len(families) == 1:
            return await self._async_complete(families[0])

        self._families = {str(family.family_id): family for family in families}
        return self.async_show_form(
            step_id="family",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_FAMILY_ID): SelectSelector(
                        SelectSelectorConfig(
                            mode=SelectSelectorMode.DROPDOWN,
                            options=[
                                {"value": family_id, "label": family.name}
                                for family_id, family in self._families.items()
                            ],
                        )
                    )
                }
            ),
            errors={},
        )

    async def _async_complete(self, family: EveccaFamily) -> ConfigFlowResult:
        """Create or update the config entry."""
        if self._session is None or self._username is None:
            return self.async_abort(reason="unknown")

        await self.async_set_unique_id(f"{self._session.user_id}:{family.family_id}")
        data = {
            CONF_USERNAME: self._username,
            CONF_HW_ID: self._hw_id,
            CONF_TOKEN: self._session.token,
            CONF_USER_ID: self._session.user_id,
            CONF_FAMILY_ID: family.family_id,
            CONF_FAMILY_NAME: family.name,
            CONF_MQTT_HOST: family.mqtt.host,
            CONF_MQTT_PORT: family.mqtt.port,
            CONF_MQTT_USERNAME: family.mqtt.username,
            CONF_MQTT_PASSWORD: family.mqtt.password,
            CONF_MQTT_TOPIC: family.mqtt.topic,
        }

        if self.source == SOURCE_REAUTH:
            entry = self._get_reauth_entry()
            self._abort_if_unique_id_mismatch(reason="wrong_account")
            return self.async_update_reload_and_abort(
                entry,
                data_updates=data,
            )

        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=f"EVECCA {family.name}",
            data=data,
        )

    def _api(self) -> EveccaApi:
        """Return an API client for this flow."""
        return EveccaApi(
            async_get_clientsession(self.hass),
            base_url=self._base_url,
        )


def _password_schema(username: str | None) -> vol.Schema:
    """Return the password login schema."""
    return vol.Schema(
        {
            vol.Required(CONF_USERNAME, default=username or ""): str,
            vol.Required(CONF_PASSWORD): TextSelector(
                TextSelectorConfig(type=TextSelectorType.PASSWORD)
            ),
        }
    )


def _sms_schema(username: str | None) -> vol.Schema:
    """Return the SMS username schema."""
    return vol.Schema({vol.Required(CONF_USERNAME, default=username or ""): str})


def _sms_code_schema() -> vol.Schema:
    """Return the SMS code schema."""
    return vol.Schema({vol.Required(CONF_CODE): str})
