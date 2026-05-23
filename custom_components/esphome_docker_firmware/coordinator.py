"""Data coordinator for ESPHome Docker Firmware Updates."""

from __future__ import annotations

from datetime import timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_DASHBOARD_URL,
    CONF_NODE_FILTER,
    CONF_PASSWORD,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
    DEFAULT_SCAN_INTERVAL_SECONDS,
    DOMAIN,
)
from .dashboard import DashboardNode, ESPHomeDashboardClient, ESPHomeDashboardError

_LOGGER = logging.getLogger(__name__)


class ESPHomeDockerFirmwareCoordinator(DataUpdateCoordinator[list[DashboardNode]]):
    """Coordinate node data from ESPHome Dashboard."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the coordinator."""
        self.config_entry = entry
        data = {**entry.data, **entry.options}
        session = async_get_clientsession(hass)
        self.client = ESPHomeDashboardClient(
            session=session,
            dashboard_url=data[CONF_DASHBOARD_URL],
            username=data.get(CONF_USERNAME),
            password=data.get(CONF_PASSWORD),
            verify_ssl=data.get(CONF_VERIFY_SSL, True),
        )
        self._node_filter = _parse_node_filter(data.get(CONF_NODE_FILTER, ""))

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL_SECONDS),
        )

    async def _async_update_data(self) -> list[DashboardNode]:
        """Fetch data from ESPHome Dashboard."""
        try:
            nodes = await self.client.async_get_nodes()
        except ESPHomeDashboardError as err:
            raise UpdateFailed(str(err)) from err

        if self._node_filter:
            nodes = [node for node in nodes if node.name in self._node_filter]

        return nodes


def _parse_node_filter(value: str) -> set[str]:
    """Parse a comma-separated node filter."""
    return {item.strip() for item in value.split(",") if item.strip()}
