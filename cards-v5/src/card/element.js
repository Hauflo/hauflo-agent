// Layer 4 · the ONE card element every v5 card type extends. A type is data + two functions (a "def"):
//   watch(cfg) → entity ids the card reads · render(card, variant) → markup from primitives · act(card, name, arg, el) →
//   {key, calls, opt, after(ok)} | null · grid(cfg) → HA sections grid options · stub(hass) → picker config ·
//   hold(card, el) → true when the def opened its own sheet (else the remote).
// The element owns: config, hass diffing, size variant by its own box (size.js), theme, gestures, commands, remotes.
import { TOKENS_CSS } from "../gen/tokens.js";
import { CANON } from "../gen/canon.js";
import { KIT_CSS } from "../kit/css.js";
import { fit } from "../kit/fit.js";
import { applyTheme } from "../core/theme.js";
import { setLang, langOf } from "../core/i18n.js";
import { clamp, vars } from "../core/dom.js";
import { Commander } from "../core/command.js";
import { bindPress, bindSlider, bindHold } from "../behaviours/gestures.js";
import { variantOf } from "./size.js";
import { setHouse } from "../core/names.js";
import { ordered } from "../core/house.js";
import { orderTitle, openOrder } from "../sheets/order.js";

const CSS_PX_PER_REM = 16; // the browser's default root size: the system text scale k = actual root size / this
const stop = (ev) => ev.stopPropagation();

export class HfCard extends HTMLElement {
  static def = null;
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this.cmd = new Commander(() => this._hass, () => this.render());
    this.opt = {}; // optimistic view per command key until HA reports the new state
    this._w = 0;
  }
  get def() {
    return this.constructor.def;
  }
  setConfig(c) {
    if (!c || typeof c !== "object") throw new Error("hauflo: config required");
    this._cfg = { ...c };
    this._ocfg = null;
    this._html = null;
    this.render();
  }
  /** The config as the card draws it: the block's lists in the house's order (core/house.js). */
  get config() {
    return this._cfg ? ordered(this) : {};
  }
  set hass(h) {
    const old = this._hass;
    this._hass = h;
    setHouse(h);
    if (!old || old.language !== h.language || old.themes !== h.themes) return this.render();
    let changed = false;
    for (const id of this.def.watch(this.config, this)) {
      if (old.states[id] === h.states[id]) continue;
      changed = true;
      if (!this.cmd.busy(id)) delete this.opt[id]; // a reading that changes mid-±  must not throw the next tap back
    }
    if (changed) this.render();
  }
  get hass() {
    return this._hass;
  }
  /** System text scale (phone «large text»), clamped to the canon range (K2 ≤ 1.3). */
  get k() {
    const css = getComputedStyle(document.documentElement);
    const ha = parseFloat(css.getPropertyValue(CANON.textScale.haVar)); // in HA: its own scale (its root is 14 px)
    const px = parseFloat(css.fontSize) || CSS_PX_PER_REM;
    return clamp(ha > 0 ? ha : px / CSS_PX_PER_REM, CANON.textScale.min, CANON.textScale.max);
  }
  connectedCallback() {
    this._ro ??= new ResizeObserver(([e]) => {
      const w = Math.round(e.contentRect.width);
      if (w === this._w) return this.snap();
      this._w = w;
      this.render();
    });
    this._ro.observe(this);
    this.render();
  }
  disconnectedCallback() {
    this._ro?.disconnect();
    this._ro = null;
    for (const p of Object.values(this._subs || {})) p.then((u) => u?.()).catch(() => {});
    this._subs = null;
    for (const i of Object.values(this._tk || {})) clearInterval(i);
    this._tk = null;
  }
  /** Re-draw the card every ms while it is on screen (camera snapshot, history refresh); one timer per key. */
  tick(key, ms) {
    if (!this.isConnected) return;
    this._tk ??= {};
    this._tk[key] ??= setInterval(() => ((this._html = null), this.render()), ms);
  }
  /** A websocket subscription kept while the card is on screen (weather forecast…): the latest event is cached under key
   *  and the card re-renders on each. Returns what is cached now (undefined until the first event). */
  sub(key, msg) {
    this._subs ??= {};
    if (!this._subs[key] && this._hass?.connection?.subscribeMessage)
      this._subs[key] = this._hass.connection
        .subscribeMessage((e) => {
          (this._data ??= {})[key] = { at: Date.now(), value: e };
          this._html = null;
          this.render();
        }, msg)
        .catch(() => undefined);
    return this._data?.[key]?.value;
  }
  getGridOptions() {
    return this.def.grid?.(this.config, this) ?? { columns: CANON.sizes.grid.columns, rows: "auto", min_columns: 3 };
  }
  getCardSize() {
    const g = CANON.sizes.grid;
    return Math.max(1, Math.round((this.offsetHeight || g.row_px * 2) / (g.row_px + g.gap_px)));
  }
  render() {
    if (!this._cfg || !this._hass || !this.isConnected || this._drag) return;
    setLang(this.config.lang || langOf(this._hass));
    applyTheme(this, this._hass, { at: this });
    const k = this.k;
    const v = variantOf(this._w || this.clientWidth || 0, 0, { k });
    let body;
    try {
      body = this.def.render(this, v);
    } catch (e) {
      console.error("hauflo-cards-v5:", this.localName, e);
      body = `<div class="hf5-msg">${String(e?.message || e)}</div>`;
    }
    const frame = this.def.frame?.(this) || "";
    const html = `<div class="hf5-card is-${v.toLowerCase()}${frame ? " " + frame : ""}"${vars({ "--hf5-k": k })}>${body}</div>`;
    if (html === this._html) return;
    this._html = html;
    if (!this._style) {
      this._style = document.createElement("style");
      this._style.textContent = TOKENS_CSS + KIT_CSS + (this.def.css || "");
      this._root = document.createElement("div");
      this._root.style.display = "contents";
      this.shadowRoot.append(this._style, this._root);
      this.shadowRoot.addEventListener("hf5-dragstart", () => (this._drag = true));
      this.shadowRoot.addEventListener("hf5-dragend", () => ((this._drag = false), (this._html = null), queueMicrotask(() => this.render())));
    }
    this._root.innerHTML = html;
    this.bind(this._root);
    fit(this._root);
    this.snap();
  }
  /** Neighbours never jump: in a multi-column sections view the card's height is rounded up to HA's row step
   *  (row_px + the grid's real gap), so the cards under it start on the same line as the next column's (HA's own
   *  sections lattice). One column (phone) or no HA grid: height by content. Off while canon sizes.grid.snap_below_view
   *  is 0 (v1 look, owner 03.11: the slack was an empty bottom of up to a row step inside every block). */
  snap() {
    const card = this._root?.firstElementChild;
    if (!card) return;
    card.style.minHeight = "";
    if (!CANON.sizes.grid.snap_below_view) return;
    let el = this;
    let grid = null;
    for (let i = 0; i < 6 && el && !grid; i++) {
      el = el.parentElement || el.getRootNode?.().host;
      if (el && getComputedStyle(el).display === "grid") grid = el;
    }
    if (!grid || grid.clientWidth > document.documentElement.clientWidth * CANON.sizes.grid.snap_below_view) return;
    const gap = parseFloat(getComputedStyle(grid).rowGap) || CANON.sizes.grid.gap_px;
    const step = CANON.sizes.grid.row_px + gap;
    const h = card.offsetHeight;
    const want = Math.ceil((h + gap) / step) * step - gap;
    if (want > h) card.style.minHeight = `${want}px`;
  }
  /** data-* hooks → gestures (the only pointer code is behaviours/gestures.js). */
  bind(root) {
    // the block's title: a long press / right click opens the order sheet (when the block has something to order)
    const cap = root.querySelector(".hf5-ch .hf5-cap");
    if (orderTitle(this, cap)) {
      ["pointerdown", "pointerup", "click"].forEach((e) => cap.addEventListener(e, stop));
      bindPress(cap, { hold: () => openOrder(this) });
    }
    for (const el of root.querySelectorAll("[data-tap]")) {
      bindPress(el, { tap: () => this.act(el.dataset.tap, el.dataset.arg, el), hold: el.dataset.id ? () => this.def.hold?.(this, el) || this.more(el.dataset.id) : null });
    }
    for (const el of root.querySelectorAll("[data-slide]")) {
      bindSlider(el, { live: (v) => this.def.live?.(this, el, v), commit: (v) => this.act(el.dataset.slide, v, el), hold: () => this.more(el.dataset.id) });
    }
    for (const el of root.querySelectorAll("[data-hold]")) {
      ["pointerdown", "pointerup"].forEach((e) => el.addEventListener(e, stop));
      bindHold(el, { onDone: () => this.act(el.dataset.hold, el.dataset.arg, el), onEarly: () => this.def.early?.(this, el) });
    }
    for (const el of root.querySelectorAll("button[data-act]")) {
      ["pointerdown", "pointerup"].forEach((e) => el.addEventListener(e, stop));
      el.addEventListener("click", (ev) => (ev.stopPropagation(), this.act(el.dataset.act, el.dataset.arg, el)));
    }
    for (const el of root.querySelectorAll("[data-more]")) {
      ["pointerdown", "pointerup"].forEach((e) => el.addEventListener(e, stop));
      el.addEventListener("click", (ev) => (ev.stopPropagation(), this.more(el.dataset.more || el.closest("[data-id]")?.dataset.id)));
    }
  }
  /** A gesture → the def decides the HA calls; the Commander sends them and tracks sending / failed. */
  act(name, arg, el) {
    const r = this.def.act(this, name, arg, el);
    if (!r) return;
    if (r.more) return this.more(r.more, r.go);
    if (r.retry) return this.cmd.retry(r.retry);
    if (r.opt) for (const [id, o] of Object.entries(r.opt)) this.opt[id] = { ...(this.opt[id] || {}), ...o };
    if (r.debounce && r.calls?.length) return this.cmd.debounced(r.key, r.calls[0]), this.render();
    if (r.calls?.length) this.cmd.runAll(r.key, r.calls).then((ok) => r.after?.(ok));
    else this.render();
  }
  /** Async data a card needs besides states (history…): cached per key for ttl ms; the card re-renders when it arrives.
   *  Returns what is cached now (undefined until the first answer). */
  load(key, ttl, fn) {
    this._data ??= {};
    const d = this._data[key];
    if (d && (d.pending || Date.now() - d.at < ttl)) return d.value;
    this._data[key] = { pending: true, at: Date.now(), value: d?.value };
    Promise.resolve()
      .then(fn)
      .catch(() => d?.value ?? null)
      .then((value) => {
        this._data[key] = { at: Date.now(), value };
        this._html = null;
        this.render();
      });
    return d?.value;
  }
  /** The big remote: v5 draws light / climate (index.js routes the event); everything else → HA's own dialog. */
  more(id, go) {
    if (!id) return;
    this.dispatchEvent(new CustomEvent("hass-more-info", { detail: { entityId: id, ...(go ? { hf5go: go } : {}) }, bubbles: true, composed: true }));
  }
}
