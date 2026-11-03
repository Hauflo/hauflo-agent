// hauflo-alert · the alarm as the screen's one moment (sensors spec §5 + #8, foundations: 12 × auto, hides when all is well).
// Config (unchanged from the previous card): alarms [ids], all_alarms (every alarm of the house), rooms {id: room}, room,
// where, hide_ok, todo, action {entity_id, service: switch.turn_off | valve.close_valve, title, icon}, service {phone, email,
// company, name} (only when the house has no contact of its own).
// Colour by priority (ISA-101): critical (water, smoke, gas, CO) red rim + glow; accepted or an equipment fault — yellow.
// Accepting is never one tap: «Принять» is held 1 s (a tap says so); accepted ≠ cleared — it stays until the sensor is
// back to normal and the card says who accepted. The valve is held 1 s too; only shutting off, never a service of the
// config's choosing (security review 25.10).
import { t, tn } from "../core/i18n.js";
import { esc, notify, hideCard } from "../core/dom.js";
import { cap, ico, line, msg, holdPill } from "../kit/primitives.js";
import { houseClock } from "../core/format.js";
import { openAlarms, ackAlarms, isCritical, isDead } from "../core/alarms.js";
import { ICON, dcOf, alarmName, roomOf, wordOf, serviceOf, todo, CALL_CSS, FRAME_CSS } from "../core/contact.js";

const arr = (x) => (Array.isArray(x) ? x : []);
const ids = (c) => arr(c.alarms).filter((x) => typeof x === "string");
const SAFE_ACTIONS = ["switch.turn_off", "valve.close_valve"];
const SHOWN_MAX = 4; // more alarms listed under the first; the rest is «ещё N»
const actionOf = (c, h) => (c.action && SAFE_ACTIONS.includes(c.action.service) && h.states[c.action.entity_id] ? c.action : null);

export const ALERT = {
  key: "alert",
  watch: (c) => [...ids(c), c.action?.entity_id].filter(Boolean),
  grid: () => ({ columns: 12, rows: "auto", min_columns: 6 }),
  stub: (h) => ({ alarms: Object.keys(h?.states || {}).filter((x) => x.startsWith("binary_sensor.") && ICON[h.states[x].attributes?.device_class]).slice(0, 4) }),
  /** The card's rim: red for a critical alarm not yet accepted, yellow for the rest (K7: one even inset). */
  frame: (card) => card._q || "",
  render(card) {
    const c = card.config;
    const h = card.hass;
    const open = openAlarms(card, ids(c), !!c.all_alarms).filter((a) => h.states[a.entity]);
    card._q = "";
    if (!open.length) {
      const dead = ids(c).filter((id) => isDead(h.states[id])).length;
      hideCard(card, !!c.hide_ok && !dead);
      if (c.hide_ok && !dead) return "";
      card._q = dead ? "is-attn" : "";
      return `<div class="hf5-ch">${ico("shield", { tint: dead ? "warn" : null })}<div class="hf5-tx">${line(dead ? tn("alert.dead", dead) : t("alert.none"), { dot: dead ? "warn" : "ok", warn: !!dead })}</div></div>`;
    }
    hideCard(card, false);
    const top = open[0];
    const id = top.entity;
    const dc = dcOf(h, id);
    const crit = isCritical(h, top);
    // accepted or not critical → calm yellow until it clears; a new alarm comes back red (owner 28.10)
    const calm = !crit || !!top.ackedBy;
    card._q = calm ? "is-attn" : "is-alarm";
    const tone = calm ? "warn" : "alarm";
    const an = alarmName(h, id);
    const room = c.rooms?.[id] || c.room || "";
    const area = an.tech ? roomOf(c, h, id) : "";
    const where = open.length > 1 ? room || c.where || area : area && !an.name.toLowerCase().includes(area.toLowerCase()) ? area : "";
    const svc = serviceOf(card);
    const act = actionOf(c, h);
    const valve = act && dc === "moisture" ? act.title || "" : "";
    const when = [
      t("alert.since", { t: houseClock(h, top.since || h.states[id].last_changed) }),
      top.returned && t("alert.returned", { t: houseClock(h, top.returned) }),
      top.ackedBy && t("alert.acked", { name: top.ackedBy }),
    ].filter(Boolean);
    const rest = open.slice(1, 1 + SHOWN_MAX);
    const more = open.length - 1 - rest.length;
    const faults = open.filter((a) => a.key && !a.ackedBy && !isCritical(h, a));
    const ackTop = top.key && !top.ackedBy;
    const role = calm ? "state-warn" : "state-alarm";
    const rows = rest
      .map((a) => {
        const n = alarmName(h, a.entity).name;
        const r = roomOf(c, h, a.entity);
        const sub = [r, a.ackedBy && t("alert.acked", { name: a.ackedBy })].filter(Boolean).join(" · ");
        const q = !isCritical(h, a) || a.ackedBy ? "warn" : "alarm";
        return `<div class="hf5-alr"><button type="button" class="hf5-tap hf5-alt" data-more="${esc(a.entity)}"><i class="hf5-dot is-${q}"></i><span><b>${esc(n)}</b>${sub ? ` · ${esc(sub)}` : ""}</span></button>${
          a.key && !a.ackedBy ? holdPill(t("alert.accept"), { act: "ack", role: q === "alarm" ? "state-alarm" : "state-warn", cls: "hf5-alk", attr: { "data-arg": a.key } }) : ""
        }</div>`;
      })
      .join("");
    const acts = [
      act && holdPill(act.title || t("alert.run"), { act: "valve", icon: "valve", role: "state-alarm" }),
    ].filter(Boolean);
    const acks = [
      ackTop && holdPill(t("alert.accept"), { act: "ack", role, attr: { "data-arg": top.key } }),
      faults.length > 1 && holdPill(t("alert.accept_all", { n: faults.length }), { act: "ack_all", role: "state-warn" }),
    ].filter(Boolean);
    return `<div class="hf5-alh"><i class="hf5-dot is-${tone}"></i>${cap([wordOf(dc), where].filter(Boolean).join(" · "))}</div>
<button type="button" class="hf5-tap hf5-alm" data-more="${esc(id)}">${ico(ICON[dc] || "fault", { tint: tone })}<span class="hf5-aln">${esc(an.name)}</span></button>
${line(when.join(" · "), { cls: "hf5-alw" })}
${rows || more > 0 ? `<div class="hf5-als">${rows}${more > 0 ? `<div class="hf5-line">${esc(tn("alert.more", more))}</div>` : ""}</div>` : ""}
${msg(todo(c, h, id, svc, valve), { icon: "alert" })}
${acts.length ? `<div class="hf5-row hf5-ala">${acts.join("")}</div>` : ""}
${acks.length ? `<div class="hf5-row hf5-ala">${acks.join("")}</div>${line(t("alert.ack_hint"), { cls: "hf5-alw" })}` : ""}`;
  },
  act(card, name, arg) {
    const h = card.hass;
    if (name === "ack" || name === "ack_all") {
      const keys = name === "ack" ? [arg] : openAlarms(card, ids(card.config), !!card.config.all_alarms).filter((a) => a.key && !a.ackedBy && !isCritical(h, a)).map((a) => a.key);
      if (keys.length) ackAlarms(h, keys).then((ok) => ok || notify(card, t("alert.failed")));
      return null;
    }
    if (name === "valve") {
      const a = actionOf(card.config, h);
      if (!a) return null;
      const [domain, service] = a.service.split(".");
      return { key: a.entity_id, calls: [{ domain, service, data: { entity_id: a.entity_id } }], after: (ok) => ok || notify(card, t("common.failed")) };
    }
    return null;
  },
  /** Released before 1 s: the one hint. */
  early: (card, el) => notify(card, t("common.hold_hint", { action: el.textContent.trim().toLowerCase() })),
  css: `
${FRAME_CSS}
.hf5-alh{display:flex;align-items:center;gap:var(--hf-sp-2);min-width:0}
.hf5-alh>.hf5-cap{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.hf5-card.is-alarm .hf5-alh>.hf5-cap{color:var(--hf-state-alarm-text)}
.hf5-card.is-attn .hf5-alh>.hf5-cap{color:var(--hf-state-warn-text)}
.hf5-card.is-alarm .hf5-alh>.hf5-dot{box-shadow:0 0 var(--hf-sp-3) var(--hf-state-alarm-glow)}
.hf5-alm{display:flex;align-items:center;gap:var(--hf-sp-3);min-width:0;border:0;padding:0;background:none;color:inherit;font:inherit;text-align:left}
.hf5-aln{font-size:var(--hf-fs-num-s);font-weight:var(--hf-w-num-s);line-height:1.1;min-width:0;overflow-wrap:break-word;hyphens:auto}
.hf5-als{display:flex;flex-direction:column;gap:var(--hf-sp-2);min-width:0}
.hf5-alr{display:flex;align-items:center;gap:var(--hf-sp-2);min-width:0}
.hf5-alt{flex:1 1 auto;display:flex;align-items:center;gap:var(--hf-sp-2);min-width:0;min-height:var(--hf-hit);border:0;padding:0;background:none;color:var(--hf-ink-2);font:inherit;font-size:var(--hf-fs-s);text-align:left}
.hf5-alt b{font-weight:var(--hf-w-med);color:var(--hf-ink)}
.hf5-alt>span{min-width:0;overflow-wrap:break-word}
${CALL_CSS}
.hf5-alk{flex:none}
.hf5-alw{white-space:normal}
.hf5-alw>.hf5-t{overflow:visible;overflow-wrap:break-word}`,
};
