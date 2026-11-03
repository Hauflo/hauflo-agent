// Domain adapter · climate (floor thermostat / AC / any HA climate) → ONE model. Modes, fan, swing, presets are exactly
// what the device lists (hvac_modes, fan_modes, …); setpoint step and limits are the device's own. Quirks live here.
import { t, tv, num } from "../core/i18n.js";
import { CANON } from "../gen/canon.js";
import { snap } from "../core/format.js";
import { base } from "./common.js";

const F = { target: 1, range: 2, preset: 16, fan: 8, swing: 32, swing_h: 256 };
const ROLE = { heat: "state-heat", cool: "state-cool", heat_cool: "state-auto", auto: "state-auto", dry: "state-dry", fan_only: "state-fan" };
const HVAC_ICON = { off: "power", heat: "heat", cool: "cool", heat_cool: "heatCool", auto: "auto", dry: "dry", fan_only: "fan" };
const MODE_ICON_ONLY = false; // the remote shows icon + word (owner 30.10: in the remote icons always have a caption; tiles stay wordless)

/** A row's glyph is the device's type, not its mode (v1): a floor's coil, an AC unit, a radiator; the mode colours it. */
// cools or blows (fan-coil, split) → AC unit; heats only → radiator; a floor loop → coil
export const typeIcon = (m, floor) => (floor ? "coil" : m.sub === "ac" ? "ac" : "radiator");

export function climateModel(st) {
  const a = st.attributes || {};
  const m = base(st, "climate");
  const f = a.supported_features ?? 0;
  const modes = (a.hvac_modes || []).filter((x) => x !== "off");
  const mode = st.state;
  const hasFan = !!(a.fan_modes?.length) && (f === 0 || f & F.fan);
  const kind = hasFan || modes.some((x) => ["cool", "dry", "fan_only"].includes(x)) ? "ac" : "floor";
  const lowHigh = a.target_temp_low != null && a.target_temp_high != null && a.temperature == null;
  return {
    ...m,
    kind: "climate",
    sub: kind,
    mode,
    on: mode !== "off" && !m.offline,
    action: a.hvac_action || null,
    role: m.offline || mode === "off" ? null : ROLE[mode] || "state-heat",
    icon: HVAC_ICON[mode] && mode !== "off" ? HVAC_ICON[mode] : "thermostat",
    current: a.current_temperature ?? null,
    target: a.temperature ?? null,
    low: lowHigh ? a.target_temp_low : null,
    high: lowHigh ? a.target_temp_high : null,
    step: a.target_temp_step || a.precision || CANON.climate.step_default,
    min: a.min_temp ?? 5,
    max: a.max_temp ?? 35,
    unit: "°",
    modes,
    hasOff: (a.hvac_modes || []).includes("off"),
    fan: hasFan ? { list: a.fan_modes, cur: a.fan_mode } : null,
    swing: a.swing_modes?.length ? { list: a.swing_modes, cur: a.swing_mode } : null,
    presets: a.preset_modes?.length ? { list: a.preset_modes, cur: a.preset_mode } : null,
    caps: { target: a.temperature != null, power: m.offline ? false : modes.length > 0 || (a.hvac_modes || []).includes("off"), modes: modes.length > 1, fan: hasFan, swing: !!a.swing_modes?.length, preset: !!a.preset_modes?.length, graph: true, current: a.current_temperature != null },
  };
}

const items = (list, group, role) => list.map((v) => ({ v, label: tv(group, v), role }));

export function climateControls(m, ctx = {}) {
  const c = {};
  const word = m.offline ? t("common.offline") : tv("hvac", m.mode);
  const active = m.on && m.action && !["idle", "off"].includes(m.action);
  const text = active ? tv("action", m.action) : m.on && m.action === "idle" ? `${word} · ${tv("action", "idle")}` : word;
  c.status = { kind: "status", text, dot: m.on ? "role" : null, role: m.role, power: m.caps.power ? { on: m.on, act: "power", disabled: m.offline } : null };
  const target = ctx.opt?.target ?? m.target;
  if (m.caps.target) {
    c.setpoint = {
      kind: "setpoint",
      value: m.on ? num(target, 1) : "—",
      unit: m.unit,
      side: m.caps.current ? t("climate.now", { v: `${num(m.current, 1)}${m.unit}` }) : "",
      act: "step",
      role: m.role,
      off: !m.on,
      disabled: m.offline || !m.on,
      up: t("common.up"),
      down: t("common.down"),
    };
  } else if (m.caps.current) {
    c.setpoint = { kind: "setpoint", value: num(m.current, 1), unit: m.unit, side: "", role: m.role, readonly: true, off: !m.on };
  }
  if (m.caps.modes) c.modes = { kind: "seg", items: m.modes.map((v) => ({ v, label: tv("hvac", v), icon: HVAC_ICON[v] || "thermostat", role: ROLE[v] })), sel: m.mode, act: "mode", icons: MODE_ICON_ONLY, role: m.role };
  if (m.caps.fan) c.fan = { kind: "seg", items: items(m.fan.list, "fan", m.role), sel: m.fan.cur, act: "fan", role: m.role };
  if (m.caps.swing) c.swing = { kind: "seg", items: items(m.swing.list, "swing", m.role), sel: m.swing.cur, act: "swing", role: m.role };
  if (m.caps.preset) c.preset = { kind: "seg", items: items(m.presets.list, "preset", m.role), sel: m.presets.cur, act: "preset", role: m.role };
  c.graph = { kind: "graph", period: ctx.period || "day", series: ctx.series || [], axis: ctx.axis, role: m.role || "state-heat", periods: Object.keys(CANON.climate.history_periods_h).map((v) => ({ v, label: t(`period.${v}`) })) };
  return c;
}

export function climateAct(m, name, arg, ctx = {}) {
  const id = m.id;
  const svc = (service, data) => [{ domain: "climate", service, data: { entity_id: id, ...data } }];
  switch (name) {
    case "power": {
      const back = ctx.lastMode && m.modes.includes(ctx.lastMode) ? ctx.lastMode : m.modes.includes("heat") ? "heat" : m.modes[0];
      return m.on ? { calls: svc(m.hasOff ? "set_hvac_mode" : "turn_off", m.hasOff ? { hvac_mode: "off" } : {}) } : { calls: svc(m.hasOff ? "set_hvac_mode" : "turn_on", m.hasOff ? { hvac_mode: back } : {}) };
    }
    case "step": {
      const cur = ctx.opt?.target ?? m.target;
      const next = snap(cur + Number(arg) * m.step, m.step, m.min, m.max);
      return { calls: svc("set_temperature", { temperature: next }), opt: { target: next }, debounce: true };
    }
    case "mode":
      return { calls: svc("set_hvac_mode", { hvac_mode: arg }) };
    case "fan":
      return { calls: svc("set_fan_mode", { fan_mode: arg }) };
    case "swing":
      return { calls: svc("set_swing_mode", { swing_mode: arg }) };
    case "preset":
      return { calls: svc("set_preset_mode", { preset_mode: arg }) };
    case "period":
      return { period: arg };
  }
  return { calls: [] };
}

/** History → graph series (current line + dashed setpoint), x 0–1 over the period, y 0–1 over the shared min–max. */
export function climateSeries(rows, hours, now = Date.now()) {
  const t0 = now - hours * 3600e3;
  const cap = CANON.climate.graph_points;
  // A reading holds until the next one: the state before the window starts it (x = 0), the last one runs on to now,
  // and the line steps (no slope between two sparse readings — that drew a «triangle» out of two values).
  const pick = (key) => {
    const all = rows.map((r) => [new Date(r.last_changed || r.last_updated).getTime(), r.attributes?.[key]]).filter(([, v]) => v != null && !Number.isNaN(+v));
    const before = all.filter(([x]) => x < t0).pop();
    const seq = [...(before ? [[t0, before[1]]] : []), ...all.filter(([x]) => x >= t0 && x <= now)];
    const step = Math.max(1, Math.ceil(seq.length / cap));
    const kept = seq.filter((_, i) => i % step === 0 || i === seq.length - 1);
    return kept.length ? [...kept, [now, kept[kept.length - 1][1]]] : [];
  };
  const cur = pick("current_temperature"), set = pick("temperature");
  const all = [...cur, ...set].map((p) => +p[1]);
  if (!all.length) return { series: [], lo: null, hi: null };
  const lo = Math.min(...all), hi = Math.max(...all), span = hi - lo || 1;
  const norm = (pts) =>
    pts.flatMap(([x, v], i) => {
      const at = [Math.min(1, Math.max(0, (x - t0) / (hours * 3600e3))), (v - lo) / span];
      return i && pts[i - 1][1] !== v ? [[at[0], (pts[i - 1][1] - lo) / span], at] : [at];
    });
  return { series: [{ cls: "is-now", pts: norm(cur) }, { cls: "is-set", pts: norm(set) }], lo, hi };
}

/** What the thermostat DOES now, not its mode (old card Д13/Д16): "heat"|"cool"|"dry"|"fan"|"idle"|"off"|"mode" (nothing
 *  says what it does) | null (no link). The project's own sources (card `activity`: heat/cool commands, valves) are facts
 *  and win; else HA's hvac_action, «heating» only while it fits (not warmer than the goal). */
export function climateNow(m, states = {}, act = {}) {
  if (m.offline) return null;
  if (m.mode === "off") return "off";
  const on = (ids) => (ids || []).some((id) => states[id]?.state === "on");
  if (on(act.heat) || on(act.valves)) return "heat";
  if (on(act.cool)) return "cool";
  const goal = m.target ?? null;
  const fits = (k) => m.current == null || goal == null || (k === "heat" ? m.current < goal + CANON.climate.fits_band : m.current > goal - CANON.climate.fits_band);
  switch (m.action) {
    case "heating":
    case "preheating":
      return fits("heat") ? "heat" : "idle";
    case "cooling":
      return fits("cool") ? "cool" : "idle";
    case "drying":
      return "dry";
    case "fan":
      return "fan";
    case "idle":
    case "off":
      return "idle";
  }
  const src = [...(act.heat || []), ...(act.cool || []), ...(act.valves || [])];
  return src.some((id) => ["on", "off"].includes(states[id]?.state)) ? "idle" : "mode";
}

/** The valves of a zone: {open, total} | null. */
export const valvesOf = (states = {}, act = {}) => {
  const ids = (act.valves || []).filter((id) => states[id]);
  return ids.length ? { open: ids.filter((id) => states[id].state === "on").length, total: ids.length } : null;
};
