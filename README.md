# HolaBrain for Home Assistant

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/)
[![GitHub release](https://img.shields.io/github/v/release/dzerik/holabrain-ha?display_name=tag&sort=semver&color=41BDF5)](https://github.com/dzerik/holabrain-ha/releases)
[![Downloads](https://img.shields.io/github/downloads/dzerik/holabrain-ha/total?color=41BDF5&label=downloads)](https://github.com/dzerik/holabrain-ha/releases)
[![CI](https://img.shields.io/github/actions/workflow/status/dzerik/holabrain-ha/ci.yml?branch=main&label=CI)](https://github.com/dzerik/holabrain-ha/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-yellow.svg)](LICENSE)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2025.3%2B-41BDF5)](https://www.home-assistant.io/)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

Home Assistant integration for **HolaBrain** connected household appliances. Your
dishwasher, washing machine, oven, air conditioner, water heater and lamps become ordinary
Home Assistant entities — with real-time status, automations and a dashboard — through the
HolaBrain cloud API.

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=dzerik&repository=holabrain-ha&category=integration)
[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=holabrain)

---

## ⚠️ Read this first

**This is an early release (0.x). Expect rough edges.**

- Only the **dishwasher** category has been verified against physical hardware. The other
  five categories are modelled from the cloud API and are structurally complete, but
  **nobody has confirmed them on a real appliance yet** — a control may be missing, a reading
  may be off by a factor, a command may be silently ignored. Per-model detail is in the
  [hardware compatibility list](docs/hcl.md).
- The cloud allows **one active session per account**. Some actions — scanning the account,
  or sending a command while the mobile app holds the session — will **sign you out of the
  HolaBrain mobile app**. Read [One account, one session](docs/accounts.md) before you
  install; it says exactly what does and does not do this.
- Entity names, entity ids and options may still change between 0.x releases.
- This is an unofficial community project. See [Legal notice](#legal-notice).

If that is acceptable, the integration is genuinely useful today — and hardware reports for
the unverified categories are the single most valuable thing you can contribute.

---

## Contents

- [Supported appliances](#supported-appliances)
- [What you can do with it](#what-you-can-do-with-it)
- [Installation](#installation)
- [Setup](#setup)
- [Entities](#entities)
- [Automations](#automations)
- [Services](#services)
- [Options](#options)
- [Panel and dashboard card](#panel-and-dashboard-card)
- [One account, one session](#one-account-one-session)
- [How status arrives](#how-status-arrives)
- [Troubleshooting](#troubleshooting)
- [Removing the integration](#removing-the-integration)
- [Architecture](#architecture)
- [Development](#development)
- [Legal notice](#legal-notice)

---

## Supported appliances

| Appliance | Home Assistant platform | Verification status |
|---|---|---|
| **Dishwasher** | composite (`sensor` / `binary_sensor` / `switch` / `select` / `number` / `button`) | ✅ **verified on hardware** |
| Washing machine | composite | 🧪 modelled from the cloud API, unverified |
| Oven | composite (programme composer) | 🧪 modelled from the cloud API, unverified |
| Air conditioner | `climate` | 🧪 modelled from the cloud API, unverified |
| Water heater | `water_heater` | 🧪 modelled from the cloud API, unverified |
| Lamp | `light` | 🧪 modelled from the cloud API, unverified |

🧪 means the appliance will appear and its entities will be created, but the behaviour has
never been observed on a physical unit. The per-model list, what "verified" covers and what
to check if you own a 🧪 appliance are in **[docs/hcl.md](docs/hcl.md)**.

An appliance category the integration does not know yet raises a **repair issue** naming its
type and model, instead of silently doing nothing.

Full entity reference: **[docs/entities.md](docs/entities.md)**.

## What you can do with it

### Monitor a running cycle

Every appliance exposes its stage, its remaining time and its faults as normal entities, so
they work in dashboards, history, logbook and voice assistants:

- `sensor.dishwasher_wash_stage` — `idle` → `pre_wash` → `main_wash` → `rinse` → `drying` →
  `finished`
- `sensor.dishwasher_time_remaining` — minutes, with `device_class: duration`
- `sensor.dishwasher_temperature` — current water temperature
- `binary_sensor.dishwasher_door` — `on` while the door is open
- `sensor.dishwasher_fault` — the code shown on the appliance's own panel

Status is pushed from the cloud, so a stage change shows up in Home Assistant within a second
or two — not on the next poll.

### Start a programme

Dishwashers and ovens do not accept "set the programme" and "start" as separate commands: the
appliance takes the whole thing as **one instruction**. So the integration gives you a
*composer* — the controls stage their values locally and a start button submits them together.

For a dishwasher:

1. `select.dishwasher_programme` — `eco`, `auto`, `intensive`, `rapid`, `hygiene`, …
2. `select.dishwasher_extra_option` — `none`, `extra_drying`, `half_load`, `power_wash`,
   `turbo_speed` (only the ones your model has)
3. `select.dishwasher_wash_zone` — `upper`, `lower`, `both` (only on models with zones)
4. `button.dishwasher_start_cycle` — submits the composed cycle

The start button refuses to run with the door open, and says so, instead of sending a command
the appliance would reject.

Ovens work the same way with `select.oven_cooking_mode`, `number.oven_target_temperature`,
`number.oven_cook_time`, `number.oven_food_probe_target`, `switch.oven_pre_heat` and
`button.oven_start`. Each cooking mode has its own temperature range and its own rules — the
number entities re-range themselves when you pick a mode, and `defrost` (time only) carries no
temperature at all.

### Watch consumables

Models that dose salt and rinse aid expose `binary_sensor.dishwasher_salt_low` and
`binary_sensor.dishwasher_rinse_aid_low` (both `device_class: problem`), plus the dosing
settings as `number.dishwasher_rinse_aid_level` and `number.dishwasher_water_softener`.
Washing machines with automatic dosing expose `binary_sensor.washing_machine_detergent_low`
and `binary_sensor.washing_machine_softener_low`.

**Water and electricity consumption** is exposed as `sensor.dishwasher_energy_month`,
`sensor.dishwasher_water_month` and their yearly counterparts, in kWh and litres with the
matching `device_class`, so they drop straight into Home Assistant's energy and water
dashboards. The figures come from the cloud's own aggregation, so they are already in real
units and survive re-pairing the appliance.

Refill counters and the appliance's raw lifetime totals exist as well, as **diagnostic
entities disabled by default**.

### Automate

Notifications when a cycle ends, cheap-tariff starts, consumable reminders, fault alerts — all
standard Home Assistant. Copy-pasteable YAML in **[docs/automations.md](docs/automations.md)**
and a short tour [below](#automations).

### Control the native categories

Air conditioners, water heaters and lamps map onto the platforms Home Assistant already has,
so the built-in thermostat / water-heater / light cards, voice control and existing blueprints
work with no adapters.

## Installation

### HACS (recommended)

The repository is not in the default HACS store yet, so add it as a **custom repository**:

1. Open **HACS** in Home Assistant.
2. Click the **⋮** menu (top right) → **Custom repositories**.
3. Paste `https://github.com/dzerik/holabrain-ha`, choose category **Integration**, click
   **Add**.
4. Search for **HolaBrain** in HACS, open it and click **Download**.
5. **Restart Home Assistant.**

Or use the one-click badge, which opens step 3 for you:

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=dzerik&repository=holabrain-ha&category=integration)

### Manual

1. Download `holabrain.zip` from the
   [latest release](https://github.com/dzerik/holabrain-ha/releases/latest).
2. Extract it into `config/custom_components/holabrain/` — `manifest.json` must end up at
   `config/custom_components/holabrain/manifest.json`.
3. **Restart Home Assistant.**

### Requirements

- Home Assistant **2025.3** or newer.
- An internet connection: appliances are reached through the vendor cloud; there is no local
  control path.
- A HolaBrain account with your appliances already added to it. Adding a brand-new appliance
  still needs the mobile app once — see [docs/accounts.md](docs/accounts.md).

## Setup

**Settings → Devices & services → Add integration → HolaBrain**, or:

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=holabrain)

| Field | What to enter |
|---|---|
| **Email** | The address you sign in to the HolaBrain app with. |
| **Password** | Your account password. It is stored in the config entry so the integration can sign in again when the cloud ends the session. |
| **Region** | The cloud region of your account — the same one the mobile app uses: `eu` or `us`. |
| **Country code** | Two-letter country code sent at sign-in, e.g. `RU`. |

Every appliance on the account is added at setup, under its cloud name. If you pair an
appliance later, run **Configure → Scan for appliances** (or the panel's *Scan* button):
scanning is manual on purpose, because it is the one routine action that signs the mobile app
out.

Credentials, region and country can be changed later without losing entity history:
**HolaBrain → ⋮ → Reconfigure**. If the cloud stops accepting the stored password, Home
Assistant starts its normal re-authentication flow.

Multiple accounts are supported — add a second config entry.

## Entities

Entities are **capability-gated**: the integration resolves what *your specific model*
supports and creates only those entities, so a control the appliance would refuse never
appears, and a reading it never updates never sits there looking real. Forced drying is hidden
on models without it; a washer with no dryer has no drying-level select.

Resolution merges several sources — the cloud capability dictionary for the model, the packed
capability descriptor on the device record, per-model tables, and the status keys the
appliance actually reports — and the result is cached and revalidated automatically. Details,
and the "a control is missing" recipe: **[docs/capabilities.md](docs/capabilities.md)**.

The complete per-category entity list is in **[docs/entities.md](docs/entities.md)**.

> Entity ids follow your appliance's name in the account. Examples in this README assume an
> appliance named *Dishwasher*; yours may be `sensor.kitchen_dishwasher_wash_stage`. Check
> **Developer tools → States** for the real ids.

## Automations

A few examples; more in **[docs/automations.md](docs/automations.md)**.

**Tell me when the wash is done.**

```yaml
automation:
  - alias: Dishwasher finished
    triggers:
      - trigger: state
        entity_id: sensor.dishwasher_wash_stage
        to: finished
    actions:
      - action: notify.mobile_app_my_phone
        data:
          message: The dishwasher is done.
```

**Run the dishwasher on the night tariff — but only if it is loaded and shut.**

```yaml
automation:
  - alias: Start the dishwasher at night
    triggers:
      - trigger: time
        at: "01:00:00"
    conditions:
      - condition: state
        entity_id: binary_sensor.dishwasher_door
        state: "off"
      - condition: state
        entity_id: sensor.dishwasher_wash_stage
        state: idle
    actions:
      - action: select.select_option
        target:
          entity_id: select.dishwasher_programme
        data:
          option: eco
      - action: button.press
        target:
          entity_id: button.dishwasher_start_cycle
```

**Warn about a fault the moment the appliance reports one.**

```yaml
automation:
  - alias: Dishwasher fault
    triggers:
      - trigger: state
        entity_id: sensor.dishwasher_fault
        not_to:
          - none
          - unavailable
          - unknown
    actions:
      - action: persistent_notification.create
        data:
          title: Dishwasher fault
          message: >-
            The dishwasher reports {{ states('sensor.dishwasher_fault') }}.
```

**Remind me to refill the salt.**

```yaml
automation:
  - alias: Dishwasher salt low
    triggers:
      - trigger: state
        entity_id: binary_sensor.dishwasher_salt_low
        to: "on"
        for: "00:05:00"
    actions:
      - action: notify.persistent_notification
        data:
          message: The dishwasher is out of salt.
```

## Services

All five actions are available in **Developer tools → Actions** and in automations.

### `holabrain.refresh_capabilities`

Re-reads what each appliance supports and rebuilds its entities if the answer changed. Use it
after enabling a feature on the appliance, or when an entity you expect is missing.

```yaml
action: holabrain.refresh_capabilities
data:
  device_id: 6f1a…            # optional — limits it to this appliance's account
```

Both fields (`config_entry_id`, `device_id`) are optional; with neither, every configured
account is refreshed. Capabilities are also revalidated on a slow timer and updated lazily
from every status frame, so this is an escape hatch rather than something to schedule.

### `holabrain.scan_devices`

Re-reads the account inventory so appliances paired after setup appear — and appliances
removed from the account disappear.

```yaml
action: holabrain.scan_devices
data:
  config_entry_id: 01J…       # optional — limits it to one account
```

> ⚠️ **This signs the HolaBrain mobile app out.** Listing the account needs the account
> session, and the cloud allows only one. Everyday monitoring never does this — only this
> action, the *Configure → Scan* step and the panel's *Scan* button.

### `holabrain.refresh_token`

Signs in again with the stored credentials and replaces the account token. The integration
replaces an expired token by itself, so reach for this only when a session is stuck — the
cloud holding a token it will not accept.

```yaml
action: holabrain.refresh_token
data:
  config_entry_id: 01J…       # optional — limits it to one account
```

> ⚠️ **This signs the HolaBrain mobile app out.** A login claims the account's single
> session. The same action is available as the **Refresh token** button on the account
> device, which is disabled by default so it cannot be pressed by accident.

### `holabrain.rename_device`

Renames the appliance **in the account itself**, so the mobile app shows the new name too.
(To rename it only inside Home Assistant, use the device page.)

```yaml
action: holabrain.rename_device
data:
  device_id: 6f1a…
  name: Kitchen dishwasher
```

### `holabrain.unbind_device`

Removes the appliance from the HolaBrain **account**, and with it from Home Assistant.

```yaml
action: holabrain.unbind_device
data:
  device_id: 6f1a…
  confirm: true
```

> ⚠️ **Not undoable from Home Assistant.** Putting the appliance back needs physical access to
> it, so the action refuses to run unless `confirm: true` is set.

## Options

**Settings → Devices & services → HolaBrain → Configure**

| Option | What it does |
|---|---|
| **Panel** | Show or hide the HolaBrain panel in the sidebar. Off by default. |
| **Scan for appliances** | Re-read the account so newly paired appliances appear. Shows the sign-out warning first, then reports what it found. |
| **Add an appliance** | Search the **local network** for an appliance that is not on the account yet, and claim it. The search itself needs no account and touches nothing. |

*Add an appliance* deserves a warning: claiming only works in a narrow window — right after
the mobile app has joined the appliance to Wi-Fi but failed to add it. Outside that window the
cloud will not hand the appliance over, and pressing the appliance's pairing button does
**not** help (it clears the Wi-Fi settings and takes the appliance off the network entirely).
Joining an appliance to Wi-Fi in the first place is not possible from Home Assistant at all:
those credentials travel over a short-range radio link from a phone. The honest summary is in
[docs/accounts.md](docs/accounts.md#adding-an-appliance-to-the-account).

## Panel and dashboard card

Both are optional, and both are ordinary Home Assistant frontend clients: they read entity
states and call services, never the cloud API. Enabling them adds no cloud traffic.

**Sidebar panel.** Configure → **Panel** → *Show the panel in the sidebar*. One category-aware
card per appliance, a *Scan* button behind a confirmation, and a diagnostics tab listing every
entity of the integration with its raw state.

**Dashboard card.** **Settings → Dashboards → ⋮ → Resources → Add resource**:

- URL: `/holabrain_panel/holabrain-card.js`
- Type: **JavaScript module**

Then add the card to a dashboard — it has a visual editor, or use YAML:

```yaml
type: custom:holabrain-card
device: 6f1a…       # optional device registry id; defaults to the first appliance
```

The assets are served by the integration itself: nothing to install separately, no build step.
The frontend ships as plain ES modules with a vendored Lit.

## One account, one session

The cloud keeps **one live session per account**. Signing in anywhere invalidates the session
that was active before it. Home Assistant and the mobile app therefore share one slot, and
whoever signed in last owns it.

What the integration does about it:

- **It does not need the account while things are quiet.** Status arrives over a
  certificate-authenticated push channel that is independent of the account session; while
  push is delivering, the periodic poll is skipped entirely. Monitoring never signs the app
  out.
- **It reuses its session**, so restarting Home Assistant does not log you out of the app.
- **It does not fight for the session.** If the app takes it over, the integration reclaims it
  once and then backs off — one minute, five, fifteen — instead of stealing it back on every
  cycle. Two clients logging each other out in a loop would leave both unusable.
- **It recovers on its own** once the app is idle.

What *will* sign the app out: scanning the account, claiming an appliance, and sending a
command while the app currently holds the session.

Full explanation, including the "use a separate account" workaround:
**[docs/accounts.md](docs/accounts.md)**.

## How status arrives

Each appliance is subscribed to over a cloud push channel (MQTT with its own client
certificate), which delivers a frame the moment something changes — a stage change shows up in
Home Assistant in about a second. A 60-second poll is the fallback, and it is skipped while
push is healthy, which is also what keeps the integration out of the session contention
described above.

When the cloud is unreachable, a single failed poll is absorbed (the cloud does fail an
occasional query), but three in a row mark the entities `unavailable` rather than serving
hours-old values as if they were current.

## Troubleshooting

The full guide is **[docs/troubleshooting.md](docs/troubleshooting.md)**. The short version:

| Symptom | What to do |
|---|---|
| An appliance is missing | **Configure → Scan for appliances** (or `holabrain.scan_devices`). Appliances paired after setup are not picked up automatically. |
| A control I have on the appliance is missing | `holabrain.refresh_capabilities`, then run that programme once on the appliance. See [docs/capabilities.md](docs/capabilities.md). |
| The mobile app keeps signing me out | Expected if you scan or send commands often; see [docs/accounts.md](docs/accounts.md). |
| Everything went `unavailable` | The cloud is unreachable or the session was lost; it recovers on its own. Check the log. |
| Home Assistant asks me to re-authenticate | The stored password no longer works — the account password was changed elsewhere. |
| A reading is wrong, a control does nothing | Likely a 🧪 category. Check [docs/hcl.md](docs/hcl.md) and report it — that is how these get fixed. |
| "Unsupported appliance type" repair issue | Your account has a category this version does not support. Please report it with diagnostics. |

Reporting anything: attach the integration's **diagnostics**
(**HolaBrain → ⋮ → Download diagnostics**). They carry the capability profile and every raw
status key, with credentials and identifiers removed —
[docs/diagnostics.md](docs/diagnostics.md) explains what is in them, how to turn on debug
logging, and what should never be attached to a public issue.
[Open an issue →](https://github.com/dzerik/holabrain-ha/issues/new/choose)

## Removing the integration

**Settings → Devices & services → HolaBrain → ⋮ → Delete.** That removes the config entry, its
devices and entities, the stored session, the push client's key and the cached capability
profiles. **Nothing changes in the HolaBrain account** — the appliances stay bound to it and
the mobile app keeps working.

To remove a single appliance from the *account*, use `holabrain.unbind_device` — that one
cannot be undone from Home Assistant.

If you installed through HACS, uninstall it there afterwards and restart Home Assistant.

## Architecture

The code is split into two layers so the client can be reused outside Home Assistant:

```text
custom_components/holabrain/
├── aiodollin/          ← standalone async client (ZERO Home Assistant imports)
│   ├── auth/           ← request signing, login, token storage
│   ├── transport/      ← HTTP + cloud push (MQTT) + TLS
│   ├── api/            ← devices, capabilities, certificates, binding
│   ├── dto/            ← plain dataclasses
│   └── client.py       ← DollinClient facade
├── coordinator.py      ← DataUpdateCoordinator (push + poll) + capability cache
├── registry.py         ← declarative category → entity map
├── dishwasher.py       ← dishwasher cycle composer
├── oven.py             ← oven programme composer
├── services.py         ← refresh_capabilities, scan_devices, rename/unbind_device
├── diagnostics.py      ← redacted entry / device dumps
├── www/                ← panel + Lovelace card (plain ES modules, vendored Lit)
└── <platforms>.py      ← climate, water_heater, light, sensor, switch, …
```

`aiodollin` is meant to be extracted into its own PyPI package later; a compliance test
enforces that it never imports Home Assistant.

## Development

```bash
pip install -r requirements_test.txt
pytest                                   # all tests, with coverage
pytest --no-cov tests/test_platforms.py  # one file, fast
ruff check custom_components tests scripts
ruff format custom_components tests
python scripts/check_translations.py     # every language must match strings.json
```

Tests cover the awkward cases — encoding boundaries, capability gating, partial and malformed
cloud responses, session takeover, push/poll interplay — rather than trivial happy paths. CI
runs lint, the translation check, the core-isolation check, the test suite, hassfest and HACS
validation.

Contributions are welcome — [CONTRIBUTING.md](CONTRIBUTING.md) has the full guide. The most
valuable ones right now:

- **hardware reports** for the 🧪 categories — see
  [what a category needs to be marked verified](docs/hcl.md#what-a-category-needs-to-be-marked-verified);
- new appliance categories — start from `registry.py`, one `CategorySpec` per category;
- translations (`strings.json` is the source; `en`, `ru`, `be`, `kk`, `uz` follow it).

## Legal notice

This project:

- is an **unofficial community integration**. It is **not affiliated with, endorsed by, or
  supported by** the manufacturer of the appliances, the operator of the HolaBrain cloud
  service, or any of their subsidiaries or partners;
- works **only with appliances owned by the user**, and **only under the user's own HolaBrain
  account** — every request is made with credentials the user enters themselves;
- talks to the same public cloud API endpoints the companion app uses, and behaves as one more
  client of that service. It complements the official app; it does not replace it, and it does
  not attempt to circumvent any access control;
- is provided **AS IS, without warranty of any kind**. The cloud operator can change or
  withdraw endpoints at any time, and the integration will stop working until it is updated;
- stores credentials only in your own Home Assistant instance, and sends them only to the
  cloud service they belong to. Nothing is transmitted to the authors.

**HolaBrain** and any other product or company names mentioned in this repository are
trademarks of their respective owners. Their use here is **nominative** — solely to state what
this software is compatible with — and implies no affiliation or endorsement.

If you believe this project infringes anyone's rights,
[open an issue](https://github.com/dzerik/holabrain-ha/issues) or contact the author; it will
be addressed promptly.

## Documentation

- [Entities by appliance category](docs/entities.md)
- [Hardware compatibility list](docs/hcl.md)
- [Capabilities: why your appliance shows only some controls](docs/capabilities.md)
- [One account, one session](docs/accounts.md)
- [Automation examples](docs/automations.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Collecting diagnostics](docs/diagnostics.md)
- [Changelog](CHANGELOG.md)
- [Contributing](CONTRIBUTING.md) · [Code of conduct](CODE_OF_CONDUCT.md)
- [Security policy](SECURITY.md)

## License

[MIT](LICENSE)
