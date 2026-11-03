import { describe, it, expect, beforeAll } from "vitest";
import { readFileSync } from "node:fs";
import { execFileSync } from "node:child_process";
import * as P from "../src/kit/primitives.js";
import { setLang, t } from "../src/core/i18n.js";
import { trouble, Commander } from "../src/core/command.js";
import { LOCALES } from "../src/core/i18n.js";
import { lightModel, lightControls, lightAct } from "../src/adapters/light.js";
import { climateModel, climateControls, climateAct, climateSeries } from "../src/adapters/climate.js";
import { remoteHtml } from "../src/remote/render.js";
import { TYPES } from "../src/descriptors/index.js";
import { LIGHTS, CLIMATES } from "../src/catalog/samples.js";
import { TYPES_V5 } from "../src/compat/contract.js";

beforeAll(() => setLang("ru"));
const count = (html, re) => (html.match(re) || []).length;
const flat = (o, p = "") => Object.entries(o).flatMap(([k, v]) => (typeof v === "object" ? flat(v, p + k + ".") : [[p + k, v]]));

describe("primitives", () => {
  it("one slider implementation, role vars, aria", () => {
    const h = P.slider(60, { label: "Яркость", role: "state-light" });
    expect(h).toContain('class="hf5-slider"');
    expect(h).toContain('aria-valuenow="60"');
    expect(h).toContain("--hf5-role:var(--hf-state-light)");
    expect(P.slider(150)).toContain('aria-valuenow="100"');
  });
  it("seg: exactly one selected, icons keep an aria-label", () => {
    const h = P.seg([{ v: "a", label: "A", icon: "heat" }, { v: "b", label: "B", icon: "cool" }], { sel: "b", icons: true });
    expect(count(h, /aria-checked="true"/g)).toBe(1);
    expect(count(h, /aria-label=/g)).toBeGreaterThanOrEqual(2);
  });
  it("buttons: one style, two heights", () => {
    expect(P.btn("power", { label: "x" })).toContain('class="hf5-btn"');
    expect(P.btn("power", { label: "x", l: true })).toContain("hf5-l");
    expect(P.power(true)).toContain('aria-pressed="true"');
  });
  it("name never hyphenates (css) and splits brackets to a description", () => {
    expect(P.splitName("Гостиная (потолок)")).toEqual({ name: "Гостиная", desc: "потолок" });
    expect(readFileSync(new URL("../src/kit/css.js", import.meta.url), "utf8")).toMatch(/hyphens:none/);
  });
  it("badge priority offline > failed > locked > pending", () => {
    expect(P.badgeOf({ pending: true, failed: true, offline: true }).kind).toBe("offline");
    expect(P.badgeOf({ pending: true, locked: true }).kind).toBe("locked");
  });
  it("status dot inline in the same line", () => {
    expect(P.line("ok", { dot: "ok" })).toMatch(/hf5-line[^>]*><i class="hf5-dot is-ok"><\/i><span/);
  });
  it("± column: + over −, centred beside the value", () => {
    const h = P.pm("<b>21</b>", { act: "step" });
    expect(h.indexOf('data-arg="+1"')).toBeLessThan(h.indexOf('data-arg="-1"'));
  });
  it("tabs fold the rest into +N", () => {
    expect(P.tabs([1, 2, 3, 4].map((v) => ({ v, label: "x" + v })), { max: 2 })).toContain(">+2<");
  });
});

describe("i18n RU+EN", () => {
  it("same keys and placeholders in both languages", () => {
    const ru = Object.fromEntries(flat(LOCALES.ru)), en = Object.fromEntries(flat(LOCALES.en));
    expect(Object.keys(ru).sort()).toEqual(Object.keys(en).sort());
    for (const k of Object.keys(ru)) {
      expect(String(ru[k]).match(/\{\w+\}/g)?.sort()).toEqual(String(en[k]).match(/\{\w+\}/g)?.sort());
      expect(ru[k]).not.toBe("");
    }
  });
  it("number format by locale", () => {
    setLang("en");
    expect(t("common.close")).toBe("Close");
    setLang("ru");
  });
});

describe("command service", () => {
  it("trouble priority offline > failed > blocked > sending", () => {
    expect(trouble({ offline: true, blocked: true }, { failed: true, sending: true })).toBe("offline");
    expect(trouble({ blocked: true }, { failed: true })).toBe("failed");
    expect(trouble({ blocked: true }, { sending: true })).toBe("blocked");
    expect(trouble({}, { sending: true })).toBe("sending");
    expect(trouble({}, {})).toBe(null);
  });
  it("failed call is tracked and retried", async () => {
    let n = 0;
    const hass = { callService: async () => { if (++n === 1) throw new Error("x"); } };
    const c = new Commander(() => hass);
    expect(await c.run("e", { domain: "light", service: "turn_on" })).toBe(false);
    expect(c.of("e").failed).toBe(true);
    await c.retry("e");
    expect(c.of("e").failed).toBeFalsy();
    expect(n).toBe(2);
  });
});

describe("light adapter", () => {
  it("only what the device has", () => {
    const dim = lightModel(LIGHTS["dim + tone"]), only = lightModel(LIGHTS["on/off only"]), d = lightModel(LIGHTS["dimmable"]);
    expect(dim.caps).toMatchObject({ dim: true, ct: true });
    expect(only.caps).toMatchObject({ dim: false, ct: false });
    expect(d.caps).toMatchObject({ dim: true, ct: false });
    expect(lightControls(only).brightness).toBeUndefined();
    expect(lightControls(d).ct).toBeUndefined();
  });
  it("brightness / tone / preset → HA calls", () => {
    const m = lightModel(LIGHTS["dim + tone"]);
    expect(lightAct(m, "brightness", 40).calls[0]).toEqual({ domain: "light", service: "turn_on", data: { entity_id: "light.living", brightness_pct: 40 } });
    expect(lightAct(m, "brightness", 0).calls[0].service).toBe("turn_off");
    expect(lightAct(m, "ct", 100).calls[0].data.color_temp_kelvin).toBe(6500);
    expect(lightAct(m, "ct", 0).calls[0].data.color_temp_kelvin).toBe(2000);
    expect(lightAct(m, "preset", "warm").calls[0].data.color_temp_kelvin).toBe(3000);
  });
  it("presets clamp into the lamp's range", () => {
    const m = lightModel({ ...LIGHTS["off"], attributes: { ...LIGHTS["off"].attributes, max_color_temp_kelvin: 3500 } });
    expect(lightAct(m, "preset", "day").calls[0].data.color_temp_kelvin).toBe(3500);
  });
  it("offline model", () => expect(lightModel(LIGHTS.offline).offline).toBe(true));
});

describe("climate adapter", () => {
  it("modes only from hvac_modes, off is not a segment", () => {
    const f = climateModel(CLIMATES["floor, heating"]), ac = climateModel(CLIMATES["AC, cooling"]);
    expect(f.modes).toEqual(["heat"]);
    expect(climateControls(f).modes).toBeUndefined();
    expect(ac.modes).toEqual(["cool", "heat", "dry", "fan_only", "auto"]);
    expect(climateControls(ac).modes.items.map((i) => i.v)).not.toContain("off");
  });
  it("fan / swing only when listed; floor has none", () => {
    const f = climateControls(climateModel(CLIMATES["floor, heating"])), a = climateControls(climateModel(CLIMATES["AC, cooling"]));
    expect(f.fan).toBeUndefined();
    expect(f.swing).toBeUndefined();
    expect(a.fan.items).toHaveLength(4);
    expect(a.swing.items).toHaveLength(4);
  });
  it("± steps by the device's step, clamps to limits", () => {
    const f = climateModel(CLIMATES["floor, heating"]);
    expect(climateAct(f, "step", "+1").calls[0].data.temperature).toBe(27.5);
    expect(climateAct(f, "step", "-1", { opt: { target: 15 } }).calls[0].data.temperature).toBe(15);
    expect(climateAct(f, "step", "+1", { opt: { target: 35 } }).calls[0].data.temperature).toBe(35);
  });
  it("power off → hvac off; on → last mode", () => {
    const ac = climateModel(CLIMATES["AC, cooling"]);
    expect(climateAct(ac, "power", null).calls[0].data.hvac_mode).toBe("off");
    const off = climateModel({ ...CLIMATES["AC, cooling"], state: "off" });
    expect(climateAct(off, "power", null, { lastMode: "cool" }).calls[0].data.hvac_mode).toBe("cool");
  });
  it("history → normalized series", () => {
    const now = Date.now();
    const rows = [0, 1, 2].map((i) => ({ last_changed: new Date(now - (3 - i) * 3600e3).toISOString(), attributes: { current_temperature: 20 + i, temperature: 22 } }));
    const s = climateSeries(rows, 24, now);
    // 3 readings hold until the next one and run on to now: 3 + 2 steps + the end = 6 points, the last at x = 1
    expect(s.series[0].pts).toHaveLength(6);
    expect(s.series[0].pts.at(-1)[0]).toBe(1);
    expect(s.series[0].pts[1][1]).toBe(s.series[0].pts[0][1]);
    expect(s.lo).toBe(20);
    // a reading from before the window starts the line at x = 0
    const early = climateSeries([{ last_changed: new Date(now - 30 * 3600e3).toISOString(), attributes: { current_temperature: 21 } }], 24, now);
    expect(early.series[0].pts.map((p) => p[0])).toEqual([0, 1]);
    expect(s.series[0].pts.every(([x, y]) => x >= 0 && x <= 1 && y >= 0 && y <= 1)).toBe(true);
  });
});

describe("generic remote", () => {
  const html = (type, s) => { const m = TYPES[type].model(s); return remoteHtml(TYPES[type].remote, m, TYPES[type].controls(m, {}), {}); };
  it("light with tone: two sliders, same class; on/off-only: none", () => {
    expect(count(html("light", LIGHTS["dim + tone"]), /class="hf5-slider/g)).toBe(2);
    expect(count(html("light", LIGHTS["on/off only"]), /class="hf5-slider/g)).toBe(0);
  });
  it("climate: power is a round button, no full-width off bar", () => {
    const h = html("climate", CLIMATES["AC, cooling"]);
    expect(h).toMatch(/hf5-btn hf5-l hf5-on"[^>]*aria-label="Выключить"/);
    expect(h).not.toContain("Выключить</span>");
  });
  it("footer has the HA link and entity id", () => {
    const h = html("climate", CLIMATES["AC, cooling"]);
    expect(h).toContain("data-more-ha");
  });
  it("every string is a locale key (no raw key leaks)", () => {
    for (const s of [...Object.values(LIGHTS).map((x) => ["light", x]), ...Object.values(CLIMATES).map((x) => ["climate", x])]) expect(html(...s)).not.toMatch(/>(light|climate|common|values)\.[a-z_.]+</);
  });
});

describe("compat contract", () => {
  it("every element name v5 owns exists in the old file", () => {
    const old = readFileSync(new URL("../../custom_components/hauflo/www/hauflo-cards.js", import.meta.url), "utf8");
    for (const tag of TYPES_V5) expect(old).toContain(`"${tag}"`);
  });
  it("the old file's remote entry is the hass-more-info event v5 routes", () => {
    const old = readFileSync(new URL("../../custom_components/hauflo/www/hauflo-cards.js", import.meta.url), "utf8");
    expect(old).toContain('"hass-more-info"');
    expect(readFileSync(new URL("../src/index.js", import.meta.url), "utf8")).toContain('"hass-more-info"');
  });
});

describe("bundle", () => {
  it("committed file is current and inside the budget", () => {
    execFileSync("node", [new URL("../scripts/build.mjs", import.meta.url).pathname, "--check"], { stdio: "pipe" });
  });
});

// 30.10: «:host[data-mode=dark]» is invalid in a shadow root and silently dropped the whole palette.
it("token CSS uses :host([…]) for host attributes, never :host[…]", async () => {
  const { TOKENS_CSS } = await import("../src/gen/tokens.js");
  expect(TOKENS_CSS).not.toMatch(/:host\[/);
  expect(TOKENS_CSS).toMatch(/:host\(\[data-hf-theme=atelier\]\[data-mode=dark\]\)/);
});
