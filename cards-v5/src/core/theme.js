// Shared service · theme: dark / light, "Крупно" from the system text size, one root for rem.
// Mode, first that answers: forced (o.mode) → the theme applied where the card sits (a view/dashboard set to
// «Hauflo Atelier · светлая/тёмная» says --eh-mode; any other theme → its page background's lightness) → HA's dark mode.
import { CANON } from "../gen/canon.js";

const lum = (c) => {
  const m = String(c).match(/rgba?\(([^)]+)\)/) || null;
  let rgb;
  if (m) rgb = m[1].split(",").slice(0, 3).map(Number);
  else {
    const h = String(c).trim().match(/^#([0-9a-f]{3}|[0-9a-f]{6})$/i)?.[1];
    if (!h) return null;
    const f = h.length === 3 ? h.split("").map((x) => x + x) : h.match(/../g);
    rgb = f.map((x) => parseInt(x, 16));
  }
  const [r, g, b] = rgb.map((v) => v / 255);
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
};
/** The mode of the theme applied around `el` (null when it cannot tell). */
export const themeModeAt = (el) => {
  if (!el?.isConnected) return null;
  const cs = getComputedStyle(el);
  const said = cs.getPropertyValue("--eh-mode").trim();
  if (said === "light" || said === "dark") return said;
  const L = lum(cs.getPropertyValue("--primary-background-color"));
  return L == null ? null : L > 0.5 ? "light" : "dark";
};
export const modeOf = (hass, forced, el) => forced || themeModeAt(el) || (hass?.themes?.darkMode === false ? "light" : "dark");
/** Mark a host element so the generated tokens (gen/tokens.js) apply. */
export const applyTheme = (el, hass, o = {}) => {
  el.classList.add("hf-host");
  el.setAttribute("data-hf-theme", "atelier");
  el.setAttribute("data-mode", modeOf(hass, o.mode, o.at));
  const k = Number(o.textScale) || 1;
  if (k >= CANON.textScale.largeFrom) el.setAttribute("data-hf-large", "");
  else el.removeAttribute("data-hf-large");
};
