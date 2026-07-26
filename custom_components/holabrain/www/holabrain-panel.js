/**
 * HolaBrain — sidebar panel.
 *
 * A thin host around the shared components: it renders one
 * `<holabrain-device-card>` per appliance plus a diagnostics tab listing every
 * entity of the integration. All the data plumbing lives in
 * `HolabrainDeviceBase`, which the Lovelace card reuses unchanged.
 *
 * The panel reads `hass.states` and calls services — it never contacts the
 * cloud API, so it is a pure Home Assistant client and works offline exactly as
 * well as the rest of the dashboard.
 */

import { css, html, nothing, define } from "./lit-base.js";
import { controls, surfaces } from "./styles.js";
import { mobileBase } from "./mobile-css.js";
import { HolabrainDeviceBase } from "./components/holabrain-device-base.js";
import { formatState } from "./ha-registry.js";
import "./components/holabrain-device-card.js";
import "./components/holabrain-toast.js";

/** Integration version, injected as `?v=` by the panel registration. */
const VERSION = new URL(import.meta.url).searchParams.get("v") || "";

class HolabrainPanel extends HolabrainDeviceBase {
  static get properties() {
    return {
      ...super.properties,
      narrow: { type: Boolean },
      panel: { type: Object },
      _tab: { type: Number, state: true },
      _filter: { type: String, state: true },
      _askScan: { type: Boolean, state: true },
      _scanning: { type: Boolean, state: true },
    };
  }

  constructor() {
    super();
    this.narrow = false;
    this._tab = 0;
    this._filter = "";
    this._askScan = false;
    this._scanning = false;
  }

  static get styles() {
    return [
      surfaces,
      controls,
      css`
        :host {
          display: block;
          min-height: 100%;
          box-sizing: border-box;
          font-family: var(
            --paper-font-body1_-_font-family,
            "Roboto",
            sans-serif
          );
          color: var(--primary-text-color);
          background: var(--primary-background-color);
        }
        .top {
          padding: 16px 16px 0;
        }
        .scan-btn {
          margin-left: auto;
          background: none;
          border: 1px solid var(--divider-color, rgba(127, 127, 127, 0.4));
          border-radius: 999px;
          color: var(--primary-text-color);
          cursor: pointer;
          font: inherit;
          font-size: 0.85rem;
          padding: 6px 14px;
        }
        .scan-btn:hover:not([disabled]) {
          background: var(--secondary-background-color);
        }
        .scan-btn[disabled] {
          opacity: 0.6;
          cursor: default;
        }
        .confirm-backdrop {
          position: fixed;
          inset: 0;
          background: rgba(0, 0, 0, 0.45);
          display: grid;
          place-items: center;
          z-index: 10;
          padding: 16px;
        }
        .confirm {
          background: var(--card-background-color, #fff);
          border-radius: 16px;
          padding: 20px;
          max-width: 460px;
          box-shadow: 0 10px 30px rgba(0, 0, 0, 0.3);
        }
        .confirm h2 {
          margin: 0 0 10px;
          font-size: 1.1rem;
        }
        .confirm p {
          margin: 0 0 18px;
          color: var(--secondary-text-color);
          line-height: 1.45;
        }
        .confirm .actions {
          display: flex;
          gap: 10px;
          justify-content: flex-end;
        }
        .confirm button {
          border-radius: 999px;
          border: 1px solid var(--divider-color, rgba(127, 127, 127, 0.4));
          background: none;
          color: var(--primary-text-color);
          cursor: pointer;
          font: inherit;
          padding: 8px 16px;
        }
        .confirm button.danger {
          background: var(--error-color, #db4437);
          border-color: transparent;
          color: #fff;
        }
        .header {
          display: flex;
          align-items: baseline;
          gap: 8px;
          margin-bottom: 12px;
        }
        .header h1 {
          margin: 0;
          font-size: 24px;
          font-weight: 400;
        }
        .version {
          font-size: 13px;
          color: var(--hb-muted);
          font-family: ui-monospace, SFMono-Regular, monospace;
        }
        .tabs {
          display: flex;
          border-bottom: 2px solid var(--divider-color, #e0e0e0);
          overflow-x: auto;
          scrollbar-width: none;
        }
        .tabs::-webkit-scrollbar {
          display: none;
        }
        .tab {
          padding: 12px 24px;
          cursor: pointer;
          font-size: 14px;
          font-weight: 500;
          text-transform: uppercase;
          letter-spacing: 0.5px;
          color: var(--hb-muted);
          border-bottom: 2px solid transparent;
          margin-bottom: -2px;
          user-select: none;
          white-space: nowrap;
        }
        .tab.active {
          color: var(--hb-accent);
          border-bottom-color: var(--hb-accent);
        }
        .content {
          padding: 16px;
        }
        .chips {
          margin-bottom: 16px;
        }
        .grid {
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(340px, 1fr));
          gap: 16px;
          align-items: start;
        }
        .empty {
          padding: 32px 16px;
          text-align: center;
          color: var(--hb-muted);
        }
        table {
          width: 100%;
          border-collapse: collapse;
          font-size: 13px;
        }
        th {
          text-align: left;
          font-weight: 500;
          color: var(--hb-muted);
          text-transform: uppercase;
          font-size: 11px;
          letter-spacing: 0.5px;
        }
        th,
        td {
          padding: 8px 12px;
          border-bottom: 1px solid var(--divider-color, #e0e0e0);
        }
        tbody tr {
          cursor: pointer;
        }
        tbody tr:hover {
          background: var(--secondary-background-color, #f5f5f5);
        }
        code {
          font-family: ui-monospace, SFMono-Regular, monospace;
          font-size: 12px;
        }
        .scroll {
          overflow-x: auto;
        }
        @media (max-width: 768px) {
          .top {
            padding: 8px 8px 0;
          }
          .content {
            padding: 8px;
          }
          .header h1 {
            font-size: 20px;
          }
          .tab {
            padding: 10px 14px;
            font-size: 12px;
          }
          .grid {
            grid-template-columns: 1fr;
          }
        }
      `,
      mobileBase,
    ];
  }

  _onDevicesChanged(devices) {
    if (this._filter && !devices.some((device) => device.id === this._filter)) {
      this._filter = "";
    }
  }

  get _visibleDevices() {
    if (!this._filter) return this.devices;
    return this.devices.filter((device) => device.id === this._filter);
  }

  _renderChips() {
    const devices = this.devices;
    if (devices.length < 2) return nothing;
    const t = this.t;
    return html`
      <div class="chips">
        <button
          class="chip ${this._filter ? "" : "active"}"
          @click=${() => (this._filter = "")}
        >
          ${t("devices")} · ${devices.length}
        </button>
        ${devices.map(
          (device) => html`<button
            class="chip ${this._filter === device.id ? "active" : ""}"
            @click=${() => (this._filter = device.id)}
          >
            ${device.name}
          </button>`
        )}
      </div>
    `;
  }

  _renderDevices() {
    const t = this.t;
    if (!this.devices.length) {
      return html`<div class="empty">
        <p>${t("no_devices")}</p>
        <p class="muted">${t("no_devices_hint")}</p>
      </div>`;
    }
    return html`
      ${this._renderChips()}
      <div class="grid">
        ${this._visibleDevices.map(
          (device) => html`<holabrain-device-card
            .hass=${this.hass}
            .deviceId=${device.id}
          ></holabrain-device-card>`
        )}
      </div>
    `;
  }

  _renderEntities() {
    const t = this.t;
    const rows = [];
    for (const device of this.devices) {
      for (const entityId of device.entityIds) {
        rows.push({ device, entityId });
      }
    }
    if (!rows.length) {
      return html`<div class="empty">${t("no_devices")}</div>`;
    }
    return html`
      <div class="surface scroll">
        <table>
          <thead>
            <tr>
              <th>${t("devices")}</th>
              <th>${t("entities")}</th>
              <th>${t("status")}</th>
            </tr>
          </thead>
          <tbody>
            ${rows.map(
              ({ device, entityId }) => html`
                <tr @click=${() => this.moreInfo(entityId)}>
                  <td>${device.name}</td>
                  <td><code>${entityId}</code></td>
                  <td>${formatState(this.hass, entityId)}</td>
                </tr>
              `
            )}
          </tbody>
        </table>
      </div>
    `;
  }

  /**
   * Ask before scanning.
   *
   * Scanning needs the account session and the cloud allows only one, so it signs the
   * vendor's mobile app out. That is a real, visible consequence for the user, so it is
   * never done silently.
   */
  _renderScanConfirm() {
    const t = this.t;
    return html`<div
      class="confirm-backdrop"
      @click=${(event) => {
        if (event.target === event.currentTarget) this._askScan = false;
      }}
    >
      <div class="confirm">
        <h2>${t("scan")}</h2>
        <p>⚠️ ${t("scan_warning")}</p>
        <div class="actions">
          <button @click=${() => (this._askScan = false)}>${t("scan_cancel")}</button>
          <button class="danger" @click=${this._scan}>${t("scan_confirm")}</button>
        </div>
      </div>
    </div>`;
  }

  /** Show a toast directly, without an event round-trip. */
  _showToast(message, type = "info") {
    this.shadowRoot?.querySelector("holabrain-toast")?.show(message, type);
  }

  _scan = async () => {
    this._askScan = false;
    this._scanning = true;
    try {
      await this.callService("holabrain", "scan_devices", {});
      this._showToast(this.t("scan_done"));
    } catch (err) {
      this._showToast(`${this.t("scan_failed")}: ${err?.message || err}`, "error");
    } finally {
      this._scanning = false;
    }
  };

  render() {
    const t = this.t;
    const tabs = [t("devices"), t("diagnostics")];
    return html`
      <div class="top">
        <div class="header">
          <h1>${t("panel_title")}</h1>
          ${VERSION ? html`<span class="version">v${VERSION}</span>` : nothing}
          <button
            class="scan-btn"
            ?disabled=${this._scanning}
            @click=${() => (this._askScan = true)}
          >
            ${this._scanning ? t("scanning") : t("scan")}
          </button>
        </div>
        <div class="tabs">
          ${tabs.map(
            (label, index) => html`<div
              class="tab ${this._tab === index ? "active" : ""}"
              @click=${() => (this._tab = index)}
            >
              ${label}
            </div>`
          )}
        </div>
      </div>
      ${this._askScan ? this._renderScanConfirm() : nothing}
      <div class="content" @holabrain-toast=${this._onToast}>
        ${this._tab === 0 ? this._renderDevices() : this._renderEntities()}
      </div>
      <holabrain-toast></holabrain-toast>
    `;
  }
}

define("holabrain-panel", HolabrainPanel);
