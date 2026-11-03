// Descriptor · the device / sensor remote (owner 03.11). One descriptor for every kind: the adapter only builds the
// controls the device HAS (a fan has speed, a lock has the hold pair, a sensor has the value and the 24 h line).
export const DEVICE_REMOTE = {
  type: "device",
  over: "kind.other",
  cols: 2,
  sections: [
    { control: "status", col: 0 },
    { control: "value", col: 0, title: "rdev.now_title" },
    { control: "power", col: 0 },
    { control: "target", col: 0, title: "climate.setpoint" },
    { control: "speed", col: 0, title: "rdev.speed" },
    { control: "preset", col: 0, title: "rdev.preset" },
    { control: "humidity", col: 0, title: "rdev.target_humidity" },
    { control: "hmodes", col: 0, title: "rdev.mode" },
    { control: "omode", col: 0, title: "rdev.mode" },
    { control: "actions", col: 0 },
    { control: "vacuum", col: 0 },
    { control: "graph", col: 1, title: "rdev.history_title" },
    { control: "usage", col: 1, title: "rdev.usage" },
    { control: "facts", col: 1, title: "rdev.device" },
  ],
};
