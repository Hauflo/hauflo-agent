// Layer 2 · primitives. Every card and remote is assembled ONLY from these (no local copies of a button, slider…).
// Each returns markup; behaviour is attached by layer 3 (behaviours/*) through data-* hooks, never inline.
// Role = a canon role token name ("state-light", "state-heat", …); the primitive turns it into --hf5-role* vars.
import { esc, cls, attrs, vars, role as rv, clamp } from "../core/dom.js";
import { glyph, svg } from "../core/icons.js";
import { t } from "../core/i18n.js";
import { CANON } from "../gen/canon.js";

/** Role → the three custom properties every primitive reads (fill, -text fill under ink-on-fill, -tint). */
export const roleVars = (r) => (r ? { "--hf5-role": rv(r), "--hf5-role-text": rv(r, "text"), "--hf5-role-tint": rv(r, "tint"), "--hf5-role-glow": rv(r, "glow") } : {});

/** K11 badge priority (sizes.json v5.badge.priority): offline > failed > locked > pending. */
const BADGE_GLYPH = { offline: "offline", failed: "failed", locked: "lock", pending: null };
export const badgeOf = (st = {}) => {
  for (const k of CANON.badgePriority) if (st[k]) return { kind: k, glyph: BADGE_GLYPH[k] };
  return null;
};

/** K3 · name split: brackets, «,», «—», «·», «/» move to the smaller description line. */
export const splitName = (name = "") => {
  const s = String(name).trim();
  let m = s.match(/^(.+?)\s*\((.+)\)\s*$/);
  if (m) return { name: m[1], desc: m[2] };
  m = s.match(/^(.+?)\s*(?:,|\s—\s|\s–\s|\s·\s|\s\/\s)\s*(.+)$/);
  if (m) return { name: m[1], desc: m[2] };
  return { name: s, desc: "" };
};

const press = (o) => (o.on != null ? ` aria-pressed="${!!o.on}"` : "");
const bcls = (base, o) => cls(base, o.l && "hf5-l", o.on && "hf5-on", o.off && "is-off", o.hold && "hf5-hold", o.cls);

/** Round icon button · o: {l (remote size), on, off, hold (confirm by hold), label (aria, required), role, attr}. */
export const btn = (ic, o = {}) =>
  `<button type="button" class="${bcls("hf5-btn", o)}" aria-label="${esc(o.label || "")}"${press(o)}${attrs(o.attr)}${vars(roleVars(o.role))}${o.disabled ? " disabled" : ""}>${glyph(ic)}</button>`;

/** Wide (pill) action button with a word (+ optional icon) · same options as btn + tag (a small corner mark, admins' «KNX»). */
export const pill = (text, o = {}) =>
  `<button type="button" class="${bcls("hf5-pill", o)}"${o.label ? ` aria-label="${esc(o.label)}"` : ""}${press(o)}${attrs(o.attr)}${vars(roleVars(o.role))}${o.disabled ? " disabled" : ""}>${glyph(o.icon)}${text ? `<span>${esc(text)}</span>` : ""}${
    o.tag ? `<i class="hf5-tag" aria-hidden="true">${esc(o.tag)}</i>` : ""
  }</button>`;

/** Power = the round icon button, never a full-width bar. */
export const power = (on, o = {}) => btn("power", { l: true, label: t(on ? "common.turn_off" : "common.turn_on"), ...o, on: !!on });

/** Segmented control (modes, fan, swing, period, presets) · items [{v, label, icon}] · o: {sel, icons, l, role, act, label}. */
export const seg = (items, o = {}) =>
  `<div class="${cls("hf5-seg", o.l && "hf5-l", o.icons && "is-icons", o.cls)}" role="radiogroup"${o.label ? ` aria-label="${esc(o.label)}"` : ""}>${items
    .map((it) => {
      const on = it.v === o.sel;
      return pill(o.icons && it.icon ? "" : it.label, {
        icon: it.icon, on, l: o.l, role: it.role || o.role, label: o.icons || it.aria ? it.aria || it.label : null,
        attr: { role: "radio", "aria-checked": String(on), "data-act": o.act, "data-arg": it.v, title: o.icons ? it.label : null },
      });
    })
    .join("")}</div>`;

/** The ONE slider (brightness, colour temperature, position, volume) · v 0–100 · o: {l, label, act, role, fill, track, off, disabled, valuetext}. */
export const slider = (v, o = {}) => {
  const p = Math.round(clamp(+v || 0, 0, 100));
  return `<div class="${cls("hf5-slider", o.l && "hf5-l", o.track && "is-track", o.off && "is-off", o.cls)}" role="slider" tabindex="0" aria-label="${esc(o.label || "")}" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${p}"${o.valuetext ? ` aria-valuetext="${esc(o.valuetext)}"` : ""}${o.disabled ? ' aria-disabled="true"' : ""}${attrs({ "data-slide": o.act, ...(o.attr || {}) })}${vars({ "--v": p, "--hf5-fill": o.fill, "--hf5-track": o.track, ...roleVars(o.role) })}><i></i></div>`;
};

/** Icon container with one badge slot and an optional corner dot · o: {on, tint: warn|alarm, role, badge: states|{kind}, dot, dim}. */
export const ico = (ic, o = {}) => {
  const bd = o.badge && (o.badge.kind ? o.badge : badgeOf(o.badge));
  return `<span class="${cls("hf5-ico", o.on && "is-on", o.tint && "is-" + o.tint, o.dim && "is-dim", o.cls)}"${vars(roleVars(o.role))}>${glyph(ic)}${
    bd ? `<span class="hf5-badge is-${bd.kind}">${bd.glyph ? svg(bd.glyph) : ""}</span>` : ""
  }${o.dot ? `<i class="hf5-dot is-${o.dot}"></i>` : ""}</span>`;
};

/** Status line: ONE line, the dot inline at its start · o: {dot: ok|warn|alarm|role, short, warn, role}. */
export const line = (text, o = {}) =>
  !text && !o.dot
    ? ""
    : `<div class="${cls("hf5-line", o.warn && "is-warn", o.cls)}"${o.short ? ` data-short="${esc(o.short)}"` : ""}${vars(roleVars(o.role))}>${o.dot ? `<i class="hf5-dot is-${o.dot}"></i>` : ""}${
        text ? `<span class="hf5-t">${esc(text)}</span>` : ""
      }</div>`;

/** Name: max 2 lines between words; desc split out unless o.split === false. */
export const name = (text, o = {}) => {
  const n = o.split === false ? { name: text, desc: "" } : splitName(text);
  return `<div class="${cls("hf5-name", o.cls)}">${esc(n.name)}</div>${n.desc && o.desc !== false ? `<div class="hf5-desc">${esc(n.desc)}</div>` : ""}`;
};

/** Number on the canon scale (22/32/48/76/104) · size: xs|s|m|l|xl · o: {unit, side (secondary value next to it), off}. */
export const number = (text, o = {}) =>
  `<div class="${cls("hf5-num", o.off && "is-off", o.role && !o.off && "is-role", o.cls)}"${vars({ "--hf5-num": `var(--hf-fs-num-${o.size || "xl"})`, "--hf5-numw": `var(--hf-w-num${["xs", "s", "m"].includes(o.size) ? "-" + o.size : ""})`, ...roleVars(o.off ? null : o.role) })}><b>${esc(text)}${o.unit ? `<sup>${esc(o.unit)}</sup>` : ""}</b>${
    o.side ? `<small>${esc(o.side)}</small>` : ""
  }</div>`;

/** ± stepper: value markup | + over − (round buttons), centred on the value · o: {l, act, up, down, disabled}. */
export const pm = (valueHtml, o = {}) =>
  `<div class="${cls("hf5-setp", o.l && "hf5-l", o.cls)}"><div class="hf5-val">${valueHtml}</div><div class="hf5-pm">${btn("plus", {
    l: o.l, label: o.up || t("common.up"), disabled: o.disabled, attr: { "data-act": o.act, "data-arg": "+1" },
  })}${btn("minus", { l: o.l, label: o.down || t("common.down"), disabled: o.disabled, attr: { "data-act": o.act, "data-arg": "-1" } })}</div></div>`;

/** Row stepper − value + (a line of a card: zones, compact thermostat) · o: {act, up, down, disabled, attr (on both buttons)}. */
export const step = (valueHtml, o = {}) =>
  `<div class="${cls("hf5-step", o.cls)}">${btn("minus", { label: o.down || t("common.down"), disabled: o.disabled, attr: { "data-act": o.act, "data-arg": "-1", ...(o.attr || {}) } })}<div class="hf5-val">${valueHtml}</div>${btn("plus", {
    label: o.up || t("common.up"), disabled: o.disabled, attr: { "data-act": o.act, "data-arg": "+1", ...(o.attr || {}) },
  })}</div>`;

/** Hold-to-confirm button (class A): a pill whose fill grows under the label · o as pill + act. */
export const holdPill = (text, o = {}) => pill(text, { ...o, hold: true, attr: { ...(o.attr || {}), "data-hold": o.act } });

/** ⋮ (M/L headers and the remote). */
export const more = (label) => `<button type="button" class="hf5-more" aria-label="${esc(label)}" data-more>${svg("menu")}</button>`;

/** Tabs with «+N»: shows the first `max`, the rest folded into one «+N» tab (opens the full list via data-act). */
export const tabs = (items, o = {}) => {
  const max = o.max ?? items.length;
  const shown = items.slice(0, max);
  const rest = items.length - shown.length;
  return `<div class="hf5-tabs" role="tablist"${o.label ? ` aria-label="${esc(o.label)}"` : ""}>${shown
    .map((it) => `<button type="button" class="hf5-tab" role="tab" aria-selected="${it.v === o.sel}"${attrs({ "data-act": o.act, "data-arg": it.v })}>${esc(it.label)}</button>`)
    .join("")}${rest > 0 ? `<button type="button" class="hf5-tab is-more"${attrs({ "data-act": o.moreAct || o.act, "data-arg": "+more" })} aria-label="${esc(t("common.more_n", { n: rest }))}">+${rest}</button>` : ""}</div>`;
};

const sentence = (s = "") => s.charAt(0).toUpperCase() + s.slice(1);
/** State chip (remote): dot + words, first letter capital. */
export const chip = (text, o = {}) =>
  `<div class="${cls("hf5-chip", o.warn && "is-warn")}"${vars(roleVars(o.role))}>${o.dot ? `<i class="hf5-dot is-${o.dot}"></i>` : ""}<span>${esc(sentence(text))}</span></div>`;

/** Section label (caps — only labels). */
export const cap = (text) => `<div class="hf5-cap">${esc(text)}</div>`;

/** Message plate (only-switch light, assumed state, trouble). */
export const msg = (text, o = {}) => `<div class="${cls("hf5-msg", o.warn && "is-warn")}">${o.icon ? glyph(o.icon) : ""}<span>${esc(text)}</span></div>`;

/** Sheet / remote shell: header «kind · source / Name / ×», body (one or two columns), footer «updated · source · id · [go: the room ›] · More in HA ›». */
export const shell = (o) => `<div class="hf5-scrim" data-close></div>
<div class="hf5-sheet" role="dialog" aria-modal="true" aria-label="${esc(o.title)}" tabindex="-1">
  <div class="hf5-grab"></div>
  <div class="hf5-head">${o.icon || ""}<div class="hf5-stack">${cap(o.over)}${name(o.title, { split: false })}</div><button type="button" class="hf5-x" data-close aria-label="${esc(t("common.close"))}">${svg("close")}</button></div>
  <div class="${cls("hf5-body", o.cols === 2 && "is-2col")}">${o.body}</div>
  <div class="hf5-foot"><span>${esc(o.foot)}</span>${o.go ? `<button type="button" class="hf5-link" data-go>${esc(o.go)}${svg("chevron")}</button>` : ""}<button type="button" class="hf5-link" data-more-ha>${esc(t("common.more_ha"))}${svg("chevron")}</button></div>
</div>`;

/** Undo / hint toast markup · o: {undo: true} adds the «Вернуть» button. */
export const toast = (text, o = {}) =>
  `<div class="hf5-toast" role="status" aria-live="polite"><span>${esc(text)}</span>${o.undo ? pill(t("common.undo"), { attr: { "data-undo": "" } }) : ""}</div>`;

/** Graph · series [{cls: "is-now"|"is-set", pts: [[x 0–1, y 0–1]]}] drawn on the canon viewBox; axis [left, right] labels; empty → the one «нет данных» line. */
export const graph = (series, o = {}) => {
  const live = series.filter((s) => s.pts.length > 1);
  if (!live.length) return `<div class="hf5-empty">${esc(o.empty || t("common.no_data"))}</div>`;
  const W = CANON.graph.w, H = CANON.graph.h;
  const xy = (p) => `${(p[0] * W).toFixed(1)},${((1 - p[1]) * H).toFixed(1)}`;
  const p0 = live[0].pts, area = p0.map(xy);
  // the area stands under the line only where there is data (from x = 0 it drew a slope up to the first reading)
  const x = (p) => (p[0] * W).toFixed(1);
  return `<svg class="hf5-graph" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none"${vars(roleVars(o.role))} role="img" aria-label="${esc(o.label || "")}"><polygon class="is-area" points="${x(p0[0])},${H} ${area.join(" ")} ${x(p0[p0.length - 1])},${H}"/>${live
    .map((s) => `<polyline class="${s.cls}" points="${s.pts.map(xy).join(" ")}"/>`)
    .join("")}</svg>${o.axis ? `<div class="hf5-axis"><span>${esc(o.axis[0])}</span><span>${esc(o.axis[1])}</span></div>` : ""}`;
};

/** Column of parts inside the remote. */
export const part = (title, html) => `<section class="hf5-part">${title ? cap(title) : ""}${html}</section>`;

/** Tile template (slots) · o: {size: xs|s|m|l, icon (name or ico markup), on, role, tint, badge, name, status, short, dot,
 *  bare (icon only, the name goes to aria-label), ctl (controls markup), more (label → ⋮, M and up only), warn (status in the warning colour), fill (0–100: the value as the tile's fill),
 *  value ({text, unit, off}: the big thin value top-right opposite the icon, column tiles — owner 03.11), art (markup of the drawing under the content), attr, cls}.
 *  XS = one row: icon · name/status · control. S–L = a column: [icon · value|⋮] / name / status / controls. */
export const tile = (o = {}) => {
  const big = o.size === "m" || o.size === "l";
  const ic = o.icon == null ? "" : String(o.icon).includes("hf5-ico") ? o.icon : ico(o.icon, { on: o.on, tint: o.tint, badge: o.badge, role: o.role, dim: o.dim });
  // v1: the zone and the state share ONE line under the name («зона ТВ · выкл») — the tile is two lines, never three
  const nm = splitName(o.name), st = String(o.status ?? "").trim();
  const sub = nm.desc && o.desc !== false ? (st ? `${nm.desc} · ${st}` : nm.desc) : o.status;
  // too long for the line → the zone alone: it tells two «Люстра» / «Кухня» apart, the tint and dot already say the state (trouble keeps its word)
  const short = nm.desc && st && o.desc !== false && !o.warn ? nm.desc : o.short;
  const tx = `<div class="hf5-tx">${name(nm.name, { split: false })}${line(sub, { dot: o.dot, short, role: o.dotRole, warn: o.warn })}</div>`;
  const fv = o.fill != null ? { "--v": Math.round(clamp(+o.fill || 0, 0, 100)) } : {};
  const under = `${o.art || ""}${o.fill != null && !o.art ? `<span class="hf5-tfill"></span>` : ""}`;
  if (o.bare) return `<div class="${cls("hf5-tile", "is-bare", "hf5-tilehold", o.on && "is-on", o.cls)}"${attrs(o.attr)}${vars({ ...roleVars(o.role), ...fv })}>${under}${ic}</div>`;
  const val = o.value && o.value.text != null && o.value.text !== "" ? number(o.value.text, { size: "s", unit: o.value.unit, off: o.value.off, cls: "hf5-tv" }) : "";
  const corner = o.corner ? `<div class="hf5-tk">${o.corner}</div>` : val;
  const head = o.size === "xs" ? ic : `<div class="hf5-th">${ic}${corner || (big && o.more ? more(o.more) : "")}</div>`;
  return `<div class="${cls("hf5-tile", "is-" + (o.size || "s"), "hf5-tilehold", o.on && "is-on", o.art && "is-art", o.cls)}"${attrs(o.attr)}${vars({ ...roleVars(o.role), ...fv })}>${under}${head}${tx}${
    o.ctl ? `<div class="hf5-tc">${o.ctl}</div>` : ""
  }</div>`;
};

/** Card header (owner 03.11): caps title over ONE line — [big thin number + unit] [words: main + optional second line]
 *  [buttons], all on one centre axis; the words wrap inside their own column, never under the buttons. The line keeps the
 *  number's height even with no number, so the card does not jump when the value comes and goes.
 *  o: {title, value, unit, size: s|m, role, off, words, sub, dot, tone: warn|alarm, ctl, icon} */
export const head = (o = {}) => {
  const size = o.size || "m";
  const val = o.value == null || o.value === "" ? "" : number(String(o.value), { size, unit: o.unit, off: o.off, role: o.role });
  const w = o.words
    ? `<div class="hf5-hw"><span class="${cls("hf5-hw1", "hf5-sum", o.tone && "is-" + o.tone)}">${o.dot ? `<i class="hf5-dot is-${o.dot}"></i>` : ""}${esc(o.words)}</span>${o.sub ? `<span class="hf5-hw2">${esc(o.sub)}</span>` : ""}</div>`
    : "";
  const ctl = o.ctl ? `<div class="hf5-hc">${o.ctl}</div>` : "";
  // quiet (v1 list cards): no number; the caption is one small line under the title, the button centred on both
  if (o.quiet) return `<div class="hf5-ch is-head is-quiet">${o.icon || ""}<div class="hf5-tx">${cap(o.title)}${w}</div>${ctl}</div>`;
  const hl = val || w || ctl ? `<div class="hf5-hl"${vars({ "--hf5-num": `var(--hf-fs-num-${size})` })}>${val}${w}${ctl}</div>` : "";
  return `<div class="hf5-ch is-head">${o.icon || ""}<div class="hf5-tx">${cap(o.title)}${hl}</div></div>`;
};
