/**
 * Host-agnostic base class shared by the panel and the Lovelace card.
 *
 * It owns everything that is *not* presentation:
 *   • discovery of this integration's devices from the Home Assistant
 *     registries (with a WebSocket fallback for frontends that do not expose
 *     `hass.entities`);
 *   • the currently selected device;
 *   • role → entity id resolution and typed state accessors;
 *   • service calls, error toasts and the more-info dialog.
 *
 * Subclasses only render. `holabrain-panel` adds a device switcher and a
 * multi-device grid, `holabrain-card` renders exactly one device — neither
 * duplicates a line of the logic below.
 *
 * Extension hooks (no-ops by default):
 *   _onDevicesChanged(devices) — the device list was rebuilt;
 *   _onDeviceSelected(deviceId) — the active device changed.
 */

import { LitElement } from "../lit-base.js";
import {
  buildDevices,
  deviceAvailable,
  entityLabel,
  formatState,
  indexRegistryLists,
  numericState,
  rawState,
} from "../ha-registry.js";
import { translator } from "../i18n.js";

export class HolabrainDeviceBase extends LitElement {
  static get properties() {
    return {
      hass: { type: Object },
      /** Device registry id to render; empty means "first available". */
      deviceId: { type: String },
      _registryVersion: { type: Number, state: true },
    };
  }

  constructor() {
    super();
    this.hass = null;
    this.deviceId = "";
    this._devices = [];
    this._registrySignature = null;
    this._fallbackRegistry = null;
    this._fallbackLoading = false;
    this._registryVersion = 0;
  }

  willUpdate(changed) {
    if (changed.has("hass") || changed.has("_registryVersion")) {
      this._syncDevices();
    }
  }

  // ── device discovery ────────────────────────────────────────────────────
  /**
   * Rebuild the device list only when a registry actually changed.
   *
   * `hass` is replaced on every state update; the registries are not. Comparing
   * their identity keeps the view model (and therefore the DOM structure)
   * stable while states stream in.
   */
  _syncDevices() {
    if (!this.hass) return;
    const needsFallback = !this.hass.entities || !this.hass.devices;
    if (needsFallback) this._loadFallbackRegistry();
    const registry = needsFallback ? this._fallbackRegistry : null;
    const signature = needsFallback
      ? `fallback:${this._registryVersion}`
      : [this.hass.entities, this.hass.devices];
    if (this._sameSignature(signature)) return;
    this._registrySignature = signature;
    this._devices = buildDevices(this.hass, registry);
    if (this.deviceId && !this._devices.some((d) => d.id === this.deviceId)) {
      // The configured device disappeared (removed, renamed integration entry).
      this.deviceId = "";
    }
    this._onDevicesChanged(this._devices);
  }

  _sameSignature(signature) {
    const previous = this._registrySignature;
    if (previous === null) return false;
    if (Array.isArray(signature) && Array.isArray(previous)) {
      return signature[0] === previous[0] && signature[1] === previous[1];
    }
    return signature === previous;
  }

  /** One-shot registry fetch for frontends without `hass.entities`. */
  async _loadFallbackRegistry() {
    if (this._fallbackLoading || this._fallbackRegistry) return;
    this._fallbackLoading = true;
    try {
      const [entities, devices] = await Promise.all([
        this.hass.callWS({ type: "config/entity_registry/list" }),
        this.hass.callWS({ type: "config/device_registry/list" }),
      ]);
      this._fallbackRegistry = indexRegistryLists(entities, devices);
      this._registryVersion += 1;
    } catch {
      this._fallbackRegistry = indexRegistryLists([], []);
    } finally {
      this._fallbackLoading = false;
    }
  }

  /** Every device of this integration, sorted by name. */
  get devices() {
    return this._devices;
  }

  /** The device the subclass should render. */
  get device() {
    if (!this._devices.length) return null;
    if (this.deviceId) {
      return this._devices.find((d) => d.id === this.deviceId) || null;
    }
    return this._devices[0];
  }

  selectDevice(deviceId) {
    if (deviceId === this.deviceId) return;
    this.deviceId = deviceId || "";
    this._onDeviceSelected(this.deviceId);
  }

  _onDevicesChanged(_devices) {}

  _onDeviceSelected(_deviceId) {}

  // ── state access ────────────────────────────────────────────────────────
  /** Entity id backing a semantic role of a device, or `null`. */
  entityIdFor(role, device = this.device) {
    return device?.roles?.[role] || null;
  }

  hasRole(role, device = this.device) {
    return Boolean(this.entityIdFor(role, device));
  }

  stateObj(role, device = this.device) {
    const entityId = this.entityIdFor(role, device);
    return entityId ? this.hass?.states?.[entityId] || null : null;
  }

  /** Raw state of a role (`null` when missing/unknown/unavailable). */
  stateOf(role, device = this.device) {
    return rawState(this.hass, this.entityIdFor(role, device));
  }

  numberOf(role, device = this.device) {
    return numericState(this.hass, this.entityIdFor(role, device));
  }

  isOn(role, device = this.device) {
    return this.stateOf(role, device) === "on";
  }

  attrOf(role, attribute, device = this.device) {
    return this.stateObj(role, device)?.attributes?.[attribute];
  }

  /** Home Assistant-formatted (translated, unit-aware) state of a role. */
  displayOf(role, device = this.device, overrideState) {
    return formatState(
      this.hass,
      this.entityIdFor(role, device),
      overrideState
    );
  }

  labelOf(role, device = this.device) {
    return entityLabel(this.hass, this.entityIdFor(role, device), device?.name);
  }

  isAvailable(device = this.device) {
    return deviceAvailable(this.hass, device);
  }

  /** `t("section_title")` bound to the user's language. */
  get t() {
    const language = this.hass?.locale?.language || this.hass?.language || "en";
    if (!this._translator || this._translatorLang !== language) {
      this._translatorLang = language;
      this._translator = translator(this.hass);
    }
    return this._translator;
  }

  // ── actions ─────────────────────────────────────────────────────────────
  /** Call a service, surfacing failures as a toast instead of a dead click. */
  async callService(domain, service, data) {
    if (!this.hass) return;
    try {
      await this.hass.callService(domain, service, data);
    } catch (err) {
      this.notify(`${this.t("error_prefix")}: ${err?.message || err}`, "error");
    }
  }

  /** Turn a role on/off through the domain-agnostic `homeassistant` service. */
  async setRole(role, on, device = this.device) {
    const entityId = this.entityIdFor(role, device);
    if (!entityId) return;
    await this.callService("homeassistant", on ? "turn_on" : "turn_off", {
      entity_id: entityId,
    });
  }

  async toggleRole(role, device = this.device) {
    await this.setRole(role, !this.isOn(role, device), device);
  }

  async setNumberRole(role, value, device = this.device) {
    const entityId = this.entityIdFor(role, device);
    if (!entityId) return;
    await this.callService("number", "set_value", {
      entity_id: entityId,
      value,
    });
  }

  async selectRoleOption(role, option, device = this.device) {
    const entityId = this.entityIdFor(role, device);
    if (!entityId) return;
    await this.callService("select", "select_option", {
      entity_id: entityId,
      option,
    });
  }

  // ── UI plumbing ─────────────────────────────────────────────────────────
  /** Open the standard entity dialog. */
  moreInfo(entityId) {
    if (!entityId) return;
    this.dispatchEvent(
      new CustomEvent("hass-more-info", {
        detail: { entityId },
        bubbles: true,
        composed: true,
      })
    );
  }

  /** Ask the host (panel or card) to show a toast. */
  notify(message, type = "info") {
    this.dispatchEvent(
      new CustomEvent("holabrain-toast", {
        detail: { message, type },
        bubbles: true,
        composed: true,
      })
    );
  }

  /** Relay handler for `holabrain-toast` bubbled up by child components. */
  _onToast(event) {
    const toast = this.shadowRoot?.querySelector("holabrain-toast");
    if (toast) {
      event.stopPropagation();
      toast.show(event.detail.message, event.detail.type);
    }
  }
}
