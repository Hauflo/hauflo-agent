// Generic remote · pure: descriptor + model + controls → markup, made only of primitives. Same for every type.
import { btn, pill, seg, slider, pm, number, line, part, graph, msg, chip, shell, ico, power, cap, tabs } from "../kit/primitives.js";
import { t } from "../core/i18n.js";
import { esc } from "../core/dom.js";
import { glyph } from "../core/icons.js";

const has = (m, need) => !need || (need[0] === "!" ? !m.caps?.[need.slice(1)] : !!m.caps?.[need]);

/** One control spec → markup. Kinds: status, slider, seg, setpoint, buttons, graph, note. */
export function control(c) {
  switch (c.kind) {
    case "status":
      return `<div class="hf5-row hf5-status">${chip(c.text, { dot: c.dot, role: c.role })}${c.power ? power(c.power.on, { role: c.role, disabled: c.power.disabled, attr: { "data-act": c.power.act } }) : ""}</div>`;
    case "slider":
      return slider(c.value, { l: true, label: c.label, act: c.act, role: c.role, track: c.track, off: c.off, disabled: c.disabled, valuetext: c.valuetext });
    case "seg":
      return seg(c.items, { l: true, sel: c.sel, act: c.act, icons: c.icons, role: c.role });
    case "setpoint": {
      const val = number(c.value, { size: "l", unit: c.unit, side: c.side, off: c.off });
      return c.readonly ? val : pm(val, { l: true, act: c.act, disabled: c.disabled, up: c.up, down: c.down });
    }
    case "buttons":
      return `<div class="${c.seg ? "hf5-seg" : "hf5-row"}${c.cls ? " " + c.cls : ""}">${c.items
        .map((b) => {
          // hold: class A (gates…) — the action runs only after a 1 s hold (data-hold), a tap tells how
          const attr = b.hold ? { "data-hold": b.act, "data-arg": b.arg } : { "data-act": b.act, "data-arg": b.arg };
          const o = { l: true, on: b.on, off: b.off, hold: b.hold, icon: b.icon, disabled: b.disabled, label: b.label, role: b.role, attr };
          return b.text ? pill(b.text, o) : btn(b.icon, o);
        })
        .join("")}</div>${c.note ? line(c.note) : ""}`;
    case "graph":
      return `${c.periods ? seg(c.periods, { sel: c.period, act: "period", role: c.role }) : ""}${graph(c.series, { role: c.role, axis: c.axis, empty: c.empty, label: c.label || t("common.history") })}`;
    case "bigpower":
      // the device's one big ⏻ (canon --hf-ctl-power), centred; remote/css.js sizes it
      return `<div class="hf5-bigpower">${btn("power", { l: true, on: c.on, role: c.role, disabled: c.disabled, label: t(c.on ? "common.turn_off" : "common.turn_on"), attr: { "data-act": c.act } })}</div>`;
    case "facts":
      return `<dl class="hf5-facts">${c.items.map((i) => `<div${i.warn ? ' class="is-warn"' : ""}><dt>${esc(i.label)}</dt><dd>${esc(i.value)}</dd></div>`).join("")}</dl>`;
    case "note":
      return msg(c.text, { icon: c.icon });
  }
  return "";
}

/** Whole remote markup (the shell + sections in columns). ctx: {trouble, retry, foot, go, wide} */
export function remoteHtml(desc, m, controls, ctx = {}) {
  const cols = [[], []];
  for (const s of desc.sections) {
    const c = controls[s.control];
    if (!c || !has(m, s.need)) continue;
    cols[desc.cols === 2 ? s.col : 0].push(part(s.title ? t(s.title) : "", control(c)));
  }
  const trouble = ctx.trouble ? msg(t(`common.${ctx.trouble === "sending" ? "sending" : ctx.trouble}`), { warn: ctx.trouble !== "sending" }) + (ctx.trouble === "failed" ? pill(t("common.retry"), { l: true, attr: { "data-act": "retry" } }) : "") : "";
  const body = `${trouble ? `<div class="hf5-col hf5-trouble">${trouble}</div>` : ""}` + cols.filter((c) => c.length).map((c) => `<div class="hf5-col">${c.join("")}</div>`).join("");
  return shell({
    icon: ico(m.icon, { on: m.on, role: m.role, badge: ctx.badge }),
    over: `${m.over || t(desc.over)} · ${m.source}`,
    title: m.name,
    cols: desc.cols === 2 && cols[1].length ? 2 : 1,
    body,
    foot: ctx.foot || "",
    go: ctx.go || "",
  });
}
