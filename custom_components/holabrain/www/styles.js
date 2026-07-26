/**
 * Shared visual language for the panel and the Lovelace card.
 *
 * Only Home Assistant theme variables are used, so the UI follows the user's
 * theme (including dark mode) without any hard-coded palette. Components
 * compose these fragments with their own styles:
 *
 *   static get styles() { return [surfaces, controls, css`...`, mobileBase]; }
 */

import { css } from "./lit-base.js";

/** Card-like containers, section headings and status colouring. */
export const surfaces = css`
  :host {
    --hb-gap: 12px;
    --hb-radius: var(--ha-card-border-radius, 12px);
    --hb-accent: var(--primary-color, #03a9f4);
    --hb-ok: var(--success-color, #4caf50);
    --hb-warn: var(--warning-color, #ff9800);
    --hb-bad: var(--error-color, #f44336);
    --hb-muted: var(--secondary-text-color, #727272);
  }

  .surface {
    background: var(--ha-card-background, var(--card-background-color, #fff));
    border-radius: var(--hb-radius);
    border: var(--ha-card-border-width, 1px) solid
      var(--ha-card-border-color, var(--divider-color, #e0e0e0));
    box-shadow: var(--ha-card-box-shadow, none);
    box-sizing: border-box;
  }

  .section {
    padding: 12px 16px;
  }

  .section + .section {
    border-top: 1px solid var(--divider-color, #e0e0e0);
  }

  .section-title {
    margin: 0 0 8px;
    font-size: 12px;
    font-weight: 500;
    letter-spacing: 0.6px;
    text-transform: uppercase;
    color: var(--hb-muted);
  }

  .muted {
    color: var(--hb-muted);
  }

  .ok {
    color: var(--hb-ok);
  }
  .warn {
    color: var(--hb-warn);
  }
  .bad {
    color: var(--hb-bad);
  }

  .banner {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 10px 16px;
    font-size: 13px;
    color: #fff;
  }
  .banner.warn {
    background: var(--hb-warn);
    color: #fff;
  }
  .banner.bad {
    background: var(--hb-bad);
    color: #fff;
  }
`;

/** Buttons, chips and form controls used by the interactive rows. */
export const controls = css`
  button {
    font-family: inherit;
    font-size: 13px;
    font-weight: 500;
    color: var(--primary-text-color);
    background: var(--card-background-color, #fff);
    border: 1px solid var(--divider-color, #e0e0e0);
    border-radius: 8px;
    padding: 8px 14px;
    min-height: 36px;
    cursor: pointer;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 6px;
    transition: background 0.15s, border-color 0.15s, color 0.15s;
  }
  button:hover:not([disabled]) {
    background: var(--secondary-background-color, #f5f5f5);
  }
  button[disabled] {
    opacity: 0.45;
    cursor: not-allowed;
  }
  button.primary {
    background: var(--hb-accent);
    border-color: var(--hb-accent);
    color: var(--text-primary-color, #fff);
  }
  button.primary:hover:not([disabled]) {
    filter: brightness(1.08);
    background: var(--hb-accent);
  }
  button.active {
    border-color: var(--hb-accent);
    color: var(--hb-accent);
  }

  select {
    font-family: inherit;
    font-size: 13px;
    color: var(--primary-text-color);
    background: var(--card-background-color, #fff);
    border: 1px solid var(--divider-color, #e0e0e0);
    border-radius: 8px;
    padding: 8px 10px;
    min-height: 36px;
    max-width: 60%;
  }

  input[type="range"] {
    accent-color: var(--hb-accent);
    width: 100%;
  }

  .chips {
    display: flex;
    gap: 8px;
    overflow-x: auto;
    scrollbar-width: none;
  }
  .chips::-webkit-scrollbar {
    display: none;
  }
  .chip {
    flex-shrink: 0;
    border-radius: 999px;
    padding: 6px 14px;
  }
`;
