/**
 * Static UI labels for the panel and the card.
 *
 * Anything that describes an *entity* (its name, its state, its unit) is
 * formatted by Home Assistant itself and is therefore already translated. This
 * table only covers chrome that has no entity behind it — section headings,
 * empty states, derived appliance status.
 *
 * Add a language by adding a key here; unknown languages fall back to English.
 */

const EN = {
  panel_title: "HolaBrain",
  devices: "Devices",
  entities: "Entities",
  no_devices: "No HolaBrain devices",
  no_devices_hint:
    "Add the HolaBrain integration, or wait for the first cloud refresh to finish.",
  unavailable: "Unavailable",
  offline: "Offline",
  loading: "Loading…",

  status: "Status",
  program: "Programme",
  time_remaining: "Time remaining",
  stage: "Stage",
  temperature: "Temperature",
  controls: "Controls",
  options: "Options",
  consumables: "Consumables",
  statistics: "Statistics",
  diagnostics: "Diagnostics",

  door_open: "Door open",
  door_closed: "Door closed",
  start: "Start",
  pause: "Pause",
  resume: "Resume",
  power_on: "Turn on",
  power_off: "Turn off",

  state_off: "Off",
  state_standby: "Standby",
  state_running: "Washing",
  state_paused: "Paused",
  state_finished: "Programme end",
  state_fault: "Fault",
  state_delay: "Delayed start",

  stage_pre_wash: "Prewash",
  stage_main_wash: "Wash",
  stage_rinse: "Rinse",
  stage_drying: "Drying",
  stage_finished: "End",

  hours_short: "h",
  minutes_short: "min",
  more_info: "Details",
  show_all: "All entities",
  error_prefix: "Error",

  scan: "Scan for appliances",
  scan_warning:
    "This will sign you out of the HolaBrain mobile app. The cloud allows only one active " +
    "session per account, so scanning ends the app's session and you will have to sign in " +
    "there again. Everyday monitoring never does this.",
  scan_confirm: "Scan anyway",
  scan_cancel: "Cancel",
  scanning: "Scanning…",
  scan_done: "Scan finished",
  scan_failed: "Scan failed",
};

const RU = {
  panel_title: "HolaBrain",
  devices: "Устройства",
  entities: "Сущности",
  no_devices: "Устройства HolaBrain не найдены",
  no_devices_hint:
    "Добавьте интеграцию HolaBrain или дождитесь первого обновления из облака.",
  unavailable: "Недоступно",
  offline: "Не в сети",
  loading: "Загрузка…",

  status: "Статус",
  program: "Программа",
  time_remaining: "Осталось",
  stage: "Стадия",
  temperature: "Температура",
  controls: "Управление",
  options: "Опции",
  consumables: "Расходники",
  statistics: "Статистика",
  diagnostics: "Диагностика",

  door_open: "Дверца открыта",
  door_closed: "Дверца закрыта",
  start: "Старт",
  pause: "Пауза",
  resume: "Продолжить",
  power_on: "Включить",
  power_off: "Выключить",

  state_off: "Выключена",
  state_standby: "Ожидание",
  state_running: "Мойка",
  state_paused: "Пауза",
  state_finished: "Программа завершена",
  state_fault: "Ошибка",
  state_delay: "Отложенный старт",

  stage_pre_wash: "Замачивание",
  stage_main_wash: "Мойка",
  stage_rinse: "Ополаскивание",
  stage_drying: "Сушка",
  stage_finished: "Готово",

  hours_short: "ч",
  minutes_short: "мин",
  more_info: "Подробнее",
  show_all: "Все сущности",
  error_prefix: "Ошибка",

  scan: "Поиск устройств",
  scan_warning:
    "Вы выйдете из мобильного приложения HolaBrain. Облако допускает только один активный " +
    "сеанс на аккаунт, поэтому поиск завершит сеанс приложения и там придётся войти заново. " +
    "Обычный мониторинг этого не делает.",
  scan_confirm: "Всё равно искать",
  scan_cancel: "Отмена",
  scanning: "Идёт поиск…",
  scan_done: "Поиск завершён",
  scan_failed: "Поиск не удался",
};

const TABLES = { en: EN, ru: RU };

/** Build a `t(key)` lookup bound to the user's Home Assistant language. */
export function translator(hass) {
  const language = (hass?.locale?.language || hass?.language || "en")
    .slice(0, 2)
    .toLowerCase();
  const table = TABLES[language] || EN;
  return (key) => table[key] ?? EN[key] ?? key;
}

/** Render a minute count as "1 h 25 min" / "45 min" in the active language. */
export function formatMinutes(minutes, t) {
  if (minutes === null || minutes === undefined || Number.isNaN(minutes)) {
    return "—";
  }
  const total = Math.max(0, Math.round(minutes));
  const hours = Math.floor(total / 60);
  const rest = total % 60;
  if (!hours) return `${rest} ${t("minutes_short")}`;
  return `${hours} ${t("hours_short")} ${String(rest).padStart(2, "0")} ${t(
    "minutes_short"
  )}`;
}
