/**
 * <holabrain-device-card> — picks the right layout for a device.
 *
 * The one element hosts embed: it resolves the device's category and delegates
 * to the dedicated card when there is one, or to the generic card otherwise.
 * Adding a category later means adding one branch here plus the new card
 * module — no host has to change.
 *
 * Property: `deviceId` — device registry id; empty renders the first device.
 */

import { html, nothing, define } from "../lit-base.js";
import { HolabrainDeviceBase } from "./holabrain-device-base.js";
import { CATEGORY } from "../ha-registry.js";
import "./holabrain-dishwasher-card.js";
import "./holabrain-generic-card.js";

class HolabrainDeviceCard extends HolabrainDeviceBase {
  render() {
    const device = this.device;
    if (!this.hass || !device) return nothing;
    switch (device.category) {
      case CATEGORY.DISHWASHER:
        return html`<holabrain-dishwasher-card
          .hass=${this.hass}
          .deviceId=${device.id}
        ></holabrain-dishwasher-card>`;
      default:
        return html`<holabrain-generic-card
          .hass=${this.hass}
          .deviceId=${device.id}
        ></holabrain-generic-card>`;
    }
  }
}

define("holabrain-device-card", HolabrainDeviceCard);
