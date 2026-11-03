export const CLIMATE_REMOTE = {
  type: "climate",
  over: "climate.kind",
  cols: 2,
  sections: [
    { control: "status", col: 0 },
    { control: "setpoint", col: 0, title: "climate.setpoint", need: "current" },
    { control: "modes", col: 0, title: "climate.mode", need: "modes" },
    { control: "fan", col: 0, title: "climate.fan", need: "fan" },
    { control: "swing", col: 0, title: "climate.swing", need: "swing" },
    { control: "preset", col: 0, title: "climate.preset", need: "preset" },
    { control: "graph", col: 1, title: "common.history", need: "graph" },
  ],
};
