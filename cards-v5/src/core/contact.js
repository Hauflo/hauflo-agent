// The alarm's words and the way to the house's service — shared by hauflo-alert, hauflo-hero, hauflo-leaks, hauflo-security.
// One source: the alarm's kind → its name, the room, what to do now (persona round 5) (the number itself lives in «Настройки», owner 03.11). Moved out of alert.js unchanged.
import { t } from "./i18n.js";
import { clientName } from "./names.js";
import { houseInfo } from "./house.js";
import { pushed } from "./alarms.js";

export const ICON = { moisture: "leak", smoke: "smoke", gas: "gas", carbon_monoxide: "co" };
const WORDS = ["moisture", "smoke", "gas", "carbon_monoxide", "heat", "cold", "problem", "safety", "tamper"];
// Another integration's sensor often keeps its zigbee2mqtt name («leak_bathroom»): it reads as its kind, with its HA room.
const TECH_NAME = /^(?:[a-z0-9]+(?:[_.-][a-z0-9]+)+|0x[0-9a-f]{6,})$/;

export const dcOf = (h, id) => h.states[id]?.attributes?.device_class || "";
/** The HA area id of an entity: its own, else its device's. */
export const areaId = (h, id) => {
  const e = h.entities?.[id];
  return e?.area_id || (e?.device_id && h.devices?.[e.device_id]?.area_id) || "";
};
export const areaName = (h, id) => h.areas?.[areaId(h, id)]?.name || "";
/** An alarm source's name: its own, or — a technical one — its kind (`tech`: say the room beside it). */
export function alarmName(h, id) {
  const n = clientName(h.states[id]?.attributes?.friendly_name, id);
  if (!TECH_NAME.test(n)) return { name: n, tech: false };
  const k = `alert.sensor.${dcOf(h, id)}`;
  return { name: t(k) === k ? n : t(k), tech: true };
}
export const roomOf = (c, h, id) => c.rooms?.[id] || areaName(h, id);
export const wordOf = (dc) => t(`alert.word.${WORDS.includes(dc) ? dc : "default"}`);
/** The house's service contact (one everywhere: the agent's «Кто обслуживает дом»), else the card's own copy. */
export function serviceOf(card) {
  const own = card.load("house", 300000, () => houseInfo(card.hass))?.service;
  if (own && (own.phone || own.email)) return own;
  const c = card.config.service;
  return c && (c.phone || c.email) ? c : null;
}
/** What to do now, by the kind of alarm (persona round 5: «опасно ли это и что делать прямо сейчас»). */
export function todo(c, h, id, svc, valve) {
  if (c.todo) return c.todo;
  const dc = dcOf(h, id);
  const told = pushed(id);
  if (["moisture", "smoke", "gas", "carbon_monoxide"].includes(dc)) {
    const what = dc === "moisture" && valve ? t("alert.todo.moisture_valve", { valve }) : t(`alert.todo.${dc}`);
    return told && dc === "moisture" ? `${what} ${t("alert.todo.pushed_ack")}` : what;
  }
  return [t("alert.todo.fault"), t(svc?.phone ? "alert.todo.fault_call" : "alert.todo.fault_tell"), told && t("alert.todo.pushed")].filter(Boolean).join(" ");
}
/** The call link's look (the same in every card that has it). */
export const CALL_CSS = `.hf5-ala>.hf5-pill{flex:1 1 calc(var(--hf-tile-min) * var(--hf5-k,1))}`;
/** The card's rim (alert, leaks, security): red for a critical alarm, yellow for the rest (K7: one even inset). */
export const FRAME_CSS = `.hf5-card.is-alarm{box-shadow:var(--hf-elev-1),inset 0 0 0 var(--hf-focus-w) var(--hf-state-alarm);background:radial-gradient(60% 50% at 20% 0%,var(--hf-state-alarm-glow),transparent 75%),var(--hf-surface)}
.hf5-card.is-attn{box-shadow:var(--hf-elev-1),inset 0 0 0 var(--hf-focus-w) var(--hf-state-warn)}`;
