// hauflo-climate · one thermostat (floor heating, AC, any HA climate) or the floor zones of a room (climate spec, v5 K1/R22).
// Config (unchanged from the previous card): entity, label, floor (floor heating: its own title), activity ({heat, cool,
// valves}: the project's sources of what it does now), compact (one row), zones ([{entity, name, activity}]: a row each),
// chart (false = no 24 h graph).
// One thermostat: caps label · what it does now (never the setpoint again, R22) · ⏻ / the setpoint big in the mode's colour,
// «сейчас 24°» under it, ± (sent once after the last tap) / L: the day graph. S: the number only, tap = remote.
// Rows (compact, zones): name · what it does · now / − setpoint +. Tap = remote, hold = remote; grey only off / no link.
import { climateCompact } from "../core/house.js";
import { t, tv, num, numFixed } from "../core/i18n.js";
import { cap, head, msg, power, line, number, pm, step, graph, tile } from "../kit/primitives.js";
import { trouble, troubleStates } from "../core/command.js";
import { climateModel, climateAct, climateNow, climateSeries, valvesOf, typeIcon } from "../adapters/climate.js";
import { OFFLINE } from "../adapters/common.js";
import { attrs } from "../core/dom.js";
import { CANON } from "../gen/canon.js";

const DOING = new Set(["heat", "cool", "dry", "fan"]);
const zonesOf = (c) => (Array.isArray(c.zones) ? c.zones.filter((z) => z?.entity) : []);
const actIds = (a) => [...(a?.heat || []), ...(a?.cool || []), ...(a?.valves || [])];

/** The thermostat with the optimistic view (mode, setpoint) applied; remembers the last working mode for ⏻. */
export function model(card, id) {
  const st = card.hass.states[id];
  if (!st) return null;
  if (st.state !== "off" && !OFFLINE.has(st.state)) (card._last ??= {})[id] = st.state;
  const o = card.opt[id] || {};
  const view = o.mode != null || o.target != null ? { ...st, state: o.mode ?? st.state, attributes: { ...st.attributes, ...(o.target != null ? { temperature: o.target } : {}) } } : st;
  const m = climateModel(view);
  return { ...m, tr: trouble(m, card.cmd.of(id)) };
}

/** What it does now → one line: word (+ valves when they disagree) + the role dot while it works. */
function doing(card, m, act) {
  if (m.tr === "offline") return { text: t("common.offline"), short: t("common.no_data") };
  if (m.tr === "failed") return { text: t("common.failed"), warn: true };
  const now = climateNow(m, card.hass.states, act);
  // the building's frost guard says so, not «в норме» (old card: «Защита от замерзания»)
  if (m.on && m.presets?.cur === CANON.climate.frost_preset) return { text: t("climate.frost"), word: t("climate.frost") };
  const word = now === "mode" ? tv("hvac", m.mode) : t(`climate.state.${now}`);
  // the valves only when they contradict the word: «греет», all shut — or not heating with one open (else it is noise)
  const vs = m.on ? valvesOf(card.hass.states, act) : null;
  const v = vs && (now === "heat" ? vs.open === 0 : vs.open > 0) ? vs : null;
  return { word, text: v ? `${word} · ${t("climate.valves", { n: v.open, total: v.total })}` : word, dot: DOING.has(now) ? "role" : null };
}

/** The big value: the setpoint while it works, else what the room is (grey). */
function value(m) {
  const set = m.on && m.caps.target && m.target != null;
  const v = set ? m.target : m.current;
  return { text: num(v, 1), has: v != null, set, now: set && m.current != null ? t("climate.now_under", { v: `${numFixed(m.current, 1)}°` }) : "" };
}

const tapAttr = (id, label) => attrs({ "data-tap": "open", "data-id": id, role: "button", tabindex: "0", "aria-label": label });

const NBSP = "\u00a0";

function single(card, v, m, c) {
  const label = c.label || (c.floor ? t("climate.floor_title") : m.name);
  const d = doing(card, m, c.activity);
  const val = value(m);
  const head = `<div class="hf5-ch"><div class="hf5-tx">${cap(label)}${line(d.text, { dot: d.dot, role: m.role, warn: d.warn, short: d.short || d.word })}</div>${
    v !== "S" && m.caps.power ? power(m.on, { l: false, role: m.role, disabled: m.offline, attr: { "data-act": "power" } }) : ""
  }</div>`;
  const big = `<div class="hf5-big">${number(val.text, { size: v === "L" ? "l" : v === "M" ? "m" : "s", unit: val.has ? "°" : "", role: m.role, off: !val.set })}${val.now ? line(val.now) : ""}</div>`;
  const aria = `${label}, ${d.text}, ${val.text}°`;
  const body =
    v === "S" || !m.caps.target
      ? `<div class="hf5-tap"${tapAttr(m.id, aria)}>${big}</div>`
      : `<div class="hf5-tap"${tapAttr(m.id, aria)}>${pm(big, { act: "step", disabled: !val.set || m.offline })}</div>`;
  let chart = "";
  if (v === "L" && c.chart !== false) {
    const h = card.load(`h:${m.id}`, CANON.climate.card_history_ttl_min * 60e3, () => history(card, m.id));
    // a flat or empty day says nothing: no chart (it was ~100px of a straight line); off — nothing to follow either
    const flat = (s) => s.pts.length < 2 || s.pts.every((p) => Math.abs(p[1] - s.pts[0][1]) < CANON.climate.flat_eps);
    chart = !m.on ? "" : h === undefined ? `<div class="hf5-empty"></div>` : !h || (h.series || []).every(flat) ? "" : graph(h?.series || [], { role: m.role || "state-offline", axis: h?.lo == null ? null : [`${num(h.lo)}°`, `${num(h.hi)}°`], label: t("common.history") });
  }
  return head + body + chart;
}

async function history(card, id) {
  const hours = CANON.climate.card_history_h;
  const from = new Date(Date.now() - hours * 3600e3).toISOString();
  const r = await card.hass.callApi("GET", `history/period/${from}?filter_entity_id=${id}&significant_changes_only=0`);
  return climateSeries(r?.[0] || [], hours);
}

/** A group of thermostats (rows {m, now, fault}) → one header line: fault > no link > what they do > «в норме» / «все выкл». */
export function groupSummary(rs) {
  const n = (f) => rs.filter(f).length;
  const fault = n((r) => r.fault);
  if (fault) return { text: t("acs.fault_n", { n: fault }), dot: "warn" };
  const off = n((r) => r.m.offline);
  if (off === rs.length) return { text: t("common.offline"), dot: "warn" };
  const parts = [["heat", n((r) => r.now === "heat")], ["cool", n((r) => r.now === "cool")], ["work", n((r) => r.now === "dry" || r.now === "fan")]]
    .filter(([, k]) => k)
    .map(([w, k]) => t(`acs.${w}_n`, { n: k }));
  if (off) parts.unshift(t("devices.offline_n", { n: off }));
  if (parts.length) return { text: parts.join(" · "), dot: off ? "warn" : null };
  if (rs.some((r) => r.now === "mode")) return { text: rs.length > 1 ? t("acs.on_n", { n: n((r) => r.m.on), total: rs.length }) : "" };
  if (rs.some((r) => r.now === "idle")) return { text: t("acs.ok"), dot: "ok" };
  return { text: t("acs.all_off") };
}

/** One row (compact card, a floor zone, the house's list): name · what it does · now / − setpoint +.
 *  o: {fault, floor (a floor-heating block: the coil glyph)} — an open equipment fault of its room: «авария» in the attention colour instead of what it does. */
export function climateRow(card, v, id, name, act, o = {}) {
  const m = model(card, id);
  if (!m) return "";
  const d = o.fault && m.tr !== "offline" ? { text: t("climate.fault"), word: t("climate.fault"), warn: true } : doing(card, m, act);
  const val = value(m);
  const status = d.text;
  // S: name + the number only; M: no icon (the stepper needs the room, the dot keeps the mode's colour); L: all.
  // v1: «сейчас X°» sits UNDER the value, right-aligned; its line is always there (NBSP) — no jump when it goes
  const now = val.now && !d.warn && m.tr !== "offline" ? val.now : NBSP;
  const big = number(val.text, { size: "xs", unit: val.has ? "°" : "", role: m.role, off: !val.set });
  // S (a narrow block) keeps the number alone: «Сейчас» under it does not fit and is never shown there (no jump either)
  const n = v === "S" ? big : `<span class="hf5-vn">${big}<small class="hf5-now">${now}</small></span>`;
  const ctl = v === "S" || !m.caps.target ? n : step(n, { act: "step", disabled: !val.set || m.offline, attr: { "data-id": id } });
  return tile({
    size: "xs",
    icon: v === "L" ? typeIcon(m, o.floor) : null,
    on: m.on && !m.offline,
    role: m.role,
    dotRole: m.role,
    tint: o.fault ? "warn" : null,
    badge: troubleStates(m.tr),
    name: name || m.name,
    status,
    short: d.word || d.short,
    warn: d.warn,
    dot: d.dot,
    ctl,
    cls: [m.tr === "sending" && "is-pending", m.offline && "is-offline"].filter(Boolean).join(" "),
    attr: { "data-tap": "open", "data-id": id, role: "button", tabindex: "0", "aria-label": `${name || m.name}, ${status}${val.set ? `, ${val.text}°` : ""}` },
  });
}

export const CLIMATE = {
  key: "climate",
  watch: (c) => [...new Set([c.entity, ...actIds(c.activity), ...zonesOf(c).flatMap((z) => [z.entity, ...actIds(z.activity)])].filter(Boolean))],
  // v1: a thermostat is a full-width block (its L shows the day graph); a half-width one left an empty half beside it
  grid: () => ({ columns: 12, rows: "auto", min_columns: 3 }),
  stub: (h) => ({ entity: Object.keys(h?.states || {}).find((x) => x.startsWith("climate.")) }),
  render(card, v) {
    const c = card.config;
    const zones = zonesOf(c);
    if (zones.length) {
      const rows = zones.map((z) => climateRow(card, v, z.entity, z.name, z.activity, { floor: c.floor !== false })).filter(Boolean);
      const ms = zones.map((z) => ({ z, m: model(card, z.entity) })).filter((x) => x.m);
      const s = ms.length ? groupSummary(ms.map(({ z, m }) => ({ m, now: climateNow(m, card.hass.states, z.activity), fault: false }))) : null;
      const top = head({ title: c.label || t("climate.floor_title"), quiet: true, words: s?.text, dot: s?.dot, tone: s && s.dot !== "ok" && s.dot });
      return `${top}${rows.length ? `<div class="hf5-grid is-rows">${rows.join("")}</div>` : msg(t("climate.none"), { icon: "thermostat" })}`;
    }
    const m = c.entity && model(card, c.entity);
    if (!m) return `${head({ title: c.label || t("climate.title") })}${msg(t("climate.none"), { icon: "thermostat" })}`;
    if (climateCompact(card)) return climateRow(card, v, c.entity, c.label, c.activity);
    return single(card, v, m, c);
  },
  act(card, name, arg, el) {
    const id = el?.dataset.id || card.config.entity;
    const m = id && model(card, id);
    if (!m) return null;
    if (name === "open") return m.tr === "failed" ? { retry: id } : { more: id };
    if (m.offline) return { more: id };
    if (name === "step") {
      const r = climateAct(m, "step", arg);
      return { key: id, calls: r.calls, opt: { [id]: { target: r.opt.target } }, debounce: true };
    }
    if (name === "power") {
      const r = climateAct(m, "power", null, { lastMode: card._last?.[id] });
      const mode = r.calls[0]?.data?.hvac_mode ?? (m.on ? "off" : card._last?.[id] || m.modes[0]);
      return { key: id, calls: r.calls, opt: mode ? { [id]: { mode } } : null };
    }
    return null;
  },
};
