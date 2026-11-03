// The remote as a custom element (shadow DOM): renders a descriptor, wires the shared behaviours, sends through the commander.
// openRemote(hass, entityId) is the ONE entry (the old cards' «hass-more-info» is routed here for the types v5 draws).
import { TYPES } from "../descriptors/index.js";
import { remoteHtml } from "./render.js";
import { TOKENS_CSS } from "../gen/tokens.js";
import { KIT_CSS } from "../kit/css.js";
import { REMOTE_CSS } from "./css.js";
import { Commander, trouble, troubleStates } from "../core/command.js";
import { bindSlider, bindHold } from "../behaviours/gestures.js";
import { notify, go } from "../core/dom.js";
import { applyTheme } from "../core/theme.js";
import { setLang, langOf, t } from "../core/i18n.js";
import { CANON } from "../gen/canon.js";
import { ageText } from "../core/format.js";
import { houseApi } from "../adapters/common.js";
import { climateSeries } from "../adapters/climate.js";
import { clock, num } from "../core/i18n.js";

const TAG = "hauflo-remote-v5";
const STORE = "hauflo5.offtimers";
const store = {
  get: () => { try { return JSON.parse(localStorage.getItem(STORE) || "{}"); } catch (e) { return {}; } },
  set: (o) => { try { localStorage.setItem(STORE, JSON.stringify(o)); } catch (e) {} },
};

class Remote extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this.opt = {};
    this.period = "day";
    this.fav = false;
    this.cmd = new Commander(() => this._hass, () => this.render());
  }
  open(hass, id, o = {}) {
    this.id = id;
    this.o = o;
    this.domain = id.split(".")[0];
    this.type = TYPES[this.domain];
    this._hass = hass;
    setLang(langOf(hass));
    this.prevFocus = document.activeElement;
    this.keydown = (e) => e.key === "Escape" && this.close();
    window.addEventListener("keydown", this.keydown, true);
    this.tick = setInterval(() => this.render(), CANON.remote.updated_tick_ms);
    this.loadFav();
    this.loadHistory();
    this.render();
    queueMicrotask(() => this.shadowRoot.querySelector(".hf5-sheet")?.focus());
  }
  set hass(h) {
    this._hass = h;
    if (!this.type || this.dragging) return;
    // an answer arrived: the optimistic view yields to the device's state
    const st = h.states[this.id];
    if (st && st !== this.lastState) this.opt = {};
    this.render();
  }
  close() {
    window.removeEventListener("keydown", this.keydown, true);
    clearInterval(this.tick);
    this.cmd.dispose();
    this.remove();
    this.prevFocus?.focus?.();
  }
  async loadFav() {
    try {
      const r = await houseApi.get(this._hass);
      this.fav = (r?.favorites?.items || []).includes(this.id);
      this.render();
    } catch (e) {}
  }
  async loadHistory() {
    if (this.type?.history) {
      try {
        const r = await this.type.history(this._hass, this.id);
        this.series = r.series;
        this.axis = r.axis;
      } catch (e) {
        this.series = [];
      }
      return this.render();
    }
    if (this.domain !== "climate") return;
    const hours = CANON.climate.history_periods_h[this.period];
    const from = new Date(Date.now() - hours * 3600e3).toISOString();
    try {
      const r = await this._hass.callApi("GET", `history/period/${from}?filter_entity_id=${this.id}&significant_changes_only=0`);
      const s = climateSeries(r?.[0] || [], hours);
      this.series = s.series;
      this.axis = s.lo == null ? null : [`${num(s.lo)}°`, `${num(s.hi)}°`];
    } catch (e) {
      this.series = [];
    }
    this.render();
  }
  ctx() {
    const timers = store.get();
    const tm = timers[this.id];
    const live = tm && tm.until > Date.now() ? { min: tm.min, at: clock(new Date(tm.until)) } : null;
    return { opt: this.opt, fav: this.fav, timer: live, period: this.period, series: this.series, axis: this.axis, lastMode: this.lastMode, hass: this._hass, st: this._hass?.states?.[this.id], extras: this.type?.extras?.(this._hass, this.id) };
  }
  render() {
    if (!this.type) return;
    const st = this._hass?.states?.[this.id];
    if (!st) return;
    this.lastState = st;
    const m = this.type.model(st);
    if (m.mode && m.mode !== "off") this.lastMode = m.mode;
    const ctx = this.ctx();
    const tr = trouble(m, this.cmd.of(this.id));
    const html = remoteHtml(this.type.remote, m, this.type.controls(m, ctx), {
      trouble: tr,
      badge: troubleStates(tr),
      foot: [ageText(m.updated), m.source, m.id].filter(Boolean).join(" · "),
      go: this.o.go ? t("room.open") : "",
    });
    const root = this.shadowRoot;
    const scroll = root.querySelector(".hf5-body")?.scrollTop || 0;
    const wide = innerWidth >= CANON.remote.wide_from_px;
    const sheet = innerWidth < CANON.remote.sheet_below_px;
    root.innerHTML = `<style>${TOKENS_CSS}${KIT_CSS}${REMOTE_CSS}</style><div class="hf-root ${sheet ? "is-sheet" : wide && html.includes("is-2col") ? "is-wide" : "is-dialog"}">${html}</div>`;
    applyTheme(this, this._hass, { mode: this.o.mode });
    const body = root.querySelector(".hf5-body");
    if (body) body.scrollTop = scroll;
    this.bind(m);
  }
  bind(m) {
    const root = this.shadowRoot;
    root.querySelectorAll("[data-close]").forEach((e) => e.addEventListener("click", () => this.close()));
    root.querySelector("[data-more-ha]")?.addEventListener("click", () => {
      const id = this.id;
      this.close();
      window.dispatchEvent(new CustomEvent("hass-more-info", { detail: { entityId: id, hf5: true }, bubbles: true, composed: true }));
    });
    // opened from a list row that belongs to a room (the house's climate list): its page
    root.querySelector("[data-go]")?.addEventListener("click", () => {
      const path = this.o.go;
      this.close();
      go(path);
    });
    root.querySelectorAll("button[data-act]").forEach((b) => b.addEventListener("click", () => this.act(m, b.dataset.act, b.dataset.arg)));
    // class A (gates, garage, doors): 1 s hold runs it; a short press says how (covers spec v5 K1)
    root.querySelectorAll("[data-hold]").forEach((b) =>
      bindHold(b, { onDone: () => this.act(m, b.dataset.hold, b.dataset.arg), onEarly: () => notify(document.querySelector("home-assistant") || this, t("common.hold_hint", { action: b.textContent.trim().toLowerCase() })) }),
    );
    root.querySelectorAll("[data-slide]").forEach((s) => {
      const name = s.dataset.slide;
      s.addEventListener("hf5-dragstart", () => (this.dragging = true));
      s.addEventListener("hf5-dragend", () => (this.dragging = false));
      bindSlider(s, { commit: (v) => (this.dragging = false, this.act(m, name, v)) });
    });
  }
  async act(m, name, arg) {
    if (name === "retry") return this.cmd.retry(this.id);
    const type = this.type;
    const ctx = { ...this.ctx(), opt: this.opt };
    const r = type.act(m, name, arg, ctx);
    if (r.period) {
      this.period = r.period;
      this.series = [];
      this.loadHistory();
      return;
    }
    if (r.fav) return this.toggleFav();
    if (r.opt) this.opt = { ...this.opt, ...r.opt };
    if (r.timer !== undefined) this.setTimer(r.timer);
    this.render();
    if (!r.calls?.length) return;
    if (r.debounce) {
      this.cmd.debounced(this.id, r.calls[0]);
      this.cmd.cmd[this.id] = { sending: true };
    } else await this.cmd.runAll(this.id, r.calls);
  }
  setTimer(min) {
    const all = store.get();
    if (min > 0) all[this.id] = { min, until: Date.now() + min * 60e3 };
    else delete all[this.id];
    store.set(all);
  }
  async toggleFav() {
    const cur = (await houseApi.get(this._hass).catch(() => null))?.favorites || { items: [] };
    const items = cur.items.includes(this.id) ? cur.items.filter((x) => x !== this.id) : [...cur.items, this.id];
    const r = await this.cmd.run(this.id, { api: { method: "POST", path: "hauflo/house", body: { favorites: { ...cur, items } } } });
    if (r) this.fav = items.includes(this.id);
    this.render();
  }
}

if (!customElements.get(TAG)) customElements.define(TAG, Remote);

export function openRemote(hass, id, o = {}) {
  document.querySelectorAll(TAG).forEach((e) => e.close());
  const el = document.createElement(TAG);
  document.body.appendChild(el);
  el.open(hass, id, o);
  return el;
}
export const isDrawn = (id) => !!TYPES[String(id).split(".")[0]];
