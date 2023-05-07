# homeassistant_trueguard

This is a custom component for integrating Trueguard alarms in Home Assistant.
The code is based on the Egardia integration and modified to support Trueguard.
Currently only tested with the SmartHome panel.

# Example `configuration.yaml` entry:
``` yaml
trueguard:
  host: YOUR_HOST
  username: YOUR_USERNAME
  password: YOUR_PASSWORD
  version: SMARTHOME
```
