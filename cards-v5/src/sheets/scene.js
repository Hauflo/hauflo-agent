// The scene sheet (lighting spec §5, Д19): «+ Сцена» — a name, then what the room's lights / blinds do now;
// a hold on a scene — the family's: rename / remember again / delete; the integrator's: the same, «remove» only from this
// panel; a learnable KNX one: remember again (scene learn on the bus) / its name on this panel. Every scene: favourites
// and «Подробнее в HA». A child profile sees the scene, never the changes. Overwriting always asks first.
import { t } from "../core/i18n.js";
import { esc } from "../core/dom.js";
import { pill } from "../kit/primitives.js";
import { canEdit, houseInfo, favHas, favToggle } from "../core/house.js";
import { famCall, famReload, famName, sceneLabel, captureList, SCENE_ROLE } from "../core/scenes.js";
import { Sheet, openSheet } from "./base.js";

const TAG = "hauflo-scene-v5";
const NAME_MAX = 40;

/** Open the sheet: id null → a new scene of src's list; edit = "fam" | "ha" | "knx" | "" (view only). */
export async function openScene(card, id, src, edit = "") {
  if (openSheet()) return;
  const h = card.hass;
  const [may, info] = await Promise.all([canEdit(h), houseInfo(h)]);
  if (!id && !may) return;
  const el = document.createElement(TAG);
  if (el.begin(card)) el.open(id, src, edit, may, !!info);
}

const SHEET_CSS = `
.hf5-acts{display:flex;flex-direction:column;gap:var(--hf-sp-2);margin-top:var(--hf-sp-2)}
.hf5-acts>.hf5-pill{width:100%;justify-content:center}
.hf5-acts>.hf5-pill.is-bad{color:var(--hf-state-warn-text)}
.hf5-acts>.hf5-link{align-self:center}
.hf5-in{box-sizing:border-box;width:100%;min-height:var(--hf-hit);padding:0 var(--hf-sp-3);border:0;border-radius:var(--hf-r-ctl);background:var(--hf-surface-2);color:var(--hf-ink-1);font:inherit;font-size:var(--hf-fs-m);outline:0;box-shadow:inset 0 0 0 var(--hf-line-w) var(--hf-hair)}
.hf5-in:focus-visible{box-shadow:inset 0 0 0 var(--hf-focus-w) var(--hf-focus)}
.hf5-in::placeholder{color:var(--hf-ink-3)}
.hf5-err{margin:0;color:var(--hf-state-warn-text);font-size:var(--hf-fs-s)}
.hf5-sc{display:flex;flex-direction:column;gap:var(--hf-sp-3)}`;

class SceneSheet extends Sheet {
  open(id, src, edit, may, info) {
    Object.assign(this, { id, src, edit, may, info, kind: src?.kind || "light" });
    if (id) this.menu();
    else this.add();
  }
  get h() {
    return this.card.hass;
  }
  label() {
    return sceneLabel(this.h, { entity: this.id, name: (this.src?.scenes || []).find((x) => x.entity === this.id)?.name });
  }
  /** One screen: title, lines, an optional field, the actions; returns the shadow root. */
  screen({ title, over = "", hints = [], input = null, err = "", acts = [] }) {
    const field = input
      ? `<input class="hf5-in" data-in maxlength="${NAME_MAX}" autofocus aria-label="${esc(t("scenes.name"))}" placeholder="${esc(input.ph || "")}" value="${esc(input.value || "")}">`
      : "";
    const body = `<div class="hf5-sc">${hints.filter(Boolean).map((x) => `<p class="hf5-hint">${esc(x)}</p>`).join("")}${field}${err ? `<p class="hf5-err" role="alert">${esc(err)}</p>` : ""}<div class="hf5-acts">${acts
      .filter(Boolean)
      .map((a) =>
        a.link
          ? `<button type="button" class="hf5-link" data-a="${a.k}">${esc(a.text)}</button>`
          : pill(a.text, { on: a.go, role: a.go ? SCENE_ROLE[this.kind] : null, cls: a.bad ? "is-bad" : "", attr: { "data-a": a.k } }),
      )
      .join("")}</div></div>`;
    const root = this.frame({ over, title, body, css: SHEET_CSS });
    for (const a of acts.filter(Boolean)) {
      const b = root.querySelector(`[data-a="${a.k}"]`);
      b.onclick = async () => {
        if (b.disabled) return;
        b.disabled = true;
        await a.run();
        if (b.isConnected) b.disabled = false;
      };
    }
    const inp = root.querySelector("[data-in]");
    if (inp) {
      inp.onkeydown = (e) => e.key === "Enter" && root.querySelector('[data-a="save"]')?.click();
      if (input.select) queueMicrotask(() => inp.select());
    }
    return root;
  }
  value() {
    return this.shadowRoot.querySelector("[data-in]")?.value.trim() || "";
  }
  call(body) {
    return famCall(this.h, body, t("scenes.error"));
  }
  done() {
    this.close();
    famReload(this.card);
  }

  // ── a new scene of the family ──
  add(err = "", value = "") {
    const k = this.kind === "cover" ? "cover" : "light";
    this.screen({
      title: t(`scenes.add_${k}`),
      hints: [t(`scenes.add_hint_${k}`), t("scenes.add_where")],
      input: { ph: t(`scenes.ph_${k}`), value },
      err,
      acts: [
        { k: "save", go: true, text: t("scenes.capture"), run: () => this.addSave() },
        { k: "x", text: t("scenes.cancel"), run: () => this.close() },
      ],
    });
  }
  async addSave() {
    const name = this.value();
    if (!name) return this.add(t("scenes.need_name"));
    const { src, kind } = this;
    const r = await this.call({ op: "add", room: src.room, kind: kind === "cover" ? "c" : "l", name, entities: captureList(this.h, src, kind, src.room) });
    if (r.error) return this.add(r.error, name);
    this.done();
  }

  // ── a scene: what may be done with it ──
  menu(err = "") {
    const { edit, may } = this;
    const can = may && !!edit;
    const hint = !may ? (edit ? t("scenes.child") : "") : edit === "knx" ? t("scenes.knx_hint") : edit === "ha" ? t("scenes.ha_hint") : "";
    const fav = this.info ? { k: "fav", text: t(favHas(this.id) ? "scenes.fav_del" : "scenes.fav_add"), run: () => this.fav() } : null;
    this.screen({
      title: this.label(),
      hints: [hint],
      err,
      acts: [
        can && { k: "again", go: true, text: t("scenes.capture"), run: () => this.sure() },
        can && { k: "rename", text: t("scenes.rename"), run: () => this.rename() },
        can && edit !== "knx" && { k: "del", bad: true, text: t(edit === "fam" ? "scenes.delete" : "scenes.hide"), run: () => this.del() },
        fav,
        { k: "more", link: true, text: t("common.more_ha"), run: () => (this.close(), this.card.more(this.id)) },
      ],
    });
  }
  async fav() {
    try {
      await favToggle(this.h, this.id);
      this.close();
    } catch {
      this.menu(t("scenes.fav_error"));
    }
  }
  async del() {
    const r = await this.call({ op: "delete", entity_id: this.id });
    if (r.error) return this.menu(r.error);
    this.done();
  }

  // ── «Запомнить как сейчас» over an existing scene: asked first (the family's «Ночь» once went with one tap) ──
  sure(err = "") {
    const name = this.label();
    const knx = this.edit === "knx";
    this.screen({
      title: t("scenes.sure"),
      hints: [t(knx ? `scenes.sure_knx_${this.kind}` : `scenes.sure_${this.kind === "cover" ? "cover" : "light"}`, { name })],
      err,
      acts: [
        { k: "yes", go: true, text: t("scenes.yes"), run: () => this.again() },
        { k: "x", text: t("scenes.cancel"), run: () => this.menu() },
      ],
    });
  }
  async again() {
    const knx = this.edit === "knx";
    const body = knx ? { op: "knx_learn", entity_id: this.id } : { op: "recapture", entity_id: this.id, entities: captureList(this.h, this.src, this.kind, this.src.room) };
    const r = await this.call(body);
    if (r.error) return this.sure(r.error);
    // nothing on the bus says whether the actuators stored a KNX scene: what to do if it did not change
    this.screen({
      title: t("scenes.done_title"),
      hints: [t(knx ? "scenes.done_knx" : "scenes.done_ha", { name: this.label() })],
      acts: [{ k: "x", go: true, text: t("scenes.ready"), run: () => this.done() }],
    });
    if (!knx) famReload(this.card);
  }

  // ── the name: Hauflo's scenes are renamed in HA; a KNX scene gets its name on this panel (empty → the integrator's) ──
  rename(err = "", value = this.label()) {
    const knx = this.edit === "knx";
    this.screen({
      title: t("scenes.rename"),
      hints: [knx && t("scenes.knx_empty")],
      input: { value, select: true },
      err,
      acts: [
        { k: "save", go: true, text: t("scenes.save"), run: () => this.renameSave() },
        { k: "x", text: t("scenes.cancel"), run: () => this.menu() },
      ],
    });
  }
  async renameSave() {
    const name = this.value();
    const knx = this.edit === "knx";
    if (!name && !knx) return this.rename(t("scenes.need_name"), "");
    const r = await this.call(knx ? { op: "knx_name", entity_id: this.id, name } : { op: "rename", entity_id: this.id, name });
    if (r.error) return this.rename(r.error, name);
    if (knx) famName(this.id, name);
    this.done();
  }
}
if (typeof customElements !== "undefined" && !customElements.get(TAG)) customElements.define(TAG, SceneSheet);
