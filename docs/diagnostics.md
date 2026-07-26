# Collecting diagnostics

What to gather when something misbehaves, and what to attach when you want your appliance
added to the [compatibility list](hcl.md). In both cases the integration's own diagnostics
dump is the single most useful file, and it is designed to be safe to post in a public issue.

## 1. Download the diagnostics

**Settings → Devices & services → HolaBrain → ⋮ → Download diagnostics**

That produces one JSON file covering the account and every appliance on it. For a problem
with a single appliance, the device page has its own ⋮ → **Download diagnostics** with the
same content narrowed to that appliance.

### What is in it

| Section | Content |
|---|---|
| `entry` | The config entry — region, country, the option flags. Credentials are removed. |
| `coordinator` | Whether the last update succeeded, the poll interval, whether the cloud push channel is connected, when the last push frame arrived, how many appliances are known. |
| `devices` | One block per appliance. |

Each appliance block carries:

- `device_type` and `category` — the category token (`0xE1`, …) and the category the
  integration resolved from it, plus `supported: false` when the category is not modelled yet;
- `model` — the 8-character model code (`sn8`) and `firmware_version`;
- `plugin_type`, `thing_protocol`, `online` — how the appliance is reached and whether the
  cloud considers it reachable;
- `raw` — the account's own record for the appliance, redacted. This is the only place a
  field the integration does not model yet can show up, which is what makes it valuable for
  adding support;
- `capabilities` — the resolved capability profile: the features that gate which entities
  exist, and their parameters ([capabilities.md](capabilities.md));
- `status` — every status key the appliance currently reports, with its raw value. The
  authoritative answer to "why does this sensor show that";
- `staged` — values composed but not yet submitted (oven programme, dishwasher cycle).

### What is already removed

The dump is redacted before it is written:

- **Credentials** — account password, access and refresh tokens, session data, the push
  channel's certificate and private key.
- **Identity** — email, phone, user id, account name, postal address, coordinates.
- **Network** — MAC address, BSSID, Wi-Fi network name and password, IP address.
- **Hardware identifiers** — the appliance's full serial number, gateway and device ids.

Redaction matches by field name at any depth, so a field the integration does not model yet
is still removed as long as it is named recognisably — but see *before you post* below.

**Appliance ids are pseudonymised, not dropped.** Each appliance appears under a stable
`appliance-xxxxxxxx` name derived from its id by a one-way hash. It cannot be turned back
into a serial number, and it is the same in every report you file, so a follow-up report can
be matched to the first one.

**The model code and the category token are kept on purpose.** `760EY179` and `0xE1`
describe a product, not a household — they identify which appliance you own in the same sense
a product page does, and without them a report cannot be acted on at all.

*(Redaction lives in
[`custom_components/holabrain/diagnostics.py`](../custom_components/holabrain/diagnostics.py);
if you find something identifying that survives it, that is worth reporting on its own — see
[SECURITY.md](../SECURITY.md).)*

### Before you post

Two things are deliberately kept and may still be yours to check:

- **The appliance's name**, as you set it in the vendor app — "Kitchen dishwasher" is fine,
  a name containing a surname or an address is not. Rename it or edit the field out.
- **Anything unusual in `raw`** if your account exposes fields this integration has never
  seen. Skim that section once; it is short.

## 2. Enable debug logs

Diagnostics show the current state. A log shows the sequence that led to it — needed for
"the command does nothing", "it goes unavailable at night", sign-in and push problems.

### The quick way

**Settings → Devices & services → HolaBrain → ⋮ → Enable debug logging**, reproduce the
problem, then **⋮ → Disable debug logging**. Home Assistant downloads the relevant log
automatically when logging is turned off, and the integration is reloaded on both switches,
so the log covers a clean setup.

### With `configuration.yaml`

Use this when you need the log to survive a restart — a problem that only shows up during
startup, for instance:

```yaml
logger:
  default: warning
  logs:
    custom_components.holabrain: debug
```

Restart Home Assistant. One entry covers the whole integration, including the `aiodollin`
cloud client underneath it. The log is at `config/home-assistant.log` (and the previous run
is kept as `home-assistant.log.1`); it is also readable in the UI under
**Settings → System → Logs → Load full logs**.

Turn it back off when you are done — debug logging is verbose and the file grows quickly.

### Reading and trimming it

Take the window around the problem, not the whole file: a minute before the action you
performed through to a minute after. Debug lines are not redacted the way diagnostics are —
they can carry appliance ids and cloud payloads — so skim what you paste and replace anything
identifying with `...`.

## 3. Find your appliance's model and type

Three places, in order of convenience:

1. **Device page** — Settings → Devices & services → HolaBrain → your appliance. The **Model**
   shown there *is* the `sn8` code; the firmware version is next to it.
2. **Diagnostics** — `device_type` and `model` in the appliance's block. The only way to get
   both, and the only way for an appliance the integration does not support yet, since that
   one has no device page.
3. **The repair issue** — an unsupported appliance raises a notice under
   **Settings → Devices & services → Repairs** that names the type and model directly.

If the appliance is not on the account at all, **Configure → Add an appliance** lists what
answered on the local network with its model code, without signing in
([accounts.md](accounts.md)).

## 4. Reporting an appliance for the compatibility list

Use the
[**Appliance support** issue form](https://github.com/dzerik/holabrain-ha/issues/new?template=device_support.yml)
and include:

- **Brand and marketing name** as printed on the appliance, plus its category.
- **Type and model** (`0xE1` / `760EY179`).
- **Versions** — integration version (Settings → Devices & services → HolaBrain, or
  `manifest.json`) and Home Assistant version.
- **The diagnostics JSON**, attached as a file.
- **What you actually checked**, against the list in
  [hcl.md](hcl.md#what-a-category-needs-to-be-marked-verified):
  which readings match the appliance's own display, which controls take effect on the
  hardware, whether push updates arrive. Partial results are welcome — say what you tried and
  what you did not, and the entry is recorded with exactly that scope.
- **What was wrong**, specifically: an entity that is missing, a value that is off by a
  factor, a control the appliance ignores. A screenshot of the entity next to the appliance's
  display settles a scaling question in one image.
- **Debug logs** only if a command is being ignored or the connection drops — trimmed as
  above.

Reproducing a status reading is easiest while the appliance is doing something: run a short
programme and download the diagnostics mid-cycle, so the `status` block is not just an idle
snapshot.

## What not to attach

Not in an issue, not in a comment, not "temporarily so you can test":

- **Your account password, or the account itself.** No problem in this integration needs it,
  and it is never asked for.
- **Access, refresh or session tokens**, in full or in part.
- **The push channel's certificate or private key.**
- **The appliance's full serial number** — the model code is what identifies the product; the
  serial identifies your unit.
- **Screenshots or logs containing your email, phone number or address.**
- **MAC address, BSSID, Wi-Fi network name or password.** The BSSID field in the config flow
  is for pairing only and never needs to leave your setup.
- **Raw, unredacted cloud responses** captured outside this integration. If you have them, say
  which field you are asking about rather than pasting the payload.

The diagnostics dump has all of that removed already, which is exactly why it is the file to
attach. If you believe you have found a way in which it does not, report it privately through
[Security → Report a vulnerability](https://github.com/dzerik/holabrain-ha/security/advisories/new)
rather than in a public issue — see [SECURITY.md](../SECURITY.md).
