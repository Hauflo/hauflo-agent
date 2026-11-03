// hauflo-room · a room tile that glows with its state (house spec §7, v5 K4/K7, v2 rule 5).
// Config (unchanged from the previous card): name, icon, badge, picture, path, lights, climate, covers, alarms, area,
// temperature (sensors when there is no thermostat), temps [{entity, name}], activity ({heat, cool, valves}), rooms, subtitle.
// One colour signal: lit → the icon in the light colour + warm glow; cooling → cool colour + glow; an alarm → the even red
// rim + glow, attention (accepted, a fault) → yellow rim. No state words on the tile (K4): counters as icons (lamps on,
// blinds open, humidity), trouble = one word instead. Tap = the room's page; hold = the room remote (light, blinds, ±);
// L: + the lights' ⏻. A tile with no lights/climate/covers that only links («Автоматизации») says its subtitle.
import { t, num } from "../core/i18n.js";
import { esc, attrs, vars, go } from "../core/dom.js";
import { glyph } from "../core/icons.js";
import { ico, line, name as nameEl, number, power } from "../kit/primitives.js";
import { lightModel } from "../adapters/light.js";
import { coverModel } from "../adapters/cover.js";
import { climateModel, climateNow, climateAct } from "../adapters/climate.js";
import { movable } from "./covers.js";
import { OFFLINE } from "../adapters/common.js";
import { openAlarms, isCritical, isDead } from "../core/alarms.js";
import { CANON } from "../gen/canon.js";
import { openRoom, refreshRoom } from "../sheets/room.js";

const arr = (x) => (Array.isArray(x) ? x.filter((v) => typeof v === "string") : []);
const actIds = (a) => [...arr(a?.heat), ...arr(a?.cool), ...arr(a?.valves)];
const ROOM = CANON.room;
const tempOf = (s) => {
  if (!s || OFFLINE.has(s.state)) return undefined;
  const v = Number(s.attributes?.current_temperature ?? s.state);
  return Number.isFinite(v) ? v : undefined;
};
const avg = (xs) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : undefined);

// Н3: every moisture sensor of ANY integration in the room's HA area, beside the configured alarms (cached per registry).
const MOIST = new WeakMap();
const areaOf = (h, id) => {
  const e = h?.entities?.[id];
  return e?.area_id || (e?.device_id && h.devices?.[e.device_id]?.area_id) || null;
};
export function leakIds(h, c) {
  const area = c.area || [...arr(c.lights), c.climate, ...arr(c.covers), ...arr(c.alarms)].map((id) => id && areaOf(h, id)).find(Boolean);
  if (!area || !h?.entities) return [];
  let by = MOIST.get(h.entities);
  if (!by) {
    by = new Map();
    for (const [id, s] of Object.entries(h.states || {})) {
      if (!id.startsWith("binary_sensor.") || s.attributes?.device_class !== "moisture") continue;
      const e = h.entities[id];
      if (e?.hidden || e?.disabled_by) continue;
      const a = areaOf(h, id);
      if (a) by.set(a, [...(by.get(a) || []), id]);
    }
    MOIST.set(h.entities, by);
  }
  return (by.get(area) || []).filter((id) => !arr(c.alarms).includes(id));
}

/** The room as the tile and its remote read it (optimistic lights from the card's own commands). */
export function roomModel(card) {
  const h = card.hass;
  const c = card.config;
  const S = h.states;
  const lights = arr(c.lights).map((id) => S[id] && { ...lightModel(S[id]), ...(card.opt[id] || {}) }).filter(Boolean);
  const covers = arr(c.covers).map((id) => S[id] && coverModel(S[id])).filter(Boolean);
  const cs = c.climate && S[c.climate];
  const set = c.climate && card.opt[c.climate]?.target;
  const clim = cs ? climateModel(set != null ? { ...cs, attributes: { ...cs.attributes, temperature: set } } : cs) : null;
  const now = clim && clim.current != null ? climateNow(clim, S, c.activity) : null;
  const temp = clim ? (clim.offline ? undefined : clim.current ?? undefined) : avg(arr(c.temperature).map((id) => tempOf(S[id])).filter((v) => v !== undefined));
  const hum = clim && !clim.offline ? S[c.climate].attributes?.current_humidity : undefined;
  const others = (Array.isArray(c.temps) ? c.temps : []).map((x) => ({ name: x?.name || "", v: tempOf(S[x?.entity]) })).filter((x) => x.v !== undefined);
  const on = lights.filter((m) => m.on && !m.offline);
  const open = covers.filter((m) => !m.offline && m.known && (m.open || m.moving));
  return { lights, covers, clim, now, temp, hum: hum != null && Number.isFinite(+hum) ? +hum : undefined, others, on, open, trouble: trouble(card, lights, covers, clim) };
}

/** The one trouble, most urgent first: alarm (red) > attention (yellow rim) > leak in the area > no link > no data. */
function trouble(card, lights, covers, clim) {
  const h = card.hass;
  const c = card.config;
  const list = openAlarms(card, arr(c.alarms)).filter((a) => !a.returned);
  const live = list.filter((a) => h.states[a.entity]);
  if (live.length) {
    const a = live[0];
    const crit = live.some((x) => isCritical(h, x) && !x.ackedBy);
    const dc = h.states[a.entity].attributes?.device_class;
    const k = `alert.word.${dc}`;
    const word = t(k) === k ? t("alert.word.default") : t(k);
    const where = c.rooms?.[a.entity];
    return { kind: crit ? "alarm" : "attn", text: where ? `${word} · ${where}` : word, short: word, more: live.length - 1 };
  }
  const wet = leakIds(h, c).filter((id) => h.states[id]?.state === "on");
  if (wet.length) return { kind: "alarm", text: t("alert.word.moisture"), more: wet.length - 1 };
  const deaf = arr(c.alarms).filter((id) => isDead(h.states[id])).length;
  const watched = [...lights, ...covers, ...(clim ? [clim] : [])];
  if (watched.length && watched.every((m) => m.offline)) return { kind: "off", text: t("common.offline"), short: t("common.no_data") };
  if (deaf) return { kind: "off", text: t("room.deaf", { n: deaf }) };
  const half = watched.filter((m) => m.offline).length;
  if (half) return { kind: "off", text: t("room.deaf", { n: half }) };
  return null;
}

/** The one colour signal of the tile (frame / glow class). */
function signal(m) {
  if (m.trouble?.kind === "alarm") return "is-alarm";
  if (m.trouble?.kind === "attn") return "is-attn";
  if (m.on.length) return "is-lit";
  if (m.now === "cool") return "is-cool";
  return "";
}

/** Counters as icons (K4): lamps on, blinds open, humidity — plain ink-2, no coloured counts (v2 rule 5). */
function counters(m, v) {
  const out = [];
  if (m.lights.length && (m.on.length || m.lights.some((x) => !x.offline)))
    out.push({ ic: m.on.length ? "light" : "lightOff", text: String(m.on.length), aria: t("room.c_lights", { n: m.on.length, total: m.lights.length }) });
  if (m.covers.some((x) => x.known)) out.push({ ic: "blind", text: String(m.open.length), aria: t("room.c_covers", { n: m.open.length, total: m.covers.length }) });
  if (m.hum !== undefined) out.push({ ic: "humidity", text: `${num(m.hum, 0)} %`, aria: t("room.c_hum", { v: num(m.hum, 0) }) });
  const max = ROOM.counters_max[v] ?? out.length;
  return out.slice(0, max);
}
const countRow = (cs) =>
  cs.length ? `<div class="hf5-rc" role="list">${cs.map((x) => `<span role="listitem" aria-label="${esc(x.aria)}">${glyph(x.ic)}<b>${esc(x.text)}</b></span>`).join("")}</div>` : "";

/** The room's own icon: the integrator's MDI pick (HA draws it) or a badge letter («2.1»). */
function roomIcon(c, m, sig) {
  const own = c.badge ? `<b class="hf5-rb">${esc(c.badge)}</b>` : `<ha-icon icon="${esc(c.icon || ROOM.default_icon)}"></ha-icon>`;
  const role = sig === "is-lit" ? "state-light" : sig === "is-cool" ? "state-cool" : null;
  const tint = sig === "is-alarm" ? "alarm" : sig === "is-attn" ? "warn" : null;
  const badge = m.trouble?.kind === "off" ? { offline: true } : null;
  return ico(own, { on: !!role, role, tint, badge, cls: "hf5-ri" });
}

function status(c, m) {
  const tr = m.trouble;
  if (tr) {
    const text = tr.more > 0 ? `${tr.text} · ${t("common.more_n", { n: tr.more })}` : tr.text;
    // trouble is never hidden by fit (never_drop): the short word, and it may wrap rather than vanish
    return line(text, { cls: ["hf5-trl", tr.kind === "alarm" ? "is-alarm" : tr.kind === "attn" ? "is-warn" : ""].join(" "), short: tr.short || tr.text });
  }
  if (c.subtitle && !m.lights.length && !m.covers.length && !m.clim) return line(String(c.subtitle));
  return "";
}

const tempText = (m) => (m.temp === undefined ? "—" : String(Math.round(m.temp)));

export const ROOM_CARD = {
  key: "room",
  watch: (c, card) => [...arr(c.lights), c.climate, ...arr(c.covers), ...arr(c.alarms), ...arr(c.temperature), ...(Array.isArray(c.temps) ? c.temps.map((x) => x?.entity) : []), ...actIds(c.activity), ...(card?.hass ? leakIds(card.hass, c) : [])].filter(Boolean),
  grid: () => ({ columns: 6, rows: "auto", min_columns: 3 }),
  stub: (h) => {
    const ids = (d, n) => Object.keys(h?.states || {}).filter((x) => x.startsWith(d + ".")).slice(0, n);
    return { name: t("room.stub"), lights: ids("light", 4), climate: ids("climate", 1)[0], covers: ids("cover", 2) };
  },
  // called right after render: the same model, read once per draw
  frame(card) {
    const m = card._rm || (card.hass && roomModel(card));
    return ["hf5-room", m && signal(m), card.config.picture && "is-pic"].filter(Boolean).join(" ");
  },
  render(card, v) {
    const c = card.config;
    const m = (card._rm = roomModel(card));
    refreshRoom(card);
    const sig = signal(m);
    const nm = c.name || t("room.stub");
    const cs = counters(m, v);
    const st = status(c, m);
    const hasTemp = m.temp !== undefined || !!m.clim || arr(c.temperature).length > 0;
    const big = hasTemp ? number(tempText(m), { size: ROOM.num[v], unit: m.temp === undefined ? "" : "°", off: m.temp === undefined }) : "";
    const sub = v === "M" || v === "L" ? m.others.slice(0, ROOM.others_max).map((x) => `<div class="hf5-t2">${esc(x.name)} ${esc(num(x.v, 1))}°</div>`).join("") : "";
    const pic = c.picture ? `<div class="hf5-ph"${vars({ "--hf5-ph": `url("${String(c.picture).replace(/["\\\n]/g, encodeURIComponent)}")` })}></div>` : "";
    const aria = [nm, m.temp !== undefined ? `${Math.round(m.temp)}°` : "", m.trouble?.text, ...cs.map((x) => x.aria)].filter(Boolean).join(", ");
    const tap = attrs({ "data-tap": "open", "data-id": "room", role: c.path ? "link" : "button", tabindex: "0", "aria-label": aria });
    const pw = v === "L" && m.lights.length ? power(m.on.length > 0, { l: false, role: "state-light", disabled: m.lights.every((x) => x.offline), attr: { "data-act": "lights" } }) : "";
    if (v === "XS")
      return `<div class="hf5-rt hf5-tilehold is-xs"${tap}>${roomIcon(c, m, sig)}<div class="hf5-tx">${nameEl(nm, { split: false })}${st || countRow(cs)}</div>${big}</div>${pic}`;
    if (v === "S")
      // v1 komnaty: the temperature top-right, level with the icon; the name under the icon; the counts one line at the bottom
      return `<div class="hf5-rt hf5-tilehold is-s"${tap}><div class="hf5-rs">${roomIcon(c, m, sig)}${big}</div>${nameEl(nm, { split: false })}${st || countRow(cs)}</div>${pic}`;
    return `<div class="hf5-rt hf5-tilehold"${tap}><div class="hf5-rh">${nameEl(nm, { split: false })}${pw}</div><div class="hf5-rm">${roomIcon(c, m, sig)}<div class="hf5-rv">${big}${sub}</div></div>${st}${countRow(cs)}</div>${pic}`;
  },
  act(card, name, arg) {
    const c = card.config;
    if (name === "open") {
      if (!c.path) return openRoom(card), null;
      go(c.path);
      return null;
    }
    if (name === "lights") return lightsAll(card);
    if (name.startsWith("all_")) {
      const what = name.slice(4);
      const svc = { open: "open_cover", close: "close_cover", stop: "stop_cover" }[what];
      const ids = movable(roomModel(card).covers).filter((m) => (what === "stop" ? m.caps.stop : m.caps[what])).map((m) => m.id);
      return svc && ids.length ? { key: "covers", calls: [{ domain: "cover", service: svc, data: { entity_id: ids } }] } : null;
    }
    if (name === "step") {
      const k = roomModel(card).clim;
      if (!k || k.offline || !k.caps.target) return null;
      const r = climateAct(k, "step", arg, { opt: card.opt[c.climate] });
      return { key: c.climate, calls: r.calls, opt: { [c.climate]: r.opt }, debounce: true };
    }
    return null;
  },
  hold: (card) => (openRoom(card), true),
  css: `.hf5-card.hf5-room{position:relative;overflow:hidden;padding:0;isolation:isolate}
.hf5-rt{position:relative;z-index:1;box-sizing:border-box;display:flex;flex-direction:column;justify-content:space-between;gap:var(--hf-sp-2);height:100%;min-height:var(--hf-room-min-h);padding:var(--hf-sp-4);cursor:pointer;-webkit-tap-highlight-color:transparent;user-select:none;-webkit-user-select:none;touch-action:pan-y;outline:0;border-radius:inherit}
.hf5-rt:focus-visible{box-shadow:inset 0 0 0 var(--hf-focus-w) var(--hf-focus)}
.hf5-rt.is-s{min-height:0;padding:var(--hf-sp-3);justify-content:flex-start}
.hf5-rs{display:flex;align-items:center;justify-content:space-between;gap:var(--hf-sp-2);min-width:0}
.hf5-rs>.hf5-num{line-height:1}
.hf5-rt.is-s .hf5-rc{flex-wrap:nowrap;overflow:hidden}
.hf5-rt.is-xs{min-height:0;flex-direction:row;align-items:center;gap:var(--hf-sp-3);padding:var(--hf-sp-2) var(--hf-sp-3)}
.hf5-rt.is-xs>.hf5-tx{flex:1 1 auto;min-width:0;display:flex;flex-direction:column}
.hf5-rh{display:flex;align-items:flex-start;justify-content:space-between;gap:var(--hf-sp-2);min-width:0}
.hf5-rm{display:flex;align-items:center;justify-content:space-between;gap:var(--hf-sp-3);min-width:0}
.hf5-rv{display:flex;flex-direction:column;align-items:flex-end;min-width:0}
.hf5-t2{color:var(--hf-ink-2);font-size:var(--hf-fs-s);white-space:nowrap}
.hf5-ri ha-icon{--mdc-icon-size:var(--hf-icon-m);display:flex}
.hf5-rt:not(.is-xs):not(.is-s) .hf5-ri{width:var(--hf-ctl);height:var(--hf-ctl)}
.hf5-rb{font-size:var(--hf-fs-m);font-weight:var(--hf-w-med);letter-spacing:-.02em}
.hf5-rc{display:flex;flex-wrap:wrap;gap:var(--hf-sp-3);color:var(--hf-ink-2);font-size:var(--hf-fs-s)}
.hf5-rc>span{display:inline-flex;align-items:center;gap:var(--hf-sp-1);white-space:nowrap}
.hf5-rc .hf5-g{width:var(--hf-icon-s);height:var(--hf-icon-s)}.hf5-rc b{font-weight:var(--hf-w-med)}
.hf5-trl,.hf5-trl>.hf5-t{white-space:normal;overflow:visible;overflow-wrap:anywhere;hyphens:auto}
.hf5-line.is-alarm{color:var(--hf-state-alarm-text);font-weight:var(--hf-w-med)}
/* one colour signal (v2 rule 5): glow of light / cold; the alarm's even rim + glow; attention — yellow rim (K7) */
.hf5-room.is-lit{background:radial-gradient(55% 55% at 80% 20%,var(--hf-state-light-glow),transparent 75%),var(--hf-surface)}
.hf5-room.is-cool{background:radial-gradient(55% 55% at 80% 20%,var(--hf-state-cool-glow),transparent 75%),var(--hf-surface)}
.hf5-room.is-alarm{box-shadow:var(--hf-elev-1),inset 0 0 0 var(--hf-focus-w) var(--hf-state-alarm);background:radial-gradient(60% 55% at 75% 20%,var(--hf-state-alarm-glow),transparent 75%),var(--hf-surface)}
.hf5-room.is-attn{box-shadow:var(--hf-elev-1),inset 0 0 0 var(--hf-focus-w) var(--hf-state-warn)}
/* a photo of the HA area: the background, shaded top and bottom; text in the photo ink */
.hf5-ph{position:absolute;inset:0;z-index:0;background:var(--hf5-ph) center/cover no-repeat}
.hf5-ph::after{content:"";position:absolute;inset:0;background:var(--hf-photo-shade)}
.hf5-room.is-pic .hf5-rt,.hf5-room.is-pic .hf5-rc,.hf5-room.is-pic .hf5-t2,.hf5-room.is-pic .hf5-num>b,.hf5-room.is-pic .hf5-line{color:var(--hf-ink-on-media);text-shadow:var(--hf-photo-text-shadow)}
.hf5-room.is-pic:not(.is-lit):not(.is-cool) .hf5-ri{--hf5-tint:var(--hf-photo-chip);--hf5-glyph:var(--hf-ink-on-media)}
.hf5-room.is-pic.is-alarm .hf5-ri{--hf5-glyph:var(--hf-state-alarm)}`,
};

/** ⏻ of the room's lights: any on → all off, else all on (the old tile's hold; now the L button and the remote). */
export function lightsAll(card) {
  const ls = arr(card.config.lights).filter((id) => card.hass.states[id] && !OFFLINE.has(card.hass.states[id].state));
  if (!ls.length) return null;
  const any = ls.some((id) => (card.opt[id]?.on ?? card.hass.states[id].state === "on"));
  const byDomain = {};
  for (const id of ls) (byDomain[id.split(".")[0]] ||= []).push(id);
  return {
    key: "lights",
    calls: Object.entries(byDomain).map(([d, ids]) => ({ domain: d === "switch" ? "switch" : "light", service: any ? "turn_off" : "turn_on", data: { entity_id: ids } })),
    opt: Object.fromEntries(ls.map((id) => [id, { on: !any }])),
  };
}
