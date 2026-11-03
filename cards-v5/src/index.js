// hauflo-cards-v5.js · entry. Loads the previous cards file (same folder, same ?v=) so every type v5 does not draw yet
// keeps working exactly as before; then routes the remotes of the types v5 draws to the new remote. Rollback = the agent
// option «Вид карточек: текущий» (the old file is never touched).
import { openRemote, isDrawn } from "./remote/element.js";
import "./compat/contract.js";
import { defineCards } from "./cards/index.js";

defineCards(); // before the previous file: v5 takes over its tags one by one

const OLD = new URL("hauflo-cards.js" + new URL(import.meta.url).search, import.meta.url).href;

const hassOf = () => document.querySelector("home-assistant")?.hass;

// The old cards ask HA for its more-info dialog; for light and climate the new remote answers instead.
window.addEventListener(
  "hass-more-info",
  (ev) => {
    const id = ev.detail?.entityId;
    if (ev.detail?.hf5 || !id || !isDrawn(id)) return;
    const hass = hassOf();
    if (!hass?.states?.[id]) return;
    ev.stopImmediatePropagation();
    ev.preventDefault();
    openRemote(hass, id, ev.detail.hf5go ? { go: ev.detail.hf5go } : {});
  },
  true,
);

// Keep an open remote's hass fresh: HA sets .hass on its root on every state change.
setInterval(() => {
  const open = document.querySelectorAll("hauflo-remote-v5");
  const h = open.length && hassOf();
  if (h) open.forEach((r) => r._hass !== h && (r.hass = h));
}, 250);

import(/* webpackIgnore: true */ OLD).catch((e) => console.error("hauflo-cards-v5: previous cards file failed to load", e));

// The old cards' own detail popup (long-press / right-click on a row) asks here first.
window.__haufloRemote = (hass, id) => {
  if (!id || !isDrawn(id) || !hass?.states?.[id]) return false;
  openRemote(hass, id);
  return true;
};
window.__haufloCardsV5 = { openRemote };
