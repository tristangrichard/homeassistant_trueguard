# homeassistant_trueguard

This is a custom component for integrating Trueguard (Climax) alarms in Home Assistant.
The code is based on the [Egardia](https://github.com/home-assistant/core/tree/dev/homeassistant/components/egardia) integration and modified to support Trueguard.
Currently only tested with the SmartHome panel.

# Example `configuration.yaml` entry:
``` yaml
trueguard:
  host: YOUR_HOST
  username: YOUR_USERNAME
  password: YOUR_PASSWORD
  version: SMARTHOME
```

# Supported versions
`WL-1716`, `GATE-01`, `GATE-02`, `GATE-03` and `SMARTHOME`