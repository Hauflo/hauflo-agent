// hauflo-media · what plays, its cover, ⏮ ⏯ ⏭, volume by a slider (the old card's keys: entity, name).
// Header canon: [cover] caps «name · source» / [track][artist]; under it the transport row and the volume slider.
// Off / standby / unavailable: the same header with the word and ⏻ (the card keeps its one-line height).
import { t, num } from "../core/i18n.js";
import { head, btn, slider, ico, msg } from "../kit/primitives.js";
import { glyph } from "../core/icons.js";
import { esc } from "../core/dom.js";
import { OFFLINE, reading } from "../adapters/common.js";
import { CANON } from "../gen/canon.js";

const M = CANON.cards.media;
const OFF = new Set(["off", "standby"]);

export const MEDIA = {
  key: "media",
  watch: (c) => [c.entity].filter(Boolean),
  grid: () => ({ columns: 12, rows: "auto", min_columns: 3 }),
  stub: (h) => {
    const l = Object.values(h?.states || {}).filter((s) => s.entity_id.startsWith("media_player."));
    return { entity: (l.find((s) => s.state === "playing") || l[0])?.entity_id || "" };
  },
  render(card, v) {
    const c = card.config;
    const s = c.entity && card.hass.states[c.entity];
    if (!s) return `${head({ title: t("media.title") })}${msg(t("media.none"), { icon: "speaker" })}`;
    const a = s.attributes || {};
    const title = c.name || a.friendly_name || c.entity;
    if (OFF.has(s.state) || OFFLINE.has(s.state) || s.state === "unavailable") {
      const gone = OFFLINE.has(s.state);
      const S = v === "S"; // 120 px: the word stays in the label, the dimmed icon and ⏻ say it
      return head({ title, words: S ? "" : t(gone ? "common.offline" : "media.off"), icon: S ? "" : ico("speaker", {}), ctl: gone ? "" : btn("power", { l: !S, label: t("common.turn_on"), attr: { "data-act": "on" } }) });
    }
    const playing = s.state === "playing";
    const pic = a.entity_picture;
    const art = pic ? `<span class="hf5-ma" style="background-image:url('${esc(pic)}')" aria-hidden="true"></span>` : `<span class="hf5-ma">${glyph("speaker")}</span>`;
    const vol = reading(card.opt[c.entity]?.pct != null ? card.opt[c.entity].pct / 100 : a.volume_level, 0, 1);
    const p = vol === undefined ? undefined : Math.round(vol * 100);
    const top = head({
      title: `${title}${a.source ? ` · ${a.source}` : ""}`,
      words: a.media_title || t(playing ? "media.playing" : "media.paused"),
      sub: a.media_artist || a.media_album_name || a.app_name || "",
      icon: v === "S" ? "" : art,
    });
    const ctl = `<div class="hf5-mc">${v === "S" ? "" : btn("prev", { label: t("media.prev"), attr: { "data-act": "prev" } })}${btn(playing ? "pause" : "play", { l: true, on: true, role: M.role, label: t(playing ? "media.pause" : "media.play"), attr: { "data-act": "pp" } })}${v === "S" ? "" : btn("next", { label: t("media.next"), attr: { "data-act": "next" } })}</div>`;
    const volume =
      p === undefined
        ? ""
        : `<div class="hf5-mv">${v === "S" ? "" : glyph("speaker")}${slider(p, { act: "vol", label: t("media.volume"), role: M.role, valuetext: `${p} %`, attr: { "data-id": c.entity } })}${v === "S" ? "" : `<span class="hf5-mp">${num(p, 0)} %</span>`}</div>`;
    return `${top}${ctl}${volume}`;
  },
  live(card, el, v) {
    const b = el.parentElement?.querySelector(".hf5-mp");
    if (b) b.textContent = `${Math.round(v)} %`;
  },
  act(card, name, arg) {
    const id = card.config.entity;
    const one = (service, data = {}) => [{ domain: "media_player", service, data: { entity_id: id, ...data } }];
    switch (name) {
      case "on": return { key: id, calls: one("turn_on") };
      case "prev": return { key: id, calls: one("media_previous_track") };
      case "next": return { key: id, calls: one("media_next_track") };
      case "pp": return { key: id, calls: one("media_play_pause") };
      case "vol": {
        const p = Math.round(+arg);
        return { key: id, calls: one("volume_set", { volume_level: p / 100 }), opt: { [id]: { pct: p } } };
      }
    }
    return null;
  },
  css: `.hf5-ma{flex:none;width:var(--hf-ctl-l);height:var(--hf-ctl-l);border-radius:var(--hf-r-ctl);background:var(--hf-surface-2) center/cover;display:grid;place-items:center;color:var(--hf-ink-3)}
.hf5-ma .hf5-g{width:var(--hf-icon-l);height:var(--hf-icon-l)}
.hf5-mc{display:flex;justify-content:center;align-items:center;gap:var(--hf-sp-4);margin-top:var(--hf-sp-3)}
.hf5-mv{display:flex;align-items:center;gap:var(--hf-sp-3);margin-top:var(--hf-sp-3);color:var(--hf-ink-2)}.hf5-mv .hf5-g{width:var(--hf-icon-s);height:var(--hf-icon-s);flex:none}
.hf5-mv .hf5-slider{flex:1}.hf5-mp{flex:none;min-width:calc(var(--hf-hit) * 1);text-align:right;font-size:var(--hf-fs-s);font-weight:var(--hf-w-med);color:var(--hf-ink)}`,
};
