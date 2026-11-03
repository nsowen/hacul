# AGENTS.md — hacul

Instructions for AI coding agents and contributors working in this repository.

## Project overview

hacul is a Home Assistant custom integration (domain `cul`) for eQ-3 MAX!
heating hardware attached via a culfw-based 868 MHz stick (NanoCUL, CUL868,
CUNO/CUN). All radio/protocol work lives in the separate
[pycul](https://github.com/nsowen/pycul) library, which this integration pins
by commit in its manifest; this repository is the Home Assistant layer only.
Protocol behaviour belongs upstream in pycul — keep this codebase about HA
concepts (entities, config flow, repairs, translations).

## Code layout

```
custom_components/cul/
  __init__.py        entry setup/unload, hub lifecycle
  config_flow.py     CulConfigFlow (setup incl. USB discovery) and
                     CulOptionsFlow (sticks, heating zones, week profile from a
                     schedule helper, external room temperature / window sensors)
  hub.py             CulHub: owns the pycul MaxProtocol + sticks, adopts
                     devices, runs zone setup and schedule sync, fires
                     dispatcher signals
  entity.py          CulEntity / CulDeviceEntity / CulHubEntity base classes,
                     setup_device_entities() helper
  climate.py         per-thermostat climate + per-zone climate entities
  sensor.py          measured temperature, valve, mode, diagnostics, ...
  binary_sensor.py   window state, battery, radio link, status flags
  button.py          "Start pairing" on the CUL device
  event.py           eco switch key presses
  switch.py          device switches (wall thermostat display setting)
  number.py          write-only device configuration (temperature and valve
                     settings) as numbers
  select.py          write-only valve settings with fixed choices (boost
                     duration, decalcification day)
  external.py        virtual wall thermostat / shutter contact from HA sensors
  schedule_sync.py   HA schedule helper -> pycul week profile
  services.py, services.yaml   set_week_profile, get_week_profile,
                     set_temporary_temperature, set_group
  const.py           DOMAIN, dispatcher signal builders, shared constants
  diagnostics.py     downloadable diagnostics
  strings.json       all user-visible strings (the source of truth)
  translations/      generated/translated copies (en, de)
  manifest.json, icons.json
tests/               pytest suite (pytest-homeassistant-custom-component)
```

## Write-only device configuration

MAX! devices never report their configuration (temperature and valve config)
over the air. `hub.py` caches what was written per device (`configs`, part of
the store) and writes are **whole-block**: changing one setting re-sends the
full block with the others kept as last written — never write a partial
config, and never reset the cache to assumed values.

## Conventions

- Entity classes inherit **`CulDeviceEntity`** (a MAX! device; sets
  `unique_id`, `device_info`, and follows the device's listeners) or
  **`CulHubEntity`** (the stick/hub level). Both derive from `CulEntity`,
  which is push-based (`_attr_should_poll = False`), sets
  `_attr_has_entity_name = True`, and makes availability follow the sticks.
  Zone climate entities derive from `CulEntity` directly (no fixed device).
- **No hardcoded user-visible names.** Entities set
  `_attr_translation_key = "..."` and the name comes from `strings.json`
  (`entity:` section). Synthetic devices use `DeviceInfo(...)` with
  `translation_key` + `translation_placeholders` (see the zone device in
  `climate.py`) instead of a literal name. Errors and repairs raise with
  `translation_domain`/`translation_key` (+ placeholders), never English
  literals.
- **Cross-component updates go through dispatcher signals** built by the
  `signal_*()` helpers in `const.py` (`signal_device_added`,
  `signal_hub_updated`, `signal_connection`, `signal_zones_changed`) — the hub
  sends, entities connect in `async_added_to_hass()` and register with
  `async_on_remove()`.
- **Wrap radio commands** in `CulEntity._command()`: it turns pycul's
  `CulError` into `HomeAssistantError` with a translation key. Let pycul
  exceptions bubble into it; do not handle radio errors ad hoc.
- Everything is async (`async_setup_entry`, `async_add_entities` ...), fully
  type-hinted, `snake_case`; lint/format with **ruff**.
- `unique_id` shapes: `f"{device.address}_{key}"` for device entities,
  `f"{hub.entry.entry_id}_{key}"` for hub entities,
  `f"{hub.entry.entry_id}_zone_{group}"` for zones. Note: do not use the
  attribute name `group` on entities — HA reserves it.
- New user-facing text: add keys to `strings.json` (translations follow).

## Testing

- `python -m pytest` (see the README for the venv setup; pycul comes from a
  sibling checkout via `requirements_test.txt`).
- The suite needs a **POSIX environment** (Home Assistant imports `fcntl`):
  run it on Linux, macOS or in WSL on Windows.
- Tests live in `tests/` as `async def test_...` (plus pure unit tests), using
  **pytest-homeassistant-custom-component** (`MockConfigEntry`,
  `enable_custom_integrations`, `hass` fixture).
- **Mock at the serial layer, not at pycul:** `tests/conftest.py` patches
  `serial_asyncio_fast.open_serial_connection` with a `FakeStick` that speaks
  enough culfw for pycul to run against it for real. Feed it radio frames with
  the `frame()` helper. Prefer this so tests exercise the actual protocol
  path; only mock HA-side integrations where unavoidable.
- Config flow, options flow, entity behaviour, zone setup, schedule sync and
  unplug/replug each have dedicated test modules — extend those.
- `requirements_test.txt` is intentionally unpinned; a new Home Assistant
  release can break tests through new deprecation reporting (or new lazy
  dependencies of `homeassistant.components.usb` and friends) before any code
  here changed. When tests fail unexpectedly, compare against a pristine
  checkout first and treat HA drift as its own change.

## Home Assistant specifics

- HA **owns the serial device exclusively** while the integration runs: only
  one program can use a stick at a time. Docs and code should assume this
  (diagnostics tools that need the stick require HA to be stopped).
- Keep the boundary clean: if something is about frames, timing, pairing or
  device quirks, it belongs in pycul (and in its README/AGENTS.md), not here.
