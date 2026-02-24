# Datadis for Home Assistant

Custom Home Assistant integration to read electricity data from the Datadis private API for one or more CUPS points.

## Features

- Config flow from Home Assistant UI
- Per-entry CUPS configuration
- Auto polling with configurable interval
- Configurable query window (days)
- Manual `Refresh now` button entity
- Tolerant API request fallbacks for Datadis format differences

## Entities

Per configured CUPS, this integration creates:

- `sensor.monthly_consumption` (`kWh`)
- `sensor.yesterday_consumption` (`kWh`)
- `sensor.latest_hour_consumption` (`kWh`)
- `sensor.monthly_peak_power` (`kW`)
- `button.refresh_now`

## Installation (manual)

1. Copy `custom_components/datadis` into your Home Assistant config folder under `custom_components`.
2. Restart Home Assistant.
3. Go to `Settings -> Devices & Services -> Add Integration`.
4. Search `Datadis`.
5. Fill:
   - `username`
   - `password`
   - `cups`
   - `distributor_code` (optional)
   - `update_interval_minutes`
   - `query_days`

## Configuration options

After setup, open the integration `Configure` menu:

- `update_interval_minutes`: automatic polling frequency.
- `query_days`: number of past days requested from Datadis on each refresh.
- `distributor_code`: optional, can help in some distributor scenarios.

## Force refresh anytime

Use one of these methods:

- Open the Datadis device and press `Refresh now`.
- Call Home Assistant service `homeassistant.update_entity` on one Datadis sensor entity.

## Notes about Datadis API behavior

Datadis can return strict validation errors depending on distributor and request shape. This integration includes fallback parameter/date formats and keeps partial data working when one endpoint fails.

## Repository structure

- `custom_components/datadis/`: integration code
- `hacs.json`: HACS metadata
- `LICENSE`: project license

## Disclaimer

This project is an unofficial integration and is not affiliated with Datadis.
