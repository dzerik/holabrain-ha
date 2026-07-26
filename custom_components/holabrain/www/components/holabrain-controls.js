/**
 * Generic, entity-driven control rows.
 *
 * Each row renders exactly one Home Assistant entity and drives it with a
 * standard service call, so the same element works inside the panel, inside the
 * Lovelace card, or in any other host that can hand it a `hass` object.
 *
 * Elements defined here:
 *   <holabrain-toggle-row>  — switch / any toggleable domain
 *   <holabrain-number-row>  — number entity as a slider with a live value
 *   <holabrain-select-row>  — select entity as a dropdown
 *   <holabrain-button-row>  — button entity as a press action
 *   <holabrain-state-row>   — read-only value row (sensor, binary sensor)
 *
 * Common properties: `hass`, `entityId`, optional `label` override and
 * `deviceName` (used to strip the device prefix from the entity name).
 * Failures surface as a bubbling `holabrain-toast` event.
 */

import { LitElement, css, html, nothing, define } from "../lit-base.js";
import { controls, surfaces } from "../styles.js";
import { mobileBase } from "../mobile-css.js";
import { entityLabel, formatState, rawState } from "../ha-registry.js";

const rowStyles = css`
  :host {
    display: block;
  }
  .row {
    display: flex;
    align-items: center;
    gap: 12px;
    min-height: 44px;
    padding: 4px 0;
  }
  .row + .row {
    border-top: 1px solid var(--divider-color, #e0e0e0);
  }
  .label {
    flex: 1 1 auto;
    min-width: 0;
    font-size: 14px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    cursor: pointer;
  }
  .value {
    flex: 0 0 auto;
    font-size: 14px;
    font-weight: 500;
    font-variant-numeric: tabular-nums;
  }
  .unavailable {
    opacity: 0.5;
  }
`;

/** Shared behaviour: name resolution, availability, error reporting. */
class RowBase extends LitElement {
  static get properties() {
    return {
      hass: { type: Object },
      entityId: { type: String },
      label: { type: String },
      deviceName: { type: String },
    };
  }

  constructor() {
    super();
    this.hass = null;
    this.entityId = "";
    this.label = "";
    this.deviceName = "";
  }

  get _stateObj() {
    return this.entityId ? this.hass?.states?.[this.entityId] || null : null;
  }

  get _name() {
    return (
      this.label || entityLabel(this.hass, this.entityId, this.deviceName)
    );
  }

  get _available() {
    const stateObj = this._stateObj;
    return Boolean(stateObj) && stateObj.state !== "unavailable";
  }

  _moreInfo() {
    this.dispatchEvent(
      new CustomEvent("hass-more-info", {
        detail: { entityId: this.entityId },
        bubbles: true,
        composed: true,
      })
    );
  }

  async _call(domain, service, data) {
    try {
      await this.hass.callService(domain, service, {
        entity_id: this.entityId,
        ...data,
      });
    } catch (err) {
      this.dispatchEvent(
        new CustomEvent("holabrain-toast", {
          detail: { message: err?.message || String(err), type: "error" },
          bubbles: true,
          composed: true,
        })
      );
    }
  }
}

class HolabrainToggleRow extends RowBase {
  static get styles() {
    return [
      surfaces,
      controls,
      rowStyles,
      css`
        .track {
          flex: 0 0 auto;
          width: 48px;
          height: 28px;
          border-radius: 999px;
          border: none;
          padding: 0;
          min-height: 28px;
          background: var(--divider-color, #e0e0e0);
          position: relative;
          transition: background 0.18s;
        }
        .track.on {
          background: var(--hb-accent);
        }
        .knob {
          position: absolute;
          top: 3px;
          left: 3px;
          width: 22px;
          height: 22px;
          border-radius: 50%;
          background: #fff;
          box-shadow: 0 1px 3px rgba(0, 0, 0, 0.3);
          transition: transform 0.18s;
        }
        .track.on .knob {
          transform: translateX(20px);
        }
      `,
      mobileBase,
    ];
  }

  render() {
    if (!this._stateObj) return nothing;
    const on = this._stateObj.state === "on";
    return html`
      <div class="row ${this._available ? "" : "unavailable"}">
        <span class="label" @click=${this._moreInfo}>${this._name}</span>
        <button
          class="track ${on ? "on" : ""}"
          role="switch"
          aria-checked=${on ? "true" : "false"}
          aria-label=${this._name}
          ?disabled=${!this._available}
          @click=${() =>
            this._call("homeassistant", on ? "turn_off" : "turn_on")}
        >
          <span class="knob"></span>
        </button>
      </div>
    `;
  }
}

class HolabrainNumberRow extends RowBase {
  static get styles() {
    return [
      surfaces,
      controls,
      rowStyles,
      css`
        .row {
          flex-wrap: wrap;
        }
        input[type="range"] {
          flex: 1 1 160px;
          min-width: 120px;
        }
      `,
      mobileBase,
    ];
  }

  constructor() {
    super();
    // Value being dragged, before the service call goes out.
    this._pending = null;
  }

  _onInput(event) {
    // Optimistic label update; the service call lands on `change`.
    this._pending = Number(event.target.value);
    this.requestUpdate();
  }

  _onChange(event) {
    this._pending = null;
    this._call("number", "set_value", { value: Number(event.target.value) });
  }

  render() {
    const stateObj = this._stateObj;
    if (!stateObj) return nothing;
    const attrs = stateObj.attributes || {};
    const current =
      this._pending ?? (rawState(this.hass, this.entityId) !== null
        ? Number(stateObj.state)
        : attrs.min ?? 0);
    return html`
      <div class="row ${this._available ? "" : "unavailable"}">
        <span class="label" @click=${this._moreInfo}>${this._name}</span>
        <span class="value">${current}</span>
        <input
          type="range"
          min=${attrs.min ?? 0}
          max=${attrs.max ?? 100}
          step=${attrs.step ?? 1}
          .value=${String(current)}
          aria-label=${this._name}
          ?disabled=${!this._available}
          @input=${this._onInput}
          @change=${this._onChange}
        />
      </div>
    `;
  }
}

class HolabrainSelectRow extends RowBase {
  static get styles() {
    return [surfaces, controls, rowStyles, mobileBase];
  }

  render() {
    const stateObj = this._stateObj;
    if (!stateObj) return nothing;
    const options = stateObj.attributes?.options || [];
    return html`
      <div class="row ${this._available ? "" : "unavailable"}">
        <span class="label" @click=${this._moreInfo}>${this._name}</span>
        <select
          aria-label=${this._name}
          ?disabled=${!this._available}
          @change=${(event) =>
            this._call("select", "select_option", {
              option: event.target.value,
            })}
        >
          ${options.map(
            (option) => html`<option
              value=${option}
              ?selected=${option === stateObj.state}
            >
              ${formatState(this.hass, this.entityId, option)}
            </option>`
          )}
        </select>
      </div>
    `;
  }
}

class HolabrainStateRow extends RowBase {
  static get styles() {
    return [
      surfaces,
      rowStyles,
      css`
        .value.problem {
          color: var(--hb-bad);
        }
      `,
      mobileBase,
    ];
  }

  render() {
    const stateObj = this._stateObj;
    if (!stateObj) return nothing;
    const problem =
      stateObj.attributes?.device_class === "problem" &&
      stateObj.state === "on";
    return html`
      <div class="row ${this._available ? "" : "unavailable"}">
        <span class="label" @click=${this._moreInfo}>${this._name}</span>
        <span class="value ${problem ? "problem" : ""}">
          ${formatState(this.hass, this.entityId)}
        </span>
      </div>
    `;
  }
}

class HolabrainButtonRow extends RowBase {
  static get styles() {
    return [surfaces, controls, rowStyles, mobileBase];
  }

  render() {
    const stateObj = this._stateObj;
    if (!stateObj) return nothing;
    return html`
      <div class="row ${this._available ? "" : "unavailable"}">
        <span class="label" @click=${this._moreInfo}>${this._name}</span>
        <button
          ?disabled=${!this._available}
          @click=${() => this._call("button", "press")}
        >
          <!-- ha-state-icon resolves the integration's icon translations, the
               device class and the entity's own icon, in that order. -->
          <ha-state-icon
            .hass=${this.hass}
            .stateObj=${stateObj}
          ></ha-state-icon>
          <span>${this._name}</span>
        </button>
      </div>
    `;
  }
}

/**
 * Pick the right row element for an entity id.
 *
 * Keeps the "which control fits this domain" decision in one place; both the
 * appliance-specific cards and the generic fallback card call it.
 */
export function renderEntityRow(hass, entityId, deviceName, label = "") {
  if (!entityId || !hass?.states?.[entityId]) return nothing;
  const domain = entityId.split(".")[0];
  const common = { hass, entityId, deviceName, label };
  switch (domain) {
    case "switch":
    case "light":
    case "fan":
    case "humidifier":
    case "input_boolean":
      return html`<holabrain-toggle-row
        .hass=${common.hass}
        .entityId=${common.entityId}
        .deviceName=${common.deviceName}
        .label=${common.label}
      ></holabrain-toggle-row>`;
    case "number":
      return html`<holabrain-number-row
        .hass=${common.hass}
        .entityId=${common.entityId}
        .deviceName=${common.deviceName}
        .label=${common.label}
      ></holabrain-number-row>`;
    case "button":
      return html`<holabrain-button-row
        .hass=${common.hass}
        .entityId=${common.entityId}
        .deviceName=${common.deviceName}
        .label=${common.label}
      ></holabrain-button-row>`;
    case "select":
      return html`<holabrain-select-row
        .hass=${common.hass}
        .entityId=${common.entityId}
        .deviceName=${common.deviceName}
        .label=${common.label}
      ></holabrain-select-row>`;
    default:
      return html`<holabrain-state-row
        .hass=${common.hass}
        .entityId=${common.entityId}
        .deviceName=${common.deviceName}
        .label=${common.label}
      ></holabrain-state-row>`;
  }
}

define("holabrain-toggle-row", HolabrainToggleRow);
define("holabrain-number-row", HolabrainNumberRow);
define("holabrain-select-row", HolabrainSelectRow);
define("holabrain-button-row", HolabrainButtonRow);
define("holabrain-state-row", HolabrainStateRow);
