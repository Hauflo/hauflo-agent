// Descriptor · light remote. Pure data: which sections, in which column, shown when the device HAS the capability.
// need: a capability key of the adapter model ("!x" = lacks it). control: the adapter's control id. A new type = adapter + this.
export const LIGHT_REMOTE = {
  type: "light",
  over: "light.kind",
  cols: 2,
  sections: [
    { control: "status", col: 0 },
    { control: "brightness", col: 0, title: "light.brightness", need: "dim" },
    { control: "ct", col: 0, title: "light.ct", need: "ct" },
    { control: "presets", col: 0, need: "ct" },
    { control: "note", col: 0, need: "nodim" },
    { control: "assumed", col: 0 },
    { control: "timer", col: 1, title: "light.timer" },
    { control: "fav", col: 1 },
  ],
};
