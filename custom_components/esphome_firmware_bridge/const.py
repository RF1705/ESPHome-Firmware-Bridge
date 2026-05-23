"""Constants for ESPHome Firmware Bridge."""

from __future__ import annotations

DOMAIN = "esphome_firmware_bridge"

CONF_DASHBOARD_URL = "dashboard_url"
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_VERIFY_SSL = "verify_ssl"
CONF_NODE_FILTER = "node_filter"

DEFAULT_NAME = "ESPHome Firmware Bridge"
DEFAULT_SCAN_INTERVAL_SECONDS = 3600

PLATFORMS = ["update"]
