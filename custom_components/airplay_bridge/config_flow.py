"""Config flow: found automatically via the add-on, or set up by hand."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.service_info.hassio import HassioServiceInfo

from .api import BridgeAuthError, BridgeClient, BridgeError
from .const import CONF_TOKEN, DEFAULT_HOST, DEFAULT_PORT, DOMAIN


class AirPlayBridgeConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._discovered: dict[str, Any] = {}
        self._addon = "AirPlay 2 → Sendspin Bridge"

    async def _validate(self, data: dict[str, Any]) -> str | None:
        client = BridgeClient(
            async_get_clientsession(self.hass),
            data[CONF_HOST],
            data[CONF_PORT],
            data[CONF_TOKEN],
        )
        try:
            await client.state()
        except BridgeAuthError:
            return "invalid_auth"
        except BridgeError:
            return "cannot_connect"
        return None

    async def async_step_hassio(
        self, discovery_info: HassioServiceInfo
    ) -> ConfigFlowResult:
        """The add-on announced itself (Supervisor discovery)."""
        config = discovery_info.config
        self._discovered = {
            CONF_HOST: config.get(CONF_HOST, DEFAULT_HOST),
            CONF_PORT: int(config.get(CONF_PORT, DEFAULT_PORT)),
            CONF_TOKEN: config.get(CONF_TOKEN, ""),
        }
        await self.async_set_unique_id(DOMAIN)
        # Already set up: take over a new token/port (e.g. add-on reinstalled)
        self._abort_if_unique_id_configured(updates=self._discovered)
        self._addon = discovery_info.name or self._addon
        self.context["title_placeholders"] = {"name": self._addon}
        return await self.async_step_hassio_confirm()

    async def async_step_hassio_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if (error := await self._validate(self._discovered)) is None:
                return self.async_create_entry(
                    title="AirPlay Bridge", data=self._discovered
                )
            errors["base"] = error
        return self.async_show_form(
            step_id="hassio_confirm",
            description_placeholders={"addon": self._addon},
            errors=errors,
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Set up by hand: address, port and the token from the add-on log."""
        errors: dict[str, str] = {}
        if user_input is not None:
            await self.async_set_unique_id(DOMAIN)
            self._abort_if_unique_id_configured()
            if (error := await self._validate(user_input)) is None:
                return self.async_create_entry(title="AirPlay Bridge", data=user_input)
            errors["base"] = error
        schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default=DEFAULT_HOST): str,
                vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
                vol.Required(CONF_TOKEN): str,
            }
        )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(schema, user_input),
            errors=errors,
        )

    async def async_step_reauth(self, _entry_data: dict[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """New token (e.g. after reinstalling the add-on)."""
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            data = {**entry.data, CONF_TOKEN: user_input[CONF_TOKEN]}
            if (error := await self._validate(data)) is None:
                return self.async_update_reload_and_abort(entry, data=data)
            errors["base"] = error
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_TOKEN): str}),
            errors=errors,
        )
