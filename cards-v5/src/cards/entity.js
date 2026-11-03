// hauflo-entity · one entity as a tile (the previous card's config keys, unchanged):
// entity, name, icon, icon_type (icon | entity-picture | none), color (amber | ice | mint | alert), layout (horizontal | vertical),
// show_level, primary_info / secondary_info (name | state | last_changed | last_updated | none), controls,
// tap_action / hold_action / double_tap_action (none | more-info | toggle | navigate | url | perform-action | call-service | hass-action).
// Inline controls: a thermostat (± like the climate rows) and a number (±); everything else is one tap or the remote.
import { t, num, lang } from "../core/i18n.js";
import { head, msg, tile, step, number } from "../kit/primitives.js";
import { trouble, troubleStates } from "../core/command.js";
import { deviceCall, deviceModel } from "../adapters/device.js";
import { coverModel, coverStatus } from "../adapters/cover.js";
import { lightModel } from "../adapters/light.js";
import { climateRow, CLIMATE } from "./climate.js";
import { CANON } from "../gen/canon.js";
import { esc, go, notify } from "../core/dom.js";

const E = CANON.entity;
const NBSP = " ";
const NUM = new Set(["number", "input_number"]);
const dom = (id) => id.split(".")[0];
const offline = (s) => s.state === "unavailable" || s.state === "unknown";
const cfg = (c, k) => (c[k] === undefined || c[k] === null ? E.defaults[k] : c[k]);
const mine = new Set(["tap", "single", "double", "hold", "nstep"]);

/** «5 минут назад» from an ISO time, in the card's language. */
function ago(iso) {
  const ms = Date.parse(iso);
  if (Number.isNaN(ms)) return "";
  const sec = Math.round((ms - Date.now()) / 1000);
  const [unit, size] = E.rel_units.find(([, s]) => Math.abs(sec) >= s) || E.rel_units[E.rel_units.length - 1];
  return new Intl.RelativeTimeFormat(lang() === "ru" ? "ru-RU" : "en-US", { numeric: "auto" }).format(Math.round(sec / size), unit);
}

/** The state as a word: our own for lights / blinds / panels, the device's value for readings, else HA's. */
function stateWord(card, s, m) {
  const d = dom(s.entity_id);
  if (offline(s)) return t("common.offline");
  if (d === "light") return s.state === "on" ? (lightModel(s).caps.nodim ? t("light.on") : `${lightModel(s).pct}%`) : t("light.off");
  if (d === "cover") return coverStatus(coverModel(s));
  if (d === "alarm_control_panel") {
    const k = `security.state.${s.state}`;
    if (t(k) !== k) return t(k);
  }
  if (m.value) return m.value;
  const f = card.hass.formatEntityState?.(s);
  if (f) return f;
  if (m.tap === "toggle" && !m.guard) return t(m.on ? "light.on" : "light.off");
  const unit = s.attributes?.unit_of_measurement;
  return unit ? `${s.state} ${unit}` : String(s.state);
}

function info(card, kind, s, name, m) {
  if (kind === "name") return name;
  if (kind === "state") return stateWord(card, s, m);
  if (kind === "last_changed") return ago(s.last_changed);
  if (kind === "last_updated") return ago(s.last_updated);
  return "";
}

/** The icon: the config's own, or the entity picture (a whitelisted url only). */
function picture(c, s, on) {
  const type = cfg(c, "icon_type");
  if (type === "none") return null;
  const url = s.attributes?.entity_picture;
  const want = type === "entity-picture" || (c.icon_type === undefined && E.picture_domains.includes(dom(s.entity_id)) && s.state !== "off");
  if (!want || typeof url !== "string" || !new RegExp(E.picture_ok).test(url)) return undefined;
  return `<span class="hf5-ico en-pic${on ? " is-on" : ""}" style="--hf5-pic:url(&quot;${esc(url)}&quot;)"></span>`;
}

const level = (s) => {
  const d = dom(s.entity_id);
  const raw = Number(s.attributes?.[E.level_attr[d]]);
  return E.level_attr[d] && s.state !== "off" && s.state !== "closed" && !Number.isNaN(raw) ? Math.min(100, Math.max(0, Math.round((raw / E.level_max[d]) * 100))) : undefined;
};

const missing = (card, id, name) =>
  tile({ size: "xs", icon: "info", name: name || id, status: t("common.no_data"), warn: true, cls: "is-offline", attr: {} });

export const ENTITY = {
  key: "entity",
  watch: (c) => (c?.entity ? [c.entity] : []),
  grid: (c) => ({ columns: cfg(c || {}, "layout") === "vertical" ? 6 : 12, rows: "auto", min_columns: 3 }),
  stub: (h) => ({ entity: Object.keys(h?.states || {}).find((k) => k.startsWith("light.")) || Object.keys(h?.states || {})[0] || "" }),
  render(card, v) {
    const c = card.config;
    const id = c.entity;
    const title = (typeof c.name === "string" && c.name) || t("entity.title");
    if (!id) return `${head({ title })}${msg(t("entity.none"), { icon: "info" })}`;
    const s = card.hass.states[id];
    if (!s) return missing(card, id, c.name);
    const name = (typeof c.name === "string" && c.name) || s.attributes?.friendly_name || id;
    const d = dom(id);
    const controls = cfg(c, "controls") !== false;
    if (controls && d === "climate" && !offline(s)) return climateRow(card, v, id, name);
    const m = deviceModel(s, { name, icon: c.icon });
    if (d === "cover") Object.assign(m, (({ icon, on }) => ({ icon, on }))(coverModel(s)));
    const on = (card.opt[id] || {}).on ?? m.on;
    const tr = trouble(m, card.cmd.of(id));
    const pic = picture(c, s, on && !m.offline);
    const first = cfg(c, "primary_info");
    const second = cfg(c, "secondary_info");
    let a = info(card, first, s, name, m);
    let b = info(card, second, s, name, m);
    if (tr === "failed") b = t("common.failed");
    else if (tr === "sending" && m.action) b = t("common.sending");
    if (!a) [a, b] = [b, ""];
    const val = (card.opt[id] || {}).val ?? Number(s.state);
    const ctl = controls && NUM.has(d) && !offline(s) && !Number.isNaN(val) ? (v === "S" ? number(num(val, 1), { size: "xs", unit: s.attributes?.unit_of_measurement || "" }) : step(number(num(val, 1), { size: "xs", unit: s.attributes?.unit_of_measurement || "" }), { act: "nstep", attr: { "data-id": id } })) : "";
    const colour = E.colors[c.color];
    const lvl = cfg(c, "show_level") !== false && !ctl && on && !m.offline ? level(s) : undefined;
    const label = [a, b].filter(Boolean).join(", ");
    return tile({
      size: cfg(c, "layout") === "vertical" ? "s" : "xs",
      icon: pic === undefined ? m.icon : pic,
      on: on && !m.offline,
      tint: m.tone,
      role: colour || m.role,
      badge: troubleStates(tr),
      name: a,
      status: b || NBSP,
      fill: lvl,
      ctl,
      warn: m.tone === "warn" || tr === "failed",
      short: tr === "offline" ? t("common.no_data") : null,
      cls: ["en-t", tr === "sending" && "is-pending", m.offline && "is-offline", m.tone === "alarm" && "is-alarm"].filter(Boolean).join(" "),
      attr: { "data-tap": "tap", "data-id": id, role: "button", tabindex: "0", "aria-label": label, "aria-pressed": m.tap === "toggle" && !m.guard && !m.offline ? String(!!on) : null },
    });
  },
  /** A configured hold_action replaces the remote (even «none»). */
  hold(card, el) {
    if (!card.config.hold_action) return false;
    card.act("hold", el.dataset.id, el);
    return true;
  },
  act(card, name, arg, el) {
    const c = card.config;
    const id = c.entity;
    const s = id && card.hass.states[id];
    if (!s) return null;
    if (!mine.has(name)) return dom(id) === "climate" ? CLIMATE.act(card, name, arg, el) : null;
    if (name === "nstep") {
      if (!NUM.has(dom(id)) || offline(s)) return null;
      const a = s.attributes || {};
      const stp = Number(a.step) || 1;
      const cur = card.opt[id]?.val ?? Number(s.state);
      if (Number.isNaN(cur)) return null;
      const lo = a.min != null ? Number(a.min) : -Infinity;
      const hi = a.max != null ? Number(a.max) : Infinity;
      const val = Math.min(hi, Math.max(lo, Math.round((cur + (arg === "-1" ? -stp : stp)) * 1e6) / 1e6));
      return { key: id, calls: [{ domain: dom(id), service: "set_value", data: { entity_id: id, value: val } }], opt: { [id]: { val } }, debounce: true };
    }
    if (name === "tap" && c.double_tap_action) {
      if (card._dbl) {
        clearTimeout(card._dbl);
        card._dbl = null;
        return run(card, s, c.double_tap_action);
      }
      card._dbl = setTimeout(() => ((card._dbl = null), card.act("single", id, el)), E.double_tap_ms);
      return null;
    }
    if (name === "double") return run(card, s, c.double_tap_action);
    if (name === "hold") return run(card, s, c.hold_action);
    return run(card, s, c.tap_action, true);
  },
  css: `.en-pic{background:var(--hf5-pic) center/cover no-repeat;border-radius:inherit}
.en-t.hf5-tile.is-xs>.hf5-tx{min-width:0}
.hf5-card.is-s .hf5-tile.en-t{flex-wrap:wrap}
.hf5-card.is-s .hf5-tile.en-t>.hf5-tc{margin-inline-start:auto}`,
};

/** The default tap: the device's own call (toggle / press / run), else its remote. */
function dflt(card, s) {
  const id = s.entity_id;
  if (card.cmd.of(id).failed) return { retry: id };
  const m = deviceModel(s);
  if (m.offline) return { more: id };
  const call = dom(id) === "automation" ? { domain: "automation", service: "toggle", data: { entity_id: id } } : deviceCall(m);
  return call ? { key: id, calls: [call], opt: m.tap === "toggle" ? { [id]: { on: !m.on } } : undefined } : { more: id };
}

/** One configured action → the card's result. */
function run(card, s, a, isTap = false) {
  const id = s.entity_id;
  if (!a || !a.action) return isTap ? dflt(card, s) : { more: id };
  const act = a.action;
  if (act === "none") return null;
  if (act === "default") return dflt(card, s);
  if (a.confirmation && !window.confirm(t("entity.confirm", { name: s.attributes?.friendly_name || id }))) return null;
  if (act === "more-info") return { more: a.entity || id };
  if (act === "toggle") return { key: id, calls: [{ domain: "homeassistant", service: "toggle", data: { entity_id: id } }] };
  if (act === "navigate") {
    if (typeof a.navigation_path === "string" && a.navigation_path.startsWith("/")) go(a.navigation_path);
    return null;
  }
  if (act === "url") {
    if (typeof a.url_path === "string" && new RegExp(E.url_ok).test(a.url_path)) window.open(a.url_path, "_blank", "noopener");
    return null;
  }
  if (act === "perform-action" || act === "call-service") {
    const [domain, service] = String(a.perform_action || a.service || "").split(".");
    if (!domain || !service) return (notify(card, t("common.failed")), null);
    const data = { ...(a.data || a.service_data || {}), ...(a.target || {}) };
    if (!data.entity_id && !a.target) data.entity_id = id;
    return { key: id, calls: [{ domain, service, data }], after: (ok) => ok || notify(card, t("common.failed")) };
  }
  card.dispatchEvent(new CustomEvent("hass-action", { bubbles: true, composed: true, detail: { config: { entity: id, tap_action: a, hold_action: a, double_tap_action: a }, action: "tap" } }));
  return null;
}
