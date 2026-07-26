/**
 * Read-only display primitives.
 *
 *   <holabrain-metric>  — a labelled value tile ("Temperature / 52 °C"), the
 *                         building block of the statistics and hero rows;
 *   <holabrain-badge>   — a compact pill for a boolean-ish fact (door open,
 *                         salt low, offline).
 *
 * Both are pure presentation: no `hass`, no service calls. Callers pass already
 * formatted strings, which keeps translation and unit handling in one place.
 */

import { LitElement, css, html, nothing, define } from "../lit-base.js";
import { surfaces } from "../styles.js";
import { mobileBase } from "../mobile-css.js";

class HolabrainMetric extends LitElement {
  static get properties() {
    return {
      label: { type: String },
      value: { type: String },
      icon: { type: String },
      /** "" | "ok" | "warn" | "bad" — tints the value. */
      tone: { type: String },
      /** Renders the value at hero size. */
      large: { type: Boolean },
    };
  }

  constructor() {
    super();
    this.label = "";
    this.value = "";
    this.icon = "";
    this.tone = "";
    this.large = false;
  }

  static get styles() {
    return [
      surfaces,
      css`
        :host {
          display: block;
          min-width: 0;
        }
        .tile {
          display: flex;
          flex-direction: column;
          gap: 2px;
          min-width: 0;
        }
        .head {
          display: flex;
          align-items: center;
          gap: 4px;
          font-size: 12px;
          color: var(--hb-muted);
          text-transform: uppercase;
          letter-spacing: 0.4px;
        }
        ha-icon {
          --mdc-icon-size: 16px;
          color: var(--hb-muted);
        }
        .value {
          font-size: 18px;
          font-weight: 500;
          font-variant-numeric: tabular-nums;
          overflow: hidden;
          text-overflow: ellipsis;
        }
        .value.large {
          font-size: 34px;
          font-weight: 300;
          line-height: 1.1;
        }
        @media (max-width: 768px) {
          .value.large {
            font-size: 28px;
          }
        }
      `,
      mobileBase,
    ];
  }

  render() {
    return html`
      <div class="tile">
        <div class="head">
          ${this.icon
            ? html`<ha-icon icon=${this.icon}></ha-icon>`
            : nothing}
          <span>${this.label}</span>
        </div>
        <div class="value ${this.large ? "large" : ""} ${this.tone}">
          ${this.value || "—"}
        </div>
      </div>
    `;
  }
}

class HolabrainBadge extends LitElement {
  static get properties() {
    return {
      label: { type: String },
      icon: { type: String },
      /** "" | "ok" | "warn" | "bad". */
      tone: { type: String },
    };
  }

  constructor() {
    super();
    this.label = "";
    this.icon = "";
    this.tone = "";
  }

  static get styles() {
    return [
      surfaces,
      css`
        :host {
          display: inline-block;
        }
        .badge {
          display: inline-flex;
          align-items: center;
          gap: 4px;
          padding: 4px 10px;
          border-radius: 999px;
          font-size: 12px;
          font-weight: 500;
          background: var(--secondary-background-color, #f1f1f1);
          color: var(--hb-muted);
          white-space: nowrap;
        }
        .badge.ok {
          background: color-mix(in srgb, var(--hb-ok) 16%, transparent);
          color: var(--hb-ok);
        }
        .badge.warn {
          background: color-mix(in srgb, var(--hb-warn) 18%, transparent);
          color: var(--hb-warn);
        }
        .badge.bad {
          background: color-mix(in srgb, var(--hb-bad) 18%, transparent);
          color: var(--hb-bad);
        }
        ha-icon {
          --mdc-icon-size: 14px;
        }
      `,
    ];
  }

  render() {
    return html`
      <span class="badge ${this.tone}">
        ${this.icon ? html`<ha-icon icon=${this.icon}></ha-icon>` : nothing}
        <span>${this.label}</span>
      </span>
    `;
  }
}

define("holabrain-metric", HolabrainMetric);
define("holabrain-badge", HolabrainBadge);
