// hauflo-scenes · scenes of a room as buttons in a fixed order (lighting spec §5, house spec §5).
// Config (unchanged from the previous card):
//   rows: [{kind: light|cover|mixed, title, scenes, scene_room, capture, always}] — the room's top card: a labelled row per
//     kind, shown when its scenes go on top (the integrator's `scene_layout` or the person's choice) or `always`;
//     no row left → the card hides itself (HA's own way) and shows again when one comes back.
//   or kind + scenes (+ scene_room, capture) — one list: «Сценарии света / штор / Сцены».
// Each list: KNX → HA → the family's own, «+ Сцена» for the family (scene-row.js).
import { t } from "../core/i18n.js";
import { cap, head, msg } from "../kit/primitives.js";
import { hideCard } from "../core/dom.js";
import { blockTitle, sceneWhere } from "../core/house.js";
import { clientName } from "../core/names.js";
import { sceneRoom, sceneList, famWatched } from "../core/scenes.js";
import { sceneRow, sceneCount, sceneAct, sceneHold, SCENE_CSS } from "./scene-row.js";

const arr = (x) => (Array.isArray(x) ? x : []);
const scenesOf = (x) => arr(x).filter((s) => s && typeof s.entity === "string");
const kindOf = (k) => (k === "cover" || k === "mixed" ? k : "light");

/** The card's scene lists: [{kind, title, scenes, room, capture, entities}] — rows (filtered by where scenes go) or one. */
function lists(card, all = false) {
  const c = card.config;
  const h = card.hass;
  if (Array.isArray(c.rows))
    return c.rows
      .filter((r) => r && (all || r.always || sceneWhere(card, kindOf(r.kind)) === "top"))
      .map((r) => {
        const kind = kindOf(r.kind);
        const scenes = scenesOf(r.scenes);
        return { kind, title: r.title || t(`scenes.${kind}`), scenes, capture: r.capture, room: kind === "mixed" ? undefined : sceneRoom(h, r.scene_room, scenes, r.capture) };
      });
  const kind = kindOf(c.kind);
  const scenes = scenesOf(c.scenes);
  return [{ kind, scenes, capture: c.capture, entities: c.entities, room: kind === "mixed" ? undefined : sceneRoom(h, c.scene_room, scenes, [...arr(c.entities), ...arr(c.capture)]) }];
}
export const SCENES = {
  key: "scenes",
  watch(c, card) {
    const ids = [...scenesOf(c.scenes), ...arr(c.rows).flatMap((r) => scenesOf(r?.scenes))].map((x) => x.entity);
    const rooms = card?.hass ? lists(card, true).map((l) => l.room).filter(Boolean) : [];
    return [...new Set([...ids, ...famWatched(card?.hass, rooms)])];
  },
  grid: () => ({ columns: 12, rows: "auto", min_columns: 3 }),
  stub: (h) => ({ kind: "light", scenes: Object.keys(h?.states || {}).filter((x) => x.startsWith("scene.")).slice(0, 4).map((entity) => ({ entity })) }),
  render(card, v) {
    const c = card.config;
    const ls = lists(card);
    const rows = Array.isArray(c.rows);
    const kind = rows ? (ls.length === 1 ? ls[0].kind : "light") : ls[0].kind;
    const word = rows ? t("scenes.title") : t(`scenes.${kind}_title`);
    const title = blockTitle(card, word);
    const body = ls.map((l, i) => {
      const html = sceneRow(card, l, i);
      return html && rows ? `<div class="hf5-scr">${ls.length > 1 || l.kind !== "light" ? cap(l.title) : ""}${html}</div>` : html;
    });
    const shown = body.filter(Boolean);
    if (rows) {
      hideCard(card, !shown.length);
      if (!shown.length) return "";
    }
    return `${head({ title })}${shown.length ? shown.join("") : msg(t("scenes.none"), { icon: "scene" })}`;
  },
  /** The order sheet lists what the card shows — the family's own scenes too. */
  sections(card) {
    const h = card.hass;
    return lists(card, true)
      .map((l) => ({ label: l.title || t(`scenes.${l.kind}_title`), items: sceneList(card, l).map((x) => ({ id: x.entity, name: x.name || clientName(h.states[x.entity]?.attributes?.friendly_name, x.entity) })) }))
      .filter((s) => s.items.length);
  },
  act: (card, name, arg, el) => sceneAct(card, name, arg, el) ?? null,
  hold: (card, el) => sceneHold(card, el),
  css: `${SCENE_CSS}
.hf5-scr{display:flex;flex-direction:column;gap:var(--hf-sp-2);min-width:0}
.hf5-scr+.hf5-scr{margin-top:var(--hf-sp-1)}
.hf5-scr>.hf5-cap{color:var(--hf-ink-2)}`,
};
export { sceneCount };
