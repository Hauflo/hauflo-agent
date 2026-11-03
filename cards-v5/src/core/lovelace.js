// The dashboard around a card: its views, the first page (home), a room card's config, the «Сервис» page.
// Same behaviour as the previous cards file (lovelacePanel / home() / roomCfg / AUTO.open), minus the scroll to a part.
import { go } from "./dom.js";

const panel = () => document.querySelector("home-assistant")?.shadowRoot?.querySelector("home-assistant-main")?.shadowRoot?.querySelector("ha-drawer partial-panel-resolver ha-panel-lovelace");
export const views = () => {
  const v = panel()?.lovelace?.config?.views;
  return Array.isArray(v) ? v : [];
};
/** The header of the dashboard's first page — the one that shows this person's favourites (no views known: only the building's card). */
export function isHome(allAlarms) {
  const vs = views();
  if (!vs.length) return !!allAlarms;
  const cur = decodeURIComponent(location.pathname.split("/")[2] || "");
  return !cur || cur === "0" || cur === String(vs[0].path ?? "");
}
/** The room card of this dashboard with `path` (its lights and thermostat). */
export function roomCfg(path) {
  let hit = null;
  const walk = (v) => {
    if (hit || !v || typeof v !== "object") return;
    if (Array.isArray(v)) return v.forEach(walk);
    if (v.type === "custom:hauflo-room" && v.path === path) hit = v;
    else Object.values(v).forEach(walk);
  };
  walk(views());
  return hit;
}
/** The «Сервис»/«Автоматизации» page: the view that holds hauflo-automations, else a full-screen overlay with that element. */
export function openService(hass) {
  const base = location.pathname.split("/")[1];
  const page = views().find((v) => JSON.stringify(v.cards || v.sections || []).includes("hauflo-automations"));
  if (page?.path) {
    if (location.pathname !== `/${base}/${page.path}`) go(`/${base}/${page.path}`);
    return true;
  }
  if (!customElements.get("hauflo-automations")) return false;
  const wrap = document.createElement("div");
  wrap.style.cssText = "position:fixed;inset:0;z-index:9999;overflow:auto;background:var(--primary-background-color)";
  const el = document.createElement("hauflo-automations");
  el.setConfig({ overlay: true });
  el.hass = hass;
  const close = () => (wrap.remove(), document.removeEventListener("keydown", onKey));
  const onKey = (e) => e.key === "Escape" && close();
  document.addEventListener("keydown", onKey);
  el.addEventListener("hauflo-close", close);
  wrap.appendChild(el);
  document.body.appendChild(wrap);
  return true;
}
