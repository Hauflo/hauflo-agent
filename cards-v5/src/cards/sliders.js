// hauflo-sliders · tall sliders for lights, blinds, fans, players, switches (the old card's keys: entities (string |
// {entity, name, icon, position:false}), scenes / scene_room / scene_layout above them).
// A tile = a column: [icon · value] / name / status; the fill grows from the bottom in the device's role colour.
// Mouse: drag at once; finger: hold 350 ms, then drag (a vertical swipe stays the page scroll). Tap = on/off (a blind: to
// the end / stop), hold without moving = remote. No level (a switch, a blind with no position) = a plain tile; such
// a blind has ▲ ■ ▼ in it. Gates (class A) never move by a tap.
import { t } from "../core/i18n.js";
import { btn, msg, tile } from "../kit/primitives.js";
import { trouble, troubleStates } from "../core/command.js";
import { lightModel } from "../adapters/light.js";
import { coverModel, coverWord, noPosition, tapCall, triple } from "../adapters/cover.js";
import { base, OFFLINE } from "../adapters/common.js";
import { blockScenes, blockSceneRow, blockSceneWatch, blockSceneSection, sceneAct, sceneHold, SCENE_CSS } from "./scene-row.js";

const NBSP = " ";
const ROLE = { light: "state-light", cover: "state-cover" };
/** Other domains with a level: where it lives, the feature bit that says it can be set, the service that sets it. */
const LEVEL = {
  fan: { icon: "fan", bit: 1, pct: (a) => (a.percentage != null ? +a.percentage : null), on: (s) => s.state === "on", set: (p) => ["fan", p ? "set_percentage" : "turn_off", p ? { percentage: p } : {}] },
  media_player: { icon: "tv", bit: 4, pct: (a) => (a.volume_level != null ? Math.round(a.volume_level * 100) : null), on: (s) => !["off", "standby", ...OFFLINE].includes(s.state), set: (p) => ["media_player", "volume_set", { volume_level: p / 100 }] },
};
const entries = (c) => (Array.isArray(c.entities) ? c.entities.map((e) => (typeof e === "string" ? { entity: e } : e)).filter((e) => e?.entity) : []);
const scenes = (card, all) => blockScenes(card, entries(card.config).some((e) => e.entity.startsWith("cover.")) ? "cover" : "light", entries(card.config).map((e) => e.entity), all);

/** One entity → the tile's model with the optimistic view applied. dim = it has a level to drag. */
function model(card, e) {
  const st = card.hass.states[e.entity];
  if (!st) return null;
  const dom = e.entity.split(".")[0];
  const a = st.attributes || {};
  let m;
  if (dom === "light") m = lightModel(st);
  else if (dom === "cover") {
    if (e.position === false) noPosition.add(e.entity);
    m = coverModel(st, e);
    m = { ...m, pct: m.pct ?? (m.open ? 100 : 0) };
  } else {
    const L = LEVEL[dom];
    const p = L?.pct(a);
    const on = L ? L.on(st) : st.state === "on";
    m = { ...base(st, dom), kind: dom, icon: L?.icon || "toggle", role: "state-on", on, pct: on ? p ?? 100 : 0, caps: { dim: !!L && p != null && ((Number(a.supported_features) || 0) & L.bit) !== 0 } };
  }
  const o = card.opt[e.entity] || {};
  const dim = dom === "cover" ? m.caps.pos : m.caps.dim;
  const on = o.on ?? m.on;
  return { ...m, dom, dim, on, pct: dom === "cover" ? o.pct ?? m.pct : on ? (o.pct ?? m.pct) || 100 : 0, role: ROLE[dom] || m.role, icon: e.icon || m.icon, name: e.name || m.name, tr: trouble(m, card.cmd.of(e.entity)) };
}
const find = (card, id) => entries(card.config).find((e) => e.entity === id);

function slideTile(card, m) {
  const cover = m.dom === "cover";
  const moving = cover && m.moving;
  const status = m.tr === "offline" ? t("common.offline") : m.tr === "failed" ? t("common.failed") : cover ? (!m.known ? NBSP : moving ? coverWord(m.ty, moving) : m.dim && m.pct > 0 && m.pct < 100 ? NBSP : coverWord(m.ty, m.open ? "open" : "closed")) : NBSP;
  const value = m.dim && !m.offline && (cover ? m.known : m.on) ? { text: String(m.pct), unit: "%" } : null;
  // a blind with no position: ▲ ■ ▼ in the tile (the tile itself still starts / stops it)
  const ctl = cover && !m.dim && !m.offline && !m.caps.guard ? triple(m).map((b) => btn(b.icon, { label: b.act === "stop" ? b.label : b.text, off: b.act === "stop" && !m.moving, on: b.act !== "stop" && moving === (b.act === "open" ? "opening" : "closing"), role: m.role, attr: { "data-act": b.act, "data-arg": m.id } })).join("") : "";
  return tile({
    size: "m",
    icon: m.icon,
    on: m.on && !m.offline,
    role: m.role,
    badge: troubleStates(m.tr),
    name: m.name,
    status,
    value,
    ctl,
    fill: m.dim && !m.offline ? (m.on || cover ? m.pct : 0) : null,
    cls: ["is-v", m.tr === "sending" && "is-pending", m.offline && "is-offline", moving && "is-moving"].filter(Boolean).join(" "),
    attr: {
      "data-tap": "tap",
      "data-id": m.id,
      "data-slide": m.dim && !m.offline ? "set" : null,
      "data-axis": m.dim && !m.offline ? "y" : null,
      role: m.dim ? "slider" : "button",
      "aria-valuenow": m.dim ? m.pct : null,
      "aria-pressed": m.dim ? null : String(!!m.on),
      tabindex: "0",
      "aria-label": `${m.name}${status.trim() ? ", " + status : ""}`,
    },
  });
}

export const SLIDERS = {
  key: "sliders",
  watch: (c, card) => [...new Set([...entries(c).map((e) => e.entity), ...(card?.hass ? blockSceneWatch(card, scenes(card, true)) : [])])],
  grid: () => ({ columns: 12, rows: "auto", min_columns: 3 }),
  stub: (h) => ({ entities: [...Object.keys(h?.states || {}).filter((x) => x.startsWith("light.")).slice(0, 2), ...Object.keys(h?.states || {}).filter((x) => x.startsWith("cover.")).slice(0, 2)] }),
  render(card) {
    const ms = entries(card.config).map((e) => model(card, e)).filter(Boolean);
    const sc = blockSceneRow(card, scenes(card));
    if (!ms.length) return `${sc}${msg(t("sliders.none"), { icon: "lightOff" })}`;
    return `${sc}<div class="hf5-sl">${ms.map((m) => slideTile(card, m)).join("")}</div>`;
  },
  live(card, el, v) {
    const b = el.querySelector(".hf5-tv b");
    if (b?.firstChild) b.firstChild.nodeValue = v;
  },
  sections(card, base) {
    const sc = blockSceneSection(card, scenes(card));
    return [...base().filter((x) => x.key !== "scenes"), sc].filter(Boolean);
  },
  hold: (card, el) => sceneHold(card, el),
  css:
    SCENE_CSS +
    `.hf5-sl{display:grid;gap:var(--hf-sp-2);grid-template-columns:repeat(auto-fit,minmax(min(100%,calc(var(--hf-tile-min-slider) * var(--hf5-k,1))),1fr))}
.hf5-sl>.hf5-tile{min-height:var(--hf-tile-slider-h);justify-content:space-between;-webkit-touch-callout:none}
.hf5-sl>.hf5-tile>.hf5-tfill{inset:auto 0 0 0;width:auto;height:calc(var(--v,0) * 1%);transition:height var(--hf-dur)}
.hf5-sl>.hf5-tile.is-grab{box-shadow:var(--hf-edge),inset 0 0 0 var(--hf-focus-w) var(--hf5-role)}
.hf5-sl>.hf5-tile>.hf5-tc{flex-wrap:wrap;justify-content:center}
.hf5-sl>.hf5-tile>.hf5-th{flex-wrap:wrap}`,
  act(card, name, arg, el) {
    const s = sceneAct(card, name, arg, el);
    if (s !== undefined) return s;
    const id = name === "tap" || name === "set" ? el.dataset.id : arg;
    const e = find(card, id);
    const m = e && model(card, e);
    if (!m) return null;
    if (m.offline) return { more: id };
    if (name === "tap" && m.tr === "failed") return { retry: id };
    const one = (domain, service, data = {}) => [{ domain, service, data: { entity_id: id, ...data } }];
    if (m.dom === "cover" && m.caps.guard && !(name === "tap" && m.moving && m.caps.stop) && name !== "stop") return { more: id };
    if (name === "open" || name === "close" || name === "stop") return { key: id, calls: one("cover", `${name}_cover`) };
    if (name === "set") {
      const p = Math.round(+arg);
      if (m.dom === "light") return { key: id, calls: p <= 0 ? one("light", "turn_off") : one("light", "turn_on", { brightness_pct: p }), opt: { [id]: { on: p > 0, pct: p } } };
      if (m.dom === "cover") return { key: id, calls: one("cover", "set_cover_position", { position: p }), opt: { [id]: { pct: p } } };
      const [d, svc, data] = LEVEL[m.dom].set(p);
      return { key: id, calls: one(d, svc, data), opt: { [id]: { on: m.dom === "fan" ? p > 0 : m.on, pct: p } } };
    }
    if (m.dom === "cover") {
      const what = m.known || m.moving ? tapCall(m) : null;
      return what ? { key: id, calls: one("cover", `${what}_cover`) } : { more: id };
    }
    if (m.dom === "media_player") {
      const f = Number(card.hass.states[id].attributes.supported_features) || 0;
      return { key: id, calls: !m.on ? one("media_player", "turn_on") : f & (1 | 16384) ? one("media_player", "media_play_pause") : one("homeassistant", "toggle"), opt: m.on ? null : { [id]: { on: true } } };
    }
    return { key: id, calls: one(m.dom === "light" ? "light" : "homeassistant", m.on ? "turn_off" : "turn_on"), opt: { [id]: { on: !m.on } } };
  },
};
