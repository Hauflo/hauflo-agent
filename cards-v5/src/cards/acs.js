// hauflo-acs · every AC of the house in one list, or (kind: floor_heating) every floor heating zone — climate spec v5
// rows (K3/R22) + the group header of house spec §2 («ТИП · что происходит» · one action).
// Config (unchanged from the previous card): title, block, kind ("ac" | "floor_heating"), entities (string |
// {entity, name, zone, floor, path, activity}). Rows under the building's floor when they say it and there are several.
// Header: caps title · one summary — «авария · 1» > «нет связи» > «нагрев · 2 · охлаждение · 1» > «в норме» / «все выкл».
// ACs: ⏻ «Выключить все» (class B, one tap; never «включить все» — which mode for 14 rooms?); the floors have none
// (comfort floor: the family sets the room, the floor is kept). Rows are the climate rows: tap / hold = the remote,
// − setpoint + in place; a row with `path` adds «Открыть комнату ›» to its remote.
import { t } from "../core/i18n.js";
import { cap, head, msg, power } from "../kit/primitives.js";
import { blockTitle } from "../core/house.js";
import { openAlarms, isCritical } from "../core/alarms.js";
import { climateNow } from "../adapters/climate.js";
import { CLIMATE, climateRow, model, groupSummary as summary } from "./climate.js";

const ROLE = "state-cool";
const items = (c) => (Array.isArray(c.entities) ? c.entities.map((e) => (typeof e === "string" ? { entity: e } : e)).filter((e) => e?.entity) : []);
const actIds = (a) => [...(a?.heat || []), ...(a?.cool || []), ...(a?.valves || [])];
const floorKind = (c) => c.kind === "floor_heating";
const areaOf = (h, id) => {
  const e = h?.entities?.[id];
  return e?.area_id || (e?.device_id && h.devices?.[e.device_id]?.area_id) || null;
};

/** The rooms with an open equipment fault (not a danger — that is the alert card's): their thermostats say «авария». */
function faultAreas(card) {
  const h = card.hass;
  const areas = new Set();
  for (const a of openAlarms(card, [], true)) {
    if (a.returned || isCritical(h, a) || h.states[a.entity]?.state !== "on") continue;
    const ar = areaOf(h, a.entity);
    if (ar) areas.add(ar);
  }
  return areas;
}

/** Rows with what each one does now (the same words as the climate card), fault and the shown name. */
function rows(card) {
  const h = card.hass;
  const faults = faultAreas(card);
  const list = items(card.config)
    .map((e) => {
      const m = model(card, e.entity);
      if (!m) return null;
      const area = areaOf(h, e.entity);
      return { e, m, now: climateNow(m, h.states, e.activity), fault: !m.offline && !!area && faults.has(area), name: e.name || m.name };
    })
    .filter(Boolean);
  // two rows that read the same («Кухня», «Кухня»): the thermostat's own name tells them apart
  const seen = new Map();
  for (const r of list) seen.set(r.name.toLowerCase(), (seen.get(r.name.toLowerCase()) || 0) + 1);
  for (const r of list) if (seen.get(r.name.toLowerCase()) > 1 && r.m.name.toLowerCase() !== r.name.toLowerCase()) r.name = `${r.name} · ${r.m.name}`;
  return list;
}

/** The title when nobody named it: by what is in the list (all ACs / heating only / climate). */
function defaultTitle(c, rs) {
  if (floorKind(c)) return t("acs.title_floor");
  if (rs.length && rs.every((r) => r.m.modes.some((x) => x === "dry" || x === "fan_only"))) return t("acs.title_ac");
  if (rs.length && !rs.some((r) => r.m.modes.some((x) => ["cool", "heat_cool", "auto", "dry", "fan_only"].includes(x)))) return t("acs.title_heat");
  return t("climate.title");
}

const offable = (rs) => rs.filter((r) => r.m.on && !r.m.offline && r.m.caps.power);

export const ACS = {
  key: "acs",
  watch: (c) => [...new Set(items(c).flatMap((e) => [e.entity, ...actIds(e.activity)]))],
  grid: () => ({ columns: 12, rows: "auto", min_columns: 3 }),
  stub: (h) => ({ entities: Object.keys(h?.states || {}).filter((x) => x.startsWith("climate.")).slice(0, 8) }),
  render(card, v) {
    const c = card.config;
    const rs = rows(card);
    const title = blockTitle(card, defaultTitle(c, rs));
    if (!rs.length) return `${head({ title })}${msg(t("climate.none"), { icon: "thermostat" })}`;
    const s = summary(rs);
    // the button keeps its place in every state (K9): pale when nothing is on
    const off = floorKind(c) || v === "S" ? "" : power(true, { l: false, role: ROLE, label: t("acs.turn_all_off"), disabled: !offable(rs).length, attr: { "data-act": "all_off" } });
    const top = head({ title, quiet: true, words: s.text, dot: s.dot, tone: s.dot !== "ok" && s.dot, ctl: off });
    // the building's floors as sub-headers (several only), in the order they first come
    const floors = [...new Set(rs.map((r) => r.e.floor).filter(Boolean))];
    const groups = floors.length > 1 ? [...floors.map((f) => [f, rs.filter((r) => r.e.floor === f)]), ["", rs.filter((r) => !r.e.floor)]] : [["", rs]];
    const body = groups
      .filter(([, g]) => g.length)
      .map(([f, g]) => (f ? `<div class="hf5-fl">${cap(f)}</div>` : "") + g.map((r) => climateRow(card, v, r.e.entity, r.name, r.e.activity, { fault: r.fault, floor: floorKind(c) })).join(""))
      .join("");
    return `${top}<div class="hf5-grid is-rows">${body}</div>`;
  },
  css: `.hf5-grid>.hf5-fl{grid-column:1/-1;padding-top:var(--hf-sp-2)}
.hf5-grid>.hf5-fl:first-child{padding-top:0}`,
  hold(card, el) {
    const e = items(card.config).find((x) => x.entity === el.dataset.id);
    if (!e) return false;
    card.more(e.entity, e.path);
    return true;
  },
  act(card, name, arg, el) {
    if (name === "all_off") {
      const rs = offable(rows(card));
      if (!rs.length) return null;
      const byOff = rs.filter((r) => r.m.hasOff).map((r) => r.m.id);
      const byTurn = rs.filter((r) => !r.m.hasOff).map((r) => r.m.id);
      const calls = [
        ...(byOff.length ? [{ domain: "climate", service: "set_hvac_mode", data: { entity_id: byOff, hvac_mode: "off" } }] : []),
        ...(byTurn.length ? [{ domain: "climate", service: "turn_off", data: { entity_id: byTurn } }] : []),
      ];
      return { key: "all", calls, opt: Object.fromEntries(rs.map((r) => [r.m.id, { mode: "off" }])) };
    }
    const r = CLIMATE.act(card, name, arg, el);
    if (r?.more) r.go = items(card.config).find((x) => x.entity === r.more)?.path;
    return r;
  },
};
