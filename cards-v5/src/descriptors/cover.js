// Descriptor · cover remote (covers spec v5 §1 «Большой пульт»): status · position · the triple with words · presets ·
// slats · «no position» note · favourite. need: a capability of the adapter model ("!x" = lacks it).
export const COVER_REMOTE = {
  type: "cover",
  over: "cover.kind",
  cols: 2,
  sections: [
    { control: "status", col: 0 },
    { control: "position", col: 0, title: "cover.position", need: "pos" },
    { control: "triple", col: 0 },
    { control: "guard", col: 0, need: "guard" },
    { control: "presets", col: 0, need: "pos" },
    { control: "note", col: 0, need: "nopos" },
    { control: "tilt", col: 1, title: "cover.tilt", need: "tilt" },
    { control: "fav", col: 1 },
  ],
};
