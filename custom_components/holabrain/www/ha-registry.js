/**
 * Turn the Home Assistant registries into a device view model.
 *
 * The UI is built strictly on top of regular Home Assistant entities: it reads
 * `hass.states` and drives devices through service calls. It never talks to the
 * cloud API — everything it can do, a user could do from a stock dashboard.
 *
 * Entities belonging to this integration are grouped per device and indexed by
 * *role*. A role is the entity's `translation_key` (which the integration's
 * declarative registry already assigns: `wash_stage`, `remaining_time`, `door`,
 * `power`, …), so the presentation layer can ask for a semantic slot instead of
 * guessing entity ids. Entities of a native platform (light, climate, …) have
 * no translation key and take their domain as the role.
 *
 * This module is deliberately free of Lit and of Home Assistant imports: it is
 * plain data plumbing and can be unit-tested on its own.
 */

/** `platform` value that marks an entity as belonging to this integration. */
export const INTEGRATION_PLATFORM = "holabrain";

/** Domains whose entity *is* the appliance rather than one of its features. */
export const NATIVE_DOMAINS = new Set([
  "light",
  "climate",
  "water_heater",
  "fan",
  "humidifier",
  "vacuum",
  "media_player",
]);

/**
 * Roles the dedicated cards know how to lay out. Also used as a fallback when
 * the frontend registry does not expose `translation_key` (older cores): the
 * entity id suffix is matched against this list.
 */
export const KNOWN_ROLES = [
  // account-level: these belong to the cloud connection, not to an appliance
  "exclusive_mode",
  "refresh_now",
  // consumption, aggregated by the cloud rather than read off the appliance
  "energy_month",
  "energy_year",
  "water_month",
  "water_year",
  // dishwasher
  "wash_stage",
  "program",
  "fault",
  "remaining_time",
  "temperature",
  "salt_refills",
  "rinse_aid_refills",
  "salt_low",
  "rinse_aid_low",
  "auto_door_open",
  "rinse_aid_level",
  "water_softener",
  "running",
  // oven
  "machine_status",
  "oven_fault",
  "working_temperature",
  "target_temperature",
  "probe_temperature",
  "cook_time",
  "cook_mode",
  "preheat",
  "preheating",
  "preheat_reached",
  "water_tank",
  "water_low",
  "water_change",
  "child_lock",
  // shared
  "heat_status",
  "door",
  "power",
];

export const CATEGORY = {
  DISHWASHER: "dishwasher",
  OVEN: "oven",
  AIR_CONDITIONER: "air_conditioner",
  WATER_HEATER: "water_heater",
  LIGHT: "light",
  GENERIC: "generic",
};

/** Resolve the semantic role of one entity. */
export function roleFor(entityId, entry) {
  if (entry?.translation_key) return entry.translation_key;
  const domain = entityId.split(".")[0];
  if (NATIVE_DOMAINS.has(domain)) return domain;
  const guessed = KNOWN_ROLES.find((role) => entityId.endsWith(`_${role}`));
  // Unmatched entities keep their id as role: unique, and never collides.
  return guessed || entityId;
}

/** Pick the card layout for a device from the roles it exposes. */
export function categoryFor(roles) {
  if ("wash_stage" in roles) return CATEGORY.DISHWASHER;
  if ("cook_mode" in roles || "machine_status" in roles) return CATEGORY.OVEN;
  if ("climate" in roles) return CATEGORY.AIR_CONDITIONER;
  if ("water_heater" in roles) return CATEGORY.WATER_HEATER;
  if ("light" in roles) return CATEGORY.LIGHT;
  return CATEGORY.GENERIC;
}

/**
 * Index the array-shaped payloads of `config/entity_registry/list` and
 * `config/device_registry/list` into the record shape `hass.entities` /
 * `hass.devices` use, so both sources feed `buildDevices()` unchanged.
 */
export function indexRegistryLists(entityList, deviceList) {
  const entities = {};
  for (const entry of entityList || []) {
    if (entry?.entity_id) entities[entry.entity_id] = entry;
  }
  const devices = {};
  for (const device of deviceList || []) {
    if (device?.id) devices[device.id] = device;
  }
  return { entities, devices };
}

function displayName(device, entityIds, hass) {
  const registryName = device?.name_by_user || device?.name;
  if (registryName) return registryName;
  const first = hass?.states?.[entityIds[0]];
  return first?.attributes?.friendly_name || entityIds[0] || "";
}

/**
 * Build the device list.
 *
 * @param hass      Home Assistant frontend object.
 * @param registry  Optional `{entities, devices}` override, used when the
 *                  running frontend does not expose `hass.entities`.
 */
export function buildDevices(hass, registry) {
  const entities = registry?.entities || hass?.entities || {};
  const devices = registry?.devices || hass?.devices || {};

  const grouped = new Map();
  for (const [entityId, entry] of Object.entries(entities)) {
    if (entry?.platform !== INTEGRATION_PLATFORM) continue;
    if (entry.disabled_by) continue;
    // Entities without a device still deserve a card of their own.
    const deviceId = entry.device_id || `entity:${entityId}`;
    if (!grouped.has(deviceId)) grouped.set(deviceId, []);
    grouped.get(deviceId).push({ entityId, entry });
  }

  const result = [];
  for (const [deviceId, items] of grouped) {
    items.sort((a, b) => a.entityId.localeCompare(b.entityId));
    const roles = {};
    for (const { entityId, entry } of items) {
      const role = roleFor(entityId, entry);
      // First entity wins: ids are sorted, so the mapping is deterministic.
      if (!(role in roles)) roles[role] = entityId;
    }
    const device = devices[deviceId] || {};
    const entityIds = items.map((item) => item.entityId);
    result.push({
      id: deviceId,
      name: displayName(device, entityIds, hass),
      model: device.model || "",
      manufacturer: device.manufacturer || "",
      areaId: device.area_id || null,
      category: categoryFor(roles),
      roles,
      entityIds,
      visibleEntityIds: items
        .filter((item) => !item.entry?.hidden && !item.entry?.hidden_by)
        .map((item) => item.entityId),
    });
  }
  result.sort((a, b) => a.name.localeCompare(b.name));
  return result;
}

/** A device counts as reachable while at least one of its entities has a state. */
export function deviceAvailable(hass, device) {
  if (!device || !hass?.states) return false;
  return device.entityIds.some(
    (entityId) => hass.states[entityId]?.state !== "unavailable"
  );
}

const EMPTY_STATES = new Set(["unavailable", "unknown", "none", ""]);

/** Raw state string, or `null` when the entity is missing or has no value. */
export function rawState(hass, entityId) {
  const stateObj = entityId ? hass?.states?.[entityId] : null;
  if (!stateObj) return null;
  return EMPTY_STATES.has(stateObj.state) ? null : stateObj.state;
}

/** Numeric state, or `null` when it is missing or not a number. */
export function numericState(hass, entityId) {
  const value = rawState(hass, entityId);
  if (value === null) return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

/**
 * Display string for an entity state, translated and unit-formatted by Home
 * Assistant when the running frontend offers `formatEntityState`.
 *
 * @param overrideState Format a state the entity does not currently hold —
 *                      used to label every step of the stage bar.
 */
export function formatState(hass, entityId, overrideState) {
  const stateObj = entityId ? hass?.states?.[entityId] : null;
  if (!stateObj) return "—";
  if (typeof hass.formatEntityState === "function") {
    try {
      return overrideState === undefined
        ? hass.formatEntityState(stateObj)
        : hass.formatEntityState(stateObj, overrideState);
    } catch {
      // Fall through to the manual formatting below.
    }
  }
  const value = overrideState === undefined ? stateObj.state : overrideState;
  const unit = stateObj.attributes?.unit_of_measurement;
  return unit ? `${value} ${unit}` : String(value);
}

/** Entity name with the device prefix stripped, as dashboards render it. */
export function entityLabel(hass, entityId, deviceName) {
  const stateObj = entityId ? hass?.states?.[entityId] : null;
  let name = stateObj?.attributes?.friendly_name || entityId || "";
  if (deviceName && name.startsWith(`${deviceName} `)) {
    name = name.slice(deviceName.length + 1);
  }
  return name;
}

/** Entities of a device that no dedicated section already renders. */
export function remainingEntities(device, usedRoles) {
  if (!device) return [];
  const used = new Set(
    usedRoles.map((role) => device.roles[role]).filter(Boolean)
  );
  return device.visibleEntityIds.filter((entityId) => !used.has(entityId));
}
