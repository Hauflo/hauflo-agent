// Sample HA states for the catalog and tests (invented; the shapes are HA's own).
const st = (entity_id, state, attributes = {}) => ({ entity_id, state, attributes, last_updated: new Date(Date.now() - 4 * 60e3).toISOString() });
export const LIGHTS = {
  "dim + tone": st("light.living", "on", { friendly_name: "Гостиная, потолок", supported_color_modes: ["color_temp"], brightness: 168, color_mode: "color_temp", color_temp_kelvin: 3000, min_color_temp_kelvin: 2000, max_color_temp_kelvin: 6500 }),
  "dimmable": st("light.hall", "on", { friendly_name: "Прихожая", supported_color_modes: ["brightness"], brightness: 90 }),
  "off": st("light.bed", "off", { friendly_name: "Спальня, бра", supported_color_modes: ["color_temp"], min_color_temp_kelvin: 2200, max_color_temp_kelvin: 5000 }),
  "on/off only": st("light.stair", "on", { friendly_name: "Лестница", supported_color_modes: ["onoff"] }),
  "offline": st("light.lost", "unavailable", { friendly_name: "Терраса" }),
};
export const CLIMATES = {
  "floor, heating": st("climate.floor", "heat", { friendly_name: "Тёплый пол, ванная", hvac_modes: ["off", "heat"], hvac_action: "heating", current_temperature: 24.5, temperature: 27, target_temp_step: 0.5, min_temp: 15, max_temp: 35 }),
  "AC, cooling": st("climate.ac", "cool", { friendly_name: "Кондиционер, гостиная", hvac_modes: ["off", "cool", "heat", "dry", "fan_only", "auto"], hvac_action: "cooling", current_temperature: 25.1, temperature: 23, target_temp_step: 1, min_temp: 16, max_temp: 30, fan_modes: ["auto", "low", "medium", "high"], fan_mode: "auto", swing_modes: ["off", "vertical", "horizontal", "both"], swing_mode: "off", supported_features: 41 }),
  "off": st("climate.off", "off", { friendly_name: "Спальня", hvac_modes: ["off", "heat", "cool"], current_temperature: 21, temperature: 22, min_temp: 7, max_temp: 35 }),
  "offline": st("climate.lost", "unavailable", { friendly_name: "Кабинет" }),
};
