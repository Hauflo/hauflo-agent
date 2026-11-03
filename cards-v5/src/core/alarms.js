// Shared service · the house's alarms from the agent (GET /api/hauflo/alarms, notify.py): open alarms with who accepted
// them, the returned ones (back to normal, not yet seen) and which a phone was told about. Every card that shows alarms
// reads ONE store: asked again when a watched sensor changes or a minute passed; without the agent — HA's «on».
// Accepting is at once on every card (owner 28.10: «нажимаешь Принято — толку ноль»), then the agent records who.

import { t } from "./i18n.js";

const MIN_AGE = 60000;
const A = { at: 0, sig: "", data: null, wait: null, off: false, ids: new Set(), cards: new Set() };

/** A danger to people or the house — red, and never accepted together with equipment faults. */
export const DANGER = ["moisture", "smoke", "gas", "carbon_monoxide"];
export const isCritical = (h, a) => a.critical === true || (a.critical !== false && DANGER.includes(h?.states?.[a.entity]?.attributes?.device_class));
/** A sensor that says nothing (gone, unavailable, unknown). */
export const isDead = (s) => !s || s.state === "unavailable" || s.state === "unknown";

const redraw = () => {
  for (const c of A.cards) {
    if (!c.isConnected) continue;
    c._html = null;
    c.render();
  }
};
// The agent says 404 / 503 «not set up» when there is none: the cards show HA's own states and stop asking.
const noAgent = (e) => e?.status_code === 404 || (e?.status_code === 503 && /not set up/i.test(String(e?.body?.message || "")));

function refresh(h) {
  if (!h?.callApi || A.off || A.wait) return;
  const sig = [...A.ids].map((id) => h.states[id]?.state).join();
  if (sig === A.sig && Date.now() - A.at < MIN_AGE) return;
  A.sig = sig;
  A.wait = h
    .callApi("GET", "hauflo/alarms")
    .then((d) => (A.data = d && typeof d === "object" ? d : null))
    .catch((e) => {
      A.data = null;
      A.off = noAgent(e);
    })
    .finally(() => {
      A.wait = null;
      A.at = Date.now();
      redraw();
    });
}

/** What the agent said (null: not yet / no agent). */
export const alarmData = () => A.data;
/** Did a phone get a notice of this sensor's alarm. */
export const pushed = (id) => !!A.data?._pushed?.includes(id);

/** The open alarms of these sensors (all: every alarm of the house — the building's card), most urgent first
 *  (the agent's records, plus any watched sensor HA shows «on» that it does not list):
 *  not returned → not accepted → critical → oldest. [{key, entity, since, ackedBy, critical, returned}] */
export function openAlarms(card, ids, all = false) {
  const h = card.hass;
  A.cards.add(card);
  for (const id of ids) A.ids.add(id);
  refresh(h);
  const list = Array.isArray(A.data?._list) ? A.data._list : [];
  const want = new Set(ids);
  if (all) for (const a of list) want.add(a.entity);
  const seen = new Set();
  const known = list.filter((a) => a && want.has(a.entity) && !a.noData && !seen.has(a.entity) && seen.add(a.entity));
  // HA says «on» but the agent has no record (no agent, or the sensor is in none of its rules): shown all the same,
  // without «Принять» — a leak is never hidden because a list missed it
  const bare = ids.filter((id) => !seen.has(id) && h?.states[id]?.state === "on").map((id) => ({ entity: id, since: h.states[id].last_changed }));
  return [...known, ...bare]
    .sort(
      (a, b) =>
        Number(!!a.returned) - Number(!!b.returned) ||
        Number(!!a.ackedBy) - Number(!!b.ackedBy) ||
        Number(isCritical(h, b)) - Number(isCritical(h, a)) ||
        String(a.since).localeCompare(String(b.since)),
    );
}

/** Accept alarms by key: shown at once as accepted by this person, then posted; false → put back. */
export async function ackAlarms(h, keys) {
  const me = h.user?.name || t("alert.you");
  const items = (A.data?._list || []).filter((a) => keys.includes(a.key) && !a.ackedBy);
  items.forEach((a) => (a.ackedBy = me));
  redraw();
  try {
    await h.callApi("POST", "hauflo/alarms", keys.length === 1 ? { ack: keys[0] } : { ack_keys: keys });
  } catch {
    items.forEach((a) => (a.ackedBy = null));
    redraw();
    return false;
  }
  A.at = 0;
  A.sig = "";
  refresh(h);
  return true;
}

/** Tests: forget everything. */
export function _resetAlarms() {
  Object.assign(A, { at: 0, sig: "", data: null, wait: null, off: false });
  A.ids.clear();
  A.cards.clear();
}
