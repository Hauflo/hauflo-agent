// Tiny DOM/string helpers shared by every layer. No literals of looks here.
export const esc = (x) => String(x ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
export const cls = (...c) => c.filter(Boolean).join(" ");
/** {a: 1, b: true, c: null} → ` a="1" b` (null/false/undefined dropped). */
export const attrs = (o = {}) =>
  Object.entries(o)
    .filter(([, v]) => v != null && v !== false)
    .map(([k, v]) => (v === true ? ` ${k}` : ` ${k}="${esc(v)}"`))
    .join("");
/** CSS custom properties only: {"--hf5-role": "var(--hf-state-light)"} → ` style="…"`. */
export const vars = (o = {}) => {
  const v = Object.entries(o).filter(([, x]) => x != null && x !== "").map(([k, x]) => `${k}:${x}`).join(";");
  return v ? ` style="${esc(v)}"` : "";
};
/** Role name (token) → CSS value: "state-light" → var(--hf-state-light). */
export const role = (r, kind = "") => (r ? `var(--hf-${r}${kind ? "-" + kind : ""})` : null);
export const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
/** A short message at the bottom of the screen (Home Assistant's own toast). */
/** HA's own navigation to a dashboard path (no reload). */
export function go(path) {
  history.pushState(null, "", path);
  window.dispatchEvent(new CustomEvent("location-changed", { detail: { replace: false } }));
}
export const notify = (el, message) => el.dispatchEvent(new CustomEvent("hass-notification", { bubbles: true, composed: true, detail: { message } }));
/** A card that has nothing to say hides itself the way HA's own do (its wrapper and an empty section follow), and shows again. */
export function hideCard(card, hide) {
  if (card.hidden === hide) return;
  card.hidden = hide;
  card.style.display = hide ? "none" : "";
  card.dispatchEvent(new CustomEvent("card-visibility-changed", { detail: { value: !hide } }));
}
