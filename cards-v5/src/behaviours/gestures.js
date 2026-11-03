// Shared service · gestures (canon/gestures.json). The ONLY place that reads a pointer. Components declare data-* hooks.
//   tap = main action · hold 500 ms on a tile = remote · hold 1 s on a button = confirm (fill, label stays readable)
//   horizontal drag (|dx| > 2|dy| after 8 px) = a value; a tap on a scale changes nothing; touch-action pan-y.
import { CANON } from "../gen/canon.js";
import { clamp } from "../core/dom.js";

const T = CANON.timing;

/** Drag a slider (.hf5-slider, or a tile with data-slide) · cb: {live(v), commit(v), hold()}; keyboard arrows step by `step`.
 *  data-axis="y" = a vertical one (the fill grows from the bottom): a mouse drags at once; a finger first holds still for
 *  T.grab_hold (a vertical swipe is the page scroll), then drags relative to where it grabbed; a hold without moving = cb.hold. */
export function bindSlider(el, cb, { step = CANON.light.brightness_step_pct } = {}) {
  const vert = el.dataset.axis === "y";
  let start = null;
  let drag = false;
  let grab = false;
  let moved = false;
  let timer = 0;
  let base = 0;
  const val = (ev) => {
    const r = el.getBoundingClientRect();
    if (grab) return clamp(Math.round(base + ((start[1] - ev.clientY) / (r.height || 1)) * 100), 0, 100);
    return clamp(Math.round((vert ? (r.bottom - ev.clientY) / (r.height || 1) : (ev.clientX - r.left) / (r.width || 1)) * 100), 0, 100);
  };
  const show = (v) => {
    el.style.setProperty("--v", v);
    el.setAttribute("aria-valuenow", v);
  };
  const begin = (pid) => {
    drag = true;
    el.setPointerCapture?.(pid);
    el.classList.add("is-drag");
    el.dispatchEvent(new CustomEvent("hf5-dragstart", { bubbles: true, composed: true }));
  };
  const end = () => {
    clearTimeout(timer);
    start = null;
    drag = grab = false;
    el.classList.remove("is-drag", "is-grab");
  };
  el.addEventListener("pointerdown", (ev) => {
    if (el.getAttribute("aria-disabled") === "true") return;
    start = [ev.clientX, ev.clientY];
    drag = grab = moved = false;
    if (!vert || ev.pointerType === "mouse") return;
    clearTimeout(timer);
    timer = setTimeout(() => {
      el.dispatchEvent(new PointerEvent("pointercancel")); // the tile's press (tap / remote) ends here: this is a grab
      start = [ev.clientX, ev.clientY];
      base = +el.getAttribute("aria-valuenow") || 0;
      grab = true;
      begin(ev.pointerId);
      el.classList.add("is-grab");
    }, T.grab_hold);
  });
  el.addEventListener("pointermove", (ev) => {
    if (!start) return;
    const dx = ev.clientX - start[0], dy = ev.clientY - start[1];
    if (!drag) {
      if (Math.hypot(dx, dy) <= T.move_cancel_px) return;
      const mine = vert ? Math.abs(dy) > CANON.dragAxisRatio * Math.abs(dx) : Math.abs(dx) > CANON.dragAxisRatio * Math.abs(dy);
      if (!mine || (vert && ev.pointerType !== "mouse")) return void end(); // a scroll, not ours
      begin(ev.pointerId);
    }
    moved = true;
    const v = val(ev);
    show(v);
    cb.live?.(v);
  });
  el.addEventListener("pointerup", (ev) => {
    const was = drag, held = grab && !moved, v = was && !held ? val(ev) : 0;
    end();
    if (!was) return;
    el.dispatchEvent(new CustomEvent("hf5-dragend", { bubbles: true, composed: true }));
    if (held) cb.hold?.();
    else cb.commit?.(v);
  });
  el.addEventListener("pointercancel", () => grab || end());
  el.addEventListener("touchmove", (ev) => grab && ev.cancelable && ev.preventDefault(), { passive: false });
  el.addEventListener("keydown", (ev) => {
    const d = { ArrowRight: step, ArrowUp: step, ArrowLeft: -step, ArrowDown: -step }[ev.key];
    if (!d) return;
    ev.preventDefault();
    const v = clamp(+el.getAttribute("aria-valuenow") + d, 0, 100);
    show(v);
    cb.commit?.(v);
  });
  return () => {};
}

/** Hold-to-confirm on a button (class A): the fill grows UNDER the label for `ms`; released early → onEarly (the one hint). */
export function bindHold(el, { onDone, onEarly, ms = T.confirm_by_hold } = {}) {
  let timer = 0;
  let armed = false;
  const stop = (early) => {
    if (!armed) return;
    armed = false;
    clearTimeout(timer);
    el.classList.remove("is-holding");
    if (early) onEarly?.();
  };
  el.addEventListener("pointerdown", (ev) => {
    if (el.disabled) return;
    ev.preventDefault();
    armed = true;
    el.style.setProperty("--hf5-hold-ms", ms + "ms");
    el.classList.add("is-holding");
    timer = setTimeout(() => {
      armed = false;
      el.classList.remove("is-holding");
      el.classList.add("is-done");
      setTimeout(() => el.classList.remove("is-done"), T.hold_feedback);
      onDone?.();
    }, ms);
  });
  ["pointerup", "pointerleave", "pointercancel"].forEach((e) => el.addEventListener(e, () => stop(true)));
  el.addEventListener("keydown", (ev) => ev.key === "Enter" && onEarly?.());
  el.addEventListener("click", (ev) => ev.preventDefault());
}

/** Tile body: tap = main action, hold 500 ms (no movement > 8 px) = remote. A scroll is neither. */
export function bindPress(el, { tap, hold } = {}) {
  let timer = 0;
  let at = null;
  let held = false;
  el.addEventListener("pointerdown", (ev) => {
    held = false;
    at = [ev.clientX, ev.clientY];
    el.style.setProperty("--hf5-tilehold-ms", T.hold + "ms");
    el.classList.add("is-holding");
    timer = setTimeout(() => {
      held = true;
      el.classList.remove("is-holding");
      hold?.();
    }, T.hold);
  });
  const cancel = () => (clearTimeout(timer), (at = null), el.classList.remove("is-holding"));
  el.addEventListener("pointermove", (ev) => at && Math.hypot(ev.clientX - at[0], ev.clientY - at[1]) > T.move_cancel_px && cancel());
  el.addEventListener("pointercancel", cancel);
  el.addEventListener("contextmenu", (ev) => (ev.preventDefault(), cancel(), hold?.()));
  el.addEventListener("pointerup", () => {
    const still = at !== null;
    cancel();
    if (still && !held) tap?.();
  });
  el.addEventListener("keydown", (ev) => (ev.key === "Enter" || ev.key === " ") && (ev.preventDefault(), tap?.()));
}
