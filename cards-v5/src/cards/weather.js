// hauflo-weather · now and the next days (the old card's keys: entity, name, days, forecast: daily|hourly).
// Header canon: [condition icon] caps name / [big temperature °C][condition · feels like]; under it humidity · wind · pressure;
// under that the forecast (a websocket subscription, like HA's own weather card). Tap = HA's weather dialog.
import { t, tv, num, lang, clock } from "../core/i18n.js";
import { head, ico, msg } from "../kit/primitives.js";
import { glyph } from "../core/icons.js";
import { esc } from "../core/dom.js";
import { OFFLINE, reading } from "../adapters/common.js";
import { CANON } from "../gen/canon.js";

const W = CANON.cards.weather;
const R = (v) => num(Math.round(v) || 0, 0);
const unit = (u) => {
  const s = t(`weather.u.${u}`);
  return s.startsWith("weather.u.") ? u : s;
};
const kind = (c) => (c.forecast === "hourly" ? "hourly" : W.forecast_default);
const label = (d, hourly) => (hourly ? clock(d) : new Intl.DateTimeFormat(lang() === "ru" ? "ru-RU" : "en-GB", { weekday: "short" }).format(d));
const meta = (icon, text) => `<span class="hf5-wi">${glyph(icon)}<span>${text}</span></span>`;

export const WEATHER = {
  key: "weather",
  watch: (c) => [c.entity].filter(Boolean),
  grid: () => ({ columns: 12, rows: "auto", min_columns: 3 }),
  stub: (h) => ({ entity: Object.keys(h?.states || {}).find((x) => x.startsWith("weather.")) || "" }),
  render(card, v) {
    const c = card.config;
    const title = c.name || t("weather.title");
    const s = c.entity && card.hass.states[c.entity];
    if (!s) return `${head({ title })}${msg(t("weather.none"), { icon: "cloud" })}`;
    const a = s.attributes || {};
    const off = OFFLINE.has(s.state);
    const [ic, role] = off ? W.nodata_icon : W.icons[s.state] || W.other_icon;
    const temp = off ? undefined : reading(a.temperature, ...W.temp);
    const feels = reading(a.apparent_temperature, ...W.temp);
    const word = off ? t("weather.nodata") : tv("weather", s.state);
    const hum = reading(a.humidity, 0, 100);
    const wind = reading(a.wind_speed, 0, 500);
    const mm = a.pressure_unit === "mmHg";
    const pr = reading(a.pressure, ...(mm ? W.pressure_mmhg : W.pressure_hpa));
    const prOk = pr !== undefined && (mm || !a.pressure_unit || W.hpa_units.includes(a.pressure_unit));
    const top = head({
      title,
      value: temp === undefined ? "—" : R(temp),
      unit: a.temperature_unit || W.unit_default,
      size: v === "S" ? "xs" : "m",
      off: temp === undefined,
      words: v === "S" ? "" : word,
      sub: v !== "S" && !off && feels !== undefined ? t("weather.feels", { n: R(feels) }) : "",
      icon: ico(ic, { on: !off, role }),
    });
    const ms = [hum !== undefined && meta("humidity", `${num(hum, 0)} %`), wind !== undefined && meta("wind", `${num(wind, 1)} ${unit(a.wind_speed_unit || "km/h")}`), prOk && meta("gauge", `${num(pr, 0)} ${unit(a.pressure_unit || "hPa")}`)].filter(Boolean);
    const type = kind(c);
    const list = off || !((Number(a.supported_features) || 0) & W.forecast_bits) ? null : card.sub(`fc:${c.entity}:${type}`, { type: "weather/subscribe_forecast", forecast_type: type, entity_id: c.entity });
    const n = Math.min(Math.max(+c.days || W.days_default, 1), 12, v === "S" ? W.days_s : 99);
    const days = (list?.forecast || []).slice(0, n);
    const wait = !off && list === undefined && ((Number(a.supported_features) || 0) & W.forecast_bits);
    const fc =
      days.length || wait
        ? `<div class="hf5-wf" style="--n:${days.length || n}">${(days.length ? days : Array.from({ length: n }, () => null))
            .map((f) => {
              if (!f) return `<div class="is-wait"><span>&nbsp;</span>${glyph(W.other_icon[0])}<b>&nbsp;</b><small>&nbsp;</small></div>`;
              // a day's forecast is a day: «clear-night» from the provider reads as sunny
              const k = f.condition === "clear-night" && type === "daily" ? "sunny" : f.condition;
              const hi = reading(f.temperature, ...W.temp);
              const lo = reading(f.templow, ...W.temp);
              return `<div><span>${label(new Date(f.datetime), type === "hourly")}</span>${glyph((W.icons[k] || W.other_icon)[0])}<b>${hi === undefined ? "—" : R(hi) + "°"}</b><small>${lo === undefined ? "&nbsp;" : R(lo) + "°"}</small></div>`;
            })
            .join("")}</div>`
        : "";
    return `<div class="hf5-wt" data-tap="open" data-id="${esc(c.entity)}" role="button" tabindex="0" aria-label="${esc(`${title}, ${word}`)}">${top}${ms.length ? `<div class="hf5-wm">${ms.join("")}</div>` : ""}${fc}</div>`;
  },
  act: (card) => ({ more: card.config.entity }),
  css: `.hf5-wt{display:flex;flex-direction:column;gap:var(--hf-sp-3);cursor:pointer}
.hf5-wm{display:flex;flex-wrap:wrap;gap:var(--hf-sp-1) var(--hf-sp-4);font-size:var(--hf-fs-s);color:var(--hf-ink-2)}
.hf5-wi{display:inline-flex;align-items:center;gap:var(--hf-sp-1)}.hf5-wi .hf5-g{width:var(--hf-icon-s);height:var(--hf-icon-s)}
.hf5-wf{display:grid;grid-template-columns:repeat(var(--n),minmax(0,1fr));gap:var(--hf-sp-1);padding-top:var(--hf-sp-3);border-top:var(--hf-line-w) solid var(--hf-hair)}
.hf5-wf>div{display:flex;flex-direction:column;align-items:center;gap:var(--hf-sp-1);font-size:var(--hf-fs-xs);min-width:0}
.hf5-wf>div>span{color:var(--hf-ink-2)}.hf5-wf .hf5-g{width:var(--hf-icon-m);height:var(--hf-icon-m);color:var(--hf-ink-2)}
.hf5-wf b{font-size:var(--hf-fs-s);font-weight:var(--hf-w-med)}.hf5-wf small{color:var(--hf-ink-3)}
.hf5-wf>.is-wait{opacity:var(--hf-dim)}`,
};
