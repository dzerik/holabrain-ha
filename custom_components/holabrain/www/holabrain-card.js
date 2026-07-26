/**
 * HolaBrain — Lovelace card.
 *
 * The dashboard counterpart of the panel: same base class, same presentational
 * components, no duplicated logic. Add it as a dashboard resource pointing at
 * this file (the integration serves it at `/holabrain_panel/holabrain-card.js`)
 * and the card is available as `custom:holabrain-card`.
 *
 * Configuration:
 *
 *   type: custom:holabrain-card
 *   device: <device registry id>   # optional, defaults to the first appliance
 *
 * The visual editor below is inlined on purpose: a dashboard resource is a
 * single module, so a separate editor file would never be fetched.
 */

import { css, html, nothing, define } from "./lit-base.js";
import { controls, surfaces } from "./styles.js";
import { mobileBase } from "./mobile-css.js";
import { HolabrainDeviceBase } from "./components/holabrain-device-base.js";
import { buildDevices } from "./ha-registry.js";
import { translator } from "./i18n.js";
import "./components/holabrain-device-card.js";
import "./components/holabrain-toast.js";

const CARD_VERSION = "0.4.0";

/* ── visual editor ──────────────────────────────────────────────────────── */
class HolabrainCardEditor extends HolabrainDeviceBase {
  static get properties() {
    return {
      ...super.properties,
      _config: { type: Object, state: true },
    };
  }

  constructor() {
    super();
    this._config = {};
  }

  setConfig(config) {
    this._config = { ...config };
  }

  static get styles() {
    return [
      surfaces,
      controls,
      css`
        .field {
          display: flex;
          align-items: center;
          gap: 12px;
          padding: 8px 0;
        }
        .label {
          flex: 1 1 auto;
          font-size: 14px;
        }
      `,
      mobileBase,
    ];
  }

  _emit(patch) {
    this._config = { ...this._config, ...patch };
    this.dispatchEvent(
      new CustomEvent("config-changed", {
        detail: { config: this._config },
        bubbles: true,
        composed: true,
      })
    );
  }

  render() {
    if (!this.hass) return nothing;
    const t = this.t;
    const selected = this._config?.device || "";
    return html`
      <div class="field">
        <span class="label">${t("devices")}</span>
        <select
          @change=${(event) =>
            this._emit({ device: event.target.value || undefined })}
        >
          <option value="" ?selected=${!selected}>—</option>
          ${this.devices.map(
            (device) => html`<option
              value=${device.id}
              ?selected=${selected === device.id}
            >
              ${device.name}
            </option>`
          )}
        </select>
      </div>
    `;
  }
}

/* ── card ───────────────────────────────────────────────────────────────── */
class HolabrainCard extends HolabrainDeviceBase {
  static get properties() {
    return {
      ...super.properties,
      _config: { type: Object, state: true },
    };
  }

  constructor() {
    super();
    this._config = {};
  }

  /** Lovelace contract: validate and store the YAML/UI configuration. */
  setConfig(config) {
    this._config = { ...(config || {}) };
    this.deviceId = this._config.device || "";
  }

  getCardSize() {
    return 6;
  }

  static getConfigElement() {
    return document.createElement("holabrain-card-editor");
  }

  /** Pre-fill the config when the card is added from the UI picker. */
  static getStubConfig(hass) {
    const devices = buildDevices(hass);
    return devices.length ? { device: devices[0].id } : {};
  }

  static get styles() {
    return [
      surfaces,
      css`
        :host {
          display: block;
        }
        .empty {
          padding: 24px 16px;
          text-align: center;
          color: var(--hb-muted);
        }
      `,
      mobileBase,
    ];
  }

  render() {
    if (!this.hass) return nothing;
    const device = this.device;
    if (!device) {
      const t = translator(this.hass);
      return html`<div class="surface empty">${t("no_devices")}</div>`;
    }
    return html`
      <div @holabrain-toast=${this._onToast}>
        <holabrain-device-card
          .hass=${this.hass}
          .deviceId=${device.id}
        ></holabrain-device-card>
        <holabrain-toast></holabrain-toast>
      </div>
    `;
  }
}

define("holabrain-card", HolabrainCard);
define("holabrain-card-editor", HolabrainCardEditor);

window.customCards = window.customCards || [];
if (!window.customCards.some((card) => card.type === "holabrain-card")) {
  window.customCards.push({
    type: "holabrain-card",
    name: "HolaBrain",
    description: "Appliance card for the HolaBrain integration.",
    preview: true,
    documentationURL: "https://github.com/dzerik/holabrain-ha",
  });
}

// eslint-disable-next-line no-console
console.info(`%c HOLABRAIN-CARD %c ${CARD_VERSION} `, "background:#03a9f4;color:#fff", "");
