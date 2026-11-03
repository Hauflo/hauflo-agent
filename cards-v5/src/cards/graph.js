// hauflo-graph · values over the last hours from HA's history (the old card's keys: entities [id | {entity, name, color}], hours, title).
// One block per entity: [name ... value unit] over the soft line (the kit graph) with min · max under it; a tap opens HA's dialog.
import { t, num } from "../core/i18n.js";
import { head, graph, msg } from "../kit/primitives.js";
import { esc } from "../core/dom.js";
import { OFFLINE, reading } from "../adapters/common.js";
import { CANON } from "../gen/canon.js";

const G = CANON.cards.graph;
const idOf = (e) => (typeof e === "string" ? e : e?.entity);
const items = (c) => (c.entities || []).map((e) => (typeof e === "string" ? { entity: e } : e)).filter((e) => e.entity);

/** History rows → {series, lo, hi}: x 0–1 over the period, y 0–1 over this entity's own min–max; values out of the sensor's physical range dropped. */
export function graphSeries(rows, hours, range, now = Date.now()) {
  const t0 = now - hours * 3600e3;
  const pts = (rows || [])
    .map((r) => [new Date(r.last_changed || r.last_updated).getTime(), Number(r.state)])
    .filter(([x, v]) => Number.isFinite(x) && Number.isFinite(v) && x >= t0 - 1 && (!range || (v >= range[0] && v <= range[1])));
  if (pts.length < 2) return { series: [], lo: null, hi: null };
  const vs = pts.map((p) => p[1]);
  const lo = Math.min(...vs), hi = Math.max(...vs), span = hi - lo || 1;
  const step = Math.max(1, Math.ceil(pts.length / G.points));
  const out = pts.filter((_, i) => i % step === 0 || i === pts.length - 1).map(([x, v]) => [Math.min(1, Math.max(0, (x - t0) / (hours * 3600e3))), (v - lo) / span]);
  return { series: [{ cls: "is-now", pts: out }], lo, hi };
}

async function history(card, id, hours, range) {
  const from = new Date(Date.now() - hours * 3600e3).toISOString();
  const r = await card.hass.callApi("GET", `history/period/${from}?filter_entity_id=${id}&minimal_response&no_attributes&significant_changes_only=0`);
  return graphSeries(r?.[0] || [], hours, range);
}

export const GRAPH = {
  key: "graph",
  watch: (c) => items(c).map((e) => e.entity),
  grid: () => ({ columns: 12, rows: "auto", min_columns: 3 }),
  stub: (h) => ({ hours: G.hours_default, entities: Object.values(h?.states || {}).filter((s) => s.entity_id.startsWith("sensor.") && s.attributes?.unit_of_measurement && Number.isFinite(Number(s.state))).slice(0, 2).map((s) => s.entity_id) }),
  render(card) {
    const c = card.config;
    const hours = +c.hours || G.hours_default;
    const title = c.title || t("graph.title", { n: hours });
    const list = items(c);
    if (!list.length) return `${head({ title })}${msg(t("graph.none"), { icon: "info" })}`;
    card.tick("refresh", G.refresh_min * 60e3);
    const blocks = list.map((e, k) => {
      const s = card.hass.states[e.entity];
      const a = s?.attributes || {};
      const off = !s || OFFLINE.has(s.state);
      const unit = a.unit_of_measurement || "";
      const whole = G.whole_units.includes(unit);
      const d = (x) => (whole || Number.isInteger(x) ? 0 : 1);
      const n = off ? undefined : reading(s.state);
      const role = e.color ? null : G.roles[k % G.roles.length];
      const h = card.load(`h:${e.entity}:${hours}`, G.refresh_min * 30e3, () => history(card, e.entity, hours, G.ranges[a.device_class]));
      const body =
        h === undefined
          ? `<div class="hf5-empty"></div>`
          : h?.series?.length
            ? graph(h.series, { role: role || undefined, axis: [`${t("graph.min")} ${num(h.lo, d(h.lo))}`, `${t("graph.max")} ${num(h.hi, d(h.hi))}`], label: e.name || a.friendly_name || e.entity })
            : `<div class="hf5-empty">${esc(t("graph.nohist"))}</div>`;
      const nm = e.name || a.friendly_name || e.entity;
      const custom = e.color ? ` style="--hf5-role:${esc(e.color)};--hf5-role-tint:color-mix(in srgb,${esc(e.color)} 25%,transparent)"` : "";
      return `<div class="hf5-gi" data-tap="open" data-id="${esc(e.entity)}" role="button" tabindex="0" aria-label="${esc(`${nm}, ${n === undefined ? t("graph.nodata") : num(n, d(n)) + " " + unit}`)}"${custom}><div class="hf5-gh"><span>${esc(nm)}</span><b${n === undefined ? ' class="is-off"' : ""}>${n === undefined ? "—" : num(n, d(n))}${unit ? `<small>${esc(unit)}</small>` : ""}</b></div>${body}</div>`;
    });
    return `${head({ title })}<div class="hf5-gl">${blocks.join("")}</div>`;
  },
  act: (card, name, arg, el) => ({ more: el?.dataset.id }),
  css: `.hf5-gl{display:flex;flex-direction:column;gap:var(--hf-sp-3)}
.hf5-gi{cursor:pointer;display:flex;flex-direction:column;gap:var(--hf-sp-1)}.hf5-gi+.hf5-gi{padding-top:var(--hf-sp-3);border-top:var(--hf-line-w) solid var(--hf-hair)}
.hf5-gh{display:flex;justify-content:space-between;align-items:baseline;gap:var(--hf-sp-3)}.hf5-gh span{font-size:var(--hf-fs-s);font-weight:var(--hf-w-med);min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.hf5-gh b{font-family:var(--hf-font-num);font-size:var(--hf-fs-num-xs);font-weight:var(--hf-w-num-xs);white-space:nowrap}.hf5-gh b.is-off{color:var(--hf-ink-3)}
.hf5-gh small{font-size:var(--hf-fs-s);color:var(--hf-ink-2);margin-left:var(--hf-sp-1);font-weight:var(--hf-w-reg)}
.hf5-gi .hf5-graph .is-now{stroke:var(--hf5-role,var(--hf-ink-2))}`,
};
