# Security Policy

This is a community integration that holds the credentials to a cloud account which can
operate household appliances. Reports about anything that weakens that are welcome.

## Supported versions

The project is in its `0.x` series. Only the latest release is supported; fixes ship in the
next release rather than as backports.

| Version           | Supported |
| ----------------- | --------- |
| Latest release    | ✅        |
| Anything earlier  | ❌        |

## Reporting a vulnerability

**Please do not open a public issue, pull request or forum post for a security problem.**

Report it privately through GitHub:
[**Security → Report a vulnerability**](https://github.com/dzerik/holabrain-ha/security/advisories/new).
That opens a draft advisory only you and the maintainer can read.

Useful details, as far as you have them:

- integration version (`manifest.json`) and Home Assistant version;
- which part is affected — config flow, coordinator, the `aiodollin` core, the local
  discovery listener, the panel or the Lovelace card;
- steps to reproduce, and what an attacker gains;
- whether the report involves data you received from the cloud or from the local network.

What to expect:

- acknowledgement within **3 days**;
- an assessment — accepted, needs more information, or out of scope — within **7 days**;
- a fix in the next release, with the advisory published once it is available, or after
  **90 days**, whichever comes first;
- credit in the advisory, unless you would rather stay anonymous.

## Scope

In scope:

- everything under `custom_components/holabrain`, including the standalone `aiodollin` core;
- handling of account credentials, session tokens and the push-channel client certificate;
- the panel and Lovelace card in `custom_components/holabrain/www` — in particular anything
  that lets appliance- or cloud-supplied data execute as script;
- the local discovery listener, which parses unauthenticated responses from the LAN;
- the release and CI workflows in `.github`.

Out of scope here (report these to the party that owns them):

- the vendor cloud service and the appliance firmware;
- Home Assistant core, HACS and third-party dependencies;
- anything that already requires access to the Home Assistant configuration directory or an
  administrator session — at that point every secret in Home Assistant is exposed anyway.

## What the integration stores

Knowing this helps when judging impact:

- **Account e-mail and password** — entered in the config flow and kept in the config entry
  (`.storage/core.config_entries`), because the cloud rejects a stored session often enough
  that a silent re-login is the difference between working and not. They are never written
  to the log.
- **Session token** — the long-lived token issued at login, kept in the same place so a
  Home Assistant restart does not have to log in again.
- **Push-channel client certificate and private key** — minted per config entry and written
  to `.storage/holabrain_<entry_id>.crt` / `.key`. They authenticate the status channel for
  that account.
- **Wi-Fi router MAC and password** — only if you used *Add an appliance*, which needs them
  to derive an appliance verification code.

All of it lives in the Home Assistant configuration directory, so that directory and every
backup of it should be treated as secret material. Removing the config entry removes the
stored session with it.

All cloud traffic uses TLS; the push channel additionally uses mutual TLS against a pinned
Amazon Root CA 1. The integration talks to the vendor cloud and to appliances on your own
network, and to nothing else — there is no telemetry.

## How this repository is kept honest

- CI runs with a read-only token and uses no secrets; only the release job may write, and
  only to publish the release asset.
- CodeQL analyses the Python, the JavaScript and the workflows themselves.
- Dependabot keeps the test tooling and the workflow actions current.
