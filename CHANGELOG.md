# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.17.1] - 2026-08-15

### Fixed

- **An expired token no longer wedges the integration.** The cloud reports an expired token
  with business code `12001`, which the integration did not recognise — so it never became
  an authentication failure, no re-login was attempted, and setup retried every 30 seconds
  forever with a token nothing would refresh. Symptom: repeating
  *"Unexpected error fetching holabrain data … Token has expired"* and an integration stuck
  on *Retrying setup*. The stored account and password were always there; nothing was ever
  asking them to be used.

  If you hit this, no action is needed beyond updating — the next request signs in again by
  itself. The workaround before this release was **HolaBrain → ⋮ → Reconfigure**, which
  drops the stored session.

- Code `14005` is no longer treated as an expired token. It was a guess standing in for the
  code above, and the only message ever seen with it reads "unusual activity" — the wording
  of a session taken over by another client. It now takes the conservative path, which still
  recovers but serves the reclaim cool-down first.

## [0.17.0] - 2026-08-14

### Added

- Debug logging that makes a cloud problem reportable in one pass. With
  `custom_components.holabrain: debug` the log now records every request as
  `path -> HTTP status in N ms`, the business code and message behind any non-success
  answer, which command dialect and signature each appliance was given, which
  authentication branch ran and how much of the re-login budget is left, and the push
  channel connecting, dropping and re-subscribing. See
  [docs/diagnostics.md](docs/diagnostics.md#what-debug-actually-records).

  Appliance ids are replaced by the same pseudonyms the diagnostics dump uses, so a log and
  a dump can be read together and neither identifies your appliances. Request and response
  bodies are not logged at any level. One caveat worth knowing before you attach a log:
  `httpx` is not ours and prints full URLs at `info`, so the documented snippet quiets it.

## [0.16.3] - 2026-08-12

### Fixed

- Cloud errors now name the code the cloud returned. A business code the integration does
  not classify used to surface as the cloud's own text alone — "Token has expired" with no
  number — which describes the symptom but not the mapping entry that is missing, and
  nothing else in the request path records it. If you are seeing an error like that, the
  number in the new message is exactly what a bug report needs.

### Changed

- Documentation for the `0xE2` water heater now records what the appliance actually does
  with a setpoint sent while Smart is active: it neither rejects nor applies it, but leaves
  Smart and restores the previous setpoint. The refusal Home Assistant raises is therefore
  preventing a silent mode change, not mirroring a refusal by the appliance.

## [0.16.0] - 2026-08-12

### Added

- Water heater: a `remaining_time` sensor (minutes to setpoint, while heating) and a `fault`
  problem sensor.

### Changed

- **Breaking — the water heater's operation modes are different values.** `operation_list`
  offered `normal`, `eco`, `smart` and `high_temp`, read as independent flags. It now offers
  the three mutually exclusive modes the vendor app's own "Model" picker shows —
  `single`, `double`, `smart` — matching raw payloads captured from one unit
  (Terma AquaPro WiFi, `51020ED8`; see [docs/hcl.md](docs/hcl.md)).

  | Old mode | Use instead |
  |---|---|
  | `normal` | `double` (both tanks) is the closest equivalent; `single` if your unit heats one |
  | `eco` | nothing — it was sent to the appliance and never came back in a status response, and the app has no Eco control |
  | `high_temp` | nothing — it belongs to the app's separate scheduled disinfect cycle, not to this picker, and is left for its own release |

  **Two things break, and the second one needs no service call at all.** Any automation,
  script or scene that still sets `normal`, `eco` or `high_temp` now fails outright with
  *Operation mode … is not valid*, so those calls must be edited by hand. And the entity's
  own **state string changes on every `0xE2` water heater whether or not you ever touch the
  service**: what read `normal` now reads `single`, `double` or `smart` — or `unknown` on a
  model that reports neither `bodyNum` nor `cloudSmart`. Recorder history keeps the old
  strings for past periods, so a history graph shows a discontinuity, and templates or
  conditions comparing against `'normal'`, `'eco'` or `'high_temp'` stop matching.
- The water heater refuses `water_heater.set_temperature` while Smart is active instead of
  forwarding a setpoint the appliance turns into a mode change. Smart picks its own (it went
  straight to the maximum on the unit above), and the vendor app disables manual entry there
  too; forwarded anyway, the appliance dropped out of Smart and restored the old setpoint.
  The call raises rather than the control disappearing, so an automation is told what
  happened instead of silently doing nothing.

### Fixed

- Appliances on a non-direct `thingProtocol` (for example some `0xE2` water heaters) could
  never load: the status query and command endpoints for that dialect were signed with the
  wrong scheme and pointed at a path the cloud answers with 404. Setup now reaches those
  appliances instead of retrying forever with "the cloud could not be reached for any
  appliance".
- Water heater `current_temperature` could read as the setpoint instead of the tank's
  actual temperature on models that report `targetTemp` but not `cur_temperature` — the
  cloud's field names are swapped from what they suggest (`temp` is the setpoint,
  `targetTemp` is the measured temperature).
- Water heater models that report neither `bodyNum` nor `cloudSmart` now show their
  operation mode as unknown instead of claiming `double` — the integration has no evidence
  of those units' tank count.
- A cloud HTTP error whose body carried no business code (a 404 on an unknown path, a 5xx
  from the gateway) was read as a successful, empty response. That could empty the device
  inventory — and with it remove every device — turn an outage into a "re-enter your
  password" prompt, and report writes that never reached the appliance as done. Such
  responses now fail with the path and the status code.

## [0.15.0] - 2026-08-10

### Added

- Action `holabrain.refresh_token` and a disabled-by-default **Refresh token** button on the
  account device: sign in again with the stored credentials when a session is stuck. Both
  claim the account's only session and sign the mobile app out.

### Fixed

- An expired access token is now replaced immediately instead of being counted as a session
  taken over by another client. Ordinary token expiry no longer escalates the reclaim
  cool-down, which could leave a later poll refused outright.
- Credentials the cloud rejects no longer trigger another login attempt with the same
  password.

## [0.14.0] - 2026-07-27

### Added

- **An appliance category the integration does not model yet is no longer invisible.** It
  produced no device and no entity, only a repair issue, so "my fridge is on the account and
  Home Assistant ignores it" read as a broken setup rather than a missing feature. Such an
  appliance now gets a device and one sensor per status field it reports.

  The fallback is deliberately timid: raw cloud key names, no units, no device classes,
  everything diagnostic and disabled by default, and nothing writable. Nothing states what
  those keys mean or what scale they use — a guessed unit is what made a dishwasher report
  tens of megawatt-hours, and a guessed command would be worse than a wrong reading.
  Enabling one is the user saying "I recognise this number", which is also the evidence
  needed to model the category properly.

## [0.13.0] - 2026-07-27

### Removed

- **The appliance's raw lifetime counters** (`total_cycles`, `total_water`, `total_energy`).
  Nothing states their scale — the vendor's own app never reads those fields — so the `Wh`
  and `L` labels were a guess, and against the cloud's own figures the energy one was out by
  a factor of ten, which is how a dishwasher came to report tens of megawatt-hours. They
  also disappear from the status while the appliance is off, and a `total_increasing` sensor
  reads a gap followed by a return as a meter replacement, inflating long-term statistics
  without bound. The consumption sensors added in 0.12.0 carry the same information in units
  the cloud states itself.

### Added

- **The ecosystem's appliance-type catalogue.** An appliance the integration does not model
  yet was reported as a bare token like `0xCA`, which reads as a malfunction rather than a
  missing feature. The repair issue now says "Refrigerator (0xCA)", and the catalogue — 14
  types and the models sold under each — is included in diagnostics, which is what makes a
  report about an unsupported appliance actionable. An unreachable catalogue degrades to the
  raw code rather than to silence.
- The operating mode is recorded in diagnostics, so a report about stale readings can be
  told apart from one about a dead push channel.

## [0.12.1] - 2026-07-27

### Fixed

- The consumption figures could stay blank for ever. They are fetched once when the account
  first answers; if that attempt lost the race for the session, cooperative mode — which
  schedules no further polls once it has data — never tried again, and the only way out was
  a button the user had no reason to suspect they needed. Any later proof the account is
  reachable, a push frame included, now triggers the retry.
- Consumption sensors had no names of their own and fell back to "Electricity", "Electricity
  2" and so on. Named in all five languages.
- The account device was named after the account's e-mail address, and Home Assistant builds
  entity ids from device names — so the address ended up in every automation referencing the
  mode switch, every log line and every screenshot attached to a bug report.

## [0.12.0] - 2026-07-27

### Added

- **Cooperative and exclusive modes.** The cloud allows one session per account, so every
  request Home Assistant makes can sign the mobile app out. There is no way to share it,
  only a choice about who wins — so the integration now asks. Cooperative (the default)
  never spends an account request on its own initiative; exclusive treats Home Assistant as
  the primary client. An **Exclusive mode** switch and a **Refresh now** button on the new
  account device; switching takes effect without a reload.
- **Water and electricity consumption** as four sensors per metering appliance — monthly and
  yearly, in kWh and litres, with `device_class: energy` and `water`, so they drop straight
  into Home Assistant's energy and water dashboards. The figures come from the cloud's own
  aggregation rather than from the appliance's status, which is why they are already in real
  units and why they survive re-pairing.
- **A declared state machine per appliance category**, in the registry, as the single place
  each family's states are written down.

### Fixed

- **Readings that stopped meaning anything no longer look live.** An appliance reports every
  field it owns all the time: a switched-off dishwasher kept reporting the last wash's
  programme, stage and remaining time, so Home Assistant showed hours "remaining" on an idle
  machine and automations fired on a cycle that had ended days before. Each reading now
  declares the states in which it is meaningful and reads `unknown` outside them.
- **Refusals now say why.** A command the appliance would reject raises a translated error
  naming the reason (switched off, faulted, door open, child lock) instead of failing
  vaguely. Deliberately not marked unavailable: Home Assistant drops unavailable entities
  from a service call's targets, so an automation would be told it succeeded while nothing
  happened. The power control is exempt from all four checks, as it must be.
- **A delayed start is no longer reported as "off".** `power` on a dishwasher is the state
  itself rather than a boolean — `0` off, `1`/`5` standby, `2` reserved, `3` running — and
  `2` was being read as off, which fired "switch it off when it finishes" in the middle of a
  reservation.

## [0.11.1] - 2026-07-26

### Fixed

- Lifetime counters (total water, total energy, cycles) no longer freeze. They appear only
  in the cloud's full status snapshot — push frames carry a shorter subset that leaves them
  out — so with the push channel healthy the integration had stopped asking for the one
  thing push cannot deliver. A snapshot is now fetched when a cycle settles, which is the
  only moment those counters move: one request per wash rather than steady polling.

## [0.11.0] - 2026-07-26

First public release.

### Added
- **Diagnostics** — download a report from the integration's menu and attach it to an issue.
  Account, credentials, tokens and serials are redacted; appliance ids become a stable
  pseudonym so a report stays internally consistent without identifying the hardware.
- **Repair issues** for appliance categories the integration does not support yet, naming the
  type and model so they can be added instead of being silently ignored.
- **Reconfiguration flow** — a rotated password or a moved region is fixed in place, without
  deleting the integration and losing entity history.
- Entity categories, icon translations and translated action errors; every action is
  documented in the UI in all five languages.
- Community and release infrastructure: issue forms (including one that feeds the hardware
  compatibility list), contributing guide, code of conduct, security policy, Dependabot,
  CodeQL, and a release workflow that publishes a named archive with a version check.
- Documentation: hardware compatibility list, automation examples, troubleshooting, and a
  guide to collecting diagnostics.

### Changed
- Actions are registered once at startup rather than per config entry, so an automation
  referencing them keeps validating while the account is unloaded.
- `PARALLEL_UPDATES` is declared explicitly on every platform.


### Fixed
- **A password changed elsewhere is now noticed.** An account whose credentials the cloud
  refuses starts Home Assistant's re-authentication flow instead of failing every poll with
  a debug message while the entities quietly freeze on their last value.
- **A session held by the vendor app is no longer mistaken for bad credentials.** It is
  reported as a distinct condition, so Home Assistant waits for the session to come back
  instead of asking for a password that is perfectly valid.
- **An outage stops looking like fresh data.** A single failed poll is still absorbed (the
  cloud fails one query several times a day), but three in a row now mark the entities
  unavailable rather than serving hours-old values as current.
- **A throttled account is no longer hammered.** A rate-limit answer ends the poll cycle
  instead of asking for the remaining appliances anyway.
- **The TLS trust store is no longer read on the event loop.** The HTTP client is built from
  Home Assistant's pre-warmed context, so setting up, reloading and every config-flow step
  stop performing blocking I/O in the loop.
- **A setup that fails after the push channel was opened no longer leaks it.** Home
  Assistant retries every 30 seconds; each attempt used to leave a TLS connection, a network
  thread and a timer behind.
- **The push connection says goodbye properly**, so the broker releases the session
  immediately instead of holding it until it times out and refusing the reconnect after a
  reload.
- **The push client's private key is written owner-readable only**, and both it and the
  capability cache are deleted when the account is removed instead of staying in
  `.storage` forever.
- **An appliance the account never named gets a readable device name** instead of turning
  every one of its entities into " door", " temperature" and so on.
- One unparsable numeric field in the account's device list no longer fails the whole
  inventory read — and with it the setup of every appliance on the account.
- Account e-mail addresses are no longer written to the log; entries are referenced by id.

### Added
- A config-entry migration hook that refuses an entry written by a newer version of the
  integration, instead of loading data it cannot interpret after a downgrade.
- **Diagnostics**, for the whole account and for a single appliance. The dump carries the
  raw account record, the resolved capability profile and every status key the appliance
  reports — which is what makes a report about an unsupported or misbehaving appliance
  actionable — while the account, the password, the session and the appliance identifiers
  are redacted. Appliance ids are replaced by a stable pseudonym, so a device report can
  still be matched to the account report and to a later report from the same user.
- **An appliance the integration cannot model yet raises a repair issue** naming its
  category and model, instead of being silently skipped. Such an appliance produces no
  device and no entity, so nothing else would ever mention it.
- **Reconfigure.** A rotated password, a moved region or a corrected country can now be
  fixed in place, without deleting the account and with it every entity's history. The
  entry refuses to be repointed at a different account.
- Home Assistant actions are registered once, at startup: an automation referencing one now
  validates even while no account is loaded, and the call explains itself instead of failing
  with "action not found".
- `scan_devices`, `rename_device` and `unbind_device` are described in the UI (they showed
  up as bare slugs), in all five languages, and every sign-in field now carries a
  description.
- Destructive actions are covered by tests: an unbind the account did not actually perform
  is now reported as a failure instead of removing the device from Home Assistant anyway.
- **A compatibility list** (`docs/hcl.md`): which appliance, by category and model code, has
  been verified on real hardware, which is only modelled from the protocol and what can go
  wrong with the latter, plus what a tester should check to move a category up.
- **A diagnostics guide** (`docs/diagnostics.md`): how to download the dump and what it
  contains, what is already redacted from it, how to enable debug logging, where to find the
  model and category of an appliance, what a compatibility report needs — and what must never
  be attached to a public issue.

### Changed
- **Entities are categorised.** Lifetime counters and fault codes are diagnostic, appliance
  settings (rinse-aid dose, water softener, child lock, auto dosing, auto door open) are
  configuration, so the device page shows the controls that are actually used day to day.
- **Icons moved to icon translations** (`icons.json`), which is what lets a user override
  them per entity and what the panel now resolves through `ha-state-icon`.
- Every platform declares `PARALLEL_UPDATES = 0`: reads come from the coordinator and a
  write is a single instruction the cloud serialises anyway.
- Action failures are translatable rather than hard-coded English.
- `quality_scale.yaml` records the integration's self-assessment against Home Assistant's
  integration quality scale, rule by rule.

## [0.10.1] - 2026-07-26

### Changed
- The *Add an appliance* step now states plainly when it can work. Hardware testing settled
  the question: an appliance that is on the network but not in its setup mode is known to the
  cloud yet will not be handed over, and pressing its pairing button clears the Wi-Fi
  settings and takes it off the network entirely — so the two states never overlap. The one
  realistic case is an appliance the mobile app has just joined to Wi-Fi but failed to add.
  The step says so, and its error tells the user to use the app rather than to keep pressing
  the pairing button.
- The Wi-Fi details are optional: the cloud returns the appliance's own verification code, so
  they are only needed if a claim is refused. The field is labelled as the router's MAC
  address rather than the bare term "BSSID", which reads like a network name.


## [0.10.0] - 2026-07-26

### Added
- **Appliances are found on the local network.** They answer a broadcast with their own
  serial, model and category without any authentication, so adding one no longer means
  reading a 32-character code off a label and retyping it — the single most error-prone step
  of the flow. The search costs no account request at all and therefore cannot sign the
  vendor's mobile app out.
- **Appliances already on the account are filtered out**: the id an appliance announces is
  the same one the account uses, so only genuinely new ones are offered.
- **The Wi-Fi network is remembered** — BSSID and password are pre-filled for the next
  appliance instead of being typed again.
- "Nothing answered on the network", "not offering itself" and "serial unknown" are now
  three distinct outcomes, because they need three different actions from the user.


## [0.9.0] - 2026-07-26

### Added
- **Adding an appliance to the account from Home Assistant** — *Configure → Add an
  appliance*. Claims an appliance that is already on Wi-Fi and in its setup mode, given its
  serial number and the Wi-Fi network's BSSID and password. Joining an appliance to Wi-Fi in
  the first place still needs the mobile app: those credentials travel over a short-range
  radio link and have no cloud path at all.
  - "Serial unknown" and "appliance is not offering itself" are reported as different
    errors, because they need different actions from the user.
  - The serial is encrypted with the caller's own session before it leaves the process, so a
    captured request cannot be replayed by anyone else.
- `holabrain.rename_device` — renames the appliance in the account, so the vendor app shows
  the new name too.
- `holabrain.unbind_device` — removes an appliance from the account. Refuses unless called
  with `confirm: true`: putting an appliance back needs its own pairing button.
- Standalone binding API and pairing primitives in the core (`BindingApi`,
  `derive_verification_code`, `encrypt_serial`), fully covered by tests.


## [0.8.0] - 2026-07-26

### Added
- **Starting a dishwasher cycle from Home Assistant.** The appliance accepts a wash only as
  one whole instruction, so a programme select, an extra-option select, a wash-zone select
  and a *Start cycle* button compose it: the selects stage their values locally and the
  button submits them together.
  - Only the programmes the model's own table lists are offered, and only the extras it
    advertises; a model without the alternating-wash option has no zone control and the key
    is left out of the payload entirely rather than sent with a default.
  - Starting without a programme, or with the door open, is refused with a clear message
    instead of spending an account request on a command the appliance will reject.
  - Pressing start without choosing anything repeats what the appliance currently shows,
    which is how "run the same cycle again" works on the appliance's own panel.
- Translated into every supported language.


## [0.7.2] - 2026-07-26

### Fixed
- An appliance unbound while Home Assistant was **down** is now cleared on the next startup.
  The coordinator never sees such a device disappear — it simply never appears — so without
  reconciling the registry against the first inventory read it stayed forever as an
  unavailable leftover that no scan could remove.
- An appliance unbound in the vendor app is now **deleted** when a scan confirms it is gone,
  instead of being left behind as a permanently unavailable device with a dozen dead entities
  that still show up in pickers, dashboards and automations. Deletion only follows a
  *successful* inventory read — a failed scan can never be mistaken for an empty account.
- Devices can also be removed by hand from the UI, but only once the account no longer lists
  them: deleting a live appliance would just bring it back on the next scan, losing its name,
  area and entity ids.


## [0.7.0] - 2026-07-26

### Changed
- **Scanning the account is now explicit.** The inventory was re-read on a timer, which is
  the one operation that always needs the account session — and claiming it signs the
  vendor's mobile app out. Nothing in routine operation does that any more.

### Added
- *Scan for appliances* step in the integration options, which states plainly that the
  mobile app will be signed out before doing anything, and reports how many appliances were
  added or removed.
- The same action in the panel header, behind a confirmation dialog with the same warning,
  translated into every supported language.
- `holabrain.scan_devices` service for automations.

### Fixed
- The options flow no longer ends in a traceback when the cloud fails mid-scan; it reports
  the failure and leaves the existing appliances untouched.


## [0.6.0] - 2026-07-26

### Changed
- **The integration no longer competes with the vendor app for the account session.** The
  cloud allows one session per account, so any request Home Assistant makes can hit a
  session the app has taken over — and recovering from that takes the session back, which
  makes the app log in again, and so on. Status now arrives over the push channel, which
  authenticates with its own certificate and is unaffected by session ownership, and the
  periodic poll is skipped entirely while push is delivering. Heartbeats are subscribed too,
  so an idle appliance still counts as "push is alive".
- Reclaiming a session that was taken over is rate limited with a growing cool-down, so two
  clients can no longer log each other out in a loop. An isolated takeover much later starts
  from zero again.
- A rejected session is dropped from storage instead of being replayed after a restart.

### Added
- `docs/accounts.md` explaining the one-session-per-account behaviour and what to expect.
- Tests for session takeover (fake clock) and for push-first polling (cloud-call budget).


## [0.5.0] - 2026-07-26

### Added
- **Washing machine** category (composite): programme / temperature / spin / drying selects,
  power and start-pause switches, extra rinse, speed wash and automatic dosing, delayed
  start, plus status, phase, fault and consumable-level entities — each gated by its own
  capability.
- Command and status routing by the appliance's announced protocol, so families that use the
  second command dialect work through the same client.
- Documentation: `info.md` for HACS, `docs/entities.md` and `docs/capabilities.md`.

### Changed
- Binary sensors can now be registered disabled by default, used for diagnostic flags.
- Washer strings translated into every supported language.


### Added
- **Optional panel and dashboard card** — a no-build frontend (vendored Lit, served
  from `custom_components/holabrain/www`) that works purely on top of the standard
  entities: it reads `hass.states` and calls services, never the cloud API.
  - Sidebar panel, opt-in per config entry via **Configure → Show the HolaBrain
    panel in the sidebar**; the static assets are always served so the card stays
    usable with the panel switched off.
  - Dishwasher card: derived machine status, programme, time remaining, wash-stage
    strip, fault banner, door and consumable badges, power / start-pause actions,
    rinse-aid and softener gears, lifetime statistics.
  - Generic card for every other category, built from the entity registry, so a new
    appliance is usable in the panel without frontend changes.
  - `HolabrainDeviceBase` — host-agnostic base class (device discovery, role → entity
    resolution, state accessors, service calls) shared by the panel and the
    `custom:holabrain-card` Lovelace card; the card reuses the panel components
    unchanged.
- **Capability resolution for every appliance family.** Capabilities are no longer read from
  a single cloud dictionary; each family is resolved by an ordered chain of strategies whose
  results merge into one profile:
  - `DictGetResolver` — the cloud capability dictionary (dishwashers), now also
    understanding the object-shaped payload that narrows the programme list;
  - `BitfieldResolver` — the packed capability descriptor air conditioners carry in their
    metadata, decoded into feature flags plus the supported temperature range;
  - `StaticResolver` — per-model tables for ovens, washers, water heaters and lamps;
  - `StatusPresenceResolver` — features that are only visible as reported status keys
    (steam tank, food probe, auto-dosing, dual tank, fan-light fixtures).
  Unknown appliance types fall back to presence-only resolution instead of no profile.
- **Capability cache refresh.** Profiles are cached in Home Assistant storage (migrated out
  of the config entry, which now holds credentials only), revalidated on a 7-day TTL, and
  extended lazily as devices report new status keys. The new `holabrain.refresh_capabilities`
  service forces a refresh on demand; the config entry reloads itself whenever a refresh
  changes what a device advertises, so entities appear and disappear correctly.
- **Oven** (`0xB1`) — first composite category (Home Assistant has no oven platform):
  - programme composer — cooking-mode `select` (12 programmes), target-temperature,
    duration and food-probe `number`s, pre-heat `switch` and a **Start** `button` that
    submits them as the single cook instruction the cloud accepts; temperature range,
    step and pre-heat availability follow the selected programme;
  - run control — resume / pause / stop / standby buttons and a child-lock switch whose
    writes are refused while the door is open;
  - readout — derived status sensor (offline / standby / pre-heating / pre-heated /
    cooking / paused / finished / delayed), time remaining, working temperature, fault
    code, plus door, fault, pre-heat, pre-heat-reached and steam-tank flags.
  - Steam-tank and food-probe entities are capability-gated; models that report a single
    packed status integer instead of discrete flags are read through it.
- Registry descriptors for composite categories (`OvenConfig`, `OvenProgram`) and generic
  spec options: `invert` / `uid` / packed-summary fallback on binary sensors, explicit enum
  `options` and value transforms on sensors, and blocked-write guards on switches.
- Roadmap: washer composite category; optional dashboard panel with card-reusable
  components; hardware verification of the experimental categories.

## [0.3.0] - 2026-07-26

### Added
- Native standard-platform mapping for categories that have one:
  - **Lamp** (`0x13`) → `light` (on/off, brightness, tunable white 2700–6500 K, scenes).
  - **Water heater** (`0xE2`) → `water_heater` (target temperature, eco/smart/high-temp
    operation modes, on/off, heating-status sensor).
  - **Air conditioner** (`0xAC`) → `climate` (off/auto/cool/heat/dry/fan-only, target
    temperature, fan speeds).
- Registry native-mapping descriptors (`LightConfig`, `WaterHeaterConfig`, `ClimateConfig`)
  and an `iter_native` helper.

### Notes
- The lamp / water-heater / air-conditioner categories are modelled from the cloud protocol
  and are structurally complete but not yet verified against physical hardware.

## [0.2.0] - 2026-07-26

### Added
- Full cloud client in the standalone `aiodollin` core: signed HTTP transport (OEM + ToB),
  AWS IoT cloud-push (MQTT) over mutual TLS, login/token management, and APIs for devices,
  per-model capabilities and push-certificate minting.
- `HolabrainCoordinator` — a single poll + push data pump with per-model capability
  profiles fetched from the cloud and cached in the config entry.
- Config flow (account login) and a declarative device registry.
- **Dishwasher** category, fully modelled and capability-gated: wash-stage / program /
  fault / time-remaining / temperature sensors, door + salt-low + rinse-aid-low binary
  sensors, running and auto-door-open switches, and rinse-aid / water-softener numbers.
  Entities appear only when the specific model advertises the feature.
- Generic sensor / binary_sensor / switch / select / number / button platforms driven by
  the registry, ready for further categories.
- Tests: HTTP framing + error mapping, auth retry, capability parsing/gating, device
  listing and control, and state push-merge semantics.

## [0.1.0] - 2026-07-26

### Added
- Project scaffold and HACS metadata.
- Standalone `aiodollin` core package skeleton (zero Home Assistant imports) with:
  - request signing (`oem_sign`, `tob_sign`) and account password encryption,
  - exception hierarchy (`DollinError` → `AuthError` / `NetworkError` / `ApiError` /
    `RateLimitError`),
  - cloud endpoint and region constants.
- Test suite foundation: known-answer signing tests (including non-ASCII request bodies)
  and a compliance test that fails if `aiodollin` ever imports Home Assistant.
- CI: ruff, pytest, `hassfest`, and HACS validation.

[Unreleased]: https://github.com/dzerik/holabrain-ha/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/dzerik/holabrain-ha/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/dzerik/holabrain-ha/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/dzerik/holabrain-ha/releases/tag/v0.1.0
