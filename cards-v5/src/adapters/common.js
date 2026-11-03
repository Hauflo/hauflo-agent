// Shared by every adapter: the normalized model's base (name, offline, role) and the favourite / timer plumbing.
import { t } from "../core/i18n.js";
import { clientName } from "../core/names.js";

export const OFFLINE = new Set(["unavailable", "unknown"]);
/** A sensor reading as a number if it is one and sane (lo..hi), else undefined — a stuck 9999 reads as «no data». */
export const reading = (v, lo = -Infinity, hi = Infinity) => {
  const n = v == null || v === "" ? NaN : +v;
  return Number.isFinite(n) && n >= lo && n <= hi ? n : undefined;
};
/** The unit as the house gives it; a 0…100 number with none reads as a percentage (as v1 did: «50 %»). */
const PCT = "%";
export const unitOf = (st) => {
  const a = st?.attributes || {};
  return a.unit_of_measurement || (+a.min === 0 && +a.max === 100 && a.min != null && a.max != null ? PCT : "");
};
export const stateOf =(hass, id) => hass?.states?.[id] || null;
export const base = (st, domain) => ({
  id: st.entity_id,
  domain,
  name: clientName(st.attributes?.friendly_name, st.entity_id),
  offline: OFFLINE.has(st.state),
  blocked: false,
  updated: st.last_updated || st.last_changed || "",
  source: sourceOf(st),
});
/** Where the data comes from (footer): the integration's name when HA tells it, else HA itself. */
const sourceOf = () => t("common.source_ha");

export const houseApi = {
  get: (h) => h.callApi("GET", "hauflo/house"),
  setFavorites: (h, favorites) => h.callApi("POST", "hauflo/house", { favorites }),
};
