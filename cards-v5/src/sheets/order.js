// The order sheet: the client puts a block's items in their order, for the whole house (agent /api/hauflo/order).
// Opened by a long press / right click on the block's title, or from the old «Настройки → Порядок элементов» list
// (window.__haufloOrderV5.blocks()). Drag by the grip (finger or mouse) or ↑ ↓; saved 700 ms after the last move;
// closing (×, «Готово», backdrop, Esc) sends what waits and stays open with the error if it failed — never a silent loss.
import { t } from "../core/i18n.js";
import { esc, notify } from "../core/dom.js";
import { cap } from "../kit/primitives.js";
import { svg } from "../core/icons.js";
import { clientName } from "../core/names.js";
import { houseOk, houseSet, orderKey, blockTitle, houseData, canEdit } from "../core/house.js";
import { Sheet, openSheet } from "./base.js";

const TAG = "hauflo-order-v5";
const SAVE_MS = 700;
const FIELDS = [
  ["groups", "order.groups"],
  ["entities", ""],
  ["zones", ""],
  ["sensors", "order.sensors"],
  ["alarms", "order.alarms"],
  ["scenes", "order.scenes"],
];
const idOf = (x) => (typeof x === "string" ? x : typeof x?.entity === "string" ? x.entity : undefined);
const CARDS = new Set();

/** What the sheet lists: a section per list of the card's config, in the house's order; items of HA only. */
export function orderSections(card) {
  if (card.def?.sections) return card.def.sections(card, () => baseSections(card));
  return baseSections(card);
}
/** A section per list of the card's config (key = the config field). */
function baseSections(card) {
  const c = card.config;
  const h = card.hass;
  const item = (x) => {
    const id = idOf(x);
    const st = id && h?.states?.[id];
    return st ? { id, name: (typeof x === "object" && x.name) || clientName(st.attributes?.friendly_name, id) } : null;
  };
  const out = [];
  for (const [f, label] of FIELDS) if (Array.isArray(c[f])) out.push({ key: f, label: label && t(label), items: c[f].map(item).filter(Boolean) });
  if (Array.isArray(c.rows)) for (const r of c.rows) if (Array.isArray(r?.scenes)) out.push({ label: r.title || t("order.scenes"), items: r.scenes.map(item).filter(Boolean) });
  return out.filter((s) => s.items.length);
}
const orderable = (card) => !!orderKey(card) && houseOk("order") && orderSections(card).some((s) => s.items.length > 1);
/** The block's name in the sheet's title and the settings list. */
const blockName = (card) => blockTitle(card, card.config.label || t(`cards.${card.def?.key}.name`));

/** Cards register themselves when drawn: the long press on the title and the settings list find them here. */
export function orderTitle(card, el) {
  CARDS.add(card);
  if (!el || !orderable(card)) return false;
  el.classList.add("is-orderable");
  return true;
}
/** This page's blocks that can be ordered (a tablet copy is the same block), top to bottom — for the old settings sheet. */
export function orderBlocks() {
  const by = new Map();
  for (const card of CARDS) {
    if (!card.isConnected || !orderable(card)) continue;
    const key = orderKey(card);
    const r = card.getBoundingClientRect();
    const shown = r.height > 0;
    const have = by.get(key);
    if (have && (have.shown || !shown)) continue;
    const n = new Set(orderSections(card).flatMap((s) => s.items.map((x) => x.id))).size;
    by.set(key, { name: blockName(card), n, shown, top: r.top, open: () => openOrder(card) });
  }
  return [...by.values()].sort((a, b) => Number(b.shown) - Number(a.shown) || a.top - b.top);
}
if (typeof window !== "undefined") window.__haufloOrderV5 = { blocks: orderBlocks };

export async function openOrder(card) {
  const key = orderKey(card);
  if (openSheet() || !key) return;
  if (!(await canEdit(card.hass))) return notify(card, t("order.child"));
  const el = document.createElement(TAG);
  if (el.begin(card)) el.open(key);
}

const SHEET_CSS = `
.hf5-ord{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:var(--hf-sp-1)}
.hf5-ord li{display:flex;align-items:center;gap:var(--hf-sp-2);min-height:var(--hf-hit);padding:0 var(--hf-sp-1) 0 0;border-radius:var(--hf-r-ctl);background:var(--hf-surface-2);touch-action:pan-y}
.hf5-ord li.is-drag{box-shadow:var(--hf-elev-2);position:relative;z-index:1}
.hf5-ord .hf5-grip{display:grid;place-items:center;width:var(--hf-hit);height:var(--hf-hit);flex:none;color:var(--hf-ink-2);cursor:grab;touch-action:none}
.hf5-ord .hf5-grip .hf5-g{width:var(--hf-icon-m);height:var(--hf-icon-m)}
.hf5-ord .hf5-nm{flex:1 1 auto;min-width:0;overflow-wrap:anywhere}
.hf5-ord .hf5-x[disabled]{opacity:var(--hf-disabled-opacity,.38);cursor:default}
[data-lists]{display:flex;flex-direction:column;gap:var(--hf-sp-2)}
[data-lists] .hf5-ord+*{margin-top:var(--hf-sp-3)}
`;

class OrderSheet extends Sheet {
  open(key) {
    this.key = key;
    this.secs = orderSections(this.card).map((s) => ({ ...s, items: [...s.items] }));
    this.dirty = false;
    this.inflight = null;
    this.draw();
  }
  dismiss() {
    this.done();
  }
  ids() {
    return [...new Set(this.secs.flatMap((s) => s.items.map((x) => x.id)))];
  }
  save(items) {
    clearTimeout(this.saveT);
    this.dirty = false;
    const run = (this.inflight || Promise.resolve()).then(async () => {
      try {
        await houseSet(this.card.hass, "order", items, this.key);
        if (this.live) this.note(t("order.saved"));
        return true;
      } catch (e) {
        this.dirty = true;
        if (this.live) this.note(t("order.error"));
        return false;
      }
    });
    this.inflight = run.finally(() => this.inflight === run && (this.inflight = null));
    return run;
  }
  changed() {
    this.dirty = true;
    clearTimeout(this.saveT);
    this.saveT = setTimeout(() => this.save(this.ids()), SAVE_MS);
  }
  async done() {
    const ok = this.dirty ? await this.save(this.ids()) : this.inflight ? await this.inflight : true;
    if (ok !== false && this.live) this.close();
  }
  close() {
    clearTimeout(this.saveT);
    super.close();
  }
  row(x, i, n) {
    const b = (dir, on) =>
      `<button type="button" class="hf5-x" data-${dir}="${esc(x.id)}" aria-label="${esc(`${t(`order.${dir}`)}: ${x.name}`)}"${on ? "" : " disabled"}>${svg(dir)}</button>`;
    return `<li data-id="${esc(x.id)}"><span class="hf5-grip" data-grip aria-hidden="true">${svg("drag")}</span><span class="hf5-nm">${esc(x.name)}</span>${b("up", i > 0)}${b("down", i < n - 1)}</li>`;
  }
  lists(focus) {
    const body = this.shadowRoot.querySelector(".hf5-body");
    const top = body?.scrollTop || 0;
    this.shadowRoot.querySelector("[data-lists]").innerHTML = this.secs
      .map((s, si) => `${s.label ? cap(s.label) : ""}<ol class="hf5-ord" data-s="${si}">${s.items.map((x, i) => this.row(x, i, s.items.length)).join("")}</ol>`)
      .join("");
    if (body) body.scrollTop = top;
    this.bindLists();
    if (focus) {
      const li = this.shadowRoot.querySelector(`li[data-id="${CSS.escape(focus.id)}"]`);
      li?.querySelector(`[data-${focus.which}]`)?.focus({ preventScroll: true });
      li?.scrollIntoView({ block: "nearest" });
    }
  }
  bindLists() {
    const root = this.shadowRoot;
    root.querySelectorAll("ol").forEach((ol) => {
      const s = this.secs[Number(ol.dataset.s)];
      ol.querySelectorAll("li").forEach((li, i) => {
        const move = (by, which) => {
          const j = i + by;
          if (j < 0 || j >= s.items.length) return;
          [s.items[i], s.items[j]] = [s.items[j], s.items[i]];
          this.lists({ id: s.items[j].id, which: j === 0 ? "down" : j === s.items.length - 1 ? "up" : which });
          this.changed();
        };
        li.querySelector("[data-up]").onclick = () => move(-1, "up");
        li.querySelector("[data-down]").onclick = () => move(1, "down");
        li.querySelector("[data-grip]").addEventListener("pointerdown", (e) => this.drag(e, ol, li, s));
      });
    });
  }
  drag(e, ol, li, s) {
    if (e.pointerType === "mouse" && e.button !== 0) return;
    e.preventDefault();
    li.classList.add("is-drag");
    const body = this.shadowRoot.querySelector(".hf5-body");
    const before0 = [...ol.children].map((o) => o.dataset.id).join("\n");
    const onMove = (ev) => {
      ev.preventDefault();
      const y = ev.clientY;
      let before = null;
      for (const o of ol.children) {
        if (o === li) continue;
        const r = o.getBoundingClientRect();
        if (y < r.top + r.height / 2) {
          before = o;
          break;
        }
      }
      if (before !== li.nextElementSibling && before !== li) ol.insertBefore(li, before);
      const b = body.getBoundingClientRect(); // near the edge the list scrolls (a long list on a phone)
      if (y < b.top + 48) body.scrollTop -= 12;
      else if (y > b.bottom - 48) body.scrollTop += 12;
    };
    const onUp = () => {
      document.removeEventListener("pointermove", onMove);
      document.removeEventListener("pointerup", onUp);
      document.removeEventListener("pointercancel", onUp);
      const byId = new Map(s.items.map((x) => [x.id, x]));
      s.items = [...ol.children].map((o) => byId.get(o.dataset.id)).filter(Boolean);
      this.lists();
      if (s.items.map((x) => x.id).join("\n") !== before0) this.changed();
    };
    document.addEventListener("pointermove", onMove, { passive: false });
    document.addEventListener("pointerup", onUp);
    document.addEventListener("pointercancel", onUp);
  }
  draw() {
    const kept = !!houseData("order")[this.key]?.length;
    const root = this.frame({
      over: t("order.title"),
      title: blockName(this.card),
      body: `<p class="hf5-hint">${esc(t("order.hint"))}</p><div data-lists></div>`,
      foot: `<button type="button" class="hf5-link" data-reset${kept ? "" : " hidden"}>${esc(t("order.reset"))}</button><span class="hf5-sp"></span><button type="button" class="hf5-pill is-on" data-done>${esc(t("order.done"))}</button>`,
      css: SHEET_CSS,
    });
    this.lists();
    root.querySelector("[data-done]").onclick = () => this.done();
    root.querySelector("[data-reset]").onclick = async () => {
      clearTimeout(this.saveT);
      this.dirty = false;
      if (!(await this.save([]))) return;
      this.card._ocfg = null;
      this.secs = orderSections(this.card).map((s) => ({ ...s, items: [...s.items] }));
      if (this.live) (this.lists(), (root.querySelector("[data-reset]").hidden = true));
    };
  }
}
if (typeof customElements !== "undefined" && !customElements.get(TAG)) customElements.define(TAG, OrderSheet);
