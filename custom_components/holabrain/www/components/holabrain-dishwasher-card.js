/**
 * <holabrain-dishwasher-card> — the full view of one dishwasher.
 *
 * Layout follows the companion app: a hero block with the derived machine
 * status, the running programme and the time left; the stage strip underneath;
 * then the primary actions, the options, the consumables and finally the
 * lifetime statistics. Every value comes from a regular Home Assistant entity
 * and every action is a plain service call.
 *
 * Sections whose entities are absent (a model without salt sensing, statistics
 * entities left disabled) are simply skipped, so the same card serves every
 * dishwasher model without configuration.
 *
 * Property: `deviceId` — device registry id; empty renders the first device.
 */

import { css, html, nothing, define } from "../lit-base.js";
import { controls, surfaces } from "../styles.js";
import { mobileBase } from "../mobile-css.js";
import { HolabrainDeviceBase } from "./holabrain-device-base.js";
import { renderEntityRow } from "./holabrain-controls.js";
import { formatMinutes } from "../i18n.js";
import { remainingEntities } from "../ha-registry.js";
import "./holabrain-controls.js";
import "./holabrain-metric.js";
import "./holabrain-stage-bar.js";

/** Stage order of a wash cycle; `idle` is not a stage the user waits through. */
const STAGE_ORDER = ["pre_wash", "main_wash", "rinse", "drying", "finished"];

/** Roles the dedicated sections already render — everything else is "other". */
const HANDLED_ROLES = [
  "power",
  "running",
  "auto_door_open",
  "wash_stage",
  "program",
  "fault",
  "remaining_time",
  "temperature",
  "door",
  "salt_low",
  "rinse_aid_low",
  "rinse_aid_level",
  "water_softener",
  "salt_refills",
  "rinse_aid_refills",
  "total_cycles",
  "total_water",
  "total_energy",
  "energy_month",
  "energy_year",
  "water_month",
  "water_year",
];

const STATUS_TONE = {
  fault: "bad",
  offline: "bad",
  running: "ok",
  finished: "ok",
  paused: "warn",
};

class HolabrainDishwasherCard extends HolabrainDeviceBase {
  static get properties() {
    return {
      ...super.properties,
      _showAll: { type: Boolean, state: true },
    };
  }

  constructor() {
    super();
    this._showAll = false;
  }

  static get styles() {
    return [
      surfaces,
      controls,
      css`
        :host {
          display: block;
        }
        .hero {
          display: flex;
          align-items: flex-start;
          justify-content: space-between;
          gap: 16px;
          padding: 16px;
          flex-wrap: wrap;
        }
        .hero-main {
          min-width: 0;
        }
        .device-name {
          font-size: 16px;
          font-weight: 500;
          margin: 0 0 2px;
          overflow: hidden;
          text-overflow: ellipsis;
        }
        .status {
          font-size: 28px;
          font-weight: 300;
          line-height: 1.15;
          margin: 4px 0 6px;
        }
        .subline {
          font-size: 13px;
          color: var(--hb-muted);
        }
        .hero-side {
          text-align: right;
          margin-left: auto;
        }
        .badges {
          display: flex;
          flex-wrap: wrap;
          gap: 6px;
          padding: 0 16px 12px;
        }
        .stage {
          padding: 0 16px 16px;
        }
        .actions {
          display: flex;
          gap: 8px;
          flex-wrap: wrap;
        }
        .metrics {
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(110px, 1fr));
          gap: 12px;
        }
        .link {
          background: none;
          border: none;
          padding: 0;
          min-height: 0;
          color: var(--hb-accent);
          font-size: 13px;
          cursor: pointer;
        }
        @media (max-width: 768px) {
          .hero {
            padding: 12px;
          }
          .status {
            font-size: 24px;
          }
          .hero-side {
            text-align: left;
            margin-left: 0;
          }
        }
      `,
      mobileBase,
    ];
  }

  // ── derived appliance status ────────────────────────────────────────────
  /**
   * Collapse power / fault / stage / running into the single status the user
   * cares about, the way the appliance's own display does.
   */
  get _status() {
    if (!this.isAvailable()) return "offline";
    const power = this.stateOf("power");
    if (power !== null && power !== "on") return "off";
    const fault = this.stateOf("fault");
    if (fault !== null && fault !== "none") return "fault";
    const stage = this.stateOf("wash_stage");
    if (stage === "finished") return "finished";
    if (this.isOn("running")) return "running";
    if (stage !== null && stage !== "idle") return "paused";
    return "standby";
  }

  get _stages() {
    const options = this.attrOf("wash_stage", "options");
    const ids = (
      Array.isArray(options)
        ? options.filter((id) => id !== "idle")
        : STAGE_ORDER
    ).slice();
    ids.sort((a, b) => {
      const ai = STAGE_ORDER.indexOf(a);
      const bi = STAGE_ORDER.indexOf(b);
      return (ai < 0 ? STAGE_ORDER.length : ai) - (bi < 0 ? STAGE_ORDER.length : bi);
    });
    return ids.map((id) => ({
      id,
      // Home Assistant translates the enum options for us.
      label: this.displayOf("wash_stage", this.device, id),
    }));
  }

  // ── sections ────────────────────────────────────────────────────────────
  _renderHero(device, t) {
    const status = this._status;
    const minutes = this.numberOf("remaining_time");
    const running = status === "running" || status === "paused";
    const program = this.hasRole("program") ? this.displayOf("program") : null;
    const temperature = this.hasRole("temperature")
      ? this.displayOf("temperature")
      : null;
    const subline = [program, running ? temperature : null]
      .filter(Boolean)
      .join(" · ");
    return html`
      <div class="hero">
        <div class="hero-main">
          <p class="device-name">${device.name}</p>
          <div class="status ${STATUS_TONE[status] || ""}">
            ${t(`state_${status}`)}
          </div>
          ${subline ? html`<div class="subline">${subline}</div>` : nothing}
        </div>
        ${running && minutes !== null
          ? html`<div class="hero-side">
              <holabrain-metric
                large
                .label=${t("time_remaining")}
                .value=${formatMinutes(minutes, t)}
              ></holabrain-metric>
            </div>`
          : nothing}
      </div>
    `;
  }

  _renderBadges(t) {
    const badges = [];
    if (this.hasRole("door")) {
      const open = this.isOn("door");
      badges.push({
        label: open ? t("door_open") : t("door_closed"),
        icon: open ? "mdi:door-open" : "mdi:door-closed",
        tone: open ? "warn" : "",
      });
    }
    if (this.isOn("salt_low")) {
      badges.push({
        label: this.labelOf("salt_low"),
        icon: "mdi:shaker-outline",
        tone: "warn",
      });
    }
    if (this.isOn("rinse_aid_low")) {
      badges.push({
        label: this.labelOf("rinse_aid_low"),
        icon: "mdi:cup-water",
        tone: "warn",
      });
    }
    if (!badges.length) return nothing;
    return html`
      <div class="badges">
        ${badges.map(
          (badge) => html`<holabrain-badge
            .label=${badge.label}
            .icon=${badge.icon}
            .tone=${badge.tone}
          ></holabrain-badge>`
        )}
      </div>
    `;
  }

  _renderFault(t) {
    const fault = this.stateOf("fault");
    if (fault === null || fault === "none") return nothing;
    return html`
      <div class="banner bad">
        <ha-icon icon="mdi:alert"></ha-icon>
        <span>${t("state_fault")}: ${this.displayOf("fault")}</span>
      </div>
    `;
  }

  _renderStage() {
    const status = this._status;
    if (status !== "running" && status !== "paused") return nothing;
    const stages = this._stages;
    if (!stages.length) return nothing;
    return html`
      <div class="stage">
        <holabrain-stage-bar
          .stages=${stages}
          .current=${this.stateOf("wash_stage") || ""}
          ?paused=${status === "paused"}
        ></holabrain-stage-bar>
      </div>
    `;
  }

  _renderActions(t) {
    if (!this.hasRole("power") && !this.hasRole("running")) return nothing;
    const powered = this.isOn("power");
    const running = this.isOn("running");
    const available = this.isAvailable();
    return html`
      <div class="section">
        <div class="actions">
          ${this.hasRole("power")
            ? html`<button
                class=${powered ? "" : "primary"}
                ?disabled=${!available}
                @click=${() => this.toggleRole("power")}
              >
                <ha-icon icon="mdi:power"></ha-icon>
                <span>${powered ? t("power_off") : t("power_on")}</span>
              </button>`
            : nothing}
          ${this.hasRole("running")
            ? html`<button
                class=${running ? "" : "primary"}
                ?disabled=${!available || !powered}
                @click=${() => this.toggleRole("running")}
              >
                <ha-icon
                  icon=${running ? "mdi:pause" : "mdi:play"}
                ></ha-icon>
                <span>${running ? t("pause") : t("start")}</span>
              </button>`
            : nothing}
        </div>
      </div>
    `;
  }

  _renderOptions(device, t) {
    const rows = ["auto_door_open"]
      .map((role) => this.entityIdFor(role))
      .filter(Boolean);
    if (!rows.length) return nothing;
    return html`
      <div class="section">
        <h3 class="section-title">${t("options")}</h3>
        ${rows.map((entityId) =>
          renderEntityRow(this.hass, entityId, device.name)
        )}
      </div>
    `;
  }

  _renderConsumables(device, t) {
    const rows = ["rinse_aid_level", "water_softener", "salt_low", "rinse_aid_low"]
      .map((role) => this.entityIdFor(role))
      .filter(Boolean);
    const metrics = ["rinse_aid_refills", "salt_refills"].filter((role) =>
      this.hasRole(role)
    );
    if (!rows.length && !metrics.length) return nothing;
    return html`
      <div class="section">
        <h3 class="section-title">${t("consumables")}</h3>
        ${rows.map((entityId) =>
          renderEntityRow(this.hass, entityId, device.name)
        )}
        ${metrics.length
          ? html`<div class="metrics" style="margin-top:12px">
              ${metrics.map(
                (role) => html`<holabrain-metric
                  .label=${this.labelOf(role)}
                  .value=${this.displayOf(role)}
                ></holabrain-metric>`
              )}
            </div>`
          : nothing}
      </div>
    `;
  }

  _renderStatistics(t) {
    // The cloud's own aggregation first: it is already in kWh and litres and survives
    // re-pairing. The appliance's raw lifetime counters follow, for anyone who enables
    // them — they are disabled by default and their scale is not stated anywhere.
    const roles = [
      "energy_month",
      "water_month",
      "energy_year",
      "water_year",
      "total_cycles",
      "total_water",
      "total_energy",
    ].filter((role) => this.hasRole(role));
    if (!roles.length) return nothing;
    return html`
      <div class="section">
        <h3 class="section-title">${t("statistics")}</h3>
        <div class="metrics">
          ${roles.map(
            (role) => html`<holabrain-metric
              .label=${this.labelOf(role)}
              .value=${this.displayOf(role)}
            ></holabrain-metric>`
          )}
        </div>
      </div>
    `;
  }

  _renderOther(device, t) {
    const others = remainingEntities(device, HANDLED_ROLES);
    if (!others.length) return nothing;
    return html`
      <div class="section">
        <button class="link" @click=${() => (this._showAll = !this._showAll)}>
          ${this._showAll ? "▾" : "▸"} ${t("show_all")} (${others.length})
        </button>
        ${this._showAll
          ? others.map((entityId) =>
              renderEntityRow(this.hass, entityId, device.name)
            )
          : nothing}
      </div>
    `;
  }

  render() {
    const device = this.device;
    if (!this.hass || !device) return nothing;
    const t = this.t;
    return html`
      <div class="surface" @holabrain-toast=${this._onToast}>
        ${this._renderFault(t)} ${this._renderHero(device, t)}
        ${this._renderBadges(t)} ${this._renderStage()}
        ${this._renderActions(t)} ${this._renderOptions(device, t)}
        ${this._renderConsumables(device, t)} ${this._renderStatistics(t)}
        ${this._renderOther(device, t)}
      </div>
    `;
  }
}

define("holabrain-dishwasher-card", HolabrainDishwasherCard);
