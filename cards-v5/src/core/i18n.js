// RU + EN for every word a card shows (owner rule). Keys are dotted paths into locales/{ru,en}.json.
import ru from "../locales/ru.json";
import en from "../locales/en.json";

export const LOCALES = { ru, en };
let LANG = "ru";

export const langOf = (hass) => {
  const l = String(hass?.locale?.language || hass?.language || (typeof navigator !== "undefined" ? navigator.language : "") || "ru").toLowerCase();
  return l.startsWith("ru") ? "ru" : "en";
};
export const setLang = (l) => (LANG = LOCALES[l] ? l : "en");
export const lang = () => LANG;

const pick = (o, path) => path.split(".").reduce((a, k) => (a == null ? a : a[k]), o);
/** t("light.brightness"), t("remote.updated_min", {n: 3}). Missing in this language → the other one → the key itself. */
export const t = (key, vars = {}, l = LANG) => {
  let s = pick(LOCALES[l], key);
  if (s == null) s = pick(LOCALES[l === "ru" ? "en" : "ru"], key);
  if (s == null) return key;
  return String(s).replace(/\{(\w+)\}/g, (_, k) => (vars[k] ?? ""));
};
/** A device value (fan mode «turbo», preset «sleep»): our word if we know it, else the device's own, capitalised. */
export const tv = (group, v, l = LANG) => {
  const s = pick(LOCALES[l], `values.${group}.${v}`) ?? pick(LOCALES[l === "ru" ? "en" : "ru"], `values.${group}.${v}`);
  if (s != null) return s;
  const raw = String(v ?? "").replace(/_/g, " ");
  return raw.charAt(0).toUpperCase() + raw.slice(1);
};
/** Locale number: 21.5 → «21,5» (ru) / «21.5» (en). */
export const num = (v, digits = 1, l = LANG) =>
  v == null || Number.isNaN(+v) ? "—" : new Intl.NumberFormat(l === "ru" ? "ru-RU" : "en-US", { maximumFractionDigits: digits, minimumFractionDigits: 0 }).format(+v);
/** Fixed decimals: 24 → «24,0» next to «24,5» — a reading that changes keeps its width (no jump). */
export const numFixed = (v, digits = 1, l = LANG) =>
  v == null || Number.isNaN(+v) ? "—" : new Intl.NumberFormat(l === "ru" ? "ru-RU" : "en-US", { maximumFractionDigits: digits, minimumFractionDigits: digits }).format(+v);
export const clock = (d, l = LANG) => new Intl.DateTimeFormat(l === "ru" ? "ru-RU" : "en-GB", { hour: "2-digit", minute: "2-digit" }).format(d);
/** A count with its word: tn("alert.sensors", 3) → «3 датчика» (key.one / few / many / other by the language's rules). */
export const tn = (key, n, vars = {}, l = LANG) => {
  const form = new Intl.PluralRules(l === "ru" ? "ru-RU" : "en-US").select(n);
  const k = pick(LOCALES[l], `${key}.${form}`) != null ? `${key}.${form}` : `${key}.other`;
  return t(k, { n, ...vars }, l);
};
