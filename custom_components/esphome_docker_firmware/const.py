"""Constants for ESPHome Docker Firmware Updates."""

from __future__ import annotations

DOMAIN = "esphome_docker_firmware"

CONF_DASHBOARD_URL = "dashboard_url"
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_VERIFY_SSL = "verify_ssl"
CONF_NODE_FILTER = "node_filter"

DEFAULT_NAME = "ESPHome Docker Firmware Updates"
DEFAULT_SCAN_INTERVAL_SECONDS = 3600

PLATFORMS = ["update"]
