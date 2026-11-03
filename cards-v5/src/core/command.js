// Shared service · command: ONE way every control calls Home Assistant. Tracks sending / failed per key, keeps the retry
// for the remote, and decides which trouble a tile or remote shows (offline > failed > blocked > sending).
import { CANON } from "../gen/canon.js";

export const TROUBLE = CANON.badgePriority; // [offline, failed, locked, pending] — one order for badge and message
const KIND = { offline: "offline", failed: "failed", blocked: "locked", sending: "pending" };

/** The one trouble to show, by priority. model: {offline, blocked}; cmd: {failed, sending}. → "offline"|"failed"|"blocked"|"sending"|null */
export const trouble = (model = {}, cmd = {}) => (model.offline ? "offline" : cmd.failed ? "failed" : model.blocked ? "blocked" : cmd.sending ? "sending" : null);
/** trouble → states object for badgeOf (primitives). */
export const troubleStates = (tr) => (tr ? { [KIND[tr]]: true } : {});

export class Commander {
  /** getHass: () => hass; onChange: () => void (re-render). */
  constructor(getHass, onChange = () => {}) {
    this.getHass = getHass;
    this.onChange = onChange;
    this.cmd = {}; // key → {sending, failed, retry}
    this.timers = {};
  }
  of(key) {
    return this.cmd[key] || {};
  }
  /** call HA: {domain, service, data}; key groups the state (the entity id). Resolves true / false. */
  async run(key, call) {
    const hass = this.getHass();
    const retry = () => this.run(key, call);
    this.cmd[key] = { sending: true, failed: false, retry };
    this.onChange();
    clearTimeout(this.timers[key]);
    let ok = true;
    try {
      const r = call.api ? await hass.callApi(call.api.method, call.api.path, call.api.body) : await hass.callService(call.domain, call.service, call.data || {});
      this.result = r;
    } catch (e) {
      ok = false;
      this.error = e;
    }
    this.cmd[key] = ok ? {} : { failed: true, retry };
    if (ok) this.timers[key] = setTimeout(() => this.onChange(), CANON.timing.no_answer);
    this.onChange();
    return ok;
  }
  /** Several calls of one gesture, in order; stops at the first failure. */
  async runAll(key, calls) {
    for (const c of calls) if (!(await this.run(key, c))) return false;
    return true;
  }
  retry(key) {
    return this.cmd[key]?.retry?.();
  }
  /** Rapid changes (a slider, ± taps) collapse into the last call after send_debounce. */
  debounced(key, call, ms = CANON.timing.send_debounce) {
    clearTimeout(this.timers["d:" + key]);
    this.timers["d:" + key] = setTimeout(() => (delete this.timers["d:" + key], this.run(key, call)), ms);
  }
  /** A value of this key is still on its way (waiting for the debounce or for HA): keep the optimistic view. */
  busy(key) {
    return !!this.timers["d:" + key] || !!this.cmd[key]?.sending;
  }
  dispose() {
    for (const k of Object.keys(this.timers)) clearTimeout(this.timers[k]);
  }
}
