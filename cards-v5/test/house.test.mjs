import { describe, it, expect, beforeEach } from "vitest";
import { blockTitle, orderSlot, orderKey, orderSort, ordered, sceneWhere, climateCompact, _resetHouse } from "../src/core/house.js";

const tick = () => new Promise((r) => setTimeout(r, 0));
/** A stand-in card: config + hass with the agent's answers; counts redraws. */
function fake(cfg, { titles = {}, order = {}, view = {}, fail = false } = {}) {
  const card = {
    _cfg: { type: "custom:hauflo-lights", ...cfg },
    isConnected: true,
    draws: 0,
    render() {
      this.draws++;
    },
    hass: {
      callApi: async (m, p) => {
        if (fail) throw new Error("404");
        return p === "hauflo/titles" ? { titles } : { order };
      },
      callWS: async () => (fail ? Promise.reject(new Error("x")) : { value: view }),
    },
  };
  Object.defineProperty(card, "config", { get: () => ordered(card) });
  return card;
}

beforeEach(() => _resetHouse());

describe("house layer", () => {
  it("titles: client name > config title (own block only) > card word; answer arrives → redraw", async () => {
    const c = fake({ block: "lights", title: "Свет кухни" }, { titles: { lights: "Мой свет" } });
    expect(blockTitle(c, "Свет")).toBe("Свет кухни"); // before the agent answers
    await tick();
    expect(c.draws).toBeGreaterThan(0);
    expect(blockTitle(c, "Свет")).toBe("Мой свет");
    expect(blockTitle(c, "Сценарии света", "light_scenes")).toBe("Сценарии света"); // other block: not the card's title
  });
  it("titles: older agent (404) → integrator's title, no crash", async () => {
    const c = fake({ block: "lights", title: "Свет" }, { fail: true });
    blockTitle(c, "x");
    await tick();
    expect(blockTitle(c, "x")).toBe("Свет");
    expect(blockTitle(fake({}), "Слово")).toBe("Слово");
  });
  it("slot and key match the agent (order.py slot) and only on a Hauflo dashboard view", () => {
    expect(orderSlot({ type: "custom:hauflo-lights", title: "Свет" })).toBe("lights::Свет");
    expect(orderSlot({ type: "custom:hauflo-sliders", entities: ["cover.a"] })).toBe("sliders:cover:");
    expect(orderSlot({ type: "custom:hauflo-climate", zones: [{ entity: "climate.a" }] })).toBe("climate:zones:");
    expect(orderSlot({ type: "custom:hauflo-room", path: "kitchen" })).toBe("room:tiles");
    const c = fake({ title: "Свет" });
    expect(orderKey(c, "/hauflo-home/kitchen")).toBe("hauflo-home|kitchen|lights::Свет");
    expect(orderKey(c, "/hauflo-home-copy-2/kitchen")).toBe("");
    expect(orderKey(c, "/lovelace/0")).toBe("");
    expect(orderKey(c, "/hauflo-home")).toBe("");
    expect(orderKey(fake({ type: "custom:hauflo-weather" }), "/hauflo-home/a")).toBe("");
  });
  it("sort: kept first as kept, new after as they came, gone dropped, objects by entity", () => {
    expect(orderSort(["b", "x", "a"], ["a", "b", "c"])).toEqual(["b", "a", "c"]);
    expect(orderSort(["b"], [{ entity: "a" }, { entity: "b" }]).map((x) => x.entity)).toEqual(["b", "a"]);
    expect(orderSort([], ["a"])).toEqual(["a"]);
    expect(orderSort(undefined, ["a"])).toEqual(["a"]);
  });
  it("view: person's choice > card config; blinds fall back to the shared choice; popup never compact", async () => {
    const c = fake({ scene_layout: "blocks", cover_scene_layout: "top" }, { view: { scenes: "top" } });
    expect(sceneWhere(c, "light")).toBe("blocks");
    expect(sceneWhere(c, "cover")).toBe("top");
    await tick();
    expect(sceneWhere(c, "light")).toBe("top");
    expect(sceneWhere(c, "cover")).toBe("top");
    _resetHouse();
    const k = fake({ type: "custom:hauflo-climate", size_class: "floor", compact: false }, { view: { floor: "compact", climate: "large" } });
    climateCompact(k);
    await tick();
    expect(climateCompact(k)).toBe(true);
    k._cfg = { ...k._cfg, size_class: "ac", compact: true };
    expect(climateCompact(k)).toBe(false); // climate pref = large
    k._cfg = { ...k._cfg, popup: true, size_class: "floor" };
    expect(climateCompact(k)).toBe(false);
    k._cfg = { type: "custom:hauflo-climate", compact: true };
    expect(climateCompact(k)).toBe(true); // no size class → the card's own
  });
});
