# ESPHome Firmware Bridge

[![Validate](https://github.com/RF1705/ESPHome-Firmware-Bridge/actions/workflows/validate.yml/badge.svg)](https://github.com/RF1705/ESPHome-Firmware-Bridge/actions/workflows/validate.yml)
[![Hassfest](https://github.com/RF1705/ESPHome-Firmware-Bridge/actions/workflows/hassfest.yml/badge.svg)](https://github.com/RF1705/ESPHome-Firmware-Bridge/actions/workflows/hassfest.yml)

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

In HACS, open the three-dot menu, choose `Custom repositories`, and add:

```text
https://github.com/RF1705/ESPHome-Firmware-Bridge
```

Use category `Integration`, then install `ESPHome Firmware Bridge` from HACS.

After restarting Home Assistant, add the integration from:

`Settings -> Devices & services -> Add integration -> ESPHome Firmware Bridge`

## Configuration

During setup, provide the URL of your ESPHome Dashboard / Device Builder, for
example:

```text
http://homeassistant.local:6052
```

If the dashboard is protected by a reverse proxy, enter the configured username
and password. The optional node filter accepts comma-separated ESPHome node
names if only selected devices should be exposed as update entities.

## Support

If this integration helps you, you can support the project here:

[Buy me a coffee](https://buymeacoffee.com/rf1705)

## Notes

ESPHome Dashboard endpoints have changed over time and are less formally
documented than Home Assistant's own APIs. This integration therefore keeps the
Dashboard access isolated in one client and tries multiple endpoint shapes.

If your ESPHome Dashboard is protected by a reverse proxy, configure the
username and password in the integration setup.

## License

MIT
