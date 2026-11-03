// Layer 2 · the ONE stylesheet of every primitive (ported from the canon kit canon/v5.js, CANON.md §16).
// Only --hf-* tokens (gen/tokens.js) — no literal colour, size or timing (check-hardcode lints this folder).
// Unitless ratios (line-height, flex) and percentages of the element itself are geometry, not looks.
export const KIT_CSS = `
:host,.hf-host{font-family:var(--hf-font);color:var(--hf-ink);-webkit-font-smoothing:antialiased}
.hf5-g{display:block;fill:currentColor}
.hf5-g>.is-l{fill:none;stroke:currentColor;stroke-width:var(--hf-icon-stroke);stroke-linecap:round;stroke-linejoin:round}
/* R2 · one button system: round icon button + pill action button; 2 heights (ctl cards, ctl-l remote/panel); one style */
.hf5-btn,.hf5-pill{-webkit-tap-highlight-color:transparent;position:relative;overflow:hidden;display:inline-flex;align-items:center;justify-content:center;gap:var(--hf-sp-2);
  height:var(--hf-ctl);min-width:var(--hf-ctl);padding:0;border:0;outline:0;box-shadow:none;border-radius:var(--hf-r-pill);isolation:isolate;
  background:var(--hf-surface-glass);color:var(--hf-ink);font:inherit;font-size:var(--hf-fs-s);font-weight:var(--hf-w-med);cursor:pointer;
  transition:background var(--hf-dur),color var(--hf-dur),transform var(--hf-dur);flex:none;box-sizing:border-box}
.hf5-btn{width:var(--hf-ctl);box-shadow:var(--hf-edge),inset 0 0 0 var(--hf-line-w) var(--hf-hair)}
/* v1 look (owner 03.11): a pill is an outlined chip — no fill, the line ring, ink-2 text, 44 high, the control radius */
.hf5-pill{padding:0 var(--hf-sp-4);min-width:calc(var(--hf-ctl) * 2);height:var(--hf-hit);border-radius:var(--hf-r-ctl);background:none;color:var(--hf-ink-2);box-shadow:inset 0 0 0 var(--hf-line-w) var(--hf-line)}
.hf5-l .hf5-btn,.hf5-btn.hf5-l{height:var(--hf-ctl-l);width:var(--hf-ctl-l);min-width:var(--hf-ctl-l)}
.hf5-l .hf5-pill,.hf5-pill.hf5-l{height:var(--hf-ctl-l)}
.hf5-btn:active,.hf5-pill:active{transform:scale(var(--hf-press-scale))}
.hf5-btn:focus-visible,.hf5-pill:focus-visible,.hf5-more:focus-visible,.hf5-slider:focus-visible,.hf5-x:focus-visible,.hf5-link:focus-visible{box-shadow:0 0 0 var(--hf-focus-w) var(--hf-focus)}
.hf5-btn[disabled],.hf5-pill[disabled]{opacity:var(--hf-off-opacity);pointer-events:none}
.hf5-btn.is-off,.hf5-pill.is-off{opacity:var(--hf-off-opacity)}
.hf5-btn .hf5-g{width:var(--hf-icon-m);height:var(--hf-icon-m)}
.hf5-l .hf5-btn .hf5-g,.hf5-btn.hf5-l .hf5-g{width:var(--hf-icon-l);height:var(--hf-icon-l)}
.hf5-pill .hf5-g{width:var(--hf-icon-s);height:var(--hf-icon-s);flex:none}
.hf5-pill>span{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0}
.hf5-tag{position:absolute;top:calc(var(--hf-sp-1) / 2);right:var(--hf-sp-2);font-style:normal;font-size:var(--hf-fs-caps);font-weight:var(--hf-w-med);letter-spacing:var(--hf-caps-track);line-height:1;color:var(--hf-ink-3);pointer-events:none}
/* R14 · selected = the role's -text fill under ink-on-fill */
.hf5-on{background:var(--hf5-role-text,var(--hf-accent-text));color:var(--hf-ink-on-fill);box-shadow:none}
/* round on (power, «all»): v1's lit outline — the role's tint, its colour ring and glyph, not a solid disc */
.hf5-btn.hf5-on{background:var(--hf5-role-tint,var(--hf-accent-tint));color:var(--hf5-role-text,var(--hf-accent-text));box-shadow:inset 0 0 0 calc(var(--hf-line-w) * 1.5) var(--hf5-role,var(--hf-accent))}
.hf5-seg{display:flex;gap:var(--hf-sp-2);width:100%}
.hf5-seg>.hf5-pill{flex:1 1 0;min-width:0;padding:0 var(--hf-sp-2)}
.hf5-seg.is-icons>.hf5-pill{min-width:var(--hf-ctl)}
/* R3 · ± column right of the value, + on top, centred on the number line */
.hf5-pm{display:flex;flex-direction:column;gap:var(--hf-sp-2);align-self:center;flex:none}
.hf5-setp{display:flex;align-items:center;gap:var(--hf-sp-3);min-width:0}
.hf5-setp>.hf5-val{flex:1 1 auto;min-width:0}
/* the number scale 22/32/48/76/104, unit = fs-unit-k × number at the cap top, no gap; secondary value right next to it */
.hf5-num{display:flex;align-items:flex-start;gap:var(--hf-sp-2);min-width:0;font-family:var(--hf-font-num);font-variant-numeric:tabular-nums;line-height:1}
.hf5-num>b{font-weight:var(--hf5-numw,var(--hf-w-num));font-size:var(--hf5-num,var(--hf-fs-num-xl));letter-spacing:-0.02em}
.hf5-num>b>sup{font-size:calc(var(--hf5-num,var(--hf-fs-num-xl)) * var(--hf-fs-unit-k));vertical-align:top;line-height:1;margin-left:0;position:relative;top:0.15em}
.hf5-num>small{font-size:var(--hf-fs-m);color:var(--hf-ink-2);font-weight:var(--hf-w-med);padding-top:0.5em;white-space:nowrap}
.hf5-num.is-off>b{color:var(--hf-ink-3)}
/* R16 · one icon container: circle ctl-s, tint by role only when active */
.hf5-ico{position:relative;display:inline-flex;align-items:center;justify-content:center;flex:none;width:var(--hf-ctl-s);height:var(--hf-ctl-s);
  border-radius:var(--hf-r-pill);background:var(--hf5-tint,var(--hf-surface-2));color:var(--hf5-glyph,var(--hf-ink-2))}
.hf5-ico>.hf5-g{width:var(--hf-icon-m);height:var(--hf-icon-m)}
.hf5-ico.is-on{--hf5-tint:var(--hf5-role-tint,var(--hf-accent-tint));--hf5-glyph:var(--hf5-role,var(--hf-accent))}
.hf5-ico.is-warn{--hf5-tint:var(--hf-state-warn-tint);--hf5-glyph:var(--hf-state-warn)}
.hf5-ico.is-alarm{--hf5-tint:var(--hf-state-alarm-tint);--hf5-glyph:var(--hf-state-alarm)}
.hf5-ico.is-dim{opacity:var(--hf-dim)}
.hf5-ico>.hf5-dot{position:absolute;right:0;bottom:0;box-shadow:0 0 0 var(--hf-focus-w) var(--hf-surface)}
/* K11 · one badge slot, top-right of the icon container: offline > failed > locked > pending */
.hf5-badge{position:absolute;top:calc(var(--hf-sp-1) * -1);right:calc(var(--hf-sp-1) * -1);width:var(--hf-icon-s);height:var(--hf-icon-s);
  border-radius:var(--hf-r-pill);background:var(--hf-surface-sheet);color:var(--hf-ink-2);display:flex;align-items:center;justify-content:center;box-shadow:0 0 0 var(--hf-line-w) var(--hf-hair)}
.hf5-badge>.hf5-g{width:calc(var(--hf-icon-s) * 0.66);height:calc(var(--hf-icon-s) * 0.66)}
.hf5-badge.is-failed{color:var(--hf-state-warn-text)}
.hf5-badge.is-pending::after{content:"";width:calc(var(--hf-icon-s) * 0.5);height:calc(var(--hf-icon-s) * 0.5);border-radius:50%;box-shadow:inset 0 0 0 var(--hf-focus-w) var(--hf-accent);animation:hf5-spin var(--hf-dur-slow) linear infinite;clip-path:polygon(50% 50%,0 0,100% 0,100% 100%)}
@keyframes hf5-spin{to{transform:rotate(1turn)}}
/* R11 · status line: ONE line, the dot inline at its start, centred on the text */
.hf5-line{display:flex;align-items:center;gap:calc(var(--hf-sp-1) * 1.5);min-width:0;line-height:1.3;font-size:var(--hf-fs-s);color:var(--hf-ink-2);white-space:nowrap;overflow:hidden}
.hf5-line>.hf5-t{min-width:0;overflow:hidden;text-overflow:ellipsis}
.hf5-line.is-bare>.hf5-t{display:none}
.hf5-line.is-gone>.hf5-t{visibility:hidden}
.hf5-line.is-warn{color:var(--hf-state-warn-text)}
.hf5-dot{width:var(--hf-sp-2);height:var(--hf-sp-2);border-radius:50%;flex:none;background:var(--hf5-dot,var(--hf-ink-3))}
.hf5-dot.is-ok{--hf5-dot:var(--hf-state-ok)}.hf5-dot.is-warn{--hf5-dot:var(--hf-state-warn)}.hf5-dot.is-alarm{--hf5-dot:var(--hf-state-alarm)}
.hf5-dot.is-role{--hf5-dot:var(--hf5-role,var(--hf-accent))}
/* names: ≤ 2 lines, only between words, then a smaller step, then ellipsis */
.hf5-name{display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:2;line-clamp:2;overflow:hidden;min-width:0;
  hyphens:none;-webkit-hyphens:none;overflow-wrap:normal;word-break:normal;text-wrap:balance;font-size:var(--hf-fs-m);font-weight:var(--hf-w-med);line-height:1.25}
.hf5-name.is-s{font-size:var(--hf-fs-s)}
.hf5-cap.is-1{white-space:nowrap;text-overflow:ellipsis}
.hf5-name.is-1{display:block;white-space:nowrap;text-overflow:ellipsis}
.hf5-desc{font-size:var(--hf-fs-xs);color:var(--hf-ink-2);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
/* ONE slider: horizontal, surface-2 track, role fill from the start, thin handle inside the fill; track variant = the scale itself */
.hf5-slider{position:relative;display:block;height:var(--hf-ctl);border-radius:var(--hf-r-ctl);background:var(--hf-surface-2);overflow:hidden;
  touch-action:pan-y;cursor:ew-resize;user-select:none;-webkit-user-select:none;outline:0}
.hf5-l .hf5-slider,.hf5-slider.hf5-l{height:var(--hf-ctl-l)}
.hf5-slider>i{position:absolute;inset:0 auto 0 0;width:calc(var(--v,50) * 1%);background:var(--hf5-fill,var(--hf5-role,var(--hf-accent)))}
.hf5-slider>i::after{content:"";position:absolute;right:var(--hf-sp-2);top:25%;bottom:25%;width:var(--hf-sp-1);border-radius:var(--hf-r-pill);background:var(--hf-ink-on-fill);opacity:0.9}
.hf5-slider.is-track{background:var(--hf5-track)}.hf5-slider.is-track>i{background:none}
.hf5-slider.is-track>i::after{right:calc(var(--hf-sp-1) / -2);opacity:1;background:var(--hf-surface-sheet);box-shadow:0 0 0 var(--hf-line-w) var(--hf-control-edge)}
.hf5-slider.is-off>i{background:var(--hf-ink-3)}
.hf5-slider[aria-disabled=true]{pointer-events:none;opacity:var(--hf-off-opacity)}
/* hold-to-confirm: the fill grows UNDER the label for confirm_by_hold; release early = nothing */
.hf5-hold::before{content:"";position:absolute;inset:0;z-index:-1;transform-origin:left center;transform:scaleX(0);background:var(--hf5-role,var(--hf-state-warn));opacity:var(--hf-hold-role);border-radius:inherit;pointer-events:none}
.hf5-hold.is-holding::before{transform:scaleX(1);transition:transform var(--hf5-hold-ms) linear}
.hf5-hold.is-done::before{transform:scaleX(1);opacity:var(--hf-hold-done)}
.hf5-hold.is-frozen::before{transform:scaleX(0.6);transition:none}
.hf5-hold>*{position:relative}
/* hold on a tile body / pill = remote: neutral fill under the label, every size */
.hf5-tilehold{isolation:isolate;position:relative}
.hf5-tilehold::after{content:"";position:absolute;inset:0;border-radius:inherit;background:var(--hf-ink);opacity:0;transform-origin:left center;transform:scaleX(0);pointer-events:none;z-index:-1}
.hf5-tilehold.is-holding::after{opacity:var(--hf-hold-ink);transform:scaleX(1);transition:transform var(--hf5-tilehold-ms) linear}
.hf5-tilehold.is-frozen::after{opacity:var(--hf-hold-ink);transform:scaleX(0.6)}
/* ⋮ one glyph and box (M/L header, remote) */
.hf5-more,.hf5-x{-webkit-tap-highlight-color:transparent;display:inline-flex;align-items:center;justify-content:center;flex:none;width:var(--hf-hit);height:var(--hf-hit);
  padding:0;border:0;background:none;color:var(--hf-ink-2);border-radius:var(--hf-r-pill);cursor:pointer;outline:0}
.hf5-more>.hf5-g,.hf5-x>.hf5-g{width:var(--hf-icon-m);height:var(--hf-icon-m)}
.hf5-x{background:var(--hf-surface-2);width:var(--hf-ctl-s);height:var(--hf-ctl-s)}
/* tabs with «+N» */
.hf5-tabs{display:flex;gap:var(--hf-sp-1);overflow:hidden;min-width:0}
.hf5-tab{flex:none;height:var(--hf-ctl-s);padding:0 var(--hf-sp-3);border:0;border-radius:var(--hf-r-pill);background:none;color:var(--hf-ink-2);font:inherit;font-size:var(--hf-fs-s);font-weight:var(--hf-w-med);cursor:pointer;white-space:nowrap}
.hf5-tab[aria-selected=true]{background:var(--hf-surface-2);color:var(--hf-ink)}
.hf5-tab.is-more{color:var(--hf-ink-3)}
/* chip (state in the remote) */
.hf5-chip{display:inline-flex;align-items:center;gap:var(--hf-sp-2);height:var(--hf-ctl-s);padding:0 var(--hf-sp-3);border-radius:var(--hf-r-pill);background:var(--hf-surface-2);font-size:var(--hf-fs-s);font-weight:var(--hf-w-med);color:var(--hf-ink);align-self:flex-start;max-width:100%}
.hf5-chip.is-warn{color:var(--hf-state-warn-text)}
/* undo / hint toast */
.hf5-toast{position:fixed;left:50%;bottom:var(--hf-sp-6);transform:translateX(-50%);display:flex;align-items:center;gap:var(--hf-sp-3);max-width:calc(100% - var(--hf-sp-8));
  padding:var(--hf-sp-2) var(--hf-sp-2) var(--hf-sp-2) var(--hf-sp-4);border-radius:var(--hf-r-pill);background:var(--hf-surface-sheet);color:var(--hf-ink);box-shadow:var(--hf-elev-2);
  font-size:var(--hf-fs-s);z-index:2;min-height:var(--hf-ctl)}
.hf5-toast:not(:has(button)){padding-right:var(--hf-sp-4)}
/* section label (caps only here) */
.hf5-cap{min-width:0;max-width:100%;overflow:hidden;font-size:var(--hf-fs-caps);letter-spacing:var(--hf-caps-track);text-transform:uppercase;color:var(--hf-ink-2);font-weight:var(--hf-w-med)}
.hf5-row{display:flex;gap:var(--hf-sp-2);align-items:center;flex-wrap:wrap}
.hf5-stack{display:flex;flex-direction:column;gap:var(--hf-sp-2);min-width:0}
/* graph */
.hf5-graph{display:block;width:100%;height:var(--hf-graph-h);overflow:visible}
.hf5-graph .is-now{fill:none;stroke:var(--hf-ink-2);stroke-width:var(--hf-focus-w);vector-effect:non-scaling-stroke;stroke-linejoin:round}
.hf5-graph .is-set{fill:none;stroke:var(--hf5-role,var(--hf-accent));stroke-width:var(--hf-focus-w);vector-effect:non-scaling-stroke;stroke-dasharray:var(--hf-sp-1) var(--hf-sp-1)}
.hf5-graph .is-area{fill:var(--hf5-role-tint,var(--hf-accent-tint));stroke:none}
.hf5-axis{display:flex;justify-content:space-between;font-size:var(--hf-fs-xs);color:var(--hf-ink-3);font-variant-numeric:tabular-nums}
.hf5-empty{font-size:var(--hf-fs-s);color:var(--hf-ink-3);min-height:var(--hf-graph-h);display:flex;align-items:center}
/* ===== the remote shell: sheet (< sheet_below) / dialog / wide dialog ===== */
:host(hauflo-remote-v5){position:fixed;inset:0;z-index:7;display:block}
.hf5-scrim{position:absolute;inset:0;background:var(--hf-scrim)}
.hf5-sheet{position:absolute;display:flex;flex-direction:column;box-sizing:border-box;background:var(--hf-surface-sheet);color:var(--hf-ink);box-shadow:var(--hf-elev-2);
  max-height:var(--hf-remote-max-h);overflow:hidden;outline:0}
.is-sheet .hf5-sheet{left:0;right:0;bottom:0;border-radius:var(--hf-r-sheet) var(--hf-r-sheet) 0 0}
.is-dialog .hf5-sheet,.is-wide .hf5-sheet{left:50%;top:50%;transform:translate(-50%,-50%);width:min(var(--hf-remote-w),calc(100% - var(--hf-sp-6)));border-radius:var(--hf-r-sheet)}
.is-wide .hf5-sheet{width:min(var(--hf-remote-w-wide),calc(100% - var(--hf-sp-6)))}
.hf5-grab{width:var(--hf-sp-8);height:var(--hf-sp-1);border-radius:var(--hf-r-pill);background:var(--hf-hair);margin:var(--hf-sp-2) auto 0;flex:none}
.is-dialog .hf5-grab,.is-wide .hf5-grab{display:none}
.hf5-head{display:flex;align-items:flex-start;gap:var(--hf-sp-3);padding:var(--hf-sp-4) var(--hf-sp-4) var(--hf-sp-2) var(--hf-sp-5);flex:none}
.hf5-head>.hf5-stack{flex:1 1 auto;gap:var(--hf-sp-1)}
.hf5-head .hf5-name{font-size:var(--hf-fs-l)}
.hf5-body{overflow:auto;overscroll-behavior:contain;padding:var(--hf-sp-2) var(--hf-sp-5) var(--hf-sp-4);display:grid;gap:var(--hf-sp-5);grid-template-columns:minmax(0,1fr);align-content:start}
.is-wide .hf5-body.is-2col{grid-template-columns:minmax(0,1fr) minmax(0,1fr);column-gap:var(--hf-sp-7)}
.hf5-col{display:flex;flex-direction:column;gap:var(--hf-sp-5);min-width:0}
.hf5-part{display:flex;flex-direction:column;gap:var(--hf-sp-2);min-width:0}
.hf5-foot{display:flex;align-items:center;justify-content:space-between;gap:var(--hf-sp-3);flex-wrap:wrap;padding:var(--hf-sp-3) var(--hf-sp-5) var(--hf-sp-4);border-top:var(--hf-line-w) solid var(--hf-hair);
  font-size:var(--hf-fs-xs);color:var(--hf-ink-2);flex:none}
.hf5-foot>span{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.hf5-link{border:0;background:none;font:inherit;font-size:var(--hf-fs-s);font-weight:var(--hf-w-med);color:var(--hf-accent-text);cursor:pointer;display:inline-flex;align-items:center;gap:var(--hf-sp-1);min-height:var(--hf-hit);padding:0;border-radius:var(--hf-r-ctl);outline:0}
.hf5-link .hf5-g{width:var(--hf-icon-s);height:var(--hf-icon-s)}
.hf5-msg{display:flex;gap:var(--hf-sp-3);align-items:center;padding:var(--hf-sp-3);border-radius:var(--hf-r-ctl);background:var(--hf-surface-2);font-size:var(--hf-fs-s);color:var(--hf-ink-2)}
.hf5-msg>.hf5-g{width:var(--hf-icon-m);height:var(--hf-icon-m);flex:none}
.hf5-msg.is-warn{color:var(--hf-state-warn-text);background:var(--hf-state-warn-tint)}
/* v5.1 · tile template (slots): [icon · ⋮] / name / status / controls. XS = one row; S–L = a column; bare = icon only.
   Height by content; a taller fixed cell centres the content (G7). The value is the tile's fill (R4), never a big %. */
.hf5-tile{position:relative;display:flex;flex-direction:column;justify-content:center;gap:var(--hf-sp-2);min-width:0;box-sizing:border-box;
  padding:var(--hf-sp-3);border-radius:var(--hf-r-tile);background:var(--hf-tile);color:var(--hf-ink);overflow:hidden;isolation:isolate;min-height:var(--hf-tile-row-h);
  cursor:pointer;user-select:none;-webkit-user-select:none;-webkit-tap-highlight-color:transparent;touch-action:pan-y;outline:0}
/* v1 look: an off tile is paper with a hairline; on = the track under the value fill, or the role's tint with no fill */
.hf5-tile{box-shadow:inset 0 0 0 var(--hf-line-w) var(--hf-hair)}
.hf5-tile.is-on:not(.is-art){background:var(--hf5-role-tint,var(--hf-accent-tint));box-shadow:none}
.hf5-tile.is-on:not(.is-art):has(>.hf5-tfill){background:var(--hf-surface-2)}
.hf5-tile .hf5-ico{width:var(--hf-icon-m);height:var(--hf-icon-m);background:none;--hf5-glyph:var(--hf-ink-3)}
.hf5-tile .hf5-ico>.hf5-g{width:var(--hf-icon-s);height:var(--hf-icon-s)}
.hf5-tile .hf5-ico.is-on{--hf5-glyph:var(--hf5-role,var(--hf-accent))}
.hf5-tile .hf5-ico.is-warn{--hf5-glyph:var(--hf-state-warn)}.hf5-tile .hf5-ico.is-alarm{--hf5-glyph:var(--hf-state-alarm)}
.hf5-tile:focus-visible{box-shadow:0 0 0 var(--hf-focus-w) var(--hf-focus)}
/* v2 · the value top-right, opposite the icon, centred on it (owner 03.11) */
.hf5-th>.hf5-tv{margin-left:auto;align-self:stretch;display:flex;align-items:center;line-height:1}
.hf5-tile>.hf5-tfill{position:absolute;inset:0 auto 0 0;width:calc(var(--v,0) * 1%);background:linear-gradient(to right,color-mix(in srgb,var(--hf5-role,var(--hf-accent)) var(--hf-fill-from),transparent),color-mix(in srgb,var(--hf5-role,var(--hf-accent)) var(--hf-fill-to),transparent));z-index:-1;pointer-events:none;transition:width var(--hf-dur)}
.hf5-tile.is-drag>.hf5-tfill{transition:none}
.hf5-tile>.hf5-th{display:flex;align-items:center;justify-content:space-between;gap:var(--hf-sp-2)}
.hf5-tile>.hf5-tx{display:flex;flex-direction:column;gap:calc(var(--hf-sp-1) / 2);min-width:0}
.hf5-tile .hf5-tx>.hf5-name{font-size:var(--hf-fs-s);font-weight:var(--hf-w-med);line-height:1.25}
.hf5-tile .hf5-tx>.hf5-line{min-height:1.3em}
.hf5-tile>.hf5-tc{display:flex;gap:var(--hf-sp-2);align-items:center}
.hf5-tile.is-xs{flex-direction:row;align-items:center;gap:var(--hf-sp-2);padding:var(--hf-sp-2) var(--hf-sp-3)}
/* v1: the glyph's own box — the name gets the width (two lines at most in a half-width grid) */
.hf5-tile.is-xs>.hf5-ico{width:var(--hf-icon-s);height:var(--hf-icon-s)}
.hf5-tile.is-xs>.hf5-tx{flex:1 1 auto}
.hf5-tile.is-xs>.hf5-tc{flex:none}
.hf5-tile.is-bare{align-items:center;padding:var(--hf-sp-2)}
.hf5-tile.is-pending{box-shadow:inset 0 0 0 var(--hf-line-w) var(--hf-state-pending)}
.hf5-tile.is-offline>.hf5-tx>.hf5-name{color:var(--hf-ink-2)}
/* v5.1 · the card frame: header (caps title + one summary line + its one button) · body. Height by content. */
:host{display:block;min-width:0}
.hf5-card{position:relative;isolation:isolate;box-sizing:border-box;display:flex;flex-direction:column;gap:var(--hf-sp-3);min-width:0;height:100%;padding:var(--hf-sp-4);
  border-radius:var(--hf-r-card);background:var(--hf-surface-glass);color:var(--hf-ink);box-shadow:var(--hf-elev-1)}
/* Ателье v2 material: glass + the edge's highlight (own layer: a shadow list with a «none» bridge is invalid) + grain under the content */
.hf5-card::before,.hf5-card::after{content:"";position:absolute;inset:0;border-radius:inherit;pointer-events:none}
.hf5-card::before{box-shadow:var(--hf-edge);z-index:1}
.hf5-card::after{background:var(--hf-grain-img);opacity:var(--hf-grain);mix-blend-mode:var(--hf-grain-blend);z-index:-1}
.hf5-card.is-s{padding:var(--hf-sp-3);gap:var(--hf-sp-2)}
.hf5-ch{display:flex;align-items:center;gap:var(--hf-sp-3);min-width:0}
.hf5-ch>.hf5-tx{flex:1 1 auto;display:flex;flex-direction:column;gap:calc(var(--hf-sp-1) / 2);min-width:0}
.hf5-sum{font-size:var(--hf-fs-l);font-weight:var(--hf-w-med);line-height:1.25;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.hf5-card.is-s .hf5-sum{font-size:var(--hf-fs-m)}
/* v2 · header line: number · words · buttons on ONE centre axis; keeps the number's height (no jump when it goes) */
.hf5-hl{display:flex;align-items:center;gap:var(--hf-sp-3);min-width:0;min-height:var(--hf5-num)}
.hf5-hl>.hf5-num{flex:none}
.hf5-hw{flex:1 1 auto;min-width:0;display:flex;flex-direction:column;justify-content:center;gap:calc(var(--hf-sp-1) / 2);color:var(--hf-ink-2)}
.hf5-hw1{font-size:var(--hf-fs-m);line-height:1.25;display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:2;line-clamp:2;overflow:hidden;text-wrap:balance}
.hf5-hw1>.hf5-dot{display:inline-block;margin-right:var(--hf-sp-2);vertical-align:middle}
.hf5-hw2{font-size:var(--hf-fs-s);line-height:1.25;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
/* v1 look: with no number the words ARE the summary — v1's «3 из 9» line (light weight, ink, the large step) */
.hf5-hl:not(:has(>.hf5-num)) .hf5-hw1{font-size:var(--hf-fs-l);font-weight:var(--hf-w-num-m)}
.hf5-hl:not(:has(>.hf5-num)) .hf5-hw1:not(.is-warn):not(.is-alarm){color:var(--hf-ink)}
.hf5-hw1.is-warn{color:var(--hf-state-warn-text)}.hf5-hw1.is-alarm{color:var(--hf-state-alarm-text)}
.hf5-ch.is-quiet .hf5-hw1{font-size:var(--hf-fs-s);font-weight:var(--hf-w-reg);color:var(--hf-ink-2);-webkit-line-clamp:1;line-clamp:1}
.hf5-ch.is-quiet .hf5-hw1.is-warn{color:var(--hf-state-warn-text)}.hf5-ch.is-quiet .hf5-hw1.is-alarm{color:var(--hf-state-alarm-text)}
.hf5-hc{flex:none;display:flex;align-items:center;gap:var(--hf-sp-2);margin-left:auto}
.hf5-hc .hf5-btn{width:var(--hf-hit);height:var(--hf-hit);min-width:var(--hf-hit)}
.hf5-grid{display:grid;gap:var(--hf-sp-2);grid-template-columns:repeat(auto-fit,minmax(min(100%,calc(var(--hf-tile-min) * var(--hf5-k,1))),1fr))}
.hf5-grid.is-rows{grid-template-columns:repeat(auto-fill,minmax(min(100%,calc(var(--hf-tile-min-row) * var(--hf5-k,1))),1fr))}
/* v2 · a list = quiet rows like v1 (owner 03.11): no tile fill, a hairline between, a line icon without its circle, ± as glyphs */
.hf5-grid.is-rows{row-gap:0;column-gap:var(--hf-sp-6)}
.hf5-grid.is-rows>.hf5-tile{background:none;box-shadow:none;border-radius:0;padding:var(--hf-sp-2) 0;border-bottom:var(--hf-line-w) solid var(--hf-hair)}
.hf5-grid.is-rows>.hf5-tile:last-child{border-bottom:0}
.hf5-grid.is-rows .hf5-tx>.hf5-name{font-size:var(--hf-fs-m)}
.hf5-grid.is-rows>.hf5-tile:focus-visible{box-shadow:0 0 0 var(--hf-focus-w) var(--hf-focus);border-radius:var(--hf-r-ctl)}
.hf5-grid.is-rows>.hf5-tile.is-pending{box-shadow:none;opacity:var(--hf-off-opacity)}
.hf5-grid.is-rows .hf5-tile .hf5-ico{background:none;width:var(--hf-icon-l);height:var(--hf-icon-l)}
.hf5-grid.is-rows .hf5-tile .hf5-ico>.hf5-g{width:var(--hf-icon-m);height:var(--hf-icon-m)}
.hf5-grid.is-rows .hf5-step>.hf5-btn{background:none;box-shadow:none;color:var(--hf-ink-2)}
@media (hover:hover){.hf5-grid.is-rows .hf5-step>.hf5-btn:hover,.hf5-grid.is-rows .hf5-tc>.hf5-btn:not(.is-on):hover{background:var(--hf-surface-2);color:var(--hf-ink)}}
.hf5-grid.is-rows .hf5-tc>.hf5-btn:not(.hf5-on){background:none;box-shadow:none;color:var(--hf-ink-2)}
/* v2 · a free card (the hero): no glass, it stands on the page like v1's «Всё хорошо» */
.hf5-card.is-free{background:none;box-shadow:none}
.hf5-card.is-free::before,.hf5-card.is-free::after{display:none}
.hf5-card.is-s .hf5-msg>.hf5-g{display:none}
.hf5-grid.is-bare{grid-template-columns:repeat(auto-fill,minmax(var(--hf-ctl-l),1fr))}
.hf5-row{display:flex;flex-wrap:wrap;gap:var(--hf-sp-2)}
.hf5-num.is-role>b{color:var(--hf-ink)}
.hf5-step{display:flex;align-items:center;gap:var(--hf-sp-1);flex:none}
.hf5-step>.hf5-btn{height:var(--hf-ctl-s);width:var(--hf-ctl-s);min-width:var(--hf-ctl-s)}
.hf5-step>.hf5-val{min-width:3.6ch;display:flex;justify-content:center}
/* v1 · a row's value with «сейчас X°» under it; the under-line always holds its place, the column one width (no jump) */
.hf5-vn{display:flex;flex-direction:column;align-items:center;gap:calc(var(--hf-sp-1) / 2);min-width:var(--hf-row-val-w)}
.hf5-tc>.hf5-vn{align-items:flex-end}
.hf5-now{font-size:var(--hf-fs-xs);color:var(--hf-ink-2);white-space:nowrap;line-height:1.3}
.hf5-tap{cursor:pointer;-webkit-tap-highlight-color:transparent;user-select:none;-webkit-user-select:none;touch-action:pan-y;outline:0;border-radius:var(--hf-r-ctl)}
.hf5-tap:focus-visible{box-shadow:0 0 0 var(--hf-focus-w) var(--hf-focus)}
.hf5-big{display:flex;flex-direction:column;gap:var(--hf-sp-1);min-width:0}
`;
