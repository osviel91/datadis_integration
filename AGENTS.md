## Scope
- This repo is a single Home Assistant custom integration rooted at `custom_components/datadis`; there is no monorepo/workspace structure.

## Verify
- The only committed verification command is `python3 -m compileall custom_components/datadis` from `README.md`.
- There is no committed test, lint, typecheck, pre-commit, or CI workflow config in this repo. Do not invent repo-local commands that are not here.

## Runtime Wiring
- `custom_components/datadis/__init__.py` is the setup entrypoint: it builds `DatadisApiClient`, creates `DatadisCoordinator`, stores it in `entry.runtime_data`, and forwards platforms `sensor`, `button`, `number`, `text`, and `binary_sensor`.
- `config_flow.py` is the onboarding/options entrypoint. Initial setup validates access against the Datadis supplies flow and uses normalized CUPS as the config entry unique ID.

## Easy-To-Miss Constraints
- Never persist credentials in config entry options. `__init__.py` explicitly strips `username` and `password` from `entry.options`; runtime-editable entities must keep credentials in `entry.data` only.
- Runtime-editable controls are implemented as entities: `number.py` writes `update_interval_minutes`, `query_days`, and `rate_limit_cooldown_hours` to config entry options; `text.py` writes `cups`, `distributor_code`, and `point_type`.
- Changes to runtime text settings are validated live through `DatadisApiClient.async_validate_access()`. Keep that validation path intact when changing option handling.
- `point_type` is constrained to strings `"1"` or `"5"`; CUPS format is strict `^ES[0-9A-Z]{20}$`.

## Data/Behavior Notes
- `coordinator.py` intentionally preserves last known rows in Home Assistant storage (`datadis_cache_<name>`) with a 48 hour TTL and uses progressive backoff on rate limits. Avoid changes that clear entities on transient API failures unless that behavior is explicitly intended.
- The API client is defensive on purpose: it retries Datadis endpoints with multiple param/date formats and GET/POST fallbacks, and supply resolution is best-effort rather than a hard prerequisite for polling.

## Release Metadata
- Version metadata is duplicated in `custom_components/datadis/manifest.json` and `hacs.json`; keep both in sync when making a release change.

## Source-Of-Truth Notes
- Trust code defaults in `custom_components/datadis/const.py` over prose in `README.md`. Current defaults in code are `60` minutes, `35` query days, `24` cooldown hours, and point type `"5"`.
