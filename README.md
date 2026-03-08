# Datadis for Home Assistant

Unofficial Home Assistant custom integration for Datadis private API.

It lets you connect one or more CUPS and expose consumption/power sensors in Home Assistant, with editable controls directly in the device page.

## Features

- UI setup flow (no YAML)
- Multiple CUPS support (one config entry per CUPS)
- Datadis API fallback handling for common parameter/date variants
- `Refresh now` control button
- `Rate Limit Reached` binary sensor for dashboard visibility
- Editable controls in device `Controls` section:
  - `Update Interval` (minutes)
  - `Query Window` (days)
  - `Rate Limit Cooldown` (hours)
  - `CUPS`
  - `Distributor Code`
  - `Point Type` (`1` or `5`)
- Diagnostic sensors:
  - `Last Successful Update`
  - `Next Allowed Query`

## Entities

Per configured CUPS:

- Sensors
  - `monthly_consumption` (`kWh`)
  - `yesterday_consumption` (`kWh`)
  - `latest_hour_consumption` (`kWh`)
  - `monthly_peak_power` (`kW`)
  - `last_successful_update` (timestamp)
  - `next_allowed_query_at` (timestamp)
  - `rate_limit_reached` (binary)
- Controls
  - `button.refresh_now`
  - `number.update_interval_minutes`
  - `number.query_days`
  - `number.rate_limit_cooldown_hours`
  - `text.cups`
  - `text.distributor_code`
  - `text.point_type`

Credentials are intentionally not exposed as entities for security reasons. Update them from integration reconfiguration instead of device controls.

## Install with HACS

1. Open HACS in Home Assistant.
2. Go to `Integrations`.
3. Click menu (top-right) -> `Custom repositories`.
4. Add this repository URL and set category to `Integration`.
5. Search for `Datadis` in HACS and install it.
6. Restart Home Assistant.
7. Go to `Settings -> Devices & Services -> Add Integration`.
8. Add `Datadis` and complete setup.

## Manual installation

1. Copy `custom_components/datadis` into your HA config folder under `custom_components`.
2. Restart Home Assistant.
3. Add integration from `Settings -> Devices & Services`.

## Initial configuration fields

- `username`
- `password`
- `cups`
- `distributor_code` (optional)
- `point_type` (`1` or `5`)
- `update_interval_minutes`
- `query_days`
- `rate_limit_cooldown_hours`

## Datadis limits and behavior

Datadis may return `429 Consulta ya realizada en las últimas 24 horas`.

This integration handles it by:

- Keeping previous valid values instead of clearing sensors
- Exposing `next_allowed_query_at` for visibility
- Respecting configurable cooldown before retry

Recommended defaults:

- `Update Interval`: `1440` minutes
- `Rate Limit Cooldown`: `24` hours

## Development

```bash
python3 -m compileall custom_components/datadis
```

## Disclaimer

This project is not affiliated with Datadis.
