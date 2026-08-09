# One account, one session

The cloud keeps a **single live session per account**. Signing in anywhere invalidates the
session that was active before it: the previous token starts failing every request with
*"unusual activity on your account"*.

That is a property of the service, not of this integration, and it has a practical
consequence: **Home Assistant and the vendor's mobile app share one slot.** Whoever signed in
last owns it.

## The short version

| Action | Signs the mobile app out? |
|---|---|
| Reading status (sensors, states, the panel, the card) | **No** |
| Restarting Home Assistant | No |
| Sending a command while Home Assistant owns the session | No |
| Sending a command while the *app* owns the session | Yes — the session is taken back |
| **Scan for appliances** (option flow, panel button, `holabrain.scan_devices`) | **Yes, always** |
| **Add an appliance** (claiming one) | **Yes** |
| **Refresh now** (account device) | **Yes** — that is what you are asking for |
| **Refresh token** (account device, `holabrain.refresh_token`) | **Yes** — a login claims the session |
| Reading consumption figures (exclusive mode, after a wash) | **Yes**, once per cycle |

## Choosing who wins: cooperative or exclusive

There is no way to share the session, only a choice about who wins — so the integration asks
instead of deciding for you. The account device carries a **Exclusive mode** switch and a
**Refresh now** button.

**Cooperative (the default).** The integration never spends an account request on its own
initiative. Status comes from the push channel, which uses its own certificate and does not
touch the session; consumption figures are fetched once at start-up and then only when you
ask. The mobile app keeps working normally. The cost is that if the push channel dies
quietly, nothing notices until you press **Refresh now** — the integration will not start
competing to find out.

**Exclusive.** Home Assistant behaves as the primary client: it polls when push is silent,
re-reads the consumption figures after every wash, and reclaims the session when something
else takes it. Expect the mobile app to be signed out repeatedly. Choose this if Home
Assistant is where you actually control the appliances.

Your own actions are never withheld in either mode. Sending a command, pressing **Refresh
now**, scanning for appliances — all of those work the same either way, because the point of
cooperative mode is to stop the *integration* from taking the session, not to stop you.

Switching takes effect immediately; no reload, and nothing is lost.

## What the integration does about it

- **It stops needing the account at all while things are quiet.** Status arrives over the push
  channel, which authenticates with its own certificate and is completely unaffected by who
  owns the account session. While push is delivering — and an idle appliance still sends
  heartbeats — the periodic poll is skipped, so Home Assistant makes **no account requests**
  and cannot collide with the app. The account is only needed to send a command, to discover
  new appliances, and if push goes silent.
- **It reuses its session.** The token is stored, so restarting Home Assistant does not log
  you out of the app.
- **It does not fight for the session.** When the app takes it over, the integration reclaims
  it once. If the app takes it again straight away, the integration waits — one minute, then
  five, then fifteen — instead of stealing it back on every poll. Two clients logging each
  other out in a loop would leave both unusable.
- **It recovers on its own.** Once the app is idle, the next cycle picks the session back up.
  No reconfiguration is needed.

## Adding an appliance to the account

**Settings → Devices & services → HolaBrain → Configure → Add an appliance.**

Home Assistant looks for appliances on your local network first. They answer a broadcast with
their own serial, model and category — no account, no typing a code off a label — and because
the id they report is the one the account uses, appliances that are **already added are left
out of the list**. The search itself needs no session and disturbs nothing.

**Claiming one only works in a narrow window**, and it is worth knowing which: an appliance
offers itself to the cloud right after the mobile app has joined it to Wi-Fi. Outside that
window the cloud knows the appliance but will not hand it over.

Pressing the appliance's pairing button does **not** reopen that window — it clears the Wi-Fi
settings and takes the appliance off the network entirely, so the cloud can no longer see it
at all. This was verified on hardware: on the network the appliance answered "known, not
offering itself"; the moment the button was pressed it vanished from the network and the cloud
reported it as offline.

So the realistic case for this step is an appliance the mobile app has just set up but failed
to add — which does happen. In every other case, add it in the mobile app; Home Assistant will
pick it up on the next scan.

The Wi-Fi BSSID and password are optional and only needed if a claim is refused. The BSSID is
the **MAC address of your router's radio** (`a4:2b:b0:11:22:33`), not the network name.

What it cannot do: **join an appliance to Wi-Fi in the first place.** Those credentials travel
over a short-range radio link between the appliance and a phone — there is no cloud path for
them, by design. A brand-new appliance still has to be set up once with the mobile app; after
that Home Assistant can claim it.

Errors are specific on purpose: *nothing answered on the network* means the appliance is off
or on another network, *not offering itself* means it must be added in the app, and *serial
unknown* means the cloud has never seen this appliance. Three different problems, three
different things to do — see
[troubleshooting.md](troubleshooting.md#add-an-appliance-cannot-find-or-claim-my-appliance).

### Renaming and removing

`holabrain.rename_device` renames the appliance in the account itself, so the vendor app sees
the new name too. (To change only the Home Assistant name, use the device page — that never
touches the account.)

`holabrain.unbind_device` removes it — irreversibly from Home Assistant's side, since putting
it back needs the appliance's own pairing button, so the service refuses unless called with
`confirm: true`.

## Re-reading the account

Reading the account list is the one thing that always needs the session, so it is never done
automatically. Scan when you have actually paired something:

- **Settings → Devices & services → HolaBrain → Configure → Scan for appliances** — shows the
  warning first and reports what it found.
- **The panel** has the same action in its header, behind the same confirmation.
- **`holabrain.scan_devices`** service, for automations.

Each of them signs the mobile app out. Everything else — readings, controls — does not. For
the same reason, **do not put a scan on a timer**: it will sign the app out on that timer.

## What you will notice

- Monitoring is unaffected: readings keep updating through the push channel even while the app
  owns the session.
- Sending a command from Home Assistant while the app holds the session takes the session
  back, so the app will ask you to sign in again.
- If the app takes the session repeatedly, Home Assistant waits it out and picks the session
  up on a later cycle.
- If you want both at once, use a **separate account** for Home Assistant and share the
  appliances with it, if the vendor app offers sharing. That is the only way to have the app
  and Home Assistant both fully live at the same time.

## Related

- Entities go `unavailable` rather than showing stale values while the session is lost —
  [troubleshooting.md](troubleshooting.md#unavailable-entities-and-outages).
- If the password itself changes, Home Assistant asks you to re-authenticate instead of
  retrying blindly.
- [capabilities.md](capabilities.md) — what the integration reads when it does use the account.
