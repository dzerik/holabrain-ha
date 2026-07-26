/**
 * <holabrain-generic-card> — fallback view for a device without a dedicated
 * layout (a lamp, an air conditioner, a category added after this file).
 *
 * It shows the device header plus every entity of the device as a control row,
 * controls first and read-only values after, so a newly supported appliance is
 * usable from the panel on day one — no code change required.
 *
 * Property: `deviceId` — device registry id; empty renders the first device.
 */

import { css, html, nothing, define } from "../lit-base.js";
import { controls, surfaces } from "../styles.js";
import { mobileBase } from "../mobile-css.js";
import { HolabrainDeviceBase } from "./holabrain-device-base.js";
import { renderEntityRow } from "./holabrain-controls.js";
import { formatMinutes } from "../i18n.js";
import "./holabrain-controls.js";
import "./holabrain-metric.js";

/** Domains the user can act on — rendered above the read-only rows. */
const ACTIONABLE = new Set([
  "switch",
  "light",
  "fan",
  "humidifier",
  "number",
  "select",
  "button",
  "climate",
  "water_heater",
]);

class HolabrainGenericCard extends HolabrainDeviceBase {
  static get styles() {
    return [
      surfaces,
      controls,
      css`
        :host {
          display: block;
        }
        .head {
          display: flex;
          align-items: center;
          gap: 8px;
          padding: 16px 16px 8px;
        }
        .name {
          font-size: 16px;
          font-weight: 500;
          margin: 0;
          flex: 1 1 auto;
          min-width: 0;
          overflow: hidden;
          text-overflow: ellipsis;
        }
        .model {
          font-size: 12px;
          color: var(--hb-muted);
          font-family: ui-monospace, SFMono-Regular, monospace;
        }
        .hero {
          display: flex;
          gap: 24px;
          align-items: flex-end;
          flex-wrap: wrap;
          padding: 4px 16px 12px;
        }
      `,
      mobileBase,
    ];
  }

  /**
   * Appliances that report a run state get the same hero treatment as the
   * dishwasher: the derived status large, the time left next to it.
   */
  _renderHero(t) {
    const hasStatus = this.hasRole("machine_status");
    const minutes = this.numberOf("remaining_time");
    if (!hasStatus && minutes === null) return nothing;
    return html`
      <div class="hero">
        ${hasStatus
          ? html`<holabrain-metric
              large
              .label=${this.labelOf("machine_status")}
              .value=${this.displayOf("machine_status")}
            ></holabrain-metric>`
          : nothing}
        ${minutes !== null
          ? html`<holabrain-metric
              .label=${this.labelOf("remaining_time")}
              .value=${formatMinutes(minutes, t)}
            ></holabrain-metric>`
          : nothing}
      </div>
    `;
  }

  render() {
    const device = this.device;
    if (!this.hass || !device) return nothing;
    const t = this.t;
    const hero = new Set(
      ["machine_status", "remaining_time"]
        .map((role) => this.entityIdFor(role))
        .filter(Boolean)
    );
    const entityIds = device.visibleEntityIds.filter((id) => !hero.has(id));
    const actionable = entityIds.filter((id) =>
      ACTIONABLE.has(id.split(".")[0])
    );
    const readOnly = entityIds.filter(
      (id) => !ACTIONABLE.has(id.split(".")[0])
    );
    return html`
      <div class="surface" @holabrain-toast=${this._onToast}>
        <div class="head">
          <p class="name">${device.name}</p>
          ${device.model
            ? html`<span class="model">${device.model}</span>`
            : nothing}
          ${this.isAvailable()
            ? nothing
            : html`<holabrain-badge
                .label=${t("offline")}
                .tone=${"bad"}
                .icon=${"mdi:cloud-off-outline"}
              ></holabrain-badge>`}
        </div>
        ${this._renderHero(t)}
        ${actionable.length
          ? html`<div class="section">
              <h3 class="section-title">${t("controls")}</h3>
              ${actionable.map((entityId) =>
                renderEntityRow(this.hass, entityId, device.name)
              )}
            </div>`
          : nothing}
        ${readOnly.length
          ? html`<div class="section">
              <h3 class="section-title">${t("status")}</h3>
              ${readOnly.map((entityId) =>
                renderEntityRow(this.hass, entityId, device.name)
              )}
            </div>`
          : nothing}
      </div>
    `;
  }
}

define("holabrain-generic-card", HolabrainGenericCard);
