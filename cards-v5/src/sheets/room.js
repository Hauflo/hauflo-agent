// The room remote (house spec §7: hold on the room tile): the room's light ⏻, its blinds' triple, the setpoint ± on the right
// (K10), other temperatures, «Открыть комнату ›». Every button goes through the tile's own act (one Commander, the same
// optimistic view), so the tile and the sheet always say the same; the sheet redraws when the tile does.
import { Sheet, openSheet } from "./base.js";
import { t, num } from "../core/i18n.js";
import { esc } from "../core/dom.js";
import { svg } from "../core/icons.js";
import { line, number, part, pm, power } from "../kit/primitives.js";
import { allTriple } from "../cards/covers.js";
import { roomModel } from "../cards/room.js";

const TAG = "hauflo-room-sheet-v5";
const CSS = `.hf5-rr{display:flex;align-items:center;justify-content:space-between;gap:var(--hf-sp-3);min-width:0}
.hf5-rr>.hf5-tx{flex:1 1 auto;min-width:0}
.hf5-rr .hf5-sum{font-size:var(--hf-fs-l);font-weight:var(--hf-w-med)}
.hf5-ctr{display:flex;gap:var(--hf-sp-2);flex:none}
.hf5-t2{color:var(--hf-ink-2);font-size:var(--hf-fs-s)}
.hf5-ft .hf5-link{margin-left:auto}`;

class RoomSheet extends Sheet {
  draw() {
    const card = this.card;
    const c = card.config;
    const m = roomModel(card);
    const parts = [];
    if (m.lights.length) {
      const dead = m.lights.every((x) => x.offline);
      const sum = dead ? t("common.offline") : t("lights.count", { n: m.on.length, total: m.lights.length });
      parts.push(part(t("lights.title"), `<div class="hf5-rr"><div class="hf5-tx"><div class="hf5-sum">${esc(sum)}</div></div>${power(m.on.length > 0, { role: "state-light", disabled: dead, attr: { "data-act": "lights" } })}</div>`));
    }
    if (m.covers.length) {
      const live = m.covers.filter((x) => !x.offline);
      const sum = live.length ? t("covers.count", { n: m.open.length, total: live.length }) : t("common.offline");
      parts.push(part(t("covers.title"), `<div class="hf5-rr"><div class="hf5-tx"><div class="hf5-sum">${esc(sum)}</div></div>${allTriple(m.covers)}</div>`));
    }
    if (m.clim) {
      const k = m.clim;
      const set = k.on && k.caps.target && k.target != null;
      const val = number(set ? num(k.target, 1) : k.current != null ? num(k.current, 1) : "—", { size: "l", unit: set || k.current != null ? "°" : "", role: k.role, off: !set });
      const word = k.offline ? t("common.offline") : t(`climate.state.${m.now && m.now !== "mode" ? m.now : k.on ? "idle" : "off"}`);
      const now = set && k.current != null ? t("climate.now", { v: `${num(k.current, 1)}°` }) : "";
      const others = m.others.map((x) => `<div class="hf5-t2">${esc(x.name)} ${esc(num(x.v, 1))}°</div>`).join("");
      const body = set ? pm(val, { l: true, act: "step" }) : val;
      parts.push(part(t("climate.title"), `${line(word)}${body}${now ? line(now) : ""}${others}<button type="button" class="hf5-link" data-clim>${esc(t("room.climate_more"))}${svg("chevron")}</button>`));
    } else if (m.temp !== undefined || m.others.length) {
      parts.push(part(t("room.temp"), `${m.temp !== undefined ? number(num(m.temp, 1), { size: "l", unit: "°", off: true }) : ""}${m.others.map((x) => `<div class="hf5-t2">${esc(x.name)} ${esc(num(x.v, 1))}°</div>`).join("")}`));
    }
    if (m.trouble) parts.unshift(line(m.trouble.text, { cls: m.trouble.kind === "alarm" ? "is-alarm" : m.trouble.kind === "attn" ? "is-warn" : "" }));
    if (!parts.length) parts.push(line(t("room.empty")));
    const foot = c.path ? `<button type="button" class="hf5-link" data-go>${esc(t("room.open"))}${svg("chevron")}</button>` : "";
    const root = this.frame({ over: t("room.over"), title: c.name || t("room.stub"), body: parts.join(""), foot, css: CSS });
    for (const b of root.querySelectorAll("button[data-act]")) b.onclick = () => card.act(b.dataset.act, b.dataset.arg, b);
    const go = root.querySelector("[data-go]");
    if (go) go.onclick = () => (this.close(), card.act("open"));
    const cl = root.querySelector("[data-clim]");
    if (cl) cl.onclick = () => (this.close(), card.more(c.climate));
  }
}
if (!customElements.get(TAG)) customElements.define(TAG, RoomSheet);

export function openRoom(card) {
  if (openSheet()) return;
  const el = document.createElement(TAG);
  if (el.begin(card)) el.draw();
}
/** The tile redrew (a state changed): its open sheet follows. */
export function refreshRoom(card) {
  const s = openSheet();
  if (s && s.card === card && s.localName === TAG) s.draw();
}
