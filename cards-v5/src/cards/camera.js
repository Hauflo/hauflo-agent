// hauflo-camera · the picture, refreshed; a tap opens the live view (the old card's keys: entity, name, refresh seconds).
// Photo plate (the room-photo shade tokens): name with the live dot top-left, the state word bottom-left. Fixed aspect: the card never jumps.
import { t, clock } from "../core/i18n.js";
import { head, msg } from "../kit/primitives.js";
import { glyph } from "../core/icons.js";
import { esc } from "../core/dom.js";
import { OFFLINE } from "../adapters/common.js";
import { CANON } from "../gen/canon.js";

const C = CANON.cards.camera;

export const CAMERA = {
  key: "camera",
  watch: (c) => [c.entity].filter(Boolean),
  grid: () => ({ columns: 12, rows: "auto", min_columns: 3 }),
  stub: (h) => ({ entity: Object.keys(h?.states || {}).find((x) => x.startsWith("camera.")) || "", refresh: C.refresh_default }),
  render(card) {
    const c = card.config;
    const s = c.entity && card.hass.states[c.entity];
    if (!s) return `${head({ title: t("camera.title") })}${msg(t("camera.none"), { icon: "camera" })}`;
    card.tick("snap", Math.max(+c.refresh || C.refresh_default, C.refresh_min) * 1000);
    const a = s.attributes || {};
    const gone = OFFLINE.has(s.state);
    const nm = c.name || a.friendly_name || c.entity;
    const word = gone ? t("common.offline") : ["recording", "streaming"].includes(s.state) ? t(`camera.${s.state}`) : t("camera.updated", { t: clock(new Date()) });
    const pic = !gone && a.entity_picture ? `${a.entity_picture}${a.entity_picture.includes("?") ? "&" : "?"}t=${Date.now()}` : "";
    return `<div class="hf5-cam${pic ? "" : " is-none"}" data-tap="open" data-id="${esc(c.entity)}" role="button" tabindex="0" aria-label="${esc(`${nm}, ${word}`)}"${pic ? ` style="background-image:url('${esc(pic)}')"` : ""}>${pic ? "" : glyph("camera")}<div class="hf5-cn"><i class="hf5-dot${gone ? "" : " is-alarm"}"></i>${esc(nm)}</div><div class="hf5-cs">${esc(word)}</div></div>`;
  },
  act: (card) => ({ more: card.config.entity }),
  css: `.hf5-cam{position:relative;aspect-ratio:${CANON.cards.camera.aspect};border-radius:var(--hf-r-ctl);background:var(--hf-surface-2) center/cover;cursor:pointer;overflow:hidden;color:var(--hf-ink-on-media);text-shadow:var(--hf-photo-text-shadow)}
.hf5-cam::after{content:"";position:absolute;inset:0;background:var(--hf-photo-shade);pointer-events:none}
.hf5-cam.is-none{color:var(--hf-ink-2);text-shadow:none;display:grid;place-items:center}.hf5-cam.is-none::after{display:none}.hf5-cam.is-none>.hf5-g{width:var(--hf-icon-l);height:var(--hf-icon-l)}
.hf5-cn,.hf5-cs{position:absolute;left:var(--hf-sp-4);z-index:1;font-size:var(--hf-fs-s);display:flex;align-items:center;gap:var(--hf-sp-2)}
.hf5-cn{top:var(--hf-sp-3);font-weight:var(--hf-w-med)}.hf5-cs{bottom:var(--hf-sp-3);font-size:var(--hf-fs-xs)}
.hf5-cn .hf5-dot{box-shadow:0 0 var(--hf-sp-2) var(--hf5-dot)}`,
};
