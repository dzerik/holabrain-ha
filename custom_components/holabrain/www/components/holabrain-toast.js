/**
 * <holabrain-toast> — transient message, shown by the host after a failed
 * service call. Hosts render one instance and call `show(message, type)` from
 * their `holabrain-toast` event relay.
 */

import { LitElement, css, html, define } from "../lit-base.js";

class HolabrainToast extends LitElement {
  static get properties() {
    return {
      _message: { type: String, state: true },
      _type: { type: String, state: true },
      _visible: { type: Boolean, state: true },
    };
  }

  constructor() {
    super();
    this._message = "";
    this._type = "info";
    this._visible = false;
    this._timer = null;
  }

  show(message, type = "info", duration = 4000) {
    if (this._timer) clearTimeout(this._timer);
    this._message = message;
    this._type = type;
    this._visible = true;
    this._timer = setTimeout(() => {
      this._visible = false;
      this._timer = null;
    }, duration);
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    if (this._timer) {
      clearTimeout(this._timer);
      this._timer = null;
    }
  }

  static get styles() {
    return css`
      :host {
        position: fixed;
        bottom: 24px;
        right: 24px;
        z-index: 10000;
        pointer-events: none;
      }
      .toast {
        display: inline-flex;
        align-items: center;
        gap: 8px;
        padding: 12px 20px;
        border-radius: 8px;
        font-size: 14px;
        font-weight: 500;
        color: #fff;
        max-width: min(80vw, 420px);
        box-shadow: 0 4px 16px rgba(0, 0, 0, 0.25);
        opacity: 0;
        transform: translateY(16px);
        transition: opacity 0.25s, transform 0.25s;
      }
      .toast.visible {
        opacity: 1;
        transform: translateY(0);
      }
      .toast.info {
        background: var(--primary-color, #03a9f4);
      }
      .toast.success {
        background: var(--success-color, #4caf50);
      }
      .toast.error {
        background: var(--error-color, #f44336);
      }
      @media (max-width: 768px) {
        :host {
          right: 12px;
          left: 12px;
          bottom: 12px;
        }
      }
    `;
  }

  render() {
    return html`
      <div class="toast ${this._type} ${this._visible ? "visible" : ""}">
        ${this._message}
      </div>
    `;
  }
}

define("holabrain-toast", HolabrainToast);
