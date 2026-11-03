// hauflo-air · PM2.5 on the US EPA scale + the other air readings (the old card's keys: entity, pm25, pm10, co2, voc, humidity, temperature, name).
// Header canon: caps name / [big PM2.5 µg/m³][level word, toned]; under it the 4-step scale with a marker, under that a row of value cells.
import { t, num } from "../core/i18n.js";
import { head, ico, msg } from "../kit/primitives.js";
import { esc } from "../core/dom.js";
import { reading } from "../adapters/common.js";
import { CANON } from "../gen/canon.js";

const A = CANON.cards.air;
const SEGS = [0, 1, 2, 3];
const value = (card, id, k) => {
  const c = card.config;
  const s = id && card.hass.states[id];
  const a = c.entity && card.hass.states[c.entity]?.attributes;
  const [lo, hi] = A.ranges[k];
  return reading(s?.state, lo, hi) ?? reading(a?.[A.attrs[k]], lo, hi);
};
const level = (p) => A.pm25_steps.findIndex(([max]) => max === null || p <= max);

export const AIR = {
  key: "air",
  watch: (c) => [c.entity, c.pm25, c.pm10, c.co2, c.voc, c.humidity, c.temperature].filter(Boolean),
  grid: () => ({ columns: 12, rows: "auto", min_columns: 3 }),
  stub: (h) => {
    const s = Object.values(h?.states || {});
    const pick = (cls) => s.find((x) => x.entity_id.startsWith("sensor.") && x.attributes?.device_class === cls)?.entity_id || "";
    return { pm25: pick("pm25"), co2: pick("carbon_dioxide") };
  },
  render(card, v) {
    const c = card.config;
    const title = c.name || t("air.title");
    const ids = { pm25: c.pm25, pm10: c.pm10, co2: c.co2, voc: c.voc, hum: c.humidity, t: c.temperature };
    if (!c.entity && !Object.values(ids).some(Boolean)) return `${head({ title })}${msg(t("air.none"), { icon: "gauge" })}`;
    const x = Object.fromEntries(Object.keys(ids).map((k) => [k, value(card, ids[k], k)]));
    const lv = x.pm25 === undefined ? -1 : level(x.pm25);
    const role = lv < 0 ? null : A.pm25_steps[lv][1];
    const seg = Math.min(Math.max(lv, 0), SEGS.length - 1);
    const E = A.scale_edges;
    const pos = lv < 0 ? 0 : Math.min(100, ((seg + Math.min(1, (x.pm25 - E[seg]) / (E[seg + 1] - E[seg]))) / SEGS.length) * 100);
    const word = lv < 0 ? t("air.nopm") : t(`air.l${lv}`);
    const top = head({
      title,
      value: x.pm25 === undefined ? "—" : num(x.pm25, 0),

      size: v === "S" ? "xs" : v === "M" ? "s" : "m",
      off: lv < 0,
      role,
      words: v === "S" ? "" : word,
      sub: v === "S" ? "" : `PM2.5 · ${t("air.unit")}`,
      tone: role ? role.replace("state-", "") : "",
      icon: ico("gauge", { on: lv >= 0, role: role || undefined }),
    });
    const scale = `<div class="hf5-asc${lv < 0 ? " is-off" : ""}" role="img" aria-label="${esc(`PM2.5, ${word}`)}">${SEGS.map(
      (k) => `<b class="${lv >= 0 && seg === k ? "is-on" : ""}" style="--hf5-seg:var(--hf-${A.pm25_steps[k][1]})"></b>`
    ).join("")}<i style="left:${pos}%"></i></div>`;
    const cells = A.cells
      .filter(([k]) => x[k] !== undefined)
      .map(([k, u, d]) => {
        const lab = k === "co2" ? t("air.co2", { w: t(`air.co2_${x.co2 < A.co2_limits[0] ? "fresh" : x.co2 < A.co2_limits[1] ? "vent" : "stuffy"}`) }) : t(`air.${k}`);
        return `<div><b>${num(x[k], d)}<small>${esc(u)}</small></b><span>${esc(lab)}</span></div>`;
      })
      .join("");
    return `${top}${scale}${cells ? `<div class="hf5-acl">${cells}</div>` : ""}`;
  },
  act: () => null,
  css: `.hf5-asc{position:relative;display:grid;grid-template-columns:repeat(4,1fr);gap:var(--hf-sp-1);margin:var(--hf-sp-4) 0 var(--hf-sp-2)}
.hf5-asc b{height:var(--hf-sp-2);border-radius:var(--hf-r-pill);background:var(--hf5-seg);opacity:var(--hf-dim)}.hf5-asc b.is-on{opacity:1;box-shadow:0 0 var(--hf-sp-3) var(--hf5-seg)}
.hf5-asc.is-off b{background:var(--hf-surface-2);opacity:1;box-shadow:none}.hf5-asc.is-off i{display:none}
.hf5-asc i{position:absolute;top:calc(var(--hf-sp-1) * -1);width:var(--hf-sp-4);height:var(--hf-sp-4);border-radius:50%;background:var(--hf-surface);box-shadow:0 0 0 var(--hf-focus-w) var(--hf-ink);transform:translateX(-50%)}
.hf5-acl{display:grid;grid-template-columns:repeat(auto-fit,minmax(calc(var(--hf-hit) * 2),1fr));gap:var(--hf-sp-2);margin-top:var(--hf-sp-3)}
.hf5-acl>div{padding:var(--hf-sp-2) var(--hf-sp-3);border-radius:var(--hf-r-ctl);background:var(--hf-surface-2);min-width:0}
.hf5-acl b{display:block;font-size:var(--hf-fs-l);font-weight:var(--hf-w-reg)}.hf5-acl small{font-size:var(--hf-fs-xs);color:var(--hf-ink-2);margin-left:var(--hf-sp-1)}
.hf5-acl span{display:block;font-size:var(--hf-fs-s);color:var(--hf-ink-2);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}`,
};
