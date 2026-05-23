# ESPHome Docker Firmware Updates

HACS custom integration for Home Assistant Core installations that run ESPHome
Device Builder in Docker.

The integration creates Home Assistant update entities for ESPHome nodes and
delegates firmware builds and OTA installs to the existing ESPHome Dashboard.
This aims to mimic the Home Assistant OS workflow where an ESPHome firmware
update can be started from Home Assistant without opening the ESPHome UI.

## Requirements

- Home Assistant Core 2024.8 or newer
- A reachable ESPHome Dashboard / Device Builder instance, for example
  `http://homeassistant.local:6052`
- ESPHome device YAML files available inside that ESPHome Dashboard
- ESPHome OTA enabled in every node:

```yaml
ota:
  - platform: esphome
```

## Installation

Copy this repository into HACS as a custom repository with category
`Integration`, then install it from HACS.

After restarting Home Assistant, add the integration from:

`Settings -> Devices & services -> Add integration -> ESPHome Docker Firmware Updates`

## Notes

ESPHome Dashboard endpoints have changed over time and are less formally
documented than Home Assistant's own APIs. This integration therefore keeps the
Dashboard access isolated in one client and tries multiple endpoint shapes.

If your ESPHome Dashboard is protected by a reverse proxy, configure the
username and password in the integration setup.
