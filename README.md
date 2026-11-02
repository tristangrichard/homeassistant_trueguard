# Trueguard Home Assistant Integration

Custom integration for connecting Trueguard (Climax-based) alarm panels to Home Assistant.
It started as a fork of Home Assistant's Egardia integration; since 2.6.0 none
of that code remains.

## What's included
- Alarm control panel entity (arm away, arm home, disarm).
- Binary sensors for door, motion, smoke and related device types.
- Per-sensor health/diagnostic values as attributes on the main sensor entity:
  - `battery_ok`
  - `tamper_ok`
  - `rssi`
- Per-zone **battery** and **tamper** diagnostic sensors (on = problem), and a
  per-zone **signal** sensor (disabled by default; enable it per zone).
- Panel status sensors when the panel reports them: **Panel battery**,
  **Panel tamper**, **Radio interference**, **230 V power**, **GSM problem**,
  and **GSM signal**.
- Optional push receiver: the panel reports alarms, arming, and faults to Home
  Assistant as they happen (SIA DC-09 / Contact ID). The alarm shows
  **triggered** with the cause, zone, and time, and every report fires a
  `trueguard_report` event.
- Discovery of Climax-based panels on the network (DHCP), and following the
  panel when its IP address changes.
- Diagnostics download with credentials, MAC, and account redacted.
- State-aware icons for alarm and sensor entities.
- Device registry linking so entities appear under the integration device overview.
- Config Flow support (UI setup in Home Assistant).
- Local integration branding assets via `custom_components/trueguard/brand/`.

## Setup
### Preferred: UI setup
1. Go to `Settings -> Devices & Services`.
2. Click `Add Integration`.
3. Search for `Trueguard`.
4. Fill in host, port, username, password, and panel version.

### Changing settings after setup
`Settings -> Devices & Services -> Trueguard`:

- **Configure**
  - **Polling interval** (1–3600 seconds, default 5). Each poll makes two
    requests to the panel (alarm state and sensor list). A shorter interval
    updates sensors faster but puts more load on the panel's small web server;
    raise it if the panel starts timing out. Applied from the next poll,
    without a restart.
  - **Receive reports from the panel** and **Report server port**. Changing
    these restarts the integration once.
- **Reconfigure** (in the integration's ⋮ menu): change the panel's host/IP,
  port, panel version, username, or password. The new details are checked
  against the panel before they are saved. The device and all entity IDs stay
  the same, so automations and dashboards keep working.
- If the panel rejects the saved password, Home Assistant asks you to sign in
  again.

### Push reports from the panel
Without push reports, Home Assistant only learns what the panel is doing by
polling. The panel's own alarm reporting (the feature it uses for TrueConnect
or a monitoring centre) can also send every alarm, arm/disarm, and fault
report straight to Home Assistant.

1. In Home Assistant: **Configure**, turn on **Receive reports from the
   panel**, keep port `52010` (or pick another free port), and optionally enter
   an account number.
2. On the panel, under **Rapport indst.**, pick a **free** row and set it to

   ```
   ip://1234@HOME-ASSISTANT-IP:52010/CID_SIA
   ```

   where `1234` is the account number. Save, then press **Test**.

Never change a row that is already in use (for example the TrueConnect row or
a monitoring centre): that would stop their alarms. `/SIA` and `/CID` also
work; encrypted reporting (`/SES`) is not supported. Only connections from
the panel's own IP address are accepted, and the traffic is not encrypted, so
keep the panel and Home Assistant on a trusted network. In Docker, publish the
port or use host networking.

How reports affect the alarm entity:
- **Burglary** alarms show **triggered** until the panel is armed or disarmed
  again.
- **Fire, water, panic, and other 24-hour alarms** show **triggered** until the
  zone restores.
- A reported alarm clears after one hour at the latest. It survives a restart
  of Home Assistant, unless the panel was armed or disarmed in the meantime.
- **Duress** and **silent panic** only fire the event, never show triggered.
- **Arm/disarm reports** update the alarm at once.
- Every report also makes the integration read the panel straight away.

Every report fires a `trueguard_report` event with `format`, `account`,
`cid`, `qualifier`, `code`, `description`, `sia_code`, `group`, `zone`, and
`zone_name`, for example:

```yaml
trigger:
  - trigger: event
    event_type: trueguard_report
    event_data:
      cid: "1111"   # smoke alarm
```

### YAML setup (removed)
YAML setup was removed in 2.6.0. If your `configuration.yaml` still has a
`trueguard:` block, it is imported into the UI setup once (host, login, panel
version, polling interval, and report server settings are kept), and Home
Assistant shows a notice under **Settings -> Repairs** asking you to remove the
block. `report_server_codes` is no longer used: the push receiver understands
the panel's SIA / Contact ID reports by itself.

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

## Release 2.2.0 highlights
- Refactored polling to use a shared coordinator refresh path for both alarm state and binary sensors.
- Reduced duplicate panel requests by fan-out from one fetched payload (`state` + `sensors`) per refresh cycle.
- Improved icon mapping resilience for siren, keypad, and remote entities using type code + type text + name matching.
- Hardened sensor state parsing for known type codes (`2`, `4`, `11`, `27`, `37`, `45`, `46`) to reduce `None` state edge cases.

## Release 2.2.1 highlights
- Fixed alarm state updates after manual overrides by restoring `CoordinatorEntity` subscription in `alarm_control_panel` (`async_added_to_hass` now calls `super()`).
- Reduced log noise by changing per-poll request/state lines in `egardiadevice` from info level to debug level.

## Release 2.2.2 highlights
- Fixed `brand/icon.png` and `brand/icon@2x.png` to have an opaque white background inside the rounded-rect logo instead of transparency, so the icon renders cleanly instead of looking washed out.
- Resized icon assets to the `256x256` / `512x512` spec used by Home Assistant's brand icon system.

## Release 2.2.3 highlights
- Trimmed the empty transparent margin around the icon artwork and re-padded it to a consistent 10px (20px on the hDPI `icon@2x.png`) top/bottom margin, preserving aspect ratio, so the logo fills the icon frame properly.

## Release 2.3.0 highlights
- Pass `config_entry` explicitly to `TrueguardSensorCoordinator` instead of relying on Home Assistant's deprecated `ContextVar`-based auto-detection, ahead of that behavior being removed for core integrations in HA 2026.8 (custom integrations still get a deprecation warning today, but this avoids it).
- Added a missing `custom_components/trueguard/depend/__init__.py`. Without it, `depend` was an implicit namespace package, which broke `from .depend import egardiadevice, egardiaserver` on some Home Assistant setups with `ImportError: cannot import name 'egardiadevice' from 'custom_components.trueguard.depend' (unknown location)`.

## Release 2.3.1 highlights
- Home Assistant 2026.9 compatibility: entities only attach to a device when they belong to a config entry (UI setup). YAML-configured entities no longer report `device_info`, which avoids the new "entity without config entry cannot attach a device" warning (this becomes an error in HA 2027.8).
- Fixed a leftover debug string in the report-server status fallback (`UNKNOWN Tristan` → `UNKNOWN`).
- Removed the unused `sensor.py` signal-strength platform. It was no longer loaded after 2.1.0 consolidated diagnostics into binary sensor attributes; `rssi` remains available as an attribute.

## Release 2.3.2 highlights
- Adjustable polling interval (`Configure` in the UI, or `scan_interval` in YAML). The default is now 5 seconds instead of a fixed 1 second, which cuts requests to the panel by 80%.
- Fixed the report server never reaching the alarm entity with UI setup: the callback was looked up where only YAML setup stored the server.
- Fixed report-server events updating the alarm entity from the server's own thread; they are now handed to Home Assistant's event loop.
- Report codes without a mapping no longer clear the alarm state; they trigger an immediate refresh from the panel instead (useful because the UI setup has no report-code field).
- The report server now releases its port on unload/reload, runs as a daemon thread, survives malformed frames, and no longer adds its own DEBUG log handler.
- If the panel is unreachable when Home Assistant starts, setup is retried automatically (`ConfigEntryNotReady`) instead of failing permanently.
- A rejected login (at setup or while polling) now starts Home Assistant's reauthentication flow, so you can enter new credentials without removing the integration.
- The setup form now checks the credentials and panel version before creating the entry, and has English labels and error messages (`translations/en.json`).

## Release 2.4.0 highlights
- New async panel client (`api.py`) on Home Assistant's shared `aiohttp` session, replacing the blocking `requests`-based `depend/egardiadevice.py`. The alarm state and sensor list are read at the same time on each poll.
- Network errors: reads are retried once; arm/disarm commands are never retried after a network error (the panel may already have acted). Both are retried once if the panel answers "Unauthorized", which some panels do now and then.
- A failed arm/disarm command now shows an error in Home Assistant instead of only being logged.
- New parsers (`parser.py`) with a tolerant reader for the panel's JavaScript-style answers, so commas, colons, or quotes in sensor names no longer break parsing. Answers without a declared charset are read as UTF-8, falling back to Latin-1.
- Entities are only updated when something changed: the coordinator skips unchanged polls, and each sensor only writes its state when its own data or availability changed.
- Startup reads the panel once instead of twice.
- Tests: `python -m pytest tests` runs the parser tests, including real (anonymised) SMARTHOME answers.
- Report server on/off and port can be changed under **Configure** (the integration restarts once).
- New **Reconfigure** option to change host/IP, port, panel version, and login without removing the integration. The device and entity IDs stay the same.

## Release 2.5.0 highlights
- Panel status sensors from the panel's own status answer: battery, tamper, radio interference, 230 V power, GSM problem, and GSM signal.
- Per-zone battery and tamper diagnostic sensors, and per-zone signal sensors (disabled by default). The attributes on the main sensors are unchanged.
- New push receiver for SIA DC-09 and Contact ID reports, adapted from MRDonnii/trueguard-mz-home-assistant (MIT). The alarm can now show **triggered**, with cause, zone, and time attributes, and every report fires a `trueguard_report` event. Replaces the threaded `depend/egardiaserver.py`; the old Egardia frame format is still understood.
- Optional account number for the receiver under **Configure**.
- DHCP discovery of Climax panels (MAC prefix `00:1D:94`). A panel set up with an IP address is followed when its address changes; device and entity IDs stay the same.
- Diagnostics download with credentials, MAC, and account redacted.

## Release 2.6.0 highlights
- YAML setup removed. An existing `trueguard:` block is imported into a config entry once, and a Repairs notice asks you to remove it. If the import fails (for example a wrong password), a Repairs notice says why.
- The YAML-only `report_server_codes` mapping and the old Egardia `[ACCOUNT STATUS]` report format are gone; use the SIA / Contact ID push receiver.
- All remaining Egardia code names removed (`TrueguardAlarm`, `TrueguardZoneSensor`, `TrueguardCoordinator`). Entity and device IDs are unchanged.
- `manifest.json` now lists this repository as the documentation and issue tracker, with @tristangrichard as code owner. Removed the outdated `requirements.txt`.

## Release 2.7.0 highlights
- Partial arm 2 and 3 (`Deltilkobling 2` / `3`, and firmware variants such as `Deltilkoblet` or `Fuldtilkoblet`) now show as armed home instead of an unknown state. The alarm entity has a `panel_mode` attribute with the panel's own mode text.
- A mode the integration does not recognise keeps the last known state and logs one warning with the mode text, so it can be added.
- Zones added, removed, or renamed on the panel reload the integration automatically (after the change is seen on two polls in a row).
- `hacs.json` declares Home Assistant 2024.12.0 as the minimum version.

## Release 2.7.1 highlights
- Reauthentication and Reconfigure no longer use a Home Assistant call that logs an error on this integration and is marked as breaking in 2026.12.
- The alarm shows **triggered**, **arming**, or **pending** from polling alone when the panel's mode text says so (for example `Alarm`, `Udløst`, `Tilkobler`, `Udgang`, `Indgang`). Before, these texts kept the last state.
- A command the panel refuses now shows an error in Home Assistant with the panel's own reason, for example arming with an open door names the zone.
- A reported alarm and its cause survive a restart or reload.
- A zone renamed on the panel updates its entities without reloading the integration. Added or removed zones still reload.
- The report receiver no longer accepts reports from any address when the panel's hostname cannot be resolved.
- Keypads and remotes no longer get an always-off sensor; their battery, tamper, and signal sensors stay. Existing always-off sensors are removed, so update any automation or dashboard that uses them.
- Zone entities' internal unique IDs now include the panel, so two panels cannot collide. Existing entries are migrated and entity IDs stay the same. Going back to an older version would create duplicate zone entities.
- The device keeps the name given to the integration instead of being overwritten with "Trueguard".
- Tests against a real Home Assistant in `tests_ha` (`python -m pytest tests_ha`, needs `pytest-homeassistant-custom-component`).
