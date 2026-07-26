"""Coordinator: one poll/push data pump shared by every platform.

Status arrives two ways:

* **push** — an AWS IoT MQTT subscription per device delivers JSON frames the moment state
  changes (near-instant, low traffic); and
* **poll** — a periodic ``appliance/query`` snapshot as a fallback and to pick up anything
  push missed.

Capability profiles
-------------------
Entity generation is capability-gated, so every device needs a resolved profile before the
platforms are forwarded. Profiles come from the ``aiodollin`` resolver chain (which merges
the cloud capability dictionary, the packed descriptor, per-model tables and status-field
presence) and are cached in HA storage — not in the config entry, which holds credentials
only. The cache is kept fresh three ways:

* **TTL** — a profile older than :data:`CAPABILITY_TTL` is re-resolved, checked periodically;
* **on demand** — :meth:`HolabrainCoordinator.async_refresh_capabilities`, exposed as the
  ``holabrain.refresh_capabilities`` service;
* **lazily** — every status snapshot feeds the keys a device actually reported back into its
  profile, which is how presence-gated entities appear.

When a refresh changes what a device advertises, the config entry is reloaded so the entity
set is rebuilt.
"""

from __future__ import annotations

import contextlib
import logging
import os
import time
from collections.abc import Iterable
from dataclasses import fields, replace
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .aiodollin import (
    AuthError,
    CapabilityProfile,
    Device,
    DeviceState,
    DollinClient,
    DollinError,
    RateLimitError,
    SessionTakeoverError,
)
from .aiodollin.transport.mqtt import MqttClient
from .aiodollin.transport.ssl import build_client_ssl_context
from .conditions import resolve_state
from .const import DEFAULT_SCAN_INTERVAL, DOMAIN
from .registry import CATEGORIES, get_category


class _SnapshotContext:
    """Condition context for resolving the state machine itself.

    Deliberately raw: only status keys the appliance reported. ``@state`` resolves to
    nothing here, so a rule cannot refer to the state it is helping to decide.
    """

    __slots__ = ("_state",)

    def __init__(self, state: DeviceState) -> None:
        self._state = state

    def value(self, key: str) -> str | None:
        raw = self._state.get(key)
        return None if raw is None else str(raw)

    def program_allows(self, flag: str, exclusion_param: str | None) -> bool:
        # Programme scope belongs to an entity's composer, not to the appliance's state.
        return True

_LOGGER = logging.getLogger(__name__)

# Former location of the profile cache; migrated into HA storage on first run.
_LEGACY_CAPABILITY_CACHE = "capability_cache"

CAPABILITY_STORAGE_VERSION = 1
CAPABILITY_STORAGE_KEY = f"{DOMAIN}.capabilities"

# A profile older than this is re-resolved from the cloud.
CAPABILITY_TTL = timedelta(days=7)
# How often staleness is checked. Cheap: nothing happens unless a profile is actually stale.
CAPABILITY_REVALIDATE_INTERVAL = timedelta(hours=6)

# How long the push channel may stay silent before the poll is used again. The broker keeps
# sending heartbeats, so silence means the channel is really gone rather than merely idle.
PUSH_SILENCE_LIMIT = timedelta(minutes=20)
# Debounce for persisting lazily-discovered status keys.
CAPABILITY_SAVE_DELAY = 30

# How many consecutive cycles may fail for *every* appliance before the entities are marked
# unavailable. One is the cloud having a bad minute; several in a row is an outage the user
# has to be able to see.
POLL_FAILURE_GRACE = 2

# Repair issue raised for an appliance category the integration does not model yet.
ISSUE_UNSUPPORTED = "unsupported_category"
UNSUPPORTED_HELP_URL = "https://github.com/dzerik/holabrain-ha/issues"


class HolabrainCoordinator(DataUpdateCoordinator[dict[str, DeviceState]]):
    """Owns the cloud client, device inventory, capabilities and the push connection."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, client: DollinClient
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            # Passed explicitly rather than picked up from the setup context: it is what
            # lets the coordinator start the re-authentication flow when the cloud rejects
            # the credentials, and it works the same when a coordinator is built outside a
            # config-entry setup (tests, diagnostics).
            config_entry=entry,
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
        )
        self._entry = entry
        self._client = client
        self.devices: dict[str, Device] = {}
        self.capabilities: dict[str, CapabilityProfile] = {}
        self.drafts: dict[str, dict[str, str]] = {}
        self._mqtt: MqttClient | None = None
        self._store: Store[dict[str, Any]] = Store(
            hass,
            CAPABILITY_STORAGE_VERSION,
            f"{CAPABILITY_STORAGE_KEY}_{entry.entry_id}",
        )
        self._gating_tokens: frozenset[str] = _gating_tokens()
        self._unsub_revalidate: Any = None
        self._last_push: datetime | None = None
        self._setup_done = False
        self._failed_polls = 0
        self._unsupported: set[str] = set()
        self._snapshot_due = False
        self._machine_states: dict[str, tuple[DeviceState, str | None]] = {}

    @property
    def client(self) -> DollinClient:
        return self._client

    @property
    def push_connected(self) -> bool:
        """Whether a push connection is currently established."""
        return self._mqtt is not None

    @property
    def last_push(self) -> datetime | None:
        """When the push channel last delivered anything (heartbeats included)."""
        return self._last_push

    def machine_state(self, thing_code: str) -> str | None:
        """The category's state-machine state for a device, or ``None`` if undecidable.

        Resolved once per snapshot and cached against its identity: every gated entity on
        the device asks for this on every update, and the appliance families need several
        status keys combined to answer it.
        """
        state = (self.data or {}).get(thing_code)
        if state is None:
            return None
        cached = self._machine_states.get(thing_code)
        if cached is not None and cached[0] is state:
            return cached[1]
        device = self.devices.get(thing_code)
        category = get_category(device.device_type) if device is not None else None
        name = (
            resolve_state(category.states, _SnapshotContext(state))
            if category is not None and category.states
            else None
        )
        self._machine_states[thing_code] = (state, name)
        return name

    def profile_key(self, device: Device) -> str:
        """Cache key for a device's profile: its model, or the device id if it has none."""
        return device.model or device.thing_code

    def capability_for(self, thing_code: str) -> CapabilityProfile | None:
        """Return the capability profile for a device's model, if known."""
        device = self.devices.get(thing_code)
        if device is None:
            return None
        return self.capabilities.get(self.profile_key(device))

    async def _async_setup(self) -> None:
        """One-time setup: inventory, capabilities and the push connection."""
        try:
            devices = await self._client.devices.async_list()
        except SessionTakeoverError as err:
            # The vendor app is holding the session. Nothing is wrong with the credentials,
            # so this is a retry, not a re-authentication prompt.
            raise ConfigEntryNotReady(str(err)) from err
        except AuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except DollinError as err:
            raise ConfigEntryNotReady(str(err)) from err
        self.devices = {d.thing_code: d for d in devices}
        # The inventory just read is authoritative, so anything left in the registry from a
        # previous run — an appliance unbound while Home Assistant was down — is stale.
        self._async_prune_registry()
        self._async_report_unsupported()
        await self._async_load_capabilities()
        await self._async_start_push()
        self._unsub_revalidate = async_track_time_interval(
            self.hass, self._async_revalidate, CAPABILITY_REVALIDATE_INTERVAL
        )
        self._setup_done = True

    async def async_scan_devices(self) -> tuple[int, int]:
        """Re-read the account inventory and return ``(added, removed)``.

        Listing the account needs the account session, and claiming that session logs the
        vendor's mobile app out (the cloud allows only one). Monitoring deliberately avoids
        it, so this runs **only when the user asks for it** — from the integration options,
        the panel, or the ``holabrain.scan_devices`` service.

        New devices get their capability profile resolved before any entity is built for
        them, otherwise the first pass would gate every optional feature off.
        """
        devices = await self._client.devices.async_list()

        latest = {d.thing_code: d for d in devices}
        if set(latest) == set(self.devices):
            self.devices = latest  # names / firmware may still have changed
            return (0, 0)

        added = set(latest) - set(self.devices)
        removed = set(self.devices) - set(latest)
        self.devices = latest
        if added:
            await self._async_load_capabilities()
            _LOGGER.info("discovered %s new appliance(s) on the account", len(added))
        if removed:
            _LOGGER.info("%s appliance(s) are no longer bound to the account", len(removed))
            self._async_forget_devices(removed)
        if added and self._mqtt is not None:
            for thing_code in added:
                self._mqtt.subscribe(self._client.push_topics(thing_code))
        self._async_report_unsupported()
        # Platforms build their entities from this inventory, so they have to be told the
        # moment it changes rather than waiting for the next status snapshot.
        self.async_update_listeners()
        return (len(added), len(removed))

    def _push_is_healthy(self) -> bool:
        """True while the push channel is delivering, so polling would add nothing."""
        if self._mqtt is None or self._last_push is None or not self.data:
            return False
        return dt_util.utcnow() - self._last_push < PUSH_SILENCE_LIMIT

    async def _async_update_data(self) -> dict[str, DeviceState]:
        """Refresh device status.

        Polling costs an account request, and every account request can hit a session that
        another client has taken over — which would make Home Assistant log in again and
        take the session back from that client. The push channel authenticates with its own
        certificate and is unaffected by that, so while it is delivering, the poll is
        skipped entirely and the integration stops competing for the account.
        """
        if self._push_is_healthy() and not self._snapshot_due:
            return dict(self.data)
        # One snapshot settles every pending trigger; clear it before the request so a
        # failure does not leave the flag latched into a poll on every tick.
        self._snapshot_due = False

        states: dict[str, DeviceState] = dict(self.data or {})
        # Drop states of appliances that left the account so stale values cannot linger.
        states = {code: state for code, state in states.items() if code in self.devices}
        errors = 0
        for thing_code in self.devices:
            try:
                states[thing_code] = await self._client.devices.async_get_state(thing_code)
            except SessionTakeoverError as err:
                # The vendor app holds the account's single session. Nothing is wrong with
                # the credentials and there is nothing for the user to do, so this must not
                # turn into a re-authentication prompt; the auth manager reclaims the
                # session on its own once the other client goes idle.
                errors = len(self.devices)
                _LOGGER.debug("poll skipped, the account session is elsewhere: %s", err)
                break
            except AuthError as err:
                # Credentials the cloud refuses (typically a password changed in the app).
                # Retrying would keep failing and eventually lock the account, so hand it
                # straight to Home Assistant, which starts the re-authentication flow.
                raise ConfigEntryAuthFailed(str(err)) from err
            except RateLimitError as err:
                # Throttling is per account, so polling the remaining devices would only
                # dig the hole deeper.
                errors = len(self.devices)
                _LOGGER.debug("poll stopped, the cloud is rate limiting us: %s", err)
                break
            except DollinError as err:
                errors += 1
                _LOGGER.debug("poll failed for %s: %s", thing_code, err)

        if errors and errors >= len(self.devices):
            self._failed_polls += 1
            # A single failed cycle is normal: the cloud answers one query with an error
            # several times a day. Reporting that as a failed update would flicker every
            # entity to unavailable for a minute. A cloud that is *gone*, on the other hand,
            # must be visible — keeping hours-old values while pretending everything is fine
            # is how an automation ends up acting on a state from last night.
            if self._failed_polls > POLL_FAILURE_GRACE or not states:
                raise UpdateFailed(
                    f"the cloud could not be reached for any appliance "
                    f"({self._failed_polls} cycle(s) in a row)"
                )
            _LOGGER.debug(
                "poll cycle %s of %s failed for every appliance; keeping the last known "
                "state for now",
                self._failed_polls,
                POLL_FAILURE_GRACE,
            )
        else:
            self._failed_polls = 0
        self._absorb_status_fields(states)
        return states

    @callback
    def _async_report_unsupported(self) -> None:
        """Raise a repair issue per appliance category this version does not model.

        An unsupported appliance is otherwise completely silent: it produces no device, no
        entity and no log line the user would ever look at, so "the integration ignores my
        oven" looks like a bug in the setup. The issue names the category token, which is
        the one piece of information needed to add support for it.
        """
        unsupported = {
            device.device_type
            for device in self.devices.values()
            if get_category(device.device_type) is None and device.device_type
        }
        for device_type in unsupported - self._unsupported:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                f"{ISSUE_UNSUPPORTED}_{device_type}",
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key=ISSUE_UNSUPPORTED,
                translation_placeholders={
                    "device_type": device_type,
                    "models": ", ".join(
                        sorted(
                            device.model or "?"
                            for device in self.devices.values()
                            if device.device_type == device_type
                        )
                    ),
                },
                learn_more_url=UNSUPPORTED_HELP_URL,
            )
        for device_type in self._unsupported - unsupported:
            ir.async_delete_issue(
                self.hass, DOMAIN, f"{ISSUE_UNSUPPORTED}_{device_type}"
            )
        self._unsupported = unsupported

    @callback
    def _async_prune_registry(self) -> None:
        """Drop registry devices the account no longer lists.

        Removal detected while running is handled by the scan, but an appliance unbound
        while Home Assistant was down leaves a device nothing will ever match again: the
        coordinator never knew it, so it cannot notice it disappearing. Reconciling against
        a freshly read inventory is the only way to clear those.
        """
        registry = dr.async_get(self.hass)
        stale = {
            identifier[1]
            for device in dr.async_entries_for_config_entry(
                registry, self._entry.entry_id
            )
            for identifier in device.identifiers
            if identifier[0] == DOMAIN and identifier[1] not in self.devices
        }
        if stale:
            _LOGGER.info("removing %s appliance(s) no longer on the account", len(stale))
            self._async_forget_devices(stale)

    @callback
    def _async_forget_devices(self, thing_codes: set[str]) -> None:
        """Delete appliances the account no longer has.

        A device that was unbound in the vendor app is never coming back under that id, so
        leaving it behind would leave a permanently unavailable device and a dozen dead
        entities that still show up in pickers, dashboards and automations. Deleting the
        device registry entry cascades to its entities.

        This only runs after a *successful* inventory read: a failed scan raises, and an
        unknown inventory must never be mistaken for an empty one.
        """
        registry = dr.async_get(self.hass)
        for thing_code in thing_codes:
            device = registry.async_get_device(identifiers={(DOMAIN, thing_code)})
            if device is None:
                continue
            registry.async_update_device(
                device.id, remove_config_entry_id=self._entry.entry_id
            )
            self.drafts.pop(thing_code, None)

    async def async_send_instruction(
        self, thing_code: str, instruction: dict[str, Any]
    ) -> None:
        """Send a control instruction and optimistically reflect it locally."""
        await self._client.devices.async_send_instruction(thing_code, instruction)
        current = (self.data or {}).get(thing_code)
        # Structured payloads (e.g. a cook programme's step list) are not status attributes,
        # so they are never folded into the local state — the next update reports the result.
        if current is not None and all(isinstance(v, str) for v in instruction.values()):
            patched = current.apply_push(dict(instruction))
            self.async_set_updated_data({**self.data, thing_code: patched})

    # -- staged (draft) values ---------------------------------------------------------
    def draft(self, thing_code: str) -> dict[str, str]:
        """Values staged for a command the appliance only accepts as a whole."""
        return self.drafts.setdefault(thing_code, {})

    @callback
    def async_stage(self, thing_code: str, values: dict[str, str]) -> None:
        """Buffer values locally and refresh the entities that render them."""
        self.draft(thing_code).update(values)
        self.async_update_listeners()

    @callback
    def async_clear_draft(self, thing_code: str) -> None:
        """Drop staged values so entities fall back to what the appliance reports."""
        if self.drafts.pop(thing_code, None):
            self.async_update_listeners()

    # -- capabilities ------------------------------------------------------------------
    async def _async_load_capabilities(self) -> None:
        """Populate :attr:`capabilities` from the cache, resolving what is missing or stale."""
        cache = await self._async_read_cache()
        self.capabilities = {
            key: CapabilityProfile.from_cache(key, record)
            for key, record in cache.items()
            if isinstance(record, dict)
        }
        await self._async_resolve_devices(force=False)

    async def async_refresh_capabilities(self, *, force: bool = True) -> bool:
        """Re-resolve every device's capabilities. Returns True if anything changed.

        A change means the entity set may differ, so the config entry is reloaded.
        """
        changed = await self._async_resolve_devices(force=force)
        if changed and self._setup_done:
            _LOGGER.info("capabilities changed, reloading %s", DOMAIN)
            self.hass.config_entries.async_schedule_reload(self._entry.entry_id)
        return changed

    async def _async_revalidate(self, _now: Any = None) -> None:
        """Periodic TTL check — re-resolves only profiles that are actually stale."""
        try:
            await self.async_refresh_capabilities(force=False)
        except Exception:
            _LOGGER.exception("capability revalidation failed")

    async def _async_resolve_devices(self, *, force: bool) -> bool:
        """Resolve profiles for all devices; persist and report whether gating changed."""
        now = time.time()
        ttl = CAPABILITY_TTL.total_seconds()
        changed = False
        seen: set[str] = set()

        for thing_code, device in self.devices.items():
            key = self.profile_key(device)
            if key in seen:
                continue
            seen.add(key)
            cached = self.capabilities.get(key)
            if not force and cached is not None and not cached.is_stale(now, ttl):
                continue

            try:
                profile = await self._client.capabilities.async_resolve(
                    device_type=device.device_type,
                    model=device.model,
                    thing_code=thing_code,
                    metadata=device.metadata,
                    status=self._known_status(thing_code),
                )
            except DollinError as err:
                if cached is not None:
                    _LOGGER.debug("keeping cached capabilities for %s (%s)", key, err)
                else:
                    _LOGGER.warning("no capabilities for %s: %s", key, err)
                continue

            # The cache owns the timestamp: a profile is exactly as old as the moment it was
            # resolved here, whatever clock the resolver chain used.
            profile = replace(profile, fetched_at=now)
            if cached is not None:
                # Field discovery is monotonic: never let a truncated response drop entities.
                profile = profile.with_fields(cached.present_fields)
                if profile.fingerprint() != cached.fingerprint() or self._gating_differs(
                    cached, profile
                ):
                    changed = True
            else:
                changed = True
            self.capabilities[key] = profile

        if self.capabilities:
            await self._async_save_cache()
        return changed

    def _known_status(self, thing_code: str) -> dict[str, Any]:
        """The last status we saw, so presence-based resolution needs no extra request."""
        state = (self.data or {}).get(thing_code)
        return dict(state.attributes) if state is not None else {}

    def _absorb_status_fields(self, states: dict[str, DeviceState]) -> None:
        """Feed reported status keys back into the profiles (presence-based gating).

        Only keys some registry descriptor gates on can change the entity set, so a reload is
        scheduled just for those; everything else is merely persisted.
        """
        dirty = False
        needs_reload = False
        for thing_code, state in states.items():
            device = self.devices.get(thing_code)
            if device is None:
                continue
            key = self.profile_key(device)
            profile = self.capabilities.get(key)
            if profile is None:
                continue
            updated = profile.with_fields(state.attributes.keys())
            if updated is profile:
                continue
            self.capabilities[key] = updated
            dirty = True
            if self._gating_differs(profile, updated):
                needs_reload = True

        if dirty:
            self._store.async_delay_save(self._cache_snapshot, CAPABILITY_SAVE_DELAY)
        if needs_reload and self._setup_done:
            _LOGGER.info("new gated attributes reported, reloading %s", DOMAIN)
            self.hass.config_entries.async_schedule_reload(self._entry.entry_id)

    def _gating_differs(
        self, before: CapabilityProfile, after: CapabilityProfile
    ) -> bool:
        """True if the profile gained or lost something an entity descriptor gates on."""
        before_tokens = (before.features | before.present_fields) & self._gating_tokens
        after_tokens = (after.features | after.present_fields) & self._gating_tokens
        return before_tokens != after_tokens

    # -- capability cache persistence --------------------------------------------------
    async def _async_read_cache(self) -> dict[str, Any]:
        """Load the stored cache, migrating the legacy config-entry copy once."""
        stored = await self._store.async_load()
        if isinstance(stored, dict) and stored:
            return stored

        legacy = self._entry.data.get(_LEGACY_CAPABILITY_CACHE)
        if isinstance(legacy, dict) and legacy:
            _LOGGER.debug("migrating the capability cache out of the config entry")
            data = {
                k: v for k, v in self._entry.data.items() if k != _LEGACY_CAPABILITY_CACHE
            }
            self.hass.config_entries.async_update_entry(self._entry, data=data)
            await self._store.async_save(dict(legacy))
            return dict(legacy)
        return {}

    def _cache_snapshot(self) -> dict[str, Any]:
        return {key: profile.to_cache() for key, profile in self.capabilities.items()}

    async def _async_save_cache(self) -> None:
        await self._store.async_save(self._cache_snapshot())

    # -- push (MQTT) -------------------------------------------------------------------
    async def _async_start_push(self) -> None:
        try:
            certificate = await self._client.certificates.async_create()
            cert_path, key_path = await self.hass.async_add_executor_job(
                self._write_cert_files, certificate.private_key, certificate.certificate_pem
            )
            ssl_context = await self.hass.async_add_executor_job(
                build_client_ssl_context, cert_path, key_path
            )
            self._mqtt = MqttClient(
                endpoint=certificate.endpoint,
                port=certificate.port,
                client_id=f"holabrain-{self._entry.entry_id[:12]}",
                ssl_context=ssl_context,
                on_message=self._on_push_threadsafe,
            )
            self._mqtt.connect()
            for thing_code in self.devices:
                self._mqtt.subscribe(self._client.push_topics(thing_code))
        except (DollinError, OSError) as err:
            # Push is an optimization; fall back to polling if it cannot be established.
            _LOGGER.warning("cloud push unavailable, using polling only: %s", err)
            self._mqtt = None

    def _write_cert_files(self, key_pem: str, cert_pem: str) -> tuple[str, str]:
        """Write the push client certificate to disk. Runs in an executor.

        The private key is written with owner-only permissions and never through a plain
        ``open()``: the default mode would leave it world-readable, and anything that can
        read it can impersonate this installation on the push channel for as long as the
        certificate is valid.
        """
        cert_path, key_path = cert_file_paths(self.hass, self._entry.entry_id)
        _write_private(key_path, key_pem)
        _write_private(cert_path, cert_pem)
        return cert_path, key_path

    def _on_push_threadsafe(self, topic: str, payload: dict[str, Any]) -> None:
        # Runs on paho's network thread — hop back onto the event loop.
        self.hass.loop.call_soon_threadsafe(self._handle_push, topic, payload)

    @callback
    def _handle_push(self, topic: str, payload: dict[str, Any]) -> None:
        # Any frame — including a heartbeat for another device — proves the channel is live.
        self._last_push = dt_util.utcnow()
        thing_code = _thing_code_from_topic(topic)
        if thing_code is None or not self.data:
            return
        current = self.data.get(thing_code)
        if current is None:
            return
        updated = current.apply_push(payload)
        if updated is not current:
            self._absorb_status_fields({thing_code: updated})
            self.async_set_updated_data({**self.data, thing_code: updated})
            self._note_snapshot_trigger(thing_code, current, updated)

    def _note_snapshot_trigger(
        self, thing_code: str, before: DeviceState, after: DeviceState
    ) -> None:
        """Ask for a full snapshot if this frame says a cycle just finished.

        The counters the snapshot carries are written by the appliance at that moment, so
        this is the one instant where polling buys something the push channel cannot give.
        """
        device = self.devices.get(thing_code)
        category = get_category(device.device_type) if device is not None else None
        trigger = getattr(category, "snapshot_after", None)
        if trigger is None:
            return
        if trigger.fires(before.attributes.get(trigger.key), after.attributes.get(trigger.key)):
            self._snapshot_due = True
            self.hass.async_create_task(self.async_request_refresh())

    async def async_shutdown(self) -> None:
        """Tear down the push connection and the revalidation timer."""
        if self._unsub_revalidate is not None:
            self._unsub_revalidate()
            self._unsub_revalidate = None
        if self._mqtt is not None:
            await self.hass.async_add_executor_job(self._mqtt.disconnect)
            self._mqtt = None
        await super().async_shutdown()


def cert_file_paths(hass: HomeAssistant, entry_id: str) -> tuple[str, str]:
    """Where this entry's push client certificate and its key live."""
    base = hass.config.path(".storage", f"{DOMAIN}_{entry_id}")
    return f"{base}.crt", f"{base}.key"


def _write_private(path: str, content: str) -> None:
    """Write a file only its owner can read, replacing any previous content."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(content)
    # An existing file keeps its old mode when re-opened, so tighten it explicitly.
    os.chmod(path, 0o600)


async def async_cleanup_entry_storage(hass: HomeAssistant, entry_id: str) -> None:
    """Remove the capability cache and the push certificate belonging to an entry."""
    store: Store[dict[str, Any]] = Store(
        hass, CAPABILITY_STORAGE_VERSION, f"{CAPABILITY_STORAGE_KEY}_{entry_id}"
    )
    await store.async_remove()
    await hass.async_add_executor_job(_remove_files, cert_file_paths(hass, entry_id))


def _remove_files(paths: Iterable[str]) -> None:
    for path in paths:
        with contextlib.suppress(OSError):
            os.unlink(path)


def _gating_tokens() -> frozenset[str]:
    """Every capability token some registry descriptor gates on.

    Used to decide whether a newly discovered status key can change the entity set — most
    cannot, and reloading the entry for those would be pure churn.
    """
    tokens: set[str] = set()
    for category in CATEGORIES.values():
        for spec_field in fields(category):
            value = getattr(category, spec_field.name, None)
            if not isinstance(value, tuple):
                continue
            for spec in value:
                for attribute in ("capability", "options_from_capability"):
                    token = getattr(spec, attribute, None)
                    if token:
                        tokens.add(token)
                tokens.update(getattr(spec, "requires_any", ()) or ())
    return frozenset(tokens)


def _thing_code_from_topic(topic: str) -> str | None:
    """Extract the thing code from ``<region>/<region>_<thing_code>/<channel>``."""
    parts = topic.split("/")
    if len(parts) < 2:
        return None
    middle = parts[1]
    _, _, thing_code = middle.partition("_")
    return thing_code or None
