// The ONE frame of every small sheet a card opens (order, scene): a sheet from the bottom on a phone, a dialog on a wider
// screen; the card's theme and mode; closed by ×, Esc or the backdrop; one sheet at a time; focus back where it was.
import { TOKENS_CSS } from "../gen/tokens.js";
import { KIT_CSS } from "../kit/css.js";
import { CANON } from "../gen/canon.js";
import { applyTheme } from "../core/theme.js";
import { t } from "../core/i18n.js";
import { esc } from "../core/dom.js";
import { cap, name as nameEl, toast } from "../kit/primitives.js";
import { svg } from "../core/icons.js";

const NOTE_MS = 2500;
const AFTER_HOLD_MS = 700; // the click that ends the long press which opened a sheet comes this late at most
let OPEN = null;
/** The sheet open now (one at a time). */
export const openSheet = () => OPEN;

// above the previous cards' settings sheet (z 9999) a sheet may open from — the same layer as their order sheet
const BASE_CSS = `:host{position:fixed;inset:0;z-index:10000;display:block}
.hf5-hint{margin:0;color:var(--hf-ink-2);font-size:var(--hf-fs-s)}
.hf5-ft{display:flex;align-items:center;gap:var(--hf-sp-3);padding:var(--hf-sp-3) var(--hf-sp-5) var(--hf-sp-4);border-top:var(--hf-line-w) solid var(--hf-hair)}
.hf5-ft .hf5-sp{flex:1}`;

export class Sheet extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
  }
  /** Put the sheet on the page for a card. → false when another sheet is open. */
  begin(card) {
    if (OPEN) return false;
    OPEN = this;
    this.card = card;
    this.prevFocus = document.activeElement;
    this.onKey = (e) => e.key === "Escape" && this.dismiss();
    window.addEventListener("keydown", this.onKey, true);
    this.opened = Date.now();
    document.body.append(this);
    return true;
  }
  get live() {
    return OPEN === this;
  }
  /** Draw the frame: over (caps) / title / ×, the body, an optional footer; css = the sheet's own rules. */
  frame({ over, title, body, foot = "", css = "" }) {
    const sheet = innerWidth < CANON.remote.sheet_below_px;
    this.shadowRoot.innerHTML = `<style>${TOKENS_CSS}${KIT_CSS}${BASE_CSS}${css}</style><div class="hf-root ${sheet ? "is-sheet" : "is-dialog"}"><div class="hf5-scrim" data-scrim></div>
<div class="hf5-sheet" role="dialog" aria-modal="true" aria-label="${esc(over ? `${over}: ${title}` : title)}" tabindex="-1">
  <div class="hf5-grab"></div>
  <div class="hf5-head"><div class="hf5-stack">${over ? cap(over) : ""}${nameEl(title, { split: false })}</div><button type="button" class="hf5-x" data-x aria-label="${esc(t("common.close"))}">${svg("close")}</button></div>
  <div class="hf5-body">${body}</div>${foot ? `<div class="hf5-ft">${foot}</div>` : ""}
</div><div data-note></div></div>`;
    applyTheme(this, this.card.hass, { at: this.card }); // the card's mode (a dark dashboard → a dark sheet)
    const root = this.shadowRoot;
    root.querySelector("[data-x]").onclick = () => this.dismiss();
    // the click that ends the long press which opened the sheet lands on the backdrop: not a close
    root.querySelector("[data-scrim]").onclick = () => Date.now() - this.opened > AFTER_HOLD_MS && this.dismiss();
    if (!root.activeElement) queueMicrotask(() => (root.querySelector("[autofocus]") || root.querySelector(".hf5-sheet"))?.focus());
    return root;
  }
  /** × / Esc / backdrop: a sheet may finish something first (the order sheet saves). */
  dismiss() {
    this.close();
  }
  close() {
    clearTimeout(this.noteT);
    window.removeEventListener("keydown", this.onKey, true);
    if (OPEN === this) OPEN = null;
    this.remove();
    this.prevFocus?.focus?.();
  }
  /** A short note at the bottom of the sheet. */
  note(text) {
    const n = this.shadowRoot.querySelector("[data-note]");
    if (!n) return;
    n.innerHTML = toast(text);
    clearTimeout(this.noteT);
    this.noteT = setTimeout(() => (n.innerHTML = ""), NOTE_MS);
  }
}
