/**
 * <holabrain-stage-bar> — linear progress through the stages of a programme.
 *
 * Mirrors how the companion app presents a running cycle: the stages of the
 * programme laid out left to right, everything before the current one filled
 * in, the current one highlighted. Purely presentational — the caller passes
 * the stage ids, their already-translated labels and the active id.
 *
 *   <holabrain-stage-bar
 *     .stages=${[{id: "pre_wash", label: "Prewash"}, …]}
 *     current="rinse"
 *     ?paused=${true}>
 *   </holabrain-stage-bar>
 */

import { LitElement, css, html, nothing, define } from "../lit-base.js";
import { surfaces } from "../styles.js";
import { mobileBase } from "../mobile-css.js";

class HolabrainStageBar extends LitElement {
  static get properties() {
    return {
      stages: { type: Array },
      current: { type: String },
      paused: { type: Boolean },
    };
  }

  constructor() {
    super();
    this.stages = [];
    this.current = "";
    this.paused = false;
  }

  static get styles() {
    return [
      surfaces,
      css`
        :host {
          display: block;
        }
        .track {
          display: flex;
          align-items: flex-start;
          gap: 4px;
        }
        .step {
          flex: 1 1 0;
          min-width: 0;
          display: flex;
          flex-direction: column;
          align-items: center;
          gap: 6px;
        }
        .bar {
          width: 100%;
          height: 4px;
          border-radius: 2px;
          background: var(--divider-color, #e0e0e0);
        }
        .step.done .bar,
        .step.active .bar {
          background: var(--hb-accent);
        }
        .step.active .bar {
          animation: pulse 1.6s ease-in-out infinite;
        }
        .step.active.paused .bar {
          animation: none;
          opacity: 0.6;
        }
        .label {
          font-size: 11px;
          color: var(--hb-muted);
          text-align: center;
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
          max-width: 100%;
        }
        .step.active .label {
          color: var(--hb-accent);
          font-weight: 500;
        }
        @keyframes pulse {
          0%,
          100% {
            opacity: 1;
          }
          50% {
            opacity: 0.45;
          }
        }
        @media (prefers-reduced-motion: reduce) {
          .step.active .bar {
            animation: none;
          }
        }
      `,
      mobileBase,
    ];
  }

  render() {
    const stages = this.stages || [];
    if (!stages.length) return nothing;
    const activeIndex = stages.findIndex((stage) => stage.id === this.current);
    return html`
      <div class="track">
        ${stages.map((stage, index) => {
          const done = activeIndex >= 0 && index < activeIndex;
          const active = index === activeIndex;
          return html`
            <div
              class="step ${done ? "done" : ""} ${active ? "active" : ""} ${this
                .paused
                ? "paused"
                : ""}"
            >
              <div class="bar"></div>
              <div class="label">${stage.label}</div>
            </div>
          `;
        })}
      </div>
    `;
  }
}

define("holabrain-stage-bar", HolabrainStageBar);
