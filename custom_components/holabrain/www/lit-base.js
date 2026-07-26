/**
 * Self-contained Lit 3.x re-export.
 *
 * Everything in `www/` imports Lit from this module, never from the Home
 * Assistant frontend internals: relying on `ha-panel-lovelace`'s prototype to
 * hand out `LitElement`/`html`/`css` breaks on frontend versions that no longer
 * proxy those symbols, and it makes the Lovelace card unusable on installs that
 * happen to load it before the frontend hydrates them.
 *
 * `vendor/lit.js` is a static, self-contained esm.sh build (~16 KB) — the same
 * bundle used by the sberhome panel. It is not generated at runtime and has no
 * build step: copy it verbatim from
 * `ha-sberhome/custom_components/sberhome/www/vendor/lit.js`.
 */
export {
  LitElement,
  html,
  css,
  ReactiveElement,
  CSSResult,
  unsafeCSS,
  nothing,
  noChange,
  render,
  svg,
  mathml,
} from "./vendor/lit.js";

/**
 * Register a custom element only once.
 *
 * The panel and the Lovelace card load the very same component modules. When a
 * dashboard resource and the panel entry point resolve to different URLs (query
 * string, alternate mount path) the ES module graph is duplicated and a plain
 * `customElements.define()` would throw "name already used", taking the whole
 * card down. Guarding keeps whichever definition landed first.
 */
export function define(tagName, elementClass) {
  if (!customElements.get(tagName)) {
    customElements.define(tagName, elementClass);
  }
}
