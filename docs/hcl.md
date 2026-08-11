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
| ❓ **reported** | A user reported it working, with less than full coverage — for example status reads correctly but no one has tried every control. The issue linked in the row says what was actually checked. |

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
| Water heater | `0xE2` | `51020ED1`, `510214FN`, `510214HB`, `5102152H`, `51001938`, others | — | 🧪 modelled | Expected: target temperature, on/off, operation modes, heating status | — |
| Water heater | `0xE2` | `51020ED8` | Terma AquaPro WiFi | ❓ reported | Entities populate on `thingProtocol` 2 (ALT-endpoint signing fix). `current_temperature`/`temperature`/`heating`-`standby`-`keep_warm` all confirmed against the unit: setpoint was changed on the real appliance and the reported values tracked it correctly, including which raw field is which (`temp` = setpoint, `targetTemp` = measured tank temperature — swapped from what the names suggest). `remaining_time` and a `fault` problem sensor added from the same payload. **The mode model in this PR is not solid and needs its own follow-up:** sending `operation_mode: eco` from Home Assistant showed the change optimistically but reverted to `normal` on the next real poll — the cloud's query response never echoes an `eco` key at all, and the app itself has no visible "Eco" control anywhere, so this unit likely does not support it despite the flag existing in the shared `0xE2` write payload. The app's own "Model" screen is a genuine 3-way exclusive picker (**Smart** / **Single Bile** / **Dual Bile**) that writes `cloudSmart` and `bodyNum` together — not the independent flags this PR assumes, and not the static hardware descriptor `tank_configuration` is currently modelled as (kept anyway since it is at minimum an accurate read of the current value). `highTemp` is probably the live/manual trigger for the same disinfect cycle the app's separate "Disinfect" schedule toggle drives (`modeList.disinfect.error1` is reused as the highTemp error string in the vendor's own code) — not confirmed on hardware. Command write-back is confirmed to reach the cloud (the eco attempt above did complete a request) but not confirmed to *work* for any mode. Needs a fresh diagnostics dump per Model-picker state before the mode mapping can be trusted. | reporter, own hardware |
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
