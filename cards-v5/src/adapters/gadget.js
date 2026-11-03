// Domain adapter · the big remote of "everything else" (owner 03.11): switch, fan, humidifier, siren, valve, lock,
// water heater, vacuum, sensor, binary sensor → ONE model + controls for the same remote engine. The tile model (glyph,
// value, tone) is the device adapter's; here only what the remote adds: the big ⏻, speed / target, class A hold,
// consumption / battery of the SAME HA device, the sensor's 24 h sparkline. Data (kinds, guard pairs, bits) = kit.json → remote_device.
import { t, tv, num } from "../core/i18n.js";
import { CANON } from "../gen/canon.js";
import { fromPct, toPct, snap, houseClock } from "../core/format.js";
import { base, unitOf } from "./common.js";
import { deviceModel } from "./device.js";
import { statusModel } from "./status.js";

const RD = () => CANON.remoteDevice;
const lower = (s) => String(s || "").toLowerCase();
const TIER_ROLE = { alarm: "state-alarm", fault: "state-warn", warn: "state-warn", nolink: "state-offline" };
const TONE_ROLE = { alarm: "state-alarm", warn: "state-warn" };

const sinceOf = (st, hass) => houseClock(hass, st?.last_changed || st?.last_updated);

export function gadgetModel(st) {
  const dom = st.entity_id.split(".")[0];
  const rd = RD();
  const rk = rd.kinds[dom] || "switch";
  const F = rd.features[dom] || {};
  const dm = deviceModel(st);
  const a = st.attributes || {};
  const f = Number(a.supported_features) || 0;
  const m = base(st, dom);
  const guard = !!rd.guard[dom];
  const sensor = rk === "sensor" || rk === "binary";
  const n = Number(st.state);
  const numeric = rk === "sensor" && st.state !== "" && !Number.isNaN(n);
  const sm = rk === "binary" ? statusModel(st) : null;
  let role = dm.on ? "state-on" : null;
  if (dom === "lock") role = dm.on ? "state-warn" : st.state === "locked" ? "state-secure" : null;
  if (sensor) role = TONE_ROLE[dm.tone] || (sm && TIER_ROLE[sm.tier]) || null;
  const lim =
    rk === "heater"
      ? { min: a.min_temp ?? rd.heater_range.min, max: a.max_temp ?? rd.heater_range.max }
      : rk === "humidifier"
        ? { min: a.min_humidity ?? rd.humidity_range.min, max: a.max_humidity ?? rd.humidity_range.max }
        : null;
  const ops = (a.operation_list || []).filter((x) => x !== "off");
  const hmodes = a.available_modes || [];
  return {
    ...m,
    kind: "device",
    rk,
    over: t(`rdev.over.${guard ? dom : rk}`),
    icon: dom === "lock" && dm.on ? "lockOpen" : dm.icon,
    role: m.offline ? null : role,
    on: !m.offline && dm.on,
    st: st.state,
    word: sm?.word || "",
    unit: unitOf(st),
    n: numeric ? n : null,
    pct: a.percentage ?? null,
    hum: a.humidity ?? a.target_humidity ?? null,
    target: a.temperature ?? null,
    current: a.current_temperature ?? null,
    step: a.target_temp_step || rd.heater_step,
    lim,
    ops,
    op: a.operation_mode ?? null,
    presets: a.preset_modes || [],
    preset: a.preset_mode ?? null,
    hmodes,
    hmode: a.mode ?? null,
    position: a.current_position ?? null,
    f,
    caps: {
      bigpower: ["switch", "fan", "humidifier"].includes(rk),
      speed: rk === "fan" && !!(f & F.speed),
      preset: rk === "fan" && !!(f & F.preset) && !!a.preset_modes?.length,
      humidity: rk === "humidifier",
      hmodes: rk === "humidifier" && !!(f & F.modes) && hmodes.length > 1,
      target: rk === "heater" && a.temperature != null,
      omode: rk === "heater" && !!(f & F.mode) && ops.length > 1,
      guard: guard && (dom !== "water_heater" || !!(f & F.power)),
      vac: rk === "vacuum",
      sensor,
      graph: sensor && (numeric || rk === "binary"),
    },
  };
}

/** The status word of one device: «включено · 40 %», «заперт», «открыт», «нагрев»… */
function statusText(m) {
  if (m.st === "unavailable") return t("common.offline");
  if (m.st === "unknown") return t("common.no_data");
  switch (m.rk) {
    case "switch":
      return t(m.on ? "rdev.on" : "rdev.off");
    case "fan":
      return m.on ? (m.pct != null && m.caps.speed ? `${t("rdev.on")} · ${num(m.pct, 0)} %` : t("rdev.on")) : t("rdev.off");
    case "humidifier":
      return m.on ? (m.hum != null ? `${t("rdev.on")} · ${num(m.hum, 0)} %` : t("rdev.on")) : t("rdev.off");
    case "heater":
      return (m.on ? tv("wh_mode", m.st) : t("rdev.off")) + (m.current != null ? ` · ${num(m.current, 0)}°` : "");
    case "vacuum":
      return tv("vacuum_state", m.st);
    case "guard":
      if (m.domain === "lock") return tv("lock_state", m.st);
      if (m.domain === "valve") return tv("valve_state", m.st) + (m.position != null && m.position > 0 && m.position < 100 ? ` · ${num(m.position, 0)} %` : "");
      return t(m.on ? "rdev.siren_on" : "rdev.siren_off");
    case "binary":
      return m.word;
  }
  return "";
}

/** Sibling sensors of the SAME HA device (consumption, battery, link): the entity registry's device_id is the join. */
export function siblings(hass, id) {
  const reg = hass?.entities;
  const dev = reg?.[id]?.device_id;
  if (!dev) return [];
  return Object.keys(reg).filter((k) => k !== id && k.startsWith("sensor.") && reg[k].device_id === dev && hass.states?.[k]);
}

/** {power, energy, battery, link} → each {text, n} or absent. */
export function gadgetExtras(hass, id) {
  const S = RD().siblings;
  const out = {};
  const re = new RegExp(S.link_id, "i");
  for (const k of siblings(hass, id)) {
    const st = hass.states[k];
    const n = Number(st.state);
    if (st.state === "" || Number.isNaN(n)) continue;
    const dc = st.attributes?.device_class;
    const unit = unitOf(st);
    const text = `${num(n, n % 1 ? 1 : 0)}${unit ? " " + unit : ""}`;
    const key = ["power", "energy", "battery"].find((g) => S[g].includes(dc)) || (S.link.includes(dc) || re.test(k) ? "link" : null);
    if (key && !out[key]) out[key] = { text, n };
  }
  return out;
}

export function gadgetControls(m, ctx = {}) {
  const c = {};
  const dis = m.offline;
  const x = ctx.extras || {};
  const since = ctx.st && !dis ? t("rdev.since", { t: sinceOf(ctx.st, ctx.hass) }) : "";
  const text = m.rk === "sensor" ? since : m.rk === "binary" ? [m.word, since].filter(Boolean).join(" · ") : statusText(m);
  c.status = { kind: "status", text: text || statusText(m), dot: m.on || m.rk === "sensor" || (m.caps.guard && m.role) ? "role" : null, role: m.role };
  if (m.rk === "sensor" && !dis) c.value = { kind: "setpoint", readonly: true, value: m.n != null ? num(m.n, m.n % 1 ? 1 : 0) : m.st, unit: m.n != null ? m.unit : "", side: "", role: m.role };
  if (m.caps.bigpower) c.power = { kind: "bigpower", on: m.on, act: "power", disabled: dis, role: m.role };
  if (m.caps.speed) {
    const p = ctx.opt?.pct ?? m.pct ?? 0;
    c.speed = { kind: "slider", value: m.on ? p : 0, label: t("rdev.speed"), act: "speed", role: m.role, off: !m.on, disabled: dis, valuetext: `${num(p, 0)}%` };
  }
  if (m.caps.preset) c.preset = { kind: "seg", act: "preset", sel: m.preset, role: m.role, items: m.presets.map((v) => ({ v, label: tv("preset", v) })) };
  if (m.caps.humidity) {
    const h = ctx.opt?.hum ?? m.hum;
    c.humidity = { kind: "slider", value: h == null ? 0 : toPct(h, m.lim.min, m.lim.max), label: t("rdev.target_humidity"), act: "humidity", role: m.role, off: !m.on, disabled: dis, valuetext: h == null ? "" : `${num(h, 0)}%` };
  }
  if (m.caps.hmodes) c.hmodes = { kind: "seg", act: "hmode", sel: m.hmode, role: m.role, items: m.hmodes.map((v) => ({ v, label: tv("hum_mode", v) })) };
  if (m.caps.target) {
    const tg = ctx.opt?.target ?? m.target;
    c.target = { kind: "setpoint", value: m.on ? num(tg, 1) : "—", unit: "°", side: m.current != null ? t("climate.now", { v: `${num(m.current, 1)}°` }) : "", act: "step", role: m.role, off: !m.on, disabled: dis || !m.on, up: t("common.up"), down: t("common.down") };
  }
  if (m.caps.omode) c.omode = { kind: "seg", act: "omode", sel: m.op, role: m.role, items: m.ops.map((v) => ({ v, label: tv("wh_mode", v) })) };
  if (m.caps.guard) {
    const g = RD().guard[m.domain];
    // class A: what secures is a tap, what releases (unlock, open, sound, heat) is only a 1 s hold
    // the pair shares the width; the button of the state the device is in now is lit in its colour
    const safe = !dis && !m.on && m.st === g.secure.state;
    c.actions = {
      kind: "buttons",
      cls: "hf5-pair",
      items: [
        { icon: g.secure.icon, text: t(g.secure.text), act: "secure", on: safe, role: safe ? m.role : null, disabled: dis },
        { icon: g.release.icon, text: t(g.release.text), act: "release", hold: true, on: m.on, role: m.on ? m.role : null, disabled: dis },
      ],
      note: t("common.hold_hint", { action: lower(t(g.release.text)) }),
    };
  }
  if (m.caps.vac) {
    const F = RD().features.vacuum;
    const active = RD().vacuum_active.includes(m.st);
    const items = [];
    if (!active || !(m.f & F.pause)) items.push({ icon: "power", text: t("rdev.vac_start"), act: "start", disabled: dis, on: active });
    if (active && m.f & F.pause) items.push({ icon: "stop", text: t("rdev.vac_pause"), act: "pause", disabled: dis });
    if (m.f & F.home) items.push({ icon: "home", text: t("rdev.vac_home"), act: "home", disabled: dis || m.st === "docked" });
    c.vacuum = { kind: "buttons", items };
  }
  if (m.caps.graph) {
    const hrs = RD().history_hours;
    c.graph = { kind: "graph", series: ctx.series || [], axis: ctx.axis, role: m.role || "state-on", empty: t("rdev.no_history", { n: hrs }), label: t("rdev.history", { n: hrs }) };
  }
  const use = [];
  if (x.power) use.push({ label: t("rdev.power"), value: x.power.text });
  if (x.energy) use.push({ label: t("rdev.energy"), value: x.energy.text });
  if (use.length && !m.caps.sensor) c.usage = { kind: "facts", items: use };
  if (ctx.st) {
    const items = [{ label: t("rdev.changed"), value: sinceOf(ctx.st, ctx.hass) }];
    if (x.battery) items.push({ label: t("rdev.battery"), value: x.battery.text, warn: x.battery.n < CANON.device.battery_low_pct });
    if (x.link) items.push({ label: t("rdev.link"), value: x.link.text });
    c.facts = { kind: "facts", items };
  }
  return c;
}

export function gadgetAct(m, name, arg, ctx = {}) {
  const id = m.id;
  const svc = (domain, service, data = {}) => [{ domain, service, data: { entity_id: id, ...data } }];
  const dom = m.domain;
  switch (name) {
    case "power":
      return { calls: svc(dom, m.on ? "turn_off" : "turn_on") };
    case "speed": {
      const p = Math.round(+arg);
      return p <= 0 ? { calls: svc("fan", "turn_off"), opt: { pct: 0 } } : { calls: svc("fan", "set_percentage", { percentage: p }), opt: { pct: p } };
    }
    case "preset":
      return { calls: svc("fan", "set_preset_mode", { preset_mode: arg }) };
    case "humidity": {
      const h = Math.round(fromPct(+arg, m.lim.min, m.lim.max));
      return { calls: svc("humidifier", "set_humidity", { humidity: h }), opt: { hum: h } };
    }
    case "hmode":
      return { calls: svc("humidifier", "set_mode", { mode: arg }) };
    case "step": {
      const cur = ctx.opt?.target ?? m.target;
      const next = snap(cur + Number(arg) * m.step, m.step, m.lim.min, m.lim.max);
      return { calls: svc("water_heater", "set_temperature", { temperature: next }), opt: { target: next }, debounce: true };
    }
    case "omode":
      return { calls: svc("water_heater", "set_operation_mode", { operation_mode: arg }) };
    case "secure":
    case "release": {
      const g = RD().guard[dom]?.[name];
      return g ? { calls: svc(dom, g.service) } : { calls: [] };
    }
    case "start":
      return { calls: svc("vacuum", "start") };
    case "pause":
      return { calls: svc("vacuum", "pause") };
    case "home":
      return { calls: svc("vacuum", "return_to_base") };
  }
  return { calls: [] };
}

/** History rows (WS minimal_response or REST) → graph series over `hours`; x 0–1 on time, y 0–1 on min–max. binary → on/off steps. */
export function gadgetSeries(rows, hours, binary = false, now = Date.now()) {
  const from = now - hours * 3600e3;
  const at = (r) => (r.lu != null ? r.lu * 1000 : r.lc != null ? r.lc * 1000 : Date.parse(r.last_updated || r.last_changed));
  const val = (r) => {
    const s = r.s ?? r.state;
    return binary ? (s === "on" ? 1 : s === "off" ? 0 : NaN) : s === "" || s == null ? NaN : Number(s);
  };
  const pts = (rows || []).map((r) => [at(r), val(r)]).filter((p) => !Number.isNaN(p[0]) && !Number.isNaN(p[1])).sort((p, q) => p[0] - q[0]);
  if (!pts.length) return { series: [], lo: null, hi: null };
  const ys = pts.map((p) => p[1]);
  const lo = binary ? 0 : Math.min(...ys);
  const hi = binary ? 1 : Math.max(...ys);
  const nx = (ms) => Math.min(1, Math.max(0, (ms - from) / (now - from)));
  const ny = (v) => (hi > lo ? (v - lo) / (hi - lo) : 0.5);
  const out = [[0, ny(pts[0][1])]];
  let prev = pts[0][1];
  for (const [ms, v] of pts) {
    if (binary) out.push([nx(ms), ny(prev)]);
    out.push([nx(ms), ny(v)]);
    prev = v;
  }
  out.push([1, ny(prev)]);
  const step = Math.ceil(out.length / RD().history_max_points);
  const thin = step > 1 ? out.filter((_, i) => i % step === 0 || i === out.length - 1) : out;
  return { series: [{ cls: "is-now", pts: thin }], lo, hi };
}

/** The 24 h of a sensor through HA's websocket history (the REST route when the page has no websocket). */
export async function gadgetHistory(hass, id) {
  const rd = RD();
  const kind = rd.kinds[id.split(".")[0]];
  if (kind !== "sensor" && kind !== "binary") return { series: [], axis: null };
  const binary = kind === "binary";
  const now = Date.now();
  const from = new Date(now - rd.history_hours * 3600e3).toISOString();
  const res = hass.callWS
    ? await hass.callWS({ type: rd.history_ws, start_time: from, end_time: new Date(now).toISOString(), entity_ids: [id], minimal_response: true, no_attributes: true, significant_changes_only: false })
    : await hass.callApi("GET", `history/period/${from}?filter_entity_id=${id}&minimal_response&no_attributes`);
  const rows = Array.isArray(res) ? res[0] : res?.[id];
  const s = gadgetSeries(rows, rd.history_hours, binary, now);
  const unit = unitOf(hass.states?.[id]);
  const u = unit ? " " + unit : "";
  const axis = s.lo == null ? null : binary ? [t("rdev.h_ago", { n: rd.history_hours }), t("rdev.now")] : [`${num(s.lo)}${u}`, `${num(s.hi)}${u}`];
  return { series: s.series, axis };
}
