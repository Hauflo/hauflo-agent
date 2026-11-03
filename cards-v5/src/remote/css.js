// Remote-only styles of the device / sensor remote (the kit's css.js stays untouched): the big ⏻ and the facts list.
// Sizes are canon tokens (kit.json → tokens: ctl-power); colours and spacing the shared --hf-* tokens.
export const REMOTE_CSS = `
.hf5-bigpower{display:flex;justify-content:center;padding:var(--hf-sp-3) 0}
.hf5-bigpower .hf5-btn{height:var(--hf-ctl-power);width:var(--hf-ctl-power);min-width:var(--hf-ctl-power)}
.hf5-bigpower .hf5-btn .hf5-g{width:calc(var(--hf-ctl-power) * .42);height:calc(var(--hf-ctl-power) * .42)}
.hf5-facts{margin:0;display:flex;flex-direction:column;gap:var(--hf-sp-2)}
.hf5-facts>div{display:flex;justify-content:space-between;align-items:baseline;gap:var(--hf-sp-3);min-width:0}
.hf5-facts dt{font-size:var(--hf-fs-s);color:var(--hf-ink-3)}
.hf5-facts dd{margin:0;font-size:var(--hf-fs-m);font-weight:var(--hf-w-med);color:var(--hf-ink);font-variant-numeric:tabular-nums;text-align:right;min-width:0;overflow-wrap:anywhere}
.hf5-facts .is-warn dd{color:var(--hf-state-warn-text)}
.hf5-pair>.hf5-pill{flex:1 1 0;justify-content:center}
`;
