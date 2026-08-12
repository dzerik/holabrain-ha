# Hardware compatibility list

Which appliances this integration is known to work with, and how sure we are about each one.

An appliance is identified by two values the cloud reports for it:

- the **type** — a one-byte category token such as `0xE1`, which decides the entity set
  ([registry.py](../custom_components/holabrain/registry.py));
- the **model**, an 8-character code (`sn8`) such as `760EY179`, which decides *which
  features of that category* your unit has ([capabilities.md](capabilities.md)).

Both are visible in the integration's diagnostics, and the model is shown as **Model** on the
device page. See [diagnostics.md](diagnostics.md) for how to read them off your own setup.

## What the status column means

| Status | Meaning |
|---|---|
| ✅ **verified** | The maintainer or a user ran this exact appliance through the integration and reported what worked. Readings match the appliance panel, commands take effect on the hardware. |
| 🧪 **modelled** | Built from the cloud protocol and observed cloud responses for the category, structurally complete, but never confirmed against a physical unit. |
| ❓ **reported** | A user reported it working, with less than full coverage — for example status reads correctly but no one has tried every control. The row says what was actually checked, and links the issue or pull request it came from. |

**Why 🧪 may not work on your unit.** The entity set for a modelled category is a plausible
reading of the protocol, and plausible is not the same as correct. The failure modes seen in
practice are:

- a status key exists but carries a different scale (tenths of a degree, minutes vs. seconds)
  — the entity shows a number that is wrong by a factor;
- a command is accepted by the cloud and acknowledged, but the appliance ignores it, because
  the real unit expects the field alongside another one;
- the model advertises a feature it does not have, or has one it does not advertise, so an
  entity is missing or extra ([capabilities.md](capabilities.md) covers both);
- the enum values differ per model, so a programme name maps to the wrong programme.

None of that can damage an appliance — every command goes through the same cloud endpoint the
vendor app uses, and the appliance rejects what it does not understand — but a 🧪 category can
be misleading rather than merely incomplete. Treat its readings as unconfirmed until someone
has checked them against the appliance's own display.

## The list

| Category | Type | Model (`sn8`) | Brand / name | Status | What works | Confirmed by |
|---|---|---|---|---|---|---|
| Dishwasher | `0xE1` | `760EY179` | Weissgauff | ✅ verified | Full monitoring, power, start/pause, consumables, staged programme start, cloud push status — see below | maintainer, own hardware |
| Dishwasher | `0xE1` | any other | — | 🧪 modelled | Expected to work as above: this family answers the cloud capability dictionary, so the entity set adapts per model | — |
| Lamp | `0x13` | `79010863`, others | — | 🧪 modelled | Expected: on/off, brightness, tunable white 2700–6500 K, scene effects | — |
| Water heater | `0xE2` | `51020ED1`, `510214FN`, `510214HB`, `5102152H`, `51001938`, others | — | 🧪 modelled | Expected: target temperature, on/off, operation modes, heating status. The `single`/`double`/`smart` mode model and the Smart temperature lock below are extrapolated from a single unit's dumps and are unconfirmed for the rest of the family; a model that reports neither `bodyNum` nor `cloudSmart` shows no operation mode at all rather than a guessed one | — |
| Water heater | `0xE2` | `51020ED8` | Terma AquaPro WiFi | ❓ reported | Entities populate on `thingProtocol` 2 (ALT-endpoint signing fix). `current_temperature` and `temperature` were checked against the unit, including which raw field is which (`temp` = setpoint, `targetTemp` = measured tank temperature — swapped from what the names suggest). Of the heating-status enum only `standby` and `heating` were seen; `keep_warm` (`heatStatus` 2) comes from the protocol and has not been observed. `remaining_time` and a `fault` problem sensor were added from the same payload but neither was watched change. `operation_list` is `single`/`double`/`smart`, matching the app's real "Model" picker rather than the generic category's `eco`/`high_temp` flags, and a mode set **from Home Assistant has been confirmed to reach the appliance** — see [below](#the-51020ed8-model-picker) for the evidence. Home Assistant refuses manual temperature entry while in Smart, as the app does. Not yet confirmed: setting the *temperature* from Home Assistant reaching the appliance (only the mode was tried), whether the cloud itself would reject that write in Smart, the push channel, and disinfect/`highTemp`, which stays out of `operation_list` entirely. | reporter, own hardware — [#3](https://github.com/dzerik/holabrain-ha/pull/3) |
| Air conditioner | `0xAC` | any | — | 🧪 modelled | Expected: HVAC modes, target temperature, fan speeds; features come from the packed capability descriptor in the device record | — |
| Oven | `0xB1` | any | — | 🧪 modelled | Expected: programme composition (mode, temperature, duration, probe, pre-heat) submitted by the start button, plus status and fault sensors | — |
| Washing machine | `0xDB` | `38127413`, `38127414`, others | — | 🧪 modelled | Expected: status and phase, programme, temperature, spin and drying selects, power/run, dosing warnings, delayed start | — |

Model codes listed for a 🧪 category are the ones with a **model-specific profile** in the
integration — other models of the same category still work, they just fall back to the
category's generic profile plus whatever status keys the appliance reports.

The entity set behind each "what works" cell is documented per category in
[entities.md](entities.md).

### What ✅ covers on the dishwasher

Verified on a physical Weissgauff `760EY179`:

- **Monitoring** — wash stage, programme, remaining time, water temperature, door, fault code;
  values agree with the appliance's own display.
- **Power** — the power switch turns the appliance on and off.
- **Start / pause** — the running switch starts and pauses the current cycle.
- **Consumables** — salt and rinse-aid warnings, refill counters, rinse-aid level and water
  softener settings, all gated on what the model reports.
- **Starting a cycle** — programme, extra option and wash zone are staged and submitted
  together by the start button, and the appliance runs the cycle that was selected.
- **Cloud push** — state changes arrive over the push channel within seconds, without polling
  and without taking the account session away from the mobile app ([accounts.md](accounts.md)).

### The 51020ED8 model picker

The app's own "Model" screen for this unit is a 3-way exclusive picker — **Smart** /
**Single Bile** / **Dual Bile** ("Bile" is almost certainly a mistranslation of the Chinese
for tank/liner, 胆, which also literally means "gallbladder"). It is modelled as one
mutually-exclusive `operation_mode` (`single`/`double`/`smart`), not the independent
`eco`/`cloudSmart`/`highTemp` flags a first pass at this category assumed. It is a setting,
not a fixed property of the unit, so it needs no separate sensor reporting the tank
configuration — the mode itself carries it.

Raw `query` payloads for all three states, captured back to back on the same unit, are what
the mapping below was built from:

| Model picker selection | `cloudSmart` | `bodyNum` | `temp` (setpoint) | Notes |
|---|---|---|---|---|
| Dual Bile | `0` | `2` | unchanged | → `operation_mode: double` |
| Single Bile | `0` | `1` | unchanged | → `operation_mode: single` |
| Smart | `1` | `0` (new value, not `1`/`2`) | jumped to `75` (max) unprompted | → `operation_mode: smart`; the app greys out manual temperature entry here, and the integration refuses it too |

Two more things confirmed live rather than assumed, neither included in `operation_list`:

- **`eco` is likely not supported by this unit at all**, and is dropped rather than offered.
  `operation_mode: eco` sent from Home Assistant showed the change optimistically, then
  reverted on the next real poll — the cloud's `query` response never echoes an `eco` key
  back, and the app has no visible Eco control anywhere.
- **`highTemp` is probably the manual/live trigger for the same disinfect cycle** the app's
  separate "Disinfect" schedule toggle drives on a timer — the vendor's own code reuses the
  `modeList.disinfect.error1` string as the error shown when `highTemp` is already active.
  Not confirmed on hardware; disinfect itself looks driven by a separate scheduling API
  (`v1/oemTimer/e2/*` in the vendor's plugin bundle), a genuinely separate feature rather
  than a fourth `operation_mode`, and left for its own PR.

Every row in the table above was read after changing the mode *in the app*. Since then, a
mode set from **Home Assistant** (`water_heater.set_operation_mode`) has also been confirmed
to reach and change the real appliance — so the write path is no longer just built to match
the read evidence, it has been checked against hardware directly. Still open: setting the
*temperature* from Home Assistant reaching the appliance was not separately tried (only the
mode was), and neither was the push channel.

## What a category needs to be marked verified

If you own one of the 🧪 appliances, this is the useful thing to check — in this order,
because a wrong reading invalidates everything below it:

1. **Entities exist and are not `unavailable`** after setup.
2. **Readings match the appliance.** Compare temperature, remaining time, programme and status
   against the appliance's own display, including while a cycle runs.
3. **Feature gating is right** — no control for something your unit does not have, nothing
   missing that it does.
4. **Each control takes effect on the hardware**, not just in the UI: the value is still
   correct a minute later, after the next status frame has overwritten the optimistic one.
5. **Push works** — change something on the appliance itself and see Home Assistant follow
   within a few seconds.

Report whichever of these you got to. A partial report is worth filing: "status is correct,
temperature is off by 10×" is more actionable than silence, and lands the appliance in the
list as ❓ with exactly that noted.

## My appliance is not in the list

**It has entities but is not listed** — it works through the generic profile for its
category. Please report it anyway so the row can be added; that is how 🧪 becomes ❓ and ❓
becomes ✅.

**It has no entities at all** — the category is not modelled yet. The integration raises a
repair issue naming the type and model as soon as it sees such an appliance
(*Settings → Devices & services → Repairs*). Attach the diagnostics to a report: they carry
the raw account record and every status key the appliance sends, which is what adding a
category needs.

Either way, use the
[**Appliance support** issue form](https://github.com/dzerik/holabrain-ha/issues/new?template=device_support.yml)
— it asks for exactly the fields a row needs — and see [diagnostics.md](diagnostics.md) for
what to attach and what to strip first.

## Adding a row

Entries are added by pull request against this file, with the issue linked in the last
column. Keep one row per (category, model): a status is a claim about a specific unit, and
merging models hides which one was actually tested.
