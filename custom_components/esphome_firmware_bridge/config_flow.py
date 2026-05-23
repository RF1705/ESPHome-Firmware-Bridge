"""Config flow for ESPHome Firmware Bridge."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CONF_DASHBOARD_URL,
    CONF_NODE_FILTER,
    CONF_VERIFY_SSL,
    DEFAULT_NAME,
    DOMAIN,
)
from .dashboard import ESPHomeDashboardClient, ESPHomeDashboardError


class ESPHomeDockerFirmwareConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for ESPHome Firmware Bridge."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                await _validate_input(self.hass, user_input)
            except ESPHomeDashboardError:
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(user_input[CONF_DASHBOARD_URL])
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=DEFAULT_NAME, data=user_input)

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_DASHBOARD_URL): str,
                    vol.Optional(CONF_USERNAME): str,
                    vol.Optional(CONF_PASSWORD): str,
                    vol.Optional(CONF_VERIFY_SSL, default=True): bool,
                    vol.Optional(CONF_NODE_FILTER, default=""): str,
                }
            ),
            errors=errors,
        )


async def _validate_input(hass: HomeAssistant, data: dict[str, Any]) -> None:
    """Validate that ESPHome Dashboard can be reached."""
    client = ESPHomeDashboardClient(
        session=async_get_clientsession(hass),
        dashboard_url=data[CONF_DASHBOARD_URL],
        username=data.get(CONF_USERNAME),
        password=data.get(CONF_PASSWORD),
        verify_ssl=data.get(CONF_VERIFY_SSL, True),
    )
    await client.async_get_nodes()
