import { describe, it, expect, beforeEach } from "vitest";
import { sceneGroups, sceneList, sceneEdit, sceneSource, sceneRoom, captureList, famUse, _resetScenes } from "../src/core/scenes.js";
import { _resetHouse } from "../src/core/house.js";
import { setLang } from "../src/core/i18n.js";

const tick = () => new Promise((r) => setTimeout(r, 0));
const st = (id, attributes = {}, state = "scening") => ({ entity_id: id, state, attributes });
/** A house: the integrator's KNX + HA scenes and a family one in room «r1», lights in its area. */
function house(api = { hidden: [], knx: { "scene.k1": 1 }, names: {} }) {
  const states = {
    "scene.k1": st("scene.k1", { friendly_name: "Вечер" }),
    "scene.h1": st("scene.h1", { friendly_name: "Кино", id: "hauflo_r1_l_1", entity_id: ["light.a", "light.b"] }),
    "scene.f1": st("scene.f1", { friendly_name: "Ночь", id: "hauflo_fam_r1_l_100" }),
    "scene.f2": st("scene.f2", { friendly_name: "Утро", id: "hauflo_fam_r1_c_100" }),
    "scene.x": st("scene.x", { friendly_name: "Чужая", id: "other" }),
    "light.a": st("light.a", {}, "on"),
    "light.b": st("light.b", {}, "off"),
    "light.g": st("light.g", { entity_id: ["light.a"] }, "on"),
  };
  const entities = { "scene.k1": { platform: "knx" }, "scene.h1": { area_id: "kitchen" }, "light.a": { area_id: "kitchen" }, "light.b": { area_id: "kitchen" }, "light.g": { area_id: "kitchen" } };
  const card = { isConnected: false, render() {}, hass: { states, entities, devices: {}, callApi: async () => api } };
  return card;
}

beforeEach(() => (_resetScenes(), _resetHouse(), setLang("ru")));

describe("scenes", () => {
  it("source: KNX by platform, family by id, else the integrator's HA", () => {
    const { hass: h } = house();
    expect(["scene.k1", "scene.h1", "scene.f1", "scene.x"].map((id) => sceneSource(h, id))).toEqual(["knx", "ha", "fam", "ha"]);
  });
  it("list: KNX → HA → family of the room and kind; hidden and missing dropped", async () => {
    const card = house({ hidden: ["scene.x"], knx: {}, names: {} });
    famUse(card);
    await tick();
    const ids = sceneList(card, { scenes: [{ entity: "scene.x" }, { entity: "scene.h1" }, { entity: "scene.gone" }, { entity: "scene.k1" }], kind: "light", room: "r1" }).map((x) => x.entity);
    expect(ids).toEqual(["scene.k1", "scene.h1", "scene.f1"]);
    expect(sceneList(card, { scenes: [], kind: "cover", room: "r1" }).map((x) => x.entity)).toEqual(["scene.f2"]);
    expect(sceneList(card, { scenes: [], kind: "mixed", room: "r1" })).toEqual([]); // no family scenes in a mixed list
  });
  it("edit: learnable KNX only when the agent says so; Hauflo's need a known room; others none", async () => {
    const card = house();
    const h = card.hass;
    expect(sceneEdit(h, "scene.k1", "r1")).toBe(""); // agent not asked yet
    famUse(card);
    await tick();
    expect(sceneEdit(h, "scene.k1", "r1")).toBe("knx");
    expect(sceneEdit(h, "scene.h1", "r1")).toBe("ha");
    expect(sceneEdit(h, "scene.f1", "r1")).toBe("fam");
    expect(sceneEdit(h, "scene.h1", undefined)).toBe("");
    expect(sceneEdit(h, "scene.x", "r1")).toBe("");
  });
  it("room: config > the scenes' own id > the area of the lights", () => {
    const { hass: h } = house();
    expect(sceneRoom(h, "cfg", [{ entity: "scene.h1" }])).toBe("cfg");
    expect(sceneRoom(h, null, [{ entity: "scene.k1" }, { entity: "scene.h1" }])).toBe("r1");
    expect(sceneRoom(h, null, [], ["light.b"])).toBe("r1");
    expect(sceneRoom(h, null, [{ entity: "scene.k1" }], [])).toBe(undefined);
  });
  it("capture: the block's own lights first; else what its scenes hold + the area, never a group", () => {
    const { hass: h } = house();
    expect(captureList(h, { entities: ["light.b", "cover.x"] }, "light", "r1")).toEqual(["light.b"]);
    expect(captureList(h, { scenes: [{ entity: "scene.h1" }] }, "light", "r1").sort()).toEqual(["light.a", "light.b"]);
  });
  it("groups: KNX series with headers + «Мои сцены»; blinds by type when two or more; else one", () => {
    const { hass: h } = house();
    const s = sceneGroups(h, [{ entity: "a", series: "Гостиная", series_head: false }, { entity: "b", series: "Кухня" }, { entity: "c" }], "light");
    expect(s.map((g) => [g.label, g.items.map((i) => i.x.entity)])).toEqual([["", ["a"]], ["Кухня", ["b"]], ["Мои сцены", ["c"]]]);
    const n = (name) => ({ entity: name, name });
    const b = sceneGroups(h, [n("Открыть шторы"), n("Закрыть шторы"), n("Открыть тюль"), n("Утро")], "cover");
    expect(b.map((g) => [g.label, g.items.map((i) => i.short || i.x.entity)])).toEqual([["", ["Утро"]], ["Шторы", ["Открыть", "Закрыть"]], ["Тюль", ["Открыть"]]]);
    expect(sceneGroups(h, [n("Открыть шторы"), n("Закрыть шторы")], "cover")).toHaveLength(1); // one type: plain list
  });
});
