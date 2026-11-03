// Shared service · the house layer: what the people of the house chose over the integrator's dashboard.
//   titles — block names for the whole house (agent /api/hauflo/titles; a deploy rewrites the dashboard, names stay)
//   order  — item order of a block for the whole house (agent /api/hauflo/order)
//   view   — each person's own view: scenes on top / in their blocks, climate and floor large / a line (HA user data)
// Same keys, storage and endpoints as the previous cards: the old settings sheet still edits them and fires a
// `hauflo-house` window event, so every v5 card redraws at once. A card only calls the helpers below.

const obj = (x) => (x && typeof x === "object" && !Array.isArray(x) ? x : {});
const STORES = {
  titles: { ls: "hauflo_titles", get: (h) => h.callApi("GET", "hauflo/titles").then((r) => obj(r?.titles)) },
  order: { ls: "hauflo_order", get: (h) => h.callApi("GET", "hauflo/order").then((r) => obj(r?.order)) },
  view: { ls: "hauflo_view", get: (h) => h.callWS({ type: "frontend/get_user_data", key: "hauflo_view" }).then((r) => obj(r?.value)) },
};
const S = Object.fromEntries(Object.keys(STORES).map((k) => [k, { data: null, state: "", ok: false }]));
const CARDS = new Set();

const readLs = (k) => {
  try {
    return obj(JSON.parse(localStorage.getItem(STORES[k].ls) || "null"));
  } catch (e) {
    return {}; // private mode
  }
};
const writeLs = (k) => {
  try {
    localStorage.setItem(STORES[k].ls, JSON.stringify(S[k].data || {}));
  } catch (e) {
    /* private mode */
  }
};
const redraw = () => {
  for (const c of CARDS) {
    if (!c.isConnected) continue;
    c._html = null;
    c._ocfg = null;
    c.render();
  }
};
// The old settings sheet (same page) and other tabs change the stores: re-read and redraw.
if (typeof window !== "undefined") {
  const reread = (k) => k && S[k] && ((S[k].data = readLs(k)), redraw());
  window.addEventListener("hauflo-house", (e) => e.detail?.from !== "v5" && reread(e.detail?.store));
  window.addEventListener("storage", (e) => reread(Object.keys(STORES).find((k) => STORES[k].ls === e.key)));
}

/** A store's data for this card (loads once per page; the card redraws when it arrives). */
function use(card, k) {
  CARDS.add(card);
  const s = S[k];
  const h = card.hass;
  if (!s.state && h && (k === "view" ? h.callWS : h.callApi)) {
    s.state = "loading";
    s.data = readLs(k);
    STORES[k]
      .get(h)
      .then((d) => ((s.data = d), (s.ok = true)))
      .catch(() => {}) // an older agent or no link: the integrator's choice, nothing to change it with
      .finally(() => ((s.state = "done"), writeLs(k), redraw()));
  }
  return s.data || {};
}
/** A store's data as loaded (no load started). */
export const houseData = (k) => S[k].data || {};
/** Whether the agent answered for a store (the order sheet is offered only then). */
export const houseOk = (k) => S[k].ok;
/** Save a store and redraw every card (the settings / order sheets). */
export async function houseSet(h, k, value, key) {
  if (k === "titles") S.titles.data = obj((await h.callApi("POST", "hauflo/titles", { titles: value }))?.titles);
  else if (k === "order") S.order.data = obj((await h.callApi("POST", "hauflo/order", { key, items: value }))?.order);
  else {
    S.view.data = obj(value);
    await h.callWS({ type: "frontend/set_user_data", key: "hauflo_view", value: S.view.data });
  }
  writeLs(k);
  redraw();
  window.dispatchEvent(new CustomEvent("hauflo-house", { detail: { store: k, from: "v5" } }));
}

// ── titles ──
/** A block's title: the client's name for the block, else the config's title (only for the card's own block), else
 *  the card's word. kind = the block the title belongs to (light_scenes, cover_scenes, or the card's `block`). */
export function blockTitle(card, fallback, kind = card.config.block) {
  const own = kind && use(card, "titles")[kind];
  if (typeof own === "string" && own.trim()) return own;
  return (kind === card.config.block && typeof card.config.title === "string" && card.config.title) || fallback;
}

// ── view ──
/** The person's choice for a field (scenes, cover_scenes, climate, floor), or undefined. */
export const viewPref = (card, field) => use(card, "view")[field];
/** Where scenes of a kind go, "blocks" or "top": the person's choice, else the card's layout (blind scenes fall back to
 *  the one choice older versions kept for both). */
export function sceneWhere(card, kind) {
  const p = use(card, "view");
  const c = card.config;
  return (kind === "cover" ? p.cover_scenes || p.scenes || c.cover_scene_layout || c.scene_layout : p.scenes || c.scene_layout) || "blocks";
}
/** A thermostat as one line: the person's choice for its size class (floor | climate), else the card's `compact`. */
export function climateCompact(card) {
  const c = card.config;
  if (c.popup) return false;
  const pref = c.size_class ? viewPref(card, c.size_class === "floor" ? "floor" : "climate") : undefined;
  return pref ? pref === "compact" : !!c.compact;
}

// ── order ──
const ORDER_TYPES = new Set(["lights", "covers", "sliders", "scenes", "climate", "acs", "devices", "status", "area", "alert"]);
const ORDER_FIELDS = ["groups", "entities", "zones", "scenes", "alarms", "sensors"];
const itemId = (x) => (typeof x === "string" ? x : typeof x?.entity === "string" ? x.entity : undefined);
/** The card's slot on its view — the same as the agent's (order.py `slot`). */
export function orderSlot(c) {
  const type = String(c.type).replace(/^custom:hauflo-/, "");
  if (type === "room" && c.path) return "room:tiles";
  const title = typeof c.title === "string" ? c.title : "";
  // untitled light and blind sliders of one room are two blocks: by the domain of their first item
  const first = !title && Array.isArray(c.entities) ? itemId(c.entities[0]) : undefined;
  const k = [c.block, c.kind, Array.isArray(c.zones) && c.zones.length ? "zones" : "", c.area, typeof first === "string" ? first.split(".")[0] : ""].find((x) => typeof x === "string" && x) || "";
  return `${type}:${k}:${title}`;
}
/** The block's key: only on a Hauflo dashboard's view (not a copy, not the default dashboard). */
export function orderKey(card, path = typeof location !== "undefined" ? location.pathname : "") {
  const c = card._cfg;
  if (!c || typeof c.type !== "string") return "";
  const type = c.type.replace(/^custom:hauflo-/, "");
  if (!ORDER_TYPES.has(type) && !(type === "room" && c.path)) return "";
  let dash = "";
  let view = "";
  try {
    const p = path.split("/");
    dash = decodeURIComponent(p[1] || "");
    view = decodeURIComponent(p[2] || "");
  } catch (e) {
    return ""; // a broken URL: no order
  }
  return /^hauflo-/.test(dash) && !/-copy-\d/.test(dash) && view ? `${dash}|${view}|${orderSlot(c)}` : "";
}
/** A list in the block's order: the kept ones first, as kept; the rest after them, as they came. */
export function orderSort(list, arr, idOf = itemId) {
  if (!Array.isArray(list) || !list.length || !Array.isArray(arr)) return arr;
  const rank = new Map(list.map((id, i) => [id, i]));
  return arr
    .map((x, i) => [x, rank.has(idOf(x)) ? rank.get(idOf(x)) : 1e6 + i])
    .sort((a, b) => a[1] - b[1])
    .map(([x]) => x);
}
/** The card's config with its lists in the block's order (a copy, cached until the config or the order changes). */
export function ordered(card) {
  const c = card._cfg || {};
  const key = card.isConnected ? orderKey(card) : "";
  const list = key ? use(card, "order")[key] : undefined;
  const sig = Array.isArray(list) && list.length ? `${key}\n${list.join("\n")}` : "";
  if (card._ocfg && card._ocfg.raw === c && card._ocfg.sig === sig) return card._ocfg.out;
  let out = c;
  if (sig) {
    out = { ...c };
    for (const f of ORDER_FIELDS) if (Array.isArray(c[f])) out[f] = orderSort(list, c[f]);
    if (Array.isArray(c.rows)) out.rows = c.rows.map((r) => (r && Array.isArray(r.scenes) ? { ...r, scenes: orderSort(list, r.scenes) } : r));
  }
  card._ocfg = { raw: c, sig, out };
  return out;
}
/** Ids a card finds itself (an area's entities) in the block's order. */
export const orderIds = (card, ids) => orderSort(use(card, "order")[orderKey(card)], ids, (x) => x);

// ── the house's settings (agent /api/hauflo/house): who may change the house, each person's favourites ──
const INFO = { data: null, at: 0, wait: null };
const INFO_TTL = { ok: 300000, none: 60000 }; // asked again: the service contact or a profile changes
/** The house's settings, fetched once in a while (null: an older agent or no link). */
export async function houseInfo(h) {
  if (INFO.wait) return INFO.wait;
  if (INFO.data && Date.now() - INFO.at < INFO_TTL.ok) return INFO.data;
  if (!h?.callApi || (INFO.at && !INFO.data && Date.now() - INFO.at < INFO_TTL.none)) return INFO.data;
  INFO.at = Date.now();
  INFO.wait = h
    .callApi("GET", "hauflo/house")
    .then((d) => (INFO.data = obj(d)), () => INFO.data)
    .finally(() => (INFO.wait = null));
  return INFO.wait;
}
/** Whether this person may change the house (a child profile may not); no answer → HA admins only. */
export async function canEdit(h) {
  const d = await houseInfo(h);
  return typeof d?.can_edit === "boolean" ? d.can_edit : !!h?.user?.is_admin;
}
/** The same, as known now (to draw «+ Сцена»): the answer when it came, else HA admins. */
export const canEditNow = (h) => (typeof INFO.data?.can_edit === "boolean" ? INFO.data.can_edit : !!h?.user?.is_admin);
/** This person's favourites (entity ids on top of «Главная»). */
export const favHas = (id) => (INFO.data?.favorites?.items || []).includes(id);
/** Add / remove a favourite (saved by the agent per HA user); the previous cards take the new settings too. */
export async function favToggle(h, id) {
  await houseInfo(h);
  const cur = obj(INFO.data?.favorites);
  const items = Array.isArray(cur.items) ? cur.items : [];
  const r = await h.callApi("POST", "hauflo/house", { favorites: { ...cur, items: items.includes(id) ? items.filter((x) => x !== id) : [...items, id] } });
  INFO.data = { ...obj(INFO.data), favorites: r?.favorites };
  window.dispatchEvent(new CustomEvent("hauflo-house", { detail: { store: "house", from: "v5", data: INFO.data } }));
  return r?.favorites;
}

/** Tests: forget everything loaded. */
export function _resetHouse() {
  for (const k of Object.keys(S)) S[k] = { data: null, state: "", ok: false };
  CARDS.clear();
  Object.assign(INFO, { data: null, at: 0, wait: null });
}
