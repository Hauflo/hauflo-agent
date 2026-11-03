// Domain adapter · any other device (house spec v5 §2–3): switch, fan, valve, boiler, lock, vacuum, button, sensors…
// → ONE tile model: glyph, lit or not, the value (a plain switch: the tile says «вкл/выкл», v1 canon), the trouble tone, and the tap's safe call.
// The data (glyphs, taps, class A, sensor classes) is canon: kit.json → device.
import { t, num } from "../core/i18n.js";
import { CANON } from "../gen/canon.js";
import { base, unitOf } from "./common.js";

const D = () => CANON.device;
const lower = (s) => String(s || "").toLowerCase();

/** The glyph: the config's purpose icon when the kit has it, else the sensor class's, else the domain's. */
function iconOf(st, dom, hint) {
  const c = D();
  const own = hint?.icon && c.icons[hint.icon];
  if (own) return own;
  const dc = st.attributes?.device_class;
  if (dom === "binary_sensor" && c.binary.icons[dc]) return c.binary.icons[dc];
  if (dom === "sensor" && c.sensor_icons[dc]) return c.sensor_icons[dc];
  return c.domains[dom]?.icon || c.default_icon;
}

/** A number with its unit, as the house writes it: «84 Вт», «21,5 °C», «45 %». */
function valueOf(st) {
  const n = Number(st.state);
  if (st.state === "" || Number.isNaN(n)) return st.state;
  const unit = unitOf(st);
  return `${num(n, n % 1 ? 1 : 0)}${unit ? " " + unit : ""}`;
}

/** binary_sensor: the word and the tone by its class (danger red, fault yellow); quiet when normal. */
function binary(st) {
  const b = D().binary;
  const dc = st.attributes?.device_class;
  const on = st.state === "on";
  if (b.danger.includes(dc)) return on ? { word: lower(t(`alert.word.${dc}`)), tone: "alarm" } : { word: "" };
  if (b.fault.includes(dc)) return on ? { word: lower(t(`alert.word.${dc}`)), tone: "warn" } : { word: "" };
  if (b.open.includes(dc)) return { word: t(on ? "device.open" : "device.closed"), lit: on };
  if (b.presence.includes(dc)) return on ? { word: t("device.presence"), lit: true } : { word: "" };
  return { word: on ? t("device.on_word") : "", lit: on };
}

/** The tile's model. hint: the config's {name, icon}. */
export function deviceModel(st, hint = {}) {
  const dom = st.entity_id.split(".")[0];
  const c = D();
  const d = c.domains[dom] || { read: true };
  const a = st.attributes || {};
  // stateless (button, scene, script…): «unknown» = never pressed, only «unavailable» is no link
  const m = d.action ? { ...base(st, dom), offline: st.state === "unavailable" } : base(st, dom);
  let on = d.on ? d.on.includes(st.state) : d.off ? !d.off.includes(st.state) : st.state === "on";
  let value = "";
  let tone = null;
  if (dom === "binary_sensor") {
    const b = binary(st);
    value = b.word;
    tone = b.tone || null;
    on = !!b.lit;
  } else if (dom === "sensor" || dom === "number" || dom === "input_number") {
    value = valueOf(st);
    const n = Number(st.state);
    if (a.device_class === "battery" && !Number.isNaN(n) && n < c.battery_low_pct) tone = "warn";
    on = false;
  } else if (["select", "input_select", "text", "input_text"].includes(dom)) {
    value = st.state;
  } else if (dom === "device_tracker" || dom === "person") {
    value = t(on ? "device.home" : "device.away");
  } else if (dom === "update") {
    value = st.state === "on" ? t("device.update") : "";
    on = st.state === "on";
  } else if (dom === "lock") {
    if (st.state === "jammed") (value = t("device.jammed")), (tone = "alarm");
    else if (on) (value = t("device.unlocked")), (tone = "warn");
  } else if ((dom === "vacuum" || dom === "lawn_mower") && st.state === "error") {
    value = t("device.error");
    tone = "alarm";
    on = false;
  } else if (dom === "siren" && on) {
    tone = "alarm";
  } else if (d.value && on && a[d.value] != null && !Number.isNaN(+a[d.value])) {
    value = `${num(+a[d.value], 1)}${d.unit === "°" ? "°" : " " + d.unit}`;
  }
  return {
    ...m,
    name: hint.name || m.name,
    kind: "device",
    icon: iconOf(st, dom, hint),
    role: "state-on",
    on: !m.offline && on,
    value: m.offline ? "" : value,
    tone: m.offline ? null : tone,
    read: !!d.read,
    action: !!d.action,
    guard: !!d.guard || hint.important === true,
    tap: d.tap || null,
    count: d.count !== false,
  };
}

/** The tap's call, or null (the remote): class A, read-only and no-tap devices never act on a tap. */
export function deviceCall(m) {
  if (!m.tap || m.guard || m.read) return null;
  const tp = D().taps[m.tap];
  const service = tp.service || (m.on ? tp.on : tp.off);
  return service ? { domain: m.domain, service, data: { entity_id: m.id } } : null;
}

/** Is it something that is «вкл» or not (counted in «N из M вкл»; a lock or a siren is never «вкл»)? */
export const switchable = (m) => !m.read && !m.action && m.count;
