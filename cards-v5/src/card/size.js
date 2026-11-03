// Card size by ITS box (canon sizes.json): the first variant whose `when` matches wins. A card whose height the grid
// fixes (rows: n) can be XS (one row); a card with height by content is judged by width only. k = system text scale:
// thresholds act as if the box were k times smaller (XS by the real height — a row stays a row).
import { CANON } from "../gen/canon.js";

const hit = (w, h, c = {}) => {
  if (c.any) return c.any.some((x) => hit(w, h, x));
  if (c.all) return c.all.every((x) => hit(w, h, x));
  if (c.w_lt != null && !(w < c.w_lt)) return false;
  if (c.h_lt != null && !(h < c.h_lt)) return false;
  if (c.h_ge != null && !(h >= c.h_ge)) return false;
  return true;
};
/** → "XS" | "S" | "M" | "L" */
export function variantOf(w, h, { fixed = false, k = 1 } = {}) {
  const list = fixed ? CANON.sizes.variants : CANON.sizes.variants_unfixed;
  for (const v of list) {
    const c = v.when || {};
    if (c.fixed && !fixed) continue;
    const ok = v.id === "XS" ? hit(w / k, h, { ...c, fixed: undefined }) : hit(w / k, h / k, c);
    if (ok) return v.id;
  }
  return "L";
}
