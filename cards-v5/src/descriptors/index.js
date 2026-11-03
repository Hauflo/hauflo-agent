import { LIGHT_REMOTE } from "./light.js";
import { CLIMATE_REMOTE } from "./climate.js";
import { COVER_REMOTE } from "./cover.js";
import { DEVICE_REMOTE } from "./device.js";
import { CANON } from "../gen/canon.js";
import { lightModel, lightControls, lightAct } from "../adapters/light.js";
import { climateModel, climateControls, climateAct } from "../adapters/climate.js";
import { coverModel, coverControls, coverAct } from "../adapters/cover.js";
import { gadgetModel, gadgetControls, gadgetAct, gadgetHistory, gadgetExtras } from "../adapters/gadget.js";

/** Registry: domain → adapter + descriptor. The only list of what v5 draws; everything else falls back to the old cards. */
export const TYPES = {
  light: { model: lightModel, controls: lightControls, act: lightAct, remote: LIGHT_REMOTE },
  climate: { model: climateModel, controls: climateControls, act: climateAct, remote: CLIMATE_REMOTE },
  cover: { model: coverModel, controls: coverControls, act: coverAct, remote: COVER_REMOTE },
};

// Devices and sensors (owner 03.11): every domain of kit.json → remote_device.kinds shares ONE adapter + descriptor.
const GADGET = { model: gadgetModel, controls: gadgetControls, act: gadgetAct, remote: DEVICE_REMOTE, history: gadgetHistory, extras: gadgetExtras };
for (const dom of Object.keys(CANON.remoteDevice.kinds)) TYPES[dom] = GADGET;
