// The v5 card registry. Each def takes over the OLD tag name (same config keys → the generated dashboards keep working);
// the previous cards file loads after and skips tags already defined, so it still draws every type v5 does not.
import { HfCard } from "../card/element.js";
import { LIGHTS } from "./lights.js";
import { CLIMATE } from "./climate.js";
import { SCENES } from "./scenes.js";
import { ALERT } from "./alert.js";
import { COVERS } from "./covers.js";
import { DEVICES } from "./devices.js";
import { ROOM_CARD } from "./room.js";
import { ACS } from "./acs.js";
import { STATUS } from "./status.js";
import { HERO } from "./hero.js";
import { SLIDERS } from "./sliders.js";
import { WEATHER } from "./weather.js";
import { AIR } from "./air.js";
import { GRAPH } from "./graph.js";
import { MEDIA } from "./media.js";
import { CAMERA } from "./camera.js";
import { AUTO } from "./auto.js";
import { LEAKS } from "./leaks.js";
import { SECURITY } from "./security.js";
import { AREA } from "./area.js";
import { ENTITY } from "./entity.js";
import { t, setLang } from "../core/i18n.js";

const DEFS = { "hauflo-lights": LIGHTS, "hauflo-climate": CLIMATE, "hauflo-scenes": SCENES, "hauflo-alert": ALERT, "hauflo-covers": COVERS, "hauflo-devices": DEVICES, "hauflo-room": ROOM_CARD, "hauflo-acs": ACS, "hauflo-status": STATUS, "hauflo-hero": HERO, "hauflo-sliders": SLIDERS, "hauflo-weather": WEATHER, "hauflo-air": AIR, "hauflo-graph": GRAPH, "hauflo-media": MEDIA, "hauflo-camera": CAMERA, "hauflo-auto": AUTO, "hauflo-leaks": LEAKS, "hauflo-security": SECURITY, "hauflo-area": AREA, "hauflo-entity": ENTITY };
export const V5_TAGS = Object.keys(DEFS);

export function defineCards() {
  const lang = String(navigator.language || "ru").toLowerCase().startsWith("ru") ? "ru" : "en";
  for (const [tag, def] of Object.entries(DEFS)) {
    if (customElements.get(tag)) continue;
    customElements.define(
      tag,
      class extends HfCard {
        static def = def;
        // the editor stays the previous file's one (same config keys); it is defined by the time HA asks
        static getConfigElement() {
          const e = document.createElement("hauflo-card-editor");
          e.tag = tag;
          return e;
        }
        static getStubConfig(hass) {
          return def.stub?.(hass) ?? {};
        }
      },
    );
    setLang(lang);
    (window.customCards ||= []).push({ type: tag, name: t(`cards.${def.key}.name`), description: t(`cards.${def.key}.desc`), preview: true });
  }
}
