# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Unofficial Home Assistant custom integration (`domain: holabrain`) for HolaBrain cloud
appliances: dishwasher, washer, oven, air conditioner, water heater, lamp. Cloud-only —
there is no local control path. Python 3.12+ (CI runs 3.13), Home Assistant 2025.3+.

## Commands

```bash
pip install -r requirements_test.txt          # installs HA itself via pytest-homeassistant-custom-component

pytest                                        # everything, with coverage
pytest tests/test_platforms.py -k remaining_time   # one test
pytest --no-cov tests/aiodollin                # core only, fast
pytest tests/aiodollin/test_no_ha_imports.py --no-cov   # the architectural invariant, fails in seconds

ruff check custom_components tests scripts     # what CI enforces
ruff format custom_components tests            # available but NOT enforced — format only the lines you touch
python scripts/check_translations.py           # strings.json vs. every translations/*.json
```

CI (`.github/workflows/ci.yml`): ruff → core-isolation test → full pytest, plus separate
`translations`, `hassfest` and `hacs` jobs. Third-party actions are pinned by commit SHA, not
by branch — Dependabot bumps the pins, so do not "simplify" one back to `@master`.
`requirements_test.txt` pins almost nothing on purpose — `pytest-homeassistant-custom-component`
pulls in Home Assistant and with it the exact `httpx` / `cryptography` / `paho-mqtt` / pytest
versions; adding version floors of our own makes the resolver fail.

## Architecture

### Two layers

`custom_components/holabrain/aiodollin/` is a standalone async cloud client that imports
**nothing** from Home Assistant — it is meant to be extracted as its own PyPI package.
`tests/aiodollin/test_no_ha_imports.py` fails the build if that is broken. Inside:
`auth/` (request signing, login, session takeover back-off, `TokenStore`), `transport/`
(`http.py`, `mqtt.py`, `ssl.py`), `api/` (`devices`, `capabilities`, `certificates`,
`binding`, `statistics`, `catalog`), `dto/`, and the `DollinClient` façade in `client.py`.

The Home Assistant side is deliberately thin. New functionality goes into `aiodollin`
first, with its own unit test, and only then gets wired into a platform.

### Coordinator (`coordinator.py`)

One `DataUpdateCoordinator` per config entry owning the client, the device inventory, the
capability cache and the MQTT push connection. Non-obvious behaviour that must be preserved:

- **Push-first.** Status arrives over an MQTT channel authenticated with its own client
  certificate (independent of the account session). While push is healthy the 60 s poll is
  skipped entirely — that is what keeps the integration out of session contention.
- **One session per account.** The cloud allows a single live session, shared with the
  vendor's mobile app. `CONF_MODE` chooses who wins: `MODE_COOPERATIVE` (default) never
  spends an account request on our own initiative; `MODE_EXCLUSIVE` polls and reclaims.
  `SessionTakeoverError` must never become a re-auth prompt — only `AuthError` may.
- **Failure grace.** `POLL_FAILURE_GRACE` absorbs isolated all-device poll failures; more
  than that raises `UpdateFailed` rather than serving hours-old values as current.
- **Snapshot triggers.** Push frames omit lifetime counters, so `SnapshotTrigger` fires one
  HTTP snapshot exactly when a cycle settles.
- **Best-effort background fetches.** The appliance-type catalogue (`DeviceCatalog`) and
  consumption statistics are enrichment, not state: their failures are caught broadly and
  logged at debug, and an empty catalogue degrades to the raw type code. Never let one of
  them fail setup or an update cycle.
- Certificate + capability cache live in `.storage` (never in the config entry, which holds
  credentials) and are deleted in `async_remove_entry`.

### Declarative registry (`registry.py`)

The single place that says which entities each appliance category exposes: one
`CategorySpec` per `device_type` — `0xE1` dishwasher, `0x13` lamp, `0xE2` water heater,
`0xAC` air conditioner, `0xB1` oven, `0xDB` washer — holding tuples of frozen descriptors
(`SensorSpec`, `BinarySensorSpec`, `SwitchSpec`, `SelectSpec`, `NumberSpec`, `ButtonSpec`)
plus an optional native-platform config (`LightConfig`, `ClimateConfig`,
`WaterHeaterConfig`, `OvenConfig`, `DishwasherConfig`). Platform modules are generic: they
look up the spec for a device's type and build entities from it. A composite category needs
no platform code at all; only `primary_platform` categories (`climate` / `water_heater` /
`light`) need a small branch in that platform module.

### Unmodelled categories (`generic.py` + repair issue)

An appliance whose `device_type` has no `CategorySpec` must never be silently invisible —
that reads as a broken setup rather than a missing feature. Two things happen instead:
`coordinator._async_report_unsupported()` raises one repair issue per unknown category
(naming the catalogue description and the models), and `generic.py` still creates a device
with one **diagnostic, disabled-by-default** sensor per reported status key. Nothing in that
fallback writes — a command built from a guessed key is not a feature. A user enabling one
of those sensors is exactly the evidence needed to model the category properly.

### Capability gating

Every descriptor may carry a `capability` gate, and the entity is not created unless the
model's resolved profile satisfies it. Profiles are merged from the cloud capability
dictionary, the packed descriptor on the device record, per-model tables and the status keys
the appliance actually reports (`aiodollin/api/capabilities.py`, `build_default_chains()`).
Field discovery is **monotonic** — reported keys are only ever added, so a truncated or
offline response can never make entities disappear. Never widen a mapping "just in case":
a control the appliance would refuse is worse than a missing one. See `docs/capabilities.md`.

### Conditions and gates (`conditions.py`)

An appliance reports every field all the time, including leftovers that mean nothing in the
current state (a switched-off dishwasher still reports last cycle's programme and remaining
time). `conditions.py` is the vocabulary for saying so declaratively: `Cond` predicates
(`Is`, `Between`, `Missing`, …), `StateRule` (the category's state machine, first match
wins), `Gates` (`meaningful_when` blanks a reading; `blocks` refuses a write with a
translated reason; `exempt` opts a control out of a category-wide `guard`).

Two rules that are easy to get wrong:

- **Three-valued, fail open.** `holds()` returns `True` / `False` / `None`, where `None`
  means the snapshot lacks the key. Connectives follow Kleene logic. Unknown never blanks a
  reading and never refuses a command.
- **Refuse, don't disable.** A write the appliance would reject raises
  `ServiceValidationError` rather than marking the entity unavailable — Home Assistant drops
  unavailable entities from a service call's targets, so an automation would be told it
  succeeded while nothing happened.
- A lifetime total (`state_class` TOTAL / TOTAL_INCREASING) must never carry
  `meaningful_when`; long-term statistics read a gap as a counter reset. `SensorSpec` and
  friends enforce this in `__post_init__`.

### Composers (`dishwasher.py`, `oven.py`)

Dishwashers and ovens accept a programme only as one whole instruction, so the selects /
numbers stage their values in `coordinator.drafts` (virtual `@staged:` keys) and a start
button submits them together. `entity.py` supplies the gate context that resolves `@state`
and `@staged:`.

### Frontend (`www/`)

Sidebar panel and Lovelace card, plain ES modules with a vendored Lit — **no build step**.
Served by `panel.py` at `/holabrain_panel`; the static path is mounted whenever the
integration loads (the card is a separate opt-in from the panel). Both are ordinary Home
Assistant clients: entity states and service calls only, never the cloud API.

## Conventions that CI or reviewers will catch

- **Version lives in two files that must never disagree**: `version` in `pyproject.toml` and
  in `custom_components/holabrain/manifest.json`. Bump both in the same commit (patch for
  fixes/docs/tests, minor for a new category / entity / action / option, major or a
  documented migration for renamed-or-removed entities and config-entry changes).
- **Translations**: `strings.json` is the source of truth; every `translation_key` must be
  mirrored into all five of `translations/{en,ru,be,kk,uz}.json`, enum sensors with each
  state under `state:`, plus an entry in `icons.json`. `scripts/check_translations.py` also
  checks placeholders and empty values.
- **Commits**: Conventional Commits (`feat:`, `fix:`, `refactor:`, `docs:`, `test:`,
  `chore:`, `perf:`, `ci:`), imperative subject describing the user-visible effect — the
  changelog is assembled from these. User-visible changes get a `CHANGELOG.md` line under
  `## [Unreleased]`.
- **Line length 100.** `ruff` lint selects `E, F, W, I, UP, B, SIM, RUF`.
- **Tests never touch the network** (`respx` for HTTP in `tests/aiodollin/`, which must not
  import Home Assistant fixtures; `tests/` uses `pytest-homeassistant-custom-component` with
  shared fixtures in `conftest.py`). Fixtures use synthetic identifiers only — no real
  serials, account addresses or MAC addresses, in tests or commit messages. Coverage is a
  diagnostic, not a target: prefer tests for encoding/unit boundaries, capability gating,
  malformed cloud responses, session takeover and push/poll interplay.

## Adding an appliance category

Follow `CONTRIBUTING.md` ("Adding an appliance category") — it is the authoritative
six-step checklist: resolver chain → `CategorySpec` → native platform handler (only for
`primary_platform`) → strings/icons → gating + behaviour tests → README, `docs/entities.md`,
`docs/hcl.md`, `CHANGELOG.md`. Work from a real diagnostics dump and state in the PR which
appliance and which dump a mapping came from.

Only the dishwasher category is verified against physical hardware; the other five are
modelled from the cloud API. Do not silently "fix" a mapping for an unverified category
without a dump backing it — mark honestly in `docs/hcl.md` instead.
