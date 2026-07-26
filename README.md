# Trueguard Home Assistant Integration

Custom integration for connecting Trueguard / Woonveilig alarm systems to Home Assistant.
The project is based on the Egardia integration and adapted for Trueguard variants.

## What's included
- Alarm control panel entity (arm away, arm home, disarm).
- Binary sensors for door, motion, smoke and related device types.
- Per-sensor health/diagnostic values as attributes on the main sensor entity:
  - `battery_ok`
  - `tamper_ok`
  - `rssi`
- State-aware icons for alarm, sensor, and diagnostic entities.
- Device registry linking so entities appear under the integration device overview.
- Config Flow support (UI setup in Home Assistant).
- Local integration branding assets via `custom_components/trueguard/brand/`.

## Setup
### Preferred: UI setup
1. Go to `Settings -> Devices & Services`.
2. Click `Add Integration`.
3. Search for `Trueguard`.
4. Fill in host, port, username, password, and panel version.

### Legacy YAML setup (still supported)
```yaml
trueguard:
  host: YOUR_HOST
  username: YOUR_USERNAME
  password: YOUR_PASSWORD
  version: SMARTHOME
```

If you migrate from YAML to UI setup, remove the YAML block after successful UI setup to avoid duplicate setup.

## Supported panel versions
`WV-1716`, `GATE-01`, `GATE-02`, `GATE-03`, `SMARTHOME`

## HACS notes
- Repository includes `hacs.json` and is compatible with custom repository installation in HACS.
- For release detection, keep `custom_components/trueguard/manifest.json` `version` aligned with git tags.

## Release 2.0.1 highlights
- Added UI setup flow (`config_flow` + config entry setup paths).
- Added diagnostics for battery/tamper/signal.
- Improved SMARTHOME state parsing and sensor type handling.
- Added entity icons and better siren/door class mapping.
- Added local `brand/` assets support for newer Home Assistant branding behavior.

## Release 2.1.0 highlights
- Consolidated per-sensor diagnostics into attributes on the main sensor entity (no extra battery/tamper/signal entities).
- Updated config-entry platform setup so entities are correctly owned by the integration domain.
- Improved sensor naming to use clean panel sensor names without `trueguard_` prefix.
