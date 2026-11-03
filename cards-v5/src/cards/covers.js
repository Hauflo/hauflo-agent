// hauflo-covers · the room's (floor's, house's) blinds (covers spec v5 §2 «Группа штор», K1/K4/K5).
// Config (unchanged from the previous card): entities (string | {entity, name, position:false}), groups (central covers:
// shown when there are no entities, moved by «все»), title, block, scenes / scene_room / cover_scene_layout.
// Header: caps title · «2 из 3 открыто · 57 %» · ↑ ■ ↓ все (S: none). Blinds: one-row tiles — the fill is the position,
// no state words; tap = to the end / stop while moving; hold = remote. No position: the row IS ▲ ■ ▼.
// Gates / garage / doors (class A): tap = remote (moving → stop); opening only by a 1 s hold in the remote.
import { t, tn } from "../core/i18n.js";
import { btn, cap, head, msg, tile } from "../kit/primitives.js";
import { trouble, troubleStates } from "../core/command.js";
import { coverModel, coverWord, noPosition, tapCall, triple } from "../adapters/cover.js";
import { cls, vars } from "../core/dom.js";
import { blockTitle } from "../core/house.js";
import { blockScenes, blockSceneRow, blockSceneWatch, blockSceneSection, sceneAct, sceneHold, SCENE_CSS } from "./scene-row.js";

const NBSP = " ";
const ROLE = "state-cover";
const idOf = (e) => (typeof e === "string" ? e : e?.entity);
const list = (x) => (Array.isArray(x) ? x.map(idOf).filter(Boolean) : []);
const shownOf = (c) => (list(c.entities).length ? c.entities : c.groups || []);
const shown = (c) => list(shownOf(c));
/** The config's word on each blind: own name, «no position status» (KNX without a status address). */
function hints(c) {
  const h = {};
  for (const e of [...(c.entities || []), ...(c.groups || [])]) {
    if (!e || typeof e !== "object" || !e.entity) continue;
    h[e.entity] = e;
    if (e.position === false) noPosition.add(e.entity);
  }
  return h;
}
const scenes = (card, all) => blockScenes(card, "cover", shown(card.config), all);

/** One blind → the tile's model. No optimistic motion: a blind already at its end never reports a change, so a guessed
 *  arrow could stay forever — «отправка…» (the command's badge) is the honest answer until the bus does. */
function blind(card, id, h = {}) {
  const st = card.hass.states[id];
  if (!st || !id.startsWith("cover.")) return null;
  const m = coverModel(st, h[id] || {});
  return { ...m, name: h[id]?.name || m.name, tr: trouble(m, card.cmd.of(id)) };
}
const all = (card) => {
  const h = hints(card.config);
  return shown(card.config).map((id) => blind(card, id, h)).filter(Boolean);
};
/** Who «все» moves (spec §2): on the line, not a gate (class A never moves with a group). */
export const movable = (ms) => ms.filter((m) => !m.offline && !m.caps.guard);

/** Header: the average position big (blinds that report one), «N из M открыто», «1 движется» under it. */
function summary(ms) {
  const live = ms.filter((m) => !m.offline);
  const moving = live.filter((m) => m.moving).length;
  if (ms.length && !live.length) return { text: t("common.offline"), avg: null, moving };
  if (live.length && live.every((m) => m.caps.nopos && !m.known)) return { text: t("covers.no_position"), avg: null, moving };
  const open = live.filter((m) => m.known && m.open);
  const pos = live.filter((m) => m.caps.pos && m.pct != null);
  const avg = pos.length ? Math.round(pos.reduce((a, m) => a + m.pct, 0) / pos.length) : null;
  return { text: live.length > 1 ? t("covers.count", { n: open.length, total: live.length }) : "", avg, moving };
}

/** The moving blind's icon is its arrow (K4: «стрелка хода в подложке»); otherwise its type's glyph. */
const iconOf = (m) => (m.moving ? triple(m).find((b) => b.act === (m.moving === "opening" ? "open" : "close")).icon : m.icon);

/** The window under the tile, flat: the open part tinted, the fabric where it hangs (position / opening). */
function artOf(m) {
  if (!m.sub.art) return "";
  const v = m.caps.pos ? m.pct ?? 0 : m.known && m.open ? 100 : 0;
  return `<span class="${cls("hf5-art", "is-" + m.sub.art, m.sub.opening && "is-o-" + m.sub.opening)}"${vars({ "--v": v })}><i></i><i></i></span>`;
}

/** The art tile's one line: what the drawing cannot say (moving, slats, the way it opens, trouble). */
function artStatus(m) {
  if (m.tr === "offline") return t("common.offline");
  if (m.tr === "failed") return t("common.failed");
  if (!m.known) return t("common.no_data");
  if (m.moving) return coverWord(m.ty, m.moving) + (m.caps.pos && m.pct != null && !m.caps.guard ? ` · ${m.pct} %` : "");
  if (m.caps.guard || !m.caps.pos || !m.pct) return coverWord(m.ty, m.open ? "open" : "closed");
  if (m.tilt != null && m.caps.tilt) return t("cover.tilt_short", { v: m.tilt });
  if (m.sub.opening && m.ty.axis === "h" && !m.caps.guard) return t(`cover.opening.${m.sub.opening}`);
  return NBSP;
}

/** M/L · one window: [type icon · position big] / name / status; moving → ■ takes the corner (the % goes to the status). */
function artTile(m) {
  const status = artStatus(m);
  const value = m.caps.pos && m.known && !m.offline && !m.moving && m.pct > 0 ? { text: String(m.pct), unit: "%" } : null;
  const corner = m.moving && m.caps.stop ? btn("stop", { label: t("cover.stop"), on: true, role: ROLE, attr: { "data-act": "stop", "data-arg": m.id } }) : "";
  return tile({
    size: "s",
    icon: iconOf(m),
    on: m.on,
    role: ROLE,
    badge: troubleStates(m.tr),
    name: m.name,
    status,
    value,
    art: artOf(m),
    corner,
    warn: m.tr === "failed",
    cls: [m.tr === "sending" && "is-pending", m.offline && "is-offline", m.moving && "is-moving"].filter(Boolean).join(" "),
    attr: { "data-tap": "tap", "data-id": m.id, role: "button", tabindex: "0", "aria-label": `${m.name}, ${m.over}${value ? ", " + value.text + " %" : ""}${status.trim() ? ", " + status : ""}` },
  });
}
/** Which blinds are rows: S (no room), and those with no position — the row IS ▲ ■ ▼ (owner 03.11, image 4). */
const isRow = (m, v) => v === "S" || (m.caps.nopos && !m.caps.guard && !m.offline);

function blindTile(m, v) {
  const status = m.tr === "offline" ? t("common.offline") : m.tr === "failed" ? t("common.failed") : !m.known ? (m.caps.nopos ? (m.moving ? coverWord(m.ty, m.moving) : "") : t("common.no_data")) : m.caps.pos && m.pct > 0 ? `${m.pct} %` : NBSP;
  // no position: the row is ▲ ■ ▼ (■ always on its place, pale while idle — canon sizes v5.cover_triplet);
  // S has no room for them (spec §1.2 XS: «тесно — уходят ▲ ▼, тап по строке»)
  const ctl =
    v !== "S" && m.caps.nopos && !m.caps.guard && !m.offline
      ? triple(m)
          .map((b) => btn(b.icon, { label: b.act === "stop" ? b.label : b.text, off: b.act === "stop" && !m.moving, on: b.act !== "stop" && m.moving === (b.act === "open" ? "opening" : "closing"), role: ROLE, attr: { "data-act": b.act, "data-arg": m.id } }))
          .join("")
      : "";
  // a ▲■▼ row says nothing in the second line unless it moves — the name sits on the centre line
  const rowStatus = ctl && !m.moving && status === NBSP ? "" : ctl && m.moving ? coverWord(m.ty, m.moving) : status;
  return tile({
    size: "xs",
    icon: iconOf(m),
    on: m.on,
    role: ROLE,
    badge: troubleStates(m.tr),
    name: m.name,
    status: rowStatus,
    short: m.tr === "offline" ? t("common.no_data") : null,
    fill: m.caps.pos ? m.pct ?? 0 : null,
    ctl,
    cls: [ctl && "is-row", m.tr === "sending" && "is-pending", m.offline && "is-offline"].filter(Boolean).join(" "),
    attr: {
      "data-tap": "tap",
      "data-id": m.id,
      role: "button",
      tabindex: "0",
      "aria-label": `${m.name}, ${m.over}${status.trim() ? ", " + status : ""}`,
    },
  });
}

/** ↑ ■ ↓ for the whole block: the glyphs of its blinds' axis (all curtains → ⟷ ■ →←). */
export function allTriple(ms) {
  const mv = movable(ms);
  const h = mv.length && mv.every((m) => m.ty.axis === "h");
  const inv = mv.length && mv.every((m) => m.ty.inverse);
  const moving = mv.some((m) => m.moving);
  const b = (act, icon, label, o = {}) => btn(icon, { label, role: ROLE, disabled: !mv.length, attr: { "data-act": act }, ...o });
  const up = b(inv ? "all_close" : "all_open", h ? "expandH" : "up", t(inv ? "covers.all_close" : "covers.all_open"));
  const dn = b(inv ? "all_open" : "all_close", h ? "collapseH" : "down", t(inv ? "covers.all_open" : "covers.all_close"));
  const st = b("all_stop", "stop", t("covers.all_stop"), { off: !moving, disabled: !mv.some((m) => m.caps.stop) });
  return `<div class="hf5-row hf5-ctr">${up}${st}${dn}</div>`;
}

export const COVERS = {
  key: "covers",
  watch: (c, card) => [...new Set([...shown(c), ...list(c.groups), ...(card?.hass ? blockSceneWatch(card, scenes(card, true)) : [])])],
  grid: () => ({ columns: 12, rows: "auto", min_columns: 3 }),
  stub: (h) => ({ title: t("covers.title"), entities: Object.keys(h?.states || {}).filter((x) => x.startsWith("cover.")).slice(0, 6) }),
  render(card, v) {
    const title = blockTitle(card, t("covers.title"));
    const ms = all(card);
    const sc = blockSceneRow(card, scenes(card));
    if (!ms.length) return `${head({ title })}${sc}${msg(t("covers.none"), { icon: "blind" })}`;
    const s = summary(ms);
    // offline first (spec §2: «нет связи» heads the list), otherwise the config's order; the ▲■▼ rows after the windows
    const order = [...ms.filter((m) => m.offline), ...ms.filter((m) => !m.offline)];
    const tiles = [...order.filter((m) => !isRow(m, v)), ...order.filter((m) => isRow(m, v))];
    const top = head({
      title,
      value: s.avg,
      unit: "%",
      size: v === "L" ? "m" : "s",
      words: s.text,
      sub: s.moving ? tn("covers.moving_n", s.moving) : "",
      ctl: v === "S" ? "" : allTriple(ms),
    });
    const art = tiles.some((m) => !isRow(m, v));
    return `${top}${sc}<div class="hf5-grid${art ? " is-art" : " is-rows"}">${tiles.map((m) => (isRow(m, v) ? blindTile(m, v) : artTile(m))).join("")}</div>`;
  },
  sections(card, base) {
    const sc = blockSceneSection(card, scenes(card));
    return [...base().filter((x) => x.key !== "scenes"), sc].filter(Boolean);
  },
  hold: (card, el) => sceneHold(card, el),
  css:
    SCENE_CSS +
    `.hf5-grid>.hf5-tile.is-row{grid-column:1/-1;flex-wrap:wrap}
.hf5-tile.is-row>.hf5-tx{min-width:calc(var(--hf-ctl) * 1.5)}
.hf5-tile.is-row>.hf5-tc{margin-left:auto}
.hf5-hc>.hf5-ctr{flex-wrap:nowrap}
.hf5-grid.is-art{grid-template-columns:repeat(auto-fill,minmax(min(100%,calc(var(--hf-tile-min-art) * var(--hf5-k,1))),1fr))}
/* the window tile, flat (v1 calm): the open part is the role's tint, the hanging part the tile's plain surface — no hatching, frame or halo */
.hf5-tile.is-art{justify-content:space-between;min-height:var(--hf-tile-art-h);background:var(--hf-surface-2)}
.hf5-th>.hf5-tk{margin-left:auto;display:flex}
.hf5-th>.hf5-tk>.hf5-btn{width:var(--hf-ctl-s);height:var(--hf-ctl-s);min-width:var(--hf-ctl-s)}
.hf5-tile.is-moving{box-shadow:var(--hf-edge),inset 0 0 0 var(--hf-line-w) var(--hf5-role)}
.hf5-tile.is-art.is-offline{background:none;box-shadow:none;outline:var(--hf-line-w) dashed var(--hf-state-offline);outline-offset:calc(var(--hf-line-w) * -1)}
.hf5-tile.is-art.is-offline>.hf5-art{opacity:var(--hf-off-opacity);filter:grayscale(1)}
.hf5-art{position:absolute;inset:0;z-index:-1;overflow:hidden;background:var(--hf5-role-tint,var(--hf-accent-tint));pointer-events:none}
.hf5-art>i{position:absolute;display:block;z-index:1;background:linear-gradient(var(--hf-surface-2),var(--hf-surface-2)),var(--hf-surface);transition:height var(--hf-dur) var(--hf-ease),width var(--hf-dur) var(--hf-ease),opacity var(--hf-dur) var(--hf-ease)}
.hf5-art>i:nth-child(2){display:none}
/* lifting: hangs from the top by the position */
.hf5-art.is-roller>i:first-child,.hf5-art.is-roman>i:first-child,.hf5-art.is-pleated>i:first-child,.hf5-art.is-venetian>i:first-child,.hf5-art.is-shutter>i:first-child,.hf5-art.is-garage>i:first-child,.hf5-art.is-awning>i:first-child{left:0;right:0;top:0;height:calc((100 - var(--v,0)) * 1%)}
/* drawing: panels from the sides (center), or one panel from where it gathers */
.hf5-art.is-curtain>i,.hf5-art.is-tulle>i,.hf5-art.is-vertical>i,.hf5-art.is-gate>i{top:0;bottom:0;width:calc((100 - var(--v,0)) * 0.5%)}
.hf5-art.is-curtain>i:nth-child(2),.hf5-art.is-tulle>i:nth-child(2),.hf5-art.is-vertical>i:nth-child(2),.hf5-art.is-gate>i:nth-child(2){display:block}
.hf5-art>i:first-child{left:0}.hf5-art>i:nth-child(2){right:0}
.hf5-art.is-o-left>i:first-child,.hf5-art.is-o-right>i:first-child{width:calc((100 - var(--v,0)) * 1%)}
.hf5-art.is-o-left>i:nth-child(2),.hf5-art.is-o-right>i:nth-child(2){display:none!important}
.hf5-art.is-o-right>i:first-child{left:auto;right:0}
/* window / door: open or shut — the plain surface fades out when open */
.hf5-art.is-window>i:first-child{inset:0;opacity:calc(1 - var(--v,0) / 100)}
.hf5-tile.is-moving>.hf5-art>i:first-child{box-shadow:0 var(--hf-line-w) 0 var(--hf5-role)}`,
  act(card, name, arg, el) {
    const s = sceneAct(card, name, arg, el);
    if (s !== undefined) return s;
    const c = card.config;
    const h = hints(c);
    const call = (service, ids) => ({ domain: "cover", service, data: { entity_id: ids } });
    const SVC = { open: "open_cover", close: "close_cover", stop: "stop_cover" };
    if (name === "tap" || SVC[name]) {
      const id = name === "tap" ? el.dataset.id : arg;
      const m = blind(card, id, h);
      if (!m) return null;
      if (m.offline) return { more: id };
      if (name === "tap" && m.tr === "failed") return { retry: id };
      // class A: a tap never opens a gate — it stops one that moves, else the remote (open by a 1 s hold there)
      if (m.caps.guard && !(name === "tap" && m.moving && m.caps.stop)) return { more: id };
      const what = name === "tap" ? (m.known || m.moving ? tapCall(m) : null) : name;
      if (!what) return { more: id };
      return { key: id, calls: [call(SVC[what], id)] };
    }
    if (name === "all_open" || name === "all_close" || name === "all_stop") {
      const what = name.slice(4);
      const ms = movable(all(card)).filter((m) => (what === "stop" ? m.caps.stop : m.caps[what]));
      // KNX central objects (groups) move the room in one telegram when the block lists both
      const groups = list(c.entities).length ? list(c.groups).filter((id) => card.hass.states[id] && !blind(card, id, h)?.offline && !blind(card, id, h)?.caps.guard) : [];
      const ids = groups.length ? groups : ms.map((m) => m.id);
      if (!ids.length) return null;
      return { key: "all", calls: [call(SVC[what], ids)] };
    }
    return null;
  },
};
