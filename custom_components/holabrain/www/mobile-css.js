/**
 * Shared mobile baseline for every component in the panel and the card.
 *
 * Usage:
 *
 *   import { mobileBase } from "../mobile-css.js";
 *   static get styles() {
 *     return [css`...own styles...`, mobileBase];
 *   }
 *
 * It complements (never replaces) a component's own media queries. Selectors are
 * scoped through `:host`, so the rules cross each LitElement's shadow boundary
 * without any global stylesheet.
 *
 * Breakpoint 768px covers phones and portrait tablets.
 */

import { css } from "./lit-base.js";

export const mobileBase = css`
  @media (max-width: 768px) {
    .row,
    .rows,
    .actions,
    .controls,
    .chips,
    .metrics,
    .header {
      flex-wrap: wrap;
      gap: 8px;
    }

    input,
    select,
    textarea {
      font-size: 13px;
    }

    /* Keep a 44px touch target on every interactive control. */
    button,
    select {
      min-height: 44px;
    }
  }
`;
