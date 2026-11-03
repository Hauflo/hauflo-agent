// Domain adapter · light → ONE normalized model + the controls its remote needs. Only what the device has (capabilities).
// Type quirks (HA colour modes, kelvin range, on/off-only, assumed state) live here and nowhere else.
import { t, tv, num } from "../core/i18n.js";
import { CANON } from "../gen/canon.js";
import { fromPct, toPct } from "../core/format.js";
import { clamp } from "../core/dom.js";
import { base } from "./common.js";

const MODES_CT = new Set(["color_temp", "hs", "xy", "rgb", "rgbw", "rgbww"]);
const kelvinRange = (a) => {
  const f = CANON.light.ct_fallback_k;
  return { lo: a.min_color_temp_kelvin || f.min, hi: a.max_color_temp_kelvin || f.max };
};

export function lightModel(st) {
  const a = st.attributes || {};
  const m = base(st, "light");
  const modes = a.supported_color_modes || [];
  const dim = modes.some((x) => x !== "onoff") || a.brightness != null;
  const ct = modes.includes("color_temp");
  const on = st.state === "on";
  const { lo, hi } = kelvinRange(a);
  const k = a.color_temp_kelvin ?? null;
  return {
    ...m,
    kind: "light",
    icon: on ? "light" : "lightOff",
    role: "state-light",
    on,
    assumed: a.assumed_state === true,
    caps: { onoff: true, dim, ct, nodim: !dim },
    pct: on ? Math.round(((a.brightness ?? 255) / 255) * 100) : 0,
    ct: ct ? { lo, hi, k, pct: k == null ? null : Math.round(toPct(k, lo, hi)) } : null,
  };
}

/** The 4 canon presets clamped into the lamp's own range; selected = the nearest within ct_match_k. */
const presets = (m) =>
  CANON.light.ct_presets_k.map((k, i) => ({ key: CANON.light.ct_preset_keys[i], k: clamp(k, m.ct.lo, m.ct.hi) }));

/** controls: id → a generic spec (kind: status|slider|seg|buttons|note) the Remote renders with primitives. */
export function lightControls(m, ctx = {}) {
  const pct = ctx.opt?.pct ?? m.pct;
  const c = {};
  const word = m.offline ? t("common.offline") : m.on ? t("light.on") : t("light.off");
  c.status = { kind: "status", text: m.on && m.caps.dim ? `${word} · ${pct}%` : word, dot: m.on ? "role" : null, role: m.role, power: { on: m.on, act: "power", disabled: m.offline } };
  if (m.caps.dim) c.brightness = { kind: "slider", value: m.on ? pct : 0, label: t("light.brightness"), act: "brightness", role: m.role, off: !m.on, disabled: m.offline, valuetext: `${pct}%` };
  if (m.caps.ct) {
    const p = ctx.opt?.ctPct ?? m.ct.pct ?? 50;
    c.ct = { kind: "slider", value: p, label: t("light.ct"), act: "ct", role: m.role, track: "var(--hf-ct-track)", disabled: m.offline || !m.on, valuetext: m.ct.k ? `${m.ct.k} K` : "" };
    const near = m.ct.k == null ? null : presets(m).reduce((b, x) => (Math.abs(x.k - m.ct.k) < Math.abs(b.k - m.ct.k) ? x : b));
    const sel = near && Math.abs(near.k - m.ct.k) <= CANON.light.ct_match_k ? near.key : null;
    c.presets = { kind: "seg", act: "preset", sel, role: m.role, items: presets(m).map((x) => ({ v: x.key, label: tv("light_ct", x.key), aria: `${tv("light_ct", x.key)}, ${num(x.k, 0)} K` })) };
  }
  if (m.caps.nodim) c.note = { kind: "note", text: t("light.only_switch") };
  if (m.assumed) c.assumed = { kind: "note", text: t("light.assumed"), icon: "alert" };
  const off = ctx.timer;
  c.timer = {
    kind: "buttons",
    seg: true,
    note: off ? t("light.timer_off_at", { t: off.at }) : "",
    items: [
      ...CANON.light.timer_min.map((n) => ({ text: n >= 60 ? t("time.h", { n: n / 60 }) : t("time.min", { n }), act: "timer", arg: n, on: off?.min === n, disabled: m.offline || !m.on })),
      ...(off ? [{ icon: "close", act: "timer", arg: 0, label: t("light.timer_cancel") }] : []),
    ],
  };
  c.fav = { kind: "buttons", items: [{ icon: ctx.fav ? "star" : "starOff", text: ctx.fav ? t("light.fav_remove") : t("light.fav_add"), act: "fav", on: !!ctx.fav, wide: true }] };
  return c;
}

/** act(name, arg) → HA calls + the optimistic view patch. Everything the remote can do to a light. */
export function lightAct(m, name, arg, ctx = {}) {
  const id = m.id;
  const on = (data) => [{ domain: "light", service: "turn_on", data: { entity_id: id, ...data } }];
  switch (name) {
    case "power":
      return { calls: [{ domain: "light", service: m.on ? "turn_off" : "turn_on", data: { entity_id: id } }] };
    case "brightness":
      return arg <= 0 ? { calls: [{ domain: "light", service: "turn_off", data: { entity_id: id } }], opt: { pct: 0 } } : { calls: on({ brightness_pct: Math.round(arg) }), opt: { pct: Math.round(arg) } };
    case "ct": {
      const k = Math.round(fromPct(arg, m.ct.lo, m.ct.hi));
      return { calls: on({ color_temp_kelvin: k }), opt: { ctPct: Math.round(arg) } };
    }
    case "preset": {
      const p = presets(m).find((x) => x.key === arg);
      return p ? { calls: on({ color_temp_kelvin: p.k }), opt: { ctPct: Math.round(toPct(p.k, m.ct.lo, m.ct.hi)) } } : { calls: [] };
    }
    case "timer":
      return { calls: [{ domain: "hauflo", service: "off_in", data: { entity_id: id, minutes: Number(arg) } }], timer: Number(arg) };
    case "fav":
      return { fav: true };
  }
  return { calls: [] };
}
