# Capabilities: why your appliance shows only some controls

Two appliances of the same category rarely have the same features: one dishwasher doses rinse
aid, another does not; one washer has a dryer, another only spins. Creating every possible
entity would fill the dashboard with controls the appliance refuses and readings it never
updates — worse than an absent control, because a stale reading looks real.

So the integration asks the cloud what **your specific model** can do, and builds entities
from that answer.

## Where the answer comes from

There is no single mechanism, so capability discovery is a chain of sources whose results are
merged into one profile:

| Source | Used by |
|---|---|
| Cloud capability dictionary for the model | families that answer it (e.g. dishwashers) |
| Packed capability descriptor in the device record | air conditioners |
| Per-model tables | families with fixed variants |
| Status keys the appliance actually reports | everything — the universal fallback |

The last one matters more than it looks: an appliance that reports a `probeTemp` field
demonstrably has a food probe, whether or not anything advertised it. Reported keys are only
ever added, never removed, so a truncated or offline status response cannot make entities
disappear.

A profile carries more than yes/no flags. Where the cloud states limits — the number of
rinse-aid steps, the water-softener range — those become the range of the corresponding
`number` entity, so the control cannot be set to a value the appliance would reject.

## Caching and refresh

Profiles are cached in Home Assistant storage (not in the config entry, which holds
credentials only) and are kept fresh three ways:

1. **TTL** — a profile older than a week is re-resolved; staleness is checked periodically.
2. **On demand** — the `holabrain.refresh_capabilities` service.
3. **Lazily** — every status snapshot feeds newly seen keys back into the profile.

When a refresh changes what an appliance advertises, the config entry is reloaded so the
entity set is rebuilt. If the cloud is unreachable, the cached profile is used rather than
dropping every gated entity.

The resolved profile is visible in the integration's diagnostics, under `capabilities` for
each appliance, next to the `status` keys it was partly derived from — see
[diagnostics.md](diagnostics.md).

## Troubleshooting

**A control I have on the appliance is missing.** Call `holabrain.refresh_capabilities`. If it
stays missing, use the feature once on the appliance itself: a status key the appliance has
never sent cannot be inferred, and running the corresponding programme once is usually enough
for the entity to appear. If it is still absent, the cloud does not advertise that feature for
your model — please [report it](https://github.com/dzerik/holabrain-ha/issues/new/choose) with
the model code, which is what per-model tables exist for.

**A control appeared that my appliance does not have.** That means the appliance itself
reported the status key. Please open an issue with the model code so the mapping can be
narrowed; meanwhile the entity can be disabled on the device page.

**Everything is missing after a cloud outage.** It should not be: an unreachable cloud falls
back to the cached profile precisely so gated entities survive. If it happens, that is a bug
worth reporting with debug logs.

## Related

- [entities.md](entities.md) — what each category can expose in the first place
- [hcl.md](hcl.md) — which models have been confirmed on real hardware
- [troubleshooting.md](troubleshooting.md) — missing entities in context
