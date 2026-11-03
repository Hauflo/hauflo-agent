// The catalog, generated from the REAL code: every primitive in every state, every descriptor at every remote size.
// Pure string output (no DOM): scripts/catalog.mjs writes it; scripts/shots.mjs screenshots it (dark/light, 390/1440).
import * as P from "../kit/primitives.js";
import { TOKENS_CSS } from "../gen/tokens.js";
import { KIT_CSS } from "../kit/css.js";
import { remoteHtml } from "../remote/render.js";
import { TYPES } from "../descriptors/index.js";
import { setLang, t } from "../core/i18n.js";
import { LIGHTS, CLIMATES } from "./samples.js";
import { trouble, troubleStates } from "../core/command.js";

const CSS = `
body{margin:0;background:var(--hf-bg);color:var(--hf-ink);font-family:var(--hf-font)}
.cat{background:var(--hf-bg);min-height:100vh;padding:var(--hf-sp-4);max-width:1400px;margin:0 auto;display:flex;flex-direction:column;gap:var(--hf-sp-6)}
.cat h1{font-size:var(--hf-fs-xl);font-weight:var(--hf-w-med);margin:0}
.cat h2{font-size:var(--hf-fs-l);font-weight:var(--hf-w-med);margin:0}
.cat-grid{display:flex;flex-wrap:wrap;gap:var(--hf-sp-4);align-items:flex-start}
.cat-cell{display:flex;flex-direction:column;gap:var(--hf-sp-2);background:var(--hf-surface);padding:var(--hf-sp-4);border-radius:var(--hf-r-tile);min-width:0;max-width:100%;box-sizing:border-box}
.cat-cell>small{font-size:var(--hf-fs-xs);color:var(--hf-ink-3)}
.cat-frame{position:relative;max-width:100%;width:var(--w);background:var(--hf-surface-sheet);border-radius:var(--hf-r-sheet);overflow:hidden}
.cat-frame .hf5-sheet{position:relative;left:auto;right:auto;top:auto;bottom:auto;transform:none;width:auto;max-height:none;box-shadow:none;border-radius:0}
.cat-frame .hf5-scrim{display:none}
.cat-w{width:var(--hf-ctl-xl);flex:none}
`;

const cell = (label, html, w) => `<div class="cat-cell"${w ? ` style="width:${w}"` : ""}>${html}<small>${label}</small></div>`;
const row = (a) => `<div class="hf5-row">${a.join("")}</div>`;
const grid = (cells) => `<div class="cat-grid">${cells.join("")}</div>`;

function primitives() {
  const seg3 = [{ v: "a", label: "Авто" }, { v: "b", label: "Низкая" }, { v: "c", label: "Высокая" }];
  const segI = [{ v: "heat", label: t("values.hvac.heat"), icon: "heat" }, { v: "cool", label: t("values.hvac.cool"), icon: "cool" }, { v: "auto", label: t("values.hvac.auto"), icon: "auto" }];
  return [
    cell("round button: normal / on / off / disabled / hold", row([P.btn("power", { label: "p" }), P.btn("power", { label: "p", on: true, role: "state-light" }), P.btn("power", { label: "p", off: true }), P.btn("power", { label: "p", disabled: true }), P.btn("lock", { label: "p", hold: true, role: "state-warn" })])),
    cell("round button, remote size", row([P.btn("plus", { l: true, label: "p" }), P.btn("minus", { l: true, label: "m" }), P.power(true, { role: "state-heat" })])),
    cell("wide button: normal / on / icon / hold", row([P.pill("Повторить", { }), P.pill("15 мин", { on: true, role: "state-light" }), P.pill("В избранное", { icon: "star" }), P.holdPill("Открыть", { icon: "lock", role: "state-warn" })])),
    cell("segmented: words / icons / role", [P.seg(seg3, { sel: "b" }), P.seg(segI, { sel: "cool", icons: true, role: "state-cool" })].join('<div style="height:var(--hf-sp-2)"></div>'), "min(420px,100%)"),
    cell("ONE slider: 0 / 60 / 100 / off / disabled / tone track", [P.slider(0, { label: "a" }), P.slider(60, { label: "a", role: "state-light" }), P.slider(100, { label: "a", role: "state-heat" }), P.slider(40, { label: "a", off: true }), P.slider(40, { label: "a", disabled: true }), P.slider(55, { label: "a", role: "state-light", track: "var(--hf-ct-track)" })].join('<div style="height:var(--hf-sp-2)"></div>'), "min(420px,100%)"),
    cell("icon container: off / on / warn / alarm / offline / failed / pending / dot", row([P.ico("light", {}), P.ico("light", { on: true, role: "state-light" }), P.ico("alert", { tint: "warn" }), P.ico("alert", { tint: "alarm" }), P.ico("light", { dim: true, badge: { offline: true } }), P.ico("light", { badge: { failed: true } }), P.ico("lock", { badge: { locked: true } }), P.ico("light", { badge: { pending: true } }), P.ico("thermostat", { dot: "ok" })])),
    cell("name: short / long / bracket / unbroken long word", [P.name("Свет"), P.name("Люстра над обеденным столом в гостиной"), P.name("Гостиная (потолок)"), P.name("Электрокарниз_спальня_очень_длинное_слово")].join('<div style="height:var(--hf-sp-2)"></div>'), "180px"),
    cell("status line: ok / warn / alarm / role / text only", [P.line("в норме", { dot: "ok" }), P.line("не выполнено", { dot: "warn", warn: true }), P.line("авария", { dot: "alarm" }), P.line("обогрев", { dot: "role", role: "state-heat" }), P.line("только текст")].join("")),
    cell("number scale xs/s/m/l/xl", ["xs", "s", "m", "l", "xl"].map((s) => P.number("21,5", { size: s, unit: "°" })).join("")),
    cell("± stepper with value + current", P.pm(P.number("23", { size: "l", unit: "°", side: t("climate.now", { v: "25,1°" }) }), { l: true, act: "step" }), "min(420px,100%)"),
    cell("± stepper disabled", P.pm(P.number("—", { size: "l", off: true }), { l: true, disabled: true }), "min(420px,100%)"),
    cell("tabs with +N", P.tabs([{ v: 1, label: "Гостиная" }, { v: 2, label: "Кухня" }, { v: 3, label: "Спальня" }, { v: 4, label: "Ещё" }, { v: 5, label: "Кабинет" }], { sel: 1, max: 3 }), "min(420px,100%)"),
    cell("chip / message / caption", [P.chip("Включён · 60%", { dot: "role", role: "state-light" }), P.chip("не выполнено", { warn: true }), P.msg(t("light.only_switch")), P.msg(t("common.failed"), { warn: true, icon: "alert" }), P.cap("Яркость")].join('<div style="height:var(--hf-sp-2)"></div>'), "min(420px,100%)"),
    cell("graph: data / empty", [P.graph([{ cls: "is-now", pts: [[0, 0.4], [0.3, 0.6], [0.6, 0.5], [1, 0.8]] }, { cls: "is-set", pts: [[0, 0.7], [1, 0.7]] }], { role: "state-heat", axis: ["21°", "27°"] }), P.graph([], {})].join(""), "min(420px,100%)"),
    cell("toast: hint / undo", [P.toast(t("common.hold_hint", { action: "открыть" })), P.toast("Свет выключен", { undo: true })].map((x) => `<div style="position:relative;height:var(--hf-ctl-xl)">${x.replace('class="hf5-toast"', 'class="hf5-toast" style="position:absolute;bottom:0"')}</div>`).join(""), "min(420px,100%)"),
  ];
}

function remotes() {
  const out = [];
  const widths = [["sheet", "390px"], ["dialog", "560px"], ["wide", "760px"]];
  const states = (type, samples, only) =>
    Object.entries(samples).map(([name, s]) => {
      const m = TYPES[type].model(s);
      return [name, m, TYPES[type].controls(m, { series: [{ cls: "is-now", pts: [[0, 0.3], [0.4, 0.5], [0.8, 0.45], [1, 0.7]] }, { cls: "is-set", pts: [[0, 0.6], [1, 0.6]] }], axis: ["21,0°", "26,5°"] })];
    });
  for (const [type, samples] of [["light", LIGHTS], ["climate", CLIMATES]]) {
    for (const [name, m, c] of states(type, samples)) {
      for (const [w, px] of widths) {
        const tr = name === "offline" ? trouble(m, {}) : null;
        const html = remoteHtml(TYPES[type].remote, m, c, { trouble: tr, badge: troubleStates(tr), foot: `${t("common.updated_min", { n: 4 })} · ${m.source} · ${m.id}` });
        out.push(cell(`${type} · ${name} · ${w}`, `<div class="cat-frame is-${w}" style="--w:${px}">${html}</div>`));
      }
    }
  }
  // the failed state (retry) once per type
  const m = TYPES.light.model(LIGHTS["dim + tone"]);
  out.push(cell("light · failed · sheet", `<div class="cat-frame is-sheet" style="--w:390px">${remoteHtml(TYPES.light.remote, m, TYPES.light.controls(m, {}), { trouble: "failed", badge: troubleStates("failed"), foot: "" })}</div>`));
  return out;
}

export function catalogHtml() {
  setLang("ru");
  return `<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Hauflo cards v5 — catalog</title>
<style>${TOKENS_CSS}${KIT_CSS}${CSS}</style></head>
<body><div class="hf-host cat" data-hf-theme="atelier" data-mode="dark" id="root"><h1>Hauflo cards v5 — catalog</h1>
<h2>Primitives</h2>${grid(primitives())}
<h2>Remotes (descriptor × state × size)</h2>${grid(remotes())}</div>
<script>const q=new URLSearchParams(location.search);const r=document.getElementById("root");r.dataset.mode=q.get("mode")||"dark";document.body.style.background=getComputedStyle(r).getPropertyValue("--hf-bg")||"";</script></body></html>`;
}
