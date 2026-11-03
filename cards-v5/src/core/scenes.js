// Shared service · scenes on the panel: KNX ones (the wall switches') first, then the integrator's HA ones, then the
// family's own (Д19, owner 28.10). Same agent endpoint and scene ids as the previous cards (agent scenes.py):
//   GET  /api/hauflo/scenes → {hidden: [ids taken off the panel], knx: {entity: scene number} (learnable), names: {entity: family's name}}
//   POST /api/hauflo/scenes {op: add|rename|recapture|delete|knx_learn|knx_name, …}
// A family scene is `scene.*` with attributes.id `hauflo_fam_<room>_<l|c>_<time>`; the integrator's `hauflo_<room>_…`.
// The room key comes from the card (`scene_room`), else from the room's own Hauflo scenes, else from their HA area.
import { clientName } from "./names.js";
import { t } from "./i18n.js";
import { CANON } from "../gen/canon.js";
import { orderKey, orderSort, houseData } from "./house.js";

/** A list's colour role (canon §5): light · blinds · both. */
export const SCENE_ROLE = { light: "state-light", cover: "state-cover", mixed: "state-on" };

const FAM = { hidden: [], knx: {}, names: {}, api: null, state: "" };
const CARDS = new Set();
const redraw = () => {
  for (const c of CARDS) {
    if (!c.isConnected) continue;
    c._html = null;
    c.render();
  }
};
/** What the agent says about scenes (loads once per page; the card redraws when it arrives). */
export function famUse(card) {
  CARDS.add(card);
  const h = card.hass;
  if (!FAM.state && h?.callApi) {
    FAM.state = "loading";
    h.callApi("GET", "hauflo/scenes")
      .then((r) => {
        FAM.hidden = Array.isArray(r?.hidden) ? r.hidden : [];
        FAM.knx = r?.knx && typeof r.knx === "object" ? r.knx : {};
        FAM.names = r?.names && typeof r.names === "object" ? r.names : {};
        FAM.api = true;
      })
      .catch(() => (FAM.api = false)) // an HA not restarted since the update, or no agent: nothing to save scenes with
      .finally(() => ((FAM.state = "done"), redraw()));
  }
  return FAM;
}
/** A scene was saved / renamed / removed: ask again (HA reloads its scenes — the new entity shows a moment later). */
export function famReload(card, settle = 400) {
  FAM.state = "";
  famUse(card);
  setTimeout(redraw, settle);
}
/** The agent's answer to a change → {ok} | {error: words for the person}. */
export async function famCall(h, body, fallback) {
  try {
    return { ok: await h.callApi("POST", "hauflo/scenes", body) };
  } catch (e) {
    const m = e?.body?.message || e?.message || (typeof e === "string" ? e : "");
    return { error: m && !/^\d{3}/.test(m) ? m : fallback };
  }
}
/** Family's name of a KNX scene on this panel (agent `names`) — kept here at once after a rename. */
export const famName = (id, name) => {
  FAM.names = { ...FAM.names };
  if (name) FAM.names[id] = name;
  else delete FAM.names[id];
};

// ── which scene is what ──
const ALL = new WeakMap(); // once per states object (HA replaces it on any change), not a walk per card and update
/** Every family scene of the house, oldest first. */
export function allFamily(h) {
  const states = h?.states;
  if (!states) return [];
  if (!ALL.has(states))
    ALL.set(
      states,
      Object.values(states)
        .filter((s) => s.entity_id.startsWith("scene.") && String(s.attributes?.id || "").startsWith("hauflo_fam_"))
        .sort((a, b) => String(a.attributes.id).localeCompare(String(b.attributes.id))),
    );
  return ALL.get(states);
}
const famPrefix = (room, kind) => `hauflo_fam_${String(room).replace(/[^a-z0-9]/g, "")}_${kind === "cover" ? "c" : "l"}_`;
/** The room's family scenes of a kind (light | cover). */
export const familyScenes = (h, room, kind) => (room ? allFamily(h).filter((s) => String(s.attributes.id).startsWith(famPrefix(room, kind))).map((s) => ({ entity: s.entity_id })) : []);

/** "knx" | "ha" | "fam" */
export function sceneSource(h, id) {
  if (h?.entities?.[id]?.platform === "knx") return "knx";
  return /^hauflo_fam_/.test(String(h?.states?.[id]?.attributes?.id || "")) ? "fam" : "ha";
}
const RANK = { knx: 0, ha: 1, fam: 2 };
/** A block's scenes in their base order (stable within a source) — the client's «Порядок» goes over it. */
export const bySource = (h, arr) =>
  (arr || [])
    .map((x, i) => [x, RANK[sceneSource(h, x.entity)] * 1e4 + i])
    .sort((a, b) => a[1] - b[1])
    .map(([x]) => x);
/** The scene's name on the panel: the family's own for a KNX scene, else the card's, else HA's. */
export const sceneLabel = (h, x) => FAM.names[x.entity] || x.name || clientName(h?.states?.[x.entity]?.attributes?.friendly_name, x.entity);

// ── the room key without a redeploy (owner 28.10: a dashboard built before the agent could save scenes has no `scene_room`) ──
const ROOM_OF_SCENE = /^(?:hauflo|easyha)_(?:fam_)?([a-z0-9]{1,12})_/;
const roomOfScene = (s) => ROOM_OF_SCENE.exec(String(s?.attributes?.id || ""))?.[1];
const areaOf = (h, id) => {
  const e = h?.entities?.[id];
  return e?.area_id || (e?.device_id && h?.devices?.[e.device_id]?.area_id) || null;
};
const AREA_ROOM = new WeakMap();
/** HA area → the room key of the Hauflo scenes in it. */
function areaRooms(h) {
  const states = h?.states;
  if (!states) return new Map();
  if (!AREA_ROOM.has(states)) {
    const m = new Map();
    for (const s of Object.values(states)) {
      if (!s.entity_id.startsWith("scene.")) continue;
      const room = roomOfScene(s);
      const area = room && areaOf(h, s.entity_id);
      if (area && !m.has(area)) m.set(area, room);
    }
    AREA_ROOM.set(states, m);
  }
  return AREA_ROOM.get(states);
}
const ids = (list) => (Array.isArray(list) ? list.map((e) => (typeof e === "string" ? e : e?.entity)).filter(Boolean) : []);
/** The room key of a scene list: the config's, else its scenes', else its lights' / blinds' area's. */
export function sceneRoom(h, configured, scenes, entities) {
  if (configured) return String(configured);
  if (!h) return undefined;
  for (const x of scenes || []) {
    const r = roomOfScene(h.states?.[x.entity]);
    if (r) return r;
  }
  const rooms = areaRooms(h);
  for (const id of [...ids(entities), ...(scenes || []).flatMap((x) => h.states?.[x.entity]?.attributes?.entity_id || [])]) {
    const area = areaOf(h, id);
    if (area && rooms.has(area)) return rooms.get(area);
  }
  return undefined;
}
const isGroup = (h, id) => Array.isArray(h?.states?.[id]?.attributes?.entity_id);
/** What «Запомнить как сейчас» takes: the block's own lights / blinds, one by one (a group would move twice);
 *  else what its scenes hold, else the room's HA area. src = {scenes, capture, entities}. */
export function captureList(h, src, kind, room) {
  const want = kind === "cover" ? "cover." : "light.";
  const own = [...new Set([...ids(src.entities), ...ids(src.capture)].filter((id) => id.startsWith(want)))];
  if (own.length) return own;
  const held = (src.scenes || []).flatMap((x) => h?.states?.[x.entity]?.attributes?.entity_id || []).filter((id) => id.startsWith(want));
  const areas = new Set(held.map((id) => areaOf(h, id)).filter(Boolean));
  if (!areas.size && room)
    for (const s of Object.values(h?.states || {})) if (s.entity_id.startsWith("scene.") && roomOfScene(s) === room) areas.add(areaOf(h, s.entity_id));
  const inArea = Object.keys(h?.entities || {}).filter((id) => id.startsWith(want) && areas.has(areaOf(h, id)) && !h.entities[id].hidden);
  return [...new Set([...held, ...inArea])].filter((id) => !isGroup(h, id));
}

/** One scene list of a card (a row of the room's top card, the scenes card, a light / blinds block). */
export function sceneList(card, { scenes, kind, room }) {
  const h = card.hass;
  const fam = famUse(card);
  const list = (scenes || []).filter((x) => x && typeof x.entity === "string");
  const own = kind === "mixed" ? [] : familyScenes(h, room, kind).filter((x) => !list.some((y) => y.entity === x.entity));
  const hidden = new Set(fam.hidden);
  const key = card.isConnected ? orderKey(card) : "";
  return orderSort(key ? houseData("order")[key] : undefined, bySource(h, [...list, ...own]), (x) => x.entity).filter((x) => h?.states?.[x.entity] && !hidden.has(x.entity));
}
/** Can this scene be changed from the panel: a learnable KNX scene (agent says), or a Hauflo one of a known room. */
export function sceneEdit(h, id, room) {
  const src = sceneSource(h, id);
  if (src === "knx") return FAM.api === true && FAM.knx[id] !== undefined ? "knx" : "";
  return /^hauflo_/.test(String(h?.states?.[id]?.attributes?.id || "")) && room ? (src === "fam" ? "fam" : "ha") : "";
}
/** The family may add scenes here (the agent can save them and the room is known). */
export const sceneAddable = (kind, room) => FAM.api === true && !!room && kind !== "mixed";
/** Family scenes a card shows — watched, so one saved on another phone shows here too. */
export const famWatched = (h, rooms) => (rooms.length ? allFamily(h).filter((s) => rooms.some((r) => String(s.attributes.id).startsWith(`hauflo_fam_${r}_`))).map((s) => s.entity_id) : []);

// «Открыть Шторы» / «Закрыть Тюль»: blind scenes of two or more curtain types read as a labelled row per type
const VERB = new RegExp(`^(${CANON.cover.scene_verbs.join("|")})\\s+(.+)$`, "i");
const upper = (s) => s.charAt(0).toUpperCase() + s.slice(1);

/** The list's groups: [{label, items: [{x, short}]}] — KNX series (≥ 2) with subheaders and «Мои сцены», or blind
 *  types, or one group. The last group takes «+ Сцена». */
export function sceneGroups(h, all, kind) {
  if (all.some((x) => x.series)) {
    // `series_head: false` — the room's own only series: no subheader, first
    const bare = (n) => all.some((x) => x.series === n && x.series_head === false);
    const names = [...new Set(all.filter((x) => x.series).map((x) => x.series))].sort((a, b) => Number(!bare(a)) - Number(!bare(b)));
    const out = names.map((n) => ({ label: bare(n) ? "" : n, items: all.filter((x) => x.series === n).map((x) => ({ x })) }));
    out.push({ label: t("scenes.mine"), items: all.filter((x) => !x.series).map((x) => ({ x })) });
    return out;
  }
  if (kind === "cover") {
    const parsed = all.map((x) => {
      const m = VERB.exec(sceneLabel(h, x));
      return m ? { x, short: upper(m[1].toLowerCase()), type: m[2].trim() } : null;
    });
    const types = [...new Set(parsed.filter(Boolean).map((p) => p.type.toLowerCase()))];
    if (types.length >= 2) {
      const rest = all.filter((_, i) => !parsed[i]).map((x) => ({ x }));
      const rows = types.map((ty) => {
        const mine = parsed.filter((p) => p && p.type.toLowerCase() === ty);
        return { label: upper(mine[0].type), items: mine.map(({ x, short }) => ({ x, short })) };
      });
      return rest.length ? [{ label: "", items: rest }, ...rows] : rows;
    }
  }
  return [{ label: "", items: all.map((x) => ({ x })) }];
}

/** Tests: forget everything loaded. */
export function _resetScenes() {
  Object.assign(FAM, { hidden: [], knx: {}, names: {}, api: null, state: "" });
  CARDS.clear();
}
