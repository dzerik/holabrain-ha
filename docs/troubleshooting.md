# Troubleshooting

Symptom-first. Each section says what is happening, what to do, and when it is worth
reporting.

If you end up filing an issue, attach the integration's diagnostics —
[diagnostics.md](diagnostics.md) covers how to collect them, how to turn on debug logging, and
what must never be pasted into a public issue.

## Contents

- [Setup and sign-in](#setup-and-sign-in)
- [Missing appliances](#missing-appliances)
- [Missing or unexpected entities](#missing-or-unexpected-entities)
- [Values that look wrong](#values-that-look-wrong)
- [Commands that do nothing](#commands-that-do-nothing)
- [Unavailable entities and outages](#unavailable-entities-and-outages)
- [The mobile app keeps signing out](#the-mobile-app-keeps-signing-out)
- [Panel and dashboard card](#panel-and-dashboard-card)
- [Repair issues](#repair-issues)
- [Updating and uninstalling](#updating-and-uninstalling)
- [Reporting a problem](#reporting-a-problem)

---

## Setup and sign-in

### "Invalid account or password"

The cloud rejected the credentials. In order of likelihood:

1. **The wrong region.** An account created in one region does not exist in the other. Use the
   same region the mobile app uses — `eu` or `us`. This is the most common cause, because the
   error is the same as for a wrong password.
2. **The country code.** Two letters, uppercase, the country the account was registered in
   (for example `RU`).
3. **The password really is wrong** — confirm by signing in to the mobile app. Note that
   signing in there takes the session; that is fine, Home Assistant will take it back.

### "Failed to connect to the HolaBrain cloud"

Home Assistant could not reach the cloud at all: no DNS, no route, a proxy in the way, or the
service is down. Check that the Home Assistant host itself can reach the internet, then retry.
If it only happens sometimes, it is usually the cloud, and the integration retries by itself.

### "This account is already configured"

There is already a config entry for that email. To change its password, region or country, use
**HolaBrain → ⋮ → Reconfigure** instead of adding a second entry.

### "Those credentials belong to a different account"

You entered a different email in the **Reconfigure** flow. A config entry is tied to one
account for the life of its entities — add a second entry for the other account.

### Home Assistant asks me to re-authenticate

The cloud stopped accepting the stored password. Almost always this means the account password
was changed somewhere else. Enter the new one in the re-authentication dialog; nothing is lost,
entities and history stay.

If it keeps happening with a password you know is correct, collect debug logs and report it —
the integration distinguishes "bad credentials" from "another client holds the session", and
being asked for a password in the second case would be a bug.

---

## Missing appliances

### An appliance I paired after setup does not appear

Run **Settings → Devices & services → HolaBrain → Configure → Scan for appliances**, or call
`holabrain.scan_devices`, or press *Scan* in the panel.

This is deliberately manual: reading the account inventory is the one operation that always
needs the account session, and taking it signs the mobile app out. Everyday monitoring never
does that. See [accounts.md](accounts.md).

### An appliance disappeared from Home Assistant

It is no longer on the account — removed in the mobile app, or unbound. A scan removes it from
Home Assistant too; that is the intended behaviour, not data loss.

### The appliance is on the account but has almost no entities

Its category is not modelled yet. You still get a device, and one **disabled** diagnostic
sensor per status field the appliance reports, named with the cloud's own raw key. Home
Assistant also shows a **repair issue** naming the type — see
[Repair issues](#repair-issues) below, and [hcl.md](hcl.md).

Those raw sensors are offered rather than presented, because nothing states what the keys
mean or what scale they use. Enable one on the device page if you recognise the number; a
screenshot of it next to the appliance's own display is the single most useful thing you can
attach to a request to support the category.

Nothing writable is created. A command assembled from a guessed key is not a missing
feature — it is a way to put the appliance into a state nobody asked for.

### "Add an appliance" cannot find or claim my appliance

Three distinct failures, three different fixes:

| Message | Meaning | What to do |
|---|---|---|
| *No appliances answered on the network* | Nothing replied to the local broadcast | Power the appliance on and make sure it is on the same network as Home Assistant. If Home Assistant runs in Docker with bridge networking, the broadcast may not reach it — host networking is needed. |
| *The cloud will not hand this appliance over* | The appliance is known to the cloud but is not offering itself | Expected. Add it in the mobile app. **Do not press its pairing button** — that clears its Wi-Fi settings and takes it off the network entirely. |
| *The cloud does not know this serial number* | The appliance has never been registered | It must be set up once with the mobile app; Wi-Fi credentials cannot be delivered from Home Assistant. |

The full explanation of the narrow window in which claiming works is in
[accounts.md](accounts.md#adding-an-appliance-to-the-account).

---

## Missing or unexpected entities

### A control I have on the appliance is missing in Home Assistant

Entities are capability-gated: the integration creates only what your model advertises or has
actually reported. So:

1. Call **`holabrain.refresh_capabilities`** (Developer tools → Actions). If the profile was
   resolved while the cloud was degraded, this fixes it.
2. If it is still missing, **use that feature once on the appliance itself**. Status keys the
   appliance reports are folded into the profile, so a feature that was never exercised may
   simply have never been mentioned. The entity appears after the next status frame.
3. Still missing? The cloud does not advertise the feature for your model. Please report it
   with diagnostics — that is exactly the case per-model tables exist for.

Details: [capabilities.md](capabilities.md).

### An entity exists that my appliance does not have

The appliance itself reported the matching status key, which is usually a firmware quirk.
Report it with the model code so the mapping can be narrowed; meanwhile you can disable the
entity on the device page.

### Some entities are greyed out / "disabled"

Lifetime counters (`total_cycles`, `total_water`, `total_energy`, refill counts) and a couple
of diagnostic flags are **created but disabled by default**, because they change rarely.
Enable the ones you want on the device page; they start recording from that moment.

### The entity ids are not what the docs say

Entity ids are generated from the appliance's name in the account plus the entity name. An
appliance called "Kitchen" produces `sensor.kitchen_wash_stage`. Rename the device in Home
Assistant (device page → ✏️) or in the account (`holabrain.rename_device`), or just use the
ids you see in **Developer tools → States**.

---

## Values that look wrong

First check [hcl.md](hcl.md): if the category is marked 🧪, its mapping has never been
confirmed on hardware, and a wrong scale or a wrong enum is exactly what is expected to be
found there.

| What you see | Likely cause |
|---|---|
| A temperature or duration off by a factor of 10 or 60 | The model uses a different scale for that key. Worth reporting — it is a one-line fix. |
| A programme name that does not match the appliance | The enum values differ per model. Report the model code plus which programme was actually running. |
| Remaining time frozen or jumping | Many appliances only recompute it at phase boundaries. Compare with the appliance's own display before reporting. |
| A reading that never updates | The appliance may not report that key at all; check the `status` block in the diagnostics. |

For any of these, the decisive evidence is a screenshot of the entity next to the appliance's
own display, plus diagnostics taken **mid-cycle** rather than idle.

### A reading went "unknown" when the cycle ended

That is deliberate, and it is the fix for a real problem rather than a bug.

The appliance reports every field it owns all the time, whether or not the field currently
means anything. A switched-off dishwasher keeps reporting the last wash's programme, stage
and remaining time — the vendor's own app refuses to display those and shows a table
estimate instead. Home Assistant used to pass them through, so an idle appliance showed
hours "remaining" and automations fired on a cycle that had ended days earlier.

So each reading now declares the states in which it means something:

| Reading | Meaningful while |
|---|---|
| Wash stage | a cycle is running, paused, or has just finished |
| Remaining time, water temperature | a cycle is running or paused |
| Programme | the appliance is not switched off |
| Cook time, oven temperature | a cook is running or paused |
| Heating status | the water heater is not switched off |

Outside those states the entity reads `unknown` rather than showing a leftover. If a reading
blanks while the appliance is genuinely working, that *is* worth reporting — attach
diagnostics taken at that moment, since they include the resolved appliance state.

### The consumption figures are unknown

Water and electricity are reported by four sensors — `energy_month`, `water_month`,
`energy_year`, `water_year` — in kilowatt-hours and litres. They come from the cloud's own
aggregation rather than from the appliance, which is why they are already in real units and
why they survive re-pairing: the history belongs to the appliance's record, not to the
current binding.

They are fetched once after start-up, and after that only when a wash finishes — the buckets
are calendar days, so asking more often cannot produce a different number, and every request
competes for the account's single session.

In **cooperative mode** the after-the-wash fetch is skipped as well, because that mode does
not spend account requests on the integration's own initiative. Press **Refresh now** on the
account device to bring them up to date, or switch to exclusive mode.

If they stay unknown after a refresh, the appliance's model does not report consumption: not
every model meters itself.

### The appliance's own lifetime counters are unknown

`total_cycles`, `total_water` and `total_energy` are a different thing: raw counters read out
of the status payload, disabled by default, and kept only for completeness. They appear in
the status snapshot but not in push frames, and their scale is not documented anywhere — the
vendor's own app never reads them. **Prefer the consumption sensors above**, which are stated
in real units.

---

## Commands that do nothing

### The dishwasher will not start

- **The door is open.** The start button refuses and says so; the appliance would refuse too.
- **No programme is staged.** `select.dishwasher_programme` must be set first — the appliance
  accepts programme + options + start as one instruction, so the button has nothing to send
  otherwise.
- **A cycle is already running or paused.** *Start cycle* composes a new cycle; use the
  **Running** switch to pause and resume an existing one.

### The oven ignores the start button

A cooking mode must be selected first, for the same reason. Also set the temperature *after*
the mode: each mode has its own range, and a value outside it is rejected.

### A switch flips back after a second or two

The integration updates the state optimistically and the appliance's next status frame
overwrites it. If it flips back, the appliance rejected the command. Common reasons:

- the appliance is in a state that forbids the change (child lock while the door is open, most
  settings while a cycle runs);
- a washing machine with **Remote control allowed** `off` — its panel has to permit remote
  control. That entity is disabled by default; enable it to see the flag.
- the model does not really support the feature, despite advertising it (a 🧪 category — please
  report).

### Nothing at all happens, no error

Check that the appliance is online in the mobile app first. If it is, enable debug logging
([diagnostics.md](diagnostics.md#2-enable-debug-logs)), reproduce, and look for the command
being sent and its answer. That log is what a report needs.

---

## Unavailable entities and outages

Entities go `unavailable` rather than showing stale values when the integration cannot trust
its data. The usual causes:

- **The cloud is unreachable.** One failed poll is absorbed; three in a row mark entities
  unavailable. It recovers by itself.
- **The account session was lost** to another client — see the next section.
- **The appliance is offline** (unplugged, off the Wi-Fi). The cloud reports it, and the
  integration reflects that.

Nothing needs to be reconfigured for any of them; recovery is automatic. If entities stay
unavailable for hours while the mobile app works fine, that is worth reporting with debug
logs.

### The push channel

Status normally arrives over the cloud push channel within a second. If it does not, the
60-second poll takes over — you will notice state changes lagging up to a minute rather than
failing. The diagnostics `coordinator` block shows whether push is connected and when the last
frame arrived; if `push_connected` is false for good, report it. Outbound MQTT over TLS
(port 8883) must not be blocked by your firewall.

---

## The mobile app keeps signing out

The cloud allows **one session per account**, so Home Assistant and the app share one slot.
Only three things take it:

1. **Scanning the account** — Configure → Scan, the panel's *Scan* button, or
   `holabrain.scan_devices`.
2. **Claiming an appliance** — Configure → Add an appliance.
3. **Sending a command** while the app currently holds the session.

Monitoring does not: status comes over a channel that authenticates with its own certificate
and is independent of the account session, and the poll is skipped while push is healthy.

If it is a nuisance, the workaround is a **separate account for Home Assistant**, with the
appliances shared to it if the vendor app offers sharing. Full detail:
[accounts.md](accounts.md).

Do not schedule `holabrain.scan_devices` — a scan on a timer will sign the app out on that
timer.

---

## Panel and dashboard card

### The panel is not in the sidebar

It is off by default: **HolaBrain → Configure → Panel → Show the panel in the sidebar**. If it
still does not appear, reload the page with a hard refresh (Ctrl/Cmd + Shift + R) — the
sidebar is cached by the frontend.

### "Custom element doesn't exist: holabrain-card"

The dashboard resource is missing or wrong. **Settings → Dashboards → ⋮ → Resources**, and
check there is an entry with URL `/holabrain_panel/holabrain-card.js` and type **JavaScript
module**. Then hard-refresh the browser.

### The card is stale after an update

Browsers cache dashboard resources aggressively. Hard-refresh; if that fails, append `?v=2` to
the resource URL to force a new fetch.

Note that both the panel and the card are plain Home Assistant frontend clients — they read
entity states and call services. If an entity is wrong in the card, it is wrong in
**Developer tools → States** too, and that is where to look.

---

## Repair issues

### "Unsupported appliance type (0x??)"

Your account contains an appliance category this version does not model, so it has no
entities. The issue names the type and the model. Please report it with the diagnostics
attached: they carry the raw account record and every status key the appliance sends, which is
what adding a category requires. Use the
[Appliance support form](https://github.com/dzerik/holabrain-ha/issues/new?template=device_support.yml).

Dismissing the issue does not break anything; it will reappear on the next restart while the
appliance is on the account.

---

## Updating and uninstalling

### The version did not change after an update

Home Assistant reports the version from `manifest.json`, and it only re-reads it after a
restart. Update in HACS, then **restart Home Assistant** — reloading the integration is not
enough.

### After an update, entities are gone or renamed

Between 0.x releases entity ids can change. Check the [changelog](../CHANGELOG.md) for the
version you upgraded to; anything not mentioned there is a bug worth reporting.

### Removing everything

**HolaBrain → ⋮ → Delete** removes the config entry, its devices and entities, the stored
session, the push client's key and the cached capability profiles. The account is untouched:
appliances stay bound to it and the mobile app keeps working. Uninstall in HACS afterwards and
restart.

To take an appliance off the **account**, use `holabrain.unbind_device` with `confirm: true` —
that cannot be undone from Home Assistant, because putting it back needs physical access.

---

## Reporting a problem

1. Reproduce it, ideally while the appliance is doing something.
2. Download the diagnostics (**HolaBrain → ⋮ → Download diagnostics**).
3. If a command is being ignored or the connection drops, add debug logs for the window around
   the problem.
4. Open an issue with the [bug report form](https://github.com/dzerik/holabrain-ha/issues/new/choose).

What to include, what is already redacted and what must never be attached:
**[diagnostics.md](diagnostics.md)**.

For a security-relevant finding — for example something identifying that survives redaction —
use
[Security → Report a vulnerability](https://github.com/dzerik/holabrain-ha/security/advisories/new)
instead of a public issue. See [SECURITY.md](../SECURITY.md).

## Related

- [diagnostics.md](diagnostics.md) — collecting diagnostics and logs
- [hcl.md](hcl.md) — what has been confirmed on real hardware
- [capabilities.md](capabilities.md) — why an entity is missing
- [accounts.md](accounts.md) — the single-session rule
- [entities.md](entities.md) — the entity reference
