// hauflo-security · «Охрана»: the alarm panel, locks, doors / windows / motion of the house.
// Config (unchanged from the previous card): name, entity (alarm_control_panel), locks [id | {entity, name}], openings [id | {entity, name}].
// The card decides nothing: it shows the alarm's state and sends arm / disarm. A CODE is never typed into a card: a panel (or a lock) that
// wants one opens its remote (HA's own dialog). A lock is a releasing action: it locks / unlocks by a 1 s HOLD on its pill (a tap says so);
// a tap on any row = the entity's remote. Unknown is said as unknown — never «всё закрыто» made up from no data.
import { t } from "../core/i18n.js";
import { CANON } from "../gen/canon.js";
import { notify } from "../core/dom.js";
import { head, tile, seg, holdPill, msg } from "../kit/primitives.js";
import { trouble, troubleStates } from "../core/command.js";
import { isDead } from "../core/alarms.js";
import { alarmName, FRAME_CSS } from "../core/contact.js";

const S = CANON.security;
const idOf = (e) => (typeof e === "string" ? e : e?.entity);
const nameCfg = (e) => (typeof e === "object" ? e?.name : "");
const items = (x) => (Array.isArray(x) ? x.filter((e) => typeof idOf(e) === "string" && idOf(e)) : []);

const name = (h, e) => nameCfg(e) || alarmName(h, idOf(e)).name;
const kindOf = (s) => (isDead(s) ? "dead" : S.states[s.state] || "ok");
const modeOf = (s) => S.modes.find((m) => m.state === s.state)?.v || "";
const modesOf = (s) => S.modes.filter((m) => !m.feature || (s.attributes?.supported_features & m.feature));

function read(card) {
  const c = card.config;
  const h = card.hass;
  const panel = c.entity ? h.states[c.entity] || null : null;
  const locks = items(c.locks).map((e) => ({ id: idOf(e), e, s: h.states[idOf(e)] })).filter((x) => x.s);
  const opens = items(c.openings).map((e) => ({ id: idOf(e), e, s: h.states[idOf(e)] })).filter((x) => x.s);
  for (const x of locks) x.dead = isDead(x.s);
  for (const x of opens) x.dead = isDead(x.s);
  const lockKind = (x) => (x.dead ? "dead" : x.s.state === "locked" ? "locked" : x.s.state === "jammed" ? "jammed" : x.s.state === "locking" || x.s.state === "unlocking" ? "moving" : "unlocked");
  for (const x of locks) x.kind = lockKind(x);
  const openKind = (x) => (x.dead ? "dead" : S.motion_classes.includes(x.s.attributes?.device_class) ? (x.s.state === "on" ? "motion" : "still") : x.s.state === "on" ? "open" : "closed");
  for (const x of opens) x.kind = openKind(x);
  const openN = opens.filter((x) => x.kind === "open").length;
  const openND = opens.filter((x) => x.dead).length;
  const lockND = locks.filter((x) => x.dead).length;
  const unlocked = locks.filter((x) => x.kind === "unlocked" || x.kind === "jammed").length;
  return { panel, pk: panel ? kindOf(panel) : "", locks, opens, openN, openND, lockND, unlocked };
}

/** The glyph of an opening: a window, a motion sensor, else a door. */
const openIcon = (x) => (x.s.attributes?.device_class === "window" ? (x.kind === "open" ? "windowOpen" : "window") : S.motion_classes.includes(x.s.attributes?.device_class) ? "motion" : x.kind === "open" ? "doorOpen" : "door");

function lockRow(card, x) {
  const h = card.hass;
  const cmd = card.cmd.of(x.id);
  const nm = name(h, x.e);
  const word = cmd.failed ? t("common.failed") : cmd.sending ? t("common.sending") : t(`security.lock.${x.kind}`);
  const bad = x.kind === "unlocked" || x.kind === "jammed";
  const can = x.kind === "locked" || x.kind === "unlocked" || x.kind === "jammed";
  const label = t(x.kind === "locked" ? "security.unlock" : "security.lock_do");
  const ctl = can ? holdPill("", { act: "lock", icon: x.kind === "locked" ? "lockOpen" : "lock", label, attr: { "data-arg": x.id } }) : "";
  return tile({
    size: "xs",
    icon: x.kind === "locked" ? "lock" : "lockOpen",
    tint: bad ? "warn" : null,
    badge: troubleStates(trouble({ offline: x.dead }, cmd)),
    name: nm,
    status: word,
    warn: !!cmd.failed || bad,
    cls: [x.dead && "is-offline", "sc-row"].filter(Boolean).join(" "),
    ctl,
    attr: { "data-tap": "open", "data-id": x.id, role: "button", tabindex: "0", "aria-label": `${nm}, ${word}` },
  });
}

/** An opening: icon · name / status (no control). */
function openRow(card, x) {
  const nm = name(card.hass, x.e);
  const word = t(`security.open.${x.kind}`);
  return tile({
    size: "xs",
    icon: openIcon(x),
    tint: x.kind === "open" ? "warn" : null,
    badge: x.dead ? { offline: true } : null,
    name: nm,
    status: word,
    warn: x.kind === "open",
    cls: [x.dead && "is-offline", "sc-row"].filter(Boolean).join(" "),
    attr: { "data-tap": "open", "data-id": x.id, role: "button", tabindex: "0", "aria-label": `${nm}, ${word}` },
  });
}

export const SECURITY = {
  key: "security",
  watch: (c) => [c.entity, ...items(c.locks).map(idOf), ...items(c.openings).map(idOf)].filter(Boolean),
  grid: () => ({ columns: 12, rows: "auto", min_columns: 3 }),
  stub: (h) => ({
    entity: Object.keys(h?.states || {}).find((x) => x.startsWith("alarm_control_panel.")),
    locks: Object.keys(h?.states || {}).filter((x) => x.startsWith("lock.")).slice(0, 2),
    openings: Object.keys(h?.states || {}).filter((x) => x.startsWith("binary_sensor.") && S.open_classes.includes(h.states[x].attributes?.device_class)).slice(0, 4),
  }),
  frame: (card) => card._q || "",
  render(card, v) {
    const c = card.config;
    const title = (typeof c.name === "string" && c.name) || t("security.title");
    const m = read(card);
    card._q = m.pk === "alarm" ? "is-alarm" : m.pk === "dead" || m.unlocked || m.openN ? "is-attn" : "";
    if (!m.panel && !m.locks.length && !m.opens.length) return `${head({ title })}${msg(t("security.none"), { icon: "shield" })}`;
    const cmd = c.entity ? card.cmd.of(c.entity) : { sending: false, failed: false };
    const openWords = m.openN ? t("security.opened", { n: m.openN }) : m.openND ? t("security.nodata", { n: m.openND }) : m.opens.length ? t("security.closed") : "";
    const lockWords = !m.locks.length ? "" : m.unlocked ? t("security.unlocked", { n: m.unlocked }) : m.lockND ? t("security.lockdead", { n: m.lockND }) : t("security.locked");
    const sub = [openWords, lockWords].filter(Boolean).join(" · ");
    const state = m.panel ? m.panel.state : "";
    const words = !m.panel ? openWords || lockWords : m.pk === "dead" ? t("common.offline") : cmd.failed ? t("common.failed") : t(`security.state.${state}`) === `security.state.${state}` ? state : t(`security.state.${state}`);
    const top = head({
      title,
      words,
      sub: m.panel ? sub : m.opens.length && m.locks.length ? lockWords : "",
      dot: m.pk === "alarm" ? "alarm" : m.pk === "ok" || (!m.panel && !m.openN && !m.unlocked) ? "ok" : "warn",
      tone: m.pk === "alarm" ? "alarm" : m.pk === "wait" || m.pk === "dead" || (!m.panel && (m.openN || m.unlocked)) ? "warn" : null,
    });
    const modes = m.panel && m.pk !== "dead"
      ? seg(
          modesOf(m.panel).map((md) => ({ v: md.v, label: t(`security.mode.${md.v}`), icon: md.icon, aria: t(`security.mode_do.${md.v}`) })),
          { sel: modeOf(m.panel), act: "arm", icons: v === "S", l: true, role: m.pk === "alarm" ? "state-alarm" : "state-on", label: t("security.title") },
        )
      : "";
    const rows = [...m.locks.map((x) => lockRow(card, x)), ...m.opens.map((x) => openRow(card, x))].join("");
    return `${top}${modes}${rows ? `<div class="hf5-grid is-rows">${rows}</div>` : ""}`;
  },
  act(card, name, arg, el) {
    const c = card.config;
    if (name === "open") return { more: el.dataset.id };
    if (name === "arm") {
      const p = c.entity && card.hass.states[c.entity];
      const md = S.modes.find((x) => x.v === arg);
      if (!p || !md || isDead(p) || !modesOf(p).includes(md)) return null;
      // A code is asked by HA's own dialog — never typed into a card.
      if (p.attributes?.code_format) return { more: c.entity };
      return { key: c.entity, calls: [{ domain: "alarm_control_panel", service: md.service, data: { entity_id: c.entity } }], after: (ok) => ok || notify(card, t("common.failed")) };
    }
    if (name === "lock") {
      const ok = items(c.locks).map(idOf).includes(arg);
      const s = card.hass.states[arg];
      if (!ok || !s || isDead(s) || s.state === "locking" || s.state === "unlocking") return null;
      if (s.attributes?.code_format) return { more: arg };
      return { key: arg, calls: [{ domain: "lock", service: s.state === "locked" ? "unlock" : "lock", data: { entity_id: arg } }], after: (done) => done || notify(card, t("common.failed")) };
    }
    return null;
  },
  /** A lock pill released before 1 s: the one hint. */
  early: (card, el) => notify(card, t("common.hold_hint", { action: (el.getAttribute("aria-label") || el.textContent.trim()).toLowerCase() })),
  css: `${FRAME_CSS}
.hf5-tile.is-xs>.hf5-tx{min-width:0}
.hf5-tile.is-xs .hf5-pill{min-width:var(--hf-ctl);max-width:100%;padding:0 var(--hf-sp-3)}
.hf5-card.is-s .hf5-seg{display:grid;grid-template-columns:repeat(2,minmax(0,1fr))}
.hf5-card.is-s .hf5-seg.is-icons>.hf5-pill{min-width:0}
.hf5-card.is-s .hf5-tile.sc-row{flex-wrap:wrap}
.hf5-card.is-s .hf5-tile.sc-row>.hf5-tc{margin-inline-start:auto}
.hf5-ch.is-head+.hf5-seg{margin-top:var(--hf-sp-3)}
.hf5-seg+.hf5-grid{margin-top:var(--hf-sp-3)}`,
};
