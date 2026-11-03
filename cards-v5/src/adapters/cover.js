// Domain adapter · cover → ONE normalized model + the controls its remote needs (covers spec v5 §1, §4).
// The type (device_class) decides the glyphs, the triple's order and the words; the canon data is in kit.json → cover.
// supported_features: 1 open · 2 close · 4 set_position · 8 stop · 128 set_tilt_position.
import { t } from "../core/i18n.js";
import { CANON } from "../gen/canon.js";
import { base } from "./common.js";
import { ICONS } from "../core/icons.js";

const F = { open: 1, close: 2, pos: 4, stop: 8, tilt: 128 };
/** Covers the config says have no position status (KNX without a position status address): the block registers them,
 *  the remote reads them — a time estimate is never shown as a position. */
export const noPosition = new Set();

/** The HA type: the config's subtype decides it when set (a «curtain» with no device_class), else device_class. */
export const coverType = (st, hint = {}) => {
  const T = CANON.cover.types;
  const dc = CANON.cover.subtypes[hint.kind]?.base || st?.attributes?.device_class;
  const key = T[dc] ? dc : CANON.cover.default_type;
  return { key, ...T[key] };
};

/** Ателье v2 · what hangs on the window: subtype (icon, drawing) and the way a curtain opens (center | left | right).
 *  hint = the config's {kind, opening}; a glyph the set lacks falls back to the type's. */
export const coverSub = (ty, hint = {}) => {
  const C = CANON.cover;
  const key = C.subtypes[hint.kind] && C.subtypes[hint.kind].base === ty.key ? hint.kind : C.by_class[ty.key];
  const sub = C.subtypes[key] || {};
  const opening = C.openings.includes(hint.opening) ? hint.opening : sub.opening || null;
  const icon = [C.opening_icons[sub.base]?.[opening], sub.icon].find((i) => i && ICONS[i]) || ty.icon;
  return { key, art: sub.art || null, opening, icon };
};

/** The words of one cover: verb (button) and state (status) by its type's locale groups. */
export const coverWord = (ty, w) => t(`cover.state.${ty.state}.${w}`);
export const coverVerb = (ty, w) => t(`cover.verb.${ty.verb}.${w}`);

export function coverModel(st, hint = {}) {
  const a = st.attributes || {};
  const m = base(st, "cover");
  const f = Number(a.supported_features) || 0;
  const ty = coverType(st, hint);
  const sub = coverSub(ty, hint);
  const pos = !!(f & F.pos) && a.current_position != null && !noPosition.has(st.entity_id);
  const moving = st.state === "opening" ? "opening" : st.state === "closing" ? "closing" : null;
  const pct = pos ? Math.round(+a.current_position) : null;
  const open = st.state === "open" || (pct != null && pct > 0);
  const known = !m.offline && (moving || st.state === "open" || st.state === "closed");
  return {
    ...m,
    kind: "cover",
    type: ty.key,
    ty,
    sub,
    over: t(`cover.type.${ty.key}`),
    icon: sub.icon,
    role: "state-cover",
    on: !m.offline && (open || !!moving),
    open,
    moving,
    known: !!known,
    pct,
    tilt: f & F.tilt && a.current_tilt_position != null ? Math.round(+a.current_tilt_position) : null,
    caps: { pos, nopos: !pos, stop: !!(f & F.stop), open: !!(f & F.open) || !f, close: !!(f & F.close) || !f, tilt: !!(f & F.tilt), guard: !!ty.guard },
  };
}

/** The triple in the type's order: [toward-open-or-up, stop, the other] — vertical ↑ ■ ↓, horizontal ⟷ ■ →←; an awning's ↓ opens. */
export function triple(m) {
  const h = m.ty.axis === "h";
  const op = { act: "open", icon: h ? "expandH" : m.ty.inverse ? "down" : "up", text: coverVerb(m.ty, "open") };
  const cl = { act: "close", icon: h ? "collapseH" : m.ty.inverse ? "up" : "down", text: coverVerb(m.ty, "close") };
  const st = { act: "stop", icon: "stop", label: t("cover.stop") };
  return m.ty.inverse ? [cl, st, op] : [op, st, cl];
}

/** The status word (+ %): «открыта · 40 %», «поднимается», «нет данных». */
export function coverStatus(m, pct = m.pct) {
  if (m.offline) return t("common.offline");
  if (!m.known) return t("common.no_data");
  const w = coverWord(m.ty, m.moving || (m.open ? "open" : "closed"));
  return pct != null && !m.moving && pct > 0 && pct < 100 ? `${w} · ${pct} %` : w;
}

/** controls: id → a generic spec the Remote renders with primitives. */
export function coverControls(m, ctx = {}) {
  const pct = ctx.opt?.pct ?? m.pct;
  const c = {};
  c.status = { kind: "status", text: coverStatus(m, pct), dot: m.on ? "role" : null, role: m.role };
  if (m.caps.pos) c.position = { kind: "slider", value: pct ?? 0, label: t("cover.position"), act: "position", role: m.role, off: !pct, disabled: m.offline, valuetext: `${pct ?? 0}%` };
  // class A (gates, garage, doors): open and close only by a 1 s hold; stop at once
  c.triple = {
    kind: "buttons",
    items: triple(m).map((b) =>
      b.act === "stop"
        ? { icon: "stop", act: "stop", label: b.label, disabled: m.offline || !m.caps.stop, off: !m.moving }
        : { icon: b.icon, text: b.text, act: b.act, hold: m.caps.guard, on: m.moving === (b.act === "open" ? "opening" : "closing"), disabled: m.offline || !m.caps[b.act] },
    ),
  };
  if (m.caps.pos)
    c.presets = {
      kind: "seg",
      act: "preset",
      sel: CANON.cover.presets_pct.includes(pct) ? String(pct) : null,
      role: m.role,
      items: CANON.cover.presets_pct.map((p) => ({ v: String(p), label: p === 0 ? coverWord(m.ty, "closed") : p === 100 ? coverWord(m.ty, "open") : `${p} %` })),
    };
  if (m.caps.tilt) {
    const tl = ctx.opt?.tilt ?? m.tilt ?? 0;
    c.tilt = { kind: "slider", value: tl, label: t("cover.tilt"), act: "tilt", role: m.role, disabled: m.offline, valuetext: `${tl}%` };
  }
  if (m.caps.nopos) c.note = { kind: "note", text: t("cover.no_position") };
  if (m.caps.guard) c.guard = { kind: "note", text: t("common.hold_hint", { action: coverVerb(m.ty, "open").toLowerCase() }) };
  c.fav = { kind: "buttons", items: [{ icon: ctx.fav ? "star" : "starOff", text: ctx.fav ? t("light.fav_remove") : t("light.fav_add"), act: "fav", on: !!ctx.fav, wide: true }] };
  return c;
}

/** act(name, arg) → HA calls + the optimistic view patch. Everything the remote can do to a cover. */
export function coverAct(m, name, arg) {
  const id = m.id;
  const svc = (service, data = {}) => [{ domain: "cover", service, data: { entity_id: id, ...data } }];
  switch (name) {
    case "open":
      return { calls: svc("open_cover") };
    case "close":
      return { calls: svc("close_cover") };
    case "stop":
      return { calls: svc("stop_cover") };
    case "position":
    case "preset": {
      const p = Math.round(+arg);
      return { calls: svc("set_cover_position", { position: p }), opt: { pct: p } };
    }
    case "tilt": {
      const p = Math.round(+arg);
      return { calls: svc("set_cover_tilt_position", { tilt_position: p }), opt: { tilt: p } };
    }
    case "fav":
      return { fav: true };
  }
  return { calls: [] };
}

/** The tile's tap (covers spec K1): moving → stop; else to the end — open when closed, close otherwise. */
export function tapCall(m) {
  if (m.moving) return m.caps.stop ? "stop" : null;
  return m.open ? (m.caps.close ? "close" : null) : m.caps.open ? "open" : null;
}
