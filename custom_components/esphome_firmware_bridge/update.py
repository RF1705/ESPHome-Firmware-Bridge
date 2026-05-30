"""Update entities for ESPHome Firmware Bridge."""

from __future__ import annotations

from homeassistant.components.update import UpdateEntity, UpdateEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import ESPHomeDockerFirmwareCoordinator
from .dashboard import DashboardNode


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up ESPHome firmware update entities."""
    coordinator: ESPHomeDockerFirmwareCoordinator = hass.data[DOMAIN][entry.entry_id]
    known_nodes: set[str] = set()

    def add_new_entities() -> None:
        entities = []
        for node in coordinator.data:
            if node.name in known_nodes:
                continue
            known_nodes.add(node.name)
            entities.append(ESPHomeFirmwareUpdateEntity(coordinator, node))

        if entities:
            async_add_entities(entities)

    add_new_entities()
    entry.async_on_unload(coordinator.async_add_listener(add_new_entities))


class ESPHomeFirmwareUpdateEntity(
    CoordinatorEntity[ESPHomeDockerFirmwareCoordinator], UpdateEntity
):
    """ESPHome firmware update entity backed by ESPHome Dashboard."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_supported_features = UpdateEntityFeature.INSTALL

    def __init__(
        self,
        coordinator: ESPHomeDockerFirmwareCoordinator,
        node: DashboardNode,
    ) -> None:
        """Initialize the update entity."""
        super().__init__(coordinator)
        self._node_name = node.name
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_{node.name}"
        self._attr_translation_key = "firmware"
        self._attr_has_entity_name = True
        self._attr_name = f"{node.name} Firmware"

    @property
    def node(self) -> DashboardNode | None:
        """Return the current node data."""
        for node in self.coordinator.data:
            if node.name == self._node_name:
                return node
        return None

    @property
    def available(self) -> bool:
        """Return if the entity is available."""
        return self.node is not None and super().available

    @property
    def installed_version(self) -> str | None:
        """Return the installed firmware version."""
        if (node := self.node) is None:
            return None
        return node.installed_version or _find_esphome_device_version(
            self.coordinator.hass, node.name
        )

    @property
    def latest_version(self) -> str | None:
        """Return the latest available firmware version."""
        if (node := self.node) is None:
            return None
        return node.latest_version

    @property
    def in_progress(self) -> bool:
        """Return if an update is running."""
        return False

    @property
    def device_info(self):
        """Return device information."""
        if (node := self.node) is None:
            return None

        device_info = {
            "identifiers": {(DOMAIN, node.name)},
            "name": node.name,
            "manufacturer": "ESPHome",
            "sw_version": self.installed_version,
            "configuration_url": self.coordinator.config_entry.data.get(
                "dashboard_url"
            ),
        }

        esphome_device = _find_esphome_device(self.coordinator.hass, node.name)
        if esphome_device is not None:
            device_info["name"] = (
                esphome_device.name_by_user or esphome_device.name or node.name
            )
            if esphome_device.connections:
                device_info["connections"] = set(esphome_device.connections)
            device_info["identifiers"] = {
                *esphome_device.identifiers,
                (DOMAIN, node.name),
            }

        return device_info

    async def async_install(
        self,
        version: str | None,
        backup: bool,
        **kwargs,
    ) -> None:
        """Build and OTA install firmware through ESPHome Dashboard."""
        if (node := self.node) is None:
            return

        await self.coordinator.client.async_install(node)
        await self.coordinator.async_request_refresh()


def _find_esphome_device_version(hass: HomeAssistant, node_name: str) -> str | None:
    """Find the firmware version Home Assistant knows for an ESPHome node."""
    if (device := _find_esphome_device(hass, node_name)) is None:
        return None

    return getattr(device, "sw_version", None)


def _find_esphome_device(hass: HomeAssistant, node_name: str):
    """Find the Home Assistant device registry entry for an ESPHome node."""
    registry = dr.async_get(hass)
    normalized_node = _normalize_name(node_name)
    esphome_entry_ids = {
        entry.entry_id for entry in hass.config_entries.async_entries("esphome")
    }

    for device in registry.devices.values():
        has_esphome_identifier = any(
            identifier[0] == "esphome" for identifier in device.identifiers
        )
        has_esphome_entry = bool(device.config_entries & esphome_entry_ids)
        if not has_esphome_identifier and not has_esphome_entry:
            continue

        candidates = {
            *(str(identifier[1]) for identifier in device.identifiers),
            *(str(connection[1]) for connection in device.connections),
            device.name or "",
            device.name_by_user or "",
        }
        if any(
            _normalize_name(candidate) == normalized_node for candidate in candidates
        ):
            return device

    return None


def _normalize_name(value: str) -> str:
    """Normalize names for loose Dashboard-to-device-registry matching."""
    return value.lower().replace("_", "-").replace(" ", "-")
