// hauflo-lights · the room's (floor's, house's) lights console (lighting spec §4, v5 K1/R4/R22/R23).
// Config (unchanged from the previous card): entities (lamps shown, string | {entity}), groups (central commands: shown
// when there are no entities, always switched by «all»), lamps (what the counter counts and «all off» reaches), title.
// Header: caps title · «4 из 6 · 48 %» · ⏻ (all). L: + one common brightness bar. Lamps: one-row tiles, tap = on/off,
// drag = brightness, hold = remote. S (narrow): icon-only tiles. The state word as v1: «45 %» when dimmed, else «вкл/выкл» (owner canon 03.11 overrides K4).
import { t } from "../core/i18n.js";
import { cap, head, msg, pill, power, slider, tile } from "../kit/primitives.js";
import { trouble, troubleStates } from "../core/command.js";
import { lightModel } from "../adapters/light.js";
import { base } from "../adapters/common.js";
import { blockTitle } from "../core/house.js";
import { blockScenes, blockSceneRow, blockSceneWatch, blockSceneSection, sceneAct, sceneHold, SCENE_CSS } from "./scene-row.js";

const NBSP = " ";
const ROLE = "state-light";
const idOf = (e) => (typeof e === "string" ? e : e?.entity);
const list = (x) => (Array.isArray(x) ? x.map(idOf).filter(Boolean) : []);
const shown = (c) => (list(c.entities).length ? list(c.entities) : list(c.groups));
const counted = (c) => (list(c.lamps).length ? list(c.lamps) : shown(c));
/** The block's scenes («в блоке»): what «+ Сцена» remembers is the block's lamps. */
const scenes = (card, all) => blockScenes(card, "light", [...new Set([...counted(card.config), ...shown(card.config)])], all);

/** One lamp (light or a switched socket) → the tile's model with the optimistic view applied. */
function lamp(card, id) {
  const st = card.hass.states[id];
  if (!st) return null;
  const m = id.startsWith("light.") ? lightModel(st) : { ...base(st, id.split(".")[0]), on: st.state === "on", caps: { dim: false }, pct: 0, icon: "socket" };
  const o = card.opt[id] || {};
  const on = o.on ?? m.on;
  const pct = o.pct ?? m.pct;
  return { ...m, on, pct: on ? pct || m.pct || 100 : 0, tr: trouble(m, card.cmd.of(id)) };
}

/** Header: average brightness of the lit dimmers big, «N из M горит» beside it. */
function summary(lamps) {
  const live = lamps.filter((m) => !m.offline);
  if (lamps.length && !live.length) return { text: t("common.offline"), any: false, avg: null };
  const on = live.filter((m) => m.on);
  const dim = on.filter((m) => m.caps.dim);
  const avg = dim.length ? Math.round(dim.reduce((a, m) => a + m.pct, 0) / dim.length) : null;
  // «1 из 1 горит» says nothing the tile does not: a count only for two and more
  const text = live.length > 1 ? t("lights.count_on", { n: on.length, total: live.length }) : "";
  return { text, any: on.length > 0, avg };
}

function lampTile(m, bare) {
  const status = m.tr === "offline" ? t("common.offline") : m.tr === "failed" ? t("common.failed") : m.on && m.caps.dim ? `${m.pct} %` : t(m.on ? "common.on" : "common.off");
  const icon = m.icon === "socket" ? "socket" : m.on ? "light" : "lightOff";
  return tile({
    size: "xs",
    bare,
    icon,
    on: m.on && !m.offline,
    role: ROLE,
    badge: troubleStates(m.tr),
    name: m.name,
    status,
    short: m.tr === "offline" ? t("common.no_data") : null,
    fill: m.caps.dim ? m.pct : null,
    cls: [m.tr === "sending" && "is-pending", m.offline && "is-offline"].filter(Boolean).join(" "),
    attr: {
      "data-tap": "toggle",
      "data-id": m.id,
      "data-slide": m.caps.dim && !m.offline ? "bri" : null,
      role: "button",
      tabindex: "0",
      "aria-pressed": String(!!m.on),
      "aria-label": `${m.name}${status.trim() ? ", " + status : ""}`,
    },
  });
}

/** A central group («Весь свет в доме») when the block shows only groups: v1's compact content-width pill, not a tile. */
function groupPill(m) {
  const on = m.on && !m.offline;
  return pill(m.name, {
    icon: on ? "light" : "lightOff",
    on,
    role: ROLE,
    cls: ["is-group", m.tr === "sending" && "is-pending", m.offline && "is-offline"].filter(Boolean).join(" "),
    attr: { "data-tap": "toggle", "data-id": m.id, "aria-pressed": String(on) },
  });
}

export const LIGHTS = {
  key: "lights",
  watch: (c, card) => [...new Set([...shown(c), ...list(c.groups), ...list(c.lamps), ...(card?.hass ? blockSceneWatch(card, scenes(card, true)) : [])])],
  grid: () => ({ columns: 12, rows: "auto", min_columns: 3 }),
  stub: (h) => ({ title: t("lights.title"), entities: Object.keys(h?.states || {}).filter((x) => x.startsWith("light.")).slice(0, 6) }),
  render(card, v) {
    const c = card.config;
    const title = blockTitle(card, t("lights.title"));
    const tiles = shown(c).map((id) => lamp(card, id)).filter(Boolean);
    const count = counted(c).map((id) => lamp(card, id)).filter(Boolean);
    const sc = blockSceneRow(card, scenes(card));
    if (!tiles.length) return `${head({ title })}${sc}${msg(t("lights.none"), { icon: "lightOff" })}`;
    const s = summary(count);
    // S: no room for ⏻ beside the count — «all» lives in the remote of each lamp and in M and up
    const top = head({
      title,
      value: s.avg,
      unit: "%",
      size: v === "L" ? "m" : "s",
      role: ROLE,
      off: !s.any,
      words: s.text,
      ctl: v === "S" ? "" : power(s.any, { l: false, role: ROLE, label: t(s.any ? "lights.all_off" : "lights.all_on"), disabled: !count.some((m) => !m.offline), attr: { "data-act": "all" } }),
    });
    const dims = tiles.filter((m) => m.caps.dim && !m.offline);
    const bar = v === "L" && dims.length > 1 ? slider(s.avg ?? 0, { act: "all_bri", label: t("light.brightness"), role: ROLE, off: !s.any, valuetext: s.avg != null ? `${s.avg}%` : "" }) : "";
    const bare = v === "S";
    // canon order (lighting §4 L, Crestron / Savant): scenes → the common bar → the lamps
    if (!list(c.entities).length) return `${top}${sc}<div class="hf5-row">${tiles.map(groupPill).join("")}</div>`;
    return `${top}${sc}${bar}<div class="hf5-grid${bare ? " is-bare" : ""}">${tiles.map((m) => lampTile(m, bare)).join("")}</div>`;
  },
  live(card, el, v) {
    const t1 = el.querySelector(".hf5-line .hf5-t");
    if (t1 && el.dataset.slide === "bri") t1.textContent = `${v} %`;
  },
  /** The order sheet: the config's lists; the block's scenes only while they're in it, with the family's own. */
  sections(card, base) {
    const sc = blockSceneSection(card, scenes(card));
    return [...base().filter((x) => x.key !== "scenes"), sc].filter(Boolean);
  },
  hold: (card, el) => sceneHold(card, el),
  css: `${SCENE_CSS}
.hf5-pill.is-group{color:var(--hf-ink);max-width:100%;min-width:0}
.hf5-pill.is-group.hf5-on{background:var(--hf5-role-tint,var(--hf-accent-tint));color:var(--hf-ink);box-shadow:none}
.hf5-pill.is-group.hf5-on .hf5-g{color:var(--hf5-role,var(--hf-accent))}`,
  act(card, name, arg, el) {
    const s = sceneAct(card, name, arg, el);
    if (s !== undefined) return s;
    const c = card.config;
    if (name === "toggle" || name === "bri") {
      const id = el.dataset.id;
      const m = lamp(card, id);
      if (!m) return null;
      if (m.offline) return { more: id };
      if (name === "toggle" && m.tr === "failed") return { retry: id };
      const dom = id.split(".")[0];
      if (name === "bri") {
        const p = Math.round(+arg);
        return p <= 0
          ? { key: id, calls: [{ domain: "light", service: "turn_off", data: { entity_id: id } }], opt: { [id]: { on: false } } }
          : { key: id, calls: [{ domain: "light", service: "turn_on", data: { entity_id: id, brightness_pct: p } }], opt: { [id]: { on: true, pct: p } } };
      }
      return { key: id, calls: [{ domain: dom === "light" ? "light" : "homeassistant", service: m.on ? "turn_off" : "turn_on", data: { entity_id: id } }], opt: { [id]: { on: !m.on } } };
    }
    const all = [...new Set([...counted(c), ...shown(c), ...list(c.groups)])].filter((id) => card.hass.states[id]);
    const by = (ids) => {
      const lights = ids.filter((x) => x.startsWith("light."));
      const other = ids.filter((x) => !x.startsWith("light."));
      return [lights, other];
    };
    if (name === "all") {
      const any = summary(counted(c).map((id) => lamp(card, id)).filter(Boolean)).any;
      const ids = any ? all : list(c.groups).length ? list(c.groups) : shown(c);
      const [lights, other] = by(ids);
      const svc = any ? "turn_off" : "turn_on";
      const calls = [
        ...(lights.length ? [{ domain: "light", service: svc, data: { entity_id: lights } }] : []),
        ...(other.length ? [{ domain: "homeassistant", service: svc, data: { entity_id: other } }] : []),
      ];
      return { key: "all", calls, opt: Object.fromEntries(shown(c).map((id) => [id, { on: !any }])) };
    }
    if (name === "all_bri") {
      const p = Math.round(+arg);
      const dims = shown(c).map((id) => lamp(card, id)).filter((m) => m && m.caps.dim && !m.offline);
      const target = dims.some((m) => m.on) ? dims.filter((m) => m.on) : dims;
      if (!target.length) return null;
      const ids = target.map((m) => m.id);
      return p <= 0
        ? { key: "all", calls: [{ domain: "light", service: "turn_off", data: { entity_id: ids } }], opt: Object.fromEntries(ids.map((id) => [id, { on: false }])) }
        : { key: "all", calls: [{ domain: "light", service: "turn_on", data: { entity_id: ids, brightness_pct: p } }], opt: Object.fromEntries(ids.map((id) => [id, { on: true, pct: p }])) };
    }
    return null;
  },
};
