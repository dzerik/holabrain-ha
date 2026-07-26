# Contributing

Thanks for wanting to help. The most valuable contribution to this project is not code: it
is a **diagnostics dump from an appliance nobody here owns**, because five of the six
categories are modelled from the cloud protocol and have never met a physical device. If
that is what you have, open an
[appliance report](https://github.com/dzerik/holabrain-ha/issues/new?template=device_support.yml)
— [docs/hcl.md](docs/hcl.md) says what is confirmed so far and
[docs/diagnostics.md](docs/diagnostics.md) how to collect the dump — and stop reading here.

By participating you agree to the [Code of Conduct](CODE_OF_CONDUCT.md). Security problems
go through [SECURITY.md](SECURITY.md), never through a public issue.

## Development environment

Python 3.13 is what CI runs; 3.12 is the floor.

```bash
git clone https://github.com/dzerik/holabrain-ha.git
cd holabrain-ha
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements_test.txt
```

`requirements_test.txt` deliberately pins almost nothing: `pytest-homeassistant-custom-component`
installs Home Assistant itself and with it the exact pytest, `httpx`, `cryptography` and
`paho-mqtt` versions Home Assistant is tested against. Adding version floors of our own
makes the resolver fail.

```bash
pytest                                              # everything, with coverage
pytest tests/test_platforms.py -k remaining_time    # one test
pytest --no-cov tests/aiodollin                     # core only, fast
ruff check custom_components tests scripts          # what CI enforces
python scripts/check_translations.py                # strings.json vs. every language
```

`ruff format` is available but **not** enforced: the tree predates it and running it
repository-wide would bury a change in reformatting. Format the lines you touch, nothing
else. The line limit is 100 characters (`pyproject.toml`).

To try a change in a real Home Assistant instance, symlink or copy
`custom_components/holabrain` into that instance's `config/custom_components/` and restart.
Debug logging:

```yaml
logger:
  default: warning
  logs:
    custom_components.holabrain: debug
```

## Architecture in two paragraphs

**Core and adapter.** Everything that talks to the cloud lives in
`custom_components/holabrain/aiodollin/` — request signing and login (`auth/`), HTTP, TLS
and the certificate-authenticated push channel (`transport/`), the endpoint wrappers
(`api/`), plain dataclasses (`dto/`) and the `DollinClient` facade. That package imports
**nothing** from Home Assistant, so it can be split out into its own PyPI distribution
without touching a line; `tests/aiodollin/test_no_ha_imports.py` fails the build if anyone
breaks that. The Home Assistant side is deliberately thin: `coordinator.py` (a
`DataUpdateCoordinator` fed by push, with polling as a fallback, plus the capability cache),
`config_flow.py`, `services.py`, `diagnostics.py` and the platform modules. New
functionality goes into `aiodollin` first, with its own unit test, and is only then wired
into a platform.

**Declarative registry.** `registry.py` is the single place that says which entities each
appliance category exposes: one `CategorySpec` per `device_type` (`0xE1` dishwasher, `0x13`
lamp, `0xE2` water heater, `0xAC` air conditioner, `0xB1` oven, `0xDB` washer), holding
tuples of frozen descriptors — `SensorSpec`, `BinarySensorSpec`, `SwitchSpec`, `SelectSpec`,
`NumberSpec`, `ButtonSpec` — plus an optional native-platform config (`LightConfig`,
`ClimateConfig`, `WaterHeaterConfig`, `OvenConfig`, `DishwasherConfig`). The platform
modules are generic: they look up the spec for a device's type and build entities from it.
Every descriptor may carry a `capability` gate, and an entity is not created unless the
model's resolved capability profile satisfies it — see [docs/capabilities.md](docs/capabilities.md).
That is why a control missing on one dishwasher and present on another is normal, and why
mappings must never be widened "just in case".

## Adding an appliance category

Work from a diagnostics dump of a real appliance — ideally two, one idle and one mid-cycle.
The `raw` block is the account record verbatim, `status` is every key the appliance reports,
and those keys are the contract you are mapping.

1. **Capability chain** — `aiodollin/api/capabilities.py`: add a `ResolverChain` for the
   device type in `build_default_chains()`. Most families need a static feature table plus
   `StatusPresenceResolver` rules ("reports `probeTemp` ⇒ has a food probe"). Only ask the
   cloud capability dictionary for a family that is known to answer it; a wasted request per
   model on every revalidation is worse than no extra information. Cover the chain in
   `tests/aiodollin/test_resolvers.py`.
2. **Category spec** — `registry.py`: add a `CategorySpec` and register it in `CATEGORIES`.
   Set `primary_platform` for a category with a native Home Assistant platform
   (`climate` / `water_heater` / `light`), leave it `None` for a composite one. Gate every
   optional descriptor with `capability=`. Keep the constants (`_PROGRAM`, `_FAULT_CODE`, …)
   next to the spec they belong to.
3. **Native platform handler**, only for `primary_platform` categories: a small branch in
   `climate.py` / `water_heater.py` / `light.py` that translates the config into that
   platform's attributes. Composite categories need no platform code at all — that is the
   point of the registry.
4. **Strings** — add every `translation_key` you used to `strings.json` under
   `entity.<platform>.<key>`, then mirror it into all five translations
   (`translations/{en,ru,be,kk,uz}.json`). Enum sensors need each state listed under
   `state:`. Run `python scripts/check_translations.py` — CI runs it as its own job.
   Add icons for the new keys in `icons.json`.
5. **Tests** — a gating test (the entity appears only when the capability is advertised) in
   `tests/test_registry_gating.py`, and a behaviour test for anything non-obvious: a value
   transform, a command that has to fold power into it, a control the appliance refuses in
   some state. Build fixtures from the real status keys in the dump.
6. **Documentation** — a row in the category table in `README.md`, a section in
   `docs/entities.md`, a row in the compatibility list `docs/hcl.md` with the honest status
   (🧪 *modelled* until someone has run it on hardware), and a `CHANGELOG.md` entry.

Widening an existing category is steps 4 to 6 plus one descriptor: a new key from a dump
becomes a descriptor with a capability gate, its strings, and a test that fails without it.

Everything the integration knows comes from the cloud API and from observed cloud and
appliance responses. If you are adding a mapping, say in the pull request which appliance
and which dump it came from.

## Tests

Tests exist to catch regressions that would actually reach a user. A test that asserts a
constructor stored its arguments does not; it costs a maintenance slot and buys a coverage
percentage. Coverage is a diagnostic here, not a target.

Write tests for the things that have historically broken: encoding and unit boundaries,
capability gating, partial or malformed cloud responses, session takeover by the vendor app,
push going silent, a command issued while the appliance refuses writes. Prefer one test that
fails for exactly one reason over a scenario that exercises everything at once.

- `tests/aiodollin/` — pure unit tests of the core, with `respx` for HTTP. They must not
  import Home Assistant fixtures.
- `tests/` — Home Assistant tests on `pytest-homeassistant-custom-component`, with shared
  fixtures in `conftest.py`.
- No test may touch the real network. Ever.
- Fixtures use synthetic identifiers — no real serials, account addresses or MAC addresses,
  in tests or in commit messages.

## Commits and versioning

Commit messages follow Conventional Commits: `feat:`, `fix:`, `refactor:`, `docs:`,
`test:`, `chore:`, `perf:`, `ci:`, with an optional scope (`fix(coordinator): …`). Write the
subject in the imperative and describe the effect on the user rather than the diff — the
changelog is assembled from these.

The project is in `0.x` and versioned with SemVer. The version lives in **two** places that
must never disagree: `version` in `pyproject.toml` and `version` in
`custom_components/holabrain/manifest.json`. Bump both in the same commit as the change:

| Change | Bump |
| --- | --- |
| Bug fix, refactor, docs, tests, dependencies | patch (`0.10.1` → `0.10.2`) |
| New category, new entity, new action, new option | minor (`0.10.1` → `0.11.0`) |
| Renamed or removed entities, changed config-entry data, anything that breaks an existing automation | major, or a documented migration while still in `0.x` |

Every user-visible change gets a line under `## [Unreleased]` in `CHANGELOG.md`, written for
someone who runs the integration and has not read the code.

## Pull requests

Open one against `main`, fill in the template, and let CI finish: lint, the core-isolation
test, the full test suite, the translation check and hassfest all have to be green. Small
and focused merges quickly; a branch that changes a mapping, refactors the coordinator and
reformats a platform module does not.
