// hauflo-devices · a block of devices by purpose or a sub-block («Вентиляция», «Котельная») — house spec v5 §2–3.
// Config (unchanged from the previous card): entities (string | {entity, name, icon, important}), title, block.
// Header: caps title · one summary — «авария · 1» > «нет связи · 2» > «2 из 5 вкл» > «всё в норме» (dot).
// Tiles: one-row, the integrator's order (trouble is in the summary, tiles never jump); lit = on; the status is a value
// («84 Вт», «45 %», «протечка»); a plain switch with no value says «вкл/выкл» as v1 did (owner canon 03.11 overrides K4). Tap = the safe action (toggle / press / start); class A (valve,
// boiler, lock, siren, «важное»), read-only and offline → the remote; hold = the remote. No «Выключить все»: only sockets
// have it (§2 «Группы»), and they live in the lights console.
import { t } from "../core/i18n.js";
import { cap, head, msg, tile } from "../kit/primitives.js";
import { trouble, troubleStates } from "../core/command.js";
import { deviceCall, deviceModel, switchable } from "../adapters/device.js";
import { blockTitle } from "../core/house.js";

const NBSP = " ";
const ROLE = "state-on";
const idOf = (e) => (typeof e === "string" ? e : e?.entity);
const items = (c) => (Array.isArray(c.entities) ? c.entities.filter((e) => idOf(e)) : []);

/** One device → the tile's model with the optimistic switch applied. */
function device(card, e) {
  const id = idOf(e);
  const st = card.hass.states[id];
  if (!st) return null;
  const m = deviceModel(st, typeof e === "object" ? e : {});
  const o = card.opt[id] || {};
  return { ...m, on: o.on ?? m.on, tr: trouble(m, card.cmd.of(id)) };
}
const all = (card) => items(card.config).map((e) => device(card, e)).filter(Boolean);

/** The block's one line (sub-block §3: alarm > no link > attention > «всё в норме»; a switch group: «N из M вкл»). */
function summary(ms) {
  const n = (f) => ms.filter(f).length;
  const alarm = n((m) => m.tone === "alarm");
  if (alarm) return { text: t("devices.alarm_n", { n: alarm }), dot: "alarm" };
  const off = n((m) => m.offline);
  if (off === ms.length) return { text: t("common.offline"), dot: "warn" };
  if (off) return { text: t("devices.offline_n", { n: off }), dot: "warn" };
  const warn = n((m) => m.tone === "warn");
  if (warn) return { text: t("devices.warn_n", { n: warn }), dot: "warn" };
  const sw = ms.filter(switchable);
  if (sw.length > 1) return { text: t("devices.count", { n: sw.filter((m) => m.on).length, total: sw.length }) };
  if (sw.length) return { text: "" }; // one switch: its tile says it all — no «1 из 1 вкл»
  return { text: t("devices.ok"), dot: "ok" };
}

function devTile(m, bare) {
  const status = m.tr === "offline" ? t("common.offline") : m.tr === "failed" ? t("common.failed") : m.tr === "sending" && m.action ? t("common.sending") : m.value || (switchable(m) ? t(m.on ? "common.on" : "common.off") : NBSP);
  return tile({
    size: "xs",
    bare,
    icon: m.icon,
    on: m.on && !m.offline,
    tint: m.tone,
    role: ROLE,
    badge: troubleStates(m.tr),
    name: m.name,
    status,
    warn: m.tone === "warn" || m.tr === "failed",
    short: m.tr === "offline" ? t("common.no_data") : null,
    cls: [m.tone === "alarm" && "is-alarm", m.tr === "sending" && "is-pending", m.offline && "is-offline"].filter(Boolean).join(" "),
    attr: {
      "data-tap": "tap",
      "data-id": m.id,
      role: "button",
      tabindex: "0",
      "aria-pressed": switchable(m) && !m.offline ? String(!!m.on) : null,
      "aria-label": `${m.name}${status.trim() ? ", " + status : ""}`,
    },
  });
}

export const DEVICES = {
  key: "devices",
  watch: (c) => items(c).map(idOf),
  grid: () => ({ columns: 12, rows: "auto", min_columns: 3 }),
  stub: (h) => ({
    title: t("devices.title"),
    entities: Object.keys(h?.states || {})
      .filter((x) => /^(fan|switch|valve|water_heater|vacuum|lock|humidifier)\./.test(x))
      .slice(0, 6),
  }),
  render(card, v) {
    const title = blockTitle(card, t("devices.title"));
    const ms = all(card);
    if (!ms.length) return `${head({ title })}${msg(t("devices.none"), { icon: "power" })}`;
    const s = summary(ms);
    const top = head({ title, quiet: true, words: s.text, dot: s.dot, tone: s.dot !== "ok" && s.dot });
    const bare = v === "S";
    return `${top}<div class="hf5-grid${bare ? " is-bare" : ""}">${ms.map((m) => devTile(m, bare)).join("")}</div>`;
  },
  css: `/* K7: an alarm is one even inset frame */
.hf5-tile.is-alarm{box-shadow:inset 0 0 0 var(--hf-focus-w) var(--hf-state-alarm)}
.hf5-tile.is-alarm .hf5-line{color:var(--hf-state-alarm-text)}`,
  act(card, name, arg, el) {
    if (name !== "tap") return null;
    const id = el.dataset.id;
    const e = items(card.config).find((x) => idOf(x) === id);
    const m = e && device(card, e);
    if (!m) return null;
    if (m.offline) return { more: id };
    if (m.tr === "failed") return { retry: id };
    const call = deviceCall(m);
    if (!call) return { more: id };
    return { key: id, calls: [call], ...(m.tap === "toggle" ? { opt: { [id]: { on: !m.on } } } : {}) };
  },
};
