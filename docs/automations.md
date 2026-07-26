# Automation examples

Ready-to-paste automations, scripts and template sensors for HolaBrain appliances.

**Before you copy anything:** entity ids follow the appliance's name in the account. These
examples assume appliances named *Dishwasher*, *Washing machine*, *Oven*, *Boiler* and
*Air conditioner*. Replace the ids with your own — **Developer tools → States**, filter by
`dishwasher`. The full entity list per category is in [entities.md](entities.md).

The YAML uses the modern `triggers` / `conditions` / `actions` syntax (Home Assistant 2024.10
and newer). Paste it into `configuration.yaml` under `automation:`, or use the UI editor's
**Edit in YAML** and drop the `automation:` and `- alias:` wrapper accordingly.

## Contents

- [Notifications](#notifications)
- [Starting a cycle](#starting-a-cycle)
- [Consumables and maintenance](#consumables-and-maintenance)
- [Faults and safety](#faults-and-safety)
- [Climate, water heater and lamps](#climate-water-heater-and-lamps)
- [Template sensors worth having](#template-sensors-worth-having)
- [Using the services in automations](#using-the-services-in-automations)
- [Pitfalls](#pitfalls)

---

## Notifications

### The dishwasher has finished

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
          title: Dishwasher
          message: The cycle is done.
```

### Announce it on a speaker, but only when someone is home and awake

```yaml
automation:
  - alias: Announce the dishwasher
    triggers:
      - trigger: state
        entity_id: sensor.dishwasher_wash_stage
        to: finished
    conditions:
      - condition: state
        entity_id: group.family
        state: home
      - condition: time
        after: "08:00:00"
        before: "22:30:00"
    actions:
      - action: tts.speak
        target:
          entity_id: tts.google_translate_en_com
        data:
          media_player_entity_id: media_player.kitchen
          message: The dishwasher has finished.
```

### Remind me the clean dishes are still inside

Fires only if the door has not been opened for two hours after the cycle ended.

```yaml
automation:
  - alias: Dishwasher still loaded
    triggers:
      - trigger: state
        entity_id: sensor.dishwasher_wash_stage
        to: finished
        for: "02:00:00"
    conditions:
      - condition: state
        entity_id: binary_sensor.dishwasher_door
        state: "off"
    actions:
      - action: notify.mobile_app_my_phone
        data:
          message: The dishes have been sitting in the dishwasher for two hours.
```

### Fifteen minutes before the wash ends

Useful when you want to be in the kitchen when it stops.

```yaml
automation:
  - alias: Washing machine nearly done
    triggers:
      - trigger: numeric_state
        entity_id: sensor.washing_machine_time_remaining
        below: 15
    conditions:
      - condition: state
        entity_id: sensor.washing_machine_machine_status
        state: running
    actions:
      - action: notify.mobile_app_my_phone
        data:
          message: >-
            The washing machine finishes in about
            {{ states('sensor.washing_machine_time_remaining') }} minutes.
```

---

## Starting a cycle

The dishwasher and the oven **compose** a programme: the selects and numbers stage values
locally, and the start button submits them as one instruction. So an automation that starts a
cycle always looks the same — set the controls, then press the button.

### Run the dishwasher on the night tariff

```yaml
automation:
  - alias: Start the dishwasher at night
    triggers:
      - trigger: time
        at: "01:00:00"
    conditions:
      # Never start with the door open — the appliance refuses, and so does the button.
      - condition: state
        entity_id: binary_sensor.dishwasher_door
        state: "off"
      # Don't restart a machine that is already busy or has a cycle waiting inside.
      - condition: state
        entity_id: sensor.dishwasher_wash_stage
        state: idle
      # Only when you actually loaded it — flip this helper when you fill the machine.
      - condition: state
        entity_id: input_boolean.dishwasher_loaded
        state: "on"
    actions:
      - action: select.select_option
        target:
          entity_id: select.dishwasher_programme
        data:
          option: eco
      - action: select.select_option
        target:
          entity_id: select.dishwasher_extra_option
        data:
          option: none
      - action: button.press
        target:
          entity_id: button.dishwasher_start_cycle
      - action: input_boolean.turn_off
        target:
          entity_id: input_boolean.dishwasher_loaded
```

### Start when electricity is cheapest today

With an hourly price sensor (Nord Pool, Tibber, …) exposing the cheapest hour:

```yaml
automation:
  - alias: Dishwasher on the cheapest hour
    triggers:
      - trigger: template
        value_template: >-
          {{ now().hour == state_attr('sensor.electricity_price', 'cheapest_hour') | int(-1) }}
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

### A script for "quick wash now"

Put this on a dashboard button.

```yaml
script:
  dishwasher_quick_wash:
    alias: Dishwasher — quick wash
    sequence:
      - condition: state
        entity_id: binary_sensor.dishwasher_door
        state: "off"
      - action: select.select_option
        target:
          entity_id: select.dishwasher_programme
        data:
          option: rapid
      - action: select.select_option
        target:
          entity_id: select.dishwasher_extra_option
        data:
          option: turbo_speed
      - action: button.press
        target:
          entity_id: button.dishwasher_start_cycle
    mode: single
```

### Pre-heat the oven on the way home

```yaml
automation:
  - alias: Pre-heat the oven
    triggers:
      - trigger: zone
        entity_id: person.me
        zone: zone.home
        event: enter
    conditions:
      - condition: state
        entity_id: input_boolean.dinner_planned
        state: "on"
      - condition: state
        entity_id: binary_sensor.oven_door
        state: "off"
    actions:
      - action: select.select_option
        target:
          entity_id: select.oven_cooking_mode
        data:
          option: conventional_fan
      # Set the temperature *after* the mode: each mode has its own range,
      # and the number entity re-ranges itself when the mode changes.
      - action: number.set_value
        target:
          entity_id: number.oven_target_temperature
        data:
          value: 200
      - action: number.set_value
        target:
          entity_id: number.oven_cook_time
        data:
          value: 45
      - action: switch.turn_on
        target:
          entity_id: switch.oven_pre_heat
      - action: button.press
        target:
          entity_id: button.oven_start
```

### Tell me when the oven is up to temperature

```yaml
automation:
  - alias: Oven pre-heated
    triggers:
      - trigger: state
        entity_id: binary_sensor.oven_pre_heat_reached
        to: "on"
    actions:
      - action: notify.mobile_app_my_phone
        data:
          message: The oven is at temperature.
```

---

## Consumables and maintenance

### Salt or rinse aid is low

The `for:` avoids a notification from a single frame during a cycle, when the level sensor can
flap.

```yaml
automation:
  - alias: Dishwasher consumables low
    triggers:
      - trigger: state
        entity_id:
          - binary_sensor.dishwasher_salt_low
          - binary_sensor.dishwasher_rinse_aid_low
        to: "on"
        for: "00:05:00"
    actions:
      - action: todo.add_item
        target:
          entity_id: todo.shopping_list
        data:
          item: >-
            {{ 'Dishwasher salt' if trigger.entity_id.endswith('salt_low')
               else 'Dishwasher rinse aid' }}
```

### The washing machine wants a drum clean

```yaml
automation:
  - alias: Washing machine drum clean
    triggers:
      - trigger: state
        entity_id: binary_sensor.washing_machine_drum_cleaning_due
        to: "on"
    actions:
      - action: persistent_notification.create
        data:
          title: Washing machine
          notification_id: washer_drum_clean
          message: >-
            Time for a drum clean —
            {{ states('sensor.washing_machine_washes_since_drum_clean') }} washes since
            the last one. Run the "drum_clean" programme.
```

*(`sensor.washing_machine_washes_since_drum_clean` is disabled by default; enable it on the
device page if you want the count in the message.)*

### Track how much a cycle costs

`sensor.dishwasher_energy_month` and `sensor.dishwasher_water_month` carry the cloud's own
monthly totals in kWh and litres, with `device_class: energy` and `water`, so Home
Assistant's energy and water dashboards accept them as they are — no template, no scaling.

To follow a single cycle rather than the month, compare the daily figure before and after:

```yaml
automation:
  - alias: Report what the wash cost
    trigger:
      - platform: state
        entity_id: sensor.dishwasher_wash_stage
        to: finished
    action:
      - delay: "00:02:00"        # give the cloud a moment to book the cycle
      - service: notify.mobile_app
        data:
          message: >-
            Wash finished: {{ states('sensor.dishwasher_energy_month') }} kWh and
            {{ states('sensor.dishwasher_water_month') }} L used this month so far.
```

---

## Faults and safety

### Any appliance reports a fault

```yaml
automation:
  - alias: Appliance fault
    triggers:
      - trigger: state
        entity_id:
          - sensor.dishwasher_fault
          - sensor.washing_machine_fault
          - sensor.oven_fault_code
        not_to:
          - none
          - unknown
          - unavailable
    conditions:
      # State changes to unavailable/unknown are excluded above; also ignore the
      # transition *out* of those, which is not a new fault.
      - condition: template
        value_template: "{{ trigger.from_state.state not in ['unknown', 'unavailable'] }}"
    actions:
      - action: notify.mobile_app_my_phone
        data:
          title: Appliance fault
          message: >-
            {{ trigger.to_state.attributes.friendly_name }}:
            {{ trigger.to_state.state }}
```

*(The fault sensors are diagnostic entities and are enabled by default, but their state is a
short code — `e1`, `ec`, `e30`. Look it up in the appliance's manual.)*

### The oven was left running and nobody is home

```yaml
automation:
  - alias: Oven left on
    triggers:
      - trigger: state
        entity_id: group.family
        to: not_home
        for: "00:10:00"
    conditions:
      - condition: state
        entity_id: sensor.oven_machine_status
        state:
          - cooking
          - preheating
    actions:
      - action: notify.mobile_app_my_phone
        data:
          title: The oven is still on
          message: >-
            {{ states('sensor.oven_time_remaining') }} minutes left.
          data:
            actions:
              - action: OVEN_STOP
                title: Stop it
  - alias: Oven stop from the notification
    triggers:
      - trigger: event
        event_type: mobile_app_notification_action
        event_data:
          action: OVEN_STOP
    actions:
      - action: button.press
        target:
          entity_id: button.oven_stop
```

### The appliance went offline

Entities go `unavailable` when the cloud cannot be reached or the session is lost. A short
outage is normal; alerting after half an hour is not noise.

```yaml
automation:
  - alias: Dishwasher unreachable
    triggers:
      - trigger: state
        entity_id: sensor.dishwasher_wash_stage
        to: unavailable
        for: "00:30:00"
    actions:
      - action: persistent_notification.create
        data:
          notification_id: holabrain_offline
          title: HolaBrain
          message: The dishwasher has been unreachable for half an hour.
```

---

## Climate, water heater and lamps

These are native platforms, so every standard blueprint and card works. A few HolaBrain-shaped
examples:

### Heat the water only on the cheap tariff

```yaml
automation:
  - alias: Boiler on the night tariff
    triggers:
      - trigger: time
        at: "23:30:00"
    actions:
      - action: water_heater.set_operation_mode
        target:
          entity_id: water_heater.boiler
        data:
          operation_mode: high_temp
      - action: water_heater.set_temperature
        target:
          entity_id: water_heater.boiler
        data:
          temperature: 70

  - alias: Boiler back to eco in the morning
    triggers:
      - trigger: time
        at: "06:30:00"
    actions:
      - action: water_heater.set_operation_mode
        target:
          entity_id: water_heater.boiler
        data:
          operation_mode: eco
```

### Cool the bedroom before bedtime

```yaml
automation:
  - alias: Pre-cool the bedroom
    triggers:
      - trigger: time
        at: "22:00:00"
    conditions:
      - condition: numeric_state
        entity_id: sensor.bedroom_temperature
        above: 24
    actions:
      - action: climate.set_temperature
        target:
          entity_id: climate.air_conditioner
        data:
          temperature: 22
          hvac_mode: cool
      - action: climate.set_fan_mode
        target:
          entity_id: climate.air_conditioner
        data:
          fan_mode: low
```

Setting any active `hvac_mode` also powers the unit on — power and mode are one instruction to
the appliance.

### Evening light scene

```yaml
automation:
  - alias: Evening lamp
    triggers:
      - trigger: sun
        event: sunset
        offset: "-00:20:00"
    actions:
      - action: light.turn_on
        target:
          entity_id: light.living_room_lamp
        data:
          effect: soft
```

Brightness, colour temperature and effects all carry the power flag, so `light.turn_on` with
any of them turns the lamp on in a single command.

---

## Template sensors worth having

### When will it finish?

A timestamp is far more useful on a dashboard than "83 minutes".

```yaml
template:
  - sensor:
      - name: Dishwasher finishes at
        unique_id: dishwasher_finishes_at
        device_class: timestamp
        state: >-
          {% set left = states('sensor.dishwasher_time_remaining') | int(0) %}
          {% if left > 0 %}
            {{ (now() + timedelta(minutes=left)).isoformat() }}
          {% else %}
            {{ none }}
          {% endif %}
        availability: >-
          {{ has_value('sensor.dishwasher_time_remaining') }}
```

### One "is anything running?" sensor

```yaml
template:
  - binary_sensor:
      - name: An appliance is running
        unique_id: holabrain_any_running
        state: >-
          {{ states('sensor.dishwasher_wash_stage') not in
               ['idle', 'finished', 'unknown', 'unavailable']
             or states('sensor.washing_machine_machine_status') == 'running'
             or states('sensor.oven_machine_status') in ['cooking', 'preheating'] }}
```

---

## Using the services in automations

### Pick up an appliance you just paired

Handy as a dashboard button rather than a schedule — it signs the mobile app out.

```yaml
script:
  holabrain_scan:
    alias: HolaBrain — scan for appliances
    sequence:
      - action: holabrain.scan_devices
      - action: persistent_notification.create
        data:
          message: HolaBrain scan finished. The mobile app will ask you to sign in again.
    mode: single
```

### Re-resolve capabilities after servicing an appliance

```yaml
script:
  holabrain_refresh:
    alias: HolaBrain — refresh capabilities
    sequence:
      - action: holabrain.refresh_capabilities
        data:
          device_id: "{{ device_id('sensor.dishwasher_wash_stage') }}"
    mode: single
```

> Do **not** put `holabrain.scan_devices` on a timer. Every call takes the account session and
> signs the mobile app out — see [accounts.md](accounts.md). `refresh_capabilities` is safe to
> call occasionally but pointless on a schedule: profiles are revalidated automatically.

---

## Pitfalls

**Do not trigger on `to: "on"` for a fault sensor that is an enum.** The dishwasher, washer
and oven expose faults as `sensor` entities whose state is a code (`none`, `e1`, …). Only the
oven additionally has a `binary_sensor` fault flag.

**Exclude `unknown` and `unavailable` from state triggers.** Every entity passes through them
on restart and during a cloud outage, and a trigger like `not_to: none` will fire on the way
back. The fault example above shows the guard.

**Set the oven's mode before its temperature.** Each cooking mode has its own range; writing a
temperature that the newly selected mode does not allow is rejected.

**The dishwasher's selects are a draft, not a report.** `select.dishwasher_programme` shows
what you staged; `sensor.dishwasher_program` shows what the appliance is actually running. Use
the sensor in conditions.

**Give a command time to be confirmed.** After a write, the integration updates its local state
optimistically and the next status frame confirms it — normally within a second or two over
push. An automation that writes and immediately reads back may see the optimistic value.

**A washer with `remote_control_allowed` set to `off` ignores commands** until its own panel
re-enables remote control. That entity is disabled by default; enable it if remote starts
mysteriously do nothing.

## Related

- [entities.md](entities.md) — the full entity list per category
- [capabilities.md](capabilities.md) — why an entity referenced here may not exist for you
- [accounts.md](accounts.md) — which actions disturb the mobile app's session
- [troubleshooting.md](troubleshooting.md) — when an automation does nothing
