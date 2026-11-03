// The one glyph set (canon/icons.json → gen/icons.js). A glyph is a kit name or ready markup.
// An entry is a filled path (legacy string, or {f}) and/or a line path {l} drawn with the theme's stroke (--hf-icon-stroke).
import { ICONS } from "../gen/icons.js";
export { ICONS };
const paths = (e) =>
  !e ? "" : typeof e === "string" ? `<path d="${e}"/>` : `${e.f ? `<path d="${e.f}"/>` : ""}${e.l ? `<path class="is-l" d="${e.l}"/>` : ""}`;
export const svg = (n, c = "") => `<svg class="hf5-g${c ? " " + c : ""}" viewBox="0 0 24 24" aria-hidden="true">${paths(ICONS[n])}</svg>`;
export const glyph = (ic) => (!ic ? "" : String(ic).trim().startsWith("<") ? ic : svg(ic));
