// hauflo-hero · the house header (top of «Главная» / rooms): caps over-title, ONE status phrase with its dot, what to do
// and what to do when it is bad (the call lives in «Настройки», owner 03.11), a row of small facts and the weather pill. House spec v5 header canon: caps title over one
// line [words][buttons]; nothing but the status is big; the height does not jump (the quiet line and the facts keep their slot).
// Config (unchanged from the previous card): label, temperature [], humidity [], lights [], rooms_lights {room: [ids]},
// alarms [], devices [], weather, rooms {entity: room}, all_alarms, service {phone, email, company, name}.
// Status, by priority: an unwatched leak > alarm sensors without link > ALL GOOD (+ one quiet line) > siren / panel triggered >
// alarms (red when critical and not yet accepted, else yellow). On «Главная» it also carries this person's favourites.
import { t, tn, num } from "../core/i18n.js";
import { CANON } from "../gen/canon.js";
import { esc, notify, go } from "../core/dom.js";
import { cap, head, line, msg, pill, tile } from "../kit/primitives.js";
import { openAlarms, isDead } from "../core/alarms.js";
import { alarmName, areaName, dcOf, todo } from "../core/contact.js";
import { CALL_CSS } from "../core/contact.js";
import { clientName } from "../core/names.js";
import { houseInfo } from "../core/house.js";
import { isHome, openService, roomCfg } from "../core/lovelace.js";
import { presence, presPartial } from "../core/presence.js";
import { climateModel } from "../adapters/climate.js";

const H = CANON.hero;
const WX = CANON.cards.weather; // one weather icon set for the hero pill and the weather card
const arr = (x) => (Array.isArray(x) ? x.filter((v) => typeof v === "string") : []);
const uniq = (xs) => [...new Set(xs)];
const val = (v) => (v === undefined || v === null || v === "" || Number.isNaN(Number(v)) ? undefined : Number(v));
const avg = (xs) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : undefined);
const tempOf = (s) => (s && !isDead(s) ? val(s.entity_id.startsWith("climate.") ? s.attributes?.current_temperature : s.state) : undefined);
const nameOf = (s, id) => (s?.attributes?.friendly_name ? clientName(s.attributes.friendly_name, id) : id);
const on = (s) => s?.state === "on";
const moreName = (ids, h) => `${nameOf(h.states[ids[0]], ids[0])}${ids.length > 1 ? ` +${ids.length - 1}` : ""}`;

/** A light that is silent only for a moment after a restart reads «обновляется…», later «нет данных». */
const freshUntil = (s) => (s && isDead(s) ? Date.parse(s.last_changed || s.last_updated || "") + H.fresh_ms : NaN);
function updating(card, list) {
  const silent = list.filter(isDead);
  if (!silent.length || !silent.every((s) => s && freshUntil(s) > Date.now())) return false;
  const at = Math.max(...silent.map(freshUntil));
  clearTimeout(card._heroWake);
  card._heroWake = setTimeout(() => ((card._html = null), card.render()), Math.max(0, at - Date.now()) + 50);
  return true;
}

/** Everything the card says, from the states (one pass; render only formats it). */
function read(card) {
  const h = card.hass;
  const c = card.config;
  const S = (id) => h.states[id];
  const roomsLights = c.rooms_lights && typeof c.rooms_lights === "object" ? c.rooms_lights : {};
  const m = { home: isHome(c.all_alarms) };
  // lights
  const lightIds = uniq([...Object.values(roomsLights).flat(), ...arr(c.lights)]);
  m.litRooms = Object.values(roomsLights).filter((ids) => arr(ids).some((id) => on(S(id)))).length;
  m.lamps = uniq(Object.values(roomsLights).flat()).filter((id) => on(S(id))).length;
  m.outerOn = uniq(arr(c.lights)).filter((id) => on(S(id)));
  m.lightsUnknown = lightIds.length > 0 && lightIds.every((id) => isDead(S(id)));
  const longDead = (id) => isDead(S(id)) && (!S(id) || Date.now() - Date.parse(S(id).last_changed || 0) > H.dead_ms);
  m.lightsDeaf = lightIds.filter(longDead).length;
  m.lightsFresh = lightIds.some((id) => isDead(S(id))) && (!m.lightsDeaf || updating(card, lightIds.map(S)));
  // alarms: back to normal and not yet accepted is no open alarm of the house (only the alert card asks to accept it)
  const open = openAlarms(card, arr(c.alarms)).filter((a) => !a.returned);
  m.alarms = open.map((a) => a.entity);
  m.critical = open.some((a) => a.critical !== false && !a.ackedBy);
  m.pending = open.filter((a) => !a.ackedBy).length;
  m.top = open.find((a) => !a.ackedBy) || open[0];
  m.loud = Object.values(h.states).filter((s) => (s.entity_id.startsWith("siren.") && s.state === "on") || (s.entity_id.startsWith("alarm_control_panel.") && s.state === "triggered"));
  m.ok = !m.alarms.length && !m.loud.length;
  // devices: a thermostat with no setpoint heard yet is «waiting» (a restart), a lost one is «нет связи»
  const dev = arr(c.devices);
  m.waiting = dev.filter((id) => id.startsWith("climate.") && S(id) && !isDead(S(id)) && val(S(id).attributes.temperature) === undefined && val(S(id).attributes.target_temp_low) === undefined);
  m.climates = dev.filter((id) => id.startsWith("climate.") && !isDead(S(id)));
  const offline = dev.filter((id) => !S(id) || S(id).state === "unavailable" || (id.startsWith("climate.") && S(id).state === "unknown"));
  m.deaf = arr(c.alarms).filter((id) => isDead(S(id)));
  m.deafOnly = offline.concat(m.deaf.filter((id) => !offline.includes(id))).filter((id) => m.deaf.includes(id));
  m.otherOff = offline.filter((id) => !m.deaf.includes(id));
  m.offlineAll = m.otherOff.length + m.deafOnly.length;
  // a leak of any integration's sensor that no rule watches yet is no «Всё хорошо» either
  m.wet =
    m.home || c.all_alarms
      ? Object.values(h.states).filter((x) => x.entity_id.startsWith("binary_sensor.") && x.attributes?.device_class === "moisture" && x.state === "on" && !h.entities?.[x.entity_id]?.hidden && !arr(c.alarms).includes(x.entity_id))
      : [];
  // facts
  const temps = arr(c.temperature).map((id) => tempOf(S(id))).filter((v) => v !== undefined);
  m.tmin = temps.length ? Math.min(...temps) : undefined;
  m.tmax = temps.length ? Math.max(...temps) : undefined;
  m.hum = avg(arr(c.humidity).map((id) => val(S(id)?.state)).filter((v) => v !== undefined));
  return m;
}

/** The headline: {text, dot, tone, sub (a quiet line), todo, first}. */
function headline(card, m) {
  const h = card.hass;
  const c = card.config;
  const svc = null; // no «Позвонить» up here (owner 03.11): the to-do says who to tell, the number is in «Настройки»
  const quiet = (text, o = {}) => ({ text, ...o });
  const offLine = m.otherOff.length ? quiet(tn("hero.devices_off", m.otherOff.length), { dot: "warn", link: true }) : null;
  if (!m.alarms.length && !m.loud.length && m.wet.length) {
    const w = m.wet[0];
    const where = areaName(h, w.entity_id);
    return { text: t("hero.leak", { name: alarmName(h, w.entity_id).name + (where ? ` · ${where}` : "") }), dot: "alarm", todo: todo(c, h, w.entity_id, svc) };
  }
  if (m.ok && m.deafOnly.length) return { text: tn("alert.dead", m.deafOnly.length), dot: "warn", sub: offLine };
  if (m.ok) {
    const sub = offLine
      ? offLine
      : m.waiting.length && m.waiting.length === m.climates.length
        ? quiet(t("hero.climate_wait"))
        : m.lightsFresh
          ? quiet(t("hero.fresh"))
          : m.lightsDeaf
            ? quiet(t("hero.deaf_lights", { what: m.lightsUnknown ? t("hero.light_word") : tn("hero.lamps", m.lightsDeaf) }), { link: true })
            : null;
    return { text: t("hero.ok"), dot: "ok", sub };
  }
  if (!m.alarms.length) return { text: t(m.loud.some((s) => s.entity_id.startsWith("alarm_control_panel.")) ? "hero.panel" : "hero.siren"), dot: "alarm" };
  const top = m.top;
  const an = alarmName(h, top.entity);
  const room = c.rooms?.[top.entity] || (an.tech ? areaName(h, top.entity) : "");
  const name = `${an.name}${room ? ` · ${room}` : ""}`;
  const text = m.alarms.length === 1 ? t("hero.alarm1", { name }) : m.pending ? tn("hero.alarms", m.pending) : tn("hero.accepted", m.alarms.length);
  const first = m.alarms.length > 1 && m.pending ? t("hero.first", { name }) : null;
  return { text, dot: m.critical ? "alarm" : "warn", first, todo: alertShown(card) ? "" : todo(c, h, top.entity, svc), tone: m.critical ? "alarm" : "warn" };
}
/** Another hauflo-alert card on the page already says what to do: the header does not repeat it. */
function alertShown(card) {
  return [...document.querySelectorAll("hauflo-alert")].some((e) => e !== card && e.isConnected && e.style.display !== "none");
}

const lightFact = (card, m) => {
  const h = card.hass;
  const more = (ids) => moreName(ids, h);
  if (m.lightsUnknown) return t(m.lightsFresh ? "hero.l_fresh" : "hero.l_none");
  if (m.litRooms) return `${t("hero.lamps_in", { lamps: tn("hero.lamps", m.lamps), rooms: tn("hero.in_rooms", m.litRooms) })}${m.outerOn.length ? ` · ${more(m.outerOn)}` : ""}`;
  if (m.outerOn.length) return t("hero.l_outer", { name: more(m.outerOn) });
  if (m.lightsDeaf) return t(m.lightsFresh ? "hero.l_off_fresh" : "hero.l_off_deaf", { n: m.lightsDeaf });
  return t("hero.l_off");
};
const fact = (label, text, o = {}) => `<div class="hf5-fact">${cap(label)}<div class="hf5-fv" title="${esc(text)}">${o.dot ? `<i class="hf5-dot is-${o.dot}"></i>` : ""}${esc(text)}</div></div>`;
const NUMBER = (v, d = 0) => num(v, d);

function facts(card, m) {
  const c = card.config;
  const out = [];
  if (m.tmin !== undefined) out.push(fact(t("hero.f_rooms"), m.tmin === m.tmax ? `${NUMBER(m.tmin)}°` : `${NUMBER(Math.floor(m.tmin))}–${NUMBER(Math.ceil(m.tmax))}°`));
  if (m.hum !== undefined) out.push(fact(t("hero.f_hum"), `${NUMBER(m.hum)} %`));
  out.push(fact(t("hero.f_light"), lightFact(card, m)));
  if (m.ok && !m.offlineAll && m.waiting.length && m.waiting.length < m.climates.length) out.push(fact(t("hero.f_climate"), t("hero.climate_part", { n: m.waiting.length, total: m.climates.length })));
  if (arr(c.alarms).length && m.ok) out.push(m.deaf.length ? fact(t("hero.f_sensors"), tn("hero.sensors_off", m.deaf.length), { dot: "warn" }) : fact(t("hero.f_sensors"), t("hero.sensors_ok")));
  return `<div class="hf5-facts">${out.join("")}</div>`;
}

function weather(card) {
  const c = card.config;
  const w = c.weather && card.hass.states[c.weather];
  const wt = w ? val(w.attributes?.temperature) : undefined;
  if (!w || wt === undefined || !(wt > H.temp_ok[0] && wt < H.temp_ok[1])) return "";
  const word = WX.icons[w.state] ? t(`hero.weather.${w.state}`) : String(w.state);
  return pill(`${t("hero.out")} ${NUMBER(Math.round(wt))}° · ${word.toLowerCase()}`, { icon: (WX.icons[w.state] || WX.other_icon)[0], cls: "hf5-wx", attr: { "data-more": c.weather } });
}
function awayChip(card) {
  const pr = presence(card);
  if (!pr?.enabled) return "";
  const part = presPartial(pr);
  const word = t(!part ? "hero.away" : part.kind === "covers" ? "hero.away_covers" : part.kind === "lights" ? "hero.away_lights" : "hero.away_none");
  return pill(word, { icon: part ? "alert" : "away", role: "state-warn", on: !part, cls: part ? "hf5-away is-part" : "hf5-away", attr: { "data-act": "away" } });
}

// ── favourites: «Моя комната» and what this person pinned ──
function myRoom(card, room) {
  const h = card.hass;
  const rc = roomCfg(room.path) || {};
  const lights = arr(rc.lights).filter((id) => h.states[id]);
  const lit = lights.filter((id) => on(h.states[id])).length;
  const cs = rc.climate && h.states[rc.climate];
  const cm = cs && !isDead(cs) && cs.attributes?.preset_mode !== "building_protection" ? climateModel(cs) : null;
  const want = cm ? card.opt[rc.climate]?.target ?? cm.target : undefined;
  const light = lights.length
    ? pill(`${t("hero.my_light")} · ${lit ? (lights.length > 1 ? t("hero.my_light_on", { n: lit, total: lights.length }) : t("hero.my_light_on1")) : t("hero.off")}`, { icon: "light", role: "state-light", on: lit > 0, attr: { "data-act": "mylight", "data-arg": room.path } })
    : "";
  const warm = want != null
    ? pill(`${t("hero.warm")} · ${cm.current != null ? t("hero.warm_now", { set: NUMBER(want, want % 1 ? 1 : 0), now: NUMBER(cm.current, 1) }) : t("hero.warm_set", { set: NUMBER(want, want % 1 ? 1 : 0) })}`, { icon: "heat", role: "state-heat", attr: { "data-act": "warm", "data-arg": room.path } })
    : "";
  return (
    tile({ size: "s", icon: "home", on: true, role: "state-secure", name: room.name, status: t("hero.my_room"), attr: { "data-tap": "room", "data-arg": room.path, role: "button", tabindex: "0", "aria-label": room.name } }) +
    (light || warm ? `<div class="hf5-row hf5-fr">${light}${warm}</div>` : "")
  );
}
function favTile(card, id) {
  const h = card.hass;
  const s = h.states[id];
  const dom = id.split(".")[0];
  const dead = isDead(s);
  const lit = dom === "cover" ? s.state !== "closed" && !dead : on(s);
  const dim = dom === "light" && (s.attributes?.supported_color_modes || []).some((x) => x !== "onoff");
  const pct = Math.round(((val(s.attributes?.brightness) ?? 255) / 255) * 100);
  const run = H.fav[dom] === "run";
  const status = dead ? t("common.offline") : dom === "cover" ? t(lit ? "hero.open" : "hero.closed") : run ? t("hero.scene") : lit ? (dim ? `${pct} %` : t("hero.on")) : t("hero.off");
  const icon = dom === "cover" ? "curtain" : run ? "scene" : dom === "light" ? "light" : "power";
  return tile({
    size: "s",
    icon,
    on: lit && !run,
    role: dom === "cover" ? "state-cover" : dom === "light" ? "state-light" : "state-on",
    name: nameOf(s, id),
    status,
    attr: { "data-tap": "fav", "data-id": id, role: "button", tabindex: "0", "aria-label": `${nameOf(s, id)}, ${status}` },
  });
}
function favs(card, m) {
  const f = card.load("house", 300000, () => houseInfo(card.hass))?.favorites;
  if (!f || !m.home) return "";
  const items = arr(f.items).filter((id) => card.hass.states[id]);
  if (!items.length && !f.room) return "";
  return `<section class="hf5-part">${cap(t("hero.fav"))}<div class="hf5-grid">${f.room ? myRoom(card, f.room) : ""}${items.map((id) => favTile(card, id)).join("")}</div></section>`;
}

export const HERO = {
  key: "hero",
  watch(c, card) {
    const h = card?.hass;
    const loud = Object.keys(h?.states || {}).filter((id) => id.startsWith("siren.") || id.startsWith("alarm_control_panel."));
    const items = h ? arr(card.load("house", 300000, () => houseInfo(h))?.favorites?.items) : [];
    const rc = h ? roomCfg(card.load("house", 300000, () => houseInfo(h))?.favorites?.room?.path) : null;
    return [...arr(c.temperature), ...arr(c.humidity), ...arr(c.lights), ...Object.values(c.rooms_lights || {}).flat(), ...arr(c.alarms), ...arr(c.devices), c.weather, ...loud, ...items, ...arr(rc?.lights), rc?.climate].filter(Boolean);
  },
  grid: () => ({ columns: 12, rows: "auto" }),
  frame: () => "is-free",
  stub: (h) => {
    const ids = Object.keys(h?.states || {});
    return { label: t("hero.title"), temperature: ids.filter((x) => x.startsWith("climate.")).slice(0, 6), lights: ids.filter((x) => x.startsWith("light.")).slice(0, 6), weather: ids.find((x) => x.startsWith("weather.")) };
  },
  render(card, v) {
    const c = card.config;
    const m = read(card);
    const hl = headline(card, m);
    const wx = weather(card);
    const top = head({ title: c.label || t("hero.title"), words: hl.text, sub: hl.first, dot: hl.dot, tone: hl.dot !== "ok" && hl.dot, ctl: v === "S" ? "" : wx });
    const away = awayChip(card);
    const sub = hl.sub
      ? hl.sub.link
        ? `<button type="button" class="hf5-hq" data-act="service"><i class="hf5-dot is-${hl.sub.dot || "ok"}"></i><span>${esc(hl.sub.text)}</span><span aria-hidden="true">›</span></button>`
        : line(hl.sub.text, { dot: hl.sub.dot, cls: "hf5-hq" })
      : null;
    // v1 rhythm: no empty placeholder line between the headline and the facts (it left ~70px of air in the calm state)
    const keep = sub ?? "";
    const todoHtml = hl.todo ? msg(hl.todo, { icon: "alert" }) : "";
    const extra = v === "S" && wx ? `<div class="hf5-row">${wx}</div>` : "";
    return `${top}${keep}${todoHtml}${away ? `<div class="hf5-row">${away}</div>` : ""}${extra}${facts(card, m)}${favs(card, m)}`;
  },
  css: `${CALL_CSS}
.is-free>.hf5-ch .hf5-hw1{font-size:var(--hf-fs-num-s);font-weight:var(--hf-w-num-s);line-height:1.15;letter-spacing:-0.01em}
.is-free>.hf5-ch .hf5-hw1>.hf5-dot{margin-right:var(--hf-sp-4)}
.is-free>.hf5-ch .hf5-hl{flex-wrap:wrap;row-gap:var(--hf-sp-3)}
.is-free>.hf5-ch .hf5-hw{flex:1 1 calc(var(--hf-tile-min) * 2)}
.is-free>.hf5-ch .hf5-hc{margin-left:0}
.is-free .hf5-fv{font-size:var(--hf-fs-l)}
.hf5-card.is-free{padding-bottom:0}
.hf5-card.is-free.is-s>.hf5-ch .hf5-hw1{font-size:var(--hf-fs-xl)}
.hf5-hq{display:flex;align-items:center;gap:var(--hf-sp-2);min-width:0;min-height:var(--hf-sp-6);border:0;padding:0;background:none;color:var(--hf-ink-2);font:inherit;font-size:var(--hf-fs-s);text-align:left}
button.hf5-hq{min-height:var(--hf-hit);cursor:pointer}
.hf5-hq>span:not([aria-hidden]){min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.hf5-wx{max-width:100%;background:var(--hf-surface-2);box-shadow:none}
.hf5-wx>span{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.hf5-away.is-part{box-shadow:inset 0 0 0 var(--hf-focus-w) var(--hf-state-warn)}
.hf5-facts{display:flex;flex-wrap:wrap;gap:var(--hf-sp-3) var(--hf-sp-6);min-width:0}
.hf5-fact{display:flex;flex-direction:column;gap:var(--hf-sp-1);min-width:0;max-width:100%}
.hf5-fv{font-size:var(--hf-fs-m);font-weight:var(--hf-w-reg);overflow-wrap:anywhere}
.hf5-fv>.hf5-dot{display:inline-block;margin-right:var(--hf-sp-2);vertical-align:middle}
.hf5-card.is-s .hf5-facts{flex-direction:column}
.hf5-fr{grid-column:1/-1}
.hf5-fr>.hf5-pill{flex:1 1 calc(var(--hf-tile-min) * var(--hf5-k,1))}`,
  act(card, name, arg, el) {
    const h = card.hass;
    if (name === "service") {
      openService(h);
      return null;
    }
    if (name === "away") {
      openService(h);
      return null;
    }
    if (name === "room") {
      go(arg);
      return null;
    }
    if (name === "fav") {
      const id = el.dataset.id;
      const dom = id.split(".")[0];
      const kind = H.fav[dom];
      if (!h.states[id]) return null;
      if (kind === "run") return { key: id, calls: [{ domain: dom, service: "turn_on", data: { entity_id: id } }], after: (ok) => ok && notify(card, t("hero.scene_on", { name: nameOf(h.states[id], id) })) };
      if (kind === "toggle") return { key: id, calls: [{ domain: dom === "light" ? "light" : "homeassistant", service: "toggle", data: { entity_id: id } }] };
      return { more: id };
    }
    if (name === "mylight") {
      const lights = arr(roomCfg(arg)?.lights).filter((id) => h.states[id]);
      if (!lights.length) return null;
      const lit = lights.some((id) => on(h.states[id]));
      const ls = lights.filter((id) => id.startsWith("light."));
      const other = lights.filter((id) => !id.startsWith("light."));
      const svc = lit ? "turn_off" : "turn_on";
      const calls = [ls.length && { domain: "light", service: svc, data: { entity_id: ls } }, other.length && { domain: "homeassistant", service: svc, data: { entity_id: other } }].filter(Boolean);
      return { key: `mylight:${arg}`, calls };
    }
    if (name === "warm") {
      const id = roomCfg(arg)?.climate;
      const s = id && h.states[id];
      if (!s) return null;
      const m = climateModel(s);
      const base = card.opt[id]?.target ?? m.target;
      if (base == null) return null;
      const next = Math.min(val(s.attributes?.max_temp) ?? H.warm_max, base + Math.max(m.step, H.warm_min_step));
      return { key: id, calls: [{ domain: "climate", service: "set_temperature", data: { entity_id: id, temperature: next } }], opt: { [id]: { target: next } }, debounce: true };
    }
    return null;
  },
};
