// hauflo-auto · automations and scripts: on/off, run, when it last worked (the old card's keys: title, entities [id]).
// Rows of the status kind: icon · name · «worked 5 min ago» · ⏻ (automation) or «Run» (script); a tap on the row = HA's dialog.
import { t, num } from "../core/i18n.js";
import { head, msg, tile, pill, btn } from "../kit/primitives.js";
import { blockTitle } from "../core/house.js";
import { OFFLINE } from "../adapters/common.js";
import { CANON } from "../gen/canon.js";

const A = CANON.cards.auto;

const ago = (iso, now = Date.now()) => {
  const min = Math.max(0, Math.floor((now - new Date(iso).getTime()) / 60000));
  if (min < 1) return t("auto.ago_now");
  if (min < 60) return t("auto.ago_min", { n: min });
  if (min < 1440) return t("auto.ago_h", { n: Math.floor(min / 60) });
  return t("auto.ago_d", { n: Math.floor(min / 1440) });
};

function row(card, id, s) {
  const a = s.attributes || {};
  const script = id.startsWith("script.");
  const gone = OFFLINE.has(s.state);
  const on = s.state === "on";
  const last = a.last_triggered;
  const word = gone
    ? t("common.offline")
    : script
      ? on ? t("auto.running") : last ? t("auto.ran", { ago: ago(last) }) : t("auto.never_ran")
      : !on ? t("auto.off") : last ? t("auto.fired", { ago: ago(last) }) : t("auto.never_fired");
  const nm = a.friendly_name || id;
  const ran = card._ran?.[id];
  const ctl = gone
    ? ""
    : script
      ? on && !ran
        ? pill(t("auto.stop"), { icon: "stop", role: A.role, attr: { "data-act": "stop", "data-arg": id } })
        : pill(t(ran ? "auto.started" : "auto.run"), { icon: ran ? "check" : "play", on: !!ran, role: A.role, attr: { "data-act": "run", "data-arg": id } })
      : btn("power", { on, role: A.role, label: t(on ? "common.turn_off" : "common.turn_on"), attr: { "data-act": "tog", "data-arg": id } });
  return tile({
    size: "xs",
    icon: script ? "play" : "timer",
    on: !script && on && !gone,
    role: A.role,
    badge: gone ? { offline: true } : null,
    name: nm,
    status: word,
    cls: gone ? "is-offline" : "",
    ctl,
    attr: { "data-tap": "open", "data-id": id, role: "button", tabindex: "0", "aria-label": `${nm}, ${word}` },
  });
}

export const AUTO = {
  key: "auto",
  watch: (c) => c.entities || [],
  grid: () => ({ columns: 12, rows: "auto", min_columns: 3 }),
  stub: (h) => ({ entities: Object.keys(h?.states || {}).filter((x) => /^(automation|script)\./.test(x)).slice(0, 4) }),
  render(card, v) {
    const title = blockTitle(card, t("auto.title"));
    const rows = (card.config.entities || []).map((id) => ({ id, s: card.hass.states[id] })).filter((r) => r.s);
    if (!rows.length) return `${head({ title })}${msg(t("auto.empty"), { icon: "timer" })}`;
    // the header line: how many automations are on of all; S has room for nothing else (the rows are in M and up)
    const on = rows.filter((r) => r.s.state === "on" && !r.id.startsWith("script.")).length;
    const all = rows.filter((r) => !r.id.startsWith("script.")).length;
    if (v === "S") return head({ title, value: num(on, 0), unit: `/${num(all, 0)}`, size: "xs", off: !on });
    const top = all > 1 ? head({ title, value: num(on, 0), size: v === "L" ? "m" : "s", off: !on, role: A.role, words: t("auto.on_of", { all: num(all, 0) }) }) : head({ title });
    return `${top}<div class="hf5-grid is-rows">${rows.map((r) => row(card, r.id, r.s)).join("")}</div>`;
  },
  act(card, name, arg, el) {
    if (name === "open") return { more: el.dataset.id };
    if (name === "tog") return { key: arg, calls: [{ domain: "automation", service: "toggle", data: { entity_id: arg } }] };
    if (name === "stop") return { key: arg, calls: [{ domain: "script", service: "turn_off", data: { entity_id: arg } }] };
    if (name === "run") {
      (card._ran ??= {})[arg] = 1;
      setTimeout(() => (delete card._ran[arg], card.render()), A.ran_ms);
      return { key: arg, calls: [{ domain: "script", service: "turn_on", data: { entity_id: arg } }] };
    }
    return null;
  },
};
