// «Нет дома — имитация» (the agent's presence simulation): asked once a minute for all the headers on the page.
// Working only in part is said on the chip too: what is missing (final review 28.10).
import { CANON } from "../gen/canon.js";

const NO_DEVICES = ["no_devices", "no_recorder"];
/** The agent's presence answer, cached per card (null: no agent, or an agent without presence). */
export const presence = (card) => card.load("presence", CANON.hero.presence_ttl_ms, () => card.hass.callApi("GET", "hauflo/presence"));

/** «Нет дома» on but working only in part: {kind: "covers" | "lights" | "none", why, sun} — or null when everything is planned. */
export function presPartial(pr, now = Date.now()) {
  if (!pr?.enabled) return null;
  if (NO_DEVICES.includes(pr.status)) return { kind: "none", why: pr.status, sun: false };
  const tn = pr.tonight;
  if (!tn) return null;
  const wantLights = pr.lights && (pr.devices?.lights ?? 1) > 0;
  const wantCovers = pr.covers && (pr.devices?.covers ?? 1) > 0;
  const rooms = tn.rooms || [];
  const lightsOk = !wantLights || rooms.some((r) => Date.parse(r.to) > now);
  const coversOk = !wantCovers || !!(tn.covers?.open || tn.covers?.close);
  if (lightsOk && coversOk) return null;
  const why = !lightsOk ? (tn.lights === "few" || tn.lights === "no_location" ? tn.lights : rooms.length ? "late" : "none") : "covers";
  const kind = wantLights && lightsOk ? "lights" : wantCovers && coversOk ? "covers" : "none";
  return { kind, why, sun: why === "few" || why === "none" };
}
