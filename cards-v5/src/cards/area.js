// hauflo-area · whatever HA puts in a room (another integration's entities too), in Hauflo's own look, by kind.
// Config (unchanged from the previous card): area (HA area id), title. The cloud never names foreign entities: members are read here
// from HA's registry — the entity's own area, else its device's; not hidden, not diagnostic / config.
// Blocks: thermostats (± like the climate rows) · points and faults · switches (tap) · settings (numbers ±, lists → remote) ·
// readings · buttons / scenes (1 s HOLD: a button another integration made never fires on a tap) · other (tap = the remote).
// What can open the house or the water (lock, valve, siren, panel, camera, gate) is read-only here: a tap opens HA's own dialog.
import { t, num } from "../core/i18n.js";
import { cap, head, msg, tile, step, number, holdPill } from "../kit/primitives.js";
import { trouble, troubleStates } from "../core/command.js";
import { deviceCall, deviceModel } from "../adapters/device.js";
import { coverModel, coverStatus } from "../adapters/cover.js";
import { statusModel, tierRank } from "../adapters/status.js";
import { climateRow, CLIMATE } from "./climate.js";
import { areaId } from "../core/contact.js";
import { CANON } from "../gen/canon.js";
import { notify } from "../core/dom.js";

const KINDS = ["climate", "status", "switches", "settings", "values", "buttons", "other"];
const TOGGLE = new Set(["switch", "light", "fan", "input_boolean", "humidifier"]);
const NUM = new Set(["number", "input_number"]);
const NBSP = " ";
const dom = (id) => id.split(".")[0];
const offline = (s) => s.state === "unavailable" || s.state === "unknown";
const textual = (s) => Number.isNaN(Number(s.state));
const faulty = (s) => new RegExp(CANON.status.fault_name, "i").test(`${s.attributes?.device_class || ""} ${s.attributes?.friendly_name || s.entity_id}`);

/** The name without what the page already says: the room's and the device's. */
function label(h, id, area) {
  const s = h.states[id];
  let n = String(s?.attributes?.friendly_name || id.split(".")[1] || id);
  const dev = h.devices?.[h.entities?.[id]?.device_id];
  for (const x of [h.areas?.[area]?.name, dev?.name_by_user, dev?.name]) {
    if (x && n.toLowerCase().startsWith(x.toLowerCase()) && n.length > x.length + 1) n = n.slice(x.length).replace(/^[\s_:·.,-]+/, "");
  }
  n = n.replace(/_+/g, " ").replace(/\s{2,}/g, " ").trim();
  return n ? n[0].toUpperCase() + n.slice(1) : id;
}

function kindOf(s) {
  const d = dom(s.entity_id);
  if (d === "climate") return "climate";
  if (d === "binary_sensor" || d === "water_heater" || (d === "sensor" && !offline(s) && textual(s) && faulty(s))) return "status";
  if (d === "sensor") return "values";
  if (NUM.has(d) || d === "select" || d === "input_select") return "settings";
  if (TOGGLE.has(d) || d === "cover") return "switches";
  if (["button", "input_button", "script", "scene"].includes(d)) return "buttons";
  return "other";
}

/** The area's members {id, s, name, kind}, sorted by name. */
function members(card) {
  const h = card.hass;
  const area = card.config.area;
  if (!h || !area) return [];
  return Object.keys(h.entities || {})
    .filter((id) => h.states[id] && !h.entities[id].hidden && !h.entities[id].entity_category && areaId(h, id) === area)
    .map((id) => ({ id, s: h.states[id], name: label(h, id, area), kind: kindOf(h.states[id]) }))
    .sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true, sensitivity: "base" }));
}

const tapAttr = (id, name, status) => ({ "data-tap": "open", "data-id": id, role: "button", tabindex: "0", "aria-label": status.trim() ? `${name}, ${status}` : name });

/** A text sensor that reports a fault: red unless it says all is well. */
const textFault = (s) => dom(s.entity_id) === "sensor" && !offline(s) && !new RegExp(CANON.status.fault_ok, "i").test(String(s.state).trim());

/** One point → its status model, a fault-named text sensor counted as a fault unless it says all is well. */
function point(card, x) {
  const st = statusModel(x.s, { name: x.name }, card.hass.states);
  if (dom(x.id) !== "sensor" || st.offline) return st;
  return { ...st, word: x.s.state, tier: textFault(x.s) ? "fault" : null };
}

function pointRow(card, x) {
  const st = point(card, x);
  const tr = trouble({ offline: !!st.offline }, card.cmd.of(x.id));
  const bad = st.tier === "alarm" || st.tier === "fault" || st.tier === "warn";
  return tile({
    size: "xs",
    icon: st.icon,
    on: !!st.on && !st.offline,
    tint: st.tier === "alarm" ? "alarm" : bad ? "warn" : null,
    badge: troubleStates(tr),
    name: x.name,
    status: st.word || NBSP,
    warn: bad,
    cls: [st.offline && "is-offline", st.tier === "alarm" && "is-alarm"].filter(Boolean).join(" "),
    attr: tapAttr(x.id, x.name, st.word || ""),
  });
}

function deviceRow(card, x) {
  const m = deviceModel(x.s, { name: x.name });
  if (dom(x.id) === "cover") Object.assign(m, (({ icon, on }) => ({ icon, on }))(coverModel(x.s)), { value: coverStatus(coverModel(x.s)) });
  const on = (card.opt[x.id] || {}).on ?? m.on;
  const tr = trouble(m, card.cmd.of(x.id));
  const status = tr === "offline" ? t("common.offline") : tr === "failed" ? t("common.failed") : tr === "sending" && m.action ? t("common.sending") : m.value || NBSP;
  return tile({
    size: "xs",
    icon: m.icon,
    on: on && !m.offline,
    tint: m.tone,
    role: m.role,
    badge: troubleStates(tr),
    name: x.name,
    status,
    warn: m.tone === "warn" || tr === "failed",
    short: tr === "offline" ? t("common.no_data") : null,
    cls: [tr === "sending" && "is-pending", m.offline && "is-offline", m.tone === "alarm" && "is-alarm"].filter(Boolean).join(" "),
    attr: { ...tapAttr(x.id, x.name, status), "aria-pressed": m.tap === "toggle" && !m.guard && !m.offline ? String(!!on) : null },
  });
}

function settingRow(card, v, x) {
  if (!NUM.has(dom(x.id)) || offline(x.s)) return deviceRow(card, x);
  const val = (card.opt[x.id] || {}).val ?? Number(x.s.state);
  const unit = x.s.attributes?.unit_of_measurement || "";
  const n = number(num(val, 1), { size: "xs", unit });
  const tr = trouble({ offline: false }, card.cmd.of(x.id));
  return tile({
    size: "xs",
    icon: deviceModel(x.s).icon,
    badge: troubleStates(tr),
    name: x.name,
    status: tr === "failed" ? t("common.failed") : NBSP,
    warn: tr === "failed",
    ctl: v === "S" ? n : step(n, { act: "nstep", attr: { "data-id": x.id } }),
    cls: "ar-row",
    attr: tapAttr(x.id, x.name, `${num(val, 1)}${unit}`),
  });
}

function buttonRow(card, x) {
  const m = deviceModel(x.s, { name: x.name });
  const tr = trouble(m, card.cmd.of(x.id));
  const ctl = m.offline ? "" : holdPill("", { act: "press", icon: m.icon, label: t("area.press", { name: x.name }), attr: { "data-arg": x.id } });
  const status = tr === "offline" ? t("common.offline") : tr === "failed" ? t("common.failed") : tr === "sending" ? t("common.sending") : NBSP;
  return tile({
    size: "xs",
    icon: m.icon,
    badge: troubleStates(tr),
    name: x.name,
    status,
    warn: tr === "failed",
    ctl,
    cls: ["ar-row", tr === "sending" && "is-pending", m.offline && "is-offline"].filter(Boolean).join(" "),
    attr: tapAttr(x.id, x.name, status),
  });
}

function row(card, v, x) {
  if (x.kind === "climate") return climateRow(card, v, x.id, x.name);
  if (x.kind === "status") return dom(x.id) === "water_heater" ? deviceRow(card, x) : pointRow(card, x);
  if (x.kind === "settings") return settingRow(card, v, x);
  if (x.kind === "buttons") return buttonRow(card, x);
  return deviceRow(card, x);
}

/** The header line: alarm > fault > no link > attention > «всё в норме». */
function summary(card, ms) {
  const n = { alarm: 0, fault: 0, nolink: 0, warn: 0 };
  for (const x of ms) {
    if (offline(x.s)) n.nolink++;
    else if (x.kind === "status" && dom(x.id) !== "water_heater") {
      const tier = point(card, x).tier;
      if (tier === "nolink") n.nolink++;
      else if (tier) n[tier]++;
    } else if (x.kind !== "climate") {
      const tone = deviceModel(x.s).tone;
      if (tone) n[tone]++;
    }
  }
  if (n.alarm) return { text: t("devices.alarm_n", { n: n.alarm }), dot: "alarm" };
  if (n.fault) return { text: t("acs.fault_n", { n: n.fault }), dot: "warn" };
  if (n.nolink) return { text: n.nolink === ms.length ? t("common.offline") : t("devices.offline_n", { n: n.nolink }), dot: "warn" };
  if (n.warn) return { text: t("devices.warn_n", { n: n.warn }), dot: "warn" };
  return { text: t("devices.ok"), dot: "ok" };
}

export const AREA = {
  key: "area",
  watch: (c, card) => (card ? members(card).map((x) => x.id) : []),
  grid: () => ({ columns: 12, rows: "auto", min_columns: 3 }),
  stub: (h) => ({ area: Object.keys(h?.areas || {})[0] || "" }),
  render(card, v) {
    const c = card.config;
    const title = (typeof c.title === "string" && c.title) || card.hass.areas?.[c.area]?.name || t("area.title");
    const ms = members(card);
    if (!ms.length) return `${head({ title })}${msg(t(card.hass.user?.is_admin ? "area.none_admin" : "area.none"), { icon: "home" })}`;
    const s = summary(card, ms);
    const blocks = KINDS.map((k) => ({ k, xs: ms.filter((x) => x.kind === k) })).filter((b) => b.xs.length);
    const rank = (x) => tierRank(point(card, x));
    const body = blocks
      .map((b) => {
        const xs = b.k === "status" ? [...b.xs].sort((p, q) => rank(p) - rank(q)) : b.xs;
        return `<section class="ar-b">${blocks.length > 1 ? cap(t(`area.block.${b.k}`)) : ""}<div class="hf5-grid is-rows">${xs.map((x) => row(card, v, x)).join("")}</div></section>`;
      })
      .join("");
    return `${head({ title, words: s.text, dot: s.dot, tone: s.dot !== "ok" && s.dot })}${body}`;
  },
  act(card, name, arg, el) {
    const id = el?.dataset.id || arg;
    const x = members(card).find((m) => m.id === id);
    if (!x) return null;
    if (x.kind === "climate") return CLIMATE.act(card, name, arg, el);
    if (name === "open") {
      if (card.cmd.of(id).failed) return { retry: id };
      const m = deviceModel(x.s);
      const call = x.kind === "switches" && !m.offline && m.tap === "toggle" ? deviceCall(m) : null;
      return call ? { key: id, calls: [call], opt: { [id]: { on: !m.on } } } : { more: id };
    }
    if (name === "nstep" && NUM.has(dom(id)) && !offline(x.s)) {
      const a = x.s.attributes || {};
      const stp = Number(a.step) || 1;
      const cur = card.opt[id]?.val ?? Number(x.s.state);
      if (Number.isNaN(cur)) return null;
      const lo = a.min != null ? Number(a.min) : -Infinity;
      const hi = a.max != null ? Number(a.max) : Infinity;
      const val = Math.min(hi, Math.max(lo, Math.round((cur + (arg === "-1" ? -stp : stp)) * 1e6) / 1e6));
      return { key: id, calls: [{ domain: dom(id), service: "set_value", data: { entity_id: id, value: val } }], opt: { [id]: { val } }, debounce: true };
    }
    if (name === "press" && x.kind === "buttons" && x.s.state !== "unavailable") {
      const call = deviceCall(deviceModel(x.s));
      return call ? { key: id, calls: [call], after: (ok) => ok || notify(card, t("common.failed")) } : { more: id };
    }
    return null;
  },
  /** A button pill released before 1 s: the one hint. */
  early: (card, el) => notify(card, t("common.hold_hint", { action: (el.getAttribute("aria-label") || el.textContent.trim()).toLowerCase() })),
  css: `.ar-b+.ar-b{margin-top:var(--hf-sp-3)}
.ar-b>.hf5-cap{margin-bottom:var(--hf-sp-2)}
.hf5-tile.is-xs>.hf5-tx{min-width:0}
.hf5-tile.is-xs .hf5-pill{min-width:var(--hf-ctl);max-width:100%;padding:0 var(--hf-sp-3)}
.hf5-tile.is-alarm{box-shadow:inset 0 0 0 var(--hf-focus-w) var(--hf-state-alarm)}
.hf5-card.is-s .hf5-tile.ar-row{flex-wrap:wrap}
.hf5-card.is-s .hf5-tile.ar-row>.hf5-tc{margin-inline-start:auto}
.hf5-ch.is-head+.ar-b{margin-top:var(--hf-sp-3)}`,
};
