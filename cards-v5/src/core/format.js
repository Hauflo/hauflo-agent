// Shared service · small formatters used by adapters (relative time, number step, nearest pick).
import { t } from "./i18n.js";
import { clamp } from "./dom.js";

export const ageText = (iso, now = Date.now()) => {
  const ms = now - new Date(iso).getTime();
  if (!iso || Number.isNaN(ms)) return "";
  const min = Math.max(0, Math.floor(ms / 60000));
  if (min < 1) return t("common.updated_now");
  if (min < 60) return t("common.updated_min", { n: min });
  if (min < 1440) return t("common.updated_h", { n: Math.floor(min / 60) });
  return t("common.updated_d", { n: Math.floor(min / 1440) });
};
/** Round to a device step (0.5 °C): 21.3 → 21.5, avoiding float tails. */
export const snap = (v, step, min, max) => {
  const s = step > 0 ? step : 1;
  const n = Math.round(v / s) * s;
  const dec = (String(s).split(".")[1] || "").length;
  return clamp(+n.toFixed(dec), min, max);
};
/** Linear 0–100 ↔ [lo, hi]. */
export const toPct = (v, lo, hi) => (hi > lo ? clamp(((v - lo) / (hi - lo)) * 100, 0, 100) : 0);
export const fromPct = (p, lo, hi) => lo + (clamp(p, 0, 100) / 100) * (hi - lo);
/** A moment on the house's clock — HA's time zone, as the agent's pushes and mails say it (integrator review 29.10:
 *  the card «с 03:46», the push «01:46»); the viewer's clock when HA has none. «HH:MM» today, «DD.MM HH:MM» before. */
export const houseClock = (hass, at, now = Date.now()) => {
  const parts = (d) => {
    const o = { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23" };
    try {
      return Object.fromEntries(new Intl.DateTimeFormat("en-GB", { ...o, timeZone: hass?.config?.time_zone || undefined }).formatToParts(d).map((x) => [x.type, x.value]));
    } catch {
      return Object.fromEntries(new Intl.DateTimeFormat("en-GB", o).formatToParts(d).map((x) => [x.type, x.value]));
    }
  };
  const d = new Date(at);
  if (Number.isNaN(d.getTime())) return "";
  const p = parts(d);
  const n = parts(new Date(now));
  const hm = `${p.hour}:${p.minute}`;
  return p.year === n.year && p.month === n.month && p.day === n.day ? hm : `${p.day}.${p.month} ${hm}`;
};
