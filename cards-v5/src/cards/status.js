// hauflo-status · the building's dispatching — sensors spec §7 (ISA-101 / ISA-18.2: quiet normal, the alarm's life).
// Config (unchanged from the previous card): title, block, entities (string | {entity, name, icon, group}).
// Header: caps title · one summary — «авария · 1» (red) > «авария · 1» (equipment, yellow) > «нет связи · 2» >
// «внимание · 1» > «всё в норме · 8 точек» (one dot for the card). S: the number of problems big, or ✓ «всё в норме».
// Rows: trouble first (alarm → fault → no link → attention), then normal in the integrator's order; normal is quiet
// (no dot per row). M folds the normal tail under «ещё N — в норме» (never trouble); L shows all, grouped by `group`
// or the HA area when there are several, and an open alarm has «Принять» (hold 1 s). Tap / hold a row = the remote.
import { t, tn, num } from "../core/i18n.js";
import { cap, head, msg, tile, number, holdPill, pill } from "../kit/primitives.js";
import { blockTitle } from "../core/house.js";
import { openAlarms, ackAlarms } from "../core/alarms.js";
import { esc, notify } from "../core/dom.js";
import { CANON } from "../gen/canon.js";
import { statusModel, tierRank } from "../adapters/status.js";

const idOf = (e) => (typeof e === "string" ? e : e?.entity);
const items = (c) => (Array.isArray(c.entities) ? c.entities.filter((e) => idOf(e)).map((e) => (typeof e === "string" ? { entity: e } : e)) : []);
const TINT = { alarm: "alarm", fault: "warn", warn: "warn" };

function areaName(h, id) {
  const e = h?.entities?.[id];
  const a = e?.area_id || (e?.device_id && h.devices?.[e.device_id]?.area_id);
  return (a && h.areas?.[a]?.name) || "";
}

/** The points with their word and tier, trouble first (stable: the integrator's order inside a tier). */
function points(card) {
  const h = card.hass;
  return items(card.config)
    .map((e, i) => {
      const st = h.states[e.entity];
      if (!st) return null;
      const m = statusModel(st, e, h.states);
      return { ...m, i, area: areaName(h, e.entity), group: e.group || "" };
    })
    .filter(Boolean)
    .sort((a, b) => tierRank(a) - tierRank(b) || a.i - b.i);
}

function summary(ps) {
  const n = (tier) => ps.filter((p) => p.tier === tier).length;
  if (n("alarm")) return { text: t("devices.alarm_n", { n: n("alarm") }), dot: "alarm", bad: n("alarm") + n("fault") + n("nolink") + n("warn") };
  if (n("fault")) return { text: t("acs.fault_n", { n: n("fault") }), dot: "warn", bad: n("fault") + n("nolink") + n("warn") };
  if (n("nolink") === ps.length) return { text: t("common.offline"), dot: "warn", bad: ps.length };
  if (n("nolink")) return { text: t("devices.offline_n", { n: n("nolink") }), dot: "warn", bad: n("nolink") + n("warn") };
  if (n("warn")) return { text: t("devices.warn_n", { n: n("warn") }), dot: "warn", bad: n("warn") };
  return { text: tn("status.ok_n", ps.length), dot: "ok", bad: 0 };
}

/** One row: the room goes to the small line when the name does not say it already. */
function row(p, ack) {
  const nm = p.area && !p.name.toLowerCase().includes(p.area.toLowerCase()) ? `${p.name} · ${p.area}` : p.name;
  return tile({
    size: "xs",
    icon: p.icon,
    on: p.on && !p.tier,
    tint: TINT[p.tier] || null,
    role: "state-on",
    badge: p.offline ? { offline: true } : null,
    name: nm,
    status: p.word,
    warn: p.tier === "fault" || p.tier === "warn",
    cls: [p.tier === "alarm" && "is-alarm", p.offline && "is-offline"].filter(Boolean).join(" "),
    ctl: ack ? holdPill(t("alert.accept"), { act: "ack", role: p.tier === "alarm" && !ack.ackedBy ? "state-alarm" : "state-warn", attr: { "data-arg": ack.key } }) : "",
    attr: { "data-tap": "open", "data-id": p.id, role: "button", tabindex: "0", "aria-label": `${nm}, ${p.word}` },
  });
}

export const STATUS = {
  key: "status",
  watch: (c) => items(c).map(idOf),
  grid: () => ({ columns: 12, rows: "auto", min_columns: 3 }),
  stub: (h) => ({
    entities: Object.keys(h?.states || {})
      .filter((x) => x.startsWith("binary_sensor.") && ["problem", "connectivity", "battery", "moisture", "running", "power"].includes(h.states[x].attributes?.device_class))
      .slice(0, 8),
  }),
  render(card, v) {
    const c = card.config;
    const title = blockTitle(card, t("status.title"));
    const ps = points(card);
    if (!ps.length) return `${head({ title })}${msg(t("status.none"), { icon: "fault" })}`;
    const s = summary(ps);
    const top = head({ title, words: s.text, dot: s.dot, tone: s.dot !== "ok" && s.dot });
    if (v === "S")
      return `${top}<div class="hf5-big">${s.bad ? number(num(s.bad, 0), { size: "s", role: s.dot === "alarm" ? "state-alarm" : "state-warn" }) + `<div class="hf5-line">${esc(tn("status.problems", s.bad))}</div>` : ""}</div>`;
    // L: «Принять» at a row with an open, not yet accepted alarm of the agent's
    const acks = new Map();
    if (v === "L") for (const a of openAlarms(card, ps.map((p) => p.id))) if (a.key && !a.ackedBy && !a.returned && !acks.has(a.entity)) acks.set(a.entity, a);
    const bad = ps.filter((p) => p.tier);
    const ok = ps.filter((p) => !p.tier);
    const cap0 = CANON.status.fold_after;
    const fold = v === "M" && !card._unfold && ok.length > cap0 + 1 ? ok.length - cap0 : 0;
    const shown = [...bad, ...ok.slice(0, ok.length - fold)];
    // L groups: the integrator's `group`, else the HA area, when there are several
    const key = (p) => p.group || (v === "L" ? p.area : "");
    const groups = [...new Set(shown.map(key))];
    let body;
    if (v === "L" && groups.length > 1)
      body = groups
        .map((g) => {
          const gs = shown.filter((p) => key(p) === g);
          const worst = gs.find((p) => p.tier);
          return `<div class="hf5-fl">${cap(g || t("status.title"))}${worst ? `<i class="hf5-dot is-${worst.tier === "alarm" ? "alarm" : "warn"}"></i>` : ""}</div>${gs.map((p) => row(p, acks.get(p.id))).join("")}`;
        })
        .join("");
    else body = shown.map((p) => row(p, acks.get(p.id))).join("");
    const more = fold
      ? pill(tn("status.fold", fold), { cls: "hf5-fold", attr: { "data-act": "fold" } })
      : v === "M" && card._unfold && ok.length > cap0 + 1
        ? pill(t("status.fold_less"), { cls: "hf5-fold", attr: { "data-act": "fold" } })
        : "";
    return `${top}<div class="hf5-grid is-rows">${body}</div>${more}`;
  },
  css: `.hf5-tile.is-alarm{box-shadow:inset 0 0 0 var(--hf-focus-w) var(--hf-state-alarm)}
.hf5-tile.is-alarm .hf5-line{color:var(--hf-state-alarm-text)}
.hf5-grid>.hf5-fl{grid-column:1/-1;display:flex;align-items:center;gap:var(--hf-sp-2);padding-top:var(--hf-sp-2)}
.hf5-grid>.hf5-fl:first-child{padding-top:0}
.hf5-fold{align-self:flex-start;margin-top:var(--hf-sp-2)}`,
  act(card, name, arg, el) {
    if (name === "fold") {
      card._unfold = !card._unfold;
      return {};
    }
    if (name === "ack") {
      ackAlarms(card.hass, [arg]).then((ok) => ok || notify(card, t("alert.failed")));
      return null;
    }
    if (name === "open") return { more: el.dataset.id };
    return null;
  },
  early: (card, el) => notify(card, t("common.hold_hint", { action: el.textContent.trim().toLowerCase() })),
};
