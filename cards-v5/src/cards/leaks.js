// hauflo-leaks · «Защита от протечек»: leak sensors + a water valve of ANY integration (Neptun, Aqara, Zigbee…).
// Config (unchanged from the previous card): title, area (a HA area id: every moisture binary_sensor, every valve — not gas — and
// every water switch of it joins), sensors [id | {entity}], valves [id | {entity}] (by hand; both work together).
// The card decides nothing: it says «всё в порядке» / «протечка: где». The valve is a releasing action — it closes (or opens) only
// by a 1 s HOLD on its own pill (a tap says so), exactly like «Принять» in the alert card. Tap on a row = the entity's remote.
import { t, tn, num } from "../core/i18n.js";
import { CANON } from "../gen/canon.js";
import { notify } from "../core/dom.js";
import { head, msg, tile, holdPill } from "../kit/primitives.js";
import { trouble, troubleStates } from "../core/command.js";
import { isDead } from "../core/alarms.js";
import { alarmName, areaId, areaName, FRAME_CSS } from "../core/contact.js";

const L = CANON.leaks;
const idOf = (e) => (typeof e === "string" ? e : e?.entity);
const list = (x) => (Array.isArray(x) ? x.map(idOf).filter((v) => typeof v === "string" && v) : []);
const uniq = (xs) => [...new Set(xs)];

/** Sensors and valves: those named by hand + those of the HA area (hidden / disabled ones never join). */
function ids(card) {
  const c = card.config;
  const h = card.hass;
  const sensors = list(c.sensors);
  const valves = list(c.valves);
  if (c.area && h?.entities) {
    for (const [id, s] of Object.entries(h.states || {})) {
      const e = h.entities[id];
      if (e?.hidden || e?.disabled_by || areaId(h, id) !== c.area) continue;
      const dom = id.split(".")[0];
      const dc = s.attributes?.device_class;
      if (dom === "binary_sensor" && dc === L.sensor_class) sensors.push(id);
      else if (dom === "valve" && dc !== L.valve_skip_class) valves.push(id);
      else if (dom === "switch" && dc === L.switch_class) valves.push(id);
    }
  }
  return { sensors: uniq(sensors), valves: uniq(valves) };
}

/** A valve's state: closed (water is off) / open (water is on) / moving / nolink / other. */
function valveKind(s) {
  if (isDead(s)) return "nolink";
  if (s.state === "opening" || s.state === "closing") return "moving";
  if (s.state === "closed" || s.state === "off") return "closed";
  if (s.state === "open" || s.state === "on") return "open";
  return "other";
}

const nameOf = (h, id) => {
  const n = alarmName(h, id).name;
  const a = areaName(h, id);
  return a && !n.toLowerCase().includes(a.toLowerCase()) ? `${n} · ${a}` : n;
};

function read(card) {
  const h = card.hass;
  const { sensors, valves } = ids(card);
  const ss = sensors.filter((id) => h.states[id]).map((id) => ({ id, s: h.states[id], dead: isDead(h.states[id]), wet: h.states[id].state === "on" }));
  const vs = valves.filter((id) => h.states[id]).map((id) => ({ id, s: h.states[id], kind: valveKind(h.states[id]) }));
  const wet = ss.filter((x) => x.wet);
  const dead = ss.filter((x) => x.dead);
  const kind = !ss.length ? "empty" : wet.length ? "wet" : dead.length === ss.length ? "nolink" : "ok";
  return { ss, vs, wet, dead, kind };
}

/** The open / close call of a valve by its domain (kit.json → leaks). */
function call(id, open) {
  const dom = id.split(".")[0];
  const m = open ? L.open : L.close;
  if (dom === "valve") return { domain: "valve", service: m.valve, data: { entity_id: id } };
  if (dom === "switch") return { domain: "switch", service: m.switch, data: { entity_id: id } };
  return { domain: "homeassistant", service: m.other, data: { entity_id: id } };
}

function sensorRow(h, x) {
  const word = x.dead ? t("common.offline") : x.wet ? t("leaks.wet") : t("leaks.dry");
  return tile({
    size: "xs",
    icon: "leak",
    tint: x.wet ? "alarm" : null,
    badge: x.dead ? { offline: true } : null,
    name: nameOf(h, x.id),
    status: word,
    warn: false,
    cls: [x.wet && "is-alarm", x.dead && "is-offline"].filter(Boolean).join(" "),
    attr: { "data-tap": "open", "data-id": x.id, role: "button", tabindex: "0", "aria-label": `${nameOf(h, x.id)}, ${word}` },
  });
}

function valveRow(card, x, compact, wet) {
  const h = card.hass;
  const cmd = card.cmd.of(x.id);
  const nm = nameOf(h, x.id);
  const word = cmd.failed ? t("common.failed") : cmd.sending ? t("common.sending") : x.kind === "nolink" ? t("common.offline") : x.kind === "moving" ? t(`leaks.valve.${x.s.state}`) : x.kind === "closed" ? t("leaks.valve.off") : x.kind === "open" ? t("leaks.valve.on") : x.s.state;
  const open = x.kind === "closed";
  const can = x.kind === "closed" || x.kind === "open";
  const label = t(open ? "leaks.open" : "leaks.close");
  const ctl = can ? holdPill(compact ? "" : label, { act: "valve", icon: "valve", label, role: !open && wet ? "state-alarm" : null, attr: { "data-arg": x.id } }) : "";
  return tile({
    size: "xs",
    icon: "valve",
    on: x.kind === "open",
    role: "state-on",
    badge: troubleStates(trouble({ offline: x.kind === "nolink" }, cmd)),
    name: nm,
    status: word,
    warn: !!cmd.failed,
    cls: x.kind === "nolink" ? "is-offline lk-valve" : "lk-valve",
    ctl,
    attr: { "data-tap": "open", "data-id": x.id, role: "button", tabindex: "0", "aria-label": `${nm}, ${word}` },
  });
}

export const LEAKS = {
  key: "leaks",
  watch: (c, card) => {
    if (!card?.hass) return [...list(c.sensors), ...list(c.valves)];
    const { sensors, valves } = ids(card);
    return [...sensors, ...valves];
  },
  grid: () => ({ columns: 12, rows: "auto", min_columns: 3 }),
  stub: (h) => ({
    sensors: Object.keys(h?.states || {}).filter((x) => x.startsWith("binary_sensor.") && h.states[x].attributes?.device_class === CANON.leaks.sensor_class).slice(0, 4),
    valves: Object.keys(h?.states || {}).filter((x) => x.startsWith("valve.")).slice(0, 2),
  }),
  frame: (card) => card._q || "",
  render(card, v) {
    const c = card.config;
    const h = card.hass;
    const title = (typeof c.title === "string" && c.title) || t("leaks.title");
    const m = read(card);
    card._q = m.kind === "wet" ? "is-alarm" : "";
    if (m.kind === "empty" && !m.vs.length) return `${head({ title })}${msg(t("leaks.none"), { icon: "leak" })}`;
    const words = m.kind === "wet" ? t("leaks.leak") : m.kind === "nolink" ? t("leaks.nolink") : m.kind === "empty" ? t("leaks.no_sensors") : t("leaks.ok");
    const sub =
      m.kind === "wet"
        ? m.wet.map((x) => alarmName(h, x.id).name).join(", ")
        : m.kind === "nolink"
          ? t("leaks.of", { n: m.dead.length, total: m.ss.length })
          : m.kind === "empty"
            ? ""
            : [tn("leaks.sensors", m.ss.length), m.dead.length ? t("leaks.deaf", { n: m.dead.length }) : t("leaks.dry")].join(" · ");
    const top = head({
      title,
      value: m.wet.length ? num(m.wet.length, 0) : "",
      role: "state-alarm",
      words,
      sub,
      dot: m.kind === "wet" ? "alarm" : m.kind === "ok" ? "ok" : "warn",
      tone: m.kind === "wet" ? "alarm" : m.kind === "ok" ? null : "warn",
    });
    // trouble first: a leak, then no link, then dry (stable inside)
    const rank = (x) => (x.wet ? 0 : x.dead ? 1 : 2);
    const rows = m.ss
      .map((x, i) => ({ x, i }))
      .sort((a, b) => rank(a.x) - rank(b.x) || a.i - b.i)
      .map(({ x, i }) => sensorRow(h, x));
    const compact = v !== "L";
    const valves = m.vs.map((x) => valveRow(card, x, compact, m.kind === "wet"));
    const hint = m.ss.length && !m.vs.length ? msg(t("leaks.no_valve"), { icon: "valve" }) : "";
    return `${top}<div class="hf5-grid is-rows">${rows.join("")}${valves.join("")}</div>${hint}`;
  },
  act(card, name, arg, el) {
    if (name === "open") return { more: el.dataset.id };
    if (name !== "valve") return null;
    const { valves } = ids(card);
    const s = card.hass.states[arg];
    // only a valve this card shows, only open / closed (never mid-way or without link)
    if (!valves.includes(arg) || !s || valveKind(s) === "nolink" || valveKind(s) === "moving") return null;
    const open = valveKind(s) === "closed";
    return { key: arg, calls: [call(arg, open)], after: (ok) => ok || notify(card, t("common.failed")) };
  },
  /** Released before 1 s: the one hint. */
  early: (card, el) => notify(card, t("common.hold_hint", { action: (el.textContent.trim() || el.getAttribute("aria-label") || "").toLowerCase() })),
  css: `${FRAME_CSS}
.hf5-tile.is-xs>.hf5-tx{min-width:0}
.hf5-tile.is-xs .hf5-pill{min-width:var(--hf-ctl);max-width:100%;padding:0 var(--hf-sp-3)}
.hf5-tile.lk-valve{grid-column:1/-1}
.hf5-card.is-s .hf5-tile.lk-valve{flex-wrap:wrap}
.hf5-card.is-s .hf5-tile.lk-valve>.hf5-tc{margin-inline-start:auto}
.hf5-tile.is-alarm{box-shadow:inset 0 0 0 var(--hf-focus-w) var(--hf-state-alarm)}
.hf5-tile.is-alarm .hf5-line{color:var(--hf-state-alarm-text)}`,
};
