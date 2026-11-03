// Domain adapter · one point of the building's dispatching (sensors spec §7, ISA-101): what it is now in ONE word and
// how bad: alarm (danger to people / the house — red) > fault (equipment — yellow, says «авария») > nolink (grey) >
// warn (attention: battery, open) > null (normal, quiet). Icons, values and classes come from the device adapter
// (kit.json → device); the fault-by-name tie-breaker from kit.json → status.
import { t, tv, num } from "../core/i18n.js";
import { CANON } from "../gen/canon.js";
import { deviceModel } from "./device.js";
import { climateModel, climateNow } from "./climate.js";

const lower = (s) => String(s || "").toLowerCase();
/** The order the card lists them in and the summary picks from. */
export const TIERS = ["alarm", "fault", "nolink", "warn"];
export const tierRank = (m) => (m.tier ? TIERS.indexOf(m.tier) : TIERS.length);

function binary(st) {
  const b = CANON.device.binary;
  const dc = st.attributes?.device_class;
  const on = st.state === "on";
  if (dc === "battery") return on ? { word: t("status.battery_low"), tier: "warn" } : { word: t("status.ok") };
  if (dc === "connectivity") return on ? { word: t("status.online") } : { word: t("common.offline"), tier: "nolink" };
  if (b.danger.includes(dc)) return on ? { word: lower(t(`alert.word.${dc}`)), tier: "alarm" } : { word: t("status.ok") };
  // «Ошибка тёплого пола» with no class is a fault, not «вкл» (old card, persona round 3)
  const byName = !dc && new RegExp(CANON.status.fault_name, "i").test(`${st.attributes?.friendly_name || ""} ${st.entity_id}`);
  const icon = byName ? { icon: b.icons.problem } : {};
  if (b.fault.includes(dc) || byName) return on ? { ...icon, word: lower(t("climate.fault")), tier: "fault" } : { ...icon, word: t("status.ok") };
  if (b.open.includes(dc)) return on ? { word: t("device.open"), tier: "warn" } : { word: t("device.closed") };
  // a point always says what it is — HA's meaning of on/off per class (motion «не обнаружено», power «нет питания»)
  if (b.presence.includes(dc)) return { word: on ? t("device.presence") : t("status.bin.clear") };
  const s = CANON.status;
  const k = on ? "on" : "off";
  if (s.words.includes(dc)) return { word: t(`status.bin.${dc}_${k}`), tier: s.warn_when[dc] === k ? "warn" : null };
  return { word: t(`status.bin.${k}`) };
}

/** One point → {id, name, icon, word, tier, on}. hint: the config's {name, icon}. */
export function statusModel(st, hint = {}, states = {}) {
  const m = deviceModel(st, hint);
  const out = { id: m.id, name: m.name, icon: m.icon, on: m.on, updated: m.updated };
  if (st.state === "unavailable") return { ...out, word: t("common.offline"), tier: "nolink", offline: true };
  if (st.state === "unknown") return { ...out, word: t("common.no_data"), tier: "nolink", offline: true };
  const dom = m.domain;
  const a = st.attributes || {};
  if (dom === "binary_sensor") return { ...out, ...binary(st) };
  if (dom === "sensor" && a.device_class === "battery" && !Number.isNaN(Number(st.state))) {
    const v = Number(st.state);
    return { ...out, word: t("status.battery", { v: num(v, 0) }), tier: v < CANON.device.battery_low_pct ? "warn" : null };
  }
  if (dom === "climate") {
    const c = climateModel(st);
    const now = climateNow(c, states);
    const cur = c.current != null ? ` · ${num(c.current, 1)}°` : "";
    return { ...out, word: lower(now === "mode" || !now ? tv("hvac", c.mode) : t(`climate.state.${now}`)) + cur, on: c.on };
  }
  if (dom === "water_heater") {
    const cur = a.current_temperature != null ? ` · ${num(a.current_temperature, 0)}°` : "";
    return { ...out, word: t(st.state === "off" ? "status.stopped" : "status.running") + cur };
  }
  if (dom === "valve") return { ...out, word: t(st.state === "open" ? "status.valve_open" : st.state === "closed" ? "status.valve_closed" : "status.ok") };
  // the device adapter's own words first (lock «не заперт», vacuum «ошибка», a sensor's value)
  if (m.tone === "alarm") return { ...out, word: m.value || lower(t("alert.word.default")), tier: "alarm" };
  if (m.tone === "warn") return { ...out, word: m.value, tier: "warn" };
  if (m.value) return { ...out, word: m.value };
  return { ...out, word: t(m.on ? "status.running" : "status.stopped") };
}
