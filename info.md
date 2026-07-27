# HolaBrain

Control your **HolaBrain** connected household appliances from Home Assistant — dishwasher,
washing machine, oven, air conditioner, water heater and lamps as ordinary entities, with
real-time status, automations and an optional dashboard.

## ⚠️ Early release (0.x) — read this first

- Only the **dishwasher** has been verified against physical hardware. The other five
  categories are modelled from the cloud API: they appear and create entities, but nobody has
  confirmed them on a real appliance yet, so a reading may be wrong or a control may be
  ignored.
  [Compatibility list →](https://github.com/dzerik/holabrain-ha/blob/main/docs/hcl.md)
- The cloud allows **one session per account**. Scanning the account, claiming an appliance,
  or sending a command while the mobile app holds the session will **sign the HolaBrain app
  out**. Everyday monitoring never does.
  [Details →](https://github.com/dzerik/holabrain-ha/blob/main/docs/accounts.md)
- Entity names and ids may still change between 0.x releases.
- Unofficial community integration — not affiliated with the appliance manufacturer or the
  cloud operator.
  [Legal notice →](https://github.com/dzerik/holabrain-ha#legal-notice)

## What you get

- **Cloud control** of every appliance bound to your account — no per-device setup.
- **Real-time status** over the cloud push channel (a stage change shows up in about a
  second), with polling as a fallback.
- **Model-aware entities** — the integration resolves what your specific model supports and
  exposes only that, so a control the appliance would refuse never appears.
- **Standard platforms** where Home Assistant has one: air conditioners are `climate`, water
  heaters are `water_heater`, lamps are `light`. Dishwashers, washers and ovens have no native
  platform and map to a coherent set of sensors, switches, selects, numbers and buttons.
- **Start a cycle from Home Assistant** — programme, extra option and zone are staged and
  submitted together, which is the only way these appliances accept a start.
- **Four services** — `refresh_capabilities`, `scan_devices`, `rename_device`,
  `unbind_device`.
- **Optional sidebar panel** — off by default; the same components also power a Lovelace card
  (`custom:holabrain-card`), so you can build your own view instead.
- Interface in English, Russian, Belarusian, Kazakh and Uzbek.

## Supported appliances

| Appliance | Platform | Status |
|---|---|---|
| Dishwasher | composite | ✅ verified on hardware |
| Washing machine | composite | 🧪 modelled, unverified |
| Oven | composite | 🧪 modelled, unverified |
| Air conditioner | `climate` | 🧪 modelled, unverified |
| Water heater | `water_heater` | 🧪 modelled, unverified |
| Lamp | `light` | 🧪 modelled, unverified |

An appliance category that is not modelled yet still appears as a device, with one disabled
diagnostic sensor per status field it reports, and raises a repair issue naming its type —
visible and reportable rather than silently ignored.

## Setup

1. Download here in HACS, then **restart Home Assistant**.
2. **Settings → Devices & Services → Add Integration → HolaBrain.**
3. Sign in with your HolaBrain account: email, password, region (`eu` or `us` — the same one
   the mobile app uses) and two-letter country code.

Every appliance on the account is added automatically. Appliances paired *later* need an
explicit **Configure → Scan for appliances**, because scanning is the one action that signs
the mobile app out.

The sidebar panel is switched on afterwards under the integration's **Configure**.

## Notes

- Requires an internet connection: the appliances are reached through the vendor cloud, there
  is no local control path.
- Requires Home Assistant **2025.3** or newer.
- A brand-new appliance still has to be set up once with the mobile app — Wi-Fi credentials
  cannot be delivered from Home Assistant.

## Documentation

- [README](https://github.com/dzerik/holabrain-ha#readme)
- [Entities by category](https://github.com/dzerik/holabrain-ha/blob/main/docs/entities.md)
- [Hardware compatibility list](https://github.com/dzerik/holabrain-ha/blob/main/docs/hcl.md)
- [Automation examples](https://github.com/dzerik/holabrain-ha/blob/main/docs/automations.md)
- [Troubleshooting](https://github.com/dzerik/holabrain-ha/blob/main/docs/troubleshooting.md)
- [Changelog](https://github.com/dzerik/holabrain-ha/blob/main/CHANGELOG.md)
