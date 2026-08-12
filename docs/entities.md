# Entities by appliance category

What each appliance category exposes in Home Assistant.

Two rules apply everywhere:

- **Every entity is capability-gated.** It is created only if your model advertises the
  matching feature, or has actually reported the matching status key — see
  [capabilities.md](capabilities.md). A shorter entity list than the one below is normal.
- **Entity ids follow the appliance's name in the account.** The examples here assume an
  appliance named *Dishwasher* (`sensor.dishwasher_wash_stage`); yours may be
  `sensor.kitchen_dishwasher_wash_stage`. Check **Developer tools → States**.

Categories that Home Assistant has no platform for (dishwasher, washing machine, oven) are
modelled as a coherent set of ordinary entities instead.

🧪 marks a category modelled from the cloud API but never confirmed on physical hardware. Per
model status: [hcl.md](hcl.md).

---

## Dishwasher — composite ✅ hardware-verified

| Entity | Platform | Entity id suffix | Notes |
|---|---|---|---|
| Wash stage | `sensor` (enum) | `_wash_stage` | `idle`, `pre_wash`, `main_wash`, `rinse`, `drying`, `finished` |
| Program | `sensor` (enum) | `_program` | The programme the appliance reports as running |
| Time remaining | `sensor` | `_time_remaining` | Minutes, `device_class: duration` |
| Temperature | `sensor` | `_temperature` | Current water temperature |
| Fault | `sensor` (enum) | `_fault` | `none` or the code shown on the appliance panel (`e1`, `ec`, `l4`, …); diagnostic |
| Door | `binary_sensor` | `_door` | `on` = open. The appliance refuses to start while it is |
| Salt low | `binary_sensor` | `_salt_low` | `device_class: problem`; only on models that dose salt |
| Rinse aid low | `binary_sensor` | `_rinse_aid_low` | `device_class: problem`; only on models that dose rinse aid |
| Power | `switch` | `_power` | Appliance power, separate from the running state |
| Running | `switch` | `_running` | Start / pause of the current cycle |
| Auto door open | `switch` | `_auto_door_open` | Config entity; only on models with the feature |
| Rinse aid level | `number` | `_rinse_aid_level` | Config entity; the range comes from the model's profile |
| Water softener | `number` | `_water_softener` | Config entity; the range comes from the model's profile |
| **Programme** | `select` | `_programme` | Stages the programme for the next start |
| **Extra option** | `select` | `_extra_option` | `none`, `extra_drying`, `half_load`, `power_wash`, `turbo_speed` — only the ones the model has |
| **Wash zone** | `select` | `_wash_zone` | `upper`, `lower`, `both` — only on models with zones |
| **Start cycle** | `button` | `_start_cycle` | Submits the staged cycle as one instruction |
| Salt refills, Rinse aid refills | `sensor` | `_salt_refills`, `_rinse_aid_refills` | Diagnostic, **disabled by default** |
| Electricity / water this month and this year | `sensor` | `_energy_month`, `_water_month`, `_energy_year`, `_water_year` | kWh and litres, `device_class: energy` / `water` — usable in the energy and water dashboards |

### Starting a cycle

The appliance accepts a wash as **one** instruction — programme, extra option, wash zone and
the run command together. Sending them separately does nothing useful, so the three selects
stage their values locally and *Start cycle* submits them.

Consequences worth knowing:

- The selects show what you have staged, not necessarily what the appliance is doing. The
  **Program** *sensor* is the appliance's own answer.
- *Start cycle* fails with an explanation if no programme is staged, or if the door is open.
- **Running** is start/pause of a cycle that already exists; it does not compose a new one.

---

## Washing machine — composite 🧪

| Entity | Platform | Entity id suffix | Notes |
|---|---|---|---|
| Machine status | `sensor` (enum) | `_machine_status` | `power_off`, `standby`, `running`, `pause`, `finished`, `fault` |
| Washing phase | `sensor` (enum) | `_washing_phase` | `idle`, `pre_wash`, `wash`, `rinse`, `spin`, `auto_weight`, `drying` |
| Time remaining | `sensor` | `_time_remaining` | Minutes |
| Fault | `sensor` (enum) | `_fault` | `none`, `e10`, `e12`, `e21`, `e30`, `e33`, `e37`; diagnostic |
| Detergent low | `binary_sensor` | `_detergent_low` | `device_class: problem`; needs automatic dosing |
| Softener low | `binary_sensor` | `_softener_low` | `device_class: problem`; needs automatic dosing |
| Drum cleaning due | `binary_sensor` | `_drum_cleaning_due` | `device_class: problem` |
| Power | `switch` | `_power` | |
| Running | `switch` | `_running` | Start / pause |
| Extra rinse | `switch` | `_extra_rinse` | Only if the model allows editing it |
| Speed wash | `switch` | `_speed_wash` | Only on models with the feature |
| Auto dosing | `switch` | `_auto_dosing` | Config entity; only on models with automatic dosing |
| Programme | `select` | `_programme` | `cotton`, `cotton_eco`, `quick`, `mix`, `synthetic`, `wool`, `baby`, `sport`, `rinse_spin`, `spin_only`, `drum_clean`, `steam_wash`, `wash_dry`, `dry`, `my_cycle` |
| Wash temperature | `select` | `_wash_temperature` | `tap_cold`, `20`, `30`, `40`, `60`, `70`, `90` °C |
| Spin speed | `select` | `_spin_speed` | `no_spin`, `400` … `1400` rpm |
| Drying level | `select` | `_drying_level` | `no_dry`, `auto`, `auto_extra`, `auto_less`; washer-dryers only |
| Delayed start | `number` | `_delayed_start` | Minutes, 0–1440 in steps of 30 |
| Washes since drum clean | `sensor` | `_washes_since_drum_clean` | Diagnostic, **disabled by default** |
| Remote control allowed | `binary_sensor` | `_remote_control_allowed` | Diagnostic, **disabled by default**; `off` means the appliance will ignore commands until its panel allows them |

Unlike the dishwasher, the washer's selects and switches are written directly rather than
staged, so changing a programme takes effect immediately if the appliance accepts it.

---

## Oven — composite 🧪

Home Assistant has no oven platform, so the oven is a **programme composer**: mode,
temperature, duration, probe and pre-heat stage their values locally, and *Start* submits them
as the single cook instruction the cloud accepts.

| Entity | Platform | Entity id suffix | Notes |
|---|---|---|---|
| **Cooking mode** | `select` | `_cooking_mode` | `conventional`, `convection`, `conventional_fan`, `radiant_heat`, `double_grill`, `double_grill_fan`, `pizza`, `bottom_heat`, `eco`, `fermentation`, `keep_warm`, `defrost` |
| **Target temperature** | `number` | `_target_temperature` | Range and step follow the selected mode; absent for `defrost` |
| **Cook time** | `number` | `_cook_time` | Minutes; the maximum follows the mode (`keep_warm` allows 9 h) |
| **Food probe target** | `number` | `_food_probe_target` | 30–100 °C; only on models with a probe |
| **Pre-heat** | `switch` | `_pre_heat` | Only for modes that support pre-heating |
| **Start** | `button` | `_start` | Submits the composed programme; refuses without a mode |
| Machine status | `sensor` (enum) | `_machine_status` | `offline`, `off`, `standby`, `cooking`, `preheating`, `preheat_finish`, `cook_complete`, `delay`, `pause` |
| Time remaining | `sensor` | `_time_remaining` | Minutes |
| Working temperature | `sensor` | `_working_temperature` | The setpoint the appliance is working to — there is no separate cavity probe |
| Fault code | `sensor` (enum) | `_fault_code` | `none`, `e01`, `e02`, `e03`, `e72`, `d11`; diagnostic |
| Door | `binary_sensor` | `_door` | `on` = open |
| Fault | `binary_sensor` | `_fault` | `device_class: problem`; diagnostic |
| Pre-heating / Pre-heat reached | `binary_sensor` | `_pre_heating`, `_pre_heat_reached` | The two halves of the pre-heat phase |
| Water tank, Water low, Change water | `binary_sensor` | `_water_tank`, `_water_low`, `_change_water` | Steam models only |
| Child lock | `switch` | `_child_lock` | Config entity; the appliance refuses to change it while the door is open |
| Resume / Pause / Stop / Standby | `button` | `_resume`, `_pause`, `_stop`, `_standby` | Act on the cycle that is already running |

Each cooking mode carries its own limits, and the number entities re-range themselves when the
mode changes: `eco` is 140–240 °C in steps of 20, `fermentation` is 30–50 °C, `keep_warm` is
60–100 °C, and `defrost` takes a duration only.

---

## Air conditioner — `climate` 🧪

A single `climate` entity named after the appliance.

| Feature | Detail |
|---|---|
| HVAC modes | `off`, `auto`, `cool`, `heat`, `dry`, `fan_only` |
| Target temperature | 16–30 °C, 1° steps |
| Current temperature | From the indoor sensor |
| Fan modes | `low`, `medium`, `high`, `auto` — present only if the model advertises them |

Power and mode are separate cloud keys, so selecting any active mode also powers the unit on,
and `off` powers it off. Which features exist comes from the packed capability descriptor on
the device record.

---

## Water heater — `water_heater` 🧪

A single `water_heater` entity, plus three sensors. One unit (Terma AquaPro WiFi, `51020ED8`)
has been reported working — ❓ in [hcl.md](hcl.md) — and everything below is modelled from
that one unit's payloads; the rest of the family is unconfirmed.

| Feature | Detail |
|---|---|
| Target temperature | 35–75 °C |
| Operation modes | `single`, `double`, `smart` |
| On / off | Supported |

| Entity | Platform | Entity id suffix | Notes |
|---|---|---|---|
| Heating status | `sensor` (enum) | `_heating_status` | `standby`, `heating`, `keep_warm`; blank while the appliance is off |
| Time remaining | `sensor` | `_time_remaining` | Minutes to the setpoint, `device_class: duration`; blank unless the appliance is heating, which is the only state the vendor app shows it in |
| Fault | `binary_sensor` | `_fault` | `device_class: problem`, `on` for any code other than `0`; diagnostic |

The operation modes are the three positions of the appliance's own "Model" picker, and they
are mutually exclusive rather than flags that combine: `single` and `double` say which of a
two-tank unit's tanks it heats, `smart` hands the setpoint to the appliance. A model that
reports neither of the underlying keys shows its mode as `unknown` rather than being credited
with a tank count nothing states.

**Setting a temperature while in `smart` is refused** with an error, not silently dropped:
the appliance picks its own setpoint in that mode (it went straight to 75 °C on the unit
above) and the vendor app disables manual entry there too. Leave `smart` for another mode
first if an automation needs to set a temperature.

---

## Lamp — `light` 🧪

A single `light` entity named after the appliance.

| Feature | Detail |
|---|---|
| On / off | Supported |
| Brightness | Supported |
| Colour temperature | Tunable white, 2700–6500 K |
| Effects | `manual`, `life`, `read`, `soft`, `cinema`, `night` |

Power is folded into every command, so setting brightness or an effect also turns the lamp on.
A model without a colour-temperature key falls back to brightness only, and one without
brightness to a plain on/off light.

---

## Entities that are disabled by default

Lifetime counters and a few diagnostic flags are created but not enabled, because they update
rarely and would otherwise clutter every dashboard. Enable them per entity:
**device page → the entity → ⚙ → Enabled**. They start recording from the moment you enable
them; no history is backfilled.

## Related

- [capabilities.md](capabilities.md) — why an entity you expected is not there
- [hcl.md](hcl.md) — which of the above has been confirmed on real hardware
- [automations.md](automations.md) — using these entities
