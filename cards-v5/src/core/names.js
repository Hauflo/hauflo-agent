// Shared service · the client's names (ported from the previous cards, review round 2): HA keeps the ETS name as is, the
// client's cards drop the integrator's working notes — a device / model code in brackets at the end («Движение (bps7)»)
// goes unless two things of one room would then read the same; an ETS note with «?» isn't a name: the card says what
// the thing is. The house (hass) is set once per update by the card element.
import { t } from "./i18n.js";

let HASS = null;
let COUNT = { states: null, base: new Map() };
export const setHouse = (h) => (HASS = h);

const trimSep = (s) => String(s ?? "").replace(/^[\s,;·•|/\-–—:]+|[\s,;·•|/\-–—:]+$/g, "").trim();
/** «Движение (bps7)» → «Движение»; null when the tail is not a code (letters and digits, ≤ 16). */
export const techTail = (n) => {
  const m = /^(.*\S)\s*\(\s*([^()]+?)\s*\)\s*$/.exec(n);
  return m && /^[A-Za-z0-9._/ -]{2,16}$/.test(m[2]) && /[A-Za-z]/.test(m[2]) && /\d/.test(m[2]) ? m[1] : null;
};
const areaOf = (id) => HASS?.entities?.[id]?.area_id || "";
/** How many entities of one HA area read `base` once their tails are dropped — counted once per states object. */
const sameBase = (id, base) => {
  if (!HASS?.states) return 1;
  if (COUNT.states !== HASS.states) {
    const c = new Map();
    for (const [eid, s] of Object.entries(HASS.states)) {
      const n = String(s?.attributes?.friendly_name || "");
      const key = `${areaOf(eid)}|${(techTail(n) ?? n).toLowerCase()}`;
      c.set(key, (c.get(key) || 0) + 1);
    }
    COUNT = { states: HASS.states, base: c };
  }
  return COUNT.base.get(`${areaOf(id)}|${base.toLowerCase()}`) || 1;
};
/** The name a client reads for an entity. */
export const clientName = (raw, id = "") => {
  const n = String(raw || "").trim();
  if (!n) return id;
  if (n.includes("?")) {
    const k = `kind.${String(id).split(".")[0]}`;
    return t(k) === k ? t("kind.other") : t(k);
  }
  const b = techTail(n);
  return trimSep(b && sameBase(id, b) < 2 ? b : n) || n;
};
