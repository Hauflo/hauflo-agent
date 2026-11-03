// Scene buttons of one list (lighting spec §5, house spec §5) — the scenes card and the light / blinds blocks share them.
// No «active scene»: a scene is a moment, not a state. A tap runs it: the button rings while it goes, then glows in its
// role for ack_glow and HA's toast says «Сценарий «X» включён»; failed → a warn ring + toast, a tap runs it again;
// no link → muted, a tap only says so. Order: KNX → the integrator's HA → the family's, then the block's «Порядок».
// Hold: the scene's sheet (rename / remember again / remove, favourites); «+ Сцена» adds the family's own.
import { t } from "../core/i18n.js";
import { notify } from "../core/dom.js";
import { cap, pill } from "../kit/primitives.js";
import { CANON } from "../gen/canon.js";
import { canEditNow, blockTitle, sceneWhere } from "../core/house.js";
import { clientName } from "../core/names.js";
import { sceneList, sceneLabel, sceneSource, sceneEdit, sceneAddable, sceneGroups, sceneRoom, famWatched, SCENE_ROLE } from "../core/scenes.js";
export { SCENE_ROLE };
import { openScene } from "../sheets/scene.js";

const kindOf = (k) => (k === "cover" || k === "mixed" ? k : "light");
const scenesOf = (x) => (Array.isArray(x) ? x.filter((s) => s && typeof s.entity === "string") : []);
/** One scene button. */
function sceneBtn(card, x, short, kind, n, room) {
  const h = card.hass;
  const id = x.entity;
  const st = h.states[id];
  const label = sceneLabel(h, x);
  const cmd = card.cmd.of(id);
  const off = st.state === "unavailable";
  const ack = card._ack?.id === id && card._ack.until > Date.now();
  const src = sceneSource(h, id);
  const admin = h.user?.is_admin === true;
  return pill(short || label, {
    icon: cmd.failed ? "failed" : kind === "cover" ? "blind" : "scene",
    role: SCENE_ROLE[kind],
    off,
    tag: admin ? t(src === "knx" ? "scenes.knx" : "scenes.ha") : "",
    cls: ["hf5-scb", cmd.sending && "is-pending", cmd.failed && "is-failed", ack && !cmd.sending && !cmd.failed && "is-ack"].filter(Boolean).join(" "),
    label: short ? `${label}` : null,
    attr: { "data-tap": "scene", "data-id": id, "data-arg": n, "data-edit": sceneEdit(h, id, room) || null, title: label },
  });
}

/** A scene list as markup. src = {scenes, kind, room, capture, entities}; n = its index on the card (the sheets find
 *  the list by it). Empty (nothing to show, nothing to add) → "". */
export function sceneRow(card, src, n = 0) {
  const kind = kindOf(src.kind);
  (card._scenes ||= [])[n] = { ...src, kind };
  const all = sceneList(card, { ...src, kind });
  const add = sceneAddable(kind, src.room) && canEditNow(card.hass);
  if (!all.length && !add) return "";
  const groups = sceneGroups(card.hass, all, kind);
  const plus = add ? pill(t("scenes.add"), { icon: "plus", cls: "hf5-scadd", label: t("scenes.add_label"), attr: { "data-act": "scene_add", "data-arg": n } }) : "";
  return `<div class="hf5-scs">${groups
    .map((g, i) => {
      const last = i === groups.length - 1;
      if (!g.items.length && !(last && plus)) return "";
      return `<div class="hf5-scg">${g.label ? cap(g.label) : ""}<div class="hf5-scn">${g.items.map(({ x, short }) => sceneBtn(card, x, short, kind, n, src.room)).join("")}${last ? plus : ""}</div></div>`;
    })
    .join("")}</div>`;
}
/** How many scenes a list shows (the card hides itself when no list has any). */
export const sceneCount = (card, src) => sceneList(card, { ...src, kind: kindOf(src.kind) }).length;

/** A scene tap / «+ Сцена» → the card's command (or a sheet). */
export function sceneAct(card, name, arg, el) {
  const src = card._scenes?.[Number(arg) || 0];
  if (name === "scene_add") {
    if (src) openScene(card, null, src);
    return null;
  }
  if (name !== "scene") return undefined;
  const h = card.hass;
  const id = el.dataset.id;
  const st = h.states[id];
  if (!st || card.cmd.of(id).sending) return null;
  const label = sceneLabel(h, { entity: id, name: (src?.scenes || []).find((x) => x.entity === id)?.name });
  if (st.state === "unavailable") return notify(card, t("scenes.offline", { name: label })), null;
  return {
    key: id,
    calls: [{ domain: id.startsWith("script.") ? "script" : "scene", service: "turn_on", data: { entity_id: id } }],
    after: (ok) => {
      if (!ok) return notify(card, t("scenes.failed"));
      card._ack = { id, until: Date.now() + CANON.timing.ack_glow };
      card._html = null;
      card.render();
      setTimeout(() => ((card._html = null), card.render()), CANON.timing.ack_glow);
      notify(card, t("scenes.on", { name: label }));
    },
  };
}
/** Hold on a scene → its sheet (any scene: favourites, «Подробнее»; Hauflo's and learnable KNX ones: edit too). */
export function sceneHold(card, el) {
  if (!el.matches(".hf5-scb")) return false;
  openScene(card, el.dataset.id, card._scenes?.[Number(el.dataset.arg) || 0] || { kind: "light" }, el.dataset.edit || "");
  return true;
}

// ── a light / blinds block's own scenes (lighting spec §4: «сцены, если клиент выбрал „в блоке“», above the lamps) ──
/** The block's scene list {kind, scenes, room, entities} (entities = its lamps / blinds: what «+ Сцена» remembers), or
 *  null: none to show or add, or they go on top (the room's scenes card). `all` — wherever they go (watch, order sheet). */
export function blockScenes(card, kind, entities, all = false) {
  const c = card.config;
  const scenes = scenesOf(c.scenes);
  const room = sceneRoom(card.hass, c.scene_room, scenes, entities);
  if (!scenes.length && !room) return null;
  if (!all && sceneWhere(card, kind) !== "blocks") return null;
  return { kind, scenes, room, entities };
}
/** The block's scene row with its caption, or "". */
export function blockSceneRow(card, src) {
  const row = src ? sceneRow(card, src, 0) : "";
  return row ? `<div class="hf5-bsc">${cap(blockTitle(card, t(`scenes.${src.kind}_title`), `${src.kind}_scenes`))}${row}</div>` : "";
}
/** What the block's scenes need watched: its scenes and the family's of its room. */
export const blockSceneWatch = (card, src) => (src ? [...src.scenes.map((x) => x.entity), ...famWatched(card.hass, [src.room].filter(Boolean))] : []);
/** The order sheet's section of the block's scenes (the family's own too), or null. */
export function blockSceneSection(card, src) {
  if (!src) return null;
  const h = card.hass;
  const items = sceneList(card, src).map((x) => ({ id: x.entity, name: x.name || clientName(h.states[x.entity]?.attributes?.friendly_name, x.entity) }));
  return items.length ? { key: "scenes", label: t(`scenes.${src.kind}_title`), items } : null;
}

export const SCENE_CSS = `
.hf5-scs{display:flex;flex-direction:column;gap:var(--hf-sp-3);min-width:0}
.hf5-scg{display:flex;flex-direction:column;gap:var(--hf-sp-2);min-width:0}
.hf5-scg>.hf5-cap{color:var(--hf-ink-3)}
.hf5-scn{display:flex;flex-wrap:wrap;gap:var(--hf-sp-2);min-width:0}
.hf5-scn>.hf5-pill{flex:0 1 auto;max-width:100%;min-width:0;padding:0 var(--hf-sp-4) 0 var(--hf-sp-3);touch-action:pan-y;user-select:none;-webkit-user-select:none}
/* v1 look: a scene is a glass chip with the edge (not an outline), ink text, the icon in its family's colour */
.hf5-scb{background:var(--hf-surface-glass);box-shadow:var(--hf-edge),inset 0 0 0 var(--hf-line-w) var(--hf-hair);color:var(--hf-ink)}
.hf5-scb>.hf5-g{color:var(--hf5-role,var(--hf-ink-2))}
.hf5-scb.is-pending{box-shadow:inset 0 0 0 var(--hf-focus-w) var(--hf-accent)}
.hf5-scb.is-ack{background:var(--hf5-role-tint)}
.hf5-scb.is-ack>.hf5-g{color:var(--hf5-role)}
.hf5-scb.is-failed{box-shadow:inset 0 0 0 var(--hf-focus-w) var(--hf-state-warn)}
.hf5-scb.is-failed>.hf5-g{color:var(--hf-state-warn-text)}
.hf5-scadd{background:none;box-shadow:inset 0 0 0 var(--hf-line-w) var(--hf-line);color:var(--hf-ink-2)}
.hf5-card.is-s .hf5-scn{display:grid;grid-template-columns:repeat(2,minmax(0,1fr))}
.hf5-card.is-s .hf5-scn>.hf5-pill{flex-direction:column;height:auto;min-height:var(--hf-hit-l);padding:var(--hf-sp-2);gap:var(--hf-sp-1);min-width:0}
.hf5-card.is-s .hf5-scn>.hf5-pill>span{max-width:100%}
.hf5-bsc{display:flex;flex-direction:column;gap:var(--hf-sp-2);min-width:0}
.hf5-bsc>.hf5-cap{color:var(--hf-ink-2)}`;
