// Hauflo «Atelier» cards for Home Assistant (owner 2026-10-24) — served by the Hauflo agent.
// Own cards only (no third-party code, nothing loaded from the internet): hauflo-hero, hauflo-room,
// hauflo-scenes, hauflo-climate, hauflo-sliders, hauflo-alert. Plain web components; HA calls
// setConfig() once and sets `hass` on every state change.
(() => {
  // Served by the agent (/hauflo_static) or, after it was removed, from /local/hauflo (keep.py).
  const BASE = new URL(".", import.meta.url).pathname.replace(/\/$/, "");

  // --- self-update after an agent update (owner 29.10: the HA app kept the old cards until «Сбросить кэш фронтенда») --
  // These cards' version is the `?v=` HA loaded them with (theme.py / keep.py put the agent's VERSION there — one place);
  // the installed agent's is the `version` attribute of its «Связь с Hauflo» entity (every HA user has the states). HA's
  // service worker serves pages and files stale-while-revalidate: a page from before the update loads the old cards. On a
  // difference: Hauflo's files and HA's cached pages leave the caches (nothing else of HA's), the service worker is asked to
  // update, the page reloads once per version (sessionStorage), and if that didn't help — a bar with a hard reload.
  const SELF = {
    v: new URL(import.meta.url).searchParams.get("v") || "",
    id: null, seenAt: 0, timer: null, bar: null, done: "",
    KEY: "hauflo_reloaded_for",
    agent(hass) {
      const st = hass?.states;
      if (!st) return "";
      if (this.id && st[this.id]?.attributes?.version) return String(st[this.id].attributes.version);
      if (Date.now() - this.seenAt < 60000) return "";
      this.seenAt = Date.now();
      const reg = hass.entities || {};
      this.id =
        Object.keys(reg).find((e) => reg[e]?.platform === "hauflo" && e.startsWith("binary_sensor.") && st[e]?.attributes?.version) ||
        Object.keys(st).find((e) => e.startsWith("binary_sensor.") && st[e].attributes?.server !== undefined && st[e].attributes?.version && st[e].attributes?.device_class === "connectivity") ||
        null;
      return this.id ? String(st[this.id].attributes.version) : "";
    },
    async purge() {
      const ours = (u) => u.pathname.startsWith("/hauflo_static/") || u.pathname.startsWith("/local/hauflo/");
      try {
        for (const name of (await window.caches?.keys?.()) || []) {
          const cache = await caches.open(name);
          for (const req of await cache.keys()) {
            const u = new URL(req.url);
            if (u.origin !== location.origin) continue;
            // A page (no file extension) cached by HA's service worker still names the old cards' address.
            const page = !/\.[a-z0-9]+$/i.test(u.pathname) && !u.pathname.startsWith("/api/");
            if (ours(u) || (page && /text\/html/.test((await cache.match(req))?.headers.get("content-type") || ""))) await cache.delete(req);
          }
        }
      } catch (e) {}
      try {
        await (await navigator.serviceWorker?.getRegistration?.())?.update();
      } catch (e) {}
    },
    store(get, val) {
      try {
        if (get) return sessionStorage.getItem(this.KEY);
        sessionStorage.setItem(this.KEY, val);
        return sessionStorage.getItem(this.KEY) === val;
      } catch (e) {
        return get ? null : false;
      }
    },
    check(hass, lang) {
      window.__haufloSelfUpdate = this; // the copy whose cards are on the page (tests)
      if (!this.v || this.timer) return;
      const want = this.agent(hass);
      if (!want || want === this.v) return this.hide();
      if (this.bar || this.done === want) return;
      // Right after HA's start the entity can be there a moment before the new page is: a short wait, then once more.
      this.timer = setTimeout(async () => {
        this.timer = null;
        const now = this.agent(hass);
        if (!now || now === this.v) return;
        this.done = now;
        await this.purge();
        // A reload only where it can be remembered (no storage — no loop: the bar instead).
        if (this.store(true) !== now && this.store(false, now)) return location.reload();
        this.show(lang, now);
      }, 2500);
    },
    show(lang, want) {
      if (this.bar) return;
      const ru = String(lang || "ru").startsWith("ru");
      const b = (this.bar = document.createElement("div"));
      b.id = "hauflo-update-bar";
      b.setAttribute("role", "status");
      b.style.cssText =
        "position:fixed;top:calc(8px + env(safe-area-inset-top,0px));left:50%;transform:translateX(-50%);z-index:9999;max-width:calc(100% - 32px);display:flex;align-items:center;gap:12px;padding:8px 8px 8px 16px;border-radius:16px;background:var(--card-background-color,#141517);color:var(--primary-text-color,#F4F2EE);box-shadow:0 10px 30px rgba(0,0,0,.35);font:500 13px/1.3 Onest,var(--ha-font-family-body,system-ui),sans-serif";
      const t = document.createElement("span");
      t.textContent = ru ? "Панель обновилась — нажмите, чтобы загрузить новую версию" : "The dashboard was updated — tap to load the new version";
      const go = document.createElement("button");
      go.textContent = ru ? "Обновить" : "Reload";
      go.style.cssText = "flex:0 0 auto;min-height:36px;padding:0 14px;border:0;border-radius:12px;cursor:pointer;font:600 13px Onest,sans-serif;background:var(--primary-color,#5CE0B4);color:var(--text-primary-color,#08090A)";
      go.onclick = async () => {
        go.disabled = true;
        await this.purge();
        // A new address for the page: no cache (the app's or the service worker's) has it.
        const u = new URL(location.href);
        u.searchParams.set("hauflo_reload", String(Date.now()));
        location.replace(u.href);
      };
      b.append(t, go);
      document.body.appendChild(b);
    },
    hide() {
      this.bar?.remove();
      this.bar = null;
    },
  };
  // The hard reload's mark leaves the address bar once the page is here.
  try {
    const u = new URL(location.href);
    if (u.searchParams.has("hauflo_reload")) {
      u.searchParams.delete("hauflo_reload");
      history.replaceState(history.state, "", u.pathname + u.search + u.hash);
    }
  } catch (e) {}

  // --- fonts (Onest, OFL) -------------------------------------------------------------------------
  if (!document.getElementById("hauflo-fonts")) {
    const s = document.createElement("style");
    s.id = "hauflo-fonts";
    s.textContent = `
@font-face{font-family:Onest;font-style:normal;font-weight:200 700;font-display:swap;src:url(${BASE}/fonts/onest-cyrillic.woff2) format("woff2");unicode-range:U+0301,U+0400-045F,U+0490-0491,U+04B0-04B1,U+2116}
@font-face{font-family:Onest;font-style:normal;font-weight:200 700;font-display:swap;src:url(${BASE}/fonts/onest-latin.woff2) format("woff2");unicode-range:U+0000-00FF,U+0131,U+0152-0153,U+02BB-02BC,U+02C6,U+02DA,U+02DC,U+0304,U+0308,U+0329,U+2000-206F,U+20AC,U+2122,U+2191,U+2193,U+2212,U+2215,U+FEFF,U+FFFD}`;
    document.head.appendChild(s);
  }

  // --- shared ------------------------------------------------------------------------------------
  const CSS = `
:host{container-type:inline-size;display:block;font-family:Onest,var(--ha-font-family-body),system-ui,sans-serif;font-variant-numeric:tabular-nums;color:var(--eh-ink,var(--primary-text-color));
 --ink:var(--eh-ink,var(--primary-text-color));--ink2:var(--eh-ink2,var(--secondary-text-color));--ink3:var(--eh-ink3,var(--disabled-text-color));
 --hair:var(--eh-hair,var(--divider-color));--amber:var(--eh-amber,#FFB25C);--amberGlow:var(--eh-amber-glow,rgba(255,160,70,.5));
 --ice:var(--eh-ice,#8DC2FF);--iceGlow:var(--eh-ice-glow,rgba(90,160,255,.42));--alert:var(--eh-alert,#FF5A5F);--alertGlow:var(--eh-alert-glow,rgba(255,70,80,.5));
 --mint:var(--eh-mint,#5CE0B4);--track:var(--eh-track,rgba(127,127,127,.12));
 --alertT:var(--eh-alert-text,var(--alert));--amberT:var(--eh-amber-text,var(--amber));--mintT:var(--eh-mint-text,var(--mint));--line:var(--eh-line-strong,var(--hair))}
*{box-sizing:border-box}
.glass{background:var(--eh-glass,var(--ha-card-background,var(--card-background-color)));box-shadow:var(--eh-edge,none),var(--eh-deep,var(--ha-card-box-shadow,none));border-radius:var(--eh-radius,30px);position:relative;overflow:hidden}
.caps{font:500 11px/1.2 Onest,var(--ha-font-family-body),sans-serif;letter-spacing:.14em;text-transform:uppercase;color:var(--ink2)}
.thin{font-weight:200;letter-spacing:-.05em;font-variant-numeric:tabular-nums}
ha-icon{--mdc-icon-size:18px;display:inline-flex}
button{font:inherit;color:inherit;background:none;border:0;padding:0;cursor:pointer;-webkit-tap-highlight-color:transparent}
.glow{position:absolute;inset:-30%;pointer-events:none;transition:opacity .4s}
button,[role="button"],.pill,.p,.r[data-e],.it{transition:transform .12s ease,background-color .25s,box-shadow .25s}
button:active,[role="button"]:active,.pill:active,.p:active,.it:active{transform:scale(.97)}
.f,.lvl,.bar i{transition:width .35s cubic-bezier(.2,.8,.2,1),height .35s cubic-bezier(.2,.8,.2,1)}
@keyframes eh-run{from{background-position:200% 0}to{background-position:-200% 0}}
.mv .f,.g.gc.mv{background-image:linear-gradient(90deg,transparent,color-mix(in srgb,var(--ice) 32%,transparent),transparent)!important;background-size:200% 100%!important;animation:eh-run 1.6s linear infinite}
@media (prefers-reduced-motion: reduce){.mv .f,.g.gc.mv{animation:none}}
.scn{display:flex;flex-wrap:wrap;gap:8px}.scn.grouped{flex-direction:column;align-items:stretch;gap:6px}.scn .scr{display:flex;flex-wrap:wrap;align-items:center;gap:8px}.scn .scl{font:500 12px Onest,sans-serif;color:var(--ink2);min-width:56px;margin-right:2px}.scn button{height:auto;min-height:44px;padding:6px 15px 6px 12px;border-radius:15px;display:flex;align-items:center;gap:8px;flex:0 1 auto;max-width:100%;min-width:0;color:var(--ink);box-shadow:var(--eh-edge,inset 0 0 0 1px var(--line));background:var(--eh-glass,transparent);font:500 13px Onest,sans-serif;transition:background .3s,box-shadow .3s}
.scn button span{min-width:0;white-space:normal;overflow:hidden;display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:2;line-height:1.2;overflow-wrap:break-word;hyphens:auto;text-align:left}.scn button ha-icon{color:var(--sacc);flex:0 0 auto;--mdc-icon-size:17px}
.scn button.go{background:color-mix(in srgb,var(--sacc) 26%,transparent)}.scn button.lit{background:color-mix(in srgb,var(--sacc) 30%,transparent);box-shadow:inset 0 0 0 2px var(--sacc);transition:background .3s,box-shadow .3s}.scn.light{--sacc:var(--amber)}.scn.cover{--sacc:var(--ink2)}.scn.mixed{--sacc:var(--mint)}
.scn button.add{box-shadow:inset 0 0 0 1px var(--line);background:transparent;color:var(--ink2);padding:0 14px 0 10px}.scn button.add ha-icon{color:var(--ink2)}
.scn button{position:relative}.scn button .src{position:absolute;top:3px;right:6px;font:600 8px/1 Onest,sans-serif;font-style:normal;letter-spacing:.04em;color:var(--ink2);opacity:.75;pointer-events:none}
.sgs .sg+.sg{margin-top:12px}.sg>.caps.ser{display:block;margin:0 0 6px 2px;font-size:10px;opacity:.85}.srow{margin-top:14px}.srow>.caps{display:block;margin-bottom:8px}
:host button.sent{background:color-mix(in srgb,var(--amber) 16%,transparent);box-shadow:inset 0 0 0 1.5px color-mix(in srgb,var(--amber) 55%,transparent)}`;

  const REDUCED = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
  const st = (hass, id) => (id && hass ? hass.states[id] : undefined);
  const num = (v) => (v === undefined || v === null || v === "" || isNaN(Number(v)) ? undefined : Number(v));
  // No data (gone, unavailable, unknown) is never shown as normal — no «Всё спокойно» over a leak sensor that lost
  // the bus (SCADA expert 25.10, ISA-101: three states of a value — good, bad, no data).
  const nd = (s) => !s || s.state === "unavailable" || s.state === "unknown";
  // Right after a deploy or an HA start the bus answers over some seconds: an entity HA has just (re)made reads «unknown»
  // — «обновляется…», not «нет данных» (integrator review r2, D9: «Свет: нет данных» on a fresh deploy whose lights all
  // answered a minute later). After the window it is «нет данных» as before; the card draws itself again then (wakeAt).
  const FRESH_MS = 30000;
  const freshUntil = (s) => (s && nd(s) ? Date.parse(s.last_changed || s.last_updated || "") + FRESH_MS : NaN);
  /** Every silent one of `list` is only waiting for its first reading: true, and the card is woken when that ends. */
  const updating = (card, list) => {
    const silent = list.filter(nd);
    if (!silent.length || !silent.every((s) => s && freshUntil(s) > Date.now())) return false;
    card?.wakeAt?.(Math.max(...silent.map(freshUntil)));
    return true;
  };
  // The agent's list of open alarms — the one set every card shows (SCADA expert 25.10, ISA-18.2): what the rules call
  // an alarm (a link rule's «off» is its alarm, not «on»), with priority, since, who accepted it. Fetched again only
  // when an alarm sensor of some card changes, or once a minute; without the agent — the sensor's «on».
  const AGENT = { at: 0, sig: "", data: null, wait: null, ids: new Set(), cards: new Set() };
  // «Нет дома — имитация» (presence.py): the header says it, so nobody wonders why the lights move. Asked once a minute
  // for all the headers on the page; the «Автоматизации» page puts its answer here at once.
  /** «Нет дома» on but working only in part (final review 28.10): what's missing and why — or null when all is planned.
   * kind: "covers" (blinds only), "lights" (lights only), "none"; why: few / no_location / late / none / covers /
   * no_devices / no_recorder; sun: «по солнцу» would help. */
  const presPartial = (pr) => {
    if (!pr?.enabled) return null;
    if (pr.status === "no_devices" || pr.status === "no_recorder") return { kind: "none", why: pr.status, sun: false };
    const tn = pr.tonight;
    if (!tn) return null;
    const now = Date.now();
    const wantLights = pr.lights && (pr.devices?.lights ?? 1) > 0;
    const wantCovers = pr.covers && (pr.devices?.covers ?? 1) > 0;
    const rooms = tn.rooms || [];
    const lightsOk = !wantLights || rooms.some((r) => Date.parse(r.to) > now);
    const coversOk = !wantCovers || !!(tn.covers?.open || tn.covers?.close);
    if (lightsOk && coversOk) return null;
    const why = !lightsOk ? (tn.lights === "few" || tn.lights === "no_location" ? tn.lights : rooms.length ? "late" : "none") : "covers";
    const kind = wantLights && lightsOk ? "lights" : wantCovers && coversOk ? "covers" : "none";
    return { kind, why, sun: why === "few" || why === "none" };
  };
  /** What a failed call to the agent's own API means (HA expert 28.10): "gone" — the agent isn't installed (HA answers
   * 404 and has no «hauflo» integration, or the views are left from a removed one: 503 «not set up»); "old" — the agent is
   * there but has no such view (an older version: «обновите в HACS»); "" — anything else (no link, an error). */
  const agentErr = (e, h) => {
    const m = String(e?.body?.message || "");
    if (e?.status_code === 503 && /not set up/i.test(m)) return "gone";
    if (e?.status_code === 404 && !m) return (h?.config?.components || []).includes("hauflo") ? "old" : "gone";
    return "";
  };
  const PRES = {
    at: 0, data: null, wait: null, cards: new Set(), off: false,
    set(d) {
      this.data = d;
      this.at = Date.now();
      for (const c of this.cards) {
        c._sig = "";
        c._update();
      }
    },
    get(card) {
      this.cards.add(card);
      const h = card._hass;
      // No agent (or one without «Нет дома»): asked no more on this page, not once a minute for nothing.
      if (h && !this.wait && !this.off && Date.now() - this.at > 60000) {
        this.at = Date.now();
        this.wait = h
          .callApi("GET", "hauflo/presence")
          .then((d) => this.set(d), (e) => ((this.data = null), (this.off = !!agentErr(e, h))))
          .finally(() => (this.wait = null));
      }
      return this.data;
    },
  };
  // The house's own settings (agent house.py, family review round 2): «Кто обслуживает дом» (the service company's contact
  // the deploy sends), whether this person may change the house's settings (Apple Home's «Allow editing»), and this
  // person's favourites / «Моя комната». Asked once per page (again after a failure, at most once a minute).
  const HOUSE = {
    data: null, at: 0, wait: null, off: false, cards: new Set(),
    set(d) {
      this.data = d;
      this.at = Date.now();
      for (const c of this.cards) {
        c._sig = "";
        c._update?.();
      }
    },
    get(card) {
      if (card) this.cards.add(card);
      const h = card?._hass || AGENT.hass;
      // Asked again every 5 minutes (family review round 3: the service contact moved, a page kept the old one).
      if (h?.callApi && !this.wait && !this.off && Date.now() - this.at > (this.data ? 300000 : 60000)) this.fetch(h);
      return this.data;
    },
    fetch(h) {
      this.at = Date.now();
      this.wait = h
        .callApi("GET", "hauflo/house")
        .then((d) => this.set(d), (e) => (this.off = !!agentErr(e, h)))
        .finally(() => (this.wait = null));
      return this.wait;
    },
    /** The settings, fetched now if not here yet (a long press right after the page opened). */
    async ready(h) {
      if (this.data) return this.data;
      if (this.wait) await this.wait;
      else if (h?.callApi && !this.off) await this.fetch(h);
      if (!this.data) throw new Error("no house settings");
      return this.data;
    },
  };
  /** May this person change the house's settings: the agent's answer; before it (or without the agent) — HA admins. */
  const canEdit = (h) => (HOUSE.data ? !!HOUSE.data.can_edit : !!h?.user?.is_admin);
  /** The service contact: ONE everywhere — the agent's («Кто обслуживает дом», service.py via /api/hauflo/house), the same
   * on the alarm card, the settings and the «Сервис» page (family review round 3: three numbers on three screens). A card's
   * own stored copy only when the agent has none (or no agent / not answered yet). */
  const serviceOf = (card) => {
    const house = HOUSE.get(card)?.service;
    if (house && (house.phone || house.email)) return house;
    return card?._config?.service?.phone || card?._config?.service?.email ? card._config.service : null;
  };
  const telOf = (p) => String(p || "").replace(/[^\d+]/g, "");
  /** «Позвонить · <company>» with the number under it — the alarm card, the settings, the «Сервис» page. */
  const callLink = (svc, cls = "call") =>
    svc?.phone
      ? `<a class="${cls}" href="tel:${esc(telOf(svc.phone))}"><ha-icon icon="mdi:phone"></ha-icon><span class="cl"><span>${svc.company || svc.name ? `Позвонить · <span data-own>${esc(svc.company || svc.name)}</span>` : "Позвонить сервису"}</span><b>${esc(svc.phone)}</b></span></a>`
      : svc?.email
        ? `<a class="${cls}" href="mailto:${esc(svc.email)}"><ha-icon icon="mdi:email-outline"></ha-icon><span class="cl"><span>${svc.company || svc.name ? `Написать · <span data-own>${esc(svc.company || svc.name)}</span>` : "Написать сервису"}</span><b>${esc(svc.email)}</b></span></a>`
        : "";
  const refreshAlarms = (hass) => {
    const sig = [...AGENT.ids].map((id) => hass.states[id]?.state).join();
    if (AGENT.off || AGENT.wait || (sig === AGENT.sig && Date.now() - AGENT.at < 60000)) return;
    AGENT.sig = sig;
    AGENT.wait = hass
      .callApi("GET", "hauflo/alarms")
      .then((d) => (AGENT.data = d))
      .catch((e) => {
        AGENT.data = null;
        AGENT.off = !!agentErr(e, hass); // no agent: the cards show HA's own states, nobody asks again in a loop
      })
      .finally(() => {
        AGENT.wait = null;
        AGENT.at = Date.now();
        for (const c of AGENT.cards) {
          c._sig = "";
          c._update();
        }
      });
  };
  const openAlarms = (card, ids) => {
    const h = card._hass;
    // The whole building's cards show every alarm the agent has — a rule's sensor needn't be on this dashboard
    // (narrow round 25.10, D6: a critical leak on the integrator's sensor, the dashboard said «в норме»).
    const everything = !!card._config?.all_alarms;
    AGENT.cards.add(card);
    for (const id of ids) AGENT.ids.add(id);
    if (h) refreshAlarms(h);
    const list = AGENT.data?._list;
    if (!Array.isArray(list)) return ids.filter((id) => st(h, id)?.state === "on").map((id) => ({ entity: id }));
    const want = new Set(ids);
    const seen = new Set();
    if (everything) for (const a of list) want.add(a.entity);
    // Critical first, then not accepted, then the oldest.
    return list
      .filter((a) => want.has(a.entity) && !a.noData && !seen.has(a.entity) && seen.add(a.entity))
      .sort((a, b) => Number(!!a.returned) - Number(!!b.returned) || Number(!!a.ackedBy) - Number(!!b.ackedBy) || Number(alarmCritical(h, b)) - Number(alarmCritical(h, a)) || String(a.since).localeCompare(String(b.since)));
  };
  /** A danger to people or the house (water, smoke, gas, CO) — never accepted together with equipment faults. */
  const DANGER = ["moisture", "smoke", "gas", "carbon_monoxide"];
  const alarmCritical = (h, a) => a.critical === true || DANGER.includes(st(h, a.entity)?.attributes?.device_class);
  const pluralAlarms = (n) => (n % 10 === 1 && n % 100 !== 11 ? "авария" : [2, 3, 4].includes(n % 10) && ![12, 13, 14].includes(n % 100) ? "аварии" : "аварий");
  const lampsWord = (n) => (n % 10 === 1 && n % 100 !== 11 ? "светильник" : [2, 3, 4].includes(n % 10) && ![12, 13, 14].includes(n % 100) ? "светильника" : "светильников");
  const sensorsWord = (n) => (n % 10 === 1 && n % 100 !== 11 ? "датчик" : [2, 3, 4].includes(n % 10) && ![12, 13, 14].includes(n % 100) ? "датчика" : "датчиков");
  /** A moment as the house's clock says it — HA's configured time zone, as the agent's pushes and Hauflo's mails do
   * (integrator review 29.10, defect 9: the card «с 03:46», the push «01:46»); the viewer's clock when HA says none.
   * → { hhmm, ddmm, day } (day: "YYYY-MM-DD" in that zone, to tell «today»). */
  const houseClock = (hass, t) => {
    const d = new Date(t);
    const tz = hass?.config?.time_zone;
    try {
      const p = Object.fromEntries(new Intl.DateTimeFormat("en-GB", { timeZone: tz || undefined, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).formatToParts(d).map((x) => [x.type, x.value]));
      return { hhmm: `${p.hour}:${p.minute}`, ddmm: `${p.day}.${p.month}`, day: `${p.year}-${p.month}-${p.day}` };
    } catch (e) {
      const two = (n) => String(n).padStart(2, "0");
      return { hhmm: `${two(d.getHours())}:${two(d.getMinutes())}`, ddmm: `${two(d.getDate())}.${two(d.getMonth() + 1)}`, day: d.toDateString() };
    }
  };
  const fmt = (v, digits = 0) => (v === undefined ? "—" : LANG === "ru" ? Number(v).toFixed(digits).replace(".", ",") : Number(v).toFixed(digits));
  // What to do now, by the kind of alarm (persona round 5: «опасно ли это и что делать прямо сейчас»); no «call service»
  // when nobody's number is set.
  const TODO = {
    moisture: "Перекройте воду: закройте кран на вводе (обычно у счётчика). Потом проверьте место протечки.",
    smoke: "Если видите дым или огонь — выйдите из дома и звоните 112.",
    gas: "Не включайте свет и огонь, откройте окна, выйдите и звоните 112.",
    carbon_monoxide: "Откройте окна, выйдите на воздух и звоните 112.",
  };
  // «The phones got a notice» only when a phone got it (integrator run 22: said over a leak no rule watched; run 23:
  // a rule to a person without a phone).
  const alarmTodo = (dc, phone, pushed, valve) =>
    TODO[dc]
      ? `${dc === "moisture" && valve ? `Перекройте воду — кнопка «${valve}» ниже (или кран на вводе у счётчика). Потом проверьте место протечки.` : TODO[dc]}${pushed && dc === "moisture" ? " Уведомление отправлено на телефоны (получено ли — неизвестно, пока кто-то не нажмёт «Принято»); «Принято» снимает повторы." : ""}`
      : `Это неисправность оборудования, людям она не опасна. ${phone ? "Позвоните сервису — кнопка ниже." : "Сообщите тому, кто обслуживает дом."}${pushed ? " Уведомление отправлено на телефоны (получено ли — неизвестно, пока кто-то не нажмёт «Принято»)." : ""}`;
  // --- English (owner 25.10: Dubai is the main market) ------------------------------------------------
  // The cards are written in Russian; with HA's language not Russian, a rendered card's own words are put into
  // English: whole phrases by the table, phrases with numbers by patterns, then known fragments. Names the
  // integrator gave (rooms, devices) stay as they are unless a whole name is one of the card's own words.
  let LANG = "ru";
  // One language per house, not per HA user (integrator review r2, D3 — as Loxone's app): the dashboard Hauflo wrote
  // gives every card its `lang`; the HA user's language only for a card without one (added by hand, an older dashboard).
  let HOUSE_LANG = null;
  const cfgLang = (c) => (typeof c?.lang === "string" && c.lang.trim() ? (c.lang.trim().toLowerCase().startsWith("ru") ? "ru" : "en") : null);
  /** The HA user's language — HA's own UI around the cards (the card picker, the card editor). */
  const userLang = (hass) => {
    const l = String(hass?.locale?.language || hass?.language || document.documentElement.lang || "ru").toLowerCase();
    return l.startsWith("ru") ? "ru" : "en";
  };
  const langOf = (hass) => HOUSE_LANG || userLang(hass);
  // Names from the project and the house — rooms, floors, devices, scenes — are never put into English (D3: «Кухня»
  // became «Kitchen», the scene «Ночь» «Night», while «Гостиная» stayed): a text that is a whole such name stays as typed.
  const CFG_NAMES = new Set();
  let OWN = { states: null, areas: null, floors: null, set: new Set() };
  const isOwn = (text) => {
    if (CFG_NAMES.has(text)) return true;
    const h = AGENT.hass;
    if (!h) return false;
    if (OWN.states !== h.states || OWN.areas !== h.areas || OWN.floors !== h.floors) {
      const set = new Set();
      for (const a of Object.values(h.areas || {})) if (a?.name) set.add(String(a.name).trim());
      for (const f of Object.values(h.floors || {})) if (f?.name) set.add(String(f.name).trim());
      for (const s of Object.values(h.states || {})) if (s?.attributes?.friendly_name) set.add(String(s.attributes.friendly_name).trim());
      OWN = { states: h.states, areas: h.areas, floors: h.floors, set };
    }
    return OWN.set.has(text);
  };
  /** The names a card's config gives (`name` of the card, its rooms, rows, entities) — the house's own words. */
  const cfgNames = (v, depth = 0) => {
    if (!v || typeof v !== "object" || depth > 6) return;
    if (Array.isArray(v)) return v.forEach((x) => cfgNames(x, depth + 1));
    if (typeof v.name === "string" && v.name.trim()) CFG_NAMES.add(v.name.trim());
    for (const x of Object.values(v)) if (x && typeof x === "object") cfgNames(x, depth + 1);
  };
  const EN = {
    "Открыть в комнате": "Open in the room", "Дома": "Home", "Дом": "Home", "Главная": "Home", "Нет дома · имитация": "Away · simulation", "Нет дома · только шторы": "Away · blinds only", "Нет дома · только свет": "Away · lights only", "Нет дома · сегодня ничего": "Away · nothing tonight", "Всё здание": "Whole building", "Всё спокойно": "All quiet", "Авария": "Alarm", "Аварий нет": "No alarms",
    "В комнатах": "Rooms", "Влажность": "Humidity", "Свет": "Lights", "Датчики": "Sensors", "в норме": "OK", "на улице": "outside",
    "Свет выключен": "Lights off", "Свет включён": "Lights on", "Всё выключено": "All off", "Свет: нет данных": "Lights: no data", "Свет: обновляется…": "Lights: updating…", "Обновляется…": "Updating…", "обновляется…": "updating…", "Всё в норме": "All OK", "всё в норме": "all OK",
    "Сценарии света": "Light scenes", "Сценарии штор": "Blind scenes", "Сценариев нет": "No scenes", "Климат": "Climate", "Тёплый пол": "Floor heating",
    "цель": "target", "нет данных из комнаты": "no room reading", "нет связи с термостатом": "thermostat offline", "проведите, чтобы изменить": "swipe to change",
    "Датчики состояния": "Status sensors", "Значения": "Values", "Режимы": "Modes", "Кнопки": "Buttons", "Вентиляция": "Ventilation", "Выключатели": "Switches",
    "Освещение": "Lighting", "Сцены": "Scenes", "Уведомления": "Notifications", "Без комнаты": "No room", "Нет связи": "Offline", "Теплее": "Warmer", "Холоднее": "Cooler", "нет связи": "offline", "Нет термостата": "No thermostat", "Держит": "Holding", "Нагревает": "Heating", "Охлаждает": "Cooling",
    "Осушает": "Drying", "Вентилирует": "Ventilating", "Выключен": "Off", "выключен": "off", "выключена": "off", "Нагрев": "Heat", "Холод": "Cool", "Охлаждение": "Cooling",
    "Авто": "Auto", "Диапазон": "Range", "Осушение": "Dry", "Вент.": "Fan", "Комфорт": "Comfort", "Эко": "Eco", "Эконом": "Economy", "Ночь": "Night", "Сон": "Sleep",
    "Буст": "Boost", "Кондиционеры": "Air conditioners", "Отопление": "Heating", "Изменить нужную температуру": "Change the setpoint", "Все выключены": "All off", "Тихо": "Quiet", "Средне": "Medium", "Сильно": "High", "Макс": "Max", "Мягко": "Diffuse", "Фокус": "Focus", "Турбо": "Turbo", "Обдув": "Swing", "Отпуск": "Away", "Нет дома": "Away", "Защита": "Protection", "Вкл": "On", "Выкл": "Off", "вкл": "on", "выкл": "off", "Вкл. все": "All on",
    "Выкл. все": "All off", "Все": "All", "Все закрыты": "All closed", "Нет данных": "No data", "нет данных": "no data", "Яркость": "Brightness", "Группы": "Groups", "Все вместе": "All together",
    "Прочее": "Other", "Откр.": "Open", "Закр.": "Closed", "закр.": "closed", "Открыть": "Open", "Закрыть": "Close", "Стоп": "Stop", "открыта": "open", "закрыта": "closed",
    "открыт": "open", "закрыт": "closed", "открыто": "open", "закрыто": "closed", "открывается…": "opening…", "закрывается…": "closing…", "Откр…": "Opening", "Закр…": "Closing", "Шторы": "Blinds",
    "Свернуть": "Collapse", "Назад": "Back", "← Назад": "← Back", "Вперёд": "Next", "Выполнено": "Done", "выполнить": "run", "Включить": "Turn on",
    "Перекройте воду: закройте кран на вводе (обычно у счётчика). Потом проверьте место протечки. Уведомление отправлено на телефоны (получено ли — неизвестно, пока кто-то не нажмёт «Принято»); «Принято» снимает повторы.": "Shut the water off: close the main valve (usually by the meter). Then check where it leaks. Sent to the phones (whether it arrived isn't known until someone taps «Accept»); «Accept» stops the repeats.",
    "Перекройте воду: закройте кран на вводе (обычно у счётчика). Потом проверьте место протечки.": "Shut the water off: close the main valve (usually by the meter). Then check where it leaks.",
    "Это неисправность оборудования, людям она не опасна. Позвоните сервису — кнопка ниже.": "Equipment fault — no danger to people. Call service with the button below.",
    "Это неисправность оборудования, людям она не опасна. Сообщите тому, кто обслуживает дом.": "Equipment fault — no danger to people. Tell whoever looks after the house.",
    "Проверьте оборудование или позвоните сервису. Уведомление отправлено на телефоны (получено ли — неизвестно, пока кто-то не нажмёт «Принято»).": "Check the equipment or call service. Sent to the phones (whether it arrived isn't known until someone taps «Accept»).",
    "Если видите дым или огонь — выйдите из дома и звоните 112.": "If you see smoke or fire, leave the house and call emergency services.",
    "Не включайте свет и огонь, откройте окна, выйдите и звоните 112.": "Don't switch lights or flames on; open the windows, get out and call emergency services.",
    "Откройте окна, выйдите на воздух и звоните 112.": "Open the windows, get outside and call emergency services.",
    "Это неисправность оборудования, людям она не опасна. Позвоните сервису — кнопка ниже. Уведомление отправлено на телефоны (получено ли — неизвестно, пока кто-то не нажмёт «Принято»).": "Equipment fault — no danger to people. Call service with the button below. Sent to the phones (whether it arrived isn't known until someone taps «Accept»).",
    "Это неисправность оборудования, людям она не опасна. Сообщите тому, кто обслуживает дом. Уведомление отправлено на телефоны (получено ли — неизвестно, пока кто-то не нажмёт «Принято»).": "Equipment fault — no danger to people. Tell whoever looks after the house. Sent to the phones (whether it arrived isn't known until someone taps «Accept»).",
    "Погода": "Weather", "Нет погоды": "No weather", "Ясно": "Clear", "Солнечно": "Sunny", "Облачно": "Cloudy", "Переменная облачность": "Partly cloudy", "Дождь": "Rain",
    "Ливень": "Downpour", "Гроза": "Thunderstorm", "Гроза, дождь": "Thunderstorm, rain", "Снег": "Snow", "Мокрый снег": "Sleet", "Град": "Hail", "Туман": "Fog",
    "Ветер": "Wind", "Ветер, облака": "Wind, clouds", "Опасность": "Danger", "Пн": "Mon", "Вт": "Tue", "Ср": "Wed", "Чт": "Thu", "Пт": "Fri", "Сб": "Sat", "Вс": "Sun",
    "Качество воздуха": "Air quality", "Хорошо": "Good", "Умеренно": "Moderate", "Вредно для чувствительных": "Unhealthy for sensitive groups", "Вредно": "Unhealthy",
    "Опасно": "Hazardous", "нет данных о частицах": "no particle data", "Летучие в-ва": "VOC", "Температура": "Temperature", "свежо": "fresh", "душно": "stuffy", "проветрить": "air out",
    "Нет плеера": "No player", "Играет": "Playing", "Пауза": "Paused", "Играть": "Play", "трансляция": "live", "запись": "recording", "Автоматизации": "Automations",
    "Запустить": "Run", "Вкл/выкл": "On/off", "выполняется…": "running…", "ещё не запускался": "never run", "ещё не срабатывала": "never triggered", "пусто": "empty",
    "Инженерные системы": "Building systems", "работает": "running", "ошибка": "error", "остановлен": "stopped", "на связи": "online", "батарея в норме": "battery OK",
    "батарея садится": "battery low", "Охрана": "Security", "Снята": "Disarmed", "Снять": "Disarm", "Под охраной": "Armed", "Под охраной · дома": "Armed · home",
    "Снята с охраны": "Disarmed", "Ставится…": "Arming…", "Снимается…": "Disarming…", "Задержка входа…": "Entry delay…", "ТРЕВОГА": "ALARM", "Позвонить сервису": "Call service", "Позвонить ·": "Call ·", "Написать ·": "Write ·", "Написать сервису": "Write to service", "Тревога · охрана": "Alarm · security", "Звучит сирена": "Siren sounding", "Уйти": "Away",
    "Ночной режим": "Night", "двери заперты": "doors locked", "заперт": "locked", "не заперт": "unlocked", "есть движение": "motion", "нет движения": "no motion",
    "сработал": "triggered", "звучит": "sounding", "ждёт": "idle", "на базе": "docked", "едет на базе": "returning to dock", "убирает": "cleaning", "заклинило": "jammed",
    "Ожидание": "Idle", "Вода": "Water", "Газ": "Gas", "Дым": "Smoke", "Угарный газ": "Carbon monoxide", "Перегрев": "Overheat", "Вскрытие": "Tamper", "Необычно": "Unusual",
    "загрузка…": "loading…", "истории пока нет": "no history yet", "только что": "just now", "Кухня": "Kitchen", "Гостиная зона": "Living area", "Обеденная": "Dining",
    "Остров": "Island", "ТВ": "TV", "Здание": "Building", "Устройства": "Devices", "Зима": "Winter", "Лето": "Summer",
    "Принято": "Accept", "тревога останется, пока причина не устранена": "the alarm stays until the cause is gone", "отправлено": "sent", "Нет ответа от дома — команда могла не дойти": "No answer from the house — the command may not have arrived", "Не получилось — ещё раз": "Didn't work — try again",
    "не отправлено — нет связи с домом": "not sent — no link to the house", "Показания": "Readings", "Комната": "Room", "Здесь пока ничего нет — добавьте устройства в проект или свои карточки в Home Assistant": "Nothing here yet — add devices to the project or your own cards in Home Assistant",
    "Тёплые полы": "Floor heating", "цель · обогрев": "target · heating", "цель · охлаждение": "target · cooling", "Сценарии": "Scenes", "Светильник": "Light", "Выключатель": "Switch", "Штора": "Blind", "Датчик": "Sensor", "Сцена": "Scene", "Кран": "Valve", "Замок": "Lock", "Устройство": "Device", "нужная температура ещё не пришла от системы": "no setpoint from the system yet", "нет данных от системы": "no data from the system yet", "нужная температура ещё не пришла": "no setpoint yet", "нет нужной температуры": "no setpoint", "Положение неизвестно": "Position unknown", "Поднять": "Raise", "Опустить": "Lower", "Раздвинуть": "Open", "Задвинуть": "Close",
    "Новая сцена": "New scene", "Новая сцена света": "New light scene", "Новая сцена штор": "New blind scene", "Название": "Name", "Например, Утро": "e.g. Morning", "Например, Кино": "e.g. Movie night",
    "Поставьте шторы как нужно — сцена запомнит их положение.": "Set the blinds the way you want — the scene remembers where they are.",
    "Включите свет как нужно — сцена запомнит, что горит и как ярко.": "Set the lights the way you want — the scene remembers what is on and how bright.",
    "Запомнить как сейчас": "Save as it is now", "Отмена": "Cancel", "Введите название": "Enter a name", "Переименовать": "Rename", "Сохранить": "Save", "Удалить сцену": "Delete scene", "Убрать с панели": "Remove from the dashboard",
    "Сцену настроил интегратор. Её можно переименовать и перезаписать; убрать — только с этой панели.": "Your installer set up this scene. You can rename it and save it again; removing only takes it off this dashboard.",
    "Сцена сохранится в Home Assistant: на панели, в голосе и автоматизациях. На настенных выключателях KNX её не будет.": "The scene is saved in Home Assistant: on the dashboard, in voice and in automations. It won't be on the KNX wall switches.",
    "Сцена — та же, что на настенных выключателях.": "The same scene as on the wall switches.", "Детский профиль: сцены меняют взрослые.": "Child profile: the adults change the scenes.", "Запомнить как сейчас?": "Save as it is now?", "Запомнить": "Save", "Отправлено": "Sent", "Готово": "Done",
    "Сработает, только если в ETS у актуаторов разрешено сохранение сцен. Если сцена не изменилась — спросите интегратора.": "This works only if scene saving is allowed in the actuators' ETS parameters. If the scene didn't change, ask your installer.",
    "Название на панели. Пусто — снова название от интегратора.": "The name on this dashboard. Empty — your installer's name again.",
    "пол сейчас": "floor now", "пол": "floor", "тёплый пол": "floor heating", "климат": "climate", "Все ждут": "All idle", "Греет": "Heating", "Греет · клапаны открыты": "Heating · valves open", "греет": "heating", "держит": "holding", "поддерживает": "keeping", "Сейчас не греют": "Not heating now", "сейчас": "now", "Пол": "Floor", "В комнате": "Room",
    "Состояние": "Status", "Управление": "Controls", "Настройки": "Settings", "охлаждает": "cooling", "осушает": "drying", "вентилирует": "ventilating", "Электро": "Electric", "Тепловой насос": "Heat pump", "Мощность": "Performance",
  };
  // Phrases with numbers / declensions (whole text node).
  const EN_RX = [
    [/^включён: /, "on: "],
    [/^обогрев до (.+)$/, "heating to $1"],
    [/^охлаждение от (.+)$/, "cooling from $1"],
    [/^(\d+) ламп[аы]? в (\d+) комнат[аеху]*$/, (m, a, b) => `${a} light${a === "1" ? "" : "s"} in ${b} room${b === "1" ? "" : "s"}`],
    [/^(\d+) из (\d+) открыто$/, "$1 of $2 open"],
    [/^(\d+) из (\d+) работают$/, "$1 of $2 running"],
    [/^(\d+) из (\d+) греют$/, "$1 of $2 heating"],
    [/^[Цц]ель (-?[\d,.–]+°)$/, (m, v) => `${m[0] === "Ц" ? "Target" : "target"} ${v}`],
    [/^→ (-?[\d,.]+°)$/, "→ $1"],
    [/^Текущее состояние (света|штор|света и штор) комнаты запомнится в сцене (\d+)\. Сцена изменится и на настенных выключателях\.$/, (m, w, n) => `The room's ${{ света: "lights", штор: "blinds", "света и штор": "lights and blinds" }[w]} as they are now will be stored in scene ${n}. The scene changes on the wall switches too.`],
    [/^Греют: (\d+)$/, "Heating: $1"],
    [/^Охлаждают: (\d+)$/, "Cooling: $1"],
    [/^Работают: (\d+)$/, "Running: $1"],
    [/^Включено: (\d+) из (\d+)$/, "On: $1 of $2"],
    [/^Греет до (.+)$/, "Heating to $1"],
    [/^Охлаждает до (.+)$/, "Cooling to $1"],
    [/^Держит (-?[\d,.]+°)$/, "Holding $1"],
    [/^Клапан (\d+): открыт$/, "Valve $1: open"],
    [/^Клапан (\d+): закрыт$/, "Valve $1: closed"],
    [/^(\d+) из (\d+) без данных$/, "$1 of $2 no data"],
    [/^(\d+) из (\d+)/, "$1 of $2"],
    [/^(\d+) авари[яийе]+$/, (m, a) => `${a} alarm${a === "1" ? "" : "s"}`],
    [/^аварий: (\d+)$/, "alarms: $1"],
    [/^без связи: (\d+)$/, "offline: $1"],
    [/^Показать все \((\d+)\)$/, "Show all ($1)"],
    [/^Нет данных · (\d+) светильник(?:а|ов)?$/, (m, a) => `No data · ${a} light${a === "1" ? "" : "s"}`],
    [/^Нет данных · свет$/, "No data · lights"],
    [/^Нет связи · (\d+) датчик(?:а|ов)?$/, (m, a) => `Offline · ${a} sensor${a === "1" ? "" : "s"}`],
    [/^(\d+) датчик(?:а|ов)? без связи$/, (m, a) => `${a} sensor${a === "1" ? "" : "s"} offline`],
    [/^выключен · (\d+) без данных$/, "off · $1 no data"],
    [/^выключен · обновляется…$/, "off · updating…"],
    [/^Климат · нет данных$/, "Climate · no data"],
    [/^Нет связи · (\d+) устройств[оа]?$/, (m, a) => `Offline · ${a} device${a === "1" ? "" : "s"}`],
    [/^Климат: нет данных$/, "Climate: no data"],
    [/^По одной \((\d+)\)$/, "One by one ($1)"], [/^Каждая отдельно \((\d+)\)$/, "Each on its own ($1)"],
    [/^Свет в (\d+) комнат[аеху]*$/, (m, a) => `Lights in ${a} room${a === "1" ? "" : "s"}`],
  ];
  // Fragments inside longer texts (between Cyrillic boundaries), longest first.
  const B = "(^|[^А-Яа-яЁё])"; // a Cyrillic word boundary (\\b doesn't know Cyrillic)
  const EN_FRAG = [
    [/^Позвонить · /, "Call · "], [new RegExp(`${B}ещё аварий:`, "g"), "$1more alarms:"], [new RegExp(`${B}и ещё (\\d+)`, "g"), "$1and $2 more"], [new RegExp(`${B}за сутки`, "g"), "$124 h"],
    [new RegExp(`${B}В комнате `, "g"), "$1Now "], [new RegExp(`${B}пол (?=\\d)`, "g"), "$1floor "], [new RegExp(`${B}Сейчас `, "g"), "$1Now "], [new RegExp(`${B}сейчас (?=\\d)`, "g"), "$1now "], [new RegExp(`${B}греет(?=$|[^А-Яа-яЁё])`, "g"), "$1heating"], [new RegExp(`${B}ждёт(?=$|[^А-Яа-яЁё])`, "g"), "$1idle"], [new RegExp(`${B}влажность `, "g"), "$1humidity "],
    [new RegExp(`${B}нужная температура ещё не пришла`, "g"), "$1no setpoint yet"], [/^поддерживает (?=-?\d)/, "keeping "],
    [new RegExp(`${B}нагрев(?=$|[^А-Яа-яЁё])`, "g"), "$1heating"], [new RegExp(`${B}Охлаждение до `, "g"), "$1Cooling to "], [new RegExp(`${B}Нагрев до `, "g"), "$1Heating to "],
    [new RegExp(`${B}Сдвиньте — `, "g"), "$1Slide — "], [new RegExp(`${B}сегодня в `, "g"), "$1today at "], [new RegExp(`${B}запускался `, "g"), "$1ran "],
    [new RegExp(`${B}сработала `, "g"), "$1triggered "], [new RegExp(`${B}батарея `, "g"), "$1battery "], [new RegExp(`${B}откр\\. `, "g"), "$1open "],
    [/^Авария · /, "Alarm · "], [new RegExp(`${B}принял `, "g"), "$1accepted by "], [/^Свет · /, "Lights · "], [new RegExp(`${B}обновлено `, "g"), "$1updated "],
    [/(\d) мин назад/g, "$1 min ago"], [/(\d) мин(?=$|[^А-Яа-яЁё])/g, "$1 min"], [/(\d) ч(?=$|[^А-Яа-яЁё])/g, "$1 h"],
    [new RegExp(`${B}с (?=\\d)`, "g"), "$1since "], [new RegExp(`${B}в (?=\\d)`, "g"), "$1at "], [new RegExp(`${B}до (?=\\d)`, "g"), "$1to "], [new RegExp(`${B}за (?=\\d)`, "g"), "$1in "],
    [new RegExp(`${B}открыто: `, "g"), "$1open: "], [new RegExp(`${B}не заперто: `, "g"), "$1unlocked: "], [new RegExp(`${B}внимание: `, "g"), "$1attention: "],
    [/ · вкл$/, " · on"], [/ · выкл$/, " · off"], [/^Яркость · /, "Brightness · "], [/^Нагрев · /, "Heat · "], [/^Охлаждение · /, "Cooling · "],
    [new RegExp(`${B}мин (?=-?\\d)`, "g"), "$1min "], [new RegExp(`${B}макс (?=-?\\d)`, "g"), "$1max "], [/^За сутки$/, "Last 24 h"], [/^За (\d+) ч$/, "Last $1 h"], [/^Пол · /, "Floor · "], [/обогрев до (-?[\d,.]+°)/g, "heating to $1"], [/охлаждение от (-?[\d,.]+°)/g, "cooling from $1"],
    [/км\/ч/g, "km/h"], [/м\/с/g, "m/s"], [/мм рт\. ст\./g, "mmHg"], [/гПа/g, "hPa"], [/мбар/g, "mbar"], [/мкг\/м³/g, "µg/m³"], [/(\d) Вт(?=$|[^А-Яа-яЁё])/g, "$1 W"],
  ];
  const tr = (text) => {
    if (LANG === "ru" || !text || !/[А-Яа-яЁё]/.test(text)) return text;
    const k = text.trim();
    if (isOwn(k)) return text;
    if (EN[k]) return text.replace(k, EN[k]);
    // «· солнечно» (the header's weather, lower case): the word's own translation, lower case too.
    const w = /^(·\s*)?([а-яё][а-яё ,]*)$/.exec(k);
    const cap = w && EN[w[2][0].toUpperCase() + w[2].slice(1)];
    if (cap) return text.replace(w[2], cap.toLowerCase());
    for (const [rx, rep] of EN_RX) if (rx.test(k)) return text.replace(k, k.replace(rx, rep));
    // «Охлаждает · 22.0°», «Нагрев · 25°»: a known word before « · », the rest on its own.
    const dot = /^([^·]+?) · (.+)$/.exec(k);
    if (dot && EN[dot[1]]) return text.replace(k, `${EN[dot[1]]} · ${tr(dot[2])}`);
    // «CO₂ · свежо», «открыто: 1 · двери заперты»: each side on its own, when that leaves no Russian.
    if (dot) {
      const both = `${tr(dot[1])} · ${tr(dot[2])}`;
      if (!/[А-Яа-яЁё]/.test(both)) return text.replace(k, both);
    }
    let out = text;
    for (const [rx, rep] of EN_FRAG) out = out.replace(rx, rep);
    return out;
  };
  const translate = (root) => {
    if (LANG === "ru") return;
    // A name the client typed (`data-own`) stays as typed.
    const walk = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, { acceptNode: (n) => (n.parentNode?.nodeName === "STYLE" || n.parentElement?.closest?.("[data-own]") ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT) });
    for (let n = walk.nextNode(); n; n = walk.nextNode()) {
      const v = tr(n.nodeValue);
      if (v !== n.nodeValue) n.nodeValue = v;
    }
    root.querySelectorAll("[aria-label],[title]").forEach((el) => {
      for (const a of ["aria-label", "title"]) if (el.hasAttribute(a)) el.setAttribute(a, tr(el.getAttribute(a)));
    });
  };
  // --- The client's names (review round 2) ---------------------------------------------------------------------
  // HA and Hauflo keep the ETS name as is (owner rule); the client's cards don't show the integrator's working notes:
  // a device/model code in brackets at the end («Движение (bps7)», «Температура (i4-net)») goes — unless two devices
  // of one room would then read the same; an ETS note with «?» («? может люстра?») isn't a name — the card says what
  // the thing is until the integrator names it.
  const techTail = (n) => {
    const m = /^(.*\S)\s*\(\s*([^()]+?)\s*\)\s*$/.exec(n);
    return m && /^[A-Za-z0-9._\/ -]{2,16}$/.test(m[2]) && /[A-Za-z]/.test(m[2]) && /\d/.test(m[2]) ? m[1] : null;
  };
  const KIND = { light: "Светильник", switch: "Выключатель", cover: "Штора", climate: "Климат", binary_sensor: "Датчик", sensor: "Датчик", scene: "Сцена", fan: "Вентиляция", valve: "Кран", lock: "Замок" };
  let NAMES = { states: null, base: new Map() };
  /** How many entities of one HA area would read `base` with their tails dropped — computed once per states object. */
  const sameBase = (id, base) => {
    const h = AGENT.hass;
    if (!h) return 1;
    if (NAMES.states !== h.states) {
      const count = new Map();
      for (const [eid, s] of Object.entries(h.states)) {
        const n = String(s?.attributes?.friendly_name || "");
        const b = techTail(n);
        const key = `${h.entities?.[eid]?.area_id || ""}|${(b ?? n).toLowerCase()}`;
        count.set(key, (count.get(key) || 0) + 1);
      }
      NAMES = { states: h.states, base: count };
    }
    return NAMES.base.get(`${h.entities?.[id]?.area_id || ""}|${base.toLowerCase()}`) || 1;
  };
  const clientName = (raw, id) => {
    const n = String(raw || "").trim();
    if (!n) return id;
    if (n.includes("?")) return KIND[String(id || "").split(".")[0]] || "Устройство";
    const base = techTail(n);
    return trimSep(base && sameBase(id, base) < 2 ? base : n) || n;
  };
  const nameOf = (s, id) => (s && s.attributes.friendly_name ? clientName(s.attributes.friendly_name, id) : id);
  // Device codes («DKP1-1», «B1-2») keep a non-breaking hyphen: «Протечка (DKP1-» / «1)» on two lines read as
  // nonsense (integrator run 15). Only upper-case codes — icon names and entity ids are lower case.
  const esc = (t) => String(t ?? "").replace(/\b([A-Z]{1,6}\d*)-(\d+)\b/g, "$1\u2011$2").replace(/[&<>"'()]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;", "(": "&#40;", ")": "&#41;" })[c]);
  // --- text size (owner 28.10: an elderly client's phone has a large system font, the HA app enlarges our text, cards bloat).
  // All text sizes of the cards are px; TZ() rewrites every font size (font:… / font-size) in the CSS and markup the cards
  // set into calc(Npx*var(--hf-ts,1)), so ONE variable on <html> — inherited by every shadow root, sheets and dialogs
  // included — rescales ALL our text live, while boxes, paddings and icons keep their size (the layout stays intact).
  // --hf-ts = chosen size / detected phone text scale: «Как в телефоне» leaves it 1 (the app's enlargement stays).
  const TS = { mode: "phone", k: 1 };
  // «Ещё крупнее» (family review round 2: «шрифт крупнее, чем крупнее» for a 72-year-old) — 140 %.
  const TS_MULT = { normal: 1, big: 1.15, huge: 1.4, small: 0.9 };
  const tsGet = () => {
    try {
      const v = localStorage.getItem("hauflo_textsize");
      return v in TS_MULT ? v : "phone";
    } catch (e) {
      return "phone";
    }
  };
  /** The browser's text enlargement (Android WebView textZoom, a large system font): the width of text in the page against
   * the same text in a canvas (which the enlargement doesn't touch). 1 when nothing is enlarged. */
  const tsDetect = () => {
    try {
      const f = "100px sans-serif";
      const t = "Hauflo MWmw 0123456789";
      const c = document.createElement("canvas").getContext("2d");
      c.font = f;
      const ref = c.measureText(t).width;
      const el = document.createElement("span");
      el.style.cssText = `position:absolute;left:-9999px;top:0;white-space:nowrap;visibility:hidden;font:${f};letter-spacing:0`;
      el.textContent = t;
      document.body.appendChild(el);
      const got = el.getBoundingClientRect().width;
      el.remove();
      const k = ref > 0 && got > 0 ? got / ref : 1;
      return Math.abs(k - 1) < 0.04 ? 1 : Math.min(2, Math.max(0.6, Math.round(k * 20) / 20));
    } catch (e) {
      return 1;
    }
  };
  const tsApply = () => {
    TS.mode = tsGet();
    TS.k = TS.mode === "phone" ? 1 : tsDetect();
    const s = TS.mode === "phone" ? 1 : Math.round((TS_MULT[TS.mode] / TS.k) * 1000) / 1000;
    document.documentElement.style.setProperty("--hf-ts", String(s));
  };
  const tsSet = (mode) => {
    try {
      if (mode === "phone") localStorage.removeItem("hauflo_textsize");
      else localStorage.setItem("hauflo_textsize", mode);
    } catch (e) {
      /* private mode: lives until reload */
    }
    tsApply();
  };
  if (document.body) tsApply();
  else document.addEventListener("DOMContentLoaded", tsApply, { once: true });
  // A changed system font / the app back from the background / another tab of the same device.
  for (const [t, n] of [["resize", window], ["visibilitychange", document], ["storage", window]]) n.addEventListener(t, () => (t !== "visibilitychange" || !document.hidden) && tsApply());
  const TZ_MEMO = new Map();
  const TZ_FONT = /(font:\s*(?:[^;}"(),]*?\s)?)(\d*\.?\d+)px(\/\d*\.?\d+(?:px)?)?(?=[\s;}"'])|(font-size:\s*)(\d*\.?\d+)px/g;
  const tzPx = (n) => `calc(${n}px*var(--hf-ts,1))`;
  const TZ = (css, memo = true) => {
    if (memo && TZ_MEMO.has(css)) return TZ_MEMO.get(css);
    const out = css.replace(TZ_FONT, (m, pre, n, lh, pre2, n2) => (pre2 ? pre2 + tzPx(n2) : pre + tzPx(n) + (lh ? (lh.endsWith("px") ? "/" + tzPx(lh.slice(1, -2)) : lh) : "")));
    if (memo) {
      if (TZ_MEMO.size > 40) TZ_MEMO.clear();
      TZ_MEMO.set(css, out);
    }
    return out;
  };
  const isOn = (s) => !!s && ["on", "open", "opening", "heat", "cool", "heat_cool", "auto", "dry", "fan_only"].includes(s.state);
  const avg = (xs) => {
    const v = xs.filter((x) => x !== undefined);
    return v.length ? v.reduce((a, b) => a + b, 0) / v.length : undefined;
  };
  const tempOf = (s) => (s ? (s.entity_id.startsWith("climate.") ? num(s.attributes.current_temperature) : num(s.state)) : undefined);
  /** A card's extra readings: `sensors: ["sensor.x", {entity, name}]`. */
  const readingsOf = (c) => (c?.sensors || []).map((x) => (typeof x === "string" ? { entity: x } : x)).filter((x) => x && x.entity);
  const brightnessPct = (s) => (s && s.state === "on" ? (num(s.attributes.brightness) !== undefined ? Math.round((num(s.attributes.brightness) / 255) * 100) : 100) : 0);
  const call = (hass, domain, service, data) => hass.callService(domain, service, data);
  /** A tap that sends a command (a scene, «Вкл./Выкл. все», ▲ ■ ▼): a tick and «отправлено» for a moment, as the KNX
   * preset buttons (final review 28.10: «нажал — непонятно, ушло ли»); «нет ответа от дома» when HA says the call
   * failed or nothing came back in 10 s. HA's own technical error toast is off for these calls. */
  /** One key per button that survives a re-render (the entity flips «Вкл. все» → «Выкл. все» at once and the card is
   * rebuilt: the «отправлено» is kept per key with its end time and put back on the new button — UI verifier 0.15.3). */
  const ackKey = (b) => {
    const d = b.dataset;
    const par = b.parentElement ? [...b.parentElement.children].indexOf(b) : 0;
    return [d.scene, "all" in d ? "all" : d.gc, d.g, d.c, b.closest("[data-e]")?.dataset.e, par].join("|");
  };
  const ackShow = (b, until) => {
    const ic = b.querySelector("ha-icon");
    const lab = b.querySelector("span") || (ic ? null : b);
    b._ack ??= { icon: ic?.getAttribute("icon"), txt: lab?.textContent, aria: b.getAttribute("aria-label"), title: b.getAttribute("title") };
    const was = b._ack;
    b.classList.add("go", "sent");
    ic?.setAttribute("icon", "mdi:check");
    if (lab) lab.textContent = tr("отправлено");
    else if (was.aria !== null) {
      // An icon-only button (▲ ■ ▼) has no room for the word: it is read out and in the tooltip.
      b.setAttribute("aria-label", tr("отправлено"));
      b.setAttribute("title", tr("отправлено"));
    }
    clearTimeout(b._ackT);
    b._ackT = setTimeout(() => {
      b.classList.remove("go", "sent");
      if (was.icon) ic?.setAttribute("icon", was.icon);
      if (lab && was.txt !== undefined) lab.textContent = was.txt;
      if (was.aria !== null && !lab) {
        b.setAttribute("aria-label", was.aria);
        was.title === null ? b.removeAttribute("title") : b.setAttribute("title", was.title);
      }
      b._ack = null;
    }, Math.max(0, until - Date.now()));
  };
  /** After a card is rebuilt: the buttons that are still within their «отправлено» get it back. */
  const reAck = (card) => {
    const m = card._acks;
    if (!m) return;
    const now = Date.now();
    for (const k of Object.keys(m)) if (m[k] <= now) delete m[k];
    if (!Object.keys(m).length) return;
    card.shadowRoot.querySelectorAll("button").forEach((b) => {
      const u = m[ackKey(b)];
      if (u) ackShow(b, u);
    });
  };
  const sendAck = (card, b, domain, service, data, ok) => {
    if (b) {
      const until = Date.now() + 1500;
      (card._acks ??= {})[ackKey(b)] = until;
      ackShow(b, until);
    }
    let done = false;
    const fail = () => {
      if (done) return;
      done = true;
      card.dispatchEvent(new CustomEvent("hass-notification", { bubbles: true, composed: true, detail: { message: tr("Нет ответа от дома — команда могла не дойти") } }));
    };
    const t = setTimeout(fail, 10000);
    Promise.resolve()
      .then(() => card._hass.callService(domain, service, data, undefined, false))
      .then(
        () => {
          done = true;
          clearTimeout(t);
          ok?.();
        },
        () => {
          clearTimeout(t);
          fail();
        },
      );
  };
  /** A scene ran (family review round 3, Apple Home / Yandex Дом): «Сценарий «Вечер» включён» at the bottom of the screen and
   * the chip lit for a few seconds (kept over the card's re-render: `card._sceneLit`). */
  const sceneDone = (card, id) => {
    const s = st(card._hass, id);
    const name = (FAM.names?.[id]) || (s ? nameOf(s, id) : id);
    card._sceneLit = { id, until: Date.now() + 4000 };
    card.shadowRoot?.querySelectorAll(`button[data-scene="${window.CSS.escape(id)}"]`).forEach((x) => x.classList.add("lit"));
    setTimeout(() => {
      if (card._sceneLit?.id === id && card._sceneLit.until <= Date.now() + 50) {
        card._sceneLit = null;
        card.shadowRoot?.querySelectorAll("button[data-scene].lit").forEach((x) => x.classList.remove("lit"));
      }
    }, 4000);
    okToast(LANG === "ru" ? `Сценарий «${name}» включён` : `Scene «${name}» is on`);
  };
  const navigate = (path) => {
    history.pushState(null, "", path);
    window.dispatchEvent(new CustomEvent("location-changed", { detail: { replace: false } }));
  };
  const haptic = (el, type = "light") => el.dispatchEvent(new CustomEvent("haptic", { detail: type, bubbles: true, composed: true }));
  const moreInfo = (el, entityId) => el.dispatchEvent(new CustomEvent("hass-more-info", { detail: { entityId }, bubbles: true, composed: true }));
  // --- «a tap on a thing opens that thing» (owner 29.10, phone: «нажал на зону тёплого пола — попал куда угодно, только не
  // туда»). Apple Home / Google Home: a row that names one accessory opens its control sheet over the page; «Открыть в
  // комнате» goes to the room and lands ON its card (scrolled to, lit for a moment) — not on the top of the room.
  /** Every Hauflo card drawn (pruned when searched): a room page is found by what its cards show. */
  const CARDS = new Set();
  const lovelacePanel = () => document.querySelector("home-assistant")?.shadowRoot?.querySelector("home-assistant-main")?.shadowRoot?.querySelector("ha-drawer partial-panel-resolver ha-panel-lovelace");
  /** The dashboard's own Hauflo card of a thermostat — the one its room shows (sensors, the second setpoint) — on the view
   * `path` (else on any): {config, view}; a zone of a room's zones card is that zone alone ({config: one zone}). */
  const climateCardOf = (entity, path) => {
    const views = lovelacePanel()?.lovelace?.config?.views || [];
    const want = path ? decodeURIComponent(String(path).split("/")[2] || "") : "";
    const base = location.pathname.split("/")[1];
    let zone = null;
    const look = (list) => {
      for (const v of list) {
        const stack = [...(v.cards || []), ...(v.sections || []).flatMap((s) => s?.cards || [])];
        while (stack.length) {
          const c = stack.shift();
          if (!c || typeof c !== "object") continue;
          if (Array.isArray(c.cards)) stack.push(...c.cards);
          if (c.card) stack.push(c.card);
          if (c.type !== "custom:hauflo-climate") continue;
          const at = `/${base}/${v.path}`;
          if (!c.zones?.length && c.entity === entity) return { config: c, view: at };
          const z = (c.zones || []).find((x) => x?.entity === entity);
          if (z && !zone) zone = { config: { entity, ...(z.name ? { label: z.name } : {}), floor: true, ...(z.activity ? { activity: z.activity } : {}), ...(c.chart === false ? { chart: false } : {}) }, view: at };
        }
      }
      return zone;
    };
    return (want && look(views.filter((v) => v.path === want))) || look(views) || null;
  };
  /** After «Открыть в комнате»: the card of the room page that shows `entity` is scrolled to and lit (≤ 5 s to appear). */
  let FOCUS = null;
  const focusOn = (entity, path) => {
    FOCUS = { entity, path, until: Date.now() + 5000 };
    const tick = () => {
      if (!FOCUS || Date.now() > FOCUS.until) return (FOCUS = null);
      if (location.pathname !== FOCUS.path) return setTimeout(tick, 150);
      const hits = [];
      for (const c of CARDS) {
        if (!c.isConnected) {
          CARDS.delete(c);
          continue;
        }
        if (!c._config || !c.getBoundingClientRect().height) continue;
        let ids = [];
        try {
          ids = c.watched();
        } catch (e) {
          ids = [];
        }
        if (c._config.entity === entity) hits.unshift(c);
        else if (ids.includes(entity)) hits.push(c);
      }
      if (!hits.length) return setTimeout(tick, 150);
      FOCUS = null;
      const card = hits[0];
      card.scrollIntoView({ block: "center", behavior: REDUCED ? "auto" : "smooth" });
      const lit = () => card.shadowRoot?.querySelector(".glass")?.animate([{ boxShadow: "0 0 0 3px var(--eh-ice,#3C82F0)" }, { boxShadow: "0 0 0 3px var(--eh-ice,#3C82F0)", offset: 0.6 }, { boxShadow: "0 0 0 0 transparent" }], { duration: 1800, easing: "ease-out" });
      setTimeout(lit, REDUCED ? 0 : 350);
    };
    setTimeout(tick, 120);
  };
  /** A small «Сохранено» over a sheet (iOS / Android settings: a change applies at once, nothing moves). */
  const TOAST_CSS = `.toast{position:fixed;left:50%;bottom:calc(var(--hf-toast-b,28px) + env(safe-area-inset-bottom));transform:translate(-50%,8px);z-index:2;display:flex;align-items:center;gap:6px;padding:10px 16px;border-radius:20px;background:var(--eh-ink,#1c1c1e);color:var(--card-background-color,#fff);font:500 14px Onest,sans-serif;box-shadow:0 8px 24px rgba(0,0,0,.25);opacity:0;pointer-events:none;transition:opacity .18s,transform .18s;white-space:nowrap}.toast.on{opacity:1;transform:translate(-50%,0)}.toast.bad{background:var(--error-color,#c62828);color:#fff;white-space:normal;max-width:calc(100vw - 32px)}.toast ha-icon{--mdc-icon-size:18px}`;
  const sheetToast = (sr, text, bad = false) => {
    let t = sr.querySelector(".toast");
    if (!t) {
      t = document.createElement("div");
      t.className = "toast";
      t.setAttribute("role", "status");
      t.setAttribute("aria-live", "polite");
      sr.appendChild(t);
    }
    t.innerHTML = `${bad ? "" : `<ha-icon icon="mdi:check"></ha-icon>`}<span>${esc(text)}</span>`;
    t.classList.toggle("bad", bad);
    t.classList.add("on");
    clearTimeout(t._t);
    t._t = setTimeout(() => t.classList.remove("on"), bad ? 4000 : 1600);
  };
  /** A part of a sheet drawn again without moving it: its scroll and the focused control stay (the old full redraw
   * threw the settings back to the top on every tap — owner 29.10). */
  const keepPlace = (sr, fn) => {
    const sh = sr.querySelector(".sh");
    const top = sh ? sh.scrollTop : 0;
    const a = sr.activeElement;
    const attr = a && [...a.attributes].find((x) => x.name.startsWith("data-"));
    const sel = attr ? `[${attr.name}${attr.value ? `="${window.CSS.escape(attr.value)}"` : ""}]` : "";
    fn();
    if (sh) sh.scrollTop = top;
    if (sel && !sr.activeElement) sr.querySelector(sel)?.focus({ preventScroll: true });
    if (sh) sh.scrollTop = top;
  };

  // --- touch discipline (owner 28.10, iPhone: «бесят случайные нажатия, когда листаешь страницу комнаты») -----------
  // 1. A finger that scrolls the page never changes anything: cards are touch-action:pan-y, a vertical gesture is a scroll.
  // 2. A tap acts only when clean: finger moved < 8 px, < 600 ms, no pointercancel (iOS sends it when it takes the gesture
  //    for a scroll), the page didn't scroll meanwhile and wasn't scrolling just before (momentum, 250 ms). One tracker for
  //    all cards: `GST.ok` for pointerup handlers, and the same verdict swallows the click of a dirty gesture on our cards.
  // 3. A value by drag: a tall tile needs a press-and-hold first (350 ms, haptic + «grab»), a horizontal bar starts only on
  //    a sideways swipe (hdrag below); a mouse keeps its direct drag.
  const GST = { type: "mouse", t: 0, up: 0, x: 0, y: 0, el: null, top: 0, bad: false, late: false, ok: true, lp: false };
  let lastScroll = 0;
  {
    const mark = () => (lastScroll = Date.now());
    window.addEventListener("scroll", mark, { capture: true, passive: true });
    window.addEventListener("wheel", mark, { capture: true, passive: true });
    const topOf = (n) => (n && n.getBoundingClientRect ? n.getBoundingClientRect().top : 0);
    window.addEventListener("pointerdown", (ev) => {
      const el = ev.composedPath()[0];
      Object.assign(GST, { type: ev.pointerType || "mouse", t: Date.now(), up: 0, x: ev.clientX, y: ev.clientY, el, top: topOf(el), bad: false, late: Date.now() - lastScroll < 250, ok: true, lp: false });
    }, { capture: true, passive: true });
    window.addEventListener("pointermove", (ev) => {
      if (Math.hypot(ev.clientX - GST.x, ev.clientY - GST.y) >= 8) GST.bad = true;
    }, { capture: true, passive: true });
    window.addEventListener("pointercancel", () => ((GST.bad = true), (GST.up = Date.now()), (GST.ok = false), mark()), { capture: true, passive: true });
    window.addEventListener("pointerup", () => {
      GST.up = Date.now();
      if (GST.bad) mark(); // a swipe ended: the page may glide on
      const moved = GST.el?.isConnected && Math.abs(topOf(GST.el) - GST.top) > 8; // the tile itself slid under the finger = a scroll
      // A long press that opened a control sheet (longPress) is no tap.
      GST.ok = GST.lp ? false : GST.type === "mouse" ? true : !GST.bad && !GST.late && GST.up - GST.t < 600 && lastScroll < GST.t && !moved;
    }, { capture: true, passive: true });
    const ours = (ev) => ev.composedPath().some((n) => typeof n.localName === "string" && (n.localName.startsWith("hauflo-") || (typeof n.className === "string" && /\bhauflo-/.test(n.className))));
    window.addEventListener("click", (ev) => {
      if (!ev.isTrusted || !ev.detail || GST.type === "mouse" || GST.ok || Date.now() - GST.up > 1000 || !ours(ev)) return;
      ev.stopPropagation();
      ev.preventDefault();
    }, { capture: true });
  }
  /** A sideways control (bar, slider, swipe handle). Touch / pen: it starts only when |dx| > 10 px and |dx| > 1.5·|dy|, else the
   * page scrolls (give the element touch-action:pan-y); a bare tap sets nothing — `tap(ev, clean)` runs instead. Mouse: from the
   * press, as before. cb: down(ev) (false = ignore), start(ev), move(ev), end(ev, committed) (committed = let go, not cancelled). */
  const hdrag = (el, cb) => {
    let p = null;
    const begin = (ev) => {
      p.go = true;
      try {
        el.setPointerCapture(p.id);
      } catch {}
      cb.start?.(ev);
      cb.move?.(ev);
    };
    el.addEventListener("pointerdown", (ev) => {
      if (ev.pointerType === "mouse" && ev.button !== 0) return;
      p = { id: ev.pointerId, x: ev.clientX, y: ev.clientY, go: false, touch: ev.pointerType !== "mouse" };
      if (cb.down?.(ev) === false) return void (p = null);
      if (!p.touch) begin(ev);
    });
    el.addEventListener("pointermove", (ev) => {
      if (!p) return;
      if (p.go) return cb.move?.(ev);
      const dx = Math.abs(ev.clientX - p.x);
      if (dx > 10 && dx > 1.5 * Math.abs(ev.clientY - p.y)) begin(ev);
    });
    const fin = (ev) => {
      if (!p) return;
      const q = p;
      p = null;
      if (q.go) cb.end?.(ev, ev.type === "pointerup");
      else cb.tap?.(ev, ev.type === "pointerup" && GST.ok);
    };
    el.addEventListener("pointerup", fin);
    el.addEventListener("pointercancel", fin);
  };

  // --- the client's own view (owner 28.10: «менять карточку как мне, так и клиенту»): scenes in their blocks or on top,
  // climate and floor heating large or a line. The integrator's choice is each card's config (the default); the person's
  // own choice is kept by HA for him (frontend user data, as his theme) — other people of the house keep theirs.
  const VIEW = { data: null, state: "", cards: new Set() };
  const viewRerender = () => {
    // Every card of the dashboard, shown or not: a hidden one (scenes that were in their blocks) may show now.
    for (const c of VIEW.cards) {
      c._sig = "";
      c._update();
    }
  };
  const viewPrefs = (card) => {
    VIEW.cards.add(card);
    const h = card._hass;
    if (!VIEW.state && h?.callWS) {
      VIEW.state = "loading";
      try {
        VIEW.data = JSON.parse(localStorage.getItem("hauflo_view") || "null");
      } catch (e) {
        VIEW.data = null;
      }
      h.callWS({ type: "frontend/get_user_data", key: "hauflo_view" })
        .then((r) => (VIEW.data = r?.value && typeof r.value === "object" ? r.value : {}))
        .catch(() => (VIEW.data = VIEW.data || {}))
        .finally(() => {
          VIEW.state = "done";
          try {
            localStorage.setItem("hauflo_view", JSON.stringify(VIEW.data));
          } catch (e) {
            /* private mode */
          }
          viewRerender();
        });
    }
    return VIEW.data || {};
  };
  /** Where a card's scenes of a kind are shown, «blocks» or «top» (owner 28.10: light on top, blinds in their block). Light
   * (and mixed) scenes: the person's choice, else the card's `scene_layout`. Blind scenes: their own choice, else the one
   * choice an older version kept (it meant both), else the card's `cover_scene_layout`, else `scene_layout`. */
  const sceneWhere = (card, kind) => {
    const p = viewPrefs(card);
    const c = card._config || {};
    return (kind === "cover" ? p.cover_scenes || p.scenes || c.cover_scene_layout || c.scene_layout : p.scenes || c.scene_layout) || "blocks";
  };
  const setViewPrefs = async (h, next) => {
    VIEW.data = next;
    try {
      localStorage.setItem("hauflo_view", JSON.stringify(next));
    } catch (e) {
      /* private mode */
    }
    viewRerender();
    await h.callWS({ type: "frontend/set_user_data", key: "hauflo_view", value: next });
  };
  // --- the client's names of the blocks (owner 28.10: «как переименовать карточку — кому-то надо так, а кому-то как мне»):
  // for the whole house, kept by the agent (/api/hauflo/titles, agent 0.14.7+) — a deploy rewrites the dashboard, the
  // names stay. A card of a block (`block: climate`, `lights`…) shows the client's name over the integrator's title.
  const TITLES = { data: null, state: "", cards: new Set() };
  const loadTitles = (h) => {
    if (TITLES.state || !h?.callApi) return;
    TITLES.state = "loading";
    try {
      TITLES.data = JSON.parse(localStorage.getItem("hauflo_titles") || "null");
    } catch (e) {
      TITLES.data = null;
    }
    h.callApi("GET", "hauflo/titles")
      .then((r) => (TITLES.data = r && typeof r.titles === "object" && r.titles ? r.titles : {}))
      // An older agent (no such view) or no link: the integrator's titles.
      .catch(() => (TITLES.data = TITLES.data || {}))
      .finally(() => {
        TITLES.state = "done";
        try {
          localStorage.setItem("hauflo_titles", JSON.stringify(TITLES.data));
        } catch (e) {
          /* private mode */
        }
        for (const c of TITLES.cards) {
          c._sig = "";
          c._update();
        }
      });
  };
  const setTitles = async (h, next) => {
    const r = await h.callApi("POST", "hauflo/titles", { titles: next });
    TITLES.data = r?.titles || {};
    try {
      localStorage.setItem("hauflo_titles", JSON.stringify(TITLES.data));
    } catch (e) {
      /* private mode */
    }
    for (const c of TITLES.cards) {
      c._sig = "";
      c._update();
    }
  };
  /** A block's title: the client's name (marked `data-own` — kept as typed, not put into English), else the config's,
   * else the card's own word. */
  const blockTitle = (card, fallback, kind = card._config?.block) => {
    if (kind) {
      TITLES.cards.add(card);
      loadTitles(card._hass);
    }
    const own = kind && TITLES.data?.[kind];
    return own ? `<span data-own>${esc(own)}</span>` : esc((kind === card._config?.block && card._config?.title) || fallback);
  };
  // --- the client's order of a block's items (owner 28.10: «не понял, как поменять последовательность элементов любого
  // блока… надо дать возможность перетаскивать, чтобы выстраивалось как надо»): for the whole house, kept by the agent
  // (/api/hauflo/order, agent 0.16.2+) over deploys. A block = this dashboard + view + the card's slot (agent order.py
  // `slot`): the phone and tablet copies of a block share it; the room tiles of a page are one block (by their paths).
  // Applied over the cloud's order — what isn't in it (added later) goes at the end, what's gone is dropped. Changed in a
  // sheet (drag the handle with a finger or the mouse, or ↑ ↓), opened by a long press on a block's title or from
  // «Настройки» → «Порядок элементов». Self-contained: the cards only call orderApply / orderAfter / orderIds.
  const ORDER = { data: null, state: "", ok: false, wait: null, cards: new Set() };
  const ORDER_TYPES = new Set(["lights", "covers", "sliders", "scenes", "climate", "acs", "devices", "status", "area", "alert"]);
  const ORDER_FIELDS = ["groups", "entities", "zones", "scenes", "alarms", "sensors"];
  const OW = {
    ru: {
      title: "Порядок", hint: "Перетащите за ⠿ пальцем или мышью, или нажимайте ↑ ↓. Сохраняется сразу — для всего дома и при следующих выкатках.",
      done: "Готово", saved: "Сохранено", reset: "Как в Hauflo", up: "Выше", down: "Ниже", close: "Закрыть",
      error: "Не сохранилось — нет связи с домом или агент Hauflo устарел (нужен 0.16.2+).",
      groups: "Группы", scenes: "Сценарии", alarms: "Тревоги", menu: "Порядок элементов", menuHint: "Блоки этой страницы. Ещё можно долго нажать на заголовок блока.",
      names: { lights: "Свет", covers: "Шторы", sliders: "Свет", scenes: "Сценарии", climate: "Тёплый пол", acs: "Кондиционеры", devices: "Устройства", status: "Датчики", area: "Комната", alert: "Тревоги", room: "Комнаты" },
      items: (n) => `${n} ${n % 10 === 1 && n % 100 !== 11 ? "элемент" : n % 10 >= 2 && n % 10 <= 4 && (n % 100 < 10 || n % 100 >= 20) ? "элемента" : "элементов"}`,
    },
    en: {
      title: "Order", hint: "Drag by ⠿ with a finger or the mouse, or tap ↑ ↓. Saved at once — for the whole house, and it stays over the next deploys.",
      done: "Done", saved: "Saved", reset: "Hauflo's order", up: "Up", down: "Down", close: "Close",
      error: "Not saved — no link to the house, or the Hauflo agent is out of date (0.16.2+ needed).",
      groups: "Groups", scenes: "Scenes", alarms: "Alarms", menu: "Order of items", menuHint: "The blocks of this page. Or long-press a block's title.",
      names: { lights: "Lights", covers: "Blinds", sliders: "Lights", scenes: "Scenes", climate: "Floor heating", acs: "Air conditioners", devices: "Devices", status: "Sensors", area: "Room", alert: "Alarms", room: "Rooms" },
      items: (n) => `${n} item${n === 1 ? "" : "s"}`,
    },
  };
  const itemId = (x) => (typeof x === "string" ? x : x && typeof x === "object" && typeof x.entity === "string" ? x.entity : undefined);
  /** The card's slot on its view — the same as the agent's (order.py `slot`). */
  const orderSlot = (c) => {
    const t = String(c.type).replace(/^custom:hauflo-/, "");
    if (t === "room" && c.path) return "room:tiles";
    const title = typeof c.title === "string" ? c.title : "";
    // Untitled light and blind sliders of one room are two blocks: by the domain of their first item.
    const first = !title && Array.isArray(c.entities) ? itemId(c.entities[0]) : undefined;
    const k = [c.block, c.kind, Array.isArray(c.zones) && c.zones.length ? "zones" : "", c.area, typeof first === "string" ? first.split(".")[0] : ""].find((x) => typeof x === "string" && x) || "";
    return `${t}:${k}:${title}`;
  };
  /** The block's key — found once the card is on its page (HA builds a view's cards for that view's URL). */
  const orderKey = (card) => {
    if (card._okeyRaw === card._raw && card._okey !== undefined) return card._okey;
    const c = card._raw;
    if (!c || typeof c.type !== "string" || !card.isConnected) return "";
    const t = c.type.replace(/^custom:hauflo-/, "");
    let key = "";
    if (ORDER_TYPES.has(t) || (t === "room" && c.path)) {
      const p = location.pathname.split("/");
      let dash = "";
      let view = "";
      try {
        dash = decodeURIComponent(p[1] || "");
        view = decodeURIComponent(p[2] || "");
      } catch (e) {
        /* a broken URL: no order */
      }
      if (/^hauflo-/.test(dash) && !/-copy-\d/.test(dash) && view) key = `${dash}|${view}|${orderSlot(c)}`;
    }
    card._okey = key;
    card._okeyRaw = c;
    return key;
  };
  const orderSave = () => {
    try {
      localStorage.setItem("hauflo_order", JSON.stringify(ORDER.data));
    } catch (e) {
      /* private mode */
    }
  };
  const orderRedraw = () => {
    for (const c of ORDER.cards) {
      c._sig = "";
      c._update();
    }
  };
  const loadOrder = (h) => {
    if (ORDER.state || !h?.callApi) return ORDER.wait;
    ORDER.state = "loading";
    try {
      ORDER.data = JSON.parse(localStorage.getItem("hauflo_order") || "null");
    } catch (e) {
      ORDER.data = null;
    }
    ORDER.wait = h
      .callApi("GET", "hauflo/order")
      .then((r) => {
        ORDER.data = r && typeof r.order === "object" && r.order ? r.order : {};
        ORDER.ok = true;
      })
      // An older agent (no such view) or no link: Hauflo's order, and nothing to change it with.
      .catch(() => (ORDER.data = ORDER.data || {}))
      .finally(() => {
        ORDER.state = "done";
        orderSave();
        orderRedraw();
      });
    return ORDER.wait;
  };
  /** A list in the block's order: the kept ones first, as kept; the rest after them, as they came. */
  const orderSort = (list, arr, idOf = itemId) => {
    if (!Array.isArray(list) || !list.length || !Array.isArray(arr)) return arr;
    const rank = new Map(list.map((id, i) => [id, i]));
    return arr
      .map((x, i) => [x, rank.has(idOf(x)) ? rank.get(idOf(x)) : 1e6 + i])
      .sort((a, b) => a[1] - b[1])
      .map(([x]) => x);
  };
  /** Before a card draws: its config's lists in the block's order (a copy; the config itself is left as it is). */
  const orderApply = (card) => {
    const c = card._raw;
    if (!c) return;
    const key = orderKey(card);
    if (key) {
      ORDER.cards.add(card);
      loadOrder(card._hass);
    }
    const list = key ? ORDER.data?.[key] : undefined;
    const sig = Array.isArray(list) && list.length ? list.join("\n") : "";
    if (card._osig === sig && card._oraw === c) return;
    card._osig = sig;
    card._oraw = c;
    if (!sig) {
      card._config = c;
      return;
    }
    const out = { ...c };
    for (const f of ORDER_FIELDS) if (Array.isArray(c[f])) out[f] = orderSort(list, c[f]);
    if (Array.isArray(c.rows)) out.rows = c.rows.map((r) => (r && Array.isArray(r.scenes) ? { ...r, scenes: orderSort(list, r.scenes) } : r));
    card._config = out;
  };
  /** Ids a card finds itself (an area's entities) in the block's order. */
  const orderIds = (card, ids) => orderSort(ORDER.data?.[orderKey(card)], ids, (x) => x);
  const orderHas = (card) => !!ORDER.data?.[orderKey(card)]?.length;
  /** The cell of HA's grid a card sits in (a room tile is a card of its own: the tiles are ordered by CSS `order`). */
  const orderCell = (card) => {
    let el = card;
    for (let n = 0; n < 5 && el; n++) {
      const up = el.parentElement || el.getRootNode?.()?.host || null;
      if (!up || /^HUI-CARD-OPTIONS$|^HUI-SECTION-EDIT/.test(up.tagName)) return null; // the dashboard being edited
      const d = getComputedStyle(up).display;
      if (d === "grid" || d === "inline-grid" || d === "flex") return el;
      el = up;
    }
    return null;
  };
  /** After a card drew (and when it's put on the page): a room tile takes its place; a block's title opens the sheet on a long press. */
  const orderAfter = (card) => {
    const key = orderKey(card);
    if (!key) return;
    ORDER.cards.add(card);
    if (card._raw?.type === "custom:hauflo-room") {
      const list = ORDER.data?.[key] || [];
      const i = list.indexOf(card._raw.path);
      const cell = orderCell(card);
      if (cell) cell.style.order = list.length ? String(i >= 0 ? i : list.length) : "";
      return;
    }
    const head = card.shadowRoot?.querySelector(".caps");
    if (!head || head._orderPress) return;
    head._orderPress = true;
    let t = 0;
    let at = null;
    const stop = () => {
      clearTimeout(t);
      at = null;
    };
    head.addEventListener("pointerdown", (e) => {
      if (!ORDER.ok || (e.pointerType === "mouse" && e.button !== 0)) return;
      at = [e.clientX, e.clientY];
      clearTimeout(t);
      t = setTimeout(() => {
        at = null;
        if (orderSections(card).every((s) => s.items.length < 2)) return;
        haptic(card, "medium");
        if (!canEdit(card._hass)) {
          card.dispatchEvent(new CustomEvent("hass-notification", { bubbles: true, composed: true, detail: { message: tr("Порядок для всего дома меняют взрослые — у вас детский профиль") } }));
          return;
        }
        // The tap that ends the press isn't a tap on the block, nor on the sheet's backdrop.
        const swallow = (ev) => {
          ev.stopPropagation();
          ev.preventDefault();
        };
        window.addEventListener("click", swallow, { capture: true, once: true });
        head.addEventListener("pointerup", () => setTimeout(() => window.removeEventListener("click", swallow, { capture: true }), 400), { once: true });
        openOrder(card);
      }, 600);
    });
    head.addEventListener("pointermove", (e) => at && Math.hypot(e.clientX - at[0], e.clientY - at[1]) > 10 && stop());
    head.addEventListener("pointerup", stop);
    head.addEventListener("pointercancel", stop);
    head.addEventListener("contextmenu", (e) => ORDER.ok && e.preventDefault());
  };
  /** What the sheet lists: a section per list of the card (groups, items, scenes…), each item with its name and icon. */
  const orderSections = (card) => {
    const h = card._hass;
    const w = OW[langOf(h)];
    const c = card._config || {};
    if (card._raw?.type === "custom:hauflo-room") {
      const key = orderKey(card);
      const seen = new Set();
      const tiles = [...ORDER.cards]
        .filter((x) => x.isConnected && orderKey(x) === key && !seen.has(x._raw.path) && seen.add(x._raw.path))
        .map((x) => ({ id: x._raw.path, name: x._raw.name || x._raw.path, icon: x._raw.icon || "" }));
      return [{ label: "", items: orderSort(ORDER.data?.[key], tiles, (x) => x.id) }];
    }
    const it = (x) => {
      const id = itemId(x);
      const s = st(h, id);
      if (!s) return null;
      const a = areaOf(h, id);
      return { id, name: FAM.names?.[id] || (x && typeof x === "object" && x.name) || (typeof card.label === "function" ? card.label(id) : nameOf(s, id)), icon: (x && typeof x === "object" && x.icon) || "", room: (a && h?.areas?.[a]?.name) || "" }; // the card's own icon only: a device's raw one looked out of place
    };
    const secs = [];
    const add = (label, arr) => {
      const items = (Array.isArray(arr) ? arr : []).map(it).filter(Boolean);
      if (items.length) secs.push({ label, items });
    };
    if (typeof card.members === "function") {
      // An area card: a section per group it draws (climate, switches, values…), in its order.
      const ids = orderIds(card, card.members()).filter((id) => st(h, id));
      const by = new Map();
      for (const id of ids) {
        const k = typeof card.kind === "function" ? card.kind(id) : "";
        by.set(k, [...(by.get(k) || []), id]);
      }
      for (const list of by.values()) add("", list);
    }
    add(w.groups, c.groups);
    add("", c.entities);
    add("", c.zones);
    add("", c.sensors);
    add(c.type === "custom:hauflo-alert" ? "" : w.alarms, c.alarms);
    // Scenes with the family's own ones of the room (agent scenes.py) after the integrator's, as the block draws them.
    // KNX → the integrator's HA → the family's (Д19), as sceneChips.
    const withFamily = (arr, room, kind) => orderSort(ORDER.data?.[orderKey(card)], bySource(h, [...(arr || []), ...(room ? familyScenes(h, room, kind).filter((x) => !(arr || []).some((y) => itemId(y) === x.entity)) : [])]));
    if (Array.isArray(c.rows)) for (const r of c.rows) add(r?.title || (r?.kind === "cover" ? w.names.covers : w.names.lights), withFamily(r?.scenes, r?.scene_room, r?.kind === "cover" ? "cover" : "light"));
    add(secs.length ? w.scenes : "", withFamily(c.scenes, c.scene_room, card.kind === "cover" || c.kind === "cover" ? "cover" : "light"));
    if (secs.length === 1) secs[0].label = "";
    // Items from two or more rooms («Ошибка тёплого пола 1» in a house-wide list): the room as a second line, unless the
    // name says it — the name and where it is, like Apple Home's order list and HA's entity picker (owner 29.10).
    for (const sec of secs) {
      if (new Set(sec.items.map((x) => x.room).filter(Boolean)).size < 2) continue;
      for (const x of sec.items) if (x.room && !String(x.name).toLowerCase().includes(x.room.toLowerCase())) x.sub = x.room;
    }
    return secs;
  };
  /** The block's name in the sheet: the house's title for it, the integrator's, else the card's own word. */
  const orderName = (card) => {
    const c = card._raw || {};
    const w = OW[langOf(card._hass)];
    const t = String(c.type || "").replace(/^custom:hauflo-/, "");
    const own = c.block && TITLES.data?.[c.block];
    if (own) return own;
    if (t === "area") return c.title || card._hass?.areas?.[c.area]?.name || w.names.area;
    if (t === "sliders") return JSON.stringify(c.entities || []).includes('"cover.') ? w.names.covers : w.names.lights;
    return c.title || c.label || w.names[t] || t;
  };
  /** This page's blocks that can be ordered (one per block — a tablet copy is the same block), top to bottom. */
  const orderBlocksHere = () => {
    const by = new Map();
    for (const card of ORDER.cards) {
      const key = card.isConnected && orderKey(card);
      if (!key) continue;
      const shown = card.getBoundingClientRect().height > 0;
      const have = by.get(key);
      if (have && (have.shown || !shown)) continue;
      const n = orderSections(card).reduce((a, s) => a + s.items.length, 0);
      if (n >= 2) by.set(key, { card, shown, n, top: card.getBoundingClientRect().top });
    }
    return [...by.values()].sort((a, b) => Number(b.shown) - Number(a.shown) || a.top - b.top);
  };
  const OCSS = `:host{--hf-toast-b:96px;position:fixed;inset:0;z-index:10000;display:grid;place-items:end center;background:rgba(0,0,0,.42);font-family:Onest,var(--ha-font-family-body),system-ui,sans-serif;color:var(--eh-ink,var(--primary-text-color));-webkit-tap-highlight-color:transparent}
@media (min-width:600px){:host{place-items:center}}
*{box-sizing:border-box}
.sh{width:min(100%,480px);max-height:92vh;overflow:auto;overscroll-behavior:contain;background:var(--card-background-color,#fff);background-image:var(--eh-glass,none);border-radius:28px 28px 0 0;padding:20px 16px calc(16px + env(safe-area-inset-bottom));box-shadow:0 20px 60px rgba(0,0,0,.35)}
@media (min-width:600px){.sh{border-radius:28px;padding:20px}}
.hd{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:4px}.hd h2{font:600 20px/1.2 Onest,sans-serif;margin:0;letter-spacing:-.01em;min-width:0;overflow-wrap:break-word;hyphens:auto}
.x{width:44px;height:44px;flex:none;border-radius:22px;border:0;background:var(--eh-track,rgba(127,127,127,.12));color:inherit;font-size:22px;cursor:pointer;display:grid;place-items:center}.x ha-icon{--mdc-icon-size:22px}
.hint{font:400 13px/1.45 Onest,sans-serif;color:var(--eh-ink2,var(--secondary-text-color));margin:4px 2px 10px}
.vl{font:500 11px/1.2 Onest,sans-serif;letter-spacing:.14em;text-transform:uppercase;color:var(--eh-ink2,var(--secondary-text-color));margin:16px 2px 6px}
ol{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:6px}
li{display:flex;align-items:center;gap:8px;min-height:56px;padding:4px 4px 4px 0;border-radius:16px;background:var(--eh-track,rgba(127,127,127,.10));user-select:none;-webkit-user-select:none;transition:box-shadow .15s,transform .15s}
li.drag{background:var(--card-background-color,#fff);box-shadow:0 8px 24px rgba(0,0,0,.25),inset 0 0 0 2px var(--eh-ice,#3C82F0);transform:scale(1.02);position:relative;z-index:1}
.gr{flex:none;width:44px;height:48px;display:grid;place-items:center;color:var(--eh-ink2,var(--secondary-text-color));cursor:grab;touch-action:none}.gr:active{cursor:grabbing}
.ic{--mdc-icon-size:20px;color:var(--eh-ink2,var(--secondary-text-color));flex:none}
.tx{flex:1;min-width:0;display:flex;flex-direction:column;gap:2px}.sb{font:400 13px/1.3 Onest,sans-serif;color:var(--eh-ink2,var(--secondary-text-color));overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.nm{flex:1;min-width:0;font:500 15px/1.3 Onest,sans-serif;overflow:hidden;text-overflow:ellipsis;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow-wrap:break-word;hyphens:auto}
.mv{flex:none;width:44px;height:44px;border:0;border-radius:14px;background:none;color:inherit;font:500 18px Onest,sans-serif;cursor:pointer}.mv:disabled{opacity:.25;cursor:default}.mv:not(:disabled):hover{background:rgba(127,127,127,.14)}
button:focus-visible{outline:2px solid var(--eh-ice,#3C82F0);outline-offset:1px}
.res{font:400 13px/1.45 Onest,sans-serif;margin:10px 2px 0;color:var(--error-color,#c62828)}.res:empty{display:none}
.ft{display:flex;align-items:center;gap:8px;margin:16px -16px 0;position:sticky;bottom:calc(-16px - env(safe-area-inset-bottom));padding:10px 16px calc(4px + env(safe-area-inset-bottom));background:var(--card-background-color,#fff);border-top:1px solid var(--divider-color,rgba(127,127,127,.18))}.ft .sp{flex:1}
@media (min-width:600px){.ft{margin:16px -20px 0;padding:12px 20px 0;bottom:-20px}}
.ft button{min-height:44px;padding:0 16px;border:0;border-radius:14px;font:500 14px Onest,sans-serif;color:inherit;cursor:pointer;background:var(--eh-track,rgba(127,127,127,.12))}
.ft .b1{background:var(--eh-ink,var(--primary-text-color));color:var(--card-background-color,#fff);font-weight:600}.ft .lnk{background:none;padding:0 8px;color:var(--eh-ink2,var(--secondary-text-color));text-decoration:underline;text-underline-offset:3px}
.ft button:disabled{opacity:.5}`;
  let orderOpen = null;
  /** The sheet: the block's items in its order — drag by the handle (pointer events: a finger, a pen, the mouse) or ↑ ↓.
   * Each change is kept for the house at once (a short pause after the last move) with a small «Сохранено»; the list is
   * drawn again in place (its scroll and the focused arrow stay); «Готово» only closes — back to «Настройки» when it was
   * opened from there (owner 29.10: «сохранение выходит из настроек — бесит»). */
  const openOrder = (card) => {
    const key = orderKey(card);
    if (orderOpen || !key) return;
    const h = card._hass;
    const w = OW[langOf(h)];
    let secs = orderSections(card).map((s) => ({ ...s, items: [...s.items] }));
    const host = document.createElement("div");
    host.className = "hauflo-order";
    const sr = host.attachShadow({ mode: "open" });
    orderOpen = host;
    let saveT = 0;
    let dirty = false;
    let inflight = null;
    const ids = () => [...new Set(secs.flatMap((s) => s.items.map((x) => x.id)))];
    const save = (items) => {
      clearTimeout(saveT);
      dirty = false;
      const run = (inflight || Promise.resolve()).then(async () => {
        try {
          const r = await h.callApi("POST", "hauflo/order", { key, items });
          ORDER.data = r?.order || {};
          orderSave();
          orderRedraw();
          if (orderOpen === host) {
            sheetToast(sr, w.saved);
            const reset = sr.querySelector("[data-reset]");
            if (reset) reset.hidden = !ORDER.data?.[key];
          }
          return true;
        } catch (e) {
          dirty = true;
          if (orderOpen === host) sheetToast(sr, w.error, true);
          return false;
        }
      });
      inflight = run.finally(() => inflight === run && (inflight = null));
      return run;
    };
    const changed = () => {
      dirty = true;
      clearTimeout(saveT);
      saveT = setTimeout(() => save(ids()), 700);
    };
    const close = () => {
      clearTimeout(saveT);
      host.remove();
      orderOpen = null;
      document.removeEventListener("keydown", onKey);
    };
    // Closing (✕, «Готово», the backdrop, Escape) keeps what was done: a move still waiting goes now, the sheet closes
    // once it's kept — a failed one keeps it open with the error over it (never a silent loss).
    const done = async () => {
      const ok = dirty ? await save(ids()) : inflight ? await inflight : true;
      if (ok !== false && orderOpen === host) close();
    };
    const onKey = (e) => e.key === "Escape" && done();
    document.addEventListener("keydown", onKey);
    const row = (x, i, n) =>
      `<li data-id="${esc(x.id)}"><span class="gr" data-grip aria-hidden="true"><ha-icon icon="mdi:drag"></ha-icon></span>${x.icon ? `<ha-icon class="ic" icon="${esc(x.icon)}"></ha-icon>` : ""}<span class="tx"><span class="nm">${esc(x.name)}</span>${x.sub ? `<span class="sb">${esc(x.sub)}</span>` : ""}</span><button class="mv" data-up="${esc(x.id)}" aria-label="${esc(`${w.up}: ${x.name}`)}"${i ? "" : " disabled"}>↑</button><button class="mv" data-down="${esc(x.id)}" aria-label="${esc(`${w.down}: ${x.name}`)}"${i < n - 1 ? "" : " disabled"}>↓</button></li>`;
    /** The lists drawn again where they are: the sheet keeps its scroll, the arrow just pressed keeps the focus. */
    const lists = (focus) => {
      keepPlace(sr, () => {
        sr.querySelector("[data-lists]").innerHTML = secs.map((s, si) => `${s.label ? `<p class="vl">${esc(s.label)}</p>` : ""}<ol data-s="${si}">${s.items.map((x, i) => row(x, i, s.items.length)).join("")}</ol>`).join("");
        bindLists();
        if (focus) sr.querySelector(`li[data-id="${window.CSS.escape(focus.id)}"] [data-${focus.which}]`)?.focus({ preventScroll: true });
      });
      if (focus) sr.querySelector(`li[data-id="${window.CSS.escape(focus.id)}"]`)?.scrollIntoView({ block: "nearest" });
    };
    const bindLists = () => {
      sr.querySelectorAll("ol").forEach((ol) => {
        const s = secs[Number(ol.dataset.s)];
        ol.querySelectorAll("li").forEach((li, i) => {
          const move = (by, which) => {
            const j = i + by;
            if (j < 0 || j >= s.items.length) return;
            [s.items[i], s.items[j]] = [s.items[j], s.items[i]];
            haptic(card, "selection");
            lists({ id: s.items[j].id, which: j === 0 ? "down" : j === s.items.length - 1 ? "up" : which });
            changed();
          };
          li.querySelector("[data-up]").onclick = () => move(-1, "up");
          li.querySelector("[data-down]").onclick = () => move(1, "down");
          li.querySelector("[data-grip]").addEventListener("pointerdown", (e) => {
            if (e.pointerType === "mouse" && e.button !== 0) return;
            e.preventDefault();
            li.classList.add("drag");
            const sh = sr.querySelector(".sh");
            const before0 = [...ol.children].map((o) => o.dataset.id).join("\n");
            const onMove = (ev) => {
              ev.preventDefault();
              const y = ev.clientY;
              let before = null;
              for (const o of ol.children) {
                if (o === li) continue;
                const r = o.getBoundingClientRect();
                if (y < r.top + r.height / 2) {
                  before = o;
                  break;
                }
              }
              if (before !== li.nextElementSibling && before !== li) ol.insertBefore(li, before);
              // Near the sheet's edge: it scrolls (a long list on a phone).
              const b = sh.getBoundingClientRect();
              if (y < b.top + 48) sh.scrollTop -= 12;
              else if (y > b.bottom - 72) sh.scrollTop += 12;
            };
            const onUp = () => {
              document.removeEventListener("pointermove", onMove);
              document.removeEventListener("pointerup", onUp);
              document.removeEventListener("pointercancel", onUp);
              const byId = new Map(s.items.map((x) => [x.id, x]));
              s.items = [...ol.children].map((o) => byId.get(o.dataset.id)).filter(Boolean);
              haptic(card);
              lists();
              if ([...ol.children].map((o) => o.dataset.id).join("\n") !== before0) changed();
            };
            document.addEventListener("pointermove", onMove, { passive: false });
            document.addEventListener("pointerup", onUp);
            document.addEventListener("pointercancel", onUp);
          });
        });
      });
    };
    sr.innerHTML = `<style>${TZ(OCSS + TOAST_CSS)}</style><div class="sh" role="dialog" aria-modal="true" aria-label="${esc(`${w.title}: ${orderName(card)}`)}">
<div class="hd"><h2>${esc(w.title)} · ${esc(orderName(card))}</h2><button class="x" data-x aria-label="${esc(w.close)}"><ha-icon icon="mdi:close"></ha-icon></button></div>
<p class="hint">${esc(w.hint)}</p>
<div data-lists></div>
<div class="ft"><button class="lnk" data-reset${ORDER.data?.[key] ? "" : " hidden"}>${esc(w.reset)}</button><span class="sp"></span><button class="b1" data-done>${esc(w.done)}</button></div></div>`;
    lists();
    sr.querySelector("[data-x]").onclick = done;
    sr.querySelector("[data-done]").onclick = done;
    sr.querySelector("[data-reset]").onclick = async () => {
      clearTimeout(saveT);
      dirty = false;
      if (!(await save([]))) return;
      secs = orderSections(card).map((s) => ({ ...s, items: [...s.items] }));
      if (orderOpen === host) lists();
    };
    // A tap on the dim backdrop closes — not the click that ends the long press which opened it (it lands on the backdrop).
    const opened = Date.now();
    host.addEventListener("click", (e) => e.composedPath()[0] === host && Date.now() - opened > 700 && done());
    document.body.appendChild(host);
    sr.querySelector("[data-done]")?.focus({ preventScroll: true });
  };
  // --- the family's own room scenes (owner 28.10: «сцены, как и сцены света, должны создаваться и редактироваться мной и
  // клиентом»; agent scenes.py, 0.16.0+): a block with `scene_room` gets «+ Сцена» → a name → «Запомнить как сейчас»;
  // a long press on a scene Hauflo made (the integrator's or the family's) → rename / remember again / delete (hide).
  // `api`: this HA's agent answers /api/hauflo/scenes (true) or not (false — an HA not restarted since the update,
  // or the cards without their agent); null until asked. «+ Сцена» shows only with it.
  // `knx`: Hauflo's own KNX scenes the agent can learn (entity → scene number; agent ≥ 0.16.5), `names`: the family's
  // names of KNX scenes on the panel.
  const FAM = { hidden: null, state: "", cards: new Set(), api: null, knx: {}, names: {} };
  const famRerender = () => {
    for (const c of FAM.cards) {
      c._sig = "";
      c._update();
    }
  };
  const loadFam = (h) => {
    if (FAM.state || !h?.callApi) return;
    FAM.state = "loading";
    h.callApi("GET", "hauflo/scenes")
      .then((r) => {
        FAM.hidden = Array.isArray(r?.hidden) ? r.hidden : [];
        FAM.knx = r?.knx && typeof r.knx === "object" ? r.knx : {};
        FAM.names = r?.names && typeof r.names === "object" ? r.names : {};
        FAM.api = true;
      })
      .catch(() => {
        FAM.hidden = [];
        FAM.api = false;
      })
      .finally(() => {
        FAM.state = "done";
        famRerender();
      });
  };
  /** The room's scenes the family saved on the dashboard (`hauflo_fam_<room>_<l|c>_<time>`), oldest first. */
  // All of them once per states object (HA replaces it on any change) — not a walk over every state per card and update.
  const FAM_ALL = new WeakMap();
  const allFamily = (hass) => {
    const states = hass?.states;
    if (!states) return [];
    if (!FAM_ALL.has(states))
      FAM_ALL.set(
        states,
        Object.values(states)
          .filter((s) => s.entity_id.startsWith("scene.") && String(s.attributes.id || "").startsWith("hauflo_fam_"))
          .sort((a, b) => String(a.attributes.id).localeCompare(String(b.attributes.id))),
      );
    return FAM_ALL.get(states);
  };
  const familyScenes = (hass, room, kind) => {
    const prefix = `hauflo_fam_${String(room).replace(/[^a-z0-9]/g, "")}_${kind === "cover" ? "c" : "l"}_`;
    return allFamily(hass)
      .filter((s) => String(s.attributes.id).startsWith(prefix))
      .map((s) => ({ entity: s.entity_id }));
  };
  // --- KNX and HA scenes on one panel (Д19, owner 28.10): KNX ones (from the wall switches) first, then the integrator's
  // HA ones, then the family's; the family sees no marks, an HA admin a small «KNX» / «HA» in the corner.
  const sceneSource = (hass, id) => {
    if (hass?.entities?.[id]?.platform === "knx") return "knx";
    return /^hauflo_fam_/.test(String(st(hass, id)?.attributes?.id || "")) ? "fam" : "ha";
  };
  const SOURCE_RANK = { knx: 0, ha: 1, fam: 2 };
  /** A block's scenes in their base order (stable within a source) — the client's «Порядок» goes over it. */
  const bySource = (hass, arr, idOf = itemId) =>
    (arr || [])
      .map((x, i) => [x, SOURCE_RANK[sceneSource(hass, idOf(x))] * 1e4 + i])
      .sort((a, b) => a[1] - b[1])
      .map(([x]) => x);
  /** The scene's name on the panel: the family's own for a KNX scene (agent scenes.py `names`), else the card's / HA's. */
  const sceneLabel = (hass, x) => FAM.names?.[x.entity] || x.name || nameOf(st(hass, x.entity), x.entity);
  // --- the room key without a redeploy (owner 28.10: «где плюс на шторах?» — the dashboard was built before the agent
  // could save scenes, so its config had no `scene_room`): these cards come with the agent, so they know it can; the
  // key is the same one the cloud writes (`short(room.id)`), read from the room's own Hauflo scenes — `hauflo_<room>_…`
  // of the integrator's, `hauflo_fam_<room>_…` of the family's — on this block, else in the same HA area.
  const ROOM_OF_SCENE = /^(?:hauflo|easyha)_(?:fam_)?([a-z0-9]{1,12})_/;
  const roomOfSceneId = (s) => ROOM_OF_SCENE.exec(String(s?.attributes?.id || ""))?.[1];
  const areaOf = (hass, id) => {
    const e = hass?.entities?.[id];
    return e?.area_id || (e?.device_id && hass?.devices?.[e.device_id]?.area_id) || null;
  };
  const areaName = (hass, id) => {
    const a = areaOf(hass, id);
    return (a && hass?.areas?.[a]?.name) || "";
  };
  // «Клапан 2.5» in a list of valves from many rooms: the room as a second line, unless the name already says it
  // (short names are the integrator's style; the room is context, never part of the stored name).
  const roomLine = (hass, id, name) => {
    const a = areaOf(hass, id);
    const r = a && hass?.areas?.[a]?.name;
    return r && !String(name || "").toLowerCase().includes(String(r).toLowerCase()) ? `<div class="rm">${esc(r)}</div>` : "";
  };
  const AREA_ROOM = new WeakMap();
  /** HA area → the room key of the Hauflo scenes in it (once per states object). */
  const areaRooms = (hass) => {
    const states = hass?.states;
    if (!states) return new Map();
    if (!AREA_ROOM.has(states)) {
      const m = new Map();
      for (const s of Object.values(states)) {
        if (!s.entity_id.startsWith("scene.")) continue;
        const room = roomOfSceneId(s);
        const area = room && areaOf(hass, s.entity_id);
        if (area && !m.has(area)) m.set(area, room);
      }
      AREA_ROOM.set(states, m);
    }
    return AREA_ROOM.get(states);
  };
  const entIds = (list) => (list || []).map((e) => (typeof e === "string" ? e : e?.entity)).filter(Boolean);
  /** The room key of a scene row: the config's, else its scenes', else its lights' / blinds' area's. */
  const derivedRoom = (hass, configured, scenes, entities) => {
    if (configured) return configured;
    if (!hass) return undefined;
    for (const x of scenes || []) {
      const r = roomOfSceneId(st(hass, x.entity));
      if (r) return r;
    }
    const rooms = areaRooms(hass);
    for (const id of [...entIds(entities), ...(scenes || []).flatMap((x) => st(hass, x.entity)?.attributes?.entity_id || [])]) {
      const area = areaOf(hass, id);
      if (area && rooms.has(area)) return rooms.get(area);
    }
    return undefined;
  };
  /** A block's (`kind`) or a top card row's room key. */
  const rowRoom = (card, kind) => {
    const c = card._config || {};
    if (Array.isArray(c.rows)) {
      const r = c.rows.find((x) => x.kind === kind);
      return r ? derivedRoom(card._hass, r.scene_room, r.scenes, r.capture) : undefined;
    }
    return derivedRoom(card._hass, c.scene_room, c.scenes, [...(c.entities || []), ...(c.capture || [])]);
  };
  /** The family scenes a card with `scene_room` shows — watched, so one saved on another phone shows here too. */
  const famWatched = (card) => {
    const c = card._config || {};
    // Only the cards that show a scene row: the room's top card and the light / blinds blocks.
    const rooms = Array.isArray(c.rows) ? c.rows.filter((r) => r.kind !== "mixed").map((r) => rowRoom(card, r.kind)) : typeof card.sceneRow === "function" ? [rowRoom(card, card.kind === "cover" ? "cover" : "light")] : [];
    if (!rooms.some(Boolean)) return [];
    const keys = rooms.filter(Boolean);
    if (!rooms.length || !card._hass) return [];
    FAM.cards.add(card);
    loadFam(card._hass);
    return allFamily(card._hass)
      .filter((s) => keys.some((r) => String(s.attributes.id).startsWith(`hauflo_fam_${r}_`)))
      .map((s) => s.entity_id);
  };
  /** Scene buttons of one kind (light amber, blinds neutral, both mint) — the room's top card and the blocks share them.
   * `room` (the card's `scene_room`): the family's own scenes after the integrator's, and «+ Сцена». */
  const sceneChips = (hass, list, kind, room, card) => {
    const own = room && hass ? familyScenes(hass, room, kind) : [];
    const hidden = new Set(FAM.hidden || []);
    // KNX scenes can be learnt / renamed wherever they are (no room needed): the agent says which (FAM.knx).
    if (card && hass && (list || []).some((x) => sceneSource(hass, x.entity) === "knx")) {
      FAM.cards.add(card);
      loadFam(hass);
    }
    const admin = hass?.user?.is_admin === true;
    // KNX → the integrator's HA → the family's, then the block's order («Порядок», `card` — the block) over it.
    const base = bySource(hass, [...(list || []), ...own.filter((x) => !(list || []).some((y) => y.entity === x.entity))], (x) => x.entity);
    const all = orderSort(card && ORDER.data?.[orderKey(card)], base, (x) => x.entity).filter((x) => st(hass, x.entity) && !hidden.has(x.entity));
    const one = (x, shortLab) => {
        const s = st(hass, x.entity);
        const src = sceneSource(hass, x.entity);
        const label = sceneLabel(hass, x);
        const editable = src === "knx" ? FAM.api === true && FAM.knx?.[x.entity] !== undefined : /^hauflo_/.test(String(s.attributes.id || "")) && !!room;
        const tag = admin ? `<i class="src" aria-hidden="true">${src === "knx" ? "KNX" : "HA"}</i>` : "";
        const lit = card?._sceneLit?.id === x.entity && card._sceneLit.until > Date.now();
        return `<button data-scene="${esc(x.entity)}" ${lit ? 'class="lit" ' : ""}${editable ? `data-edit="${src === "knx" ? "knx" : "1"}"` : ""} title="${esc(label)}"><ha-icon icon="${esc(x.icon || s.attributes.icon || (kind === "cover" ? "hauflo:blind" : "hauflo:scene"))}"></ha-icon><span${FAM.names?.[x.entity] ? " data-own" : ""}>${esc(shortLab || label)}</span>${tag}</button>`;
    };
    const addBtn = room && kind !== "mixed" && FAM.api === true && canEdit(hass) ? `<button class="add" data-scene-add="${kind === "cover" ? "cover" : "light"}" aria-label="Новая сцена" title="Новая сцена"><ha-icon icon="mdi:plus"></ha-icon><span>Сцена</span></button>` : "";
    // ≥2 KNX series in the row (`series` on the buttons, set by the generator): a subheader per series; the integrator's HA
    // scenes and the family's own go under «Мои сцены» (Д2+, owner 29.10). One series / none: as before, no subheader.
    if (all.some((x) => x.series)) {
      // `series_head: false` — the room's own only series (0.16.19): no subheader, first.
      const bare = (n) => all.some((x) => x.series === n && x.series_head === false);
      const names = [...new Set(all.filter((x) => x.series).map((x) => x.series))].sort((a, b) => Number(!bare(a)) - Number(!bare(b)));
      const mine = all.filter((x) => !x.series);
      const groups = names.map((n) => [bare(n) ? "" : n, all.filter((x) => x.series === n).map((x) => one(x)).join("")]);
      if (mine.length || addBtn) groups.push([langOf(hass) === "en" ? "My scenes" : "Мои сцены", mine.map((x) => one(x)).join("") + addBtn]);
      return `<div class="sgs">${groups.map(([n, h]) => `<div class="sg">${n ? `<span class="caps ser">${esc(n)}</span>` : ""}<div class="scn ${kind}">${h}</div></div>`).join("")}</div>`;
    }
    // Blind scenes of two or more curtain types («Открыть Шторы», «Закрыть Шторы», «Открыть Тюль»…): a labelled row per type,
    // short action names inside (family test 29.10) — «+ Сцена» ends the last row.
    if (kind === "cover") {
      const parsed = all.map((x) => {
        const m = /^(Открыть|Закрыть)\s+(.+)$/i.exec(sceneLabel(hass, x));
        return m ? { x, verb: m[1][0].toUpperCase() + m[1].slice(1).toLowerCase(), type: m[2].trim() } : null;
      });
      const types = [...new Set(parsed.filter(Boolean).map((p) => p.type.toLowerCase()))];
      if (types.length >= 2) {
        const rest = all.filter((_, i) => !parsed[i]);
        const rows = types.map((ty) => {
          const mine = parsed.filter((p) => p && p.type.toLowerCase() === ty);
          return `<div class="scr"><span class="scl">${esc(mine[0].type[0].toUpperCase() + mine[0].type.slice(1))}:</span>${mine.map((p) => one(p.x, p.verb)).join("")}</div>`;
        });
        if (rest.length) rows.unshift(`<div class="scr">${rest.map((x) => one(x)).join("")}</div>`);
        if (addBtn) rows[rows.length - 1] = rows[rows.length - 1].replace(/<\/div>$/, `${addBtn}</div>`);
        return `<div class="scn ${kind} grouped">${rows.join("")}</div>`;
      }
    }
    return `<div class="scn ${kind}">${all.map((x) => one(x)).join("")}${addBtn}</div>`;
  };
  /** What «Запомнить как сейчас» takes: the block's own lights / blinds (one by one — never a group, it would move twice). */
  const captureList = (card, kind) => {
    const c = card._config || {};
    const ids = [...(c.entities || []), ...(c.capture || []), ...((c.rows || []).find((r) => r.kind === kind)?.capture || [])].map((e) => (typeof e === "string" ? e : e?.entity)).filter(Boolean);
    const want = kind === "cover" ? "cover." : "light.";
    const own = [...new Set(ids.filter((id) => id.startsWith(want)))];
    if (own.length) return own;
    // A top card row without `capture` (a dashboard built before): what its scenes hold, else the room's area —
    // one by one, never a group (it has `entity_id`).
    const h = card._hass;
    const row = (c.rows || []).find((r) => r.kind === kind);
    const held = (row?.scenes || c.scenes || []).flatMap((x) => st(h, x.entity)?.attributes?.entity_id || []).filter((id) => id.startsWith(want));
    const areas = new Set(held.map((id) => areaOf(h, id)).filter(Boolean));
    const room = roomOf(card, kind);
    if (!areas.size && room) for (const s of allFamily(h).concat(Object.values(h?.states || {}).filter((x) => x.entity_id.startsWith("scene.") && roomOfSceneId(x) === room))) areas.add(areaOf(h, s.entity_id));
    const inArea = Object.keys(h?.entities || {}).filter((id) => id.startsWith(want) && areas.has(areaOf(h, id)) && !h.entities[id].hidden && !Array.isArray(st(h, id)?.attributes?.entity_id));
    return [...new Set([...held, ...inArea])].filter((id) => !Array.isArray(st(h, id)?.attributes?.entity_id));
  };
  const roomOf = (card, kind) => rowRoom(card, kind);
  // A function: SHEET_CSS (the climate sheet's) is defined further down.
  const sceneSheetCss = () => `${SHEET_CSS}
*{box-sizing:border-box}.sh{padding:18px 18px calc(18px + env(safe-area-inset-bottom));gap:12px}.t{font:500 17px Onest,sans-serif;color:var(--primary-text-color)}
.n{font:400 13px/1.4 Onest,sans-serif;color:var(--secondary-text-color)}.e{font:400 13px/1.4 Onest,sans-serif;color:var(--eh-alert,#FF5A5F)}
input{height:48px;border-radius:14px;border:1px solid var(--divider-color);padding:0 14px;font:400 16px Onest,sans-serif;background:transparent;color:var(--primary-text-color)}
.b{height:48px;border-radius:14px;border:0;font:500 15px Onest,sans-serif;cursor:pointer;background:var(--eh-track,rgba(127,127,127,.12));color:var(--primary-text-color)}
.b.go{background:color-mix(in srgb,var(--eh-amber,#FFB25C) 30%,transparent)}.b.bad{color:var(--eh-alert,#FF5A5F)}.b:disabled{opacity:.5}`;
  /** A small sheet over the dashboard: the family's scene actions. */
  const sceneSheet = (card, build) => {
    const host = document.createElement("div");
    host.className = "hauflo-scene-sheet";
    const sr = host.attachShadow({ mode: "open" });
    const close = () => {
      host.remove();
      document.removeEventListener("keydown", onKey);
    };
    const onKey = (e) => e.key === "Escape" && close();
    document.addEventListener("keydown", onKey);
    host.addEventListener("click", (e) => e.composedPath()[0] === host && close());
    const draw = (html) => {
      sr.innerHTML = `<style>${TZ(sceneSheetCss())}</style><div class="sh" role="dialog" aria-modal="true">${html}</div>`;
      if (LANG !== "ru") translate(sr);
    };
    document.body.appendChild(host);
    build({ sr, draw, close });
  };
  const famCall = async (card, body) => {
    try {
      return { ok: await card._hass.callApi("POST", "hauflo/scenes", body) };
    } catch (e) {
      // HA's callApi rejects with the parsed body ({message}) or a plain error.
      const msg = e?.body?.message || e?.message || (typeof e === "string" ? e : "");
      return { error: msg && !/^\d{3}/.test(msg) ? msg : "Не получилось — ещё раз" };
    }
  };
  const famDone = (card) => {
    FAM.state = "";
    loadFam(card._hass);
    setTimeout(famRerender, 400); // HA's scene reload: the new / renamed entity shows a moment later
  };
  /** «В избранное» in a scene sheet: shown unless the agent is known to be absent; the house's settings are fetched first. */
  const favBtn = (entity) => (HOUSE.off ? "" : `<button class="b" data-fav>${favHas(entity) ? "Убрать из избранного" : "В избранное"}</button>`);
  const bindFavBtn = (sr, card, entity, close, menu) =>
    sr.querySelector("[data-fav]")?.addEventListener("click", async (ev) => {
      ev.currentTarget.disabled = true;
      try {
        await HOUSE.ready(card._hass);
        await favToggle(card._hass, entity);
        haptic(card, "success");
        close();
      } catch (e) {
        menu("Не сохранилось — проверьте связь с домом");
      }
    });
  /** «+ Сцена»: a name, then what the room's lights / blinds do now. */
  const openAddScene = (card, kind) => {
    const room = roomOf(card, kind);
    const entities = captureList(card, kind);
    sceneSheet(card, ({ sr, draw, close }) => {
      const form = (err = "", value = "") => {
        draw(`<div class="t">${kind === "cover" ? "Новая сцена штор" : "Новая сцена света"}</div>
<div class="n">${kind === "cover" ? "Поставьте шторы как нужно — сцена запомнит их положение." : "Включите свет как нужно — сцена запомнит, что горит и как ярко."}</div>
<div class="n">Сцена сохранится в Home Assistant: на панели, в голосе и автоматизациях. На настенных выключателях KNX её не будет.</div>
<input maxlength="40" placeholder="${kind === "cover" ? "Например, Утро" : "Например, Кино"}" aria-label="Название" value="${esc(value)}">
${err ? `<div class="e">${esc(err)}</div>` : ""}<button class="b go" data-save>Запомнить как сейчас</button><button class="b" data-x>Отмена</button>`);
        const input = sr.querySelector("input");
        input.focus();
        sr.querySelector("[data-x]").addEventListener("click", close);
        const save = async () => {
          const name = input.value.trim();
          if (!name) return form("Введите название", "");
          sr.querySelector("[data-save]").disabled = true;
          const r = await famCall(card, { op: "add", room, kind: kind === "cover" ? "c" : "l", name, entities });
          if (r.error) return form(r.error, name);
          haptic(card, "success");
          close();
          famDone(card);
        };
        sr.querySelector("[data-save]").addEventListener("click", save);
        input.addEventListener("keydown", (e) => e.key === "Enter" && save());
      };
      form();
    });
  };
  /** A long press on a scene Hauflo made: rename, remember again, delete (the family's) or hide (the integrator's). */
  const openEditScene = (card, entity, kind) => {
    const s = st(card._hass, entity);
    if (!s) return;
    const family = /^hauflo_fam_/.test(String(s.attributes.id || ""));
    sceneSheet(card, ({ sr, draw, close }) => {
      const menu = (err = "") => {
        const may = canEdit(card._hass);
        draw(`<div class="t">${esc(nameOf(s, entity))}</div>
${!may ? `<div class="n">${esc(tr("Детский профиль: сцены меняют взрослые."))}</div>` : family ? "" : `<div class="n">Сцену настроил интегратор. Её можно переименовать и перезаписать; убрать — только с этой панели.</div>`}
${err ? `<div class="e">${esc(err)}</div>` : ""}${may ? `<button class="b" data-rename>Переименовать</button>
<button class="b go" data-again>Запомнить как сейчас</button>
<button class="b bad" data-del>${family ? "Удалить сцену" : "Убрать с панели"}</button>` : ""}${favBtn(entity)}<button class="b" data-x>Отмена</button>`);
        sr.querySelector("[data-x]").addEventListener("click", close);
        bindFavBtn(sr, card, entity, close, menu);
        sr.querySelector("[data-rename]")?.addEventListener("click", () => rename());
        sr.querySelector("[data-again]")?.addEventListener("click", () => again());
        sr.querySelector("[data-del]")?.addEventListener("click", async () => {
          const r = await famCall(card, { op: "delete", entity_id: entity });
          if (r.error) return menu(r.error);
          close();
          famDone(card);
        });
      };
      // «Точно? Сцена будет перезаписана» (family review round 3: one tap overwrote the family's «Ночь»), then the result.
      const again = (err = "") => {
        draw(`<div class="t">Точно? Сцена будет перезаписана</div>
<div class="n">«${esc(nameOf(s, entity))}» запомнит, как ${kind === "cover" ? "стоят шторы" : "горит свет"} в комнате сейчас. Прежняя сцена пропадёт.</div>
${err ? `<div class="e">${esc(err)}</div>` : ""}<button class="b go" data-yes>Да, перезаписать</button><button class="b" data-x>Отмена</button>`);
        sr.querySelector("[data-x]").addEventListener("click", close);
        const yes = sr.querySelector("[data-yes]");
        yes.addEventListener("click", async () => {
          yes.disabled = true;
          const r = await famCall(card, { op: "recapture", entity_id: entity, entities: captureList(card, kind) });
          if (r.error) return again(r.error);
          haptic(card, "success");
          draw(`<div class="t">Сцена перезаписана</div><div class="n">«${esc(nameOf(s, entity))}» теперь — как сейчас в комнате.</div><button class="b go" data-x>Готово</button>`);
          sr.querySelector("[data-x]").addEventListener("click", close);
          famDone(card);
        });
      };
      const rename = (err = "") => {
        draw(`<div class="t">Переименовать</div><input maxlength="40" aria-label="Название" value="${esc(nameOf(s, entity))}">${err ? `<div class="e">${esc(err)}</div>` : ""}
<button class="b go" data-save>Сохранить</button><button class="b" data-x>Отмена</button>`);
        const input = sr.querySelector("input");
        input.focus();
        input.select();
        sr.querySelector("[data-x]").addEventListener("click", close);
        const save = async () => {
          const name = input.value.trim();
          if (!name) return rename("Введите название");
          const r = await famCall(card, { op: "rename", entity_id: entity, name });
          if (r.error) return rename(r.error);
          close();
          famDone(card);
        };
        sr.querySelector("[data-save]").addEventListener("click", save);
        input.addEventListener("keydown", (e) => e.key === "Enter" && save());
      };
      menu();
    });
  };
  /** A long press on a KNX scene (Д19): «Запомнить как сейчас» = KNX scene learn through the agent (it finds the
   * address and the number itself; the card names the scene only), «Переименовать» = its name on this panel. No delete:
   * the scene lives in the actuators and on the wall switches. */
  const openKnxScene = (card, entity, kind) => {
    const s = st(card._hass, entity);
    if (!s) return;
    const number = FAM.knx?.[entity];
    const label = () => FAM.names?.[entity] || nameOf(s, entity);
    const what = kind === "cover" ? "штор" : kind === "mixed" ? "света и штор" : "света";
    sceneSheet(card, ({ sr, draw, close }) => {
      const menu = (err = "") => {
        const may = canEdit(card._hass);
        draw(`<div class="t" data-own>${esc(label())}</div>
<div class="n">${esc(may ? "Сцена — та же, что на настенных выключателях." : tr("Детский профиль: сцены меняют взрослые."))}</div>
${err ? `<div class="e">${esc(err)}</div>` : ""}${may ? `<button class="b go" data-again>Запомнить как сейчас</button>
<button class="b" data-rename>Переименовать</button>` : ""}${favBtn(entity)}<button class="b" data-x>Отмена</button>`);
        sr.querySelector("[data-x]").addEventListener("click", close);
        bindFavBtn(sr, card, entity, close, menu);
        sr.querySelector("[data-rename]")?.addEventListener("click", () => rename());
        sr.querySelector("[data-again]")?.addEventListener("click", () => ask());
      };
      // Asked first: the switches' scene changes too.
      const ask = (err = "") => {
        draw(`<div class="t">Точно? Сцена будет перезаписана</div>
<div class="n">Текущее состояние ${what} комнаты запомнится в сцене «${esc(label())}». Сцена изменится и на настенных выключателях.</div>
${err ? `<div class="e">${esc(err)}</div>` : ""}<button class="b go" data-yes>Да, перезаписать</button><button class="b" data-x>Отмена</button>`);
        sr.querySelector("[data-x]").addEventListener("click", close);
        const yes = sr.querySelector("[data-yes]");
        yes.addEventListener("click", async () => {
          yes.disabled = true;
          const r = await famCall(card, { op: "knx_learn", entity_id: entity });
          if (r.error) return ask(r.error);
          haptic(card, "success");
          sent();
        });
      };
      // Nothing on the bus says whether the actuators stored it: what to do if nothing changed.
      const sent = () => {
        draw(`<div class="t">Сцена перезаписана</div>
<div class="n">Команда ушла на выключатели. Проверьте: нажмите «${esc(label())}». Если сцена осталась прежней — у этих выключателей сохранение сцен выключено, спросите интегратора.</div>
<button class="b go" data-x>Готово</button>`);
        sr.querySelector("[data-x]").addEventListener("click", close);
      };
      const rename = (err = "") => {
        draw(`<div class="t">Переименовать</div><input maxlength="40" aria-label="Название" value="${esc(label())}">
<div class="n">Название на панели. Пусто — снова название от интегратора.</div>${err ? `<div class="e">${esc(err)}</div>` : ""}
<button class="b go" data-save>Сохранить</button><button class="b" data-x>Отмена</button>`);
        const input = sr.querySelector("input");
        input.focus();
        input.select();
        sr.querySelector("[data-x]").addEventListener("click", close);
        const save = async () => {
          const name = input.value.trim();
          const r = await famCall(card, { op: "knx_name", entity_id: entity, name });
          if (r.error) return rename(r.error);
          FAM.names = { ...FAM.names };
          if (name) FAM.names[entity] = name;
          else delete FAM.names[entity];
          close();
          famDone(card);
        };
        sr.querySelector("[data-save]").addEventListener("click", save);
        input.addEventListener("keydown", (e) => e.key === "Enter" && save());
      };
      menu();
    });
  };
  const bindScenes = (card) => {
    const root = card.shadowRoot;
    root.querySelectorAll("button[data-scene]").forEach((b) => {
      const scn = b.closest(".scn")?.classList;
      const kind = scn?.contains("cover") ? "cover" : "light";
      const open = () => (b.dataset.edit === "knx" ? openKnxScene(card, b.dataset.scene, scn?.contains("mixed") ? "mixed" : kind) : openEditScene(card, b.dataset.scene, kind));
      let timer = null;
      let held = false;
      if (b.dataset.edit) {
        b.addEventListener("pointerdown", () => {
          held = false;
          timer = setTimeout(() => {
            held = true;
            haptic(card, "medium");
            open();
          }, 550);
        });
        for (const ev of ["pointerup", "pointerleave", "pointercancel"]) b.addEventListener(ev, () => clearTimeout(timer));
        b.addEventListener("contextmenu", (ev) => {
          ev.preventDefault();
          clearTimeout(timer);
          if (!held) open();
          held = true;
        });
      } else longPress(b, () => openTile(card, b.dataset.scene));
      b.addEventListener("click", (ev) => {
        ev.stopPropagation();
        if (held) {
          held = false;
          return;
        }
        const id = b.dataset.scene;
        haptic(card);
        card._busy = true;
        // A tick and «отправлено» where its icon and name were: the tap took (persona round 5, final review 28.10).
        sendAck(card, b, id.split(".")[0] === "script" ? "script" : "scene", "turn_on", { entity_id: id }, () => sceneDone(card, id));
        setTimeout(() => (card._busy = false), 1600);
      });
    });
    root.querySelectorAll("button[data-scene-add]").forEach((b) =>
      b.addEventListener("click", (ev) => {
        ev.stopPropagation();
        haptic(card);
        openAddScene(card, b.dataset.sceneAdd);
      }),
    );
  };
  const OPENING = ["window", "door", "garage_door", "opening"];
  const FAN = { auto: "Авто", on_low: "Тихо", on_high: "Сильно", auto_low: "Авто ↓", auto_high: "Авто ↑", low: "Тихо", medium: "Средне", middle: "Средне", mid: "Средне", high: "Сильно", top: "Макс", quiet: "Тихо", diffuse: "Мягко", focus: "Фокус", turbo: "Турбо" };
  /** climate.turn_off is there: the TURN_OFF feature (128) or «off» among its modes. */
  const canTurnOff = (s) => ((Number(s.attributes.supported_features) || 0) & 128) !== 0 || (s.attributes.hvac_modes || []).includes("off");
  const HVAC = { heat: "Нагрев", cool: "Холод", auto: "Авто", heat_cool: "Диапазон", off: "Выкл", fan_only: "Вент.", dry: "Осушение" };
  // «режим: нагрев» — the MODE is small print; what the thermostat DOES is another word (owner 28.10, Д16).
  const modeSmall = (state) => (HVAC[state] && state !== "off" ? `режим: ${HVAC[state].toLowerCase()}` : "");
  for (const w of Object.values(HVAC)) if (EN[w]) EN[`режим: ${w.toLowerCase()}`] = `mode: ${EN[w].toLowerCase()}`;
  /** The activity sources of a climate / floor zone the project has (card config `activity`: entity ids of the heat /
   * cool command status sensors and of the valve status sensors); older configs have none. */
  const actIds = (a) => [...(a?.heat || []), ...(a?.cool || []), ...(a?.valves || [])];
  const srcOn = (h, ids) => (ids || []).some((id) => st(h, id)?.state === "on");
  const srcKnown = (h, ids) => (ids || []).some((id) => ["on", "off"].includes(st(h, id)?.state));
  /** What a thermostat DOES now, not its mode (owner 28.10, Д13/Д16): "heat" | "cool" | "dry" | "fan" | "idle" | "off" |
   * "mode" (nothing says what it does: the mode is all there is) | null (no link). A source of the zone that is on (valve
   * open, heat command on — OR) is a fact; else HA's hvac_action, «heating» only when it fits (not warmer than the goal). */
  const climateNow = (h, s, act) => {
    if (!s || ["unavailable", "unknown"].includes(s.state)) return null;
    if (s.state === "off") return "off";
    const a = s.attributes || {};
    if (srcOn(h, act?.heat) || srcOn(h, act?.valves)) return "heat";
    if (srcOn(h, act?.cool)) return "cool";
    const cur = num(a.current_temperature);
    const goal = (k) => num(a.temperature) ?? num(k === "heat" ? a.target_temp_low : a.target_temp_high);
    const fits = (k) => cur === undefined || goal(k) === undefined || (k === "heat" ? cur < goal(k) + 0.5 : cur > goal(k) - 0.5);
    const ha = a.hvac_action;
    if (ha === "heating") return fits("heat") ? "heat" : "idle";
    if (ha === "cooling") return fits("cool") ? "cool" : "idle";
    if (ha === "drying") return "dry";
    if (ha === "fan") return "fan";
    if (ha === "idle" || ha === "off") return "idle";
    return srcKnown(h, actIds(act)) ? "idle" : "mode";
  };
  /** «Защита» (frost protection, building_protection): the room is kept from freezing — the comfort setpoint HA still
   * reports isn't what it does (family review round 3: «Защита» showed «задано 22°»). */
  const frostOf = (s) => s?.attributes?.preset_mode === "building_protection";
  /** An open equipment fault of this thermostat's room (same HA area) for the floor heating («Ошибка тёплого пола») or
   * the climate — the row says «Авария», not «В норме» (family review round 3). Alarms come from the agent. */
  const faultOf = (card, id, floor) => {
    const h = card._hass;
    openAlarms(card, []);
    const area = areaOf(h, id);
    if (!area) return null;
    return (
      (AGENT.data?._list || []).find((a) => {
        const s = st(h, a.entity);
        if (!s || a.noData || a.returned || s.state !== "on" || alarmCritical(h, a) || areaOf(h, a.entity) !== area) return false;
        const isFloor = /пол|floor/i.test(`${s.attributes.friendly_name || ""} ${a.title || ""}`);
        return floor ? isFloor : !isFloor;
      }) || null
    );
  };
  const NOW_WORD = { heat: "греет", cool: "охлаждает", dry: "осушает", fan: "вентилирует", off: "выкл" };
  /** ONE word per state, everywhere (survey 29.10); heat / cool carry «до 27°» where there is room. Grey is only «Выключено» / «Нет связи». */
  const CLIMATE_STATE_WORDS = { heat: "Нагрев", cool: "Охлаждение", idle: "В норме", off: "Выключено", offline: "Нет связи", fan: "Вентиляция", dry: "Осушение" };
  const climateLine = (now, goal) => {
    const w = CLIMATE_STATE_WORDS;
    const g = goal === undefined || goal === null ? "" : `${fmt(goal, goal % 1 ? 1 : 0)}°`;
    return now === "heat" || now === "cool" ? w[now] + (g ? ` до ${g}` : "") : w[now] || "";
  };
  /** The accent of a thermostat is its MODE, always (Apple Home / Nest / Ecobee; owner 29.10: «В норме» painted the
   * whole card grey): heat amber, cool ice, auto / range by what it does now (else its own), dry / fan theirs. Off and no
   * link — "" (grey). For the mode buttons: `climateTone(null, null, mode)`. */
  const climateTone = (s, now, mode = s?.state) => {
    if (!mode || ["off", "unavailable", "unknown"].includes(mode)) return "";
    if (mode === "heat" || mode === "cool" || mode === "dry") return mode;
    if (mode === "fan_only") return "fan";
    return now === "heat" || now === "cool" ? now : "auto";
  };
  /** Not working this minute (at target, waiting): the same colour, a little softer — never grey. */
  const toneCalm = (tone, now) => !!tone && !["heat", "cool", "dry", "fan"].includes(now);
  /** The mode accents as CSS variables: --ac (fill / line), --acT (text), --acG (glow). */
  const TONE_CSS = `.m-heat{--ac:var(--amber);--acT:var(--amberT);--acG:var(--amberGlow)}.m-cool{--ac:var(--ice);--acT:color-mix(in srgb,var(--ice) 78%,var(--ink));--acG:var(--iceGlow)}.m-auto{--ac:var(--mint);--acT:var(--mintT);--acG:color-mix(in srgb,var(--mint) 45%,transparent)}.m-dry{--ac:var(--eh-dry,#2AA5B8);--acT:color-mix(in srgb,var(--eh-dry,#2AA5B8) 78%,var(--ink));--acG:color-mix(in srgb,var(--eh-dry,#2AA5B8) 45%,transparent)}.m-fan{--ac:var(--eh-fan,#8B74E8);--acT:color-mix(in srgb,var(--eh-fan,#8B74E8) 78%,var(--ink));--acG:color-mix(in srgb,var(--eh-fan,#8B74E8) 45%,transparent)}
.modes button.on.mk{background:var(--ac);color:#141414;font-weight:600;box-shadow:0 2px 12px -4px var(--acG)}`;
  /** The mode as small print only when it says something the action doesn't («режим: авто»). */
  const modeDiff = (state, now) => {
    const m = modeSmall(state);
    if (!m) return "";
    return (state === "heat" && (now === "heat" || now === "idle")) || (state === "cool" && (now === "cool" || now === "idle")) || (state === "dry" && now === "dry") || (state === "fan_only" && now === "fan") ? "" : m;
  };
  /** A name with the separators a strip left at its ends trimmed («Мастер спальня, » → «Мастер спальня»). */
  const trimSep = (s) => String(s ?? "").replace(/^[\s,;·|\-–—:]+|[\s,;·|\-–—:]+$/g, "").trim();
  let TOAST_T;
  /** A short confirmation at the bottom (no button): «Сценарий «Вечер» включён». */
  const okToast = (text) => {
    document.getElementById("hf-undo")?.remove();
    clearTimeout(TOAST_T);
    const el = document.createElement("div");
    el.id = "hf-undo";
    el.className = "hf-ok";
    el.setAttribute("role", "status");
    el.style.cssText = "position:fixed;left:50%;transform:translateX(-50%);bottom:calc(20px + env(safe-area-inset-bottom));z-index:2147483000;display:flex;align-items:center;gap:10px;max-width:calc(100vw - 32px);min-height:48px;padding:10px 18px 10px 14px;border-radius:16px;background:#1F1E1C;color:#fff;font:500 calc(15px*var(--hf-ts,1)) Onest,sans-serif;line-height:1.3;box-shadow:0 8px 28px rgba(0,0,0,.28)";
    el.innerHTML = `<span aria-hidden="true" style="color:#5CE0B4;font-size:18px">✓</span><span>${esc(text)}</span>`;
    document.body.appendChild(el);
    TOAST_T = setTimeout(() => el.remove(), 3000);
  };
  /** A 3-second «Отменить» toast instead of a «are you sure?» dialog for a switch-off done by mistake. */
  const undoToast = (text, onUndo) => {
    document.getElementById("hf-undo")?.remove();
    clearTimeout(TOAST_T);
    const el = document.createElement("div");
    el.id = "hf-undo";
    el.setAttribute("role", "status");
    el.style.cssText = "position:fixed;left:50%;transform:translateX(-50%);bottom:calc(20px + env(safe-area-inset-bottom));z-index:2147483000;display:flex;align-items:center;gap:8px;max-width:calc(100vw - 32px);padding:4px 4px 4px 16px;border-radius:16px;background:#1d1d1f;color:#fff;font:500 14px Onest,sans-serif;box-shadow:0 8px 30px rgba(0,0,0,.35)";
    el.innerHTML = `<span>${esc(tr(text))}</span><button type="button" style="min-height:44px;min-width:44px;padding:0 14px;border-radius:12px;border:0;background:transparent;color:#FFC070;font:600 14px Onest,sans-serif;cursor:pointer">${esc(tr("Отменить"))}</button>`;
    el.querySelector("button").addEventListener("click", () => {
      clearTimeout(TOAST_T);
      el.remove();
      onUndo();
    });
    document.body.appendChild(el);
    TOAST_T = setTimeout(() => el.remove(), 3000);
  };
  // Scenes: «Точно? Сцена будет перезаписана» and the result (family review round 3, item 3).
  Object.assign(EN, { "Точно? Сцена будет перезаписана": "Sure? The scene will be overwritten", "Да, перезаписать": "Yes, overwrite", "Сцена перезаписана": "Scene overwritten" });
  EN_RX.push(
    [/^«(.+)» запомнит, как стоят шторы в комнате сейчас\. Прежняя сцена пропадёт\.$/, "«$1» will remember where the room's blinds are now. The old scene is gone."],
    [/^«(.+)» запомнит, как горит свет в комнате сейчас\. Прежняя сцена пропадёт\.$/, "«$1» will remember how the room's lights are now. The old scene is gone."],
    [/^«(.+)» теперь — как сейчас в комнате\.$/, "«$1» is now as the room is."],
    [/^Текущее состояние (света и штор|штор|света) комнаты запомнится в сцене «(.+)»\. Сцена изменится и на настенных выключателях\.$/, (m, w, n) => `The room's ${w === "штор" ? "blinds" : w === "света" ? "lights" : "lights and blinds"} as they are now go into the scene «${n}». It changes on the wall switches too.`],
    [/^Команда ушла на выключатели\. Проверьте: нажмите «(.+)»\. Если сцена осталась прежней — у этих выключателей сохранение сцен выключено, спросите интегратора\.$/, "The command went to the switches. Check it: tap «$1». If the scene stayed as before, scene saving is off on these switches — ask your installer."],
  );
  EN_RX.push([/^Авария: (\d+)$/, "Fault: $1"]);
  Object.assign(EN, { "Включить всё": "Turn all on", "Выключить всё": "Turn all off", "Часы": "Hours", "Минуты": "Minutes", "Защита от замерзания": "Frost protection", "Авария": "Fault" });
  Object.assign(EN, { "Свет у меня": "My light", "Потеплее": "Warmer", "Моя комната ›": "My room ›" });
  EN_RX.push([/^задано (-?[\d.,]+°) · сейчас (-?[\d.,]+°)$/, "set $1 · now $2"], [/^вкл · (\d+) из (\d+)$/, "on · $1 of $2"]);
  Object.assign(EN, { "Точно? Защита": "Sure? Protection", "Выключить климат": "Turn off climate", "Выключить тёплые полы": "Turn off floor heating", "Каждая отдельно": "Each on its own", "Отменить": "Undo", "Выключено": "Off", "Не охлаждает": "Not cooling", "Охлаждение": "Cooling", "В норме": "At target", "Вентиляция": "Ventilation", "Осушение": "Drying", "клапаны закрыты": "valves closed", "Климат выключен": "Climate turned off", "Свет выключен": "Lights turned off", "Режим": "Mode", "Вентилятор": "Fan", "теплее": "warmer", "прохладнее": "cooler", "Точно? Выключить": "Sure? Turn off", "Выключить весь свет": "Turn off all lights", "Включить весь свет": "Turn on all lights", "Все держат нужную температуру": "At target", "обогрев до": "heating to", "охлаждение от": "cooling from" });
  EN_RX.push([/^Нагрев: (\d+)$/, "Heating: $1"], [/^Охлаждение: (\d+)$/, "Cooling: $1"], [/^клапаны открыты · (\d+)$/, "valves open · $1"], [/^задано (.+)$/, "set $1"], [/^Греет — станет (.+)$/, "Heating — will reach $1"], [/^Охлаждает — станет (.+)$/, "Cooling — will reach $1"], [/^(Тепло, не греет|Не охлаждает) \(держим (.+)\)$/, (m, a, b) => `${a === "Не охлаждает" ? "Not cooling" : "Warm, not heating"} (holding ${b})`], [/^шаг (.+)$/, "step $1"], [/^(Теплее|Холоднее) на (.+)$/, (m, a, b) => `${a === "Теплее" ? "Warmer" : "Cooler"} by ${b}`], [/^режим: (.+)$/, "mode: $1"]);
  const acting = (k) => k === "heat" || k === "cool" || k === "dry" || k === "fan";
  /** One icon per valve of the zone, open lit (Д13). */
  const valveIcons = (h, ids) => {
    const list = (ids || []).filter((id) => st(h, id));
    if (!list.length) return "";
    const open = list.filter((id) => st(h, id).state === "on").length;
    return `<span class="vwrap"><button class="vch${open ? " on" : ""}" data-vch aria-expanded="false">${open ? `клапаны открыты · ${open}` : "клапаны закрыты"}</button><span class="vpop" hidden>${list.slice(0, 24).map((id, i) => `<span>Клапан ${i + 1}: ${st(h, id).state === "on" ? "открыт" : "закрыт"}</span>`).join("")}</span></span>`;
  };
  const VALVE_CSS = `.vwrap{display:inline-flex;flex-direction:column;vertical-align:middle}.vch{display:inline-flex;align-items:center;gap:6px;min-height:44px;padding:0 4px;font:500 12px Onest,sans-serif;color:var(--ink2)}.vch::before{content:"";width:6px;height:6px;border-radius:3px;background:var(--ink3)}.vch.on::before{background:var(--amber)}.vpop{display:flex;flex-direction:column;gap:2px;padding:8px 12px;border-radius:12px;background:var(--track);font:400 12px/1.4 Onest,sans-serif;color:var(--ink2)}.vpop[hidden]{display:none}`;
  // Units in Russian; a value outside what's physically plausible is «—», not «33424 гПа» (designer review 25.10).
  const UNIT = { "km/h": "км/ч", "m/s": "м/с", hPa: "гПа", mbar: "мбар", mmHg: "мм рт. ст.", "µg/m³": "мкг/м³", ppm: "ppm", ppb: "ppb" };
  const ru = (u) => UNIT[u] || u || "";
  const sane = (v, lo, hi) => (v === undefined || v < lo || v > hi ? undefined : v);
  const PRESET = { comfort: "Комфорт", standby: "Ожидание", economy: "Эконом", building_protection: "Защита", eco: "Эко", away: "Нет дома", home: "Дома", sleep: "Сон", boost: "Буст" };
  const ALARM_WORD = { moisture: "Вода", smoke: "Дым", gas: "Газ", carbon_monoxide: "Угарный газ", heat: "Перегрев", cold: "Холод", problem: "Авария", safety: "Опасность", tamper: "Вскрытие" };
  // A sensor of another integration often keeps its zigbee2mqtt name as HA's friendly name («leak_bathroom» on the alarm
  // card — integrator review r2): a technical name reads as the kind of sensor, in the house's language, with its HA room.
  const TECH_NAME = /^(?:[a-z0-9]+(?:[_.-][a-z0-9]+)+|0x[0-9a-f]{6,})$/;
  const SENSOR_KIND = {
    moisture: ["Датчик протечки", "Leak sensor"], smoke: ["Датчик дыма", "Smoke detector"], gas: ["Датчик газа", "Gas detector"], carbon_monoxide: ["Датчик угарного газа", "CO detector"],
    heat: ["Датчик перегрева", "Heat sensor"], cold: ["Датчик холода", "Cold sensor"], problem: ["Датчик аварии", "Fault sensor"], safety: ["Датчик безопасности", "Safety sensor"],
    tamper: ["Датчик вскрытия", "Tamper sensor"], door: ["Датчик двери", "Door sensor"], window: ["Датчик окна", "Window sensor"], opening: ["Датчик открытия", "Opening sensor"],
    motion: ["Датчик движения", "Motion sensor"], occupancy: ["Датчик присутствия", "Presence sensor"], vibration: ["Датчик вибрации", "Vibration sensor"],
  };
  /** An alarm source's name: its own, or — a technical one — its kind; `tech` says the room should be said beside it. */
  const alarmName = (h, s, id) => {
    const n = nameOf(s, id);
    const k = TECH_NAME.test(n) && SENSOR_KIND[s?.attributes?.device_class];
    return k ? { name: k[LANG === "ru" ? 0 : 1], tech: true } : { name: n, tech: TECH_NAME.test(n) };
  };
  /** The room of an alarm source: the deploy's word for it, else its HA area. */
  const alarmRoom = (c, h, id) => c?.rooms?.[id] || areaName(h, id);

  // --- history: HA's recorder (and its hourly statistics on a young install), cached 5 minutes ------
  const HIST = new Map();
  const series = async (hass, id, hours = 24, attr) => {
    const key = `${id}|${hours}|${attr || ""}`;
    const c = HIST.get(key);
    if (c && Date.now() - c.at < 5 * 60000) return c.data;
    const end = new Date();
    const start = new Date(end.getTime() - hours * 3600000);
    let pts = [];
    try {
      const r = await hass.callWS({ type: "history/history_during_period", start_time: start.toISOString(), end_time: end.toISOString(), entity_ids: [id], minimal_response: !attr, no_attributes: !attr, significant_changes_only: false });
      // A missing value (an attribute not reported yet, «unknown») is a gap, not a zero.
      const val = (p) => {
        const v = attr ? (p.a || p.attributes || {})[attr] : p.s ?? p.state;
        return v === null || v === undefined || v === "" ? NaN : Number(v);
      };
      pts = (r[id] || []).map((p) => [(p.lu ?? p.lc) ? (p.lu ?? p.lc) * 1000 : Date.parse(p.last_updated || p.last_changed), val(p)]).filter(([t, v]) => !isNaN(t) && !isNaN(v));
    } catch (e) {
      pts = [];
    }
    // A young or restarted HA has minutes of detail but hours of statistics: then the statistics.
    if ((pts.length < 4 || pts[0][0] > start.getTime() + (hours * 3600000) / 2) && !attr) {
      try {
        const r = await hass.callWS({ type: "recorder/statistics_during_period", start_time: start.toISOString(), end_time: end.toISOString(), statistic_ids: [id], period: "hour", types: ["mean"] });
        const stat = (r[id] || []).map((p) => [typeof p.start === "number" ? p.start : Date.parse(p.start), Number(p.mean)]).filter(([, v]) => !isNaN(v));
        if (stat.length > pts.length) pts = stat;
      } catch (e) {
        /* no statistics for it */
      }
    }
    const data = { pts, range: [start.getTime(), end.getTime()] };
    HIST.set(key, { at: Date.now(), data });
    return data;
  };
  /** SVG path of a series in a W×H box (a step held to «now»): the line and the area under it. */
  const spark = (data, W, H, pad = 4, minSpan = 0, minPts = 2, minHours = 0) => {
    const pts = data.pts;
    const hoursIn = (data.range[1] - (pts[0]?.[0] ?? data.range[1])) / 3600000;
    if (pts.length < minPts) return null;
    // A trace of the last few minutes is a blip, not a day: drawn once it covers enough of the window.
    if (minHours && data.range[1] - pts[0][0] < minHours * 3600000) return null;
    // The axis is the stretch the data covers — a shorter history isn't a stub on the right.
    if (minHours) data = { ...data, range: [Math.max(data.range[0], pts[0][0]), data.range[1]] };
    const vals = pts.map(([, v]) => v);
    const vlo = Math.min(...vals);
    const vhi = Math.max(...vals);
    // A narrow range (23,4–23,6°) isn't stretched into a cliff: the axis spans at least minSpan around the middle.
    const mid = (vlo + vhi) / 2;
    const half = Math.max((vhi - vlo) / 2, minSpan / 2);
    const lo0 = mid - half;
    const hi0 = mid + half;
    const [t0, t1] = data.range;
    const xy = pts.map(([t, v]) => [Math.max(0, ((t - t0) / (t1 - t0 || 1)) * W), H - ((v - lo0) / (hi0 - lo0 || 1)) * (H - 2 * pad) - pad]);
    xy.push([W, xy[xy.length - 1][1]]);
    const line = xy.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`).join("");
    const yOf = (v) => Math.min(H - 1, Math.max(1, H - ((v - lo0) / (hi0 - lo0 || 1)) * (H - 2 * pad) - pad));
    return { line, area: `${line}L${W},${H}L${xy[0][0].toFixed(1)},${H}Z`, lo: vlo, hi: vhi, hours: hoursIn, yOf };
  };

  // --- a thermostat's day: its reading and its setpoint from HA's recorder (one call, attributes), cached 5 minutes --
  const CHIST = new Map();
  const climateSeries = async (hass, id, hours = 24, sensor) => {
    const key = `${id}|${sensor || ""}|${hours}`;
    const c = CHIST.get(key);
    if (c && Date.now() - c.at < 5 * 60000) return c.data;
    const end = new Date();
    const start = new Date(end.getTime() - hours * 3600000);
    let r = {};
    try {
      r = await hass.callWS({ type: "history/history_during_period", start_time: start.toISOString(), end_time: end.toISOString(), entity_ids: [id], minimal_response: false, no_attributes: false, significant_changes_only: false });
    } catch (e) {
      r = {};
    }
    const n = (v) => (v === null || v === undefined || v === "" ? NaN : Number(v));
    const at = (p) => ((p.lu ?? p.lc) ? (p.lu ?? p.lc) * 1000 : Date.parse(p.last_updated || p.last_changed));
    const of = (k) => (r[id] || []).map((p) => [at(p), n((p.a || p.attributes || {})[k])]).filter(([t, v]) => !isNaN(t) && !isNaN(v));
    // A room sensor of its own (`history`) is the reading; the thermostat's attribute otherwise.
    const cur = sensor ? (await series(hass, sensor, hours)).pts : of("current_temperature");
    // The setpoint changes seldom: only its changes (every reading writes a row with it).
    const sp = of("temperature").filter((p, i, a) => !i || p[1] !== a[i - 1][1]);
    const data = { cur, sp, range: [start.getTime(), end.getTime()] };
    CHIST.set(key, { at: Date.now(), data });
    return data;
  };
  /** The temperature chart beside a thermostat's number (owner 27.10: «рисовать рядом с текущей температурой
   * график»): the reading as a line over the day, the setpoint dashed, degrees on the right, hours under it.
   * Always the same height — loading, no history or a chart — so the page doesn't move. `data` undefined: loading. */
  const tchart = (data, live, { H = 88, label = "В комнате", tone = "" } = {}) => {
    const total = H + 44;
    const shell = (inner) => `<div class="tch ${tone}" style="height:${total}px">${inner}</div>`;
    if (data === undefined) return shell(`<div class="tno">загрузка…</div>`);
    const now = Date.now();
    const cur0 = data?.cur || [];
    // Half an hour of history at least: a few minutes are a dot, not a day.
    if (!cur0.length || now - cur0[0][0] < 30 * 60000) return shell(`<div class="tno">истории пока нет</div>`);
    const t0 = Math.max(data.range[0], cur0[0][0]);
    const span = now - t0 || 1;
    // The live value is the line's end: the chart and the big number never disagree.
    const cur = live.cur === undefined ? [...cur0] : [...cur0, [now, live.cur]];
    // The setpoint held into the window from before it, then its changes, then now.
    const spIn = (data.sp || []).filter((p) => p[0] > t0);
    const spBefore = [...(data.sp || [])].reverse().find((p) => p[0] <= t0);
    const sp = [...(spBefore ? [[t0, spBefore[1]]] : []), ...spIn, ...(live.sp !== undefined ? [[now, live.sp]] : spIn.length || spBefore ? [[now, (spIn[spIn.length - 1] || spBefore)[1]]] : [])];
    // A day of readings every few seconds: averaged into ≤ 240 points.
    const B = 240;
    let pts = cur;
    if (cur.length > B) {
      const bins = Array.from({ length: B }, () => [0, 0, 0]);
      for (const [t, v] of cur) {
        const b = bins[Math.min(B - 1, Math.max(0, Math.floor(((t - t0) / span) * B)))];
        b[0] += t;
        b[1] += v;
        b[2]++;
      }
      pts = bins.filter((b) => b[2]).map((b) => [b[0] / b[2], b[1] / b[2]]);
      pts.push(cur[cur.length - 1]);
    }
    const vals = [...pts.map((p) => p[1]), ...sp.map((p) => p[1])];
    let lo = Math.min(...vals);
    let hi = Math.max(...vals);
    // At least 2° of axis: 22,4–22,6° isn't stretched into a cliff.
    if (hi - lo < 2) {
      const m = (hi + lo) / 2;
      lo = m - 1;
      hi = m + 1;
    }
    const step = [0.5, 1, 2, 5, 10].find((s) => (hi - lo) / s <= 3) || 10;
    lo = Math.floor(lo / step) * step;
    hi = Math.ceil(hi / step) * step;
    const W = 400;
    const pad = 4;
    const x = (t) => ((Math.max(t, t0) - t0) / span) * W;
    const y = (v) => pad + (1 - (v - lo) / (hi - lo || 1)) * (H - 2 * pad);
    const line = pts.map(([t, v], i) => `${i ? "L" : "M"}${x(t).toFixed(1)},${y(v).toFixed(1)}`).join("");
    const area = `${line}L${x(pts[pts.length - 1][0]).toFixed(1)},${H}L${x(pts[0][0]).toFixed(1)},${H}Z`;
    // The setpoint as steps: it's held, then changed.
    const spLine = sp.map(([t, v], i) => (i ? `H${x(t).toFixed(1)}V${y(v).toFixed(1)}` : `M${x(t).toFixed(1)},${y(v).toFixed(1)}`)).join("");
    const ticksY = [];
    for (let v = lo; v <= hi + 1e-9; v += step) ticksY.push(v);
    // Hours: every 6 on a day, 2 / 1 / ½ on a shorter history; none at the very edges (the labels would collide).
    const hstep = span >= 12 * 3600000 ? 6 : span >= 5 * 3600000 ? 2 : span >= 2 * 3600000 ? 1 : 0.5;
    const ticksX = [];
    const d0 = new Date(t0);
    d0.setMinutes(0, 0, 0);
    for (let t = d0.getTime(); t < now; t += 1800000) {
      const d = new Date(t);
      const h = d.getHours() + d.getMinutes() / 60;
      if (t > t0 && Math.abs((h / hstep) - Math.round(h / hstep)) < 1e-6 && (t - t0) / span > 0.06 && (t - t0) / span < 0.84) ticksX.push(t);
    }
    const hm = (t) => {
      const d = new Date(t);
      return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
    };
    const hours = Math.round(span / 3600000);
    const period = span >= 22 * 3600000 ? "За сутки" : hours >= 1 ? `За ${hours} ч` : "";
    const digits = step < 1 ? 1 : 0;
    // The lines are named at their right end («23,9°» on the solid one, «цель 25°» on the dashed one) — a legend of dashes
    // read like placeholders (owner 28.10).
    const lastC = pts[pts.length - 1][1];
    const lastS = sp.length ? sp[sp.length - 1][1] : undefined;
    const curAbove = lastS === undefined || y(lastC) <= y(lastS);
    const yc = y(lastC);
    const ys = lastS === undefined ? 0 : y(lastS);
    const topC = curAbove ? Math.max(yc, 14) : Math.min(yc, H - 14);
    const topS = curAbove ? Math.min(ys, H - 14) : Math.max(ys, 14);
    const ends = `<span class="el cl" style="top:${topC.toFixed(1)}px;transform:translateY(${curAbove ? "-110%" : "10%"})">${fmt(lastC, 1)}°</span>${lastS === undefined ? "" : `<span class="el sl" style="top:${topS.toFixed(1)}px;transform:translateY(${curAbove ? "10%" : "-110%"})">задано ${fmt(lastS, lastS % 1 ? 1 : 0)}°</span>`}`;
    return shell(`<div class="tlg"><span>${esc(label)}</span><span class="per">${period}</span></div>
<div class="tpl" style="height:${H}px"><svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-hidden="true">${ticksY.map((v) => `<line class="gr" x1="0" x2="${W}" y1="${y(v).toFixed(1)}" y2="${y(v).toFixed(1)}" vector-effect="non-scaling-stroke"/>`).join("")}<path class="ar" d="${area}"/>${sp.length ? `<path class="sp" d="${spLine}" vector-effect="non-scaling-stroke"/>` : ""}<path class="cu" d="${line}" vector-effect="non-scaling-stroke"/></svg>${ends}${ticksY.map((v) => `<span class="ty" style="top:${((y(v) / H) * 100).toFixed(1)}%">${fmt(v, digits)}°</span>`).join("")}</div>
<div class="tx">${ticksX.map((t) => `<span style="left:${(((t - t0) / span) * 100).toFixed(1)}%">${hm(t)}</span>`).join("")}<span class="now">сейчас</span></div>`);
  };
  const TCHART_CSS = `.tch{position:relative;margin-top:14px;--tc:var(--ink2);--tsp:var(--ink2)}.tch.heat{--tc:var(--amber)}.tch.cool{--tc:var(--ice)}.tch.auto{--tc:var(--mint)}.tch.dry{--tc:var(--eh-dry,#2AA5B8)}.tch.fan{--tc:var(--eh-fan,#8B74E8)}.tch:is(.heat,.cool,.auto,.dry,.fan){--tsp:var(--tc)}.tch.calm .cu{opacity:.78}.tch.calm .ar{opacity:.07}.tch.calm .sp{opacity:.6}
.tlg{display:flex;align-items:center;gap:14px;height:16px;margin-bottom:6px;font:400 11px Onest,sans-serif;color:var(--ink3);white-space:nowrap}.tlg .per{margin-left:auto;margin-right:34px}
.tlg i{display:inline-block;width:14px;border-top:2px solid var(--tc);vertical-align:middle;margin:-2px 6px 0 0}.tlg .ds{border-top:2px dashed var(--ink2);opacity:.8}.tlg span:first-child{color:var(--ink2)}
.tpl{position:relative;margin-right:34px}.tpl svg{display:block;width:100%;height:100%;overflow:visible}
.tpl .gr{stroke:var(--hair);stroke-width:1}.tpl .cu{fill:none;stroke:var(--tc);stroke-width:2;stroke-linejoin:round;stroke-linecap:round}.tpl .ar{fill:var(--tc);opacity:.10;stroke:none}
.tpl .el{position:absolute;right:2px;font:500 11px Onest,sans-serif;white-space:nowrap;font-variant-numeric:tabular-nums;pointer-events:none;padding:1px 5px;border-radius:7px;background:var(--eh-glass,var(--ha-card-background,var(--card-background-color,#fff)))}.tpl .el.cl{color:var(--tc)}.tpl .el.sl{color:color-mix(in srgb,var(--tsp) 70%,var(--ink2))}
.tpl .sp{fill:none;stroke:var(--tsp);stroke-width:1.25;stroke-dasharray:5 4;opacity:.8}
.ty{position:absolute;left:calc(100% + 6px);transform:translateY(-50%);font:400 11px Onest,sans-serif;color:var(--ink3);white-space:nowrap;font-variant-numeric:tabular-nums}
.tx{position:relative;height:16px;margin:6px 34px 0 0;font:400 11px Onest,sans-serif;color:var(--ink3);font-variant-numeric:tabular-nums}.tx span{position:absolute;top:0;transform:translateX(-50%);white-space:nowrap}.tx .now{left:auto;right:-34px;transform:none}
.tno{position:absolute;inset:0;display:grid;place-items:center;border-radius:14px;background:var(--track);font:400 13px Onest,sans-serif;color:var(--ink3)}`;

  class EHCard extends HTMLElement {
    constructor() {
      super();
      this.attachShadow({ mode: "open" });
      this._sig = "";
      // A card that hides itself (hideCard) stays in HA's card wrapper — it shows again by itself when there is
      // something to show (HA's hui-card keeps an element with this flag connected).
      this.connectedWhileHidden = true;
      // Parts a card fills in later (history, a list it loads) are put into English too.
      new MutationObserver((ms) => {
        if (LANG === "ru") return;
        for (const m of ms) {
          if (m.type === "characterData") {
            if (m.target.parentElement?.closest?.("[data-own]")) continue;
            const v = tr(m.target.nodeValue);
            if (v !== m.target.nodeValue) m.target.nodeValue = v;
          } else m.addedNodes.forEach((n) => (n.nodeType === 3 ? !n.parentElement?.closest?.("[data-own]") && (n.nodeValue = tr(n.nodeValue)) : n.nodeType === 1 && n.nodeName !== "STYLE" && !n.closest?.("[data-own]") && translate(n)));
        }
      }).observe(this.shadowRoot, { childList: true, subtree: true, characterData: true });
    }
    setConfig(config) {
      this._config = { ...config };
      this._raw = this._config; // as configured — the block's order is put over a copy (orderApply)
      this._sig = "";
      // The house's language (sticky: a card the family added by hand, without `lang`, speaks it too).
      const l = cfgLang(config);
      if (l) HOUSE_LANG = l;
      cfgNames(config);
      if (this._hass) {
        LANG = langOf(this._hass);
        this._update();
      }
    }
    set hass(hass) {
      this._hass = hass;
      AGENT.hass = hass;
      SELF.check(hass, langOf(hass));
      if (this._pop) this._pop.hass = hass; // the control sheet it opened stays live
      const lang = langOf(hass);
      if (lang !== LANG) {
        LANG = lang;
        this._sig = "";
      }
      this._update();
    }
    get hass() {
      return this._hass;
    }
    /** Draw again at `t` (ms) — a text only time changes («обновляется…» after a deploy), no state change to wait for. */
    wakeAt(t) {
      if (this._wake && this._wakeAt <= t) return;
      clearTimeout(this._wake);
      this._wakeAt = t;
      this._wake = setTimeout(() => {
        this._wake = null;
        this._wakeAt = 0;
        this._sig = "";
        this._update();
      }, Math.max(0, t - Date.now()) + 200);
    }
    /** The states this card shows — re-rendered only when they change (not while dragging). */
    watched() {
      return [];
    }
    _update() {
      if (!this._config || !this._hass || this._busy) return;
      CARDS.add(this);
      // HA replaces a state object when it changes: comparing references is enough — no JSON of every
      // attribute on every hass update (HA core review 25.10). `_sig = ""` elsewhere forces a render.
      const refs = [...this.watched(), ...famWatched(this)].map((id) => this._hass.states[id]);
      const dark = !!this._hass.themes?.darkMode;
      if (this._sig && dark === this._dark && refs.length === this._refs?.length && refs.every((r, i) => r === this._refs[i])) return;
      const forced = !this._sig;
      this._sig = "r";
      this._refs = refs;
      this._dark = dark;
      this.lang = LANG; // hyphenation in the card's language («Полотенце-сушитель»)
      if (this._config.panel) this.style.cssText = "display:block;max-width:980px;margin:24px auto;padding:0 16px";
      orderApply(this);
      const html = `<style>${TZ(CSS + (this.css ? this.css() : ""))}</style>${TZ(this.render(), false)}`;
      // A state update that changes nothing on the card (a thermostat repeating its reading) leaves it alone.
      // A forced render (after a drag, a tab) always goes: the DOM may have been changed by hand.
      if (!forced && html === this._html) return;
      this._html = html;
      // Live, not static (designer review 25.10): a value that changed eases in, a room that lit up glows in.
      const SEL = ".t,.cct,.hd b,.s,.lv,.w,.vals b,.v,.cond,.st0";
      const before = [...this.shadowRoot.querySelectorAll(SEL)].map((e) => e.textContent);
      const kind = this.shadowRoot.querySelector(".glass")?.className;
      const first = !this.shadowRoot.querySelector(".glass");
      // Safari/iOS (WebKit) lays the page out with the card empty while its content is replaced: a page scrolled
      // near its end lost the card's height and jumped up — to the very top for the climate card (owner, Горького 3,
      // 27.10: «перепрыгивают страницы» on every setpoint change). The card keeps its height until the new content is in.
      const h = first ? 0 : this.getBoundingClientRect().height;
      if (h) this.style.minHeight = `${h}px`;
      this.shadowRoot.innerHTML = html;
      if (h) requestAnimationFrame(() => (this.style.minHeight = ""));
      translate(this.shadowRoot);
      this.bind && this.bind();
      orderAfter(this);
      reAck(this);
      if (first || REDUCED) return;
      [...this.shadowRoot.querySelectorAll(SEL)].forEach((e, i) => {
        if (before[i] !== undefined && before[i] !== e.textContent) e.animate([{ opacity: 0.25, transform: "translateY(3px)" }, { opacity: 1, transform: "none" }], { duration: 240, easing: "cubic-bezier(.2,.8,.2,1)" });
      });
      const g = this.shadowRoot.querySelector(".glass");
      if (g && kind !== undefined && g.className !== kind) g.querySelector(".glow")?.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 450, easing: "ease-out" });
    }
    getCardSize() {
      return 3;
    }
    /** Put on its page: the block's key is known now — drawn in the block's order; a room tile takes its place. */
    connectedCallback() {
      if (!this._raw || !this._hass) return;
      CARDS.add(this);
      const fresh = this._okeyRaw !== this._raw;
      if (orderKey(this) && fresh) {
        this._sig = "";
        this._update();
      } else orderAfter(this);
    }
    /** Nothing to show (scenes that are in their blocks): the card and HA's wrapper around it take no room. */
    hideCard(hide) {
      // HA's own way (its «hide when empty» cards): `hidden` on the card and «card-visibility-changed» to its wrapper,
      // which hides itself (and an empty section) and shows again on the same event.
      if (this.hidden === hide) return;
      this.hidden = hide;
      this.style.display = hide ? "none" : "";
      this.dispatchEvent(new CustomEvent("card-visibility-changed", { detail: { value: !hide } }));
    }
    /** The scenes of this block (light / blinds) when they're shown in it — the integrator's choice or the client's. */
    sceneRow() {
      const c = this._config;
      const room = rowRoom(this, this.kind === "cover" ? "cover" : "light");
      if (!c.scenes?.length && !room) return "";
      const where = sceneWhere(this, this.kind === "cover" ? "cover" : "light");
      if (where !== "blocks") return "";
      return `<div class="srow"><span class="caps">${blockTitle(this, this.kind === "cover" ? "Сценарии штор" : "Сценарии света", this.kind === "cover" ? "cover_scenes" : "light_scenes")}</span>${sceneChips(this._hass, c.scenes || [], this.kind === "cover" ? "cover" : "light", room, this)}</div>`;
    }
  }

  // --- hauflo-hero: the house in one number ------------------------------------------------------
  /** An alarm card of this view is showing an alarm (Apple / Google: one alarm block) — the headline then says only the
   * state; the card has what to do and «Позвонить». */
  const alertShown = (card) => [...AGENT.cards].some((c) => c !== card && c.localName === "hauflo-alert" && c.isConnected && c._shown);
  class Hero extends EHCard {
    watched() {
      const c = this._config;
      const loud = Object.keys(this._hass?.states || {}).filter((id) => id.startsWith("siren.") || id.startsWith("alarm_control_panel."));
      const myRoom = this.home() && HOUSE.data?.favorites?.room ? this.roomCfg(HOUSE.data.favorites.room.path) : null;
      return [...(c.temperature || []), ...(c.humidity || []), ...(c.lights || []), ...Object.values(c.rooms_lights || {}).flat(), ...(c.alarms || []), ...(c.devices || []), c.weather, ...loud, ...(this.home() ? favItems() : []), ...(myRoom?.lights || []), myRoom?.climate].filter(Boolean);
    }
    /** The header of the dashboard's first page («Главная») — the one that shows this person's favourites. */
    home() {
      const views = lovelacePanel()?.lovelace?.config?.views;
      const cur = decodeURIComponent(location.pathname.split("/")[2] || "");
      if (!Array.isArray(views) || !views.length) return !!this._config?.all_alarms;
      return !cur || cur === "0" || cur === String(views[0].path ?? "");
    }
    /** «Избранное» (family review round 2, Apple / Google Home): «Моя комната» first, then the lights, blinds and scenes this
     * person pinned by a long press — big tiles; nothing pinned — nothing drawn (the page looks as the integrator made it). */
    favHtml() {
      const h = this._hass;
      const f = HOUSE.get(this)?.favorites;
      if (!f || !this.home()) return "";
      const items = (f.items || []).filter((id) => st(h, id));
      if (!items.length && !f.room) return "";
      const tile = (id) => {
        const s = st(h, id);
        const dom = id.split(".")[0];
        const on = dom === "cover" ? s.state !== "closed" && !nd(s) : s.state === "on";
        const dim = dom === "light" && (s.attributes.supported_color_modes || []).some((m) => m !== "onoff");
        const word = nd(s) ? "нет связи" : dom === "cover" ? coverWord(s) || (on ? "открыта" : "закрыта") : dom === "scene" || dom === "script" ? "сценарий" : on ? (dim ? `${brightnessPct(s)} %` : "вкл") : "выкл";
        const icon = dom === "cover" ? coverIcon({ entity: id, s }, {}) : dom === "scene" || dom === "script" ? "hauflo:play" : dom === "light" ? (on ? "hauflo:bulb-on" : "hauflo:bulb") : s.attributes.icon || "hauflo:power";
        return `<button class="ft ${dom} ${on && dom !== "scene" ? "on" : ""}" data-fav="${esc(id)}"><ha-icon icon="${esc(icon)}"></ha-icon><span class="fn">${esc(nameOf(s, id))}</span><span class="fs">${esc(word)}</span></button>`;
      };
      const room = f.room ? this.myRoomHtml(f.room) : "";
      return `<div class="favs"><span class="caps">Избранное</span><div class="fg">${room}${items.map(tile).join("")}</div></div>`;
    }
    /** The room tile of this dashboard with `path` (its lights and thermostat), from the dashboard's config. */
    roomCfg(path) {
      let hit = null;
      const walk = (v) => {
        if (hit || !v || typeof v !== "object") return;
        if (Array.isArray(v)) return v.forEach(walk);
        if (v.type === "custom:hauflo-room" && v.path === path) hit = v;
        else Object.values(v).forEach(walk);
      };
      walk(lovelacePanel()?.lovelace?.config?.views || []);
      return hit;
    }
    /** «Моя комната»: the room's name opens it; «Свет у меня» switches its light; «Потеплее» +step on its thermostat. */
    myRoomHtml(room) {
      const h = this._hass;
      const rc = this.roomCfg(room.path) || {};
      const lights = (rc.lights || []).filter((id) => st(h, id));
      const lit = lights.filter((id) => st(h, id).state === "on").length;
      const cs = rc.climate && st(h, rc.climate);
      const sp = cs && !nd(cs) && cs.attributes.preset_mode !== "building_protection" ? num(cs.attributes.temperature) : undefined;
      const want = this._warm && this._warm.id === rc.climate && Date.now() - this._warm.at < 10000 ? this._warm.v : sp;
      const cur = cs ? num(cs.attributes.current_temperature) : undefined;
      const light = lights.length
        ? `<button class="rb ${lit ? "on" : ""}" data-rlight="${esc(room.path)}" aria-pressed="${lit > 0}"><ha-icon icon="${lit ? "hauflo:bulb-on" : "hauflo:bulb"}"></ha-icon><span><b>Свет у меня</b><small>${lit ? (lights.length > 1 ? `вкл · ${lit} из ${lights.length}` : "вкл") : "выкл"}</small></span></button>`
        : "";
      const warm = want !== undefined
        ? `<button class="rb warm" data-rwarm="${esc(room.path)}"><ha-icon icon="mdi:thermometer-plus"></ha-icon><span><b>Потеплее</b><small>${esc(`задано ${fmt(want, want % 1 ? 1 : 0)}°${cur !== undefined ? ` · сейчас ${fmt(cur, 1)}°` : ""}`)}</small></span></button>`
        : "";
      return `<div class="ft room${light || warm ? " big" : ""}"><button class="rn" data-froom="${esc(room.path)}"><ha-icon icon="mdi:home-heart"></ha-icon><span class="fn" data-own>${esc(room.name)}</span><span class="fs">Моя комната ›</span></button>${light || warm ? `<div class="rbs">${light}${warm}</div>` : ""}</div>`;
    }
    css() {
      return `.top{display:flex;flex-wrap:wrap;justify-content:space-between;align-items:center;gap:8px 12px}.top .lab{flex:1 1 160px}
.out{display:inline-flex;align-items:center;gap:8px;max-width:100%;padding:6px 12px 6px 8px;border-radius:16px;background:var(--track);font:400 13px Onest,sans-serif;color:var(--ink2);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;cursor:pointer}.out>span:last-child{overflow:hidden;text-overflow:ellipsis}
.out ha-icon{--mdc-icon-size:20px;color:var(--amberT)}.out b{font:500 15px Onest,sans-serif;color:var(--ink)}
.lab{display:flex;align-items:center;gap:10px;flex-wrap:wrap;min-width:0}.away{display:inline-flex;align-items:center;gap:6px;padding:4px 10px 4px 8px;border-radius:12px;background:color-mix(in srgb,var(--amber) 20%,transparent);color:var(--ink);font:500 12px Onest,sans-serif;white-space:nowrap;cursor:pointer}.away ha-icon{--mdc-icon-size:15px;color:var(--amberT)}.away.part{background:transparent;box-shadow:inset 0 0 0 1.5px var(--amber)}
.hl{font:300 34px/1.1 Onest,sans-serif;letter-spacing:-.03em;margin-top:10px;display:flex;align-items:center;gap:12px}.hl.bad{color:var(--alertT)}.hl .dot{width:10px;height:10px;border-radius:5px;flex:0 0 auto}
.hl2{font:400 13px Onest,sans-serif;color:var(--ink2);margin-top:6px;display:flex;align-items:center;gap:0}.hl2[data-svc]{cursor:pointer;min-height:32px}
.hl.good{font-weight:400}.todo2{font:500 14px Onest,sans-serif;line-height:1.45;color:var(--ink);margin-top:10px;max-width:560px}
.call{display:flex;align-items:center;gap:10px;min-height:52px;margin-top:12px;padding:0 16px;border-radius:16px;text-decoration:none;color:var(--ink);background:color-mix(in srgb,var(--alert) 12%,transparent);box-shadow:inset 0 0 0 1px color-mix(in srgb,var(--alert) 40%,transparent);font:600 15px Onest,sans-serif;max-width:420px}
.call ha-icon{--mdc-icon-size:22px;color:var(--alertT)}.call .cl{flex:1;min-width:0;display:flex;flex-direction:column;padding:8px 0}.call .cl span{white-space:normal;overflow-wrap:break-word;hyphens:auto}.call b{font:400 13px Onest,sans-serif;color:var(--ink2)}
.h{padding:6px 8px 4px}.t{font-size:112px;line-height:.9;margin-top:4px}.t sup{font:300 34px Onest,sans-serif;vertical-align:top;position:relative;top:10px;margin-left:4px;letter-spacing:0;color:var(--ink2)}
.none{font:300 30px/1.2 Onest,sans-serif;letter-spacing:-.02em;color:var(--ink2);margin:14px 0 6px}
.f{display:flex;gap:20px;margin-top:16px;flex-wrap:wrap}.f div{display:flex;flex-direction:column;gap:5px}.f b{font:400 17px Onest,sans-serif;letter-spacing:-.01em}
.dot{display:inline-block;width:6px;height:6px;border-radius:3px;margin-right:7px;vertical-align:middle}
.favs{margin-top:22px}.favs>.caps{display:block;margin:0 0 10px 2px}.fg{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px}
.ft{min-height:104px;padding:14px;border-radius:24px;display:flex;flex-direction:column;align-items:flex-start;justify-content:space-between;gap:6px;text-align:left;background:var(--eh-glass,var(--ha-card-background,var(--card-background-color)));box-shadow:var(--eh-edge,inset 0 0 0 1px var(--hair)),var(--eh-deep,none);touch-action:pan-y;user-select:none;-webkit-user-select:none;-webkit-touch-callout:none}
.ft ha-icon{--mdc-icon-size:26px;color:var(--ink2)}.ft .fn{font:500 15px Onest,sans-serif;line-height:1.25;overflow-wrap:break-word;hyphens:auto;white-space:normal}.ft .fs{font:400 13px Onest,sans-serif;color:var(--ink2)}
.ft.room.big{grid-column:1/-1;min-height:0;gap:12px}.ft .rn{display:flex;flex-direction:column;align-items:flex-start;gap:4px;text-align:left;min-height:44px;width:100%}
.rbs{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:8px;width:100%}.rb{display:flex;align-items:center;gap:10px;min-height:64px;padding:10px 14px;border-radius:18px;text-align:left;background:var(--track);color:var(--ink)}
.rb ha-icon{--mdc-icon-size:26px;color:var(--ink2);flex:none}.rb span{display:flex;flex-direction:column;min-width:0}.rb b{font:600 16px Onest,sans-serif;line-height:1.2}.rb small{font:400 14px Onest,sans-serif;color:var(--ink2);margin-top:2px}
.rb.on{background:color-mix(in srgb,var(--amber) 26%,transparent)}.rb.on ha-icon{color:var(--amberT)}.rb.warm ha-icon{color:var(--amberT)}.rb:active{transform:scale(.97)}
.ft.on{background:color-mix(in srgb,var(--amber) 22%,var(--eh-glass,var(--card-background-color)))}.ft.on ha-icon{color:var(--amberT)}.ft.cover.on{background:color-mix(in srgb,var(--ice) 18%,var(--eh-glass,var(--card-background-color)))}.ft.cover.on ha-icon{color:var(--ice)}.ft.room ha-icon,.ft.scene ha-icon{color:var(--mintT)}`;
    }
    render() {
      const h = this._hass;
      const c = this._config;
      const t = avg((c.temperature || []).map((id) => tempOf(st(h, id))));
      const hum = avg((c.humidity || []).map((id) => num(st(h, id)?.state)));
      const litRooms = Object.values(c.rooms_lights || {}).filter((ids) => ids.some((id) => st(h, id)?.state === "on")).length;
      const lamps = [...new Set(Object.values(c.rooms_lights || {}).flat())].filter((id) => st(h, id)?.state === "on").length;
      // Lights of no room (house / floor commands, «Весь свет в доме») count too: «Свет выключен» next to a lit
      // «Весь свет в доме» read as a lie (persona review round 4).
      const outerOn = [...new Set(c.lights || [])].filter((id) => st(h, id)?.state === "on");
      // Back to normal and not yet accepted is no open alarm of the house (only the alert card asks to accept it).
      const openList = openAlarms(this, c.alarms || []).filter((a) => !a.returned);
      const alarms = openList.map((a) => a.entity);
      // Accepted: still an alarm until it clears, but no longer red (ISA-18.2: acknowledged ≠ new).
      const critical = openList.some((a) => a.critical !== false && !a.ackedBy);
      // The headline counts what still waits for a «Принято» (family review round 2: «19 аварий» stayed after accepting).
      const pending = openList.filter((a) => !a.ackedBy).length;
      // A siren sounding or the security alarm triggered is no «Всё спокойно» (persona round 3: one source of truth).
      const loud = Object.values(h.states).filter((s) => (s.entity_id.startsWith("siren.") && s.state === "on") || (s.entity_id.startsWith("alarm_control_panel.") && s.state === "triggered"));
      const ok = !alarms.length && !loud.length;
      // A thermostat / AC with no setpoint heard, a blind HA lost: no «Всё спокойно» over them (persona round 5).
      // On the bus but no setpoint heard yet (right after a restart) is not «no link» (review round 2): said apart.
      const waiting = (c.devices || []).filter((id) => {
        const s = st(h, id);
        return id.startsWith("climate.") && !!s && !nd(s) && num(s.attributes.temperature) === undefined && num(s.attributes.target_temp_low) === undefined;
      });
      const climates = (c.devices || []).filter((id) => id.startsWith("climate.") && !nd(st(h, id)));
      const offline = (c.devices || []).filter((id) => {
        const s = st(h, id);
        if (!s || s.state === "unavailable") return true;
        return id.startsWith("climate.") && s.state === "unknown";
      });
      // An alarm sensor with no data is no «в норме»: it would stay silent through a leak.
      const deaf = (c.alarms || []).filter((id) => nd(st(h, id)));
      offline.push(...deaf.filter((id) => !offline.includes(id)));
      const lightIds = [...new Set([...Object.values(c.rooms_lights || {}).flat(), ...(c.lights || [])])];
      const lightsUnknown = lightIds.length > 0 && lightIds.every((id) => nd(st(h, id)));
      // Some lights silent is no «Всё спокойно · свет выключен» either (narrow round 2, D9: one of two had no data).
      // Only lights silent for over 10 minutes count (family review round 3: a restart's «нет данных» read as broken).
      const longNd = (id) => nd(st(h, id)) && (!st(h, id) || Date.now() - Date.parse(st(h, id).last_changed || 0) > 600000);
      const lightsDeaf = lightIds.filter(longNd).length;
      // Just deployed / HA just started: the lamps are being read, not silent (D9).
      const lightsFresh = lightIds.some((id) => nd(st(h, id))) && (!lightsDeaf || updating(this, lightIds.map((id) => st(h, id))));
      const roomsWord = (n) => (n % 10 === 1 && n % 100 !== 11 ? "комната" : [2, 3, 4].includes(n % 10) && ![12, 13, 14].includes(n % 100) ? "комнаты" : "комнат");
      // What the big number is, said (owner 2026-10-25: «что за 23 сверху?»), and the weather outside in one line
      // instead of a big card.
      const w = st(h, c.weather);
      const wt = w ? num(w.attributes.temperature) : undefined;
      const [wword, wicon] = w ? WEATHER[w.state] || [w.state, "mdi:weather-partly-cloudy"] : [];
      const outside = w && wt !== undefined && wt > -70 && wt < 65 ? `<span class="out" data-w="1"><ha-icon icon="${wicon}"></ha-icon><span>на улице</span><b>${Math.round(wt)}°</b><span>· ${esc(String(wword).toLowerCase())}</span></span>` : "";
      // The house's state is the headline, not an average temperature (owner 2026-10-25: «в среднем по комнатам —
      // глупо, зато на пол-экрана»). The rooms' temperatures as a range, small.
      const temps = (c.temperature || []).map((id) => tempOf(st(h, id))).filter((v) => v !== undefined);
      const tmin = temps.length ? Math.min(...temps) : undefined;
      const tmax = temps.length ? Math.max(...temps) : undefined;
      const devWord = (n) => (n % 10 === 1 && n % 100 !== 11 ? "устройство" : [2, 3, 4].includes(n % 10) && ![12, 13, 14].includes(n % 100) ? "устройства" : "устройств");
      const thermoWord = (n) => (n % 10 === 1 && n % 100 !== 11 ? "термостат" : [2, 3, 4].includes(n % 10) && ![12, 13, 14].includes(n % 100) ? "термостата" : "термостатов");
      // ONE clear state of the house (family review round 2, Gira / JUNG panels: a big green «Всё хорошо», a red «Авария»
      // with what to do and «Позвонить»). Built on the calm headline of 29.10 (deploy flow): what has no data is a quiet
      // line, never the hero. A safety sensor without link (leak, smoke) stays big — silence over a leak isn't «хорошо»;
      // other devices without link are a quiet line that opens the «Сервис» page.
      const svc = serviceOf(this);
      const deafOnly = offline.filter((id) => deaf.includes(id));
      const otherOff = offline.filter((id) => !deaf.includes(id));
      const top = openList.find((a) => !a.ackedBy) || openList[0];
      const topS = top && st(h, top.entity);
      const topAn = top && topS ? alarmName(h, topS, top.entity) : null;
      const topRoom = top ? c.rooms?.[top.entity] || (topAn?.tech ? areaName(h, top.entity) : "") : "";
      const topName = top ? `${topAn ? topAn.name : top.entity}${topRoom ? ` · ${topRoom}` : ""}` : "";
      // A leak of any integration's sensor (Zigbee, Aqara…) that no rule watches yet is no «Всё хорошо» either (item 9).
      const wetOther = this.home() || c.all_alarms ? Object.values(h.states).filter((x) => x.entity_id.startsWith("binary_sensor.") && x.attributes?.device_class === "moisture" && x.state === "on" && !h.entities?.[x.entity_id]?.hidden && !(c.alarms || []).includes(x.entity_id)) : [];
      const quiet = (txt, dot = "var(--ink2)", link = false) => `<div class="hl2"${link ? ' role="link" tabindex="0" data-svc' : ""}><span class="dot" style="background:${dot}"></span>${txt}${link ? `<span aria-hidden="true">&nbsp;›</span>` : ""}</div>`;
      const headline = !alarms.length && !loud.length && wetOther.length
        ? `<div class="hl bad"><span class="dot" style="background:var(--alert);box-shadow:0 0 12px var(--alert)"></span>Протечка: ${esc(alarmName(h, wetOther[0], wetOther[0].entity_id).name)}${areaName(h, wetOther[0].entity_id) ? ` · ${esc(areaName(h, wetOther[0].entity_id))}` : ""}</div><div class="todo2">${esc(TODO.moisture)}</div>${callLink(svc)}`
        : ok && deafOnly.length
        ? `<div class="hl"><span class="dot" style="background:var(--amber);box-shadow:0 0 12px var(--amberGlow)"></span>Нет связи · ${deafOnly.length} ${sensorsWord(deafOnly.length)}</div>${otherOff.length ? quiet(`Нет связи · ${otherOff.length} ${devWord(otherOff.length)}`, "var(--amber)", true) : ""}`
        : ok
        ? `<div class="hl good"><span class="dot" style="background:var(--mint);box-shadow:0 0 12px var(--mint)"></span>Всё хорошо</div>${
            otherOff.length
              ? quiet(`Нет связи · ${otherOff.length} ${devWord(otherOff.length)}`, "var(--amber)", true)
              : waiting.length && waiting.length === climates.length
              ? quiet("Климат: нет данных")
              : lightsFresh
              ? quiet("Обновляется…")
              : lightsDeaf
              ? quiet(`Нет данных · ${lightsUnknown ? "свет" : `${lightsDeaf} ${lampsWord(lightsDeaf)}`}`, "var(--ink2)", true)
              : ""
          }`
        : !alarms.length
        ? `<div class="hl bad"><span class="dot" style="background:var(--alert);box-shadow:0 0 12px var(--alert)"></span>${loud.some((s) => s.entity_id.startsWith("alarm_control_panel.")) ? "Тревога · охрана" : "Звучит сирена"}</div>${callLink(svc)}`
        : `<div class="hl bad"${critical ? "" : ' style="color:var(--amberT)"'}><span class="dot" style="background:${critical ? "var(--alert)" : "var(--amber)"};box-shadow:0 0 12px ${critical ? "var(--alert)" : "var(--amber)"}"></span>${esc(alarms.length === 1 ? `Авария: ${topName}` : pending ? `${pending} ${pluralAlarms(pending)}` : `Приняты · ${alarms.length} ${pluralAlarms(alarms.length)}`)}</div>${
            alarms.length > 1 && pending ? `<div class="hl2">${esc(`Первая: ${topName}`)}</div>` : ""
          }${alertShown(this) ? "" : `<div class="todo2">${esc(alarmTodo(topS?.attributes?.device_class, !!svc?.phone, false))}</div>${callLink(svc)}`}`;
      const pres = PRES.get(this);
      const away = pres?.enabled;
      // Working only in part: said on the chip too, amber outline (final review 28.10).
      const part = away ? presPartial(pres) : null;
      const awayWord = !part ? "Нет дома · имитация" : part.kind === "covers" ? "Нет дома · только шторы" : part.kind === "lights" ? "Нет дома · только свет" : "Нет дома · сегодня ничего";
      return `<div class="h"><div class="top"><span class="lab"><span class="caps">${esc(c.label || "Дома")}</span>${away ? `<span class="away${part ? " part" : ""}" role="link" tabindex="0" data-away><ha-icon icon="${part ? "mdi:alert-outline" : "mdi:home-export-outline"}"></ha-icon>${awayWord}</span>` : ""}</span>${outside}</div>
${headline}${this.favHtml()}
<div class="f">${tmin !== undefined ? `<div><span class="caps">В комнатах</span><b>${tmin === tmax ? `${fmt(tmin)}°` : `${fmt(Math.floor(tmin))}–${fmt(Math.ceil(tmax))}°`}</b></div>` : ""}
${hum !== undefined ? `<div><span class="caps">Влажность</span><b>${fmt(hum)} %</b></div>` : ""}
<div><span class="caps">Свет</span><b>${lightsUnknown ? (lightsFresh ? "обновляется…" : "нет данных") : litRooms ? `${lamps} ${lamps % 10 === 1 && lamps % 100 !== 11 ? "лампа" : [2, 3, 4].includes(lamps % 10) && ![12, 13, 14].includes(lamps % 100) ? "лампы" : "ламп"} в ${litRooms} ${litRooms === 1 ? "комнате" : "комнатах"}${outerOn.length ? ` · ${esc(nameOf(st(h, outerOn[0]), outerOn[0]))}${outerOn.length > 1 ? ` +${outerOn.length - 1}` : ""}` : ""}` : outerOn.length ? `включён: ${esc(nameOf(st(h, outerOn[0]), outerOn[0]))}${outerOn.length > 1 ? ` +${outerOn.length - 1}` : ""}` : lightsDeaf ? (lightsFresh ? "выключен · обновляется…" : `выключен · ${lightsDeaf} без данных`) : "выключен"}</b></div>
${ok && !offline.length && waiting.length && waiting.length < climates.length ? `<div><span class="caps">Климат</span><b>${waiting.length} из ${climates.length} без данных</b></div>` : ""}
${(c.alarms || []).length && ok ? `<div><span class="caps">Датчики</span><b>${deaf.length ? `<span class="dot" style="background:var(--ink2)"></span>${deaf.length} ${sensorsWord(deaf.length)} без связи` : "в норме"}</b></div>` : ""}</div></div>`;
    }
    getGridOptions() {
      return { columns: 12, rows: "auto" };
    }
  }

  Hero.prototype.bind = function () {
    this.shadowRoot.querySelector("[data-w]")?.addEventListener("click", () => moreInfo(this, this._config.weather));
    // Favourites: a tap does the obvious (a light toggles, a blind goes / stops, a scene runs, the room opens); a long press —
    // the control sheet (brightness, «Убрать из избранного»).
    this.shadowRoot.querySelectorAll("[data-fav]").forEach((b) => {
      const id = b.dataset.fav;
      longPress(b, () => openTile(this, id));
      b.addEventListener("click", () => {
        if (GST.lp) return;
        const s = st(this._hass, id);
        if (!s) return;
        const dom = id.split(".")[0];
        haptic(this);
        if (dom === "cover") openTile(this, id);
        else if (dom === "scene" || dom === "script") sendAck(this, b, dom, "turn_on", { entity_id: id }, () => okToast(LANG === "ru" ? `Сценарий «${nameOf(s, id)}» включён` : `Scene «${nameOf(s, id)}» is on`));
        else call(this._hass, dom === "light" ? "light" : "homeassistant", "toggle", { entity_id: id });
      });
    });
    this.shadowRoot.querySelector("[data-froom]")?.addEventListener("click", (ev) => navigate(ev.currentTarget.dataset.froom));
    this.shadowRoot.querySelector("[data-rlight]")?.addEventListener("click", (ev) => {
      const lights = (this.roomCfg(ev.currentTarget.dataset.rlight)?.lights || []).filter((id) => st(this._hass, id));
      if (!lights.length) return;
      const on = lights.some((id) => st(this._hass, id).state === "on");
      const ls = lights.filter((id) => id.startsWith("light."));
      const other = lights.filter((id) => !id.startsWith("light."));
      haptic(this);
      if (ls.length) sendAck(this, ev.currentTarget, "light", on ? "turn_off" : "turn_on", { entity_id: ls });
      if (other.length) call(this._hass, "homeassistant", on ? "turn_off" : "turn_on", { entity_id: other });
    });
    this.shadowRoot.querySelector("[data-rwarm]")?.addEventListener("click", (ev) => {
      const id = this.roomCfg(ev.currentTarget.dataset.rwarm)?.climate;
      const s = id && st(this._hass, id);
      if (!s) return;
      const a = s.attributes;
      const step = Math.max(num(a.target_temp_step) || 0.5, 0.5);
      const base = this._warm && this._warm.id === id && Date.now() - this._warm.at < 10000 ? this._warm.v : num(a.temperature);
      if (base === undefined) return;
      const v = Math.min(num(a.max_temp) ?? 35, base + step);
      this._warm = { id, v, at: Date.now() };
      haptic(this);
      // Several quick taps: one command with the last value (as the climate card's ±).
      clearTimeout(this._warmT);
      this._warmT = setTimeout(() => call(this._hass, "climate", "set_temperature", { entity_id: id, temperature: this._warm.v }), 600);
      this._sig = "";
      this._update();
    });
    // «Нет связи · N устройств ›»: the «Сервис» page (where the house's own things are).
    const svcLine = this.shadowRoot.querySelector("[data-svc]");
    svcLine?.addEventListener("click", () => AUTO.open(this._hass));
    svcLine?.addEventListener("keydown", (e) => e.key === "Enter" && AUTO.open(this._hass));
    // The chip opens the dashboard's «Автоматизации» tab, where the simulation is switched.
    const away = this.shadowRoot.querySelector("[data-away]");
    const open = () => AUTO.open(this._hass, ".pres");
    away?.addEventListener("click", open);
    away?.addEventListener("keydown", (e) => e.key === "Enter" && open());
  };

  // --- hauflo-room: a room that glows with its state; tap = its page, hold = its light -----------
  // Н3: the leak sensors of a room — every moisture binary_sensor of ANY integration (Neptun, Aqara, Zigbee…) in the room's
  // HA area, beside those the cloud gave. The area is the card's own `area`, else that of its first device in HA's registry.
  // Nothing is controlled from here: it only says «dry» or «leak: where» (the valve is an explicit button of the room page).
  const MOIST = new WeakMap();
  const leakIds = (card) => {
    const h = card._hass;
    const c = card._config;
    const area = c.area || [...(c.lights || []), c.climate, ...(c.covers || []), ...(c.alarms || [])].map((id) => id && areaOf(h, id)).find(Boolean);
    if (!area || !h?.entities) return [];
    let by = MOIST.get(h.entities);
    if (!by) {
      by = new Map();
      for (const [id, s] of Object.entries(h.states || {})) {
        if (!id.startsWith("binary_sensor.") || s.attributes?.device_class !== "moisture") continue;
        const e = h.entities[id];
        if (e?.hidden || e?.disabled_by) continue;
        const a = areaOf(h, id);
        if (a) by.set(a, [...(by.get(a) || []), id]);
      }
      MOIST.set(h.entities, by);
    }
    return (by.get(area) || []).filter((id) => !(c.alarms || []).includes(id));
  };
  class Room extends EHCard {
    watched() {
      const c = this._config;
      return [...(c.lights || []), c.climate, ...(c.alarms || []), ...(this._hass ? leakIds(this) : []), ...(c.covers || []), ...(c.temps || []).map((x) => x.entity), ...actIds(c.activity)].filter(Boolean);
    }
    css() {
      return `.r{height:172px;padding:16px;display:flex;flex-direction:column;justify-content:space-between;cursor:pointer;user-select:none;-webkit-user-select:none}
.r>*:not(.glow):not(.ph){position:relative}.top{display:flex;justify-content:space-between;align-items:flex-start;color:var(--ink2)}.top .caps{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.mid{display:flex;align-items:center;justify-content:space-between}
.ri{width:46px;height:46px;border-radius:16px;display:grid;place-items:center;background:var(--track);color:var(--ink2)}.ri ha-icon{--mdc-icon-size:24px}.rb{font:500 20px Onest,sans-serif;letter-spacing:-.02em;color:var(--ink)}
.lit .ri{background:color-mix(in srgb,var(--amber) 22%,transparent);color:var(--amberT)}.cold .ri{background:color-mix(in srgb,var(--ice) 22%,transparent);color:var(--ice)}
.alarm .ri{background:color-mix(in srgb,var(--alert) 18%,transparent);color:var(--alertT)}
.t{font-size:40px;line-height:1}.t small{font:300 15px Onest,sans-serif;color:var(--ink2);letter-spacing:0;margin-left:2px}
.tt{display:flex;flex-direction:column;align-items:flex-end;min-width:0}.t2+.t2{margin-top:1px}.t2{font:400 12px Onest,sans-serif;color:var(--ink2);margin-top:3px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:100%}.pic .t2{color:#fff;text-shadow:0 1px 2px rgba(0,0,0,.55)}
.s{font:400 13px Onest,sans-serif;color:var(--ink2);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.chips{display:flex;gap:10px;margin-top:5px;font:500 12px Onest,sans-serif;color:var(--ink2);white-space:nowrap;overflow:hidden}
.chips span{display:inline-flex;align-items:center;gap:3px}:host{container-type:inline-size}@container (max-width:200px){.chips span:nth-child(n+3){display:none}}.chips ha-icon{--mdc-icon-size:14px}.chips .on{color:var(--amberT)}.chips .op{color:var(--ice)}
.lamp{width:8px;height:8px;border-radius:4px;background:var(--amber);box-shadow:0 0 12px var(--amber),0 0 3px var(--amber)}
.lit .glow{background:radial-gradient(45% 45% at 75% 22%,var(--amberGlow),transparent 70%)}
.cold .glow{background:radial-gradient(45% 45% at 75% 22%,var(--iceGlow),transparent 70%)}
.alarm .glow{background:radial-gradient(50% 50% at 70% 25%,var(--alertGlow),transparent 70%)}
.alarm::after{content:"";position:absolute;inset:0;border-radius:inherit;box-shadow:inset 0 0 0 1.5px var(--alert);pointer-events:none}
.alarm .s{color:var(--alertT);font-weight:600}
.alarm.warn{--alert:var(--amber);--alertGlow:var(--amberGlow)}
.ph{position:absolute;inset:0;background-size:cover;background-position:center}
.ph::after{content:"";position:absolute;inset:0;background:linear-gradient(180deg,rgba(0,0,0,.42) 0%,rgba(0,0,0,.3) 45%,rgba(0,0,0,.64) 100%)}
.pic,.pic .top,.pic .s,.pic .chips,.pic .t small,.pic .caps,.pic .chips .on,.pic .chips .op{color:#fff}
.pic .caps,.pic .s,.pic .chips{text-shadow:0 1px 2px rgba(0,0,0,.55),0 0 14px rgba(0,0,0,.45)}.pic .t{text-shadow:0 1px 12px rgba(0,0,0,.5),0 0 2px rgba(0,0,0,.35)}
.pic .ri,.pic.lit .ri,.pic.cold .ri{background:rgba(255,255,255,.2);color:#fff;-webkit-backdrop-filter:blur(12px);backdrop-filter:blur(12px)}
.pic .glow{opacity:.45}.pic .ph{filter:brightness(1.08)}`;
    }
    state() {
      const h = this._hass;
      const c = this._config;
      const openList = openAlarms(this, c.alarms || []).filter((a) => !a.returned);
      const alarmsOn = openList.map((a) => st(h, a.entity)).filter(Boolean);
      const alarm = alarmsOn[0];
      const on = (c.lights || []).map((id) => st(h, id)).filter((s) => s && s.state === "on");
      const clim = st(h, c.climate);
      const target = clim ? num(clim.attributes.temperature) : undefined;
      // Nothing from the bus yet (no room temperature): no «Нагрев» from the default mode (integrator run 25.10).
      // What it DOES, not its mode («Нагрев» over a room warmer than the goal — owner 28.10, Д16); the zone's heat / cool /
      // valve sources of the project (`activity`) count too (Д13).
      const now = clim && num(clim.attributes.current_temperature) !== undefined ? climateNow(h, clim, c.activity) : undefined;
      const cooling = now === "cool";
      // A room in alarm: the red frame and the sign say it; a floor tile names where (persona review 25.10: said once).
      // Priority by colour (ISA-101): only a critical one is red, the rest amber — as on the alert card.
      if (alarm) return { kind: openList.some((a) => a.critical !== false && !a.ackedBy) ? "alarm" : "alarm warn", text: c.rooms?.[alarm.entity_id] ? `${c.rooms[alarm.entity_id]}${alarmsOn.length > 1 ? ` и ещё ${alarmsOn.length - 1}` : ""}` : alarmsOn.length > 1 ? `${alarmsOn.length} ${pluralAlarms(alarmsOn.length)}` : "" };
      const wet = leakIds(this).map((id) => st(h, id)).filter((s) => s && s.state === "on");
      if (wet.length) return { kind: "alarm", text: `Протечка: ${alarmName(h, wet[0], wet[0].entity_id).name}${wet.length > 1 ? ` и ещё ${wet.length - 1}` : ""}` };
      const deaf = (c.alarms || []).filter((id) => nd(st(h, id))).length;
      if (deaf) return { kind: "", text: `Нет связи · ${deaf} ${sensorsWord(deaf)}` };
      if (on.length) {
        const dim = on.map((s) => (num(s.attributes.brightness) !== undefined ? brightnessPct(s) : undefined)).filter((x) => x !== undefined);
        return { kind: "lit", text: dim.length ? `Свет · ${Math.round(avg(dim))} %` : "Свет включён" };
      }
      if (cooling) return { kind: "cold", text: `Охлаждает${target !== undefined ? ` до ${fmt(target)}°` : ""}` };
      // Warmer than the goal while a valve is open (the integrator's KNX logic heats the room): «Греет · клапаны открыты», not «до 24°».
      if (now === "heat") { const rc = num(clim.attributes.current_temperature); return { kind: "", text: target !== undefined && !(rc >= target) ? `Греет до ${fmt(target)}°` : c.activity?.valves?.length ? "Греет · клапаны открыты" : "Греет" }; }
      const lights = (c.lights || []).map((id) => st(h, id));
      const watched = [...lights, ...(c.climate ? [clim] : [])];
      if (watched.length && watched.every(nd)) return { kind: "", text: updating(this, watched) ? "Обновляется…" : "Нет данных" };
      // Some lights silent: «Всё выключено» would be a guess (round 3, D9) — say how many have no data, as the hero does.
      const lightsDeaf = lights.filter(nd).length;
      // Short enough for a 390 px tile (round 4, D1: «Нет данных · 3 светил…»); the hero gives the count.
      if (lightsDeaf) return { kind: "", text: updating(this, lights) ? "Свет: обновляется…" : "Свет: нет данных" };
      if (clim && nd(clim)) return { kind: "", text: "Климат · нет данных" };
      // A tile that is no room («Автоматизации» in «Сервис», owner 29.10) says what it is, not «Всё в норме».
      if (c.subtitle) return { kind: "", text: String(c.subtitle) };
      return { kind: "", text: lights.length ? "Всё выключено" : "Всё в норме" };
    }
    /** «Без протечек» when the room has moisture sensors (any integration) and they all say dry; a silent sensor is said. */
    leakChip() {
      const h = this._hass;
      const ids = [...leakIds(this), ...(this._config.alarms || []).filter((id) => st(h, id)?.attributes.device_class === "moisture")];
      if (!ids.length) return "";
      const deaf = ids.filter((id) => nd(st(h, id))).length;
      if (ids.some((id) => st(h, id)?.state === "on")) return "";
      return deaf ? `<span class="op"><ha-icon icon="mdi:water-off-outline"></ha-icon>датчик: нет связи</span>` : `<span><ha-icon icon="mdi:water-check-outline"></ha-icon>без протечек</span>`;
    }
    render() {
      const h = this._hass;
      const c = this._config;
      const s = this.state();
      const clim = st(h, c.climate);
      const t = clim ? num(clim.attributes.current_temperature) : avg((c.temperature || []).map((id) => tempOf(st(h, id))));
      const hum = clim ? num(clim.attributes.current_humidity) : undefined;
      // The room's other temperatures (the floor, a second zone) under the big one — a room has more than one (owner 27.10).
      const others = (c.temps || []).map((x) => ({ name: x.name || "", v: tempOf(st(h, x.entity)) })).filter((x) => x.v !== undefined);
      const lightsOn = (c.lights || []).filter((id) => st(h, id)?.state === "on").length;
      const coversOpen = (c.covers || []).filter((id) => ["open", "opening"].includes(st(h, id)?.state)).length;
      const mark = s.kind === "lit" ? `<span class="lamp"></span>` : s.kind === "cold" ? `<ha-icon icon="hauflo:snow" style="color:var(--ice)"></ha-icon>` : s.kind.startsWith("alarm") ? `<ha-icon icon="hauflo:warning" style="color:var(--alertT)"></ha-icon>` : "";
      // What's in the room at a glance (owner 2026-10-25: the tiles looked empty) — the room's icon, and a photo when its HA area has one.
      const chips = [
        // «выкл» only when every lamp said so — not under «Свет: нет данных» (round 4).
        (c.lights || []).length && (lightsOn || !(c.lights || []).some((id) => nd(st(h, id)))) ? `<span class="${lightsOn ? "on" : ""}"><ha-icon icon="${lightsOn ? "hauflo:bulb-on" : "hauflo:bulb"}"></ha-icon>${lightsOn ? lightsOn : "выкл"}</span>` : "",
        // «закр.» only when the blinds said so: unknown ones say nothing (persona round 3: tile «закр.», card «Нет данных»).
        (c.covers || []).some((id) => ["open", "opening", "closed", "closing"].includes(st(h, id)?.state)) ? `<span class="${coversOpen ? "op" : ""}"><ha-icon icon="hauflo:blind"></ha-icon>${coversOpen ? `откр. ${coversOpen}` : "закр."}</span>` : "",
        hum !== undefined ? `<span><ha-icon icon="hauflo:humidity"></ha-icon>${fmt(hum)} %</span>` : "",
        this.leakChip(),
      ].filter(Boolean);
      return `<div class="glass r ${s.kind} ${c.picture ? "pic" : ""}" role="button" tabindex="0" aria-label="${esc(c.name)}">${c.picture ? `<div class="ph" style="background-image:url('${esc(c.picture)}')"></div>` : ""}<div class="glow"></div>
<div class="top"><span class="caps">${esc(c.name)}</span>${mark}</div>
<div class="mid"><span class="ri">${c.badge ? `<b class="rb">${esc(c.badge)}</b>` : `<ha-icon icon="${esc(c.icon || "hauflo:home")}"></ha-icon>`}</span><div class="tt"><div class="t thin">${t === undefined ? "" : `${Math.round(t)}<small>°</small>`}</div>${others.slice(0, 2).map((x) => `<div class="t2">${esc(x.name)} ${fmt(x.v)}°</div>`).join("")}</div></div>
<div><div class="s">${esc(s.text)}</div>${chips.length ? `<div class="chips">${chips.join("")}</div>` : ""}</div></div>`;
    }
    bind() {
      const el = this.shadowRoot.querySelector(".r");
      let timer;
      let held = false;
      // No re-render while the finger is down: a state change mid-press replaced the card and its
      // release then opened the room after a hold.
      const release = () => setTimeout(() => {
        this._busy = false;
        this._update();
      }, 300);
      let at = null;
      el.addEventListener("pointerdown", (ev) => {
        held = false;
        at = [ev.clientX, ev.clientY];
        this._busy = true;
        timer = setTimeout(() => {
          held = true;
          haptic(this, "medium");
          const lights = this._config.lights || [];
          if (!lights.length) return;
          const anyOn = lights.some((id) => st(this._hass, id)?.state === "on");
          call(this._hass, "light", anyOn ? "turn_off" : "turn_on", { entity_id: lights });
        }, 550);
      });
      const cancel = () => {
        clearTimeout(timer);
        if (this._busy) release();
      };
      // A finger that moves is scrolling the page: no hold, no opening.
      el.addEventListener("pointermove", (ev) => at && Math.hypot(ev.clientX - at[0], ev.clientY - at[1]) > 8 && (clearTimeout(timer), (at = null)));
      el.addEventListener("pointerleave", cancel);
      el.addEventListener("pointercancel", cancel);
      el.addEventListener("pointerup", () => {
        cancel();
        const still = at !== null;
        at = null;
        if (!held && still && GST.ok && this._config.path) {
          haptic(this);
          navigate(this._config.path);
        }
      });
      el.addEventListener("keydown", (e) => e.key === "Enter" && this._config.path && navigate(this._config.path));
    }
    getGridOptions() {
      return { columns: 6, rows: "auto" };
    }
  }

  // --- hauflo-scenes: scenes as a row of buttons in a fixed order ---------------------------------
  // No «active scene»: a scene is a moment, not a state — touch one group and it isn't that scene any
  // more (owner 2026-10-25: «Сейчас: Утро» was a fake). A pressed button glows for a moment, nothing moves.
  class Scenes extends EHCard {
    watched() {
      return [...(this._config.scenes || []), ...(this._config.rows || []).flatMap((r) => r.scenes || [])].map((s) => s.entity);
    }
    css() {
      return `.m{padding:14px 14px 14px 16px;border-radius:24px}.hd{display:flex;align-items:center;gap:10px;margin-bottom:10px}
.ic{width:30px;height:30px;border-radius:10px;display:grid;place-items:center;background:color-mix(in srgb,var(--acc) 20%,transparent);color:var(--acc)}
.ic ha-icon{--mdc-icon-size:17px}
.row{display:flex;flex-wrap:wrap;gap:8px}
.row button{height:46px;padding:0 16px 0 13px;border-radius:15px;display:flex;align-items:center;gap:8px;flex:0 1 auto;max-width:100%;color:var(--ink);box-shadow:var(--eh-edge,inset 0 0 0 1px var(--line));background:var(--eh-glass,transparent);font:500 13px Onest,sans-serif;min-width:0;transition:background .3s,box-shadow .3s,color .3s}
.row button span{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.row button ha-icon{color:var(--acc);flex:0 0 auto}
.row button:active{transform:scale(.97)}
.row button.go{background:color-mix(in srgb,var(--acc) 26%,transparent);box-shadow:0 0 22px color-mix(in srgb,var(--accGlow) 60%,transparent)}
.light{--acc:var(--amber);--accGlow:var(--amberGlow)}.cover{--acc:var(--ice);--accGlow:var(--iceGlow)}.mixed{--acc:var(--mint);--accGlow:rgba(92,224,180,.4)}
.rows{--acc:var(--amber);--accGlow:var(--amberGlow)}.rows .rw+.rw{margin-top:12px}.rows .caps.sub{display:block;margin:0 0 8px 2px;font-size:10px}.rows .hd{margin-bottom:8px}`;
    }
    render() {
      const c = this._config;
      // The room's top card: a labelled row per kind («Свет», «Шторы») — never one mixed row (owner 28.10); a kind is here
      // when scenes go on top (the integrator's choice or the client's) or it has no block of its own.
      if (Array.isArray(c.rows)) {
        const rows = c.rows.filter((r) => (r.always || sceneWhere(this, r.kind === "cover" ? "cover" : "light") === "top") && (r.scenes || []).some((x) => st(this._hass, x.entity)));
        this.hideCard(!rows.length);
        if (!rows.length) return "";
        return `<div class="glass m rows"><div class="hd"><span class="ic"><ha-icon icon="hauflo:scene"></ha-icon></span><span class="caps">${blockTitle(this, "Сценарии")}</span></div>
${rows.map((r) => `<div class="rw"><span class="caps sub">${esc(r.title || (r.kind === "cover" ? "Шторы" : "Свет"))}</span>${sceneChips(this._hass, r.scenes, r.kind === "cover" ? "cover" : r.kind === "mixed" ? "mixed" : "light", r.kind === "mixed" ? undefined : rowRoom(this, r.kind), this)}</div>`).join("")}</div>`;
      }
      const list = (c.scenes || []).map((x) => ({ ...x, st: st(this._hass, x.entity) })).filter((x) => x.st);
      const kind = c.kind === "cover" ? "cover" : c.kind === "mixed" ? "mixed" : "light";
      const icon = kind === "cover" ? "hauflo:blind" : "hauflo:scene";
      this.hideCard(false);
      const label = (x) => x.name || nameOf(x.st, x.entity);
      if (!list.length) return `<div class="glass m ${kind}"><span class="caps">Сценариев нет</span></div>`;
      return `<div class="glass m ${kind}"><div class="hd"><span class="ic"><ha-icon icon="${icon}"></ha-icon></span><span class="caps">${blockTitle(this, kind === "cover" ? "Сценарии штор" : kind === "mixed" ? "Сцены" : "Сценарии света")}</span></div>
<div class="row">${list.map((x) => `<button data-scene="${esc(x.entity)}" title="${esc(label(x))}"><ha-icon icon="${esc(x.icon || (kind === "cover" ? "hauflo:blind" : kind === "mixed" ? "hauflo:scene" : "hauflo:scene"))}"></ha-icon><span>${esc(label(x))}</span></button>`).join("")}</div></div>`;
    }
    bind() {
      bindScenes(this);
    }
    getGridOptions() {
      return { columns: 12, rows: "auto" };
    }
  }

  /** Two setpoints of one thermostat (owner 28.10: «где есть уставка одна на тепло, вторая на холод — показывать, что
   * регулируется, и выставлять на шкале две уставки»): a KNX climate holds one, the other is a number linked to it
   * (`cool_setpoint` / `heat_setpoint`); any HA climate kept between two values (heat_cool: target_temp_low/high). */
  const setpointPair = (card, s) => {
    const c = card._config;
    const a = s?.attributes || {};
    const otherId = c.cool_setpoint || c.heat_setpoint;
    if (otherId) {
      const other = num(st(card._hass, otherId)?.state);
      const own = num(a.temperature);
      const heat = c.cool_setpoint ? own : other;
      const cool = c.cool_setpoint ? other : own;
      if (heat === undefined && cool === undefined) return null;
      return { kind: "pair", heat, cool, heatId: c.heat_setpoint || null, coolId: c.cool_setpoint || null };
    }
    const lo = num(a.target_temp_low);
    const hi = num(a.target_temp_high);
    if (lo !== undefined && hi !== undefined && num(a.temperature) === undefined) return { kind: "range", heat: lo, cool: hi };
    return null;
  };
  /** What the thermostat does now by its mode: heating or cooling (none known — null). */
  const activeMode = (s) => {
    const a = s?.attributes || {};
    return a.hvac_action === "cooling" || s?.state === "cool" ? "cool" : a.hvac_action === "heating" || s?.state === "heat" ? "heat" : null;
  };

  /** A compact thermostat line opens its large card (chart, scale, modes) in a sheet (owner 28.10: «по нажатию, если что,
   * открывать большой»). */
  const SHEET_CSS = `:host{position:fixed;inset:0;z-index:9998;display:grid;place-items:end center;background:rgba(0,0,0,.42);-webkit-tap-highlight-color:transparent}
@media (min-width:600px){:host{place-items:center}}
.sh{width:min(100%,520px);max-height:94vh;overflow:auto;padding:12px 12px calc(12px + env(safe-area-inset-bottom));display:flex;flex-direction:column;gap:8px;background:var(--lovelace-background,var(--primary-background-color,#f4f4f4));border-radius:28px 28px 0 0;box-shadow:0 20px 60px rgba(0,0,0,.35)}
@media (min-width:600px){.sh{border-radius:28px}}
.x{align-self:flex-end;width:44px;height:44px;border-radius:22px;border:0;background:var(--eh-track,rgba(127,127,127,.12));color:var(--primary-text-color);font-size:22px;cursor:pointer;display:grid;place-items:center}.x ha-icon{--mdc-icon-size:22px}
.go{display:flex;align-items:center;justify-content:center;gap:6px;min-height:48px;margin:2px 0 0;padding:0 16px;border:0;border-radius:16px;background:var(--eh-track,rgba(127,127,127,.12));color:var(--primary-text-color);font:500 15px Onest,sans-serif;cursor:pointer}.go ha-icon{--mdc-icon-size:20px;opacity:.7}`;
  /** A thermostat's control sheet over the page: its large card (the room's own config when given) — and, from a list of
   * the house (`room`), «Открыть в комнате» to its card on the room page. */
  const openClimateSheet = (card, cfg = card._config, room = "") => {
    if (card._pop) return;
    const host = document.createElement("div");
    host.className = "hauflo-climate-sheet";
    const sr = host.attachShadow({ mode: "open" });
    sr.innerHTML = `<style>${TZ(SHEET_CSS)}</style><div class="sh" role="dialog" aria-modal="true" aria-label="${esc(cfg.label || "Климат")}"><button class="x" aria-label="Закрыть"><ha-icon icon="mdi:close"></ha-icon></button></div>`;
    const el = document.createElement("hauflo-climate");
    const { compact: _c, size_class: _s, hauflo_id: _h, grid_options: _g, visibility: _v, ...rest } = cfg;
    el.setConfig({ ...rest, popup: true });
    sr.querySelector(".sh").appendChild(el);
    if (room) {
      const go = document.createElement("button");
      go.className = "go";
      go.dataset.room = room;
      go.innerHTML = `<span>Открыть в комнате</span><ha-icon icon="mdi:chevron-right"></ha-icon>`;
      go.addEventListener("click", () => {
        close();
        navigate(room);
        focusOn(cfg.entity, room);
      });
      sr.querySelector(".sh").appendChild(go);
    }
    const close = () => {
      host.remove();
      card._pop = null;
      document.removeEventListener("keydown", onKey);
      window.removeEventListener("location-changed", onNav);
      window.removeEventListener("popstate", onNav);
    };
    const onKey = (e) => e.key === "Escape" && close();
    // The page changed under it (the phone's back, a tab): the sheet goes with the page it was opened on.
    const onNav = () => setTimeout(() => card._pop === el && close(), 0);
    document.addEventListener("keydown", onKey);
    window.addEventListener("location-changed", onNav);
    window.addEventListener("popstate", onNav);
    sr.querySelector(".x").addEventListener("click", close);
    host.addEventListener("click", (e) => e.composedPath()[0] === host && close());
    if (LANG !== "ru") translate(sr);
    document.body.appendChild(host);
    el.hass = card._hass;
    card._pop = el;
  };

  // --- hauflo-climate: the setpoint as the one big number ----------------------------------------
  class Climate extends EHCard {
    watched() {
      return [this._config.entity, this._config.cool_setpoint, this._config.heat_setpoint, ...(this._config.extra || []), ...readingsOf(this._config).map((r) => r.entity), ...(this._config.zones || []).map((z) => z.entity), ...actIds(this._config.activity), ...(this._config.zones || []).flatMap((z) => actIds(z.activity))].filter(Boolean);
    }
    set hass(hass) {
      this._hass = hass;
      if (this._pop) this._pop.hass = hass;
      this._loadHist();
      this._update();
    }
    /** A line or the large card: the client's own choice for this kind (ACs and thermostats / floor heating), else the
     * integrator's. The large card in the sheet is always large. */
    isCompact() {
      const c = this._config;
      if (c.popup) return false;
      const pref = c.size_class ? viewPrefs(this)[c.size_class === "floor" ? "floor" : "climate"] : undefined;
      return pref ? pref === "compact" : !!c.compact;
    }
    /** A line's number: the setpoint, big (what ± changes); «Сейчас 24,0°» small under it (owner 29.10). */
    cct(cur, target, step, eid) {
      const d = (v) => fmt(v, step < 1 && v % 1 ? 1 : 0);
      if (target === undefined) return `<div class="cct thin">${cur === undefined ? "—" : `${fmt(cur, 1)}<small>°</small>`}<span class="tgc"></span></div>`;
      return `<div class="cct thin">${d(target)}<small>°</small><span class="tgc">${cur === undefined ? "" : `Сейчас ${fmt(cur, 1)}°`}</span></div>`;
    }
    /** A setpoint changed by ± / the scale: shown at once, sent once ~0.6 s after the last touch (tester 28.10: 5 quick taps
     * were 5 bus commands, all from the same stale value). The pending value is what the next tap adds to. */
    setSetpoint(kind, v, eid) {
      const key = `${eid || this._config.entity}|${kind || ""}`;
      this._spq = this._spq || {};
      this._spT = this._spT || {};
      this._spq[key] = { v, at: Date.now() };
      clearTimeout(this._spT[key]);
      this._spT[key] = setTimeout(() => {
        delete this._spT[key];
        Promise.resolve(this.sendSetpoint(kind, v, eid)).catch(() => {});
      }, 600);
    }
    /** The value being set (not sent / not heard back yet, ≤10 s), else what the thermostat says. */
    pendSp(eid, kind, fallback) {
      const q = this._spq?.[`${eid || this._config.entity}|${kind || ""}`];
      return q && Date.now() - q.at < 10000 ? q.v : fallback;
    }
    /** The setpoint ± and the scale move: the one chosen of two (heating / cooling), else the climate's own. */
    sendSetpoint(kind, v, eid) {
      const id = eid || this._config.entity;
      const p = eid ? null : setpointPair(this, st(this._hass, id));
      if (!p) return call(this._hass, "climate", "set_temperature", { entity_id: id, temperature: v });
      if (p.kind === "range") return call(this._hass, "climate", "set_temperature", { entity_id: id, target_temp_low: kind === "heat" ? v : p.heat, target_temp_high: kind === "cool" ? v : p.cool });
      const other = kind === "cool" ? p.coolId : p.heatId;
      if (other) return call(this._hass, "number", "set_value", { entity_id: other, value: v });
      return call(this._hass, "climate", "set_temperature", { entity_id: id, temperature: v });
    }
    /** Which of two setpoints the ± and the scale move: the one tapped, else the one of the mode now, else heating. */
    selOf(pair, s) {
      if (!pair) return null;
      const act = activeMode(s);
      return this._sel && pair[this._sel] !== undefined ? this._sel : act && pair[act] !== undefined ? act : pair.heat !== undefined ? "heat" : "cool";
    }
    /** The room's temperature over a day with the setpoint (owner 2026-10-25: «чтобы клиент видел, что за 24 часа
     * температура в норме»; 27.10: a chart beside every thermostat's number, the floor heating's too). From `history`
     * (a sensor) or the thermostat's own reading; on a card of zones — the zone tapped. */
    chartOf() {
      const c = this._config;
      return c.zones?.length ? (c.zones[this._zi || 0] || c.zones[0]).entity : c.entity;
    }
    async _loadHist() {
      const c = this._config;
      const id = c && this.chartOf();
      if (!id || c.chart === false || (this._histId === id && this._histAt > Date.now() - 5 * 60000)) return;
      this._histAt = Date.now();
      this._histId = id;
      let data;
      try {
        data = await climateSeries(this._hass, id, 24, c.zones?.length ? undefined : c.history);
      } catch (e) {
        data = null;
      }
      if (this._histId !== id) return;
      this._hist = data;
      this._sig = "";
      this._update();
    }
    /** The chart of a thermostat: its live reading and setpoint end the lines. */
    chart(id, label, tone, H) {
      if (this._config.chart === false) return "";
      const s = st(this._hass, id);
      const a = s?.attributes || {};
      const cur = this._config.history && !this._config.zones?.length ? num(st(this._hass, this._config.history)?.state) : num(a.current_temperature);
      const frost = frostOf(s);
      const data = this._histId === id ? (frost && this._hist ? { ...this._hist, sp: [] } : this._hist) : undefined;
      return tchart(data, { cur, sp: frost ? undefined : num(a.temperature) }, { H, label, tone });
    }
    css() {
      return `${TCHART_CSS}${VALVE_CSS}${TONE_CSS}.sub .act{margin-right:6px}.c{padding:20px 22px}.nr{display:flex;align-items:center;gap:18px;flex-wrap:wrap}.st{flex:none}.act.k-idle i{background:var(--mint);box-shadow:0 0 8px var(--mint)}
.t{font-size:76px;line-height:.9}.t sup{font:300 20px Onest,sans-serif;vertical-align:top;position:relative;top:8px;color:var(--ink2);margin-left:2px}
.sub{font:400 13px Onest,sans-serif;color:var(--ink2);margin-top:8px}.st{display:flex;flex-direction:column;gap:8px}
.none{font:300 20px/1.2 Onest,sans-serif;color:var(--ink2);margin:6px 0}
.st button{width:48px;height:48px;border-radius:24px;display:grid;place-items:center;box-shadow:var(--eh-edge,inset 0 0 0 1px var(--line));background:var(--eh-glass,transparent);font:300 26px Onest,sans-serif}
.st:not(.row2){gap:12px}.st:not(.row2) button{width:60px;height:60px;border-radius:30px;font:300 34px/1 Onest,sans-serif}
.toned .st button{background:color-mix(in srgb,var(--ac) 16%,transparent);box-shadow:inset 0 0 0 1.5px color-mix(in srgb,var(--ac) 55%,transparent);color:var(--acT)}.toned.calm .st button{background:color-mix(in srgb,var(--ac) 10%,transparent);box-shadow:inset 0 0 0 1.5px color-mix(in srgb,var(--ac) 38%,transparent)}
.mh{font:500 11px Onest,sans-serif;letter-spacing:.08em;text-transform:uppercase;color:var(--ink2);margin-top:16px}.mh+.modes{margin-top:6px}
.st button:active{transform:scale(.94)}.st button:disabled{opacity:.35;pointer-events:none}
.top{display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;min-height:22px}
.nos{font:400 13px Onest,sans-serif;color:var(--ink2);margin-top:4px}.goal{font:500 11px Onest,sans-serif;letter-spacing:.12em;text-transform:uppercase;color:var(--ink3);margin-left:8px;vertical-align:top;position:relative;top:14px}
.day{font:400 13px Onest,sans-serif;color:var(--ink2);margin-top:3px}
.act{display:inline-flex;align-items:center;gap:7px;font:500 12px Onest,sans-serif;color:var(--ink2);padding:4px 10px;border-radius:11px;background:var(--track)}
.act i{width:6px;height:6px;border-radius:3px;background:var(--ink3)}.act.bad{color:var(--alertT)}.act.k-heat i{background:var(--amber);box-shadow:0 0 8px var(--amber)}.act.k-cool i{background:var(--ice);box-shadow:0 0 8px var(--ice)}.act.k-dry i,.act.k-fan i{background:var(--ac,var(--ink3))}.act.bad i{background:var(--alert);box-shadow:0 0 8px var(--alert)}.act.frost i{background:var(--ice);box-shadow:none}
.scale{margin-top:18px;touch-action:pan-y;cursor:ew-resize;user-select:none;-webkit-user-select:none;padding:6px 0}
.ends{display:flex;justify-content:space-between;font:400 11px Onest,sans-serif;color:var(--ink3);margin-top:6px}
.ticks{display:flex;justify-content:space-between;align-items:flex-end;height:34px}
.ticks i{width:2px;border-radius:1px;background:var(--ink3);height:10px;position:relative}.ticks i.m{height:16px}.ticks i.a{height:34px;width:3px;background:var(--ac,var(--ink2));box-shadow:0 0 10px var(--acG,transparent)}.calm .ticks i.a{box-shadow:0 0 6px var(--acG,transparent)}
.ticks i.cur::after{content:"";position:absolute;left:50%;bottom:-9px;width:5px;height:5px;border-radius:3px;transform:translateX(-50%);background:var(--ink2)}
.dragging .t .v{color:var(--acT,var(--ink))}
.ticks i.ph,.ticks i.pc{height:24px;width:3px;opacity:.55}.ticks i.ph{background:var(--amber)}.ticks i.pc{background:var(--ice)}
.ticks i.ph.sel,.ticks i.pc.sel{height:34px;opacity:1}.ticks i.ph.sel{box-shadow:0 0 10px var(--amberGlow)}.ticks i.pc.sel{box-shadow:0 0 10px var(--iceGlow)}
.prs{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px}.pr{display:inline-flex;align-items:center;gap:7px;min-height:36px;padding:0 12px;border-radius:12px;font:500 13px Onest,sans-serif;color:var(--ink2);box-shadow:inset 0 0 0 1px var(--line)}
.pr::before{content:"";width:8px;height:8px;border-radius:4px;opacity:.45}.pr.heat::before{background:var(--amber)}.pr.cool::before{background:var(--ice)}
.pr.on{color:var(--ink);background:var(--track);box-shadow:none}.pr.on::before{opacity:1}.pr.act::after{content:"●";font-size:8px;color:var(--ink3);margin-left:2px}
.ccn[data-open]{cursor:pointer}.ccn[data-open] .caps::after{content:"›";margin-left:6px;font-size:14px;letter-spacing:0;color:var(--ink3)}
.pair2{font:400 12px/1.3 Onest,sans-serif;color:var(--ink3)}
.modes{display:flex;gap:6px;margin-top:16px}.modes.fan{margin-top:8px;align-items:center}.modes.fan button{white-space:nowrap;padding:0 4px;font-size:12px;min-width:0}.modes .fl{flex:0 0 auto;color:var(--ink3);--mdc-icon-size:18px;padding:0 2px}.modes button{flex:1;min-height:44px;padding:0 6px;border-radius:14px;font:500 13px Onest,sans-serif;color:var(--ink2);box-shadow:inset 0 0 0 1px var(--line)}
.modes .on{color:var(--ink);background:var(--track);box-shadow:none}.modes .arm{color:var(--alertT);background:color-mix(in srgb,var(--alert) 14%,transparent);box-shadow:inset 0 0 0 1.5px var(--alert)}
.toned .glow{background:radial-gradient(50% 50% at 20% 10%,var(--acG),transparent 70%);opacity:.6}.toned.calm .glow{opacity:.32}
.cc{padding:14px 14px 14px 18px}.ccrow{display:flex;align-items:center;gap:12px;position:relative}.ccn{flex:1;min-width:0;display:flex;flex-direction:column;gap:4px}
.ccn .caps{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.ccn .sub{margin:0}
.cct{font-size:40px;line-height:1}.cct small{font:300 15px Onest,sans-serif;color:var(--ink2);margin-left:1px}
.cct .tgc{display:block;margin-top:4px;text-align:right;font:400 12px/1.2 Onest,sans-serif;color:var(--ink2);letter-spacing:0}
.sens{display:flex;gap:6px;margin-top:2px;flex-wrap:wrap}.sens b{font:500 12px Onest,sans-serif;padding:3px 7px;border-radius:8px;background:var(--track);color:var(--ink2);white-space:nowrap}.sens i{font-style:normal;color:var(--ink3);margin-right:5px}.c .sens{margin-top:8px}
.st.row2{flex-direction:row}.st.row2 button{width:44px;height:44px;border-radius:22px;font-size:22px}
.zs .caps{display:block;margin-bottom:4px}.zr{display:flex;align-items:center;gap:12px;padding:8px 0;border-top:1px solid var(--hair)}.zr:nth-child(2){border-top:0}
.zn{font:500 15px/1.25 Onest,sans-serif;color:var(--ink);white-space:normal;overflow-wrap:break-word;hyphens:auto}.zs .zr .ccn{cursor:pointer}.zs .zr:not(.sel) .zn{color:var(--ink2)}.zs .cct{font-size:32px}.zr.heat .zn::after{content:"";display:inline-block;width:6px;height:6px;border-radius:3px;background:var(--amber);margin-left:7px;vertical-align:middle}`;
    }
    render() {
      const s = st(this._hass, this._config.entity);
      if (!s) return `<div class="glass c"><div class="caps">Нет термостата</div></div>`;
      // No link and nothing known: one honest line, not an empty dial (persona round 3).
      const compact = !this._config.zones?.length && this.isCompact();
      // Nothing heard at all (no setpoint, no reading): one compact line too — no giant «—», no ±, no empty chart
      // (final review 28.10, «Котельная»).
      const silent = num(s.attributes.temperature) === undefined && num(s.attributes.target_temp_low) === undefined && num(s.attributes.target_temp_high) === undefined && num(s.attributes.current_temperature) === undefined && (!this._config.history || num(st(this._hass, this._config.history)?.state) === undefined) && !setpointPair(this, s);
      if (!this._config.zones?.length && !compact && silent)
        return `<div class="glass cc"><div class="ccrow"><div class="ccn"><span class="caps">${esc(this._config.label || "Климат")}</span><span class="sub">${["unavailable", "unknown"].includes(s.state) ? "нет связи с термостатом" : "нет данных от системы"}</span></div></div></div>`;
      if (this._config.zones?.length) return this.zones();
      if (compact) return this.compact(s);
      const a = s.attributes;
      // Two setpoints (heating / cooling, or HA's heat_cool range): the big number is the one the ± and the scale move —
      // the mode's own unless the client tapped the other; both are on the scale.
      const pair = setpointPair(this, s);
      const sel = this.selOf(pair, s);
      const act = pair ? activeMode(s) : null;
      const target = this.pendSp(null, sel, pair ? pair[sel] : num(a.temperature));
      const lo = undefined;
      const hi = undefined;
      const range = false;
      const cur = num(a.current_temperature);
      const hum = num(a.current_humidity) ?? num(st(this._hass, this._config.humidity)?.state);
      const step = Math.max(num(a.target_temp_step) || 0.5, 0.5);
      const min = num(a.min_temp) ?? 5;
      const max = num(a.max_temp) ?? 35;
      const dgf = (v) => (step < 1 && v !== undefined && v % 1 ? 1 : 0);
      const known = target !== undefined || cur !== undefined;
      // What it DOES now (owner 28.10, Д13/Д16): the glow and the chip follow the action, never the mode. «Греет» when it is
      // warmer than the goal isn't believable (persona review 25.10) — hvac_action is trusted only when it fits (climateNow);
      // a valve / heat command of the zone that is on is a fact.
      const now = !known ? null : climateNow(this._hass, s, this._config.activity);
      // The colour is the MODE's (owner 29.10, Apple Home / Nest way): amber in «Нагрев» even at target, a little softer
      // then; grey only off / no link. The chip alone says what it does now.
      const tone = known ? climateTone(s, now) : "";
      const calm = toneCalm(tone, now);
      const cls = tone ? `m-${tone} toned${calm ? " calm" : ""}` : "";
      const goalOf = now === "heat" && pair?.heat !== undefined ? pair.heat : now === "cool" && pair?.cool !== undefined ? pair.cool : target;
      const goalShown = goalOf !== undefined && !(now === "heat" && cur >= goalOf) ? goalOf : undefined;
      const frost = frostOf(s);
      const fault = faultOf(this, this._config.entity, !!this._config.floor);
      const doing = fault ? "Авария" : !known ? "" : frost ? "Защита от замерзания" : climateLine(now, goalShown);
      const modeTxt = known && now ? modeDiff(s.state, now) : "";
      const n = 31;
      const at = (v) => Math.round(((v - min) / (max - min || 1)) * (n - 1));
      const pos = target === undefined ? -1 : at(target);
      const cpos = cur === undefined ? -1 : at(Math.min(max, Math.max(min, cur)));
      const hp = pair && pair.heat !== undefined ? at(Math.min(max, Math.max(min, pair.heat))) : -1;
      const kp = pair && pair.cool !== undefined ? at(Math.min(max, Math.max(min, pair.cool))) : -1;
      const ticks = Array.from({ length: n }, (_, i) => `<i class="${!pair && i === pos ? "a" : i % 5 === 0 ? "m" : ""}${i === hp ? ` ph${sel === "heat" ? " sel" : ""}` : ""}${i === kp ? ` pc${sel === "cool" ? " sel" : ""}` : ""}${i === cpos ? " cur" : ""}"></i>`).join("");
      const dg = (v) => (step < 1 && v !== undefined && v % 1 ? 1 : 0);
      const pairRow = pair
        ? `<div class="prs" role="group">${["heat", "cool"]
            .filter((k) => pair[k] !== undefined)
            .map((k) => `<button data-pk="${k}" class="pr ${k}${sel === k ? " on" : ""}${act === k ? " act" : ""}" aria-pressed="${sel === k}">${k === "heat" ? "обогрев до" : "охлаждение от"} ${fmt(pair[k], dg(pair[k]))}°</button>`)
            .join("")}</div>`
        : "";
      const modes = (a.hvac_modes || []).filter((m) => HVAC[m]);
      const presets = (a.preset_modes || []).filter((p) => p !== "none");
      const useModes = modes.length > 1;
      // An AC's fan speed and swing (persona round 3, Dubai: «no fan speed or swing»): shown when the device has them.
      const fans = (a.fan_modes || []).filter((f) => f !== "off");
      const swing = (a.swing_modes || []).includes("off") ? (a.swing_modes.find((m) => m !== "off") ?? null) : null;
      // A mode lit only when the thermostat has said something: HA's default «heat» isn't a fact (integrator run 25.10).
      // «Ожидание» isn't lit over a floor that heats (family review round 3): KNX's operation mode without a state address
      // reads «standby» by default — the action is the fact, the guessed preset isn't.
      const opts = useModes ? modes.map((m) => [m, HVAC[m], known && s.state === m]) : presets.map((p) => [p, PRESET[p] || p, known && a.preset_mode === p && !(p === "standby" && now === "heat")]);
      const digits = step < 1 && target !== undefined && target % 1 ? 1 : 0;
      const readings = this.readings();
      // «Комфорт» of the agent (comfort.py): the house's season beside the label, and in winter the floor's setpoint
      // the agent keeps now — the client sees why the room warms (owner 25.10).
      const season = a.house_mode === "summer" ? "Лето" : a.house_mode === "winter" ? "Зима" : "";
      const fl = season === "Зима" && Array.isArray(a.floor_targets) ? a.floor_targets.map(num).filter((v) => v !== undefined) : [];
      const floorNow = fl.length ? ` · пол ${Math.min(...fl) === Math.max(...fl) ? fmt(fl[0]) : `${fmt(Math.min(...fl))}–${fmt(Math.max(...fl))}`}°` : "";
      const chart = this.chart(this._config.entity, this._config.floor ? "Пол" : "В комнате", tone ? `${tone}${calm ? " calm" : ""}` : "", 96);
      // A thermostat of another integration may take no setpoint at all (features: TARGET_TEMPERATURE 1, RANGE 2): no hint, no scale.
      const fixed = target === undefined && !pair && num(a.supported_features) !== undefined && (num(a.supported_features) & 3) === 0 && !["unavailable", "unknown"].includes(s.state);
      const noTarget = target === undefined && !fixed ? `<div class="nos">${["unavailable", "unknown"].includes(s.state) ? "нет связи с термостатом" : "нужная температура ещё не пришла от системы"}</div>` : "";
      // The BIG number is the setpoint the +/- change (Nest / Ecobee way; owner 29.10) — never swapped; «Сейчас 25,0°» is small.
      const bigTgt = target === undefined || frost ? `<div class="t thin tbig"><span class="v">${cur === undefined ? "—" : fmt(cur, 1)}</span>${cur === undefined ? "" : `<sup>°C</sup>${frost ? `<span class="goal">сейчас</span>` : ""}`}</div>` : `<div class="t thin tbig"><span class="v">${fmt(target, digits)}</span><sup>°C</sup>${pair ? `<span class="goal">${sel === "heat" ? "обогрев до" : "охлаждение от"}</span>` : ""}</div>`;
      const small = [cur === undefined ? (this._config.floor ? "нет данных" : "нет данных из комнаты") : target !== undefined && !frost ? `Сейчас ${fmt(cur, 1)}°` : "", modeTxt].filter(Boolean).join(" · ");
      const stp = fmt(step, step % 1 ? 1 : 0);
      // Just «+» and «−» (owner 29.10: «очевидно, что + теплее − прохладнее»); the words are for screen readers.
      const stBtns = target === undefined || frost ? "" : `<div class="st"><button data-d="${step}" aria-label="Теплее на ${stp}°">+</button><button data-d="${-step}" aria-label="Холоднее на ${stp}°">−</button></div>`;
      return `<div class="glass c ${cls}"><div class="glow"></div><div style="position:relative">
<div class="top"><span class="caps">${esc(this._config.label || "Климат")}${season ? ` · ${season}` : ""}</span></div>
<div class="nr">${bigTgt}${stBtns}</div>${noTarget}
<div class="sub">${small ? `${esc(small)} ` : ""}${doing ? `<span class="act${fault ? " bad" : frost ? " frost" : now ? ` k-${now}` : ""}"><i></i>${doing}</span>` : ""}${valveIcons(this._hass, this._config.activity?.valves)}${floorNow}${hum !== undefined ? ` · влажность ${fmt(hum)} %` : ""}</div>${readings.length ? this.readingsRow(readings) : ""}${pairRow}
${chart}
${fixed || frost ? "" : `<div class="scale" data-min="${min}" data-max="${max}" data-step="${step}"${pair ? ` data-pair="1"` : ""}><div class="ticks">${ticks}</div><div class="ends"><span>${fmt(min)}°</span><span>проведите, чтобы изменить</span><span>${fmt(max)}°</span></div></div>`}
${opts.length ? `<div class="mh">Режим</div><div class="modes">${opts.slice(0, 5).map(([k, l, on]) => `<button class="${!on ? "" : useModes ? (climateTone(null, now, k) ? `on mk m-${climateTone(null, now, k)}` : "on") : tone ? "on mk" : "on"}" data-${useModes ? "m" : "p"}="${esc(k)}" aria-pressed="${!!on}">${esc(l)}</button>`).join("")}</div>` : ""}
${fans.length > 1 || swing ? `<div class="mh">Вентилятор</div><div class="modes fan"><span class="fl"><ha-icon icon="hauflo:fan"></ha-icon></span>${fans.slice(0, 5).map((f) => `<button class="${known && a.fan_mode === f ? (tone ? "on mk" : "on") : ""}" data-f="${esc(f)}" aria-pressed="${known && a.fan_mode === f}">${esc(FAN[f] || f)}</button>`).join("")}${swing ? `<button class="${known && a.swing_mode && a.swing_mode !== "off" ? (tone ? "on mk" : "on") : ""}" data-sw="${esc(a.swing_mode && a.swing_mode !== "off" ? "off" : swing)}">Обдув</button>` : ""}</div>` : ""}</div></div>`;
    }
    /** Floor-heating zones of one room in one card: a line each — name, now, setpoint, ± (persona review 25.10). */
    zones() {
      const zi = Math.min(this._zi || 0, this._config.zones.length - 1);
      const rows = this._config.zones.map((z, i) => {
        const s = st(this._hass, z.entity);
        const a = s?.attributes || {};
        const target = this.pendSp(z.entity, null, num(a.temperature));
        const cur = num(a.current_temperature);
        const step = Math.max(num(a.target_temp_step) || 0.5, 0.5);
        const nowZ = climateNow(this._hass, s, z.activity);
        const heating = nowZ === "heat";
        const zt = cur === undefined && target === undefined ? "" : climateTone(s, nowZ);
        const zWord = s && faultOf(this, z.entity, true) ? "Авария" : frostOf(s) ? "Защита от замерзания" : "";
        return `<div class="zr ${heating ? "heat" : ""}${i === zi ? " sel" : ""}${zt ? ` m-${zt} toned${toneCalm(zt, nowZ) ? " calm" : ""}` : ""}"><div class="ccn" data-z="${i}"><span class="zn">${esc(z.name || nameOf(s, z.entity))}</span><span class="sub">${!s || ["unavailable", "unknown"].includes(s.state) ? "нет связи" : cur === undefined ? "нет данных" : zWord || climateLine(nowZ, target) || "пол сейчас"}${s &&!["unavailable", "unknown"].includes(s.state) && target === undefined ? " · нужная температура ещё не пришла" : ""}</span>${valveIcons(this._hass, z.activity?.valves)}</div>
${this.cct(cur, target, step, z.entity)}
<div class="st row2"><button data-e="${esc(z.entity)}" data-d="${-step}" aria-label="Холоднее на ${fmt(step, step % 1 ? 1 : 0)}°"${target === undefined ? " disabled" : ""}>−</button><button data-e="${esc(z.entity)}" data-d="${step}" aria-label="Теплее на ${fmt(step, step % 1 ? 1 : 0)}°"${target === undefined ? " disabled" : ""}>+</button></div></div>`;
      });
      // The card's glow: the zones' mode (heat — amber), full while one heats, softer while all are at target.
      const nows = this._config.zones.map((z) => climateNow(this._hass, st(this._hass, z.entity), z.activity));
      const any = nows.includes("heat");
      const cardTone = any ? "heat" : this._config.zones.map((z, i) => climateTone(st(this._hass, z.entity), nows[i])).find(Boolean) || "";
      // One chart for the zone tapped (the first at the start): a chart per line would be a wall of lines.
      const z = this._config.zones[zi];
      const zs = st(this._hass, z.entity);
      const zt = climateTone(zs, nows[zi]);
      const chart = this.chart(z.entity, `Пол · ${z.name || nameOf(zs, z.entity)}`, zt ? `${zt}${toneCalm(zt, nows[zi]) ? " calm" : ""}` : "", 64);
      return `<div class="glass cc zs${cardTone ? ` m-${cardTone} toned${toneCalm(cardTone, any ? "heat" : "idle") ? " calm" : ""}` : ""}"><div class="glow"></div><div style="position:relative"><div class="caps">${esc(this._config.label || "Тёплый пол")}</div>${rows.join("")}${chart}</div></div>`;
    }
    /** The other readings the card shows (a floor's sensors), each with its value. */
    readings() {
      return readingsOf(this._config)
        .map((r) => ({ ...r, v: tempOf(st(this._hass, r.entity)) }))
        .filter((r) => r.v !== undefined);
    }
    readingsRow(list) {
      return `<span class="sens">${list.map((r) => `<b>${r.name ? `<i>${esc(r.name)}</i>` : ""}${fmt(r.v, 1)}°</b>`).join("")}</span>`;
    }
    /** One line for a second thermostat (zones of floor heating): name, setpoint, ±. */
    compact(s) {
      const a = s.attributes;
      const pair = setpointPair(this, s);
      const sel = this.selOf(pair, s);
      const target = this.pendSp(null, sel, pair ? pair[sel] : num(a.temperature));
      const cur = num(a.current_temperature);
      const step = Math.max(num(a.target_temp_step) || 0.5, 0.5);
      const nowC = climateNow(this._hass, s, this._config.activity);
      const heating = nowC === "heat";
      const ct = cur === undefined && target === undefined ? "" : climateTone(s, nowC);
      const ctc = ct ? `${ct}${toneCalm(ct, nowC) ? " calm" : ""}` : "";
      const cWord = faultOf(this, this._config.entity, !!this._config.floor) ? "Авария" : frostOf(s) ? "Защита от замерзания" : "";
      // A floor with several sensors: each one's reading, by its name (owner 2026-10-25, 27.10).
      const sensors = this.readings();
      // The line is always there (no data says so): a card that grows and shrinks with the state moves the page.
      return `<div class="glass cc${ct ? ` m-${ct} toned${toneCalm(ct, nowC) ? " calm" : ""}` : ""}"><div class="glow"></div><div class="ccrow">
<div class="ccn" data-open="1" role="button" tabindex="0"><span class="caps">${esc(this._config.label || nameOf(s, this._config.entity))}</span><span class="sub">${cur === undefined ? "нет данных" : cWord || climateLine(nowC, target) || (this._config.floor ? "пол сейчас" : "сейчас")}${target === undefined && !["unavailable", "unknown"].includes(s.state) ? " · нужная температура ещё не пришла" : ""}</span>${valveIcons(this._hass, this._config.activity?.valves)}${pair ? `<span class="pair2">${["heat", "cool"].filter((k) => pair[k] !== undefined).map((k) => `${k === "heat" ? "обогрев до" : "охлаждение от"} ${fmt(pair[k])}°`).join(" · ")}</span>` : ""}${sensors.length > 1 ? this.readingsRow(sensors) : ""}</div>
${this.cct(cur, target, step, this._config.entity)}
<div class="st row2"><button data-d="${-step}" aria-label="Холоднее на ${fmt(step, step % 1 ? 1 : 0)}°"${target === undefined ? " disabled" : ""}>−</button><button data-d="${step}" aria-label="Теплее на ${fmt(step, step % 1 ? 1 : 0)}°"${target === undefined ? " disabled" : ""}>+</button></div></div>${this.chart(this._config.entity, this._config.floor ? "Пол" : "Температура", ctc, 56)}</div>`;
    }
    bind() {
      const id = this._config.entity;
      this.shadowRoot.querySelectorAll("button[data-d]").forEach((b) =>
        b.addEventListener("click", () => {
          const eid = b.dataset.e || id;
          const s = st(this._hass, eid);
          if (!s) return;
          const a = s.attributes;
          const pair = b.dataset.e ? null : setpointPair(this, s);
          const sel = this.selOf(pair, s);
          // No setpoint heard yet: nothing is sent (the buttons are off, the card says why — review round 2).
          const base = this.pendSp(b.dataset.e, b.dataset.e ? null : sel, pair ? pair[sel] : num(a.temperature));
          if (base === undefined) return;
          const v = Math.min(num(a.max_temp) ?? 35, Math.max(num(a.min_temp) ?? 5, base + Number(b.dataset.d)));
          haptic(this);
          this.setSetpoint(sel, v, b.dataset.e);
          this._sig = "";
          this._update();
        }),
      );
      // A swipe along the scale sets the temperature (owner 2026-10-25: tapping ± only is tiresome).
      const sc = this.shadowRoot.querySelector(".scale");
      if (sc) {
        const box = this.shadowRoot.querySelector(".c");
        const v = this.shadowRoot.querySelector(".tbig .v");
        const mn = Number(sc.dataset.min);
        const mx = Number(sc.dataset.max);
        const stp = Number(sc.dataset.step);
        let x0 = null;
        let val;
        const valAt = (ev) => {
          const r = sc.getBoundingClientRect();
          const raw = mn + Math.min(1, Math.max(0, (ev.clientX - r.left) / r.width)) * (mx - mn);
          return Math.round(raw / stp) * stp;
        };
        // Only a sideways swipe sets it: a page scroll started on the scale scrolls (touch-action pan-y), a tap
        // sets nothing (integrator run 25.10: scrolling a phone jumped the setpoint).
        let y0 = 0;
        let sliding = false;
        const pairMode = sc.dataset.pair === "1";
        sc.addEventListener("pointerdown", (ev) => {
          x0 = ev.clientX;
          y0 = ev.clientY;
          val = undefined;
          sliding = false;
          // Two setpoints: the swipe takes the one nearer to the finger.
          if (pairMode) {
            const p = setpointPair(this, st(this._hass, id));
            const at0 = valAt(ev);
            if (p && p.heat !== undefined && p.cool !== undefined) this._sel = Math.abs(at0 - p.heat) <= Math.abs(at0 - p.cool) ? "heat" : "cool";
          }
        });
        sc.addEventListener("pointermove", (ev) => {
          if (x0 === null) return;
          if (!sliding) {
            const dx = Math.abs(ev.clientX - x0);
            if (dx <= 10 || dx < Math.abs(ev.clientY - y0) * 1.5) return;
            sliding = true;
            this._busy = true;
            sc.setPointerCapture(ev.pointerId);
          }
          box.classList.add("dragging", "edit");
          const nv = valAt(ev);
          if (nv !== val) {
            val = nv;
            haptic(this, "selection");
            if (v) v.textContent = fmt(val, stp < 1 && val % 1 ? 1 : 0);
            const ticks = [...sc.querySelectorAll(".ticks i")];
            const k = Math.round(((val - mn) / (mx - mn || 1)) * (ticks.length - 1));
            const mk = pairMode ? (this._sel === "cool" ? "pc" : "ph") : "a";
            ticks.forEach((t, i) => {
              t.classList.toggle(mk, i === k);
              if (pairMode) t.classList.toggle("sel", i === k);
            });
          }
        });
        const end = (ev) => {
          if (x0 === null) return;
          x0 = null;
          box.classList.remove("dragging");
          if (sliding && val !== undefined && ev.type === "pointerup") this.setSetpoint(pairMode ? this._sel || "heat" : null, val);
          if (!sliding) return;
          setTimeout(() => {
            this._busy = false;
            this._sig = "";
            this._update();
          }, 500);
        };
        sc.addEventListener("pointerup", end);
        sc.addEventListener("pointercancel", end);
      }
      this.shadowRoot.querySelectorAll("[data-vch]").forEach((b) =>
        b.addEventListener("click", (ev) => {
          ev.stopPropagation();
          const pop = b.nextElementSibling;
          pop.hidden = !pop.hidden;
          b.setAttribute("aria-expanded", String(!pop.hidden));
        }),
      );
      this.shadowRoot.querySelectorAll("button[data-m]").forEach((b) =>
        b.addEventListener("click", () => {
          const prev = st(this._hass, id)?.state;
          call(this._hass, "climate", "set_hvac_mode", { entity_id: id, hvac_mode: b.dataset.m });
          if (b.dataset.m === "off" && prev && !["off", "unavailable", "unknown"].includes(prev)) undoToast("Климат выключен", () => call(this._hass, "climate", "set_hvac_mode", { entity_id: id, hvac_mode: prev }));
        }),
      );
      // «Защита» (frost protection, the room cools to ~7°) asks «Точно?» for everyone (family review round 3: one tap of a
      // child): the first tap arms the button, the second within 4 s sets it.
      this.shadowRoot.querySelectorAll("button[data-p]").forEach((b) =>
        b.addEventListener("click", () => {
          if (b.dataset.p === "building_protection" && st(this._hass, id)?.attributes?.preset_mode !== "building_protection" && b.dataset.arm !== "1") {
            b.dataset.arm = "1";
            b.dataset.lbl = b.textContent;
            b.textContent = tr("Точно? Защита");
            b.classList.add("arm");
            this._busy = true;
            setTimeout(() => {
              if (b.isConnected && b.dataset.arm === "1") {
                b.dataset.arm = "";
                b.textContent = b.dataset.lbl;
                b.classList.remove("arm");
              }
              this._busy = false;
            }, 4000);
            return;
          }
          b.dataset.arm = "";
          this._busy = false;
          haptic(this);
          call(this._hass, "climate", "set_preset_mode", { entity_id: id, preset_mode: b.dataset.p });
        }),
      );
      this.shadowRoot.querySelectorAll("button[data-f]").forEach((b) => b.addEventListener("click", () => call(this._hass, "climate", "set_fan_mode", { entity_id: id, fan_mode: b.dataset.f })));
      this.shadowRoot.querySelectorAll("button[data-sw]").forEach((b) => b.addEventListener("click", () => call(this._hass, "climate", "set_swing_mode", { entity_id: id, swing_mode: b.dataset.sw })));
      if (this.shadowRoot.querySelector(".c")) this.shadowRoot.querySelector(".caps")?.addEventListener("click", () => moreInfo(this, id));
      this.shadowRoot.querySelectorAll("[data-pk]").forEach((b) =>
        b.addEventListener("click", () => {
          this._sel = b.dataset.pk;
          haptic(this, "selection");
          this._sig = "";
          this._update();
        }),
      );
      this.shadowRoot.querySelectorAll("[data-open]").forEach((el) => {
        el.addEventListener("click", () => openClimateSheet(this));
        el.addEventListener("keydown", (e) => (e.key === "Enter" || e.key === " ") && openClimateSheet(this));
      });
      this.shadowRoot.querySelectorAll("[data-z]").forEach((el) =>
        el.addEventListener("click", () => {
          if (Number(el.dataset.z) === (this._zi || 0)) return;
          this._zi = Number(el.dataset.z);
          this._sig = "";
          this._update();
          this._loadHist();
        }),
      );
    }
    getGridOptions() {
      return { columns: 12, rows: "auto" };
    }
  }

  // --- hauflo-sliders: tall sliders for lights, blinds, switches ---------------------------------
  class Sliders extends EHCard {
    watched() {
      return (this._config.entities || []).map((e) => (typeof e === "string" ? e : e.entity));
    }
    items() {
      return (this._config.entities || []).map((e) => (typeof e === "string" ? { entity: e } : e)).filter((e) => st(this._hass, e.entity));
    }
    css() {
      return `.wrap{display:grid;grid-template-columns:repeat(var(--cols),1fr);gap:12px}
.v{height:216px;padding:14px;display:flex;flex-direction:column;justify-content:space-between;touch-action:pan-y;user-select:none;-webkit-user-select:none;-webkit-touch-callout:none;cursor:pointer}
.v.grab{transform:scale(1.03);box-shadow:0 0 0 2px color-mix(in srgb,var(--ink) 60%,transparent),0 14px 40px rgba(0,0,0,.28)}
.v>*{position:relative}.lvl{position:absolute !important;left:0;right:0;bottom:0;transition:height .25s}
.light .lvl{background:linear-gradient(0deg,color-mix(in srgb,var(--amber) 85%,transparent),color-mix(in srgb,var(--amber) 50%,transparent));box-shadow:0 -10px 40px var(--amberGlow)}
.cover .lvl{background:linear-gradient(0deg,color-mix(in srgb,var(--ice) 70%,transparent),color-mix(in srgb,var(--ice) 35%,transparent));box-shadow:0 -10px 40px var(--iceGlow)}
.switch .lvl{background:linear-gradient(0deg,color-mix(in srgb,var(--mint) 70%,transparent),color-mix(in srgb,var(--mint) 35%,transparent))}
.dragging .lvl{transition:none}.p{font-size:30px;line-height:1}.off .p{font:300 20px Onest,sans-serif;color:var(--ink3);letter-spacing:0}
.n{font:500 12px Onest,sans-serif;line-height:1.25;white-space:normal;overflow:hidden;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow-wrap:break-word;hyphens:auto;margin-top:4px}
.ic{color:var(--ink2)}.unavail{opacity:.45}
.sc{padding:2px 16px 16px;margin-bottom:12px;border-radius:24px}.ud3{display:flex;gap:6px;margin-top:10px}.ud3 button{flex:1;height:44px;border-radius:13px;display:grid;place-items:center;color:var(--ink);box-shadow:inset 0 0 0 1px var(--line);background:var(--eh-glass,transparent)}.ud3 button ha-icon{--mdc-icon-size:20px}`;
    }
    model(e) {
      const s = st(this._hass, e.entity);
      const dom = e.entity.split(".")[0];
      const name = e.name || nameOf(s, e.entity);
      if (dom === "light") {
        const dimmable = (s.attributes.supported_color_modes || []).some((m) => m !== "onoff");
        return { dom: "light", name, pct: brightnessPct(s), on: s.state === "on", dimmable, icon: e.icon || "hauflo:bulb", s };
      }
      if (dom === "cover") {
        // A blind with no position address (up/down + stop only): HA's position is its guess from the travel time —
        // no number, no slider, ever (owner, Горького 3, 27.10: «откуда ты их берёшь? Это фейковые данные»).
        const real = coverHasPosition(e, s);
        const pos = real ? num(s.attributes.current_position) : undefined;
        const known = !["unknown", "unavailable"].includes(s.state);
        return { dom: "cover", name, pct: pos ?? (known && s.state !== "closed" ? 100 : 0), on: known && s.state !== "closed", known, dimmable: real, icon: e.icon || "hauflo:blind", s };
      }
      // Any other integration's fan / player: speed and volume are the level where the entity has them (feature bits:
      // fan SET_SPEED 1, media_player VOLUME_SET 4), else a plain tile. A playing player is «on».
      const sf = num(s.attributes.supported_features) || 0;
      if (dom === "fan") {
        const p = num(s.attributes.percentage);
        return { dom: "fan", name, pct: s.state === "on" ? p ?? 100 : 0, on: s.state === "on", dimmable: (sf & 1) !== 0 && p !== undefined, icon: e.icon || "hauflo:fan", s };
      }
      if (dom === "media_player") {
        const v = num(s.attributes.volume_level);
        const on = !["off", "unavailable", "unknown", "standby"].includes(s.state);
        return { dom: "media_player", name, pct: v !== undefined ? Math.round(v * 100) : on ? 100 : 0, on, dimmable: (sf & 4) !== 0 && v !== undefined, icon: e.icon || "hauflo:speaker", s };
      }
      return { dom: "switch", name, pct: s.state === "on" ? 100 : 0, on: s.state === "on", dimmable: false, icon: e.icon || "hauflo:power", s };
    }
    render() {
      const items = this.items().map((e) => ({ e, m: this.model(e) }));
      const cols = Math.min(4, Math.max(1, items.length));
      // Its scenes above the sliders (a light / blinds block of up to three).
      this.kind = items.some((x) => x.m.dom === "cover") ? "cover" : "light";
      const scenes = this.sceneRow();
      return `${scenes ? `<div class="glass sc">${scenes}</div>` : ""}<div class="wrap" style="--cols:${cols}">${items
        .map(({ e, m }) => {
          const unavailable = m.s.state === "unavailable";
          const moving = m.dom === "cover" && ["opening", "closing"].includes(m.s.state);
          const label = m.dom === "cover" ? (!m.known ? "—" : moving ? (m.s.state === "opening" ? "Откр…" : "Закр…") : m.dimmable && num(m.s.attributes.current_position) !== undefined ? `${m.pct}` : m.on ? "Откр." : "Закр.") : m.on ? (m.dimmable ? `${m.pct}` : "Вкл") : "Выкл";
          return `<div class="glass v ${m.dom === "fan" || m.dom === "media_player" ? "switch" : m.dom} ${m.on ? "" : "off"} ${moving ? "mv" : ""} ${unavailable ? "unavail" : ""}" data-e="${esc(e.entity)}" role="slider" aria-label="${esc(m.name)}" aria-valuenow="${m.pct}" tabindex="0">
<div class="lvl" style="height:${m.on || m.dom === "cover" ? m.pct : 0}%"></div><ha-icon class="ic" icon="${esc(m.icon)}"></ha-icon>
<div><div class="p thin">${esc(label)}</div><div class="n">${esc(m.name)}</div>
${m.dom === "cover" && !m.dimmable ? `<div class="ud3">${coverButtons(coverDir({ ...e, s: m.s }, this._config), "data-c")}</div>` : ""}</div></div>`;
        })
        .join("")}</div>`;
    }
    bind() {
      bindScenes(this);
      this.shadowRoot.querySelectorAll(".v").forEach((el) => {
        const id = el.dataset.e;
        let start = null;
        let moved = false;
        let pct = 0;
        const lvl = el.querySelector(".lvl");
        const p = el.querySelector(".p");
        const at = (ev) => {
          const r = el.getBoundingClientRect();
          return Math.round(Math.min(100, Math.max(0, ((r.bottom - ev.clientY) / r.height) * 100)));
        };
        el.querySelectorAll(".ud3 button").forEach((b) =>
          b.addEventListener("click", (ev) => {
            ev.stopPropagation();
            haptic(this);
            sendAck(this, b, "cover", b.dataset.c, { entity_id: id });
          }),
        );
        // A vertical drag on a phone is the page scroll (owner 28.10): touch / pen sets a value only after a press-and-hold
        // (350 ms, finger still, haptic + «grab»), then the drag moves it relative to where the hold began; a tap toggles /
        // starts a blind. A mouse keeps the direct drag. Cancelled / scrolled gestures send nothing.
        let touch = false;
        let grabbed = false;
        let held = false;
        let timer = 0;
        let x0 = 0;
        let pid = 0;
        let base = 0;
        const model = () => this.model(this.items().find((x) => x.entity === id) || { entity: id });
        const settle = () => {
          this._busy = true;
          setTimeout(() => {
            this._busy = false;
            this._sig = "";
            this._update();
          }, 400);
        };
        const show = (v) => {
          pct = v;
          lvl.style.height = `${pct}%`;
          p.textContent = String(pct);
        };
        el.addEventListener("pointerdown", (ev) => {
          if (ev.target.closest("button")) return;
          if (ev.pointerType === "mouse" && ev.button !== 0) return;
          start = ev.clientY;
          x0 = ev.clientX;
          pid = ev.pointerId;
          moved = false;
          grabbed = false;
          held = false;
          touch = ev.pointerType !== "mouse";
          clearTimeout(timer);
          if (!touch) return el.setPointerCapture(pid);
          timer = setTimeout(() => {
            const m = model();
            if (start === null) return;
            if (!m.dimmable) {
              // Nothing to drag: the hold is the sheet's (it says this one only switches) — opened on release.
              held = true;
              haptic(this, "medium");
              return;
            }
            grabbed = true;
            this._busy = true;
            base = m.pct;
            pct = m.pct;
            haptic(this, "medium");
            el.classList.add("grab");
            try {
              el.setPointerCapture(pid);
            } catch {}
          }, 350);
        });
        // The page may not scroll once the tile is grabbed (touch-action was decided at touchstart).
        el.addEventListener("touchmove", (ev) => grabbed && ev.cancelable && ev.preventDefault(), { passive: false });
        el.addEventListener("pointermove", (ev) => {
          if (start === null) return;
          if (touch) {
            if (!grabbed) {
              if (Math.hypot(ev.clientX - x0, ev.clientY - start) > 8) {
                clearTimeout(timer);
                start = null; // a scroll: this gesture does nothing
              }
              return;
            }
            moved = true;
            el.classList.add("dragging");
            show(Math.round(Math.min(100, Math.max(0, base + ((start - ev.clientY) / el.getBoundingClientRect().height) * 100))));
            el.classList.toggle("off", pct === 0 && model().dom === "light");
            return;
          }
          const m = model();
          if (!m.dimmable) return;
          if (!moved && Math.abs(ev.clientY - start) < 6) return;
          moved = true;
          this._busy = true;
          el.classList.add("dragging");
          show(at(ev));
          el.classList.toggle("off", pct === 0 && m.dom === "light");
        });
        const end = (ev) => {
          clearTimeout(timer);
          el.classList.remove("dragging", "grab");
          if (start === null) {
            if (grabbed) settle();
            grabbed = false;
            return;
          }
          start = null;
          const up = ev.type === "pointerup";
          const m = model();
          if (!up) {
            // iOS took the gesture for a scroll: nothing is sent
            if (grabbed || moved) settle();
            grabbed = false;
            return;
          }
          // A long press and let go without moving (family review round 2): the tile's control sheet — Apple Home's big
          // slider for a dimmable light / blind, «только включается и выключается» for one that only switches.
          if (touch && (grabbed || held) && !moved) {
            grabbed = false;
            held = false;
            openTile(this, id);
            return settle();
          }
          if (touch && !grabbed && !GST.ok) return;
          haptic(this);
          grabbed = false;
          if (moved) {
            if (m.dom === "light") call(this._hass, "light", pct ? "turn_on" : "turn_off", pct ? { entity_id: id, brightness_pct: Math.max(1, pct) } : { entity_id: id });
            else if (m.dom === "cover") call(this._hass, "cover", "set_cover_position", { entity_id: id, position: pct });
            else if (m.dom === "fan") call(this._hass, "fan", pct ? "set_percentage" : "turn_off", pct ? { entity_id: id, percentage: Math.max(1, pct) } : { entity_id: id });
            else if (m.dom === "media_player") call(this._hass, "media_player", "volume_set", { entity_id: id, volume_level: pct / 100 });
          } else if (m.dom === "media_player") {
            const f = num(m.s.attributes.supported_features) || 0;
            if (!m.on) call(this._hass, "media_player", "turn_on", { entity_id: id });
            else if (f & (1 | 16384)) call(this._hass, "media_player", "media_play_pause", { entity_id: id });
            else call(this._hass, "homeassistant", "toggle", { entity_id: id });
          } else if (m.dom === "cover") {
            call(this._hass, "cover", m.s.state === "opening" || m.s.state === "closing" ? "stop_cover" : m.s.state === "closed" ? "open_cover" : "close_cover", { entity_id: id });
          } else {
            call(this._hass, "homeassistant", "toggle", { entity_id: id });
          }
          setTimeout(() => {
            this._busy = false;
            this._sig = "";
            this._update();
          }, 400);
        };
        el.addEventListener("pointerup", end);
        el.addEventListener("pointercancel", end);
        el.addEventListener("contextmenu", (ev) => {
          ev.preventDefault();
          if (touch && (grabbed || held || start !== null)) return; // a finger's long press is the grab, not the detail
          openTile(this, id);
        });
      });
    }
    getGridOptions() {
      return { columns: 12, rows: "auto" };
    }
  }

  // --- hauflo-alert: the alarm as the screen's one moment; the action by a swipe ------------------
  class Alert extends EHCard {
    watched() {
      return [...(this._config.alarms || []), this._config.action?.entity_id].filter(Boolean);
    }
    css() {
      return `.a{padding:22px 22px 18px;display:flex;flex-direction:column}
.a .glow{background:radial-gradient(60% 50% at 20% 0%,var(--alertGlow),transparent 75%);opacity:.9}
.tag{display:inline-flex;align-items:center;gap:8px;font:500 12px Onest,sans-serif;letter-spacing:.18em;text-transform:uppercase;color:var(--alertT)}
.tag i{width:8px;height:8px;border-radius:4px;background:var(--alert);box-shadow:0 0 14px var(--alert)}
.call{display:flex;align-items:center;gap:10px;min-height:48px;margin-top:10px;padding:0 16px;border-radius:16px;text-decoration:none;color:var(--ink);background:color-mix(in srgb,var(--alert) 10%,transparent);box-shadow:inset 0 0 0 1px color-mix(in srgb,var(--alert) 35%,transparent);font:500 14px Onest,sans-serif}
.call ha-icon{--mdc-icon-size:20px;color:var(--alertT)}.call .cl{flex:1;min-width:0;display:flex;flex-direction:column;padding:8px 0}.call .cl span{white-space:normal;overflow-wrap:break-word;hyphens:auto}.call b{font:400 13px Onest,sans-serif;color:var(--ink2);white-space:normal}.call:active{transform:scale(.98)}
h2{font:300 30px/1.1 Onest,sans-serif;letter-spacing:-.03em;margin:14px 0 0}
.w{font:400 15px/1.5 Onest,sans-serif;color:var(--ink2);margin-top:12px}.todo{font:500 13px/1.45 Onest,sans-serif;color:var(--ink);margin-top:8px;padding:10px 12px;border-radius:14px;background:var(--track)}
.sl{margin-top:22px;height:68px;border-radius:34px;position:relative;display:flex;align-items:center;justify-content:center;font:500 15px Onest,sans-serif;color:var(--ink2);box-shadow:var(--eh-edge,inset 0 0 0 1px var(--hair));background:var(--eh-glass,transparent);touch-action:pan-y;user-select:none;-webkit-user-select:none;overflow:hidden}
.sl span{padding-left:56px}.k{position:absolute;left:5px;top:5px;width:58px;height:58px;border-radius:29px;background:var(--alert);display:grid;place-items:center;color:#fff;box-shadow:0 0 30px var(--alertGlow);cursor:grab}
.k ha-icon{--mdc-icon-size:24px}.done{color:var(--ink)}
.ok{display:flex;align-items:center;gap:10px;font:500 15px Onest,sans-serif;color:var(--ink2)}
.ack,.act{margin-top:12px;align-self:stretch;min-height:48px;padding:6px 18px;border-radius:14px;font:500 15px Onest,sans-serif;background:var(--track);color:var(--ink);box-shadow:inset 0 0 0 1px var(--eh-line-strong,var(--hair))}.act{margin-top:10px;color:var(--eh-alert-text,var(--alert));box-shadow:inset 0 0 0 1.5px currentColor}.act.arm{background:var(--alert);color:#fff;box-shadow:none}.act:disabled{opacity:.6}
.ackh{font:400 12px/1.35 Onest,sans-serif;color:var(--ink2);margin-top:6px}.ack.all{background:var(--ink);color:var(--eh-bg,var(--card-background-color,#fff));box-shadow:none}
.more .mr{display:flex;align-items:center;gap:8px;justify-content:space-between}.more .mr span{min-width:0;overflow-wrap:break-word;hyphens:auto}.ack1{flex:none;min-height:36px;padding:0 12px;border-radius:12px;font:500 12px Onest,sans-serif;background:var(--track);color:var(--ink);box-shadow:inset 0 0 0 1px var(--eh-line-strong,var(--hair))}
.more{margin-top:12px;display:flex;flex-direction:column;gap:6px;font:400 13px/1.4 Onest,sans-serif;color:var(--ink2)}.more [data-e]{cursor:pointer}
.nrm{--alert:var(--amber);--alertGlow:var(--amberGlow)}.nrm .tag{color:var(--amberT)}.nrm .tag i{background:var(--amber);box-shadow:0 0 14px var(--amberGlow)}.nrm .glow{background:radial-gradient(60% 50% at 20% 0%,var(--amberGlow),transparent 75%)}`;
    }
    render() {
      const h = this._hass;
      const c = this._config;
      const open = openAlarms(this, c.alarms || []);
      const on = open.map((a) => st(h, a.entity)).filter(Boolean);
      const shown = !!on.length;
      if (shown !== !!this._shown) {
        this._shown = shown;
        setTimeout(() => AGENT.cards.forEach((x) => x.localName === "hauflo-hero" && ((x._sig = ""), x._update())), 0);
      }
      if (!on.length) {
        const deaf = (c.alarms || []).filter((id) => nd(st(h, id))).length;
        // A room's / floor's alert card is there only while there's something to say — the card decides by the
        // agent's alarms, not HA's «state on» (a link rule's alarm is «off»).
        if (c.hide_ok && !deaf) {
          this.style.display = "none";
          return "";
        }
        this.style.display = "";
        return `<div class="glass a" style="min-height:0;padding:16px"><div class="ok"><ha-icon icon="hauflo:shield" style="color:var(--ink2)"></ha-icon>${deaf ? `Нет связи · ${deaf} ${sensorsWord(deaf)}` : "Аварий нет"}</div></div>`;
      }
      this.style.display = "";
      const s = on[0];
      const top = open[0];
      const word = ALARM_WORD[s.attributes.device_class] || "Авария";
      // Since when — from the agent (HA's last_changed restarts with HA), else HA's; the date when not today.
      const since = houseClock(h, top.since || s.last_changed);
      const today = since.day === houseClock(h, Date.now()).day;
      const when = today ? since.hhmm : `${since.ddmm} ${since.hhmm}`;
      // Priority by colour (ISA-101): critical red, the rest amber — a floor heating fault isn't a leak.
      // Accepted (owner 28.10: «нажимаешь Принято — толку ноль»): calm until it clears; a new alarm comes back red.
      const normal = AGENT.data && (top.critical === false || !!top.ackedBy);
      this.classList.toggle("normal", !!normal);
      const act = c.action && st(h, c.action.entity_id);
      // «Позвонить» always (family review round 2: Galina found no number anywhere) — the house's contact from the deploy.
      const svc = serviceOf(this);
      const rest = open.slice(1, 5);
      // «Принять все» (family review round 2: 19 faults took 19 taps) only for equipment faults (round 3): a leak, smoke or
      // gas is never silenced together with floor-heating noise — each has its own «Принято».
      const faults = open.filter((a) => a.key && !a.ackedBy && !alarmCritical(h, a));
      const valve = act && c.action?.title ? c.action.title : "";
      // The room beside the word when several alarms are on, or when the name alone doesn't say where (another
      // integration's sensor: «leak_bathroom» → «Датчик протечки», room «Ванная» from its HA area).
      const an = alarmName(h, s, s.entity_id);
      const room = c.rooms?.[s.entity_id] || c.room || "";
      const area = an.tech ? alarmRoom(c, h, s.entity_id) : "";
      const where = on.length > 1 ? room || c.where || area : area && !an.name.toLowerCase().includes(area.toLowerCase()) ? area : "";
      return `<div class="glass a${normal ? " nrm" : ""}"><div class="glow"></div><div style="position:relative;display:flex;flex-direction:column;flex:1">
<div class="tag"><i></i>${esc(word)}${where ? ` · ${esc(where)}` : ""}</div>
<h2>${esc(an.name)}</h2>
<div class="w">${LL("с", "since")} ${when}${top.returned ? ` · ${LL("в норме с", "back to normal at")} ${houseClock(h, top.returned).hhmm} — ${LL("подтвердите, что видели", "confirm you've seen it")}` : ""}${top.ackedBy ? ` · принял ${esc(top.ackedBy)}` : ""}</div>
${rest.length ? `<div class="more">${rest.map((a) => `<div class="mr"><span data-e="${esc(a.entity)}">${a.critical === false ? "○" : "●"} ${esc(alarmName(h, st(h, a.entity), a.entity).name)}${alarmRoom(c, h, a.entity) ? ` · ${esc(alarmRoom(c, h, a.entity))}` : ""}${a.ackedBy ? ` · принял ${esc(a.ackedBy)}` : ""}</span>${a.key && !a.ackedBy ? `<button class="ack1" data-ack="${esc(a.key)}">Принято</button>` : ""}</div>`).join("")}${open.length > 5 ? `<div>ещё ${open.length - 5} ${pluralAlarms(open.length - 5)}</div>` : ""}</div>` : ""}
<div class="todo">${esc(c.todo || alarmTodo(s.attributes.device_class, !!svc?.phone, !!AGENT.data?._pushed?.includes(s.entity_id), s.attributes.device_class === "moisture" ? valve : ""))}</div>
${act ? `<button class="act" data-act="1">${esc(c.action.title || LL("Выполнить", "Run"))}</button>` : ""}
${act ? `<div class="sl"><div class="k"><ha-icon icon="${esc(c.action.icon || "hauflo:valve")}"></ha-icon></div><span>${LL("Сдвиньте", "Slide")} — ${esc((c.action.title || LL("выполнить", "run")).toLowerCase())}</span></div>` : ""}
${callLink(svc)}
${top.key && !top.ackedBy ? `<button class="ack" data-ack="${esc(top.key)}">Принято: <span data-own>${esc(an.name)}</span></button>` : ""}${faults.length > 1 ? `<button class="ack all" data-ackall="${esc(faults.map((a) => a.key).join("\n"))}">Принять все неисправности · ${faults.length}</button>` : ""}${(top.key && !top.ackedBy) || faults.length > 1 ? `<div class="ackh">тревога останется, пока причина не устранена; видно, кто принял</div>` : ""}</div></div>`;
    }
    bind() {
      // A tap on the alarm opens the sensor (its history, since when); the swipe stays the action.
      this.shadowRoot.querySelector(".a h2, .a .w")?.closest(".a")?.addEventListener("click", (ev) => {
        if (ev.target.closest(".sl")) return;
        if (ev.target.closest(".ack, .act, .more, .call")) return;
        const on = openAlarms(this, this._config.alarms || [])[0]?.entity;
        if (on) moreInfo(this, on);
      });
      // «Принято» from the screen (SCADA expert 25.10: only a phone's push could accept): the agent records who.
      this.shadowRoot.querySelector("[data-ackall]")?.addEventListener("click", async (ev) => {
        ev.stopPropagation();
        const b = ev.currentTarget;
        b.disabled = true;
        const me = this._hass.user?.name || "вы";
        const keys = String(b.dataset.ackall || "").split("\n").filter(Boolean);
        const items = (AGENT.data?._list || []).filter((a) => !a.ackedBy && keys.includes(a.key));
        items.forEach((a) => (a.ackedBy = me));
        for (const c of AGENT.cards) (c._sig = ""), c._update();
        try {
          await this._hass.callApi("POST", "hauflo/alarms", { ack_keys: keys });
        } catch (e) {
          items.forEach((a) => (a.ackedBy = null));
          for (const c of AGENT.cards) (c._sig = ""), c._update();
          const again = this.shadowRoot.querySelector("[data-ackall]");
          if (again) again.textContent = tr("Не получилось — ещё раз");
          return;
        }
        AGENT.at = 0;
        AGENT.sig = "";
        refreshAlarms(this._hass);
      });
      this.shadowRoot.querySelectorAll("[data-ack]").forEach((el) => el.addEventListener("click", async (ev) => {
        ev.stopPropagation();
        const b = ev.currentTarget;
        b.disabled = true;
        // At once on every card (owner 28.10: nothing changed on a tap); the agent's answer confirms it.
        const item = (AGENT.data?._list || []).find((a) => a.key === b.dataset.ack);
        const was = item?.ackedBy;
        if (item) item.ackedBy = this._hass.user?.name || "вы";
        for (const c of AGENT.cards) (c._sig = ""), c._update();
        try {
          await this._hass.callApi("POST", "hauflo/alarms", { ack: b.dataset.ack });
        } catch (e) {
          if (item) item.ackedBy = was;
          for (const c of AGENT.cards) (c._sig = ""), c._update();
          const again = this.shadowRoot.querySelector(`[data-ack="${window.CSS.escape(b.dataset.ack)}"]`);
          if (again) again.textContent = tr("Не получилось — ещё раз");
          return;
        }
        AGENT.at = 0;
        AGENT.sig = "";
        refreshAlarms(this._hass);
      }));
      this.shadowRoot.querySelectorAll(".more [data-e]").forEach((el) => el.addEventListener("click", (ev) => (ev.stopPropagation(), moreInfo(this, el.dataset.e))));
      // The tap alternative to the swipe («Закрыть кран»): the first tap asks, the second does it.
      const runAction = () => {
        const a = this._config.action;
        // Only shutting something off — never a service of the config's choosing (security expert 25.10).
        if (!a || !["switch.turn_off", "valve.close_valve"].includes(a.service)) return false;
        const [domain, service] = a.service.split(".");
        call(this._hass, domain, service, { entity_id: a.entity_id });
        return true;
      };
      const ab = this.shadowRoot.querySelector("button.act");
      if (ab) {
        const lbl = ab.textContent;
        ab.addEventListener("click", (ev) => {
          ev.stopPropagation();
          if (ab.dataset.arm !== "1") {
            ab.dataset.arm = "1";
            ab.classList.add("arm");
            ab.textContent = tr(`Точно? ${lbl}`);
            this._busy = true;
            setTimeout(() => {
              if (ab.isConnected && ab.dataset.arm === "1") {
                ab.dataset.arm = "";
                ab.classList.remove("arm");
                ab.textContent = lbl;
              }
              this._busy = false;
            }, 5000);
            return;
          }
          haptic(this, "success");
          if (runAction()) {
            ab.textContent = tr("Выполнено");
            ab.disabled = true;
            ab.classList.remove("arm");
          }
        });
      }
      const sl = this.shadowRoot.querySelector(".sl");
      if (!sl) return;
      const k = sl.querySelector(".k");
      let x0 = 0;
      let dx = 0;
      const max = () => sl.clientWidth - k.clientWidth - 10;
      // A sideways swipe only: a page scroll that starts on the handle scrolls, a tap on it does nothing.
      hdrag(k, {
        down: (ev) => void (x0 = ev.clientX),
        start: () => (this._busy = true),
        move: (ev) => {
          dx = Math.min(max(), Math.max(0, ev.clientX - x0));
          k.style.transform = `translateX(${dx}px)`;
        },
        end: (ev, ok) => {
          const a = this._config.action;
          if (ok && dx >= max() * 0.85 && a) {
            haptic(this, "success");
            if (!runAction()) return void (this._busy = false);
            sl.querySelector("span").textContent = "Выполнено";
            sl.querySelector("span").classList.add("done");
          } else {
            k.style.transition = "transform .25s";
            k.style.transform = "translateX(0)";
            setTimeout(() => (k.style.transition = ""), 260);
          }
          dx = 0;
          setTimeout(() => {
            this._busy = false;
          }, 300);
        },
      });
    }
    getGridOptions() {
      return { columns: 12, rows: "auto" };
    }
  }

  // --- zones from the names ETS gives: «Люстра, зона ТВ», «Подсветка зоны острова», «обеденная зона» --
  const ZONE_FIX = { острова: "Остров", "living": "Гостиная зона", обеденная: "Обеденная", кухни: "Кухня", кухонная: "Кухня", входа: "Вход", входная: "Вход", тв: "ТВ" };
  const zoneOf = (name) => {
    const n = String(name || "");
    const m = /зон[аеыу]\s+([A-Za-zА-Яа-яЁё0-9-]+)/i.exec(n) || /([A-Za-zА-Яа-яЁё0-9-]+)\s+зон[аеыу]/i.exec(n);
    if (!m) return null;
    const w = m[1];
    const fix = ZONE_FIX[w.toLowerCase()];
    return fix || (w.length <= 3 ? w.toUpperCase() : w[0].toUpperCase() + w.slice(1));
  };
  /** Name inside its zone chip: the zone words dropped («Люстра, зона ТВ» → «Люстра»). */
  const shortName = (name) => {
    const n = String(name || "");
    const first = /[,(]?\s*(?:в\s+)?зон[аеыу]\s+[A-Za-zА-Яа-яЁё0-9-]+\s*\)?/i;
    const t = (first.test(n) ? n.replace(first, "") : n.replace(/[,(]?\s*(?:в\s+)?[A-Za-zА-Яа-яЁё0-9-]+\s+зон[аеыу]\s*\)?/i, "")).replace(/\s{2,}/g, " ").replace(/[\s,]+$/, "").replace(/\s+(у|в|во|на|для|около|возле|над|под)$/i, "").trim();
    return t || name;
  };

  // Blinds go up/down, curtains apart/together (owner 2026-10-25: «часто влево вправо»). Set per cover
  // (`direction: horizontal|vertical`) or for the card; else by the name: roller/жалюзи/рольставни go up,
  // «штора», «тюль», «портьера» to the side.
  // The curtain type the integrator set in the step «Сцены» is HA's device class (owner 28.10: рулонные, тюль, портьеры…):
  // a curtain to the side, a shade / blind / shutter / awning up and down.
  const coverDir = (e, cfg) => {
    const d = e.direction || cfg.direction;
    if (d === "horizontal" || d === "vertical") return d;
    const dc = e.s?.attributes?.device_class;
    if (dc === "curtain") return "horizontal";
    if (["shade", "blind", "shutter", "awning"].includes(dc)) return "vertical";
    const n = String(e.name || nameOf(e.s, e.entity) || "").toLowerCase();
    if (/рулон|жалюз|рольстав|ролет|маркиз|roller|blind|shutter|screen|экран/.test(n)) return "vertical";
    if (/штор|тюл|портьер|занавес|curtain|drape/.test(n)) return "horizontal";
    return "vertical";
  };
  // A real position: the cover can be sent to one and its config doesn't say `position: false`. Hauflo writes
  // `position: false` for a blind without a position state address — HA's «current_position» there is only its estimate
  // from the travel time. Decided by the device, not by its state: a blind keeps one look whatever it is doing
  // (owner 27.10: the tiles changed when pressed — «нет данных» had buttons, a known one had none).
  const coverHasPosition = (e, s) => e.position !== false && ((num(s?.attributes.supported_features) || 0) & 4) !== 0;
  // A blind with no position status says nothing until it moves: no «нет данных» on every row (owner, Горького 3, 28.10) —
  // the block's header says «положение неизвестно» once; a lost link is still said per blind.
  const coverWord = (s) => (s.state === "unavailable" ? "нет связи" : s.state === "unknown" ? "" : s.state === "opening" ? "открывается…" : s.state === "closing" ? "закрывается…" : s.state === "closed" ? "закрыта" : "открыта");
  /** The cover's own icon by its type (device class), else by the way it moves. */
  const COVER_ICON = { curtain: "hauflo:curtain", shade: "mdi:roller-shade", blind: "mdi:blinds-horizontal", shutter: "mdi:window-shutter", awning: "mdi:awning-outline" };
  const coverIcon = (e, cfg) => COVER_ICON[e.s?.attributes?.device_class] || (coverDir(e, cfg) === "horizontal" ? "hauflo:curtain" : "hauflo:blind");
  // Sideways: «Раздвинуть / Задвинуть»; up and down: «Поднять / Опустить» (owner 28.10).
  const COVER_BTN = {
    vertical: [["open_cover", "mdi:chevron-up", "Поднять"], ["stop_cover", "mdi:stop", "Стоп"], ["close_cover", "mdi:chevron-down", "Опустить"]],
    horizontal: [["open_cover", "mdi:curtains", "Раздвинуть"], ["stop_cover", "mdi:stop", "Стоп"], ["close_cover", "mdi:curtains-closed", "Задвинуть"]],
  };
  const coverButtons = (dir, attr, id, only) =>
    COVER_BTN[dir]
      .filter(([svc]) => !only || only.includes(svc))
      .map(([svc, icon, label]) => `<button ${attr}="${svc}" ${id ? `data-g="${esc(id)}"` : ""} aria-label="${label}" title="${label}"><ha-icon icon="${icon}"></ha-icon></button>`)
      .join("");

  /** A console for many devices: master row, zone chips, compact pills (drag sideways to set). */
  class Console extends EHCard {
    constructor() {
      super();
      this._zone = "";
    }
    watched() {
      return [...(this._config.entities || []), ...(this._config.groups || []), ...(this._config.lamps || [])].map((e) => (typeof e === "string" ? e : e.entity));
    }
    items() {
      return (this._config.entities || [])
        .map((e) => (typeof e === "string" ? { entity: e } : e))
        .map((e) => ({ ...e, s: st(this._hass, e.entity) }))
        .filter((e) => e.s);
    }
    css() {
      return `.k{padding:16px}.hd{display:flex;align-items:center;gap:12px}.hd .sum{flex:1;min-width:0}
.pill.wide{grid-column:span 2}.more{margin-top:10px;width:100%;height:44px;border-radius:14px;font:500 13px Onest,sans-serif;color:var(--ink2);box-shadow:inset 0 0 0 1px var(--line)}
.hd{flex-wrap:wrap}.cover .hd .sum{flex:1 1 auto;min-width:0}
.hd b{font:300 24px Onest,sans-serif;letter-spacing:-.03em;display:block;line-height:1.1;white-space:normal;overflow-wrap:break-word;hyphens:auto}
.hd b.nd{font:400 15px/1.3 Onest,sans-serif;letter-spacing:0;color:var(--ink3);margin-top:4px;white-space:normal}
.grp{margin-top:14px}.grp .caps{display:block;margin-bottom:8px}.grow{display:flex;gap:6px;flex-wrap:wrap}
.g{display:flex;align-items:center;gap:6px;padding:5px 5px 5px 14px;border-radius:18px;box-shadow:inset 0 0 0 1px var(--line);font:500 12px Onest,sans-serif;max-width:100%}
.g span{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;hyphens:auto;overflow-wrap:break-word;line-height:1.25}.g.on{background:var(--track);box-shadow:none}.g.on.lt{background:color-mix(in srgb,var(--amber) 22%,transparent)}
.g button{width:44px;height:44px;border-radius:14px;display:grid;place-items:center;color:var(--ink);box-shadow:inset 0 0 0 1px var(--line);background:var(--eh-glass,transparent);flex:0 0 auto}.g button ha-icon{--mdc-icon-size:20px}.g button:active,.ud button:active{transform:scale(.92)}
.g.lt{padding:0 16px;height:44px;cursor:pointer}
.g.gc{display:grid;grid-template-columns:1fr repeat(3,44px);gap:6px;width:100%;padding:5px 5px 5px 16px;font:500 13px Onest,sans-serif}
.g.gc span{min-width:0;overflow:hidden;white-space:normal;line-height:1.25;display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:3;align-self:center;overflow-wrap:break-word;hyphens:auto}.g.gc small{font:400 12px Onest,sans-serif;color:var(--ice)}
.g.gc.mv{background:color-mix(in srgb,var(--ice) 14%,transparent);box-shadow:none}.hd .caps{display:block;margin-bottom:4px}
.mst{min-height:44px;border-radius:22px;display:flex;flex-wrap:wrap;align-items:center;gap:8px;padding:0 6px}
.mst button{height:auto;min-height:44px;min-width:44px;padding:6px 16px;line-height:1.2;border-radius:22px;font:500 13px Onest,sans-serif;color:var(--ink);box-shadow:inset 0 0 0 1px color-mix(in srgb,var(--ink) 22%,transparent)}
.mst button.arm{background:color-mix(in srgb,var(--alert) 14%,transparent);box-shadow:inset 0 0 0 1.5px var(--alert);color:var(--alert-text,var(--ink))}.mst button.lbl{width:auto;display:inline-flex;align-items:center;gap:6px;padding:0 14px 0 10px;border-radius:22px}.mst button.lbl ha-icon{--mdc-icon-size:21px}
.mst button.on{background:color-mix(in srgb,var(--amber) 16%,transparent);color:var(--amberT);box-shadow:inset 0 0 0 1.5px color-mix(in srgb,var(--amber) 70%,transparent)}
.mst button:active,.pill:active{transform:scale(.97)}
.bar{margin-top:14px;height:40px;border-radius:14px;background:var(--track);position:relative;overflow:hidden;touch-action:pan-y;cursor:ew-resize}
.bar i{position:absolute;left:0;top:0;bottom:0;background:linear-gradient(90deg,color-mix(in srgb,var(--amber) 55%,transparent),color-mix(in srgb,var(--amber) 85%,transparent));box-shadow:10px 0 30px var(--amberGlow)}
.cover .bar i{background:linear-gradient(90deg,color-mix(in srgb,var(--ice) 35%,transparent),color-mix(in srgb,var(--ice) 65%,transparent));box-shadow:10px 0 30px var(--iceGlow)}
.mst button.ico{width:44px;height:44px;padding:0;border-radius:22px;display:grid;place-items:center;color:var(--ink)}.mst button.ico ha-icon{--mdc-icon-size:21px}.cover .mst{height:auto}
.bar span{position:absolute;left:14px;top:11px;font:500 12px Onest,sans-serif}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:14px}
.chips button{flex:0 0 auto;min-height:44px;padding:0 15px;display:inline-flex;align-items:center;border-radius:14px;font:500 12px Onest,sans-serif;color:var(--ink2);box-shadow:inset 0 0 0 1px var(--line)}
.chips button.on{color:var(--ink);background:var(--track);box-shadow:none}.chips em{font-style:normal;color:var(--amberT);margin-left:5px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:12px}
.pill{position:relative;min-height:56px;padding-top:7px;padding-bottom:7px;border-radius:18px;overflow:hidden;display:flex;align-items:center;gap:10px;padding:0 12px;box-shadow:inset 0 0 0 1px var(--hair);touch-action:pan-y;user-select:none;-webkit-user-select:none;cursor:pointer}
.pill .f{position:absolute;left:0;top:0;bottom:0;transition:width .25s}
.pill.dragging .f{transition:none}.pill>*:not(.f){position:relative}
.pill ha-icon{color:var(--ink3);--mdc-icon-size:18px}.pill .tx{min-width:0;flex:1}
.pill .nm{font:500 13px Onest,sans-serif;line-height:1.25;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;overflow-wrap:break-word;hyphens:auto}
.pill .lv{font:400 13px Onest,sans-serif;line-height:1.25;color:var(--ink2);white-space:normal;overflow:hidden;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow-wrap:break-word;hyphens:auto}
.ud .bl{display:none}@container (min-width:560px){.pill.wide .ud button{width:auto;min-width:44px;padding:0 12px;display:flex;align-items:center;gap:6px;font:500 12px Onest,sans-serif}.pill.wide .ud .bl{display:inline}}
.pill.on{box-shadow:none;background:var(--track)}.pill.on>ha-icon{color:var(--accentC)}
.light .pill.on .f{background:linear-gradient(90deg,color-mix(in srgb,var(--amber) 42%,transparent),color-mix(in srgb,var(--amber) 22%,transparent))}
.cover .pill.on .f{background:linear-gradient(90deg,color-mix(in srgb,var(--ice) 36%,transparent),color-mix(in srgb,var(--ice) 18%,transparent))}
.light{--accentC:var(--amber)}.cover{--accentC:var(--ice)}
.pill.solo.on{background:color-mix(in srgb,var(--amber) 22%,transparent);box-shadow:0 0 22px color-mix(in srgb,var(--amberGlow) 50%,transparent)}
.ud{display:flex;gap:6px}.ud button{width:44px;height:44px;border-radius:14px;display:grid;place-items:center;color:var(--ink);box-shadow:inset 0 0 0 1px var(--line);background:var(--eh-glass,transparent)}.ud button ha-icon{--mdc-icon-size:19px}
.unavail{opacity:.45}`;
    }
    render() {
      const all = this.items();
      const isCover = this.kind === "cover";
      const zones = [];
      for (const e of all) {
        const z = e.zone || zoneOf(e.name || nameOf(e.s, e.entity)) || "";
        e.z = z;
        if (z && !zones.includes(z)) zones.push(z);
      }
      // A name that mentions a zone without the word «зона» joins it: «Над островом кухни», «Внутри балки
      // кухня» → «Кухня» (integrator run 25.10: they were in «Прочее», the zone held one track).
      const stem = (w) => w.toLowerCase().replace(/ё/g, "е").slice(0, Math.max(3, Math.min(5, w.length - 1)));
      for (const e of all) {
        if (e.z) continue;
        const words = String(e.name || nameOf(e.s, e.entity)).toLowerCase().replace(/ё/g, "е").split(/[^a-zа-я0-9]+/);
        const hit = zones.find((z) => z.length > 2 && words.some((w) => w.length > 2 && w.startsWith(stem(z))));
        if (hit) e.z = hit;
      }
      if (zones.length && all.some((e) => !e.z)) zones.push("Прочее");
      for (const e of all) if (!e.z && zones.length) e.z = "Прочее";
      if (this._zone && !zones.includes(this._zone)) this._zone = "";
      // «Закарнизная подсветка (часть 1/2, 2/2)» live behind their group «Закарнизная подсветка (вся)» (persona review 25.10).
      const groupNames = (this._config.groups || []).map((id) => nameOf(st(this._hass, id), id).replace(/\s*\((вс[яеё]|весь|all)\)\s*$/i, "").trim().toLowerCase()).filter((n) => n.length > 3);
      const partOfGroup = (e) => /\(част|part/i.test(e.name || nameOf(e.s, e.entity)) && groupNames.some((g) => String(e.name || nameOf(e.s, e.entity)).toLowerCase().startsWith(g));
      const zoneItems = (this._zone ? all.filter((e) => e.z === this._zone) : all).filter((e) => this._expanded || !partOfGroup(e));
      // Blinds folded behind their groups: ▲ ■ ▼ still move every blind of the zone, not only the ETS groups (integrator
      // review 28.10: groups «2, 4, 6» and «Угловая, Деверь» left blinds 1, 3, 5 out of «все»).
      this._coverIds = isCover ? zoneItems.map((e) => e.entity) : null;
      const known = (e) => !["unknown", "unavailable"].includes(e.s.state);
      const isOpenOn = (e) => known(e) && (isCover ? e.s.state !== "closed" : e.s.state === "on");
      const onCount = (xs) => xs.filter(isOpenOn).length;
      // A big room (40 lights): what's on (at least 6 pills) and «Показать все» — not a 3000px list.
      const LIMIT = 12;
      let shown = zoneItems;
      let hidden = 0;
      // Blinds with groups: «все» and the groups first, one by one behind a tap — not 36 buttons for 8 blinds
      // (persona review 25.10).
      const coversFolded = isCover && (this._config.groups || []).length > 0 && zoneItems.length > 4;
      if (!this._expanded && coversFolded) {
        shown = [];
        hidden = zoneItems.length;
      } else if (!this._expanded && zoneItems.length > LIMIT) {
        const on = zoneItems.filter(isOpenOn);
        const keep = new Set(on.length >= 6 ? on : [...on, ...zoneItems.filter((e) => !isOpenOn(e)).slice(0, 6 - on.length)]);
        shown = zoneItems.filter((e) => keep.has(e));
        hidden = zoneItems.length - shown.length;
      }
      const levels = shown.map((e) => (isCover ? (this.settable(e) ? num(e.s.attributes.current_position) : undefined) : e.s.state === "on" && num(e.s.attributes.brightness) !== undefined ? brightnessPct(e.s) : undefined)).filter((x) => x !== undefined);
      const lvl = levels.length ? Math.round(avg(levels)) : undefined;
      // Only group commands (a floor's, the building's): the summary and the master act on them.
      const groupsOnly = !all.length;
      const gItems = (this._config.groups || []).map((id) => ({ entity: id, s: st(this._hass, id) })).filter((g) => g.s);
      // A floor's/building's console: its group commands and a few devices («Мультимедиа») — the summary and
      // «Вкл./Выкл. все» count both (integrator run 25.10: one switch hid the floor's lit groups).
      const both = !!this._config.master_groups && !this._zone;
      // The lamps the commands act on, when the dashboard names them: what's lit is counted by them — the central
      // switch's own state said «Выключен» under «17 ламп в 9 комнатах» (review round 2).
      const lampItems = (this._config.lamps || []).map((id) => ({ entity: id, s: st(this._hass, id) })).filter((x) => x.s);
      const gCount = lampItems.length ? lampItems : gItems;
      const counted = groupsOnly ? gCount : both ? [...zoneItems, ...gCount] : zoneItems;
      const n = onCount(counted);
      // A lit group counts in the headline too: «Выключен» above a lit «Закарнизная подсветка» (persona round 4).
      const shownN = groupsOnly || both ? n : onCount([...zoneItems, ...gItems]);
      const shownTotal = groupsOnly || both ? counted.length : zoneItems.length + gItems.length;
      const title = blockTitle(this, isCover ? "Шторы" : "Свет");
      const total = counted.length;
      const unknownAll = counted.every((e) => !known(e));
      const summary = unknownAll && total ? (isCover ? "Положение неизвестно" : "Нет данных") : isCover ? (n ? `${n} из ${total} открыто` : "Все закрыты") : shownN ? `${shownN} из ${shownTotal}${lvl !== undefined ? ` · ${lvl}\u00a0%` : ""}` : "Выключен";
      const master = isCover
        ? coverButtons(all.length && all.filter((e) => coverDir(e, this._config) === "horizontal").length * 2 >= all.length ? "horizontal" : "vertical", "data-all")
            .replace(/<button /g, '<button class="ico" ')
            .replace(/<button class="ico" ([^>]*aria-label="([^"]+)"[^>]*>)<ha-icon([^>]*)><\/ha-icon>/g, (m, a, lab, ic) => (/stop_cover/.test(a) ? m : `<button class="lbl" ${a}<ha-icon${ic}></ha-icon><span>${lab}</span>`))
        : (() => {
            // Only lights are «весь свет»: sockets and other relays in the block make it «всё» (family review round 3).
            const lightsOnly = counted.every((e) => e.entity.startsWith("light."));
            return `<button data-all="${n ? "turn_off" : "turn_on"}" class="${n ? "on" : ""}">${n ? (lightsOnly ? "Выключить весь свет" : "Выключить всё") : lightsOnly ? "Включить весь свет" : "Включить всё"}</button>`;
          })();
      const dimmables = shown.filter((e) => this.settable(e));
      const pill = (e) => {
        // The zone is in the chip (or under the name): the name itself without it.
        const full = e.name || nameOf(e.s, e.entity);
        let name = e.z && e.z !== "Прочее" ? shortName(full) : full;
        const zoneTagAll = !this._zone && e.z && e.z !== "Прочее" ? `${e.z} · ` : "";
        const settable = this.settable(e);
        const unavailable = e.s.state === "unavailable";
        if (isCover) {
          // Only a real position is a number; without one the fill is just open/closed (no travel-time guess).
          const pos = settable ? num(e.s.attributes.current_position) : undefined;
          const open = isOpenOn(e);
          // Without a position the pill has ▲ ■ ▼ — full width, the name stays readable.
          const state = open && !["opening", "closing"].includes(e.s.state) && pos !== undefined ? `${pos} %` : coverWord(e.s);
          const moving = ["opening", "closing"].includes(e.s.state);
          return `<div class="pill ${open ? "on" : ""} ${moving ? "mv" : ""} ${settable ? "" : "wide"} ${unavailable ? "unavail" : ""}" data-e="${esc(e.entity)}" data-set="${settable ? 1 : 0}"><div class="f" style="width:${moving ? 100 : open ? pos ?? 100 : 0}%"></div>
<ha-icon icon="${esc(e.icon || coverIcon(e, this._config))}"></ha-icon><div class="tx"><div class="nm">${esc(name)}</div>${state || zoneTagAll ? `<div class="lv">${esc(state ? zoneTagAll : zoneTagAll.replace(/ · $/, ""))}${state}</div>` : ""}</div>
${settable ? "" : `<div class="ud">${coverButtons(coverDir(e, this._config), "data-c").replace(/aria-label="([^"]+)"([^>]*)><ha-icon([^>]*)><\/ha-icon>/g, (m, lab, rest, ic) => `aria-label="${lab}"${rest}><ha-icon${ic}></ha-icon><span class="bl">${lab}</span>`)}</div>`}</div>`;
        }
        const on = isOpenOn(e);
        const pct = brightnessPct(e.s);
        // A long name's note in brackets goes to the second line: «Основной свет в коридоре (где…» cut on a phone (review round 2).
        const nb = name.length > 20 ? /^(.{6,}?)\s*\(([^()]{2,})\)\s*$/.exec(name) : null;
        const zoneTag = nb ? `${nb[2]} · ${zoneTagAll}` : zoneTagAll;
        name = nb ? nb[1] : name;
        return `<div class="pill ${on ? "on" : ""} ${settable ? "" : "solo"} ${unavailable ? "unavail" : ""}" data-e="${esc(e.entity)}" data-set="${settable ? 1 : 0}"><div class="f" style="width:${on && settable ? pct : 0}%"></div>
<ha-icon icon="${esc(e.icon || (!String(e.entity).startsWith("light.") ? e.s.attributes.icon || "hauflo:power" : on ? "hauflo:bulb-on" : "hauflo:bulb"))}"></ha-icon><div class="tx"><div class="nm">${esc(name)}</div><div class="lv">${esc(zoneTag)}${nd(e.s) ? "нет связи" : on ? (settable ? `${pct} %` : "вкл") : "выкл"}</div></div></div>`;
      };
      return `<div class="glass k ${isCover ? "cover" : "light"}"><div class="hd"><div class="sum"><span class="caps">${title}${this._zone ? ` · ${esc(this._zone)}` : ""}</span><b class="${isCover && unknownAll ? "nd" : ""}">${esc(summary)}</b></div><div class="mst">${master}</div></div>
${dimmables.length > 1 && (isCover || n) ? `<div class="bar" data-bar="1"><i style="width:${lvl ?? 0}%"></i><span>${isCover ? "Все" : "Яркость"}${lvl === undefined ? "" : ` · ${lvl} %`}</span></div>` : ""}
${this.sceneRow()}
${this.groupsRow()}
${zones.length > 1 ? `<div class="chips"><button data-z="" class="${this._zone ? "" : "on"}">Все</button>${zones
        .map((z) => {
          const k = onCount(all.filter((e) => e.z === z));
          return `<button data-z="${esc(z)}" class="${this._zone === z ? "on" : ""}">${esc(z)}${k ? `<em>${k}</em>` : ""}</button>`;
        })
        .join("")}</div>` : ""}
<div class="grid">${shown.map(pill).join("")}</div>
${hidden ? `<button class="more" data-more="1">${coversFolded ? `Каждая отдельно (${zoneItems.length})` : `Показать все (${total})`}</button>` : this._expanded && (total > LIMIT || coversFolded) ? `<button class="more" data-more="0">Свернуть</button>` : ""}</div>`;
    }
    /** Commands for several devices at once («Шторы 4, 6», «Подсветка (вся)») on their own row. */
    groupsRow() {
      const gs = (this._config.groups || [])
        .map((id) => ({ id, s: st(this._hass, id) }))
        .filter((g) => g.s && !(Array.isArray(g.s.attributes.entity_id) && g.s.attributes.entity_id.length < 2));
      if (!gs.length) return "";
      const label = (g) => nameOf(g.s, g.id).replace(/\s*\((вс[яеё]|весь|all)\)\s*$/i, "");
      return `<div class="grp"><span class="caps">Все вместе</span><div class="grow">${gs
        .map((g) =>
          g.id.startsWith("cover.")
            ? `<div class="g gc ${["opening", "closing"].includes(g.s.state) ? "mv" : ""}"><span>${esc(label(g))}${["opening", "closing"].includes(g.s.state) ? `<small>${g.s.state === "opening" ? "открывается…" : "закрывается…"}</small>` : ""}</span>${coverButtons(coverDir({ entity: g.id, s: g.s }, this._config), "data-gc", g.id)}</div>`
            : `<div class="g lt ${g.s.state === "on" ? "on" : ""}" data-gt="${esc(g.id)}" role="button"><span>${esc(label(g))}</span></div>`,
        )
        .join("")}</div></div>`;
    }
    settable(e) {
      if (!e.entity.startsWith(this.kind === "cover" ? "cover." : "light.")) return false;
      if (this.kind === "cover") return coverHasPosition(e, e.s);
      return (e.s.attributes.supported_color_modes || []).some((m) => m !== "onoff");
    }
    bind() {
      const root = this.shadowRoot;
      const isCover = this.kind === "cover";
      bindScenes(this);
      // Lights and switches (sockets) together: HA's generic service for the master, own domain per pill.
      const domain = isCover ? "cover" : "homeassistant";
      const shownIds = () => {
        const ids = [...root.querySelectorAll(".pill")].map((p) => p.dataset.e);
        if (isCover && !ids.length && this._coverIds?.length) return this._coverIds;
        if (this._config.master_groups && !this._zone) return [...ids, ...(this._config.groups || [])];
        return ids.length ? ids : this._config.groups || [];
      };
      /** The lamps the block's counter counts (`lamps`: the rooms' lights under a house / floor command). */
      const lampIds = () => (isCover || this._zone ? [] : (this._config.lamps || []).filter((x) => st(this._hass, x)));
      root.querySelectorAll("button[data-more]").forEach((b) =>
        b.addEventListener("click", () => {
          this._expanded = b.dataset.more === "1";
          this._sig = "";
          this._update();
        }),
      );
      root.querySelectorAll("button[data-z]").forEach((b) =>
        b.addEventListener("click", () => {
          this._zone = b.dataset.z;
          this._sig = "";
          this._update();
        }),
      );
      root.querySelectorAll("button[data-all]").forEach((b) =>
        b.addEventListener("click", () => {
          // «Выключить весь свет»: the first tap asks inline («Точно? Выключить»), the second does it — and a 3-s «Отменить».
          let snap = [];
          if (!isCover && b.dataset.all === "turn_off") {
            if (b.dataset.arm !== "1") {
              b.dataset.arm = "1";
              b.dataset.lbl = b.textContent;
              b.textContent = tr("Точно? Выключить");
              b.classList.add("arm");
              clearTimeout(this._armT);
              this._armT = setTimeout(() => {
                if (b.isConnected && b.dataset.arm === "1") {
                  b.dataset.arm = "";
                  b.textContent = b.dataset.lbl;
                  b.classList.remove("arm");
                }
              }, 4000);
              return;
            }
            snap = [...new Set([...shownIds(), ...lampIds()])].map((eid) => st(this._hass, eid)).filter((s) => s && s.state === "on");
          }
          haptic(this);
          // «Выключить весь свет» acts on what the counter counts (family review round 2: the KNX central was only the
          // façade's, «1 из 29» stayed lit): every light it counts by HA's light.turn_off, the rest (relays) by their own
          // turn_off — and the project's central command too.
          const lamps = !isCover && b.dataset.all === "turn_off" ? lampIds() : [];
          if (lamps.length) {
            const lights = lamps.filter((x) => x.startsWith("light."));
            const other = lamps.filter((x) => !x.startsWith("light."));
            sendAck(this, b, lights.length ? "light" : "homeassistant", "turn_off", { entity_id: lights.length ? lights : other });
            if (lights.length && other.length) call(this._hass, "homeassistant", "turn_off", { entity_id: other });
            const rest = shownIds().filter((x) => !lamps.includes(x));
            if (rest.length) call(this._hass, "homeassistant", "turn_off", { entity_id: rest });
          } else sendAck(this, b, domain, b.dataset.all, { entity_id: shownIds() });
          if (snap.length)
            undoToast("Свет выключен", () =>
              snap.forEach((s) => {
                const d = s.entity_id.split(".")[0];
                const br = num(s.attributes.brightness);
                call(this._hass, ["light", "switch"].includes(d) ? d : "homeassistant", "turn_on", br !== undefined && d === "light" ? { entity_id: s.entity_id, brightness: br } : { entity_id: s.entity_id });
              }),
            );
        }),
      );
      root.querySelectorAll("button[data-gc]").forEach((b) =>
        b.addEventListener("click", () => {
          haptic(this);
          sendAck(this, b, "cover", b.dataset.gc, { entity_id: b.dataset.g });
        }),
      );
      root.querySelectorAll("[data-gt]").forEach((b) => longPress(b, () => openTile(this, b.dataset.gt)));
      root.querySelectorAll(".g.gc").forEach((g) => {
        const gid = g.querySelector("button[data-g]")?.dataset.g;
        if (gid) longPress(g, () => openTile(this, gid));
      });
      root.querySelectorAll("[data-gt]").forEach((b) =>
        b.addEventListener("click", () => {
          if (GST.lp) return;
          haptic(this);
          call(this._hass, "homeassistant", "toggle", { entity_id: b.dataset.gt });
        }),
      );
      root.querySelectorAll(".ud button").forEach((b) =>
        b.addEventListener("click", (ev) => {
          ev.stopPropagation();
          haptic(this);
          sendAck(this, b, "cover", b.dataset.c, { entity_id: b.closest(".pill").dataset.e });
        }),
      );
      // Sideways swipe on a pill (or the master bar) sets the level; a clean tap toggles; a page scroll does neither.
      const drag = (el, apply, tap) => {
        let pct = 0;
        const f = el.querySelector(".f, i");
        const lv = el.querySelector(".lv, span");
        const settle = () =>
          setTimeout(() => {
            this._busy = false;
            this._sig = "";
            this._update();
          }, 450);
        hdrag(el, {
          down: (ev) => {
            if (ev.target.closest("button")) return false;
          },
          start: () => {
            this._busy = true;
            el.classList.add("dragging");
          },
          move: (ev) => {
            const r = el.getBoundingClientRect();
            pct = Math.round(Math.min(100, Math.max(0, ((ev.clientX - r.left) / r.width) * 100)));
            f.style.width = `${pct}%`;
            if (lv) lv.textContent = tr(el.dataset.bar ? `${isCover ? "Все" : "Яркость"} · ${pct} %` : `${pct} %`);
          },
          end: (ev, ok) => {
            el.classList.remove("dragging");
            if (ok) {
              haptic(this);
              apply(pct);
            }
            settle();
          },
          tap: (ev, clean) => {
            if (!clean || !tap) return;
            this._busy = true;
            haptic(this);
            tap();
            settle();
          },
        });
      };
      root.querySelectorAll(".pill").forEach((el) => {
        const id = el.dataset.e;
        const settable = el.dataset.set === "1";
        // A long press: the control sheet (brightness / position, «В избранное», «Подробнее»).
        longPress(el, () => openTile(this, id));
        drag(
          el,
          (pct) => {
            if (!settable) return;
            if (isCover) call(this._hass, "cover", "set_cover_position", { entity_id: id, position: pct });
            else call(this._hass, "light", pct ? "turn_on" : "turn_off", pct ? { entity_id: id, brightness_pct: Math.max(1, pct) } : { entity_id: id });
          },
          () => {
            const s = st(this._hass, id);
            // A moving blind stops on a tap (it reversed before — integrator run 24.10).
            if (isCover) call(this._hass, "cover", s.state === "opening" || s.state === "closing" ? "stop_cover" : s.state === "closed" ? "open_cover" : "close_cover", { entity_id: id });
            else call(this._hass, id.startsWith("light.") ? "light" : "homeassistant", "toggle", { entity_id: id });
          },
        );
      });
      const bar = root.querySelector(".bar");
      if (bar)
        drag(bar, (pct) => {
          const ids = [...root.querySelectorAll('.pill[data-set="1"]')].map((p) => p.dataset.e);
          if (isCover) call(this._hass, "cover", "set_cover_position", { entity_id: ids, position: pct });
          else {
            // Only the lights that are on follow the master dimmer — it doesn't switch the room on.
            const on = ids.filter((id) => st(this._hass, id)?.state === "on");
            if (on.length) call(this._hass, "light", pct ? "turn_on" : "turn_off", pct ? { entity_id: on, brightness_pct: Math.max(1, pct) } : { entity_id: on });
          }
        });
    }
    getGridOptions() {
      const n = (this._config?.entities || []).length;
      return { columns: 12, rows: "auto", n };
    }
  }
  class Lights extends Console {
    get kind() {
      return "light";
    }
  }
  class Covers extends Console {
    get kind() {
      return "cover";
    }
  }

  // --- a long press on a light, blind or scene (family review round 2): Apple Home's control sheet — a big brightness /
  // position slider, or a plain word that this one only switches; «В избранное» for the top of «Главная»; «Подробнее» is
  // HA's own dialog. A long press never does nothing (Marina pressed the kitchen lamp: nothing opened).
  const favItems = () => HOUSE.data?.favorites?.items || [];
  const favHas = (id) => favItems().includes(id);
  /** This person's favourites saved by the agent (per HA user); every card that shows them is drawn again. */
  const favSave = async (h, next) => {
    const r = await h.callApi("POST", "hauflo/house", { favorites: next });
    HOUSE.set({ ...(HOUSE.data || {}), favorites: r.favorites });
    return r.favorites;
  };
  const favToggle = async (h, id) => {
    const cur = HOUSE.data?.favorites || { items: [] };
    const items = favHas(id) ? cur.items.filter((x) => x !== id) : [...cur.items, id];
    return favSave(h, { ...cur, items });
  };
  /** A press-and-hold on `el` (touch, pen or mouse, finger still ≥ 500 ms): `fn()`; the tap that ends it does nothing. */
  const longPress = (el, fn, ms = 500) => {
    let t = 0;
    let at = null;
    const stop = () => {
      clearTimeout(t);
      at = null;
    };
    el.addEventListener("pointerdown", (ev) => {
      if (ev.pointerType === "mouse") return; // a mouse: the right button (contextmenu) — its press drags
      if (ev.target.closest?.("button") && ev.target.closest("button") !== el) return;
      at = [ev.clientX, ev.clientY];
      clearTimeout(t);
      t = setTimeout(() => {
        at = null;
        GST.lp = true; // the tap that ends the press isn't a tap (GST.ok false → no toggle, the click swallowed)
        GST.ok = false;
        fn();
      }, ms);
    });
    el.addEventListener("pointermove", (ev) => at && Math.hypot(ev.clientX - at[0], ev.clientY - at[1]) > 8 && stop());
    el.addEventListener("pointerup", stop);
    el.addEventListener("pointercancel", stop);
    el.addEventListener("contextmenu", (ev) => {
      ev.preventDefault();
      if (GST.type !== "mouse") return; // a finger's long press is the timer's
      stop();
      fn();
    });
  };
  const TILE_CSS = `:host{position:fixed;inset:0;z-index:9998;display:grid;place-items:end center;background:rgba(0,0,0,.42);font-family:Onest,var(--ha-font-family-body),system-ui,sans-serif;color:var(--eh-ink,var(--primary-text-color));-webkit-tap-highlight-color:transparent}
@media (min-width:600px){:host{place-items:center}}
*{box-sizing:border-box}
.sh{width:min(100%,420px);max-height:94vh;overflow:auto;padding:18px 18px calc(18px + env(safe-area-inset-bottom));display:flex;flex-direction:column;align-items:stretch;gap:12px;background:var(--card-background-color,#fff);background-image:var(--eh-glass,none);border-radius:28px 28px 0 0;box-shadow:0 20px 60px rgba(0,0,0,.35)}
@media (min-width:600px){.sh{border-radius:28px}}
.hd{display:flex;align-items:flex-start;gap:12px}.hd b{flex:1;min-width:0;font:600 20px Onest,sans-serif;line-height:1.25;overflow-wrap:break-word;hyphens:auto}.hd small{display:block;font:400 14px Onest,sans-serif;color:var(--eh-ink2,var(--secondary-text-color));margin-top:2px}
.x{width:44px;height:44px;flex:none;border-radius:22px;border:0;background:var(--eh-track,rgba(127,127,127,.12));color:inherit;cursor:pointer;display:grid;place-items:center}.x ha-icon{--mdc-icon-size:22px}
.sl{align-self:center;width:132px;height:300px;border-radius:36px;background:var(--eh-track,rgba(127,127,127,.14));position:relative;overflow:hidden;touch-action:none;cursor:ns-resize;user-select:none;-webkit-user-select:none}
.sl i{position:absolute;left:0;right:0;bottom:0;background:linear-gradient(0deg,var(--eh-amber,#FFB25C),color-mix(in srgb,var(--eh-amber,#FFB25C) 65%,transparent))}.sl.cv i{background:linear-gradient(0deg,var(--eh-ice,#8DC2FF),color-mix(in srgb,var(--eh-ice,#8DC2FF) 60%,transparent))}
.sl span{position:absolute;left:0;right:0;top:18px;text-align:center;font:300 34px Onest,sans-serif;letter-spacing:-.02em;font-variant-numeric:tabular-nums}
.msg{display:flex;align-items:center;gap:12px;padding:14px;border-radius:18px;background:var(--eh-track,rgba(127,127,127,.12));font:500 15px Onest,sans-serif;line-height:1.4}.msg ha-icon{--mdc-icon-size:26px;color:var(--eh-amber,#E8901F);flex:none}
.b{display:flex;align-items:center;justify-content:center;gap:8px;min-height:52px;padding:0 16px;border:0;border-radius:16px;background:var(--eh-track,rgba(127,127,127,.12));color:inherit;font:600 16px Onest,sans-serif;cursor:pointer}.b ha-icon{--mdc-icon-size:22px}
.b.on{background:color-mix(in srgb,var(--eh-amber,#FFB25C) 30%,transparent)}.b.fav[aria-pressed="true"] ha-icon{color:var(--eh-amber,#E8901F)}.b.lnk{background:none;min-height:44px;font-weight:500;color:var(--eh-ink2,var(--secondary-text-color))}
.r3{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}.r3 .b{flex-direction:column;gap:2px;min-height:68px;font-size:13px}`;
  let TILE = null;
  /** The control sheet of one light / blind / scene, over the page. */
  const openTile = (card, id) => {
    if (TILE) return;
    const h0 = card._hass;
    if (!st(h0, id)) return;
    haptic(card, "medium");
    HOUSE.get(card);
    const host = document.createElement("div");
    host.className = "hauflo-tile";
    const sr = host.attachShadow({ mode: "open" });
    TILE = host;
    const hass = () => card._hass || AGENT.hass;
    let drag = null;
    const close = () => {
      host.remove();
      TILE = null;
      clearInterval(live);
      document.removeEventListener("keydown", onKey);
    };
    const onKey = (e) => e.key === "Escape" && close();
    document.addEventListener("keydown", onKey);
    host.addEventListener("click", (e) => e.composedPath()[0] === host && close());
    const draw = () => {
      const h = hass();
      const s = st(h, id);
      if (!s) return close();
      const dom = id.split(".")[0];
      const name = nameOf(s, id);
      const on = s.state === "on";
      let state = "";
      let body = "";
      if (dom === "light" || dom === "switch") {
        const dim = dom === "light" && (s.attributes.supported_color_modes || []).some((m) => m !== "onoff");
        const pct = brightnessPct(s);
        state = nd(s) ? "нет связи" : on ? (dim ? `${pct} %` : "вкл") : "выкл";
        body = dim
          ? `<div class="sl" data-sl role="slider" aria-label="Яркость" aria-valuenow="${pct}"><i style="height:${on ? pct : 0}%"></i><span>${on ? pct : 0} %</span></div>`
          : `<div class="msg"><ha-icon icon="mdi:information-outline"></ha-icon><span>Этот светильник только включается и выключается</span></div>`;
        body += `<button class="b ${on ? "on" : ""}" data-tg><ha-icon icon="${on ? "hauflo:bulb-on" : "hauflo:bulb"}"></ha-icon><span>${on ? "Выключить" : "Включить"}</span></button>`;
      } else if (dom === "cover") {
        const pos = coverHasPosition({}, s) ? num(s.attributes.current_position) : undefined;
        state = coverWord(s) || (pos !== undefined ? `${pos} %` : "");
        const dir = coverDir({ entity: id, s }, {});
        body = `${pos !== undefined ? `<div class="sl cv" data-sl role="slider" aria-label="Положение" aria-valuenow="${pos}"><i style="height:${pos}%"></i><span>${pos} %</span></div>` : ""}<div class="r3">${COVER_BTN[dir].map(([svc, icon, label]) => `<button class="b" data-cv="${svc}"><ha-icon icon="${icon}"></ha-icon><span>${label}</span></button>`).join("")}</div>`;
      } else if (dom === "scene" || dom === "script") {
        state = "сценарий";
        body = `<button class="b on" data-run><ha-icon icon="hauflo:play"></ha-icon><span>Включить</span></button>`;
      } else {
        body = `<button class="b" data-tg><span>${on ? "Выключить" : "Включить"}</span></button>`;
      }
      const fav = !HOUSE.off;
      sr.innerHTML = `<style>${TZ(TILE_CSS)}</style><div class="sh" role="dialog" aria-modal="true" aria-label="${esc(name)}">
<div class="hd"><b><span data-own>${esc(name)}</span>${state ? `<small>${esc(state)}</small>` : ""}</b><button class="x" data-x aria-label="Закрыть"><ha-icon icon="mdi:close"></ha-icon></button></div>
${body}
${fav ? `<button class="b fav" data-fav aria-pressed="${favHas(id)}"><ha-icon icon="${favHas(id) ? "mdi:star" : "mdi:star-outline"}"></ha-icon><span>${favHas(id) ? "Убрать из избранного" : "В избранное"}</span></button>` : ""}
<button class="b lnk" data-more>Подробнее</button></div>`;
      if (LANG !== "ru") translate(sr);
      bind();
    };
    const bind = () => {
      const h = hass();
      sr.querySelector("[data-x]").onclick = close;
      sr.querySelector("[data-more]").onclick = () => {
        close();
        moreInfo(card, id);
      };
      sr.querySelector("[data-tg]")?.addEventListener("click", () => {
        haptic(card);
        call(h, id.startsWith("light.") ? "light" : "homeassistant", "toggle", { entity_id: id });
      });
      sr.querySelector("[data-run]")?.addEventListener("click", (ev) => {
        haptic(card);
        const s0 = st(h, id);
        sendAck(card, ev.currentTarget, id.split(".")[0], "turn_on", { entity_id: id }, () => okToast(LANG === "ru" ? `Сценарий «${nameOf(s0, id)}» включён` : `Scene «${nameOf(s0, id)}» is on`));
      });
      sr.querySelectorAll("[data-cv]").forEach((b) => b.addEventListener("click", () => {
        haptic(card);
        sendAck(card, b, "cover", b.dataset.cv, { entity_id: id });
      }));
      const fav = sr.querySelector("[data-fav]");
      if (fav)
        fav.onclick = async () => {
          fav.disabled = true;
          try {
            await HOUSE.ready(hass());
            await favToggle(hass(), id);
            haptic(card, "success");
          } catch (e) {
            card.dispatchEvent(new CustomEvent("hass-notification", { bubbles: true, composed: true, detail: { message: tr("Не сохранилось — проверьте связь с домом") } }));
          }
          draw();
        };
      const sl = sr.querySelector("[data-sl]");
      if (!sl) return;
      const cover = id.startsWith("cover.");
      const at = (ev) => {
        const r = sl.getBoundingClientRect();
        return Math.round(Math.min(100, Math.max(0, ((r.bottom - ev.clientY) / r.height) * 100)));
      };
      const show = (v) => {
        sl.querySelector("i").style.height = `${v}%`;
        sl.querySelector("span").textContent = `${v} %`;
        sl.setAttribute("aria-valuenow", String(v));
      };
      sl.addEventListener("pointerdown", (ev) => {
        drag = { v: at(ev) };
        sl.setPointerCapture(ev.pointerId);
        show(drag.v);
      });
      sl.addEventListener("pointermove", (ev) => {
        if (!drag) return;
        drag.v = at(ev);
        show(drag.v);
      });
      const end = (ev) => {
        if (!drag) return;
        const v = drag.v;
        drag = null;
        if (ev.type !== "pointerup") return draw();
        haptic(card);
        if (cover) call(hass(), "cover", "set_cover_position", { entity_id: id, position: v });
        else call(hass(), "light", v ? "turn_on" : "turn_off", v ? { entity_id: id, brightness_pct: Math.max(1, v) } : { entity_id: id });
      };
      sl.addEventListener("pointerup", end);
      sl.addEventListener("pointercancel", end);
    };
    // Live: the state follows HA while the sheet is open (never under a finger on the slider).
    let last = st(h0, id);
    const live = setInterval(() => {
      const s = st(hass(), id);
      if (s !== last && !drag && !sr.activeElement?.matches?.("[data-fav]")) {
        last = s;
        draw();
      }
    }, 400);
    draw();
    document.body.appendChild(host);
    sr.querySelector("[data-x]")?.focus({ preventScroll: true });
  };
  Object.assign(EN, { "Этот светильник только включается и выключается": "This light only switches on and off", "В избранное": "Add to favourites", "Убрать из избранного": "Remove from favourites", "Подробнее": "More details", "Включить": "Turn on", "Выключить": "Turn off", "сценарий": "scene", "Положение": "Position", "Не сохранилось — проверьте связь с домом": "Not saved — check the link to the house", "Избранное": "Favourites", "Моя комната": "My room",
    "Всё хорошо": "All is well", "тревога останется, пока причина не устранена; видно, кто принял": "the alarm stays until the cause is gone; everyone sees who accepted",
    "Порядок для всего дома меняют взрослые — у вас детский профиль": "The order for the whole home is changed by the adults — you have a child profile",
    "Позвонить сервису": "Call service", "нет связи": "no signal", "вкл": "on", "выкл": "off" });
  EN_RX.unshift(
    [/^Принять все · (\d+)$/, "Accept all · $1"], [/^Принять все неисправности · (\d+)$/, "Accept all faults · $1"], [/^Принято:$/, "Accept:"], [/^Перекройте воду — кнопка «(.+)» ниже \(или кран на вводе у счётчика\)\. Потом проверьте место протечки\.( Уведомление отправлено на телефоны.*)?$/, (m, v, sent) => `Shut the water off — the «${v}» button below (or the main valve by the meter). Then check where it leaks.${sent ? " Sent to the phones (whether it arrived isn't known until someone taps «Accept»); «Accept» stops the repeats." : ""}`], [/^Авария: /, "Alarm: "], [/^Протечка: /, "Leak: "], [/^Первая: /, "First: "],
    [/^Приняты · (\d+) авари[яий]+$/, (m, a) => `Accepted · ${a} alarm${a === "1" ? "" : "s"}`],
    [/^Нет связи · (\d+) датчик(?:а|ов)?$/, (m, a) => `No signal · ${a} sensor${a === "1" ? "" : "s"}`],
  );

  // ================================================================================================
  // More kinds (owner 2026-10-25: «покажи как может выглядеть любая из карточек»): weather, air, media,
  // graphs, automations, engineering status, security, cameras, any other device.
  const hhmm = (d) => `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  const ago = (iso) => {
    if (!iso) return "";
    const d = new Date(iso);
    const min = Math.round((Date.now() - d.getTime()) / 60000);
    if (min < 1) return "только что";
    if (min < 60) return `${min} мин назад`;
    if (min < 24 * 60 && d.getDate() === new Date().getDate()) return `сегодня в ${hhmm(d)}`;
    return `${d.getDate()}.${String(d.getMonth() + 1).padStart(2, "0")} в ${hhmm(d)}`;
  };
  const unitOf = (s) => (s && s.attributes.unit_of_measurement) || "";

  // --- hauflo-weather: now and the next days -----------------------------------------------------
  const WEATHER = {
    "clear-night": ["Ясно", "hauflo:moon"], cloudy: ["Облачно", "mdi:weather-cloudy"], exceptional: ["Необычно", "hauflo:warning"],
    fog: ["Туман", "mdi:weather-fog"], hail: ["Град", "mdi:weather-hail"], lightning: ["Гроза", "mdi:weather-lightning"],
    "lightning-rainy": ["Гроза, дождь", "mdi:weather-lightning-rainy"], partlycloudy: ["Переменная облачность", "mdi:weather-partly-cloudy"],
    pouring: ["Ливень", "mdi:weather-pouring"], rainy: ["Дождь", "mdi:weather-rainy"], snowy: ["Снег", "hauflo:snow"],
    "snowy-rainy": ["Мокрый снег", "mdi:weather-snowy-rainy"], sunny: ["Солнечно", "hauflo:sun"], windy: ["Ветер", "mdi:weather-windy"],
    "windy-variant": ["Ветер, облака", "mdi:weather-windy-variant"],
  };
  const DAYS = ["Вс", "Пн", "Вт", "Ср", "Чт", "Пт", "Сб"];
  class Weather extends EHCard {
    watched() {
      return [this._config.entity];
    }
    connectedCallback() {
      this._sub();
    }
    disconnectedCallback() {
      this._unsub?.then((u) => u()).catch(() => {});
      this._unsub = undefined;
    }
    set hass(hass) {
      this._hass = hass;
      this._sub();
      this._update();
    }
    _sub() {
      if (this._unsub || !this._hass || !this._config || !this.isConnected) return;
      this._unsub = this._hass.connection
        .subscribeMessage((m) => {
          this._fc = m.forecast || [];
          this._sig = "";
          this._update();
        }, { type: "weather/subscribe_forecast", forecast_type: this._config.forecast || "daily", entity_id: this._config.entity })
        .catch(() => undefined);
    }
    css() {
      return `.w{padding:18px 20px}.top{display:flex;justify-content:space-between;align-items:flex-start}
.t{font-size:64px;line-height:.95}.t sup{font:300 20px Onest,sans-serif;vertical-align:top;position:relative;top:8px;color:var(--ink2)}
.ic{--mdc-icon-size:46px;color:var(--amberT);filter:drop-shadow(0 0 16px var(--amberGlow))}.cond{font:400 15px Onest,sans-serif;margin-top:6px}
.meta{display:flex;gap:14px;margin-top:8px;font:400 13px Onest,sans-serif;color:var(--ink2);flex-wrap:wrap}.meta span{display:inline-flex;align-items:center;gap:4px}.meta ha-icon{--mdc-icon-size:14px}
.fc{display:grid;grid-template-columns:repeat(var(--n),1fr);gap:4px;margin-top:16px;padding-top:14px;border-top:1px solid var(--hair)}
.fc div{display:flex;flex-direction:column;align-items:center;gap:6px;font:400 12px Onest,sans-serif}.fc .d{color:var(--ink2)}.fc ha-icon{--mdc-icon-size:22px;color:var(--ink2)}
.fc b{font:500 13px Onest,sans-serif}.fc small{color:var(--ink3)}
.w .glow{background:radial-gradient(50% 60% at 85% 10%,var(--amberGlow),transparent 70%);opacity:.5}`;
    }
    render() {
      const s = st(this._hass, this._config.entity);
      if (!s) return `<div class="glass w"><span class="caps">Нет погоды</span></div>`;
      const a = s.attributes;
      const [word, icon] = nd(s) ? ["Нет данных", "mdi:weather-cloudy-alert"] : WEATHER[s.state] || [s.state, "mdi:weather-partly-cloudy"];
      const t = sane(num(a.temperature), -70, 65);
      const fc = (this._fc || []).slice(0, this._config.days || 5);
      return `<div class="glass w"><div class="glow"></div><div style="position:relative">
<div class="top"><div><span class="caps">${esc(this._config.name || "Погода")}</span><div class="t thin">${t === undefined ? "—" : Math.round(t)}<sup>°C</sup></div><div class="cond">${esc(word)}${num(a.apparent_temperature) !== undefined ? ` · ощущается как ${Math.round(a.apparent_temperature)}°` : ""}</div></div><ha-icon class="ic" icon="${icon}"></ha-icon></div>
<div class="meta">${num(a.humidity) !== undefined ? `<span><ha-icon icon="hauflo:humidity"></ha-icon>${fmt(a.humidity)} %</span>` : ""}${num(a.wind_speed) !== undefined ? `<span><ha-icon icon="mdi:weather-windy"></ha-icon>${fmt(a.wind_speed)} ${esc(ru(a.wind_speed_unit || "km/h"))}</span>` : ""}${["hPa", "mbar", undefined].includes(a.pressure_unit) && sane(num(a.pressure), 870, 1085) !== undefined ? `<span><ha-icon icon="mdi:gauge"></ha-icon>${fmt(a.pressure)} ${esc(ru(a.pressure_unit || "hPa"))}</span>` : a.pressure_unit === "mmHg" && sane(num(a.pressure), 650, 815) !== undefined ? `<span><ha-icon icon="mdi:gauge"></ha-icon>${fmt(a.pressure)} мм рт. ст.</span>` : ""}</div>
${fc.length ? `<div class="fc" style="--n:${fc.length}">${fc
        .map((f) => {
          const d = new Date(f.datetime);
          // A day's forecast is a day: «clear-night» from the provider reads as sunny.
          const [, fi] = WEATHER[f.condition === "clear-night" && (this._config.forecast || "daily") === "daily" ? "sunny" : f.condition] || ["", "mdi:weather-partly-cloudy"];
          return `<div><span class="d">${DAYS[d.getDay()]}</span><ha-icon icon="${fi}"></ha-icon><b>${num(f.temperature) === undefined ? "—" : Math.round(f.temperature)}°</b><small>${num(f.templow) === undefined ? "" : `${Math.round(f.templow)}°`}</small></div>`;
        })
        .join("")}</div>` : ""}</div></div>`;
    }
    bind() {
      this.shadowRoot.querySelector(".w")?.addEventListener("click", () => moreInfo(this, this._config.entity));
    }
    getGridOptions() {
      return { columns: 12, rows: "auto" };
    }
  }

  // --- hauflo-air: air quality — PM2.5 large on its scale, the rest as values ----------------------
  // PM2.5 by the US EPA scale (2024): what the Dubai/Moscow apps show, so the client reads it the same way.
  // Four steps in our own palette (designer review 25.10: the rainbow was the least premium thing on the screen).
  const PM25 = [[9, "Хорошо", "var(--mint)", 0], [35.4, "Умеренно", "var(--amber)", 1], [55.4, "Вредно для чувствительных", "var(--amber)", 2], [125.4, "Вредно", "var(--alert)", 3], [Infinity, "Опасно", "var(--alert)", 3]];
  class Air extends EHCard {
    watched() {
      const c = this._config;
      return [c.entity, c.pm25, c.pm10, c.co2, c.voc, c.humidity, c.temperature].filter(Boolean);
    }
    vals() {
      const c = this._config;
      const a = st(this._hass, c.entity)?.attributes || {};
      const v = (id, attr) => num(st(this._hass, id)?.state) ?? num(a[attr]);
      return { pm25: sane(v(c.pm25, "particulate_matter_2_5"), 0, 1000), pm10: sane(v(c.pm10, "particulate_matter_10"), 0, 2000), co2: sane(v(c.co2, "carbon_dioxide"), 300, 5000), voc: sane(v(c.voc, "volatile_organic_compounds"), 0, 60000), hum: sane(v(c.humidity, "humidity"), 0, 100), t: sane(v(c.temperature, "temperature"), -40, 60) };
    }
    css() {
      return `.a{padding:18px 20px}.big{display:flex;align-items:flex-end;gap:10px;margin-top:6px}.t{font-size:56px;line-height:.95}
.u{font:400 13px Onest,sans-serif;color:var(--ink2);margin-bottom:8px}.lv{font:500 15px Onest,sans-serif;margin-top:4px}
.sc{position:relative;display:grid;grid-template-columns:repeat(4,1fr);gap:4px;margin:16px 0 6px}.sc b{height:6px;border-radius:3px;opacity:.55}
.sc b:nth-child(1){background:var(--mint)}.sc b:nth-child(2){background:var(--amber);opacity:.4}.sc b:nth-child(3){background:var(--amber)}.sc b:nth-child(4){background:var(--alert)}
.sc b.on{opacity:1;box-shadow:0 0 12px currentColor}
.sc i{position:absolute;top:-5px;width:16px;height:16px;border-radius:8px;background:var(--primary-background-color,#fff);box-shadow:0 0 0 2px var(--ink),0 2px 8px rgba(0,0,0,.2);transform:translateX(-50%)}
.vals{display:grid;grid-template-columns:repeat(auto-fit,minmax(76px,1fr));gap:8px;margin-top:14px}
.vals div{padding:10px 12px;border-radius:14px;background:var(--track)}.vals b{display:block;font:400 20px Onest,sans-serif}.vals span{font:400 13px Onest,sans-serif;color:var(--ink2)}`;
    }
    render() {
      const v = this.vals();
      const lvl = v.pm25 === undefined ? undefined : PM25.find(([max]) => v.pm25 <= max);
      const step = lvl ? lvl[3] : -1;
      // The marker inside its step: 0–9 · 9–35 · 35–55 · 55+.
      const edges = [0, 9, 35.4, 55.4, 150];
      const k = Math.max(0, step);
      const pos = v.pm25 === undefined ? 0 : Math.min(100, ((k + Math.min(1, (v.pm25 - edges[k]) / (edges[k + 1] - edges[k]))) / 4) * 100);
      const co2Word = v.co2 === undefined ? "" : v.co2 < 800 ? "свежо" : v.co2 < 1200 ? "проветрить" : "душно";
      const cell = (val, label, unit, d = 0) => (val === undefined ? "" : `<div><b>${fmt(val, d)}${unit ? ` <small style="font-size:11px;color:var(--ink2)">${unit}</small>` : ""}</b><span>${label}</span></div>`);
      return `<div class="glass a" style="--c:${lvl ? lvl[2] : "var(--ink3)"}"><span class="caps">${esc(this._config.name || "Качество воздуха")}</span>
<div class="big"><div class="t thin">${v.pm25 === undefined ? "—" : fmt(v.pm25)}</div><div class="u">PM2.5 · мкг/м³</div></div>
<div class="lv" style="color:${lvl ? lvl[2] : "var(--ink2)"}">${lvl ? lvl[1] : "нет данных о частицах"}</div>
${v.pm25 === undefined ? "" : `<div class="sc">${[0, 1, 2, 3].map((k) => `<b class="${step === k ? "on" : ""}"></b>`).join("")}<i style="left:${pos}%"></i></div>`}
<div class="vals">${cell(v.pm10, "PM10", "мкг/м³")}${cell(v.co2, `CO₂ · ${co2Word}`, "ppm")}${cell(v.voc, "Летучие в-ва", "ppb")}${cell(v.hum, "Влажность", "%")}${cell(v.t, "Температура", "°", 1)}</div></div>`;
    }
    getGridOptions() {
      return { columns: 12, rows: "auto" };
    }
  }

  // --- hauflo-media: what plays, its cover, play/pause, volume by a swipe --------------------------
  class Media extends EHCard {
    watched() {
      return [this._config.entity];
    }
    css() {
      return `.p{padding:16px;min-height:150px}.bg{position:absolute;inset:0;background-size:cover;background-position:center;filter:blur(28px) saturate(1.3);opacity:.45;transform:scale(1.2)}
.row{display:flex;gap:14px;align-items:center;position:relative}.art{width:76px;height:76px;border-radius:18px;background:var(--track) center/cover;flex:0 0 auto;display:grid;place-items:center;color:var(--ink3);box-shadow:0 8px 24px rgba(0,0,0,.25)}
.art ha-icon{--mdc-icon-size:30px}.tx{min-width:0;flex:1}.tt{font:500 15px Onest,sans-serif;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.ar{font:400 13px Onest,sans-serif;color:var(--ink2);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-top:2px}
.ctl{display:flex;align-items:center;justify-content:center;gap:14px;margin-top:14px;position:relative}
.ctl button{width:46px;height:46px;border-radius:23px;display:grid;place-items:center;color:var(--ink)}.ctl button ha-icon{--mdc-icon-size:26px}
.ctl button.pp{width:58px;height:58px;border-radius:29px;background:var(--ink);color:var(--eh-glass-solid,#fff)}.ctl button.pp ha-icon{--mdc-icon-size:30px;color:var(--primary-background-color,#fff)}
.vol{margin-top:12px;height:44px;border-radius:12px;background:var(--track);position:relative;overflow:hidden;touch-action:pan-y;cursor:ew-resize}
.vol i{position:absolute;left:0;top:0;bottom:0;background:color-mix(in srgb,var(--ink) 18%,transparent)}.vol span{position:absolute;left:14px;top:13px;font:500 12px Onest,sans-serif;display:flex;gap:6px;align-items:center}.vol ha-icon{--mdc-icon-size:15px}
.off{display:flex;align-items:center;justify-content:space-between;position:relative}.pw{width:46px;height:46px;border-radius:23px;display:grid;place-items:center;box-shadow:inset 0 0 0 1px var(--line)}`;
    }
    render() {
      const s = st(this._hass, this._config.entity);
      if (!s) return `<div class="glass p"><span class="caps">Нет плеера</span></div>`;
      const a = s.attributes;
      const name = this._config.name || nameOf(s, s.entity_id);
      if (["off", "unavailable", "standby"].includes(s.state))
        return `<div class="glass p" style="min-height:0"><div class="off"><div><span class="caps">${esc(name)}</span><div class="ar">${s.state === "unavailable" ? "нет связи" : "выключен"}</div></div><button class="pw" data-s="turn_on" aria-label="Включить"><ha-icon icon="hauflo:power"></ha-icon></button></div></div>`;
      const pic = a.entity_picture;
      const vol = num(a.volume_level);
      const playing = s.state === "playing";
      return `<div class="glass p">${pic ? `<div class="bg" style="background-image:url('${esc(pic)}')"></div>` : ""}
<div class="row"><div class="art" ${pic ? `style="background-image:url('${esc(pic)}')"` : ""}>${pic ? "" : `<ha-icon icon="hauflo:speaker"></ha-icon>`}</div>
<div class="tx"><span class="caps">${esc(name)}${a.source ? ` · ${esc(a.source)}` : ""}</span><div class="tt">${esc(a.media_title || (playing ? "Играет" : "Пауза"))}</div><div class="ar">${esc(a.media_artist || a.media_album_name || a.app_name || "")}</div></div></div>
<div class="ctl"><button data-s="media_previous_track" aria-label="Назад"><ha-icon icon="mdi:skip-previous"></ha-icon></button><button class="pp" data-s="media_play_pause" aria-label="${playing ? "Пауза" : "Играть"}"><ha-icon icon="${playing ? "hauflo:pause" : "hauflo:play"}"></ha-icon></button><button data-s="media_next_track" aria-label="Вперёд"><ha-icon icon="mdi:skip-next"></ha-icon></button></div>
${vol === undefined ? "" : `<div class="vol"><i style="width:${Math.round(vol * 100)}%"></i><span><ha-icon icon="hauflo:speaker"></ha-icon>${Math.round(vol * 100)} %</span></div>`}</div>`;
    }
    bind() {
      const id = this._config.entity;
      this.shadowRoot.querySelectorAll("button[data-s]").forEach((b) =>
        b.addEventListener("click", () => {
          haptic(this);
          call(this._hass, "media_player", b.dataset.s, { entity_id: id });
        }),
      );
      const vol = this.shadowRoot.querySelector(".vol");
      if (!vol) return;
      const set = (ev, send) => {
        const r = vol.getBoundingClientRect();
        const v = Math.min(1, Math.max(0, (ev.clientX - r.left) / r.width));
        vol.querySelector("i").style.width = `${Math.round(v * 100)}%`;
        vol.querySelector("span").lastChild.textContent = `${Math.round(v * 100)} %`;
        if (send) call(this._hass, "media_player", "volume_set", { entity_id: id, volume_level: Math.round(v * 100) / 100 });
      };
      // A sideways swipe sets the volume; a tap or a vertical swipe (the page scrolls) does not.
      hdrag(vol, {
        start: () => (this._busy = true),
        move: (ev) => set(ev),
        end: (ev, ok) => {
          if (ok) set(ev, true);
          setTimeout(() => {
            this._busy = false;
            this._sig = "";
            this._update();
          }, 400);
        },
      });
    }
    getGridOptions() {
      return { columns: 12, rows: "auto" };
    }
  }

  // --- hauflo-graph: a value over the last hours (HA's history), as a soft line ------------------
  class Graph extends EHCard {
    watched() {
      return this.ids();
    }
    ids() {
      return (this._config.entities || []).map((e) => (typeof e === "string" ? e : e.entity));
    }
    connectedCallback() {
      this._load();
      this._timer = setInterval(() => this._load(true), 5 * 60000);
    }
    disconnectedCallback() {
      clearInterval(this._timer);
    }
    async _load(force) {
      if (!this._hass || !this._config || (this._hist && !force) || this._loading) return;
      this._loading = true;
      const hours = this._config.hours || 24;
      this._hist = {};
      for (const id of this.ids()) this._hist[id] = await series(this._hass, id, hours);
      this._loading = false;
      this._sig = "";
      this._update();
    }
    set hass(hass) {
      this._hass = hass;
      this._load();
      this._update();
    }
    css() {
      return `.g{padding:16px 18px}.it+.it{margin-top:14px;padding-top:12px;border-top:1px solid var(--hair)}.hd{display:flex;justify-content:space-between;align-items:baseline}
.v{font:300 30px Onest,sans-serif;letter-spacing:-.03em}.v small{font:400 13px Onest,sans-serif;color:var(--ink2);margin-left:3px}.mm{font:400 13px Onest,sans-serif;color:var(--ink2)}
svg{display:block;width:100%;height:70px;margin-top:6px;overflow:visible}`;
    }
    render() {
      const items = (this._config.entities || []).map((e) => (typeof e === "string" ? { entity: e } : e));
      return `<div class="glass g"><span class="caps">${esc(this._config.title || `За ${this._config.hours || 24} ч`)}</span>${items
        .map((e, k) => {
          const s = st(this._hass, e.entity);
          const raw = this._hist && this._hist[e.entity];
          const dc = s?.attributes.device_class;
          const range = { carbon_dioxide: [300, 5000], humidity: [0, 100], temperature: [-50, 70], pm25: [0, 1000], atmospheric_pressure: [870, 1085] }[dc];
          const data = raw && range ? { ...raw, pts: raw.pts.filter(([, v]) => v >= range[0] && v <= range[1]) } : raw;
          const whole = ["ppm", "%", "ppb", "W", "Вт"].includes(unitOf(s));
          const W = 300;
          const H = 64;
          const sp = data ? spark(data, W, H) : null;
          const lo = sp ? sp.lo : 0;
          const hi = sp ? sp.hi : 0;
          const color = e.color || ["var(--amber)", "var(--ice)", "var(--mint)", "var(--alert)"][k % 4];
          const gid = `g${k}${Math.random().toString(36).slice(2, 6)}`;
          return `<div class="it"><div class="hd"><span style="font:500 13px Onest,sans-serif">${esc(e.name || nameOf(s, e.entity))}</span><span class="v">${s ? fmt(num(s.state), whole || !(num(s.state) % 1) ? 0 : 1) : "—"}<small>${esc(unitOf(s))}</small></span></div>
${sp ? `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none"><defs><linearGradient id="${gid}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${color}" stop-opacity=".35"/><stop offset="1" stop-color="${color}" stop-opacity="0"/></linearGradient></defs><path d="${sp.area}" fill="url(#${gid})"/><path d="${sp.line}" fill="none" stroke="${color}" stroke-width="2" vector-effect="non-scaling-stroke" stroke-linejoin="round"/></svg><div class="mm">мин ${fmt(lo, whole || !(lo % 1) ? 0 : 1)} · макс ${fmt(hi, whole || !(hi % 1) ? 0 : 1)}</div>` : `<div class="mm" style="margin-top:8px">${this._hist ? "истории пока нет" : "загрузка…"}</div>`}</div>`;
        })
        .join("")}</div>`;
    }
    bind() {
      this.shadowRoot.querySelectorAll(".it").forEach((el, i) => el.addEventListener("click", () => moreInfo(this, this.ids()[i])));
    }
    getGridOptions() {
      return { columns: 12, rows: "auto" };
    }
  }

  // --- hauflo-auto: automations and scripts — on/off, run, when it last worked --------------------
  class Auto extends EHCard {
    watched() {
      return this._config.entities || [];
    }
    css() {
      return `.a{padding:16px}.r{display:flex;align-items:center;gap:12px;padding:10px 4px;min-height:60px}.r+.r{border-top:1px solid var(--hair)}
.ic{width:36px;height:36px;border-radius:12px;display:grid;place-items:center;background:var(--track);color:var(--ink2);flex:0 0 auto}.on .ic{background:var(--track);color:var(--ink)}
.tx{flex:1;min-width:0}.nm{font:500 13px Onest,sans-serif;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.lt{font:400 13px Onest,sans-serif;color:var(--ink2);margin-top:2px}
.run{height:44px;padding:0 16px;border-radius:14px;font:500 12px Onest,sans-serif;box-shadow:inset 0 0 0 1px var(--line)}.run.go{background:color-mix(in srgb,var(--mint) 25%,transparent)}
.sw{width:50px;height:30px;border-radius:15px;background:var(--track);position:relative;flex:0 0 auto}.sw::before{content:"";position:absolute;inset:-8px -6px}.sw i{position:absolute;top:3px;left:3px;width:24px;height:24px;border-radius:12px;background:var(--ink3);transition:all .2s}
.on .sw{background:var(--ink)}.on .sw i{left:23px;background:var(--primary-background-color,#fff)}`;
    }
    render() {
      const rows = (this._config.entities || [])
        .map((id) => ({ id, s: st(this._hass, id) }))
        .filter((r) => r.s)
        .map(({ id, s }) => {
          const isScript = id.startsWith("script.");
          const on = isScript ? s.state === "on" : s.state === "on";
          const last = s.attributes.last_triggered;
          const lt = isScript ? (s.state === "on" ? "выполняется…" : last ? `запускался ${ago(last)}` : "ещё не запускался") : s.state === "off" ? "выключена" : last ? `сработала ${ago(last)}` : "ещё не срабатывала";
          return `<div class="r ${!isScript && on ? "on" : ""}" data-e="${esc(id)}"><span class="ic"><ha-icon icon="${isScript ? "hauflo:play" : "hauflo:clock"}"></ha-icon></span><div class="tx"><div class="nm">${esc(nameOf(s, id))}</div><div class="lt">${esc(lt)}</div></div>
${isScript ? `<button class="run" data-run="${esc(id)}">Запустить</button>` : `<button class="sw" data-tog="${esc(id)}" aria-label="Вкл/выкл"><i></i></button>`}</div>`;
        });
      return `<div class="glass a"><span class="caps">${esc(this._config.title || "Автоматизации")}</span>${rows.join("") || `<div class="lt" style="margin-top:8px">пусто</div>`}</div>`;
    }
    bind() {
      this.shadowRoot.querySelectorAll("[data-tog]").forEach((b) => b.addEventListener("click", () => (haptic(this), call(this._hass, "automation", "toggle", { entity_id: b.dataset.tog }))));
      this.shadowRoot.querySelectorAll("[data-run]").forEach((b) =>
        b.addEventListener("click", () => {
          haptic(this);
          b.classList.add("go");
          setTimeout(() => b.classList.remove("go"), 1200);
          call(this._hass, "script", "turn_on", { entity_id: b.dataset.run });
        }),
      );
      this.shadowRoot.querySelectorAll(".r .tx").forEach((t) => t.addEventListener("click", () => moreInfo(this, t.closest(".r").dataset.e)));
    }
    getGridOptions() {
      return { columns: 12, rows: "auto" };
    }
  }

  // --- hauflo-status: dispatching — engineering systems at a glance: works / stopped / fault / no link
  class Status extends EHCard {
    watched() {
      return (this._config.entities || []).map((e) => (typeof e === "string" ? e : e.entity));
    }
    word(s) {
      const d = s.entity_id.split(".")[0];
      const dc = s.attributes.device_class;
      if (s.state === "unavailable") return ["нет связи", "nolink"];
      if (s.state === "unknown") return ["нет данных", "nolink"];
      if (d === "binary_sensor") {
        if (dc === "battery") return s.state === "on" ? ["батарея садится", "warn"] : ["батарея в норме", "ok"];
        // «Ошибка тёплого пола» without a device class is a fault, not «вкл» (persona round 3: «всё в норме» during it).
        const faulty = ["problem", "smoke", "gas", "moisture", "safety", "carbon_monoxide", "heat", "tamper"].includes(dc) || (!dc && /ошибк|авари|неисправ|протечк|утечк|перегрев|fault|error|alarm|leak|failure/i.test(s.attributes.friendly_name || s.entity_id));
        if (faulty) return s.state === "on" ? ["авария", "fault"] : ["в норме", "ok"];
        if (dc === "connectivity") return s.state === "on" ? ["на связи", "ok"] : ["нет связи", "fault"];
        // Windows and doors say «открыто/закрыто», not «вкл» with a green dot (integrator run 16).
        if (OPENING.includes(dc)) return s.state === "on" ? ["открыто", "warn"] : ["закрыто", "ok"];
        return s.state === "on" ? ["вкл", "run"] : ["выкл", "stop"];
      }
      if (d === "binary_sensor" && dc === "battery") return s.state === "on" ? ["батарея садится", "warn"] : ["батарея в норме", "ok"];
      if (d === "sensor" && dc === "battery" && num(s.state) !== undefined) return [`батарея ${fmt(num(s.state))} %`, num(s.state) < 20 ? "warn" : "ok"];
      // A text / date sensor of any integration: its own state, not a dash.
      if (["sensor", "number", "input_number"].includes(d)) return [num(s.state) === undefined ? String(s.state) : `${fmt(num(s.state), num(s.state) % 1 ? 1 : 0)} ${unitOf(s)}`.trim(), "val"];
      if (d === "climate") {
        // What it does («греет · 23,4°»), not its mode (owner 28.10, Д16); the mode only when nothing says what it does.
        const k = climateNow(null, s);
        const cur = num(s.attributes.current_temperature) !== undefined ? ` · ${fmt(s.attributes.current_temperature, 1)}°` : "";
        return [k === "off" ? "Выключено" : k === "mode" ? `${modeSmall(s.state) || HVAC[s.state] || s.state}${cur}` : `${climateLine(k, num(s.attributes.temperature)) || s.state}${cur}`, k === "off" ? "stop" : acting(k) ? "run" : "val"];
      }
      if (d === "water_heater") return [`${s.state === "off" ? "выключен" : "работает"}${num(s.attributes.current_temperature) !== undefined ? ` · ${fmt(s.attributes.current_temperature)}°` : ""}`, s.state === "off" ? "stop" : "run"];
      if (d === "valve") return [s.state === "open" ? "открыт" : s.state === "closed" ? "закрыт" : s.state, s.state === "open" ? "run" : "stop"];
      return isOn(s) ? ["работает", "run"] : ["остановлен", "stop"];
    }
    css() {
      return `.d{padding:16px}.hd{display:flex;justify-content:space-between;align-items:center;margin-bottom:6px}.sum{font:500 12px Onest,sans-serif;display:flex;align-items:center;gap:6px}
.sum i,.r i{width:8px;height:8px;border-radius:4px;flex:0 0 auto}.ok i,.run i,i.ok{background:var(--mint);box-shadow:0 0 8px var(--mint)}.stop i{background:var(--ink3)}.fault i,i.fault{background:var(--alert);box-shadow:0 0 10px var(--alert)}.nolink i{background:transparent;box-shadow:inset 0 0 0 1.5px var(--ink3)}.val i{background:var(--ice)}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px;margin-top:8px}
.r{display:flex;align-items:center;gap:10px;padding:10px 12px;min-height:56px;border-radius:18px;box-shadow:inset 0 0 0 1px var(--hair);cursor:pointer;min-width:0}.r .tx{min-width:0}
.nm{font:500 13px/1.25 Onest,sans-serif;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;overflow-wrap:break-word;hyphens:auto}
.fault,.warn{box-shadow:none}.w{font:400 13px Onest,sans-serif;color:var(--ink2)}.rm{font:400 11px/1.3 Onest,sans-serif;color:var(--ink3);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.fault{background:color-mix(in srgb,var(--alert) 14%,transparent)}.fault .w{color:var(--alertT);font-weight:600}
.warn i,i.warn{background:var(--amber);box-shadow:0 0 8px var(--amberGlow)}.warn{background:color-mix(in srgb,var(--amber) 14%,transparent)}.warn .w{color:var(--amberT);font-weight:600}`;
    }
    render() {
      const rows = (this._config.entities || []).map((e) => (typeof e === "string" ? { entity: e } : e)).map((e) => ({ ...e, s: st(this._hass, e.entity) })).filter((e) => e.s);
      this.hideCard(!rows.length);
      if (!rows.length) return "";
      const faults = rows.filter((e) => this.word(e.s)[1] === "fault").length;
      const nolink = rows.filter((e) => this.word(e.s)[1] === "nolink").length;
      const warns = rows.filter((e) => this.word(e.s)[1] === "warn").length;
      return `<div class="glass d"><div class="hd"><span class="caps">${blockTitle(this, "Инженерные системы")}</span><span class="sum"><i class="${faults ? "fault" : warns || nolink ? "warn" : "ok"}"></i>${faults ? `аварий: ${faults}` : nolink ? `без связи: ${nolink}` : warns ? `внимание: ${warns}` : "всё в норме"}</span></div>
<div class="grid">${rows
        .map((e) => {
          const [w, k] = this.word(e.s);
          return `<div class="r ${k}" data-e="${esc(e.entity)}"><i></i><div class="tx"><div class="nm">${esc(e.name || nameOf(e.s, e.entity))}</div>${roomLine(this._hass, e.entity, e.name || nameOf(e.s, e.entity))}<div class="w">${esc(w)}</div></div></div>`;
        })
        .join("")}</div></div>`;
    }
    bind() {
      this.shadowRoot.querySelectorAll(".r").forEach((r) => r.addEventListener("click", () => moreInfo(this, r.dataset.e)));
    }
    getGridOptions() {
      return { columns: 12, rows: "auto" };
    }
  }

  // --- hauflo-area: whatever HA puts in a room (owner 28.10: «Котельная» made for the ZONT boiler controller — another
  // integration's entities in that HA area), drawn in Hauflo's own look by kind: thermostats and boilers with ± like our
  // climate rows, readings as values, faults as chips, switches as toggles, numbers and lists as compact controls.
  // The cloud sends only the area id, never foreign entity ids: what is in the room is read here, from HA's area registry
  // (the entity's own area, or its device's) — the family's choice, not the cloud's. So a member can be controlled from
  // the card; what can open the house (locks, gates, garage doors, valves, alarm panels) only opens HA's own dialog, and a
  // button another integration made (ZONT «Аварийный») presses on a second tap — unless Hauflo's own items on this
  // dashboard name it (`ownedOnDashboard`, security re-review 28.10).
  const AREA_ICON = { climate: "mdi:thermostat", water_heater: "mdi:water-boiler", sensor: "mdi:gauge", number: "mdi:tune-variant", input_number: "mdi:tune-variant", select: "mdi:format-list-bulleted", input_select: "mdi:format-list-bulleted", switch: "mdi:toggle-switch-outline", input_boolean: "mdi:toggle-switch-outline", light: "mdi:lightbulb-outline", fan: "mdi:fan", binary_sensor: "mdi:checkbox-blank-circle-outline", valve: "mdi:valve", cover: "mdi:window-shutter", lock: "mdi:lock-outline", camera: "mdi:cctv", media_player: "mdi:speaker", button: "mdi:gesture-tap-button", humidifier: "mdi:air-humidifier", vacuum: "mdi:robot-vacuum" };
  const SENSOR_ICON = { temperature: "mdi:thermometer", humidity: "mdi:water-percent", pressure: "mdi:gauge", power: "mdi:flash", energy: "mdi:lightning-bolt", voltage: "mdi:sine-wave", current: "mdi:current-ac", carbon_monoxide: "mdi:molecule-co", carbon_dioxide: "mdi:molecule-co2", gas: "mdi:meter-gas", water: "mdi:water", battery: "mdi:battery", illuminance: "mdi:brightness-5", motion: "mdi:motion-sensor", occupancy: "mdi:home-account", door: "mdi:door", window: "mdi:window-closed-variant", moisture: "mdi:water-alert", smoke: "mdi:smoke-detector", connectivity: "mdi:lan-connect" };
  // Never one tap from this card: what opens the house or the water.
  const AREA_GUARDED = (s) => {
    const d = s.entity_id.split(".")[0];
    return ["lock", "valve", "alarm_control_panel", "siren", "camera"].includes(d) || (d === "cover" && ["gate", "garage", "door"].includes(s.attributes.device_class));
  };
  // A text sensor that reports a fault («Ошибок нет» / «E04 перегрев»): a chip, red unless it says all is well.
  const FAULT_NAME = /ошибк|авари|неисправ|fault|error|failure|alarm/i;
  const FAULT_OK = /^(|ok|ок|норма|в норме|нет|none|no errors?|no faults?|ошибок нет|нет ошибок|аварий нет|0|false|off|normal)$/i;
  const LL = (ru, en) => (LANG === "ru" ? ru : en);
  /** Entity ids on the open Hauflo dashboard's config: the agent let them through as Hauflo's own (or read-only).
   * Only what an item of Hauflo's (a view / section / card with a `hauflo_id`) names as its entity — `entity`,
   * `entity_id`, `entities[]` — never free text (a heading's «name: switch.ac») and never the integrator's own cards
   * (security re-review 28.10). */
  const OWNED = { url: "", ids: null, wait: null };
  const ownedIds = (cfg) => {
    const out = new Set();
    const ENT = /^[a-z_]+\.[a-z0-9_]+$/;
    const add = (v) => typeof v === "string" && ENT.test(v) && out.add(v);
    // What a Hauflo item holds (its stack's inner cards too), not its children in the tree: those are judged by their own id.
    const take = (v, skip) => {
      if (Array.isArray(v)) return v.forEach((x) => take(x));
      if (!v || typeof v !== "object") return;
      for (const [k, x] of Object.entries(v)) {
        if (k === skip) continue;
        if (k === "entity") add(x);
        else if (k === "entity_id") (Array.isArray(x) ? x : [x]).forEach(add);
        else if (k === "entities" && Array.isArray(x)) x.forEach((e) => (typeof e === "string" ? add(e) : add(e?.entity)));
        if (x && typeof x === "object") take(x);
      }
    };
    // The tree as the agent's merge reads it (dashmerge.py): views → sections → cards, or views → cards.
    const walk = (node, level) => {
      const key = level === 0 ? "views" : level === 1 ? (Array.isArray(node.sections) || node.type === "sections" ? "sections" : "cards") : level === 2 ? "cards" : null;
      if (level > 0 && typeof node.hauflo_id === "string" && node.hauflo_id) take(node, key);
      if (!key || !Array.isArray(node[key])) return;
      const next = level === 0 ? 1 : key === "sections" ? 2 : 3;
      node[key].forEach((c) => c && typeof c === "object" && !Array.isArray(c) && walk(c, next));
    };
    if (cfg && typeof cfg === "object") walk(cfg, 0);
    return out;
  };
  const ownedOnDashboard = (card) => {
    const url = location.pathname.split("/")[1] || "";
    const h = card._hass;
    if (!/^hauflo-/.test(url) || !h?.callWS) return new Set();
    if (OWNED.url !== url && !OWNED.wait) {
      OWNED.url = url;
      OWNED.wait = h
        .callWS({ type: "lovelace/config", url_path: url })
        .then((cfg) => (OWNED.ids = ownedIds(cfg)))
        .catch(() => (OWNED.ids = new Set()))
        .finally(() => {
          OWNED.wait = null;
          card._sig = "";
          card._update();
        });
    }
    return OWNED.ids || new Set();
  };
  /** Cards on this page besides Hauflo's area cards: what the family (or HA's «Добавить на панель») put there. */
  const othersOnPage = () => {
    const panel = document.querySelector("home-assistant")?.shadowRoot?.querySelector("home-assistant-main")?.shadowRoot?.querySelector("ha-drawer partial-panel-resolver ha-panel-lovelace");
    const views = panel?.lovelace?.config?.views || [];
    const path = decodeURIComponent(location.pathname.split("/")[2] || "");
    const v = views.find((x, i) => (x.path || String(i)) === path) || (!path ? views[0] : undefined);
    if (!v) return 0;
    const cards = [...(v.cards || []), ...(v.sections || []).flatMap((s) => s.cards || [])];
    return cards.filter((c) => c && c.type !== "custom:hauflo-area").length;
  };
  class Area extends Status {
    /** The area's entities: put in it themselves or through their device; not hidden, not diagnostic / config. */
    members() {
      const h = this._hass;
      const area = this._config.area;
      if (!h || !area) return [];
      if (this._memo && this._memo.ents === h.entities && this._memo.devs === h.devices && this._memo.area === area) return this._memo.ids;
      const ids = Object.values(h.entities || {})
        .filter((e) => !e.hidden && !e.entity_category && (e.area_id ? e.area_id === area : !!e.device_id && h.devices?.[e.device_id]?.area_id === area))
        .map((e) => e.entity_id)
        .sort((a, b) => this.label(a).localeCompare(this.label(b)));
      this._memo = { ents: h.entities, devs: h.devices, area, ids };
      return ids;
    }
    watched() {
      return this.members();
    }
    /** The name without what the page already says: the room's name and the device's («Котельная Дом Родителей
     * МГ28_Газовый котёл _ошибка» → «Газовый котёл ошибка»). */
    label(id) {
      const h = this._hass;
      const s = h.states[id];
      let n = nameOf(s, id);
      const e = h.entities?.[id];
      const dev = e?.device_id ? h.devices?.[e.device_id] : undefined;
      const area = h.areas?.[this._config.area]?.name;
      const cut = (x) => {
        if (x && n.toLowerCase().startsWith(x.toLowerCase()) && n.length > x.length + 1) n = n.slice(x.length).replace(/^[\s_:·.,-]+/, "");
      };
      cut(area);
      cut(dev?.name_by_user);
      cut(dev?.name);
      n = n.replace(/_+/g, " ").replace(/\s{2,}/g, " ").trim();
      return n ? n[0].toUpperCase() + n.slice(1) : nameOf(s, id);
    }
    word(s) {
      const d = s.entity_id.split(".")[0];
      if (["switch", "light", "fan", "input_boolean"].includes(d) && !nd(s)) return s.state === "on" ? ["вкл", "run"] : ["выкл", "stop"];
      if (d === "cover" && !nd(s)) return [s.state === "open" ? "открыта" : s.state === "closed" ? "закрыта" : s.state === "opening" ? "открывается…" : s.state === "closing" ? "закрывается…" : s.state, s.state === "open" ? "run" : "stop"];
      if (d === "sensor" && !nd(s) && num(s.state) === undefined) return FAULT_NAME.test(`${s.attributes.device_class || ""} ${s.attributes.friendly_name || s.entity_id}`) ? [s.state, FAULT_OK.test(s.state.trim()) ? "ok" : "fault"] : [s.state, "val"];
      if (d === "lock" && !nd(s)) return [s.state === "locked" ? "заперт" : s.state === "unlocked" ? "не заперт" : s.state, s.state === "locked" ? "ok" : "warn"];
      return super.word(s);
    }
    /** Which block an entity goes in. */
    kind(id) {
      const s = this._hass.states[id];
      const d = id.split(".")[0];
      if (d === "climate" || d === "water_heater") return "climate";
      if (d === "binary_sensor" || (d === "sensor" && !nd(s) && num(s.state) === undefined && FAULT_NAME.test(`${s.attributes.device_class || ""} ${s.attributes.friendly_name || id}`))) return "status";
      if (d === "sensor") return "values";
      if (["number", "input_number", "select", "input_select"].includes(d)) return "settings";
      if (["switch", "light", "fan", "input_boolean", "cover", "humidifier"].includes(d) && !AREA_GUARDED(s)) return "switches";
      if (["button", "input_button", "script", "scene"].includes(d)) return "buttons";
      return "other";
    }
    css() {
      return `${super.css()}${TONE_CSS}.cl.toned .pm button{background:color-mix(in srgb,var(--ac) 16%,transparent);box-shadow:inset 0 0 0 1.5px color-mix(in srgb,var(--ac) 55%,transparent);color:var(--acT)}.cl.toned.calm .pm button{background:color-mix(in srgb,var(--ac) 10%,transparent);box-shadow:inset 0 0 0 1.5px color-mix(in srgb,var(--ac) 38%,transparent)}.d{padding:18px 18px 16px}.d .sub{display:block;margin:18px 0 0 2px;font-size:10px}.r ha-icon{--mdc-icon-size:18px;color:var(--ink3);flex:0 0 auto}.r.run ha-icon{color:var(--amberT)}.r.fault ha-icon{color:var(--alertT)}
.empty{font:400 14px/1.5 Onest,sans-serif;color:var(--ink2);margin:6px 2px 2px}.hint{font:400 13px/1.45 Onest,sans-serif;color:var(--ink3);margin:8px 2px 0}.hint b{font-weight:500;color:var(--ink2)}.r{min-height:56px}
.cl{display:flex;align-items:center;gap:12px;padding:12px 0;border-top:1px solid var(--hair)}.sub+.cl,.cl:first-of-type{border-top:0}
.cl .ccn{flex:1;min-width:0;display:flex;flex-direction:column;gap:3px;cursor:pointer}.cl .zn{font:500 15px/1.25 Onest,sans-serif;color:var(--ink);overflow:hidden;text-overflow:ellipsis;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical}
.cl .cs{font:400 13px Onest,sans-serif;color:var(--ink2)}.cl.heat .zn::after{content:"";display:inline-block;width:6px;height:6px;border-radius:3px;background:var(--amber);margin-left:7px;vertical-align:middle;box-shadow:0 0 8px var(--amber)}
.cct{font-size:34px;line-height:1;text-align:right;white-space:nowrap}.cct small{font:300 14px Onest,sans-serif;color:var(--ink2);margin-left:1px}.cct .tgc{display:block;margin-top:4px;font:400 12px/1.2 Onest,sans-serif;color:var(--ink2);letter-spacing:0}.cl.off .cct{color:var(--ink3)}
.pm{display:flex;gap:6px;flex:0 0 auto}.pm button{width:44px;height:44px;border-radius:22px;display:grid;place-items:center;box-shadow:var(--eh-edge,inset 0 0 0 1px var(--line));background:var(--eh-glass,transparent);font:300 22px Onest,sans-serif}.pm button:disabled{opacity:.35;pointer-events:none}
.modes{display:flex;flex-wrap:wrap;gap:6px;margin:-4px 0 6px}.modes button{min-height:36px;padding:0 12px;border-radius:12px;font:500 12px Onest,sans-serif;color:var(--ink2);box-shadow:inset 0 0 0 1px var(--line)}.modes .on{color:var(--ink);background:var(--track);box-shadow:none}
.vals{display:grid;grid-template-columns:repeat(auto-fit,minmax(136px,1fr));gap:8px;margin-top:8px}.v{padding:12px 14px;border-radius:18px;background:var(--track);min-width:0;cursor:pointer;display:flex;flex-direction:column;gap:6px}
.v .vn{font:500 12px/1.25 Onest,sans-serif;color:var(--ink2);display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}.v .vv{font:200 26px/1 Onest,sans-serif;letter-spacing:-.03em;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.v .vv small{font:400 12px Onest,sans-serif;color:var(--ink2);margin-left:3px;letter-spacing:0}.v .vt{font:500 14px/1.3 Onest,sans-serif}
.v.nolink .vv{color:var(--ink3)}.v ha-icon{--mdc-icon-size:16px;color:var(--ink3)}.v .vh{display:flex;align-items:center;gap:6px;min-width:0}
.set{display:flex;align-items:center;gap:8px;margin-top:2px}.set button{width:36px;height:36px;border-radius:18px;box-shadow:inset 0 0 0 1px var(--line);font:300 20px Onest,sans-serif;flex:0 0 auto}.set .vv{flex:1;text-align:center;font-size:22px}
.v select{width:100%;min-height:40px;border-radius:12px;border:0;padding:0 10px;font:500 14px Onest,sans-serif;color:var(--ink);background:var(--eh-glass,var(--card-background-color));box-shadow:inset 0 0 0 1px var(--hair)}
.vals.tgs{grid-template-columns:repeat(auto-fit,minmax(230px,1fr))}.vals.sets{grid-template-columns:repeat(auto-fit,minmax(170px,1fr))}.tg{display:flex;align-items:center;gap:12px;padding:10px 12px;min-height:56px;border-radius:18px;box-shadow:inset 0 0 0 1px var(--hair);min-width:0}.tg .tx{flex:1;min-width:0;cursor:pointer}.tg.on{background:color-mix(in srgb,var(--amber) 12%,transparent);box-shadow:none}
.tg>ha-icon{--mdc-icon-size:20px;color:var(--ink3);flex:0 0 auto}.tg.on>ha-icon{color:var(--amberT)}
.sw{width:50px;height:30px;border-radius:15px;background:var(--track);position:relative;flex:0 0 auto}.sw::before{content:"";position:absolute;inset:-8px -6px}.sw i{position:absolute;top:3px;left:3px;width:24px;height:24px;border-radius:12px;background:var(--ink3);transition:all .2s}.on .sw{background:var(--amber)}.on .sw i{left:23px;background:#fff}.sw:disabled{opacity:.35}
.cv{display:flex;gap:4px;flex:0 0 auto}.cv button{width:40px;height:40px;border-radius:14px;box-shadow:inset 0 0 0 1px var(--line);display:grid;place-items:center}.cv ha-icon{--mdc-icon-size:18px;color:var(--ink2)}
.chips{display:flex;flex-wrap:wrap;gap:8px;margin-top:8px}.chip{display:flex;align-items:center;gap:8px;padding:8px 12px;border-radius:14px;box-shadow:inset 0 0 0 1px var(--line);cursor:pointer;min-width:0;max-width:100%}.chip .nm{-webkit-line-clamp:1}.chip .w{white-space:nowrap}
.chip i{width:8px;height:8px;border-radius:4px;flex:0 0 auto}.chip.fault{box-shadow:none}.chip.warn{box-shadow:none}
.btns{display:flex;flex-wrap:wrap;gap:8px;margin-top:8px}.btns button{min-height:44px;padding:0 15px 0 12px;border-radius:15px;display:flex;align-items:center;gap:8px;max-width:100%;color:var(--ink);box-shadow:var(--eh-edge,inset 0 0 0 1px var(--line));background:var(--eh-glass,transparent);font:500 13px Onest,sans-serif}.btns button ha-icon{color:var(--mintT);--mdc-icon-size:17px}
.btns button.arm{background:color-mix(in srgb,var(--amber) 22%,transparent)}.btns button.go{background:color-mix(in srgb,var(--mint) 26%,transparent)}`;
    }
    /** A thermostat / boiler circuit: name, now, mode, its setpoint large with ±; HA's dialog on the name. */
    climateRow(id) {
      const s = this._hass.states[id];
      const a = s.attributes;
      const wh = id.startsWith("water_heater.");
      const off = s.state === "off";
      const range = !wh && num(a.temperature) === undefined && (num(a.target_temp_low) !== undefined || num(a.target_temp_high) !== undefined);
      const target = num(a.temperature);
      const cur = num(a.current_temperature);
      const step = Math.max(num(a.target_temp_step) || (wh ? 1 : 0.5), 0.5);
      // What it DOES (greater than the mode, owner 28.10): hvac_action when it fits the temperatures; the mode small.
      const nowC = wh ? null : climateNow(this._hass, s, null);
      const action = wh ? a.hvac_action : { heat: "heating", cool: "cooling", idle: "idle", dry: "drying", fan: "fan" }[nowC];
      const heating = nowC === "heat" || (wh && !off && cur !== undefined && target !== undefined && cur < target - 1);
      const mode = nd(s) ? (s.state === "unavailable" ? "нет связи" : "нет данных") : off ? "выключен" : wh ? (WH_MODE[a.operation_mode] ?? a.operation_mode ?? s.state) : modeSmall(s.state) || HVAC[s.state] || s.state;
      // Each part its own text (each is put into English on its own).
      const sub = [wh ? (action && !off ? { heating: "греет", cooling: "охлаждает", idle: "", drying: "осушает", fan: "вентилирует", off: "" }[action] ?? "" : "") : !off && nowC ? climateLine(nowC, target, activeMode(s)) : "", wh || off || nd(s) ? mode : modeDiff(s.state, nowC)]
        .filter(Boolean)
        .map((x) => `<span>${esc(x)}</span>`)
        .join(" · ");
      // The big number is what is now; the setpoint is the small «цель 21°» (owner 28.10).
      const tgtTxt = range ? `${fmt(num(a.target_temp_low))}–${fmt(num(a.target_temp_high))}°` : target === undefined ? "" : `${fmt(target, step < 1 && target % 1 ? 1 : 0)}°`;
      const big = tgtTxt ? `${tgtTxt.replace(/°$/, "")}<small>°</small>` : cur === undefined ? "—" : `${fmt(cur, 1)}<small>°</small>`;
      const can = !nd(s) && !range && target !== undefined;
      const modes = wh ? a.operation_list || [] : a.hvac_modes || [];
      const modeKey = wh ? a.operation_mode : s.state;
      const chips =
        modes.length > 1 && !nd(s)
          ? `<div class="modes">${modes.slice(0, 6).map((m) => `<button class="${m !== modeKey ? "" : !wh && climateTone(null, nowC, m) ? `on mk m-${climateTone(null, nowC, m)}` : "on"}" data-mode="${esc(m)}" data-e="${esc(id)}" aria-pressed="${m === modeKey}">${esc(wh ? (WH_MODE[m] ?? m) : (HVAC[m] ?? m))}</button>`).join("")}</div>`
          : "";
      // The mode's colour on ± and the mode chip, softer at target; grey only off / no link (owner 29.10).
      const tn = wh || nd(s) ? "" : climateTone(s, nowC);
      return `<div class="cl${heating ? " heat" : ""}${off ? " off" : ""}${tn ? ` m-${tn} toned${toneCalm(tn, nowC) ? " calm" : ""}` : ""}"><div class="ccn" data-info="${esc(id)}" role="button" tabindex="0"><span class="zn">${esc(this.label(id))}</span><span class="cs">${sub}</span></div>
<div class="cct thin">${big}<span class="tgc">${tgtTxt && cur !== undefined ? `Сейчас ${fmt(cur, 1)}°` : ""}</span></div>
<div class="pm"><button data-e="${esc(id)}" data-d="${-step}" aria-label="Холоднее на ${fmt(step, step % 1 ? 1 : 0)}°"${can ? "" : " disabled"}>−</button><button data-e="${esc(id)}" data-d="${step}" aria-label="Теплее на ${fmt(step, step % 1 ? 1 : 0)}°"${can ? "" : " disabled"}>+</button></div></div>${chips}`;
    }
    /** A reading: its name and its value, large (a temperature, a pressure, a modulation…). */
    valueTile(id) {
      const s = this._hass.states[id];
      const v = num(s.state);
      const dc = s.attributes.device_class;
      const icon = s.attributes.icon || SENSOR_ICON[dc] || AREA_ICON.sensor;
      const unit = unitOf(s);
      const digits = v === undefined ? 0 : Math.abs(v) < 10 && v % 1 ? (Math.abs(v * 10) % 1 ? 2 : 1) : v % 1 ? 1 : 0;
      const val = nd(s) ? `<span class="vv">—</span>` : v === undefined ? `<span class="vt">${esc(s.state)}</span>` : `<span class="vv thin">${fmt(v, Math.min(digits, 2))}${unit ? `<small>${esc(unit)}</small>` : ""}</span>`;
      return `<div class="v${nd(s) ? " nolink" : ""}" data-info="${esc(id)}" role="button" tabindex="0"><span class="vh"><ha-icon icon="${esc(icon)}"></ha-icon><span class="vn">${esc(this.label(id))}</span></span>${val}</div>`;
    }
    /** A number (± by its step, inside its range) or a list (its options). */
    settingTile(id) {
      const s = this._hass.states[id];
      const d = id.split(".")[0];
      const a = s.attributes;
      if (d === "select" || d === "input_select") {
        const opts = a.options || [];
        return `<div class="v"><span class="vh"><ha-icon icon="${esc(a.icon || AREA_ICON[d])}"></ha-icon><span class="vn" data-info="${esc(id)}">${esc(this.label(id))}</span></span><select data-sel="${esc(id)}"${nd(s) ? " disabled" : ""}>${opts.map((o) => `<option value="${esc(o)}"${o === s.state ? " selected" : ""}>${esc(o)}</option>`).join("")}</select></div>`;
      }
      const v = num(s.state);
      const step = num(a.step) || 1;
      const min = num(a.min);
      const max = num(a.max);
      const dg = String(step).includes(".") ? String(step).split(".")[1].length : 0;
      return `<div class="v"><span class="vh"><ha-icon icon="${esc(a.icon || AREA_ICON[d])}"></ha-icon><span class="vn" data-info="${esc(id)}">${esc(this.label(id))}</span></span><div class="set"><button data-num="${esc(id)}" data-d="${-step}" aria-label="−"${v === undefined || (min !== undefined && v <= min) ? " disabled" : ""}>−</button><span class="vv thin">${v === undefined ? "—" : fmt(v, dg)}${unitOf(s) ? `<small>${esc(unitOf(s))}</small>` : ""}</span><button data-num="${esc(id)}" data-d="${step}" aria-label="+"${v === undefined || (max !== undefined && v >= max) ? " disabled" : ""}>+</button></div></div>`;
    }
    /** On / off (a switch, a lamp, a fan, a humidifier) — or a blind's open / stop / close. */
    toggleRow(id) {
      const s = this._hass.states[id];
      const d = id.split(".")[0];
      const [w] = this.word(s);
      const icon = s.attributes.icon || AREA_ICON[d] || "mdi:circle-medium";
      if (d === "cover") {
        const f = s.attributes.supported_features || 0;
        const b = (svc, ic, label) => `<button data-cover="${esc(id)}" data-svc="${svc}" aria-label="${label}"${nd(s) ? " disabled" : ""}><ha-icon icon="${ic}"></ha-icon></button>`;
        return `<div class="tg${s.state === "open" ? " on" : ""}"><ha-icon icon="${esc(icon)}"></ha-icon><div class="tx" data-info="${esc(id)}"><div class="nm">${esc(this.label(id))}</div><div class="w">${esc(w)}</div></div><div class="cv">${f & 1 ? b("open_cover", "mdi:arrow-up", "Открыть") : ""}${f & 8 ? b("stop_cover", "mdi:stop", "Стоп") : ""}${f & 2 ? b("close_cover", "mdi:arrow-down", "Закрыть") : ""}</div></div>`;
      }
      const on = s.state === "on";
      return `<div class="tg${on ? " on" : ""}"><ha-icon icon="${esc(icon)}"></ha-icon><div class="tx" data-info="${esc(id)}"><div class="nm">${esc(this.label(id))}</div><div class="w">${esc(nd(s) ? this.word(s)[0] : w)}</div></div><button class="sw" data-tog="${esc(id)}" aria-label="Вкл/выкл"${nd(s) ? " disabled" : ""}><i></i></button></div>`;
    }
    chip(id) {
      const s = this._hass.states[id];
      const [w, k] = this.word(s);
      return `<div class="chip ${k}" data-info="${esc(id)}" role="button" tabindex="0"><i></i><span class="nm">${esc(this.label(id))}</span><span class="w">${esc(w)}</span></div>`;
    }
    render() {
      const h = this._hass;
      const ids = orderIds(this, this.members()).filter((id) => h.states[id]);
      const areaName = h.areas?.[this._config.area]?.name;
      const title = this._config.title || areaName || LL("Комната", "Room");
      const admin = h.user?.is_admin === true;
      // How a device of another integration gets here — said to whoever can do it (an admin), with the room's name.
      const how = `<p class="hint">${LL("Чтобы устройство другой интеграции появилось здесь в стиле Hauflo, укажите у него комнату", "To show another integration's device here in the Hauflo style, set its area to")} «<b data-own>${esc(areaName || title)}</b>» ${LL("в Home Assistant: Настройки → Устройства и службы → устройство → ✎.", "in Home Assistant: Settings → Devices & services → the device → ✎.")}</p>`;
      if (!ids.length) {
        // The family's own cards are on the page (HA's «Добавить на панель»): no «nothing here» above them.
        const others = othersOnPage();
        this.hideCard(others > 0 && !admin);
        if (others > 0) return admin ? `<div class="glass d"><span class="caps">${esc(title)}</span>${how}</div>` : "";
        return `<div class="glass d"><span class="caps">${esc(title)}</span><p class="empty">${LL("Здесь пока ничего нет", "Nothing here yet")}</p>${admin ? how : ""}</div>`;
      }
      this.hideCard(false);
      const owned = ownedOnDashboard(this);
      this._owned = owned;
      const by = { climate: [], values: [], settings: [], switches: [], status: [], buttons: [], other: [] };
      ids.forEach((id) => by[this.kind(id)].push(id));
      const faults = ids.filter((id) => this.word(h.states[id])[1] === "fault").length;
      const block = (label, body) => `<span class="caps sub">${esc(label)}</span>${body}`;
      const parts = [];
      if (by.climate.length) parts.push(block("Климат", by.climate.map((id) => this.climateRow(id)).join("")));
      if (by.status.length) parts.push(block("Состояние", `<div class="chips">${by.status.map((id) => this.chip(id)).join("")}</div>`));
      if (by.switches.length) parts.push(block("Управление", `<div class="vals tgs">${by.switches.map((id) => this.toggleRow(id)).join("")}</div>`));
      if (by.settings.length) parts.push(block("Настройки", `<div class="vals sets">${by.settings.map((id) => this.settingTile(id)).join("")}</div>`));
      if (by.values.length) parts.push(block("Показания", `<div class="vals">${by.values.map((id) => this.valueTile(id)).join("")}</div>`));
      if (by.buttons.length)
        parts.push(block("Кнопки", `<div class="btns">${by.buttons.map((id) => `<button data-press="${esc(id)}"><ha-icon icon="${esc(h.states[id].attributes.icon || "hauflo:play")}"></ha-icon><span>${esc(this.label(id))}</span></button>`).join("")}</div>`));
      if (by.other.length)
        parts.push(
          block(
            "Прочее",
            `<div class="grid">${by.other
              .map((id) => {
                const s = h.states[id];
                const [w, k] = this.word(s);
                const d = id.split(".")[0];
                return `<div class="r ${k}" data-info="${esc(id)}" role="button" tabindex="0"><ha-icon icon="${esc(s.attributes.icon || AREA_ICON[d] || "mdi:circle-medium")}"></ha-icon><div class="tx"><div class="nm">${esc(this.label(id))}</div><div class="w">${esc(w)}</div></div></div>`;
              })
              .join("")}</div>`,
          ),
        );
      return `<div class="glass d"><div class="hd"><span class="caps">${esc(title)}</span>${faults ? `<span class="sum"><i class="fault"></i>аварий: ${faults}</span>` : ""}</div>${parts.join("")}</div>`;
    }
    /** Only a member of this area (HA's registry, read here) is ever acted on — whatever the markup says. */
    member(id) {
      return this.members().includes(id) && !!this._hass.states[id];
    }
    bind() {
      const root = this.shadowRoot;
      const h = () => this._hass;
      // A light / blind / scene row: a long press opens its sheet («В избранное», brightness) — every light, every blind.
      root.querySelectorAll(".tg .tx[data-info]").forEach((el) => /^(light|switch|cover|scene|script)\./.test(el.dataset.info) && longPress(el, () => openTile(this, el.dataset.info)));
      root.querySelectorAll("[data-info]").forEach((el) => el.addEventListener("click", () => !GST.lp && moreInfo(this, el.dataset.info)));
      root.querySelectorAll("button[data-d][data-e]").forEach((b) =>
        b.addEventListener("click", () => {
          const id = b.dataset.e;
          if (!this.member(id)) return;
          const s = h().states[id];
          const a = s.attributes;
          const base = num(a.temperature);
          if (base === undefined) return;
          const v = Math.min(num(a.max_temp) ?? 95, Math.max(num(a.min_temp) ?? 5, base + Number(b.dataset.d)));
          haptic(this);
          call(h(), id.split(".")[0], "set_temperature", { entity_id: id, temperature: v });
        }),
      );
      root.querySelectorAll("button[data-mode]").forEach((b) =>
        b.addEventListener("click", () => {
          const id = b.dataset.e;
          if (!this.member(id)) return;
          haptic(this);
          if (id.startsWith("water_heater.")) call(h(), "water_heater", "set_operation_mode", { entity_id: id, operation_mode: b.dataset.mode });
          else call(h(), "climate", "set_hvac_mode", { entity_id: id, hvac_mode: b.dataset.mode });
        }),
      );
      root.querySelectorAll("button[data-num]").forEach((b) =>
        b.addEventListener("click", () => {
          const id = b.dataset.num;
          if (!this.member(id)) return;
          const s = h().states[id];
          const v = num(s.state);
          if (v === undefined) return;
          const a = s.attributes;
          const step = Number(b.dataset.d);
          const dg = String(Math.abs(step)).includes(".") ? String(Math.abs(step)).split(".")[1].length : 0;
          const nv = Number(Math.min(num(a.max) ?? Infinity, Math.max(num(a.min) ?? -Infinity, v + step)).toFixed(dg));
          haptic(this);
          call(h(), id.split(".")[0], "set_value", { entity_id: id, value: nv });
        }),
      );
      root.querySelectorAll("select[data-sel]").forEach((sel) =>
        sel.addEventListener("change", () => {
          const id = sel.dataset.sel;
          if (!this.member(id)) return;
          haptic(this);
          call(h(), id.split(".")[0], "select_option", { entity_id: id, option: sel.value });
        }),
      );
      root.querySelectorAll("button[data-tog]").forEach((b) =>
        b.addEventListener("click", () => {
          const id = b.dataset.tog;
          if (!this.member(id) || AREA_GUARDED(h().states[id])) return;
          haptic(this);
          call(h(), "homeassistant", "toggle", { entity_id: id });
        }),
      );
      root.querySelectorAll("button[data-cover]").forEach((b) =>
        b.addEventListener("click", () => {
          const id = b.dataset.cover;
          if (!this.member(id) || AREA_GUARDED(h().states[id]) || !["open_cover", "close_cover", "stop_cover"].includes(b.dataset.svc)) return;
          haptic(this);
          call(h(), "cover", b.dataset.svc, { entity_id: id });
        }),
      );
      // A button of another integration presses on a second tap within 4 s (the first one says «ещё раз»).
      root.querySelectorAll("button[data-press]").forEach((b) =>
        b.addEventListener("click", () => {
          const id = b.dataset.press;
          if (!this.member(id)) return;
          const d = id.split(".")[0];
          if (!this._owned?.has(id) && this._armed !== id) {
            this._armed = id;
            b.classList.add("arm");
            const label = b.querySelector("span");
            const was = label.textContent;
            label.textContent = LL(`${was} — ещё раз`, `${was} — tap again`);
            clearTimeout(this._armT);
            this._armT = setTimeout(() => {
              this._armed = null;
              b.classList.remove("arm");
              label.textContent = was;
            }, 4000);
            return;
          }
          this._armed = null;
          clearTimeout(this._armT);
          haptic(this);
          b.classList.remove("arm");
          b.classList.add("go");
          setTimeout(() => b.classList.remove("go"), 1200);
          if (d === "button" || d === "input_button") call(h(), d, "press", { entity_id: id });
          else call(h(), d, "turn_on", { entity_id: id });
        }),
      );
    }
  }
  const WH_MODE = { eco: "Эко", electric: "Электро", gas: "Газ", heat_pump: "Тепловой насос", high_demand: "Макс", performance: "Мощность", off: "Выкл" };

  // --- hauflo-security: the alarm system, locks, doors and windows --------------------------------
  const ALARM_STATE = { disarmed: ["Снята с охраны", "ok"], armed_home: ["Под охраной · дома", "arm"], armed_away: ["Под охраной", "arm"], armed_night: ["Ночной режим", "arm"], armed_vacation: ["Отпуск", "arm"], armed_custom_bypass: ["Под охраной", "arm"], arming: ["Ставится…", "wait"], disarming: ["Снимается…", "wait"], pending: ["Задержка входа…", "wait"], triggered: ["ТРЕВОГА", "alarm"] };
  class Security extends EHCard {
    watched() {
      const c = this._config;
      return [c.entity, ...(c.locks || []), ...(c.openings || [])].map((e) => (typeof e === "string" ? e : e?.entity)).filter(Boolean);
    }
    css() {
      return `.s{padding:18px 20px}.big{display:flex;align-items:center;gap:14px;margin-top:8px}.sh{width:56px;height:56px;border-radius:20px;display:grid;place-items:center}.sh ha-icon{--mdc-icon-size:28px}
.ok .sh{background:var(--track);color:var(--ink2)}.arm .sh{background:color-mix(in srgb,var(--mint) 22%,transparent);color:var(--mintT)}.alarm .sh{background:var(--alert);color:#fff;box-shadow:0 0 30px var(--alertGlow)}.wait .sh{background:color-mix(in srgb,var(--amber) 22%,transparent);color:var(--amberT)}
.st{font:300 24px Onest,sans-serif;letter-spacing:-.02em}.sub{font:400 13px Onest,sans-serif;color:var(--ink2);margin-top:2px}
.btns{display:flex;gap:6px;margin-top:14px}.btns button{flex:1;height:44px;border-radius:14px;font:500 13px Onest,sans-serif;box-shadow:inset 0 0 0 1px var(--line)}.btns button.on{background:var(--track);box-shadow:none}
.lst{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:12px}.it{display:flex;align-items:center;gap:10px;padding:10px 12px;border-radius:14px;background:var(--track);cursor:pointer;min-width:0}
.it .nm{font:500 13px Onest,sans-serif;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;hyphens:auto;overflow-wrap:break-word;line-height:1.25}.it .w{font:400 13px Onest,sans-serif;color:var(--ink2)}.it.bad{background:color-mix(in srgb,var(--amber) 18%,transparent)}.it.bad ha-icon{color:var(--amberT)}
.alarm .glow{background:radial-gradient(60% 60% at 15% 10%,var(--alertGlow),transparent 70%)}`;
    }
    render() {
      const c = this._config;
      const p = st(this._hass, c.entity);
      const [word, k] = !p ? ["", "ok"] : nd(p) ? ["Нет связи", "ok"] : ALARM_STATE[p.state] || [p.state, "ok"];
      const obj = (e) => (typeof e === "string" ? { id: e } : { id: e.entity, name: e.name });
      const locks = (c.locks || []).map(obj).map((x) => ({ ...x, s: st(this._hass, x.id) })).filter((x) => x.s);
      const opens = (c.openings || []).map(obj).map((x) => ({ ...x, s: st(this._hass, x.id) })).filter((x) => x.s);
      const openN = opens.filter((x) => x.s.state === "on").length;
      // Unknown is said as unknown — never «всё закрыто» or «не заперто» made up from no data.
      const openND = opens.filter((x) => nd(x.s)).length;
      const lockND = locks.filter((x) => nd(x.s)).length;
      const unlocked = locks.filter((x) => !nd(x.s) && x.s.state !== "locked").length;
      return `<div class="glass s ${k}"><div class="glow"></div><div style="position:relative"><span class="caps">${esc(c.name || "Охрана")}</span>
${p ? `<div class="big"><span class="sh"><ha-icon icon="${k === "ok" ? "hauflo:shield" : k === "alarm" ? "hauflo:shield-alert" : "hauflo:shield"}"></ha-icon></span><div><div class="st">${esc(word)}</div><div class="sub">${openN ? `открыто: ${openN}` : openND ? `нет данных: ${openND}` : "всё закрыто"}${locks.length ? ` · ${unlocked ? `не заперто: ${unlocked}` : lockND ? `замки без связи: ${lockND}` : "двери заперты"}` : ""}</div></div></div>
<div class="btns"><button data-a="alarm_disarm" class="${p.state === "disarmed" ? "on" : ""}">${p.state === "disarmed" ? "Снята" : "Снять"}</button><button data-a="alarm_arm_home" class="${p.state === "armed_home" ? "on" : ""}">Дома</button><button data-a="alarm_arm_away" class="${p.state === "armed_away" ? "on" : ""}">${p.state === "armed_away" ? "Под охраной" : "Уйти"}</button>${p.attributes.supported_features & 4 ? `<button data-a="alarm_arm_night" class="${p.state === "armed_night" ? "on" : ""}">Ночь</button>` : ""}</div>` : ""}
${locks.length || opens.length ? `<div class="lst">${locks
        .map((x) => `<div class="it ${!nd(x.s) && x.s.state !== "locked" ? "bad" : ""}" data-lock="${esc(x.id)}"><ha-icon icon="${x.s.state === "locked" ? "hauflo:lock" : "hauflo:unlock"}"></ha-icon><div style="min-width:0"><div class="nm">${esc(x.name || nameOf(x.s, x.id))}</div><div class="w">${nd(x.s) ? "нет связи" : x.s.state === "locked" ? "заперт" : x.s.state === "jammed" ? "заклинило" : x.s.state === "open" ? "открыт" : "не заперт"}</div></div></div>`)
        .join("")}${opens
        .map((x) => `<div class="it ${x.s.state === "on" ? "bad" : ""}" data-info="${esc(x.id)}"><ha-icon icon="${x.s.attributes.device_class === "window" ? "hauflo:window" : ["motion", "occupancy", "presence"].includes(x.s.attributes.device_class) ? "hauflo:motion" : "hauflo:door"}"></ha-icon><div style="min-width:0"><div class="nm">${esc(x.name || nameOf(x.s, x.id))}</div><div class="w">${nd(x.s) ? "нет данных" : x.s.attributes.device_class === "motion" || x.s.attributes.device_class === "occupancy" ? (x.s.state === "on" ? "есть движение" : "нет движения") : x.s.state === "on" ? "открыто" : "закрыто"}</div></div></div>`)
        .join("")}</div>` : ""}</div></div>`;
    }
    bind() {
      const c = this._config;
      this.shadowRoot.querySelectorAll("[data-a]").forEach((b) =>
        b.addEventListener("click", () => {
          // A code is asked by HA's own dialog — never typed into a card.
          if (st(this._hass, c.entity)?.attributes.code_format) return moreInfo(this, c.entity);
          haptic(this);
          call(this._hass, "alarm_control_panel", b.dataset.a, { entity_id: c.entity });
        }),
      );
      this.shadowRoot.querySelectorAll("[data-lock]").forEach((el) =>
        el.addEventListener("click", () => {
          const s = st(this._hass, el.dataset.lock);
          haptic(this);
          call(this._hass, "lock", s.state === "locked" ? "unlock" : "lock", { entity_id: el.dataset.lock });
        }),
      );
      this.shadowRoot.querySelectorAll("[data-info]").forEach((el) => el.addEventListener("click", () => moreInfo(this, el.dataset.info)));
    }
    getGridOptions() {
      return { columns: 12, rows: "auto" };
    }
  }

  // --- hauflo-leaks: «Защита от протечек» (Н3) — leak sensors + a water valve of ANY integration (Neptun, Aqara, Zigbee…).
  // The owner connects the integration himself; the card only reads and says «всё в порядке» / «протечка: где». It decides
  // nothing: the valve closes (or opens) only by its own button, on a second tap within 4 s. `area` picks every moisture
  // sensor and every valve of that HA area; `sensors` / `valves` name them by hand (both work together).
  class Leaks extends EHCard {
    ids() {
      const c = this._config;
      const h = this._hass;
      const ent = (x) => (typeof x === "string" ? x : x?.entity);
      let sensors = (c.sensors || []).map(ent).filter(Boolean);
      let valves = (c.valves || []).map(ent).filter(Boolean);
      if (c.area && h?.entities) {
        for (const [id, s] of Object.entries(h.states || {})) {
          const d = id.split(".")[0];
          const e = h.entities[id];
          if (e?.hidden || e?.disabled_by || areaOf(h, id) !== c.area) continue;
          if (d === "binary_sensor" && s.attributes?.device_class === "moisture") sensors.push(id);
          else if (d === "valve" && s.attributes?.device_class !== "gas") valves.push(id);
          else if (d === "switch" && s.attributes?.device_class === "water") valves.push(id);
        }
      }
      sensors = [...new Set(sensors)];
      valves = [...new Set(valves)];
      return { sensors, valves };
    }
    watched() {
      if (!this._hass) return [];
      const { sensors, valves } = this.ids();
      return [...sensors, ...valves];
    }
    css() {
      return `.k{padding:18px 20px}.big{display:flex;align-items:center;gap:14px;margin-top:8px}.sh{width:56px;height:56px;border-radius:20px;display:grid;place-items:center;flex:0 0 auto;background:color-mix(in srgb,var(--mint) 22%,transparent);color:var(--mintT)}.sh ha-icon{--mdc-icon-size:28px}
.wet .sh{background:var(--alert);color:#fff;box-shadow:0 0 30px var(--alertGlow)}.nolink .sh{background:var(--track);color:var(--ink2)}.empty .sh{background:var(--track);color:var(--ink3)}
.st{font:300 24px Onest,sans-serif;letter-spacing:-.02em}.wet .st{color:var(--alertT);font-weight:500}.sub{font:400 13px Onest,sans-serif;color:var(--ink2);margin-top:2px}
.wet .glow{background:radial-gradient(60% 60% at 15% 10%,var(--alertGlow),transparent 70%)}
.lst{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px;margin-top:12px}.it{display:flex;align-items:center;gap:10px;padding:10px 12px;border-radius:14px;background:var(--track);cursor:pointer;min-width:0}
.it .rm,.vl2 .rm{font:400 11px/1.3 Onest,sans-serif;color:var(--ink3)}.it .nm{font:500 13px/1.25 Onest,sans-serif;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;overflow-wrap:break-word}.it .w{font:400 13px Onest,sans-serif;color:var(--ink2)}
.it.bad{background:color-mix(in srgb,var(--alert) 18%,transparent)}.it.bad .w{color:var(--alertT);font-weight:600}.it.deaf{opacity:.6}
.vl2{display:flex;align-items:center;gap:10px;margin-top:12px;padding:10px 12px;border-radius:14px;box-shadow:inset 0 0 0 1px var(--hair)}.vl2 .tx{flex:1;min-width:0}
.vl2 button{height:44px;padding:0 16px;border-radius:14px;font:500 13px Onest,sans-serif;box-shadow:inset 0 0 0 1px var(--line);color:var(--ink);white-space:nowrap}
.vl2 button.arm{background:color-mix(in srgb,var(--amber) 22%,transparent);box-shadow:none}.vl2 button.go{background:color-mix(in srgb,var(--mint) 26%,transparent);box-shadow:none}
.hint{font:400 13px/1.4 Onest,sans-serif;color:var(--ink2);margin-top:10px}`;
    }
    valveWord(s) {
      const closed = s.state === "closed" || s.state === "off";
      const open = s.state === "open" || s.state === "on";
      if (nd(s)) return [LL("нет связи", "no signal"), "nd"];
      if (s.state === "opening") return [LL("открывается…", "opening…"), "mv"];
      if (s.state === "closing") return [LL("закрывается…", "closing…"), "mv"];
      return closed ? [LL("вода перекрыта", "water is off"), "closed"] : open ? [LL("вода идёт", "water is on"), "open"] : [s.state, "x"];
    }
    render() {
      const h = this._hass;
      const { sensors, valves } = this.ids();
      const ss = sensors.map((id) => ({ id, s: st(h, id) })).filter((x) => x.s);
      const vs = valves.map((id) => ({ id, s: st(h, id) })).filter((x) => x.s);
      const wet = ss.filter((x) => x.s.state === "on");
      const deaf = ss.filter((x) => nd(x.s));
      const kind = !ss.length ? "empty" : wet.length ? "wet" : deaf.length === ss.length ? "nolink" : "ok";
      const name = (x) => nameOf(x.s, x.id);
      const [word, sub] =
        kind === "wet"
          ? [LL("Протечка", "Leak"), wet.map(name).join(", ")]
          : kind === "empty"
            ? [LL("Датчиков пока нет", "No sensors yet"), LL("Подключите датчики протечки и кран в Home Assistant — они появятся здесь сами", "Connect leak sensors and a valve in Home Assistant — they show up here by themselves")]
            : kind === "nolink"
              ? [LL("Нет связи с датчиками", "No signal from the sensors"), `${deaf.length} ${LL("из", "of")} ${ss.length}`]
              : [LL("Всё в порядке", "All is well"), `${LL("датчиков", "sensors")}: ${ss.length}${deaf.length ? ` · ${LL("нет связи", "no signal")}: ${deaf.length}` : ` · ${LL("сухо", "dry")}`}`];
      const icon = kind === "wet" ? "mdi:water-alert" : kind === "ok" ? "mdi:water-check-outline" : "mdi:water-off-outline";
      return `<div class="glass k ${kind}"><div class="glow"></div><div style="position:relative"><span class="caps" data-own>${esc(this._config.title || LL("Защита от протечек", "Leak protection"))}</span>
<div class="big"><span class="sh"><ha-icon icon="${icon}"></ha-icon></span><div><div class="st" data-own>${esc(word)}</div><div class="sub" data-own>${esc(sub)}</div></div></div>
${ss.length ? `<div class="lst">${ss.map((x) => `<div class="it ${x.s.state === "on" ? "bad" : nd(x.s) ? "deaf" : ""}" data-info="${esc(x.id)}"><ha-icon icon="${x.s.state === "on" ? "mdi:water-alert" : "mdi:water-outline"}"></ha-icon><div style="min-width:0"><div class="nm">${esc(name(x))}</div>${roomLine(this._hass, x.id, name(x))}<div class="w" data-own>${nd(x.s) ? LL("нет связи", "no signal") : x.s.state === "on" ? LL("протечка", "leak") : LL("сухо", "dry")}</div></div></div>`).join("")}</div>` : ""}
${vs.map((x) => { const [w, k] = this.valveWord(x.s); return `<div class="vl2"><ha-icon icon="${k === "closed" ? "mdi:valve-closed" : "mdi:valve-open"}" style="color:var(--${k === "closed" ? "ice" : "mint"})"></ha-icon><div class="tx" data-info="${esc(x.id)}"><div class="nm">${esc(name(x))}</div>${roomLine(this._hass, x.id, name(x))}<div class="w" data-own>${esc(w)}</div></div>${k === "nd" || k === "mv" ? "" : `<button data-valve="${esc(x.id)}" data-k="${k}"><span data-own>${k === "closed" ? LL("Открыть воду", "Open the water") : LL("Перекрыть воду", "Shut off the water")}</span></button>`}</div>`; }).join("")}
${!vs.length && ss.length ? `<div class="hint" data-own>${LL("Кран не найден: укажите его в карточке (valves) или отнесите к этой комнате в Home Assistant.", "No valve found: name it in the card (valves) or put it into this area in Home Assistant.")}</div>` : ""}
</div></div>`;
    }
    bind() {
      this.shadowRoot.querySelectorAll("[data-info]").forEach((el) => el.addEventListener("click", () => moreInfo(this, el.dataset.info)));
      // Closing the water is by hand only, and on a second tap within 4 s (the first one says so).
      this.shadowRoot.querySelectorAll("button[data-valve]").forEach((b) =>
        b.addEventListener("click", () => {
          const id = b.dataset.valve;
          const span = b.querySelector("span");
          if (this._armed !== id) {
            this._armed = id;
            b.classList.add("arm");
            const was = span.textContent;
            span.textContent = b.dataset.k === "closed" ? LL("Ещё раз — открыть", "Tap again — open") : LL("Ещё раз — перекрыть", "Tap again — shut off");
            clearTimeout(this._armT);
            this._armT = setTimeout(() => {
              this._armed = null;
              b.classList.remove("arm");
              span.textContent = was;
            }, 4000);
            return;
          }
          this._armed = null;
          clearTimeout(this._armT);
          haptic(this);
          b.classList.remove("arm");
          b.classList.add("go");
          const d = id.split(".")[0];
          const open = b.dataset.k === "closed";
          if (d === "valve") sendAck(this, b, "valve", open ? "open_valve" : "close_valve", { entity_id: id });
          else sendAck(this, b, d === "switch" ? "switch" : "homeassistant", open ? "turn_on" : "turn_off", { entity_id: id });
        }),
      );
    }
    getGridOptions() {
      return { columns: 12, rows: "auto" };
    }
  }

  // --- hauflo-camera: the picture, refreshed; a tap opens the live view ---------------------------
  class Camera extends EHCard {
    watched() {
      return [this._config.entity];
    }
    connectedCallback() {
      this._t = setInterval(() => {
        this._sig = "";
        this._update();
      }, (this._config?.refresh || 10) * 1000);
    }
    disconnectedCallback() {
      clearInterval(this._t);
    }
    css() {
      return `.c{aspect-ratio:16/10;background:#111 center/cover;cursor:pointer}.c::after{content:"";position:absolute;inset:0;background:linear-gradient(180deg,rgba(0,0,0,.35),transparent 35%,transparent 65%,rgba(0,0,0,.5))}
.l{position:absolute;left:16px;top:14px;z-index:1;color:#fff;font:500 13px Onest,sans-serif;display:flex;align-items:center;gap:8px}.l i{width:8px;height:8px;border-radius:4px;background:#ff3b30;box-shadow:0 0 10px #ff3b30}
.b{position:absolute;left:16px;bottom:12px;z-index:1;color:rgba(255,255,255,.85);font:400 12px Onest,sans-serif}`;
    }
    render() {
      const s = st(this._hass, this._config.entity);
      if (!s) return `<div class="glass c"></div>`;
      const pic = s.attributes.entity_picture ? `${s.attributes.entity_picture}&t=${Date.now()}` : "";
      return `<div class="glass c" style="background-image:url('${esc(pic)}')"><div class="l"><i></i>${esc(this._config.name || nameOf(s, s.entity_id))}</div><div class="b">${s.state === "recording" ? "запись" : s.state === "streaming" ? "трансляция" : s.state === "unavailable" ? "нет связи" : "обновлено " + hhmm(new Date())}</div></div>`;
    }
    bind() {
      this.shadowRoot.querySelector(".c")?.addEventListener("click", () => moreInfo(this, this._config.entity));
    }
    getGridOptions() {
      return { columns: 12, rows: "auto" };
    }
  }

  // --- hauflo-devices: any other device as a pill — fans, valves, locks, vacuums, humidifiers, boilers…
  const DEV = {
    fan: { icon: "hauflo:fan", word: (s) => (s.state === "on" ? (num(s.attributes.percentage) !== undefined ? `${s.attributes.percentage} %` : "вкл") : "выкл"), tap: ["fan", "toggle"] },
    valve: { icon: "hauflo:valve", word: (s) => ({ open: "открыт", closed: "закрыт", opening: "открывается…", closing: "закрывается…" })[s.state] || s.state, tap: (s) => ["valve", s.state === "open" ? "close_valve" : "open_valve"], on: (s) => s.state === "open" },
    lock: { icon: "hauflo:lock", word: (s) => (nd(s) ? "нет связи" : ({ locked: "заперт", unlocked: "не заперт", jammed: "заклинило", open: "открыт" })[s.state] || s.state), tap: (s) => ["lock", s.state === "locked" ? "unlock" : "lock"], tone: (s) => (s.state === "jammed" ? "alarm" : s.state === "locked" ? "" : "warn") },
    vacuum: { icon: "mdi:robot-vacuum", word: (s) => ({ cleaning: "убирает", docked: "на базе", returning: "едет на базу", idle: "ждёт", paused: "пауза", error: "ошибка" })[s.state] || s.state, tap: (s) => ["vacuum", s.state === "cleaning" ? "return_to_base" : "start"], on: (s) => s.state === "cleaning", tone: (s) => (s.state === "error" ? "alarm" : "") },
    humidifier: { icon: "hauflo:humidity", word: (s) => (s.state === "on" ? (num(s.attributes.humidity) !== undefined ? `${fmt(num(s.attributes.humidity))} %` : "вкл") : "выкл"), tap: ["humidifier", "toggle"] },
    water_heater: { icon: "hauflo:boiler", word: (s) => (s.state === "off" ? "выкл" : `${fmt(num(s.attributes.temperature))}°`), tap: null, on: (s) => s.state !== "off" },
    siren: { icon: "hauflo:bell", word: (s) => (s.state === "on" ? "звучит" : "выключена"), tap: ["siren", "toggle"], tone: (s) => (s.state === "on" ? "alarm" : "") },
    switch: { icon: "hauflo:power", word: (s) => (s.state === "on" ? "вкл" : "выкл"), tap: ["switch", "toggle"] },
    // More domains of other integrations (MU): none of them needs a model — the state in words, a tap where the domain has one.
    remote: { icon: "mdi:remote", word: (s) => (s.state === "on" ? "вкл" : "выкл"), tap: ["remote", "toggle"] },
    update: { icon: "mdi:package-up", word: (s) => (s.state === "on" ? "есть обновление" : "актуально"), tap: null, on: (s) => s.state === "on", tone: (s) => (s.state === "on" ? "warn" : "") },
    lawn_mower: { icon: "mdi:robot-mower", word: (s) => ({ mowing: "косит", docked: "на базе", returning: "едет на базу", paused: "пауза", error: "ошибка" })[s.state] || s.state, tap: (s) => ["lawn_mower", s.state === "mowing" ? "dock" : "start_mowing"], on: (s) => s.state === "mowing", tone: (s) => (s.state === "error" ? "alarm" : "") },
    select: { icon: "mdi:format-list-bulleted", word: (s) => s.state, tap: null, on: () => false },
    input_select: { icon: "mdi:format-list-bulleted", word: (s) => s.state, tap: null, on: () => false },
    text: { icon: "mdi:form-textbox", word: (s) => s.state, tap: null, on: () => false },
    device_tracker: { icon: "hauflo:user", word: (s) => (s.state === "home" ? "дома" : s.state === "not_home" ? "не дома" : s.state), tap: null, on: (s) => s.state === "home" },
    input_boolean: { icon: "hauflo:switch", word: (s) => (s.state === "on" ? "вкл" : "выкл"), tap: ["input_boolean", "toggle"] },
    button: { icon: "mdi:gesture-tap-button", word: (s, name = "") => (/звон|bell/i.test(name) ? "позвонить" : /калит|ворот|двер|замок|gate|door/i.test(name) ? "открыть" : "нажать"), tap: ["button", "press"], on: () => false, action: true },
    binary_sensor: { icon: "hauflo:info", word: (s) => (OPENING.includes(s.attributes.device_class) ? (s.state === "on" ? "открыто" : "закрыто") : s.state === "on" ? "сработал" : "в норме"), tap: null, tone: (s) => (s.state === "on" && ["moisture", "smoke", "gas", "problem", "safety", "carbon_monoxide"].includes(s.attributes.device_class) ? "alarm" : ""), on: (s) => s.state === "on" && !["moisture", "smoke", "gas", "problem", "safety", "carbon_monoxide"].includes(s.attributes.device_class) },
    number: { icon: "hauflo:info", word: (s) => (num(s.state) === undefined ? "нет данных" : `${fmt(num(s.state), num(s.state) % 1 ? 1 : 0)} ${unitOf(s) || (num(s.attributes.max) === 100 && num(s.attributes.min) === 0 ? "%" : "")}`.trim()), tap: null, on: () => false },
    sensor: { icon: "hauflo:info", word: (s) => (num(s.state) === undefined ? s.state : `${fmt(num(s.state), num(s.state) % 1 ? 1 : 0)} ${unitOf(s)}`.trim()), tap: null, on: () => false, tone: (s) => (s.attributes.device_class === "battery" && num(s.state) !== undefined && num(s.state) < 20 ? "warn" : "") },
  };
  // --- hauflo-acs: every AC of the house in one card (persona round 3, Dubai: 14 rooms, «turn off every AC») ---
  // `kind: "floor_heating"` — the same stack for the floor heating (owner 27.10: «тёплые полы так же, стопкой,
  // по этажам»): what heats now, the floor's reading and setpoint; rows with `floor` under that floor's sub-header;
  // a row with `path` opens the room. Older cards ignore these keys and show it as a list of ACs.
  class Acs extends EHCard {
    constructor() {
      super();
      // The setpoint being changed on one row (owner 28.10: «быстро пройтись по всем зонам и изменить»): {id, v};
      // the values sent and not yet heard back, per thermostat.
      this._ed = null;
      this._pend = {};
      this._failed = null;
    }
    watched() {
      return (this._config.entities || []).flatMap((e) => (typeof e === "string" ? [e] : [e.entity, ...actIds(e.activity)]));
    }
    disconnectedCallback() {
      this.closeEdit();
    }
    css() {
      return `${TONE_CSS}.k{padding:16px}.hd{display:flex;align-items:center;gap:10px}.hd .caps{flex:1}.sum{font:400 13px Onest,sans-serif;color:var(--ink2);margin-top:2px}
.off{min-height:40px;padding:0 14px;border-radius:20px;font:500 13px Onest,sans-serif;color:var(--ink2);box-shadow:inset 0 0 0 1px var(--line)}
.rows{margin-top:10px;display:flex;flex-direction:column}.r{display:flex;align-items:center;gap:12px;min-height:52px;border-top:1px solid var(--hair);cursor:pointer}.r:first-child,.fl+.r{border-top:0}
.r ha-icon{--mdc-icon-size:20px;color:var(--ink3)}.r.toned ha-icon{color:var(--acT)}.r.toned.calm ha-icon{opacity:.8}.r .nm{flex:1;min-width:0;font:500 14px/1.25 Onest,sans-serif;white-space:normal;overflow-wrap:break-word;hyphens:auto}
.r .w{font:400 13px Onest,sans-serif;color:var(--ink2);white-space:nowrap}.r .t{display:flex;flex-direction:column;align-items:flex-end;justify-content:center;font:300 22px Onest,sans-serif;min-width:52px;text-align:right}.r .t b{font:300 22px/1.05 Onest,sans-serif}.r .t small{font:400 12px/1.2 Onest,sans-serif;color:var(--ink2);white-space:nowrap}.r.un{opacity:.5}.r .nm small .hot{color:var(--amberT)}.sw::before{content:"";display:inline-block;width:6px;height:6px;border-radius:3px;margin-right:6px;vertical-align:middle;background:var(--ink3)}.sw.heat::before{background:var(--amber)}.sw.cool::before{background:var(--ice)}.sw.idle::before{background:var(--mint)}.sw.dry::before,.sw.fan::before{background:var(--ac,var(--ink3))}.r .nm small .sw.cool.hot{color:var(--acT,var(--ice))}.r .nm small .sw:is(.dry,.fan).hot{color:var(--acT,var(--ink2))}
.r button.t{min-height:44px;padding:0 6px;margin-right:-6px;border-radius:14px;color:var(--ink);font-variant-numeric:tabular-nums}.r button.t{position:relative}.r button.t.hint::after{content:"±";position:absolute;top:3px;right:-1px;font:500 11px Onest,sans-serif;color:var(--ink3)}.r button.t:hover{background:var(--track)}.r .t.pend{color:var(--acT,var(--ink2))}
.r .ed{display:flex;align-items:center;gap:2px;margin-right:-6px}.r .ed button{width:44px;height:44px;border-radius:22px;background:var(--track);color:var(--ink);font:400 22px/1 Onest,sans-serif;display:grid;place-items:center}
.r .ed b{font:300 22px Onest,sans-serif;min-width:58px;text-align:center;color:var(--ink)}.r.toned .ed b{color:var(--acT)}.r .ed button:disabled{opacity:.35;cursor:default}
.r .nm small span{hyphens:manual}.r .nm small .sw{white-space:nowrap}.r .nm small .sw.fault{color:var(--alertT);font-weight:600}.r .nm small .sw.frost{color:var(--ice)}.r.fault ha-icon{color:var(--alertT)}
.r .nm small{display:block;font:400 12px/1.3 Onest,sans-serif;color:var(--ink2);margin-top:1px;white-space:normal;overflow-wrap:break-word;hyphens:auto}.r .nm small.err{color:var(--alertT)}.fl{font:500 11px/1.2 Onest,sans-serif;letter-spacing:.14em;text-transform:uppercase;color:var(--ink3);padding:14px 0 6px;border-top:1px solid var(--hair)}.fl:first-child{border-top:0;padding-top:4px}`;
    }
    /** A row's setpoint and how to send a new one: the climate's own; a climate kept between two values (heat_cool:
     * low / high) — the one of the mode it's in, the other kept (as the large card does it). */
    sp(s) {
      const a = s?.attributes || {};
      const step = Math.max(num(a.target_temp_step) || 0.5, 0.5);
      const min = num(a.min_temp) ?? 5;
      const max = num(a.max_temp) ?? 35;
      const t = num(a.temperature);
      if (t !== undefined) return { v: t, step, min, max, data: (v) => ({ temperature: v }) };
      const lo = num(a.target_temp_low);
      const hi = num(a.target_temp_high);
      if (lo === undefined || hi === undefined) return null;
      const cool = activeMode(s) === "cool";
      return { v: cool ? hi : lo, step, min, max, data: (v) => (cool ? { target_temp_low: Math.min(lo, v), target_temp_high: v } : { target_temp_low: v, target_temp_high: Math.max(hi, v) }) };
    }
    /** The title: the client's name, the integrator's, else by what's in the list (all ACs / heating only / climate). */
    defaultTitle(fh, items) {
      if (fh) return "Тёплые полы";
      const ms = items.map((e) => e.s.attributes.hvac_modes || []);
      if (ms.length && ms.every((m) => m.includes("dry") || m.includes("fan_only"))) return "Кондиционеры";
      if (ms.length && !ms.some((m) => m.some((x) => ["cool", "heat_cool", "auto", "dry", "fan_only"].includes(x)))) return "Отопление";
      return "Климат";
    }
    render() {
      const fh = this._config.kind === "floor_heating";
      const items = (this._config.entities || []).map((e) => (typeof e === "string" ? { entity: e } : e)).map((e) => ({ ...e, s: st(this._hass, e.entity) })).filter((e) => e.s);
      // «На связи» as the room card says it: a setpoint heard from the bus (integrator run 18: «4 из 4 работают» here,
      // «нет связи с термостатом» in the rooms).
      const known = (s) => !["unavailable", "unknown"].includes(s.state) && (num(s.attributes.temperature) !== undefined || num(s.attributes.target_temp_low) !== undefined);
      // A floor heats when its actuator says so — and it's colder than the goal (as the climate card says it).
      // What each thermostat DOES, not its mode (owner 28.10, Д13/Д16): the zone's valves / heat command (card config
      // `activity`) or HA's hvac_action; the mode only when nothing says what it does.
      const nowOf = (e) => (known(e.s) ? climateNow(this._hass, e.s, e.activity) : null);
      const heats = (e) => nowOf(e) === "heat";
      const onMode = items.filter((e) => known(e.s) && e.s.state !== "off");
      const offable = fh ? [] : onMode.filter((e) => canTurnOff(e.s));
      // Sub-headers by the building's floor when the rows say it and there's more than one.
      const floors = [...new Set(items.map((e) => e.floor).filter(Boolean))];
      // A value sent is shown at once and until the thermostat says it (or 10 s — then its own again).
      const now = Date.now();
      for (const [id, p] of Object.entries(this._pend)) {
        const sp = this.sp(st(this._hass, id));
        if (now - p.at > 10000 || (sp && Math.abs(sp.v - p.v) < 0.01 && p.sent)) delete this._pend[id];
      }
      if (this._ed && !items.some((e) => e.entity === this._ed.id && known(e.s) && e.s.state !== "off")) this._ed = null;
      // A small «±» by the number until the family has opened the stepper once: the number is the button.
      let hint = true;
      try {
        hint = localStorage.getItem("hauflo_stepper_seen") !== "1";
      } catch (e) {
        hint = true;
      }
      let last;
      // Names that come twice in this list (before the zone tells them apart).
      const baseName = (e) => trimSep(e.zone && String(e.name || "").endsWith(` · ${e.zone}`) ? e.name.slice(0, -e.zone.length - 3) : e.name || nameOf(e.s, e.entity)).toLowerCase();
      const dupNames = new Set(items.map(baseName).filter((n, i, all) => all.indexOf(n) !== i));
      const rows = items.map((e) => {
        const a = e.s.attributes;
        const cur = num(a.current_temperature);
        const sp = known(e.s) ? this.sp(e.s) : null;
        const pend = this._pend[e.entity];
        const target = pend ? pend.v : sp?.v;
        // The mode on every row — «Нагрев» next to «Нагревает» in one list read as two different things (integrator run 17).
        // «держит · 25,0°» read as nothing (final review 28.10): «поддерживает 25,0°».
        // Now: what it DOES («греет / охлаждает / ждёт»), the mode is small print («режим: нагрев») — not the other way round.
        const fault = faultOf(this, e.entity, fh);
        const frost = frostOf(e.s);
        const nw = fault ? "fault" : nowOf(e);
        const word = fault ? "Авария" : !known(e.s) ? (["unavailable", "unknown"].includes(e.s.state) ? "нет связи" : "нет нужной температуры") : frost ? "Защита от замерзания" : climateLine(nw, target, activeMode(e.s));
        const modeTxt = !fh && known(e.s) ? modeDiff(e.s.state, nw) : "";
        const head = floors.length > 1 && e.floor && e.floor !== last ? `<div class="fl">${esc(e.floor)}</div>` : "";
        last = e.floor;
        const lit = acting(nw) || nw === "idle";
        const editing = this._ed?.id === e.entity;
        const state = [word, modeTxt].filter(Boolean).join(" · ");
        // The colour is the mode's (owner 29.10): «Нагрев» at target stays amber, softer; grey only off / no link.
        const tn = known(e.s) ? climateTone(e.s, nw === "fault" ? nowOf(e) : nw) : "";
        const tcls = tn ? ` m-${tn} toned${toneCalm(tn, nw === "fault" ? nowOf(e) : nw) ? " calm" : ""}` : "";
        const failed = this._failed?.id === e.entity && now - this._failed.at < 6000;
        const zoned = e.zone && String(e.name || "").endsWith(` · ${e.zone}`);
        // The room/zone strip can leave «, Спальня» or an empty name: separators trimmed; a name equal to the zone shows the zone once.
        let name = trimSep(zoned ? e.name.slice(0, -e.zone.length - 3) : e.name || nameOf(e.s, e.entity));
        if (!name || (e.zone && name.toLowerCase() === String(e.zone).toLowerCase())) name = trimSep(e.zone) || nameOf(e.s, e.entity);
        // Under the name: the zone; while the stepper is open — the state (no room for both on a phone); a failed send.
        let zoneTxt = zoned && trimSep(e.zone).toLowerCase() !== name.toLowerCase() ? e.zone : "";
        // Two rows that read the same («Кухня», «Кухня»): the thermostat's own name tells them apart (round 3, item 7).
        if (!zoneTxt && dupNames.has(name.toLowerCase())) zoneTxt = (() => {
          const own = trimSep(nameOf(e.s, e.entity));
          const tail = /\(([^()]+)\)\s*$/.exec(own)?.[1];
          return tail && tail.toLowerCase() !== name.toLowerCase() ? tail : own.toLowerCase() !== name.toLowerCase() ? own : "";
        })();
        // Under the name: the zone, what it does (lit while it acts), the mode small; while the stepper is open — «цель» first.
        const curTxt = cur === undefined ? "—" : `${fmt(cur, 1)}°`;
        // The kind first (integrator review 29.10, defect 6): «Спальня» in the floor heating and «Спальня» in the climate
        // list read as one thermostat twice. «тёплый пол» also says the big number is the floor's.
        const kind = fh ? "тёплый пол" : "климат";
        const parts = [`<span>${kind}</span>`, zoneTxt && !editing ? `<span>${esc(zoneTxt)}</span>` : "", word ? `<span class="sw ${nw || ""} ${acting(nw) ? "hot" : ""}${frost && !fault ? " frost" : ""}">${esc(word)}</span>` : "", modeTxt ? `<span>${esc(modeTxt)}</span>` : ""].filter(Boolean);
        const sub = failed ? `<small class="err">не отправлено — нет связи с домом</small>` : parts.length ? `<small>${parts.join(" · ")}</small>` : "";
        const digits = (v) => fmt(v, sp && sp.step < 1 && v % 1 ? 1 : Number.isInteger(v) ? 0 : 1);
        // The setpoint: a tap on it opens − 26° + for this row only; the rest of the row opens the room / the dialog.
        const tail = editing
          ? `<div class="ed" data-ed="1"><button data-sd="-1" aria-label="Холоднее"${this._ed.v - sp.step < sp.min - 0.001 ? " disabled" : ""}>−</button><b aria-live="polite">${digits(this._ed.v)}°</b><button data-sd="1" aria-label="Теплее"${this._ed.v + sp.step > sp.max + 0.001 ? " disabled" : ""}>+</button></div>`
          : target === undefined || e.s.state === "off" || !sp || frost
            ? `<div class="t"><b>${curTxt}</b>${frost ? `<small>${esc("сейчас")}</small>` : ""}</div>`
            : `<button class="t${pend ? " pend" : ""}${hint ? " hint" : ""}" data-t="${esc(e.entity)}" aria-label="Изменить нужную температуру"><b>${digits(target)}°</b><small>Сейчас ${curTxt}</small></button>`;
        return `${head}<div class="r ${lit ? "on" : ""}${nw === "idle" ? " idle" : ""}${fault ? " fault" : ""} ${known(e.s) ? "" : "un"}${tcls}" data-i="${esc(e.entity)}"${e.path ? ` data-p="${esc(e.path)}"` : ""}><ha-icon icon="hauflo:${fh ? "floor-heat" : !fh && activeMode(e.s) === "heat" ? "radiator" : "ac"}"></ha-icon><div class="nm">${esc(name)}${sub}</div>${tail}</div>`;
      });
      // The header says what is going on, not how many have a mode on (Д16): «Греют: 2 · Охлаждают: 1» / «Все ждут» /
      // «Все выключены»; «Включено: N из M» only when nothing says what they do.
      const kinds = items.map(nowOf);
      const cnt = (k) => kinds.filter((x) => x === k).length;
      const faultsN = items.filter((e) => faultOf(this, e.entity, fh)).length;
      const parts = [faultsN ? `Авария: ${faultsN}` : "", cnt("heat") ? `Нагрев: ${cnt("heat")}` : "", cnt("cool") ? `Охлаждение: ${cnt("cool")}` : "", cnt("dry") + cnt("fan") ? `Работают: ${cnt("dry") + cnt("fan")}` : ""].filter(Boolean);
      const sum = parts.length
        ? parts.join(" · ")
        : !items.some((e) => known(e.s))
          ? "Нет связи"
          : kinds.includes("mode")
            ? `Включено: ${onMode.length} из ${items.length}`
            : kinds.includes("idle")
              ? "В норме"
              : "Все выключены";
      return `<div class="glass k${fh ? " fh" : ""}"><div class="hd"><div><span class="caps">${blockTitle(this, this.defaultTitle(fh, items))}</span><div class="sum">${sum}</div></div>${offable.length ? `<button class="off" data-off="1">${fh ? "Выключить тёплые полы" : "Выключить климат"}</button>` : ""}</div><div class="rows">${rows.join("")}</div></div>`;
    }
    redraw() {
      this._sig = "";
      this._update();
    }
    /** Closes the stepper (4 s idle, a tap elsewhere); a value not sent yet goes now. */
    closeEdit() {
      clearTimeout(this._edIdle);
      if (this._edSend) {
        clearTimeout(this._edSend);
        this._edSend = null;
        this.send();
      }
      if (this._outside) {
        document.removeEventListener("pointerdown", this._outside, true);
        this._outside = null;
      }
      if (!this._ed) return;
      this._ed = null;
      this.redraw();
    }
    idle() {
      clearTimeout(this._edIdle);
      this._edIdle = setTimeout(() => this.closeEdit(), 4000);
    }
    send() {
      const ed = this._lastEd;
      if (!ed) return;
      const sp = this.sp(st(this._hass, ed.id));
      if (!sp) return;
      const p = this._pend[ed.id];
      if (p) p.sent = true;
      call(this._hass, "climate", "set_temperature", { entity_id: ed.id, ...sp.data(ed.v) }).catch(() => {
        delete this._pend[ed.id];
        this._failed = { id: ed.id, at: Date.now() };
        this.redraw();
        setTimeout(() => this.redraw(), 6100);
      });
    }
    bind() {
      // A row opens THAT thermostat's control sheet over the page — the card its room shows, «Открыть в комнате» under it
      // (owner 29.10: a zone's row opened the top of its room, the floor heating below the fold). Apple Home's way.
      this.shadowRoot.querySelectorAll("[data-i]").forEach((r) =>
        r.addEventListener("click", (ev) => {
          if (ev.composedPath().some((n) => n.dataset && (n.dataset.t || n.dataset.ed))) return;
          this.closeEdit();
          const id = r.dataset.i;
          const row = (this._config.entities || []).map((e) => (typeof e === "string" ? { entity: e } : e)).find((e) => e.entity === id) || { entity: id };
          const found = climateCardOf(id, row.path);
          const fh = this._config.kind === "floor_heating";
          const cfg = { ...(found?.config || { entity: id, ...(fh ? { floor: true } : {}), ...(row.activity ? { activity: row.activity } : {}) }) };
          // Named as the row says it («Комната 101 · Вход»): in a sheet over the house's list the room is part of the name.
          if (row.name) cfg.label = fh && !/пол|floor/i.test(row.name) ? `${LANG === "ru" ? "Тёплый пол" : "Floor heating"} · ${row.name}` : row.name;
          haptic(this);
          openClimateSheet(this, cfg, row.path || found?.view || "");
        }),
      );
      this.shadowRoot.querySelectorAll("button[data-t]").forEach((b) =>
        b.addEventListener("click", (ev) => {
          ev.stopPropagation();
          const id = b.dataset.t;
          const sp = this.sp(st(this._hass, id));
          if (!sp) return;
          if (this._ed && this._ed.id !== id) this.closeEdit();
          haptic(this);
          // The «±» hint has done its job once the stepper was found (final review 28.10).
          try {
            localStorage.setItem("hauflo_stepper_seen", "1");
          } catch (e) {
            /* private window */
          }
          this._ed = { id, v: this._pend[id]?.v ?? sp.v };
          // One open at a time on the page: a tap anywhere else closes it.
          if (!this._outside) {
            this._outside = (e) => {
              if (!e.composedPath().some((n) => n.dataset && n.dataset.ed)) this.closeEdit();
            };
            document.addEventListener("pointerdown", this._outside, true);
          }
          this.idle();
          this.redraw();
        }),
      );
      this.shadowRoot.querySelectorAll("button[data-sd]").forEach((b) =>
        b.addEventListener("click", (ev) => {
          ev.stopPropagation();
          const ed = this._ed;
          const sp = ed && this.sp(st(this._hass, ed.id));
          if (!sp) return;
          const v = Math.round(Math.min(sp.max, Math.max(sp.min, ed.v + Number(b.dataset.sd) * sp.step)) / sp.step) * sp.step;
          ed.v = Number(v.toFixed(2));
          haptic(this, "selection");
          this._pend[ed.id] = { v: ed.v, at: Date.now() };
          this._lastEd = { ...ed };
          // Each tap shows at once; one call after the taps stop (~0.6 s), not one per tap.
          clearTimeout(this._edSend);
          this._edSend = setTimeout(() => {
            this._edSend = null;
            this.send();
          }, 600);
          this.idle();
          this.redraw();
        }),
      );
      this.shadowRoot.querySelector("[data-off]")?.addEventListener("click", (ev) => {
        const ob = ev.currentTarget;
        if (ob.dataset.arm !== "1") {
          ob.dataset.arm = "1";
          ob.dataset.lbl = ob.textContent;
          ob.textContent = tr("Точно? Выключить");
          ob.classList.add("arm");
          clearTimeout(this._armT);
          this._armT = setTimeout(() => {
            if (ob.isConnected && ob.dataset.arm === "1") {
              ob.dataset.arm = "";
              ob.textContent = ob.dataset.lbl;
              ob.classList.remove("arm");
            }
          }, 4000);
          return;
        }
        ob.dataset.arm = "";
        haptic(this);
        // Only those that can be switched off (a KNX AC with cool/heat only can't: HA answered with an error).
        const ids = (this._config.entities || []).map((e) => (typeof e === "string" ? e : e.entity)).filter((id) => { const s = st(this._hass, id); return s && !["off", "unavailable", "unknown"].includes(s.state) && canTurnOff(s); });
        if (ids.length) {
          const prev = ids.map((i) => [i, st(this._hass, i).state]);
          call(this._hass, "climate", "turn_off", { entity_id: ids });
          undoToast("Климат выключен", () => prev.forEach(([i, m]) => call(this._hass, "climate", "set_hvac_mode", { entity_id: i, hvac_mode: m })));
        }
      });
    }
  }

  class Devices extends EHCard {
    watched() {
      return (this._config.entities || []).map((e) => (typeof e === "string" ? e : e.entity));
    }
    css() {
      return `.k{padding:16px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px;margin-top:10px}
.p{display:flex;align-items:center;gap:10px;min-height:58px;padding:0 12px;border-radius:18px;box-shadow:inset 0 0 0 1px var(--hair);cursor:pointer;user-select:none;-webkit-user-select:none;min-width:0}
.p ha-icon{color:var(--ink3);--mdc-icon-size:20px;flex:0 0 auto}.p .tx{min-width:0}.nm{font:500 13px/1.25 Onest,sans-serif;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;overflow-wrap:break-word;hyphens:auto}.w{font:400 13px Onest,sans-serif;color:var(--ink2)}
.p.on{background:color-mix(in srgb,var(--mint) 16%,transparent);box-shadow:none}.p.on ha-icon{color:var(--mintT)}
.p.act{box-shadow:inset 0 0 0 1.5px color-mix(in srgb,var(--amber) 55%,transparent)}.p.act ha-icon{color:var(--amberT)}.p.act .w{color:var(--ink2)}.p.act.sent{background:color-mix(in srgb,var(--amber) 14%,transparent)}.p.act:active{transform:scale(.97)}
.p.warn{background:color-mix(in srgb,var(--amber) 18%,transparent);box-shadow:none}.p.warn ha-icon,.p.warn .w{color:var(--amberT)}
.p.alarm{background:color-mix(in srgb,var(--alert) 16%,transparent);box-shadow:inset 0 0 0 1.5px color-mix(in srgb,var(--alert) 55%,transparent)}.p.alarm ha-icon,.p.alarm .w{color:var(--alertT);font-weight:600}.p:active{transform:scale(.97)}.un{opacity:.45}`;
    }
    render() {
      // What needs attention first: alarm → warning → working → resting (designer review 25.10); the list order otherwise.
      const rank = (e) => {
        const d = DEV[e.entity.split(".")[0]] || {};
        const tone = d.tone ? d.tone(e.s) : "";
        return tone === "alarm" ? 0 : tone === "warn" ? 1 : (d.on ? d.on(e.s) : e.s.state === "on") ? 2 : 3;
      };
      const items = (this._config.entities || []).map((e) => (typeof e === "string" ? { entity: e } : e)).map((e, i) => ({ ...e, s: st(this._hass, e.entity), i })).filter((e) => e.s).sort((a, b) => (orderHas(this) ? 0 : rank(a) - rank(b)) || a.i - b.i); // the house's own order wins
      this.hideCard(!items.length);
      if (!items.length) return "";
      return `<div class="glass k">${this._config.title ? `<span class="caps">${esc(this._config.title)}</span>` : ""}<div class="grid">${items
        .map((e) => {
          const d = DEV[e.entity.split(".")[0]] || { icon: "hauflo:info", word: (s) => s.state, tap: null };
          const tone = d.tone ? d.tone(e.s) : "";
          const on = !tone && (d.on ? d.on(e.s) : e.s.state === "on");
          // A button / scene / script has no on-off state: «unknown» = never pressed yet, not offline (owner 28.10:
          // damper presets read «нет связи» until pressed). Only «unavailable» is offline; a press flashes «отправлено».
          const un = d.action ? e.s.state === "unavailable" : nd(e.s);
          const sent = d.action && (this._sent?.[e.entity] || 0) > Date.now();
          return `<div class="p ${on ? "on" : ""} ${tone} ${d.action ? "act" : ""} ${sent ? "sent" : ""} ${un ? "un" : ""}" data-e="${esc(e.entity)}"><ha-icon icon="${esc(e.icon || (/звон|bell/i.test(e.name || nameOf(e.s, e.entity)) ? "hauflo:doorbell" : sent ? "hauflo:check" : d.icon))}"></ha-icon><div class="tx"><div class="nm">${esc(e.name || nameOf(e.s, e.entity))}</div><div class="w">${esc(un ? "нет связи" : sent ? "отправлено" : d.word(e.s, e.name || nameOf(e.s, e.entity)))}</div></div></div>`;
        })
        .join("")}</div></div>`;
    }
    bind() {
      this.shadowRoot.querySelectorAll(".p").forEach((el) => {
        const id = el.dataset.e;
        let t;
        let held = false;
        let at = null;
        el.addEventListener("pointerdown", (ev) => {
          held = false;
          at = [ev.clientX, ev.clientY];
          t = setTimeout(() => {
            held = true;
            moreInfo(this, id);
          }, 500);
        });
        el.addEventListener("pointermove", (ev) => at && Math.hypot(ev.clientX - at[0], ev.clientY - at[1]) > 8 && (clearTimeout(t), (at = null)));
        el.addEventListener("pointercancel", () => (clearTimeout(t), (at = null)));
        el.addEventListener("pointerup", () => {
          clearTimeout(t);
          const still = at !== null;
          at = null;
          if (held || !still || !GST.ok) return;
          const s = st(this._hass, id);
          const d = DEV[id.split(".")[0]];
          const tap = d && (typeof d.tap === "function" ? d.tap(s) : d.tap);
          if (!tap) return moreInfo(this, id);
          haptic(this);
          call(this._hass, tap[0], tap[1], { entity_id: id });
          if (d.action) {
            (this._sent ??= {})[id] = Date.now() + 1500;
            this._sig = "";
            this._update();
            setTimeout(() => ((this._sig = ""), this._update()), 1600);
          }
        });
        el.addEventListener("pointerleave", () => clearTimeout(t));
        el.addEventListener("contextmenu", (ev) => ev.preventDefault());
      });
    }
    getGridOptions() {
      return { columns: 12, rows: "auto" };
    }
  }

  const cards = [
    ["hauflo-hero", Hero, "Hauflo · Дом", "Температура дома, свет и охрана одной цифрой"],
    ["hauflo-room", Room, "Hauflo · Комната", "Комната светится своим состоянием; тап — её страница, удержание — свет"],
    ["hauflo-scenes", Scenes, "Hauflo · Сценарии", "Сценарий сейчас и остальные в одно касание"],
    ["hauflo-climate", Climate, "Hauflo · Климат", "Уставка крупно, режимы"],
    ["hauflo-sliders", Sliders, "Hauflo · Ползунки", "Свет, шторы, розетки высокими ползунками"],
    ["hauflo-alert", Alert, "Hauflo · Авария", "Авария на весь экран, действие свайпом"],
    ["hauflo-lights", Lights, "Hauflo · Пульт света", "Много светильников: общий выключатель и диммер, зоны, капсулы"],
    ["hauflo-covers", Covers, "Hauflo · Пульт штор", "Много штор: все разом, зоны, положение"],
    ["hauflo-weather", Weather, "Hauflo · Погода", "Сейчас и на несколько дней"],
    ["hauflo-air", Air, "Hauflo · Воздух", "PM2.5 на шкале, PM10, CO₂, влажность"],
    ["hauflo-media", Media, "Hauflo · Музыка и видео", "Что играет, обложка, управление, громкость"],
    ["hauflo-graph", Graph, "Hauflo · График", "Значение за последние часы"],
    ["hauflo-auto", Auto, "Hauflo · Автоматизации", "Вкл/выкл, запуск, когда сработала"],
    ["hauflo-status", Status, "Hauflo · Диспетчеризация", "Инженерные системы: работает, авария, нет связи"],
    ["hauflo-security", Security, "Hauflo · Охрана", "Охрана, замки, двери и окна"],
    ["hauflo-camera", Camera, "Hauflo · Камера", "Картинка с камеры, тап — трансляция"],
    ["hauflo-acs", Acs, "Hauflo · Кондиционеры", "Все кондиционеры дома: какие работают, температура, выключить все"],
    ["hauflo-area", Area, "Hauflo · Комната HA", "Всё, что Home Assistant отнёс к комнате, — в стиле Hauflo"],
    ["hauflo-leaks", Leaks, "Hauflo · Защита от протечек", "Датчики протечки и кран любой интеграции: всё в порядке или где протечка"],
    ["hauflo-devices", Devices, "Hauflo · Устройства", "Вентиляторы, клапаны, замки, пылесосы, бойлеры…"],
  ];
  // --- a dashboard with a fixed light or dark Atelier theme: HA paints only the view, the header follows here
  // (owner 2026-10-25: «Вилла Юг · тёмная» had a light header).
  (() => {
    const HEAD = {
      dark: ".header,.toolbar{background:#08090A!important;color:#F4F2EE!important}ha-tab-group,ha-tab-group-tab,ha-icon-button,ha-menu-button{color:#F4F2EE!important;--primary-text-color:#F4F2EE;--secondary-text-color:rgba(244,242,238,.64)}",
      light: ".header,.toolbar{background:#EFECE6!important;color:#121212!important}ha-tab-group,ha-tab-group-tab,ha-icon-button,ha-menu-button{color:#121212!important;--primary-text-color:#121212;--secondary-text-color:rgba(18,18,18,.64)}",
    };
    // The floor tabs in Atelier's type: quiet, the active one ink with a short warm mark (owner 2026-10-25: «меню
    // переключений не в тему»). Only on dashboards whose theme is Atelier.
    const TABS = `ha-tab-group-tab{font:500 15px Onest,var(--ha-font-family-body),sans-serif!important;letter-spacing:-.01em;color:var(--eh-ink2,var(--secondary-text-color))!important;position:relative}
ha-tab-group-tab[active]{color:var(--eh-ink,var(--primary-text-color))!important}
ha-tab-group-tab[active]::after{content:"";position:absolute;left:50%;bottom:7px;width:18px;height:3px;border-radius:2px;background:var(--eh-amber,#E8901F);transform:translateX(-50%);box-shadow:0 0 10px var(--eh-amber-glow,transparent)}
ha-tab-group{--ha-tab-indicator-color:transparent;--ha-tab-track-color:transparent;--wa-color-neutral-on-quiet:var(--eh-ink,var(--primary-text-color))!important}
ha-tab-group-tab:hover,ha-tab-group-tab:focus-visible{color:var(--eh-ink,var(--primary-text-color))!important}`;
    setInterval(() => {
      const panel = document.querySelector("home-assistant")?.shadowRoot?.querySelector("home-assistant-main")?.shadowRoot?.querySelector("ha-drawer partial-panel-resolver ha-panel-lovelace");
      const rs = panel?.shadowRoot?.querySelector("hui-root")?.shadowRoot;
      const views = panel?.lovelace?.config?.views;
      if (!rs || !views) return;
      const cur = location.pathname.split("/")[2];
      const theme = (views.find((v, i) => (v.path ?? String(i)) === cur) || views[0])?.theme || "";
      const mode = /тёмная|dark/i.test(theme) ? "dark" : /светлая|light/i.test(theme) ? "light" : "";
      let el = rs.querySelector("style[data-hauflo-head]");
      if (!el) {
        el = document.createElement("style");
        el.dataset.haufloHead = "1";
        rs.appendChild(el);
      }
      const atelier = /Hauflo/i.test(theme) || /Hauflo/i.test(document.querySelector("home-assistant")?.hass?.selectedTheme?.theme || document.querySelector("home-assistant")?.hass?.themes?.theme || "");
      // The sidebar follows a fixed theme too (persona review 25.10: a light menu beside a dark dashboard).
      const sb = document.querySelector("home-assistant")?.shadowRoot?.querySelector("home-assistant-main")?.shadowRoot?.querySelector("ha-sidebar")?.shadowRoot;
      if (sb) {
        let se = sb.querySelector("style[data-hauflo-side]");
        if (!se) {
          se = document.createElement("style");
          se.dataset.haufloSide = "1";
          sb.appendChild(se);
        }
        const side = mode === "dark"
          ? ":host{--sidebar-background-color:#08090A;--sidebar-text-color:#F4F2EE;--sidebar-icon-color:rgba(244,242,238,.64);--sidebar-selected-text-color:#F4F2EE;--sidebar-selected-icon-color:#F4F2EE;--primary-text-color:#F4F2EE;--secondary-text-color:rgba(244,242,238,.64);--divider-color:rgba(255,255,255,.08);background:#08090A!important;color:#F4F2EE}"
          : mode === "light"
            ? ":host{--sidebar-background-color:#EFECE6;--sidebar-text-color:#121212;--sidebar-icon-color:rgba(18,18,18,.64);--sidebar-selected-text-color:#121212;--sidebar-selected-icon-color:#121212;background:#EFECE6!important}"
            : "";
        if (se.textContent !== side) se.textContent = side;
      }
      // «Ещё крупнее» scales the tabs too (family review round 3); on a phone the tabs are tighter and the last one has room
      // past HA's scroll arrow («Сервис» was cut by it).
      const hf = /^\/hauflo-/.test(location.pathname)
        ? "ha-tab-group-tab{font-size:calc(15px*min(var(--hf-ts,1),1.4))!important}ha-tab-group-tab:last-of-type{margin-inline-end:36px}@media (max-width:600px){ha-tab-group-tab::part(base){padding-inline:10px}}ha-menu-button,ha-icon-button,.hauflo-gear{--mdc-icon-size:calc(24px*min(var(--hf-ts,1),1.3))}"
        : "";
      const css = hf + (atelier ? TABS : "") + (mode ? HEAD[mode].replace("ha-tab-group,ha-tab-group-tab,", "") + (mode === "dark" ? "ha-tab-group{--wa-color-neutral-on-quiet:#F4F2EE!important}ha-tab-group-tab{color:rgba(244,242,238,.64)!important}ha-tab-group-tab[active],ha-tab-group-tab:hover{color:#F4F2EE!important}" : "ha-tab-group{--wa-color-neutral-on-quiet:#121212!important}ha-tab-group-tab{color:rgba(18,18,18,.64)!important}ha-tab-group-tab[active],ha-tab-group-tab:hover{color:#121212!important}") : "");
      if (el.textContent !== css) el.textContent = css;
    }, 400);
  })();

  // --- «Автоматизации» (owner 28.10; docs/automations.md): the client's own rules — sunrise / sunset ± minutes, a time,
  // a phone's alarm clock — over Hauflo's covers, lights and scenes. Every rule is a real HA automation the agent writes
  // (/api/hauflo/automations); this page shows what runs next, proposes ready rules for this house, edits them.
  const AUTO = (() => {
    const W = {
      ru: {
        title: "Автоматизации", close: "Закрыть", svc: "Обслуживает дом", ro: "Детский профиль: настройки дома меняют взрослые. Свет, шторы и температура — ваши.",
        sub: "Шторы и свет сами — по солнцу, по времени, по будильнику. Работают в Home Assistant дома, даже без интернета.",
        upcoming: "Дальше",
        sunLine: (r, s) => `Следующий рассвет — ${r}, закат — ${s}.`,
        noSun: "Рассвет и закат появятся, когда будет известно, где дом.",
        nothing: "Пока ничего не запланировано.",
        in: (x) => `через ${x}`, now: "сейчас", off: "выключено", iphone: "по будильнику iPhone", noTime: "время неизвестно",
        min: "мин", h: "ч", d: "д", today: "сегодня", tomorrow: "завтра", at: "в",
        wd: ["пн", "вт", "ср", "чт", "пт", "сб", "вс"],
        every: "каждый день", workdays: "по будням", weekend: "по выходным",
        ev: { sunrise: "Рассвет", sunset: "Закат", time: "Время", alarm: "Будильник" },
        evGen: { sunrise: "рассвета", sunset: "заката", alarm: "будильника" },
        atTime: (t) => `В ${t}`,
        alarmOf: (p) => `Будильник ${p}`,
        offLong: (ev, o) => (o < 0 ? `за ${dur(-o, "ru")} до ${W.ru.evGen[ev]}` : o > 0 ? `через ${dur(o, "ru")} после ${W.ru.evGen[ev]}` : ev === "alarm" ? "со звонком будильника" : `ровно на ${ev === "sunset" ? "закате" : "рассвете"}`),
        act: {
          open: (n) => `Открыть ${n}`, close: (n) => `Закрыть ${n}`, position: (n, v) => `${n} на ${v} %`, on: (n, v) => (v ? `${n} ${v} %` : `Включить ${n}`),
          off: (n) => `Выключить ${n}`, ramp: (n, v, m) => `${n} плавно до ${v} % за ${dur(m, "ru")}`, scene: (n) => `Сцена «${n}»`, room_off: (n) => `Весь свет выкл: ${n}`, room_offs: (l) => `Весь свет выкл: ${l}`,
        },
        more: (n) => ` и ещё ${n}`,
        mine: "Ваши правила", empty: "Пока ни одного — возьмите готовое выше или добавьте своё.", add: "Добавить",
        sugg: "Готовые для этого дома", addOne: "Добавить", added: "Добавлено и включено. Нажмите на правило, чтобы поправить.",
        s: {
          sunset_covers: ["Закрывать шторы на закате", (rooms, n) => `Каждый день · ${n} · ${rooms}`],
          wake_covers: ["Открывать шторы в спальне по будильнику", (src) => `Спальня · ${src}`],
          wake_light: ["Свет-будильник: плавно 0 → 25 %", (src) => `Спальня · ${src}`],
          night_off: ["Выключать весь свет в 01:00", () => "Каждый день · все комнаты"],
          corridor_night: ["Свет в коридоре ночью на 10 %", (r) => `Каждый день в 23:00 · ${r}`],
        },
        srcAndroid: (p) => `за 20 мин до будильника ${p}`, srcIphone: "с будильником iPhone",
        newRule: "Новое правило", rule: "Правило", back: "Назад",
        when: "Когда", days: "Дни", what: "Что сделать", addAction: "Добавить действие…",
        grp: { covers: "Шторы по одной", coverRooms: "Все шторы комнаты", lights: "Свет", scenes: "Сцены", rooms: "Весь свет в комнате" },
        do: { open: "Открыть", close: "Закрыть", position: "Положение", on: "Включить", off: "Выключить", ramp: "Плавно", sceneOn: "Включить" },
        bright: "Яркость", upTo: "до", over: "за", remove: "Убрать",
        phone: "Чей будильник", iphoneOpt: "iPhone (через «Команды»)",
        iphoneTitle: "Один раз на iPhone — 2 минуты",
        iphoneSteps: [
          "Откройте на iPhone приложение «Команды».",
          "Внизу нажмите «Автоматизация», затем «+».",
          "Выберите «Будильник» → «Выключен» (или «Сработал»), будильник — «Любой». Включите «Запускать сразу» и нажмите «Далее».",
          "«Новая пустая автоматизация» → «Добавить действие» → найдите «Home Assistant» → «Отправить событие».",
          "В поле «Событие» вставьте этот код:",
          "Нажмите «Готово». Утром выключите будильник — шторы и свет сработают сами. На iPhone нужно приложение Home Assistant, вошедшее в этот дом.",
        ],
        share: "Отправить инструкцию", shareTitle: "Будильник iPhone → дом", shared: "Инструкция скопирована — вставьте её в сообщение.",
        iphoneLater: "Сохраните правило — здесь появится код для «Команд».",
        androidSteps: ["Приложение Home Assistant → Настройки → Приложение → Управление датчиками → «Следующий будильник» → включить.", "Поставьте будильник на телефоне (приложение «Часы»).", "Нажмите «Проверить» — Hauflo покажет, какой будильник он видит."],
        chk: "Проверить", chkTitle: "Что видит Hauflo",
        chkAndroidOk: (t) => `Hauflo видит ближайший будильник: ${t}. Всё работает.`,
        chkAndroidNone: "Датчик есть, но будильника не видно — сделайте шаг 2: поставьте будильник на телефоне и откройте приложение Home Assistant. Потом нажмите «Проверить» ещё раз.",
        chkAndroidOff: "Датчик недоступен (телефон выключен или приложение давно не открывали) — откройте приложение Home Assistant на телефоне и проверьте ещё раз.",
        chkAndroidMissing: "Датчика будильника ещё нет — сделайте шаг 1: включите «Следующий будильник» в приложении Home Assistant.",
        chkIphoneSave: "Правило ещё не сохранено — сохраните его, и появится код для «Команд» (шаг 5).",
        chkIphoneOk: (t) => `Команда с iPhone приходила: ${t}. Всё работает.`,
        chkIphoneNo: "Пока ни разу не приходила команда с iPhone — сделайте шаги 1–6, потом остановите будильник и нажмите «Проверить» ещё раз. Быстрая проверка: в «Командах» откройте эту автоматизацию и нажмите «Запустить».",
        chkIphoneOff: "Правило выключено в Home Assistant — включите его в списке правил.",
        copy: "Скопировать", copied: "Скопировано",
        willRun: (x) => `Сработает: ${x}`, willNot: "Сработает: по будильнику iPhone",
        save: "Сохранить", cancel: "Отмена", saved: "Сохранено.", deleted: "Правило удалено.", del: "Удалить", delAsk: "Удалить это правило?", delYes: "Да, удалить",
        run: "Проверить сейчас", runAsk: (x) => `Сейчас выполнится: ${x}. Продолжить?`, runYes: "Выполнить", ran: "Выполнено — смотрите на шторы и свет.",
        rampNote: "Плавный свет займёт заданные минуты.",
        needLoc: "Место дома не задано — время рассвета и заката будет приблизительным.", needLocAdmin: "Укажите, где дом, в блоке «Где дом» на этой странице.", needLocUser: "Попросите администратора указать место дома в Home Assistant.", approx: "время приблизительное", needAction: "Добавьте хотя бы одно действие.", needDay: "Выберите хотя бы один день.",
        err: "Не получилось: ", errNet: "Нет связи с домом — попробуйте ещё раз.", noAgent: "Нужен агент Hauflo новее — обновите его в HACS.", agentGone: "Агент Hauflo не установлен — без него правила и «Нет дома» не работают.", locWarn: "Сохранено, но место дома не задано — время рассвета и заката приблизительное.",
        last: (t) => `Последний раз: ${t}`, never: "ещё не срабатывало", missing: "нет в Home Assistant — сохраните заново",
        changedTag: "изменено в Home Assistant",
        changedNote: "Это правило поменяли в редакторе Home Assistant — Hauflo его не трогает: включать, выключать и проверять можно, править — там. Или верните как было в Hauflo (правка из Home Assistant пропадёт).",
        restore: "Вернуть как в Hauflo", restoreAsk: "Вернуть правило как было в Hauflo? Правка из Home Assistant пропадёт.", restoreYes: "Да, вернуть",
        coverRooms: (n) => `Все шторы: ${n}`, split: "По отдельности", review: "Проверьте и нажмите «Сохранить».",
        roomCovers: { open: (l) => `Открыть шторы: ${l}`, close: (l) => `Закрыть шторы: ${l}`, position: (l, v) => `Шторы на ${v} %: ${l}` },
        moreRooms: (n) => ` и ещё ${n} ${n % 10 === 1 && n % 100 !== 11 ? "комната" : [2, 3, 4].includes(n % 10) && ![12, 13, 14].includes(n % 100) ? "комнаты" : "комнат"}`,
        blindsN: (n) => `${n} ${n % 10 === 1 && n % 100 !== 11 ? "штора" : [2, 3, 4].includes(n % 10) && ![12, 13, 14].includes(n % 100) ? "шторы" : "штор"}`,
        findCity: "Найти город", cityPh: "Город или адрес", find: "Найти", noCity: "Ничего не нашлось — попробуйте иначе.", notThis: "Не то — найти иначе",
        manualLink: "Ввести координаты вручную", llPh: "например, 55.75, 37.62", or: "или",
        notAdmin: "Где дом, задаёт владелец Home Assistant (администратор) — попросите его открыть эту страницу.",
        tzAsk: (z, cur) => `Часовой пояс дома — ${z}, а в Home Assistant стоит ${cur}. Из-за этого рассвет и закат показываются не в то время.`, tzFix: (z) => `Поставить ${z}`, tzDone: "Часовой пояс исправлен.", tzNote: (z) => `Время — по часам дома (${z}).`,
        loadFail: "Не загрузилось — обновите страницу.", reload: "Обновить",
        loc: "Где дом", locWhy: "Чтобы знать время рассвета и заката. В Home Assistant сохраняются только координаты.",
        detect: "Определить", detecting: "Ищем…", detected: (c) => `Похоже, дом здесь${c ? `: ${c}` : ""}.`, confirm: "Да, дом здесь",
        byPhone: "По телефону", manual: "Ввести вручную", lat: "Широта", lon: "Долгота", saveLoc: "Сохранить место",
        locNow: (a, b) => `Дом: ${a}, ${b}`, change: "Изменить", locSaved: "Место сохранено.",
        geoNo: "Телефон не дал координаты (нужен https и разрешение) — введите вручную.", yamlLoc: "Место задано в configuration.yaml — его меняет интегратор.",
        detectFail: "По интернету не определилось — попробуйте по телефону или вручную.", mapNote: "Карта загружается с OpenStreetMap · © OpenStreetMap",
        adminLoc: "Место дома задаёт администратор Home Assistant.",
        notPlain: "Hauflo не меняет automations.yaml: в нём комментарии, !include или ручное оформление. Правило добавит интегратор.",
        on: "Включено", offShort: "Выключено",
        p: {
          title: "Нет дома — имитация присутствия",
          sub: "Свет и шторы как будто вы дома: вечером свет загорается и гаснет, шторы закрываются — как обычно у вас, каждый вечер по-новому.",
          how: (n, j) => `Учится на двух неделях истории дома: где и когда вечером горит свет, когда закрываются шторы. Свет — только в темноте и не больше чем в ${n} комнатах сразу, время каждый раз ±${j} мин. То, что кто-то дома поменял за последние 10 минут, не трогает.`,
          sunHow: "Шторы откроются через 30 мин после рассвета и закроются на закате; вечером спокойный свет в 1–2 комнатах, где он горит чаще всего.",
          since: (d) => `Включено ${d}`, untilLine: (d) => `выключится само после ${d}`, untilNone: "пока не выключите", pick: "Выбрать дату", askOn: "Точно включить? Свет и шторы начнут жить сами каждый вечер.", askOff: "Точно выключить? Дом перестанет выглядеть жилым.", yesOn: "Да, включить", yesOff: "Да, выключить", no: "Отмена",
          what: "Что участвует", lights: "Свет", covers: "Шторы", mode: "Как", learned: "По привычкам семьи", sun: "По солнцу",
          until: "Вернёмся", noDate: "Без даты",
          partCovers: "Работает частично: только шторы", partLights: "Работает частично: только свет", partNone: "Сегодня ничего не включится",
          whyFew: (n, min) => `Сегодня: свет не включится — мало истории (${n} ${n % 10 === 1 && n % 100 !== 11 ? "вечер" : [2, 3, 4].includes(n % 10) && ![12, 13, 14].includes(n % 100) ? "вечера" : "вечеров"} за две недели, нужно ${min}). «По солнцу» включает свет без истории.`,
          whyLoc: "Сегодня: свет не включится — не указано, где дом (без этого не узнать, когда стемнеет).",
          whyLate: "Сегодня: вечер уже прошёл — свет включится завтра вечером.",
          whyNone: "Сегодня: по привычкам семьи свет не выпал — так бывает и в обычные вечера.",
          whyCovers: "Шторы не сдвинутся: нет ни истории штор, ни места дома, чтобы двигать их по солнцу.",
          rooms: "Комнаты", roomsHint: "Нажмите на комнату, чтобы она не участвовала (фасад, гостевая).",
          today: "Сегодня", nothing: "сегодня вечером свет по привычкам не выпал",
          blinds: (o, c) => [o ? `шторы откроются в ${o}` : "", c ? `${o ? "" : "шторы "}закроются в ${c}` : ""].filter(Boolean).join(", "),
          bySun: "по солнцу",
          few: (n, min) => `Пока мало истории: вечером свет горел ${n} ${n % 10 === 1 && n % 100 !== 11 ? "раз" : [2, 3, 4].includes(n % 10) && ![12, 13, 14].includes(n % 100) ? "раза" : "раз"} за две недели, нужно хотя бы ${min}. Свет не включится; шторы — по солнцу.`,
          useSun: "Включить «по солнцу»",
          noLoc: "Свет включается, только когда стемнеет, — сначала укажите, где дом.",
          noRec: "В Home Assistant выключена история (recorder) — учиться не на чем.",
          noDev: "Нет света и штор Hauflo, которые можно включать.",
          log: "Что сделано", all: "Показать все", less: "Свернуть",
          skip: { manual: "пропущено: меняли вручную", offline: "пропущено: нет связи", gone: "пропущено: устройства нет", error: "не получилось" },
          sys: { on: "Имитация включена", off: "Имитация выключена", auto_off: "Выключилась сама — дата прошла" },
        },
        g: {
          title: "Гостевой доступ",
          sub: "Ссылка или QR для гостя — без входа и регистрации: свет, шторы и климат выбранных комнат на время. Замки, ворота, охрана, камеры и настройки гостю недоступны.",
          invite: "Пригласить гостя", who: "Для кого", whoPh: "Например, Иван", rooms: "Комнаты", roomsHint: "Гость увидит только свет, шторы и климат Hauflo в этих комнатах.",
          howLong: "На сколько", hours: { 3: "3 часа", 24: "Сутки", 72: "3 дня", 168: "Неделя" }, create: "Создать ссылку", cancel: "Отмена",
          ready: "Ссылка готова", once: "Показывается один раз — отправьте её гостю сейчас. Потерялась — закройте и создайте новую.",
          copy: "Скопировать", copied: "Скопировано", share: "Поделиться", done: "Готово",
          active: "Действуют", ended: "Закончились", none: "Сейчас гостевых ссылок нет.", guest: "Гость",
          until: (d) => `до ${d}`, devices: (n, m) => (n ? `открыта на ${n} из ${m} устройств` : `Ещё не открывали · можно открыть на ${m} ${m % 10 === 1 && m % 100 !== 11 ? "устройстве" : "устройствах"}`), close: "Отозвать доступ", closeAll: "Отозвать все ссылки", closeAllAsk: "Точно? Отозвать все", 
          revoked: "закрыта", expired: "истекла",
          noRooms: "Нет комнат со светом, шторами или климатом Hauflo — гостю нечего дать.",
          cant: "Гостевую ссылку создают взрослые — у вас детский профиль.",
          ask: "Попросить администратора", askFor: { "": "Без срока", "на вечер": "На вечер", "на выходные": "На выходные" },
          asked: "Готово: администратор получил просьбу в Home Assistant и на телефон.", askedNoPhone: "Готово: просьба — в уведомлениях Home Assistant у администратора.", askSoon: "Вы уже попросили — администратор увидит просьбу.",
          log: "Журнал гостей", all: "Показать все", less: "Свернуть",
          sys: { issued: "создана ссылка", revoked: "ссылка закрыта", revoked_all: "все ссылки закрыты", opened: "ссылку открыли на новом устройстве", device_refused: "отказ: ссылку открыли на лишнем устройстве" },
          act: { on: "включить", off: "выключить", open: "открыть", close: "закрыть", stop: "стоп", position: "положение", temperature: "температура" },
          failed: "не получилось",
        },
      },
      en: {
        title: "Automations", close: "Close", svc: "Looks after the house", ro: "Child profile: the home's settings are changed by the adults. Lights, blinds and temperature are yours.",
        sub: "Blinds and lights by themselves — by the sun, by the clock, by your alarm. They run in Home Assistant at home, even offline.",
        upcoming: "Next",
        sunLine: (r, s) => `Next sunrise — ${r}, sunset — ${s}.`,
        noSun: "Sunrise and sunset show up once the house's place is known.",
        nothing: "Nothing planned yet.",
        in: (x) => `in ${x}`, now: "now", off: "off", iphone: "with the iPhone alarm", noTime: "time unknown",
        min: "min", h: "h", d: "d", today: "today", tomorrow: "tomorrow", at: "at",
        wd: ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
        every: "every day", workdays: "on weekdays", weekend: "at weekends",
        ev: { sunrise: "Sunrise", sunset: "Sunset", time: "Time", alarm: "Alarm" },
        evGen: { sunrise: "sunrise", sunset: "sunset", alarm: "the alarm" },
        atTime: (t) => `At ${t}`,
        alarmOf: (p) => `Alarm ${p}`,
        offLong: (ev, o) => (o < 0 ? `${dur(-o, "en")} before ${W.en.evGen[ev]}` : o > 0 ? `${dur(o, "en")} after ${W.en.evGen[ev]}` : ev === "alarm" ? "as the alarm rings" : `right at ${ev}`),
        act: {
          open: (n) => `Open ${n}`, close: (n) => `Close ${n}`, position: (n, v) => `${n} to ${v} %`, on: (n, v) => (v ? `${n} ${v} %` : `Turn on ${n}`),
          off: (n) => `Turn off ${n}`, ramp: (n, v, m) => `${n} slowly up to ${v} % over ${dur(m, "en")}`, scene: (n) => `Scene «${n}»`, room_off: (n) => `All lights off: ${n}`, room_offs: (l) => `All lights off: ${l}`,
        },
        more: (n) => ` and ${n} more`,
        mine: "Your rules", empty: "None yet — take a ready one above or add your own.", add: "Add",
        sugg: "Ready for this house", addOne: "Add", added: "Added and on. Tap the rule to adjust it.",
        s: {
          sunset_covers: ["Close the blinds at sunset", (rooms, n) => `Every day · ${n} · ${rooms}`],
          wake_covers: ["Open the bedroom blinds with the alarm", (src) => `Bedroom · ${src}`],
          wake_light: ["Wake-up light: slowly 0 → 25 %", (src) => `Bedroom · ${src}`],
          night_off: ["All lights off at 01:00", () => "Every day · all rooms"],
          corridor_night: ["Hall light at night at 10 %", (r) => `Every day at 23:00 · ${r}`],
        },
        srcAndroid: (p) => `20 min before the alarm on ${p}`, srcIphone: "with the iPhone alarm",
        newRule: "New rule", rule: "Rule", back: "Back",
        when: "When", days: "Days", what: "What to do", addAction: "Add an action…",
        grp: { covers: "Blinds one by one", coverRooms: "All blinds of a room", lights: "Lights", scenes: "Scenes", rooms: "All lights of a room" },
        do: { open: "Open", close: "Close", position: "Position", on: "On", off: "Off", ramp: "Slowly", sceneOn: "Turn on" },
        bright: "Brightness", upTo: "up to", over: "over", remove: "Remove",
        phone: "Whose alarm", iphoneOpt: "iPhone (via Shortcuts)",
        iphoneTitle: "Once on the iPhone — 2 minutes",
        iphoneSteps: [
          "Open the Shortcuts app on the iPhone.",
          "Tap «Automation» at the bottom, then «+».",
          "Choose «Alarm» → «Is Stopped» (or «Goes Off»), alarm — «Any». Turn on «Run Immediately» and tap «Next».",
          "«New Blank Automation» → «Add Action» → find «Home Assistant» → «Fire Event».",
          "Paste this code into «Event»:",
          "Tap «Done». In the morning stop the alarm — the blinds and lights follow. The iPhone needs the Home Assistant app signed in to this house.",
        ],
        share: "Send the steps", shareTitle: "iPhone alarm → home", shared: "Steps copied — paste them into a message.",
        iphoneLater: "Save the rule — the code for Shortcuts shows up here.",
        androidSteps: ["Home Assistant app → Settings → Companion app → Manage sensors → «Next alarm» → turn it on.", "Set an alarm on the phone (the Clock app).", "Tap «Check» — Hauflo shows which alarm it sees."],
        chk: "Check", chkTitle: "What Hauflo sees",
        chkAndroidOk: (t) => `Hauflo sees the next alarm: ${t}. All works.`,
        chkAndroidNone: "The sensor is there but no alarm is visible — do step 2: set an alarm on the phone and open the Home Assistant app. Then tap «Check» again.",
        chkAndroidOff: "The sensor is unavailable (phone off, or the app wasn't opened for a long time) — open the Home Assistant app on the phone and check again.",
        chkAndroidMissing: "There is no alarm sensor yet — do step 1: turn on «Next alarm» in the Home Assistant app.",
        chkIphoneSave: "The rule isn't saved yet — save it and the code for Shortcuts appears (step 5).",
        chkIphoneOk: (t) => `The iPhone's command came: ${t}. All works.`,
        chkIphoneNo: "No command from the iPhone has come yet — do steps 1–6, then stop the alarm and tap «Check» again. Quick test: open this automation in Shortcuts and tap «Run».",
        chkIphoneOff: "The rule is switched off in Home Assistant — switch it on in the rules list.",
        copy: "Copy", copied: "Copied",
        willRun: (x) => `Runs: ${x}`, willNot: "Runs: with the iPhone alarm",
        save: "Save", cancel: "Cancel", saved: "Saved.", deleted: "Rule deleted.", del: "Delete", delAsk: "Delete this rule?", delYes: "Yes, delete",
        run: "Test now", runAsk: (x) => `This runs now: ${x}. Go on?`, runYes: "Run", ran: "Done — look at the blinds and lights.",
        rampNote: "The slow light takes the minutes set.",
        needLoc: "The home location isn't set — sunrise and sunset times will be approximate.", needLocAdmin: "Set where the house is in the «Where the house is» block on this page.", needLocUser: "Ask the administrator to set the home location in Home Assistant.", approx: "time is approximate", needAction: "Add at least one action.", needDay: "Pick at least one day.",
        err: "Didn't work: ", errNet: "No link to the house — try again.", noAgent: "This needs a newer Hauflo agent — update it in HACS.", agentGone: "The Hauflo agent isn't installed — rules and “Away” don't work without it.", locWarn: "Saved, but the home's location isn't set — sunrise and sunset times are approximate.",
        last: (t) => `Last run: ${t}`, never: "hasn't run yet", missing: "not in Home Assistant — save it again",
        changedTag: "changed in Home Assistant",
        changedNote: "This rule was changed in Home Assistant's editor — Hauflo leaves it alone: switch it on / off and test it here, edit it there. Or put it back as it was in Hauflo (the Home Assistant edit is lost).",
        restore: "Put back as in Hauflo", restoreAsk: "Put the rule back as it was in Hauflo? The Home Assistant edit is lost.", restoreYes: "Yes, put it back",
        coverRooms: (n) => `All blinds: ${n}`, split: "One by one", review: "Check it and tap «Save».",
        roomCovers: { open: (l) => `Open the blinds: ${l}`, close: (l) => `Close the blinds: ${l}`, position: (l, v) => `Blinds to ${v} %: ${l}` },
        moreRooms: (n) => ` and ${n} more room${n === 1 ? "" : "s"}`,
        blindsN: (n) => `${n} blind${n === 1 ? "" : "s"}`,
        findCity: "Find the town", cityPh: "Town or address", find: "Find", noCity: "Nothing found — try other words.", notThis: "Not this — search again",
        manualLink: "Type the coordinates", llPh: "e.g. 55.75, 37.62", or: "or",
        notAdmin: "The house's place is set by Home Assistant's owner (an admin) — ask them to open this page.",
        tzAsk: (z, cur) => `The house's time zone is ${z}, but Home Assistant has ${cur}. Sunrise and sunset show at the wrong time because of it.`, tzFix: (z) => `Set ${z}`, tzDone: "Time zone fixed.", tzNote: (z) => `Times are the house's clock (${z}).`,
        loadFail: "Didn't load — refresh the page.", reload: "Refresh",
        loc: "Where the house is", locWhy: "To know sunrise and sunset. Only the coordinates are saved in Home Assistant.",
        detect: "Find", detecting: "Looking…", detected: (c) => `Looks like the house is here${c ? `: ${c}` : ""}.`, confirm: "Yes, it's here",
        byPhone: "By the phone", manual: "Type it in", lat: "Latitude", lon: "Longitude", saveLoc: "Save the place",
        locNow: (a, b) => `Home: ${a}, ${b}`, change: "Change", locSaved: "Place saved.",
        geoNo: "The phone gave no coordinates (needs https and permission) — type them in.", yamlLoc: "The place is set in configuration.yaml — the integrator changes it.",
        detectFail: "Not found by the internet address — try the phone or type it in.", mapNote: "Map loaded from OpenStreetMap · © OpenStreetMap",
        adminLoc: "The home's location is set by the Home Assistant administrator.",
        notPlain: "Hauflo doesn't change automations.yaml: it has comments, !include or hand formatting. The integrator adds the rule.",
        on: "On", offShort: "Off",
        p: {
          title: "Away — presence simulation",
          sub: "While you're away, lights go on and off in the evening and the blinds close — the way your home usually does, a little different every evening.",
          how: (n, j) => `Learns from two weeks of the home's history: where and when lights are on in the evening, when the blinds close. Lights only in the dark and in at most ${n} rooms at once, every time ±${j} min. Anything someone at home changed in the last 10 minutes is left alone.`,
          sunHow: "The blinds open 30 min after sunrise and close at sunset; in the evening a calm light in the 1–2 rooms where it's on most.",
          since: (d) => `On since ${d}`, untilLine: (d) => `switches itself off after ${d}`, untilNone: "until you switch it off", pick: "Pick a date", askOn: "Switch it on? Lights and blinds will move by themselves every evening.", askOff: "Switch it off? The home will stop looking lived in.", yesOn: "Yes, switch on", yesOff: "Yes, switch off", no: "Cancel",
          what: "What takes part", lights: "Lights", covers: "Blinds", mode: "How", learned: "By the family's habits", sun: "By the sun",
          until: "Back on", noDate: "No date",
          partCovers: "Partly: blinds only", partLights: "Partly: lights only", partNone: "Nothing will switch tonight",
          whyFew: (n, min) => `Today: no lights — not enough history (${n} evening${n === 1 ? "" : "s"} in two weeks, ${min} needed). «By the sun» lights without history.`,
          whyLoc: "Today: no lights — the house's place isn't set (it's needed to know when it gets dark).",
          whyLate: "Today: the evening is over — lights from tomorrow evening.",
          whyNone: "Today: no evening light came up from the family's habits — that happens on normal evenings too.",
          whyCovers: "The blinds won't move: no blind history and no house place to follow the sun.",
          rooms: "Rooms", roomsHint: "Tap a room to leave it out (a façade, a guest room).",
          today: "Today", nothing: "no evening light came up from the habits tonight",
          blinds: (o, c) => [o ? `blinds open at ${o}` : "", c ? `${o ? "" : "blinds "}close at ${c}` : ""].filter(Boolean).join(", "),
          bySun: "by the sun",
          few: (n, min) => `Not enough history yet: lights were on in the evening ${n} time${n === 1 ? "" : "s"} in two weeks, at least ${min} are needed. No lights; the blinds follow the sun.`,
          useSun: "Use «by the sun»",
          noLoc: "Lights go on only once it's dark — set where the house is first.",
          noRec: "History (recorder) is off in Home Assistant — nothing to learn from.",
          noDev: "No Hauflo lights or blinds to switch.",
          log: "What it did", all: "Show all", less: "Show less",
          skip: { manual: "skipped: changed by hand", offline: "skipped: offline", gone: "skipped: device gone", error: "didn't work" },
          sys: { on: "Simulation on", off: "Simulation off", auto_off: "Switched itself off — the date passed" },
        },
        g: {
          title: "Guest access",
          sub: "A link or QR code for a guest — no account needed: lights, blinds and climate of the chosen rooms, for a while. Locks, gates, the alarm, cameras and settings are never open to a guest.",
          invite: "Invite a guest", who: "For whom", whoPh: "E.g. Ivan", rooms: "Rooms", roomsHint: "The guest sees only Hauflo lights, blinds and climate in these rooms.",
          howLong: "For how long", hours: { 3: "3 hours", 24: "A day", 72: "3 days", 168: "A week" }, create: "Create link", cancel: "Cancel",
          ready: "The link is ready", once: "Shown once — send it to your guest now. Lost it? Close it and make a new one.",
          copy: "Copy", copied: "Copied", share: "Share", done: "Done",
          active: "Active", ended: "Ended", none: "No guest links right now.", guest: "Guest",
          until: (d) => `until ${d}`, devices: (n, m) => (n ? `opened on ${n} of ${m} devices` : `Not opened yet · can be opened on ${m} device${m === 1 ? "" : "s"}`), close: "Revoke access", closeAll: "Revoke all links", closeAllAsk: "Sure? Revoke all", 
          revoked: "closed", expired: "expired",
          noRooms: "No rooms with Hauflo lights, blinds or climate — nothing to give a guest.",
          cant: "Guest links are made by the adults — you have a child profile.",
          ask: "Ask the administrator", askFor: { "": "Any time", "на вечер": "For the evening", "на выходные": "For the weekend" },
          asked: "Done: the administrator got your request in Home Assistant and on the phone.", askedNoPhone: "Done: the request is in the administrator's Home Assistant notifications.", askSoon: "You've already asked — the administrator will see it.",
          log: "Guests' log", all: "Show all", less: "Show less",
          sys: { issued: "link created", revoked: "link closed", revoked_all: "all links closed", opened: "link opened on a new device", device_refused: "refused: opened on one device too many" },
          act: { on: "on", off: "off", open: "open", close: "close", stop: "stop", position: "position", temperature: "temperature" },
          failed: "didn't work",
        },
      },
    };
    const DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
    /** A time zone as people say it («Центральная Европа», «Москва»), not «Europe/Berlin» (final review 28.10). */
    const tzName = (z, l) => {
      try {
        const p = new Intl.DateTimeFormat(l === "ru" ? "ru" : "en", { timeZone: z, timeZoneName: "longGeneric" }).formatToParts(new Date()).find((x) => x.type === "timeZoneName");
        return (p?.value || z).replace(/, стандартное время$/, "").replace(/ Standard Time$/, "");
      } catch (e) {
        return String(z).split("/").pop().replace(/_/g, " ");
      }
    };
    function dur(m, l) {
      const w = W[l];
      return m >= 60 ? `${Math.floor(m / 60)} ${w.h}${m % 60 ? ` ${m % 60} ${w.min}` : ""}` : `${m} ${w.min}`;
    }
    const sign = (o) => (o < 0 ? `−${-o}` : `+${o}`);
    // Little drawings of the iPhone steps (family review 28.10: «Команды → Автоматизация, Fire Event» lost a person).
    const svg = (d) => `<svg viewBox="0 0 32 32" width="32" height="32" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${d}</svg>`;
    const STEP_ART = [
      svg('<rect x="5" y="9" width="15" height="15" rx="4"/><rect x="12" y="5" width="15" height="15" rx="4" fill="currentColor" fill-opacity=".18"/>'),
      svg('<rect x="4" y="4" width="24" height="24" rx="6"/><path d="M16 11v10M11 16h10"/>'),
      svg('<circle cx="16" cy="17" r="9"/><path d="M16 12v5l3 2M7 8l3-3M25 8l-3-3"/>'),
      svg('<path d="M5 16 16 6l11 10"/><path d="M8 14v12h16V14"/><circle cx="16" cy="19" r="2.5"/>'),
      svg('<rect x="7" y="6" width="18" height="22" rx="3"/><path d="M12 6V4h8v2M11 14h10M11 19h7"/>'),
      svg('<circle cx="16" cy="16" r="11"/><path d="m11 16 4 4 7-8"/>'),
    ];
    const BEDROOM = /спальн|bedroom|master/i;
    const el_code = (card) => card.draft?.when?.code || "";
    const VIEWER_TZ = (() => {
      try {
        return Intl.DateTimeFormat().resolvedOptions().timeZone;
      } catch (e) {
        return "";
      }
    })();
    const HALL = /коридор|холл|прихож|hall|corridor|entrance|lobby/i;
    const CSS3 = `.svcl{display:flex;align-items:center;flex-wrap:wrap;gap:4px 8px;margin:-6px 0 14px;font:400 14px/1.4 Onest,sans-serif;color:var(--eh-ink2,var(--secondary-text-color))}.svcl ha-icon{--mdc-icon-size:18px;color:var(--eh-mint-text,#1F7A57)}.svcl a{color:var(--eh-ink,var(--primary-text-color));font-weight:600;text-decoration:none;min-height:32px;display:inline-flex;align-items:center}
:host{display:block;font-family:Onest,var(--ha-font-family-body),system-ui,sans-serif;color:var(--eh-ink,var(--primary-text-color));-webkit-tap-highlight-color:transparent}
*{box-sizing:border-box}
.pg{max-width:760px;margin:0 auto;padding:8px 12px 40px}
.map.dk img{filter:invert(1) hue-rotate(180deg) brightness(.85) contrast(.9)}
h1{font:600 26px/1.15 Onest,sans-serif;letter-spacing:-.02em;margin:6px 0 4px}h2{font:600 18px/1.2 Onest,sans-serif;margin:0}
.sub,.hint,.muted{font:400 13px/1.45 Onest,sans-serif;color:var(--eh-ink2,var(--secondary-text-color))}.sub{margin:0 0 14px}
.box{background:var(--eh-glass,var(--ha-card-background,var(--card-background-color,#fff)));box-shadow:var(--eh-edge,none),var(--eh-deep,var(--ha-card-box-shadow,0 1px 3px rgba(0,0,0,.12)));border-radius:24px;padding:16px;margin:0 0 14px}
.caps{font:500 11px/1.2 Onest,sans-serif;letter-spacing:.14em;text-transform:uppercase;color:var(--eh-ink2,var(--secondary-text-color));margin:0 0 10px}
.sunl{display:flex;gap:10px;align-items:flex-start;font:500 14px/1.4 Onest,sans-serif;margin:0 0 10px}.sunl ha-icon{color:var(--eh-amber,#E8901F);flex:none}
.tl{list-style:none;margin:0;padding:0}.tl li{display:grid;grid-template-columns:auto 1fr;gap:2px 12px;padding:10px 0;border-top:1px solid var(--eh-hair,var(--divider-color,rgba(127,127,127,.2)))}
.tl .rel{font:600 15px/1.3 Onest,sans-serif;white-space:nowrap;font-variant-numeric:tabular-nums}.tl .txt{font:400 14px/1.4 Onest,sans-serif;min-width:0}.tl .abs{color:var(--eh-ink2,var(--secondary-text-color))}
.tl li.dis{opacity:.5}.tl li.dis .rel{font-weight:500}
@container (max-width:520px){.tl li{grid-template-columns:1fr}}
.pg{container-type:inline-size}
.sg{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:10px}
.card{display:flex;flex-direction:column;gap:6px;padding:14px;border-radius:20px;background:var(--eh-track,rgba(127,127,127,.1));min-height:112px}
.card b{font:600 15px/1.3 Onest,sans-serif}.card .muted{flex:1}
.btn{font:500 14px Onest,sans-serif;color:inherit;border:0;border-radius:14px;min-height:44px;padding:8px 16px;background:var(--eh-track,rgba(127,127,127,.14));cursor:pointer}
.btn.pri{background:var(--eh-ink,var(--primary-color));color:var(--card-background-color,#fff)}.btn.sm{min-height:44px;padding:6px 14px;font-size:13px;border-radius:12px;align-self:flex-start}
.btn.dan{color:var(--eh-alert-text,var(--eh-alert,#B3261E))}.btn:disabled{opacity:.5;cursor:default}
.lnk{font:500 14px Onest,sans-serif;color:var(--eh-ink,var(--primary-color));background:none;border:0;padding:8px 0;cursor:pointer;text-decoration:underline;text-underline-offset:3px;min-height:44px}
.rl{list-style:none;margin:0;padding:0}.rl li{display:flex;align-items:center;gap:12px;padding:12px 0;border-top:1px solid var(--eh-hair,var(--divider-color,rgba(127,127,127,.2)))}
.rl li:first-child{border-top:0}.rl .ic{width:40px;height:40px;border-radius:20px;display:grid;place-items:center;background:var(--eh-track,rgba(127,127,127,.12));flex:none}
.rl .bd{flex:1;min-width:0;text-align:left;background:none;border:0;color:inherit;padding:0;cursor:pointer;font:inherit}.rl .nm{font:600 15px/1.3 Onest,sans-serif}.rl .ds{font:400 13px/1.4 Onest,sans-serif;color:var(--eh-ink2,var(--secondary-text-color));overflow-wrap:break-word;hyphens:auto}
.rl li.dis .bd{opacity:.55}
.sw{position:relative;width:52px;height:44px;border-radius:16px;border:0;background:none;cursor:pointer;flex:none}.sw::before{content:"";position:absolute;left:0;top:6px;width:52px;height:32px;border-radius:16px;background:var(--eh-line-strong,rgba(127,127,127,.5));transition:background .2s}.sw::after{content:"";position:absolute;top:10px;left:4px;width:24px;height:24px;border-radius:12px;background:#fff;box-shadow:0 1px 3px rgba(0,0,0,.3);transition:left .2s}
.sw[aria-checked="true"]::before{background:var(--eh-mint,#2FB383)}.sw[aria-checked="true"]::after{left:24px}
.seg{display:grid;grid-auto-flow:column;grid-auto-columns:1fr;gap:4px;padding:4px;border-radius:16px;background:var(--eh-track,rgba(127,127,127,.12))}
.seg button{font:500 14px Onest,sans-serif;color:inherit;border:0;border-radius:12px;min-height:44px;padding:6px 4px;background:none;cursor:pointer}
.seg button[aria-pressed="true"]{background:var(--card-background-color,#fff);box-shadow:0 1px 3px rgba(0,0,0,.18);font-weight:600}
.fld{margin:16px 0 0}.fld>.caps{margin-bottom:8px}
.step{display:flex;align-items:center;gap:10px}.step button{width:44px;height:44px;border-radius:22px;border:0;background:var(--eh-track,rgba(127,127,127,.14));color:inherit;font:500 20px Onest,sans-serif;cursor:pointer;flex:none}
.step span{flex:1;text-align:center;font:500 15px/1.3 Onest,sans-serif}
.chips{display:flex;flex-wrap:wrap;gap:6px}.chips button{min-width:44px;min-height:44px;border-radius:20px;border:0;background:var(--eh-track,rgba(127,127,127,.12));color:inherit;font:500 14px Onest,sans-serif;cursor:pointer;padding:0 12px}
.chips button[aria-pressed="true"]{background:var(--eh-ink,var(--primary-color));color:var(--card-background-color,#fff)}
.act{padding:12px;border-radius:18px;background:var(--eh-track,rgba(127,127,127,.08));margin:0 0 8px;display:grid;gap:8px}.act .top{display:flex;align-items:center;gap:8px}.act .top b{flex:1;font:600 14px/1.3 Onest,sans-serif;min-width:0}
.x{width:44px;height:44px;border-radius:22px;border:0;background:none;color:inherit;font-size:20px;cursor:pointer;flex:none;display:grid;place-items:center}.x ha-icon{--mdc-icon-size:22px}
input::placeholder{color:var(--eh-ink3,#757575);opacity:1;font-weight:400}
select,input{font:400 15px Onest,sans-serif;color:inherit;background:var(--card-background-color,#fff);border:1px solid var(--eh-line-strong,var(--divider-color,rgba(127,127,127,.5)));border-radius:12px;min-height:44px;padding:8px 12px;width:100%;max-width:100%}
input[type=time]{width:auto;min-width:140px}
.row{display:flex;flex-wrap:wrap;gap:8px;align-items:center}.row.end{justify-content:flex-end}.grow{flex:1}
.fld.tm{display:flex;align-items:center;gap:6px}.fld.tm select{width:auto;min-width:76px;font:500 20px Onest,sans-serif;text-align:center}.fld.tm b{font:500 20px Onest,sans-serif}
.msg{font:500 14px/1.4 Onest,sans-serif;margin:10px 0 0}.msg.bad{color:var(--eh-alert-text,var(--eh-alert,#B3261E))}.msg.ok{color:var(--eh-mint-text,var(--eh-ink,inherit))}.msg.warn{padding:10px 12px;border-radius:14px;background:color-mix(in srgb,var(--eh-amber,#E8901F) 12%,transparent)}
.ask{margin:12px 0 0;padding:12px;border-radius:16px;background:color-mix(in srgb,var(--eh-amber,#E8901F) 14%,transparent)}
.map{position:relative;height:180px;border-radius:18px;overflow:hidden;margin:10px 0;background:var(--eh-track,rgba(127,127,127,.12))}.map img{position:absolute;width:256px;height:256px;max-width:none}
.pin{position:absolute;left:50%;top:50%;width:22px;height:22px;margin:-22px 0 0 -11px;border-radius:11px 11px 11px 0;transform:rotate(-45deg);background:var(--eh-alert,#E5484D);box-shadow:0 2px 6px rgba(0,0,0,.4)}
.attr{position:absolute;right:6px;bottom:4px;font:400 10px Onest,sans-serif;background:rgba(255,255,255,.7);color:#333;padding:1px 4px;border-radius:4px}
ol{margin:6px 0 0;padding:0 0 0 20px;font:400 14px/1.5 Onest,sans-serif}ol li{margin:4px 0}code{font:500 14px ui-monospace,monospace;background:var(--eh-track,rgba(127,127,127,.14));padding:2px 6px;border-radius:6px;overflow-wrap:break-word;hyphens:auto}
.hd{display:flex;align-items:center;gap:8px;margin:0 0 6px}.hd h2{flex:1}
ol.steps{list-style:none;padding:0;margin:8px 0 10px;display:grid;gap:10px}ol.steps li{display:flex;gap:12px;align-items:flex-start;margin:0}
.sti{width:44px;height:44px;border-radius:12px;display:grid;place-items:center;flex:none;background:var(--eh-track,rgba(127,127,127,.12));color:var(--eh-ink,var(--primary-text-color))}
.stt{font:400 14px/1.45 Onest,sans-serif;min-width:0}.stt .row{margin-top:6px}
.rl li.bye{animation:eh-bye .38s ease forwards;overflow:hidden}@keyframes eh-bye{from{max-height:120px;opacity:1}to{max-height:0;opacity:0;padding-top:0;padding-bottom:0;border-top-width:0}}
.alt{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:12px 0 0}.alt input{flex:1;min-width:180px}
.hits{list-style:none;margin:8px 0 0;padding:0;display:grid;gap:6px}.hits button{width:100%;text-align:left;font:400 14px/1.35 Onest,sans-serif;color:inherit;border:0;border-radius:12px;min-height:44px;padding:8px 12px;background:var(--eh-track,rgba(127,127,127,.1));cursor:pointer}
details.man{margin:12px 0 0}details.man summary{font:500 14px Onest,sans-serif;cursor:pointer;min-height:44px;display:flex;align-items:center;color:var(--eh-ink2,var(--secondary-text-color))}
.pres .hd{gap:12px}.pres .pic{width:40px;height:40px;border-radius:20px;display:grid;place-items:center;background:var(--eh-track,rgba(127,127,127,.12));flex:none}
.pres.on .pic{background:color-mix(in srgb,var(--eh-amber,#E8901F) 22%,transparent);color:var(--eh-amber,#E8901F)}.pres .sub{margin:4px 0 8px}
.tn{font:400 14px/1.5 Onest,sans-serif;margin:10px 0 0;padding:10px 12px;border-radius:14px;background:color-mix(in srgb,var(--eh-amber,#E8901F) 12%,transparent)}
.pres .chips button:disabled{opacity:.45;cursor:default}
.dt{position:relative;display:inline-flex;align-items:center;gap:8px;min-height:44px;min-width:170px;padding:0 14px;border-radius:14px;box-shadow:inset 0 0 0 1px var(--divider-color,rgba(127,127,127,.35));font:500 14px Onest,sans-serif;cursor:pointer}.dt span{flex:1}.dt ha-icon{--mdc-icon-size:20px;opacity:.7}.dt.off{opacity:.5;cursor:default}
.dt input[type=date]{position:absolute;inset:0;width:100%;height:100%;min-width:0;opacity:0;cursor:pointer;border:0;padding:0}
.pres.pp .sw[aria-checked="true"]::before{background:var(--eh-amber,#E8901F)}
.pwarn{display:flex;flex-direction:column;align-items:flex-start;gap:6px;margin:8px 0 10px;padding:10px 12px;border-radius:14px;background:color-mix(in srgb,var(--eh-amber,#E8901F) 14%,transparent);box-shadow:inset 3px 0 0 var(--eh-amber,#E8901F)}
.pwarn b{font:600 14px/1.35 Onest,sans-serif;color:color-mix(in srgb,var(--eh-amber,#E8901F) 70%,var(--eh-ink,var(--primary-text-color)))}.pwarn span{font:400 14px/1.45 Onest,sans-serif}
.opt{font:500 13px Onest,sans-serif;min-height:44px;padding:0 14px;border-radius:12px;border:0;cursor:pointer;color:inherit;background:transparent;box-shadow:inset 0 0 0 1px var(--eh-hair,var(--divider-color,rgba(127,127,127,.35)))}
.opt[aria-pressed="true"]{background:var(--eh-ink,var(--primary-color));color:var(--card-background-color,#fff);box-shadow:none}
.tl.pl li{grid-template-columns:auto 1fr}.tl.pl .rel{font-size:14px;font-weight:500}.tl.pl ha-icon{--mdc-icon-size:16px;vertical-align:-2px;color:var(--eh-ink2,var(--secondary-text-color))}
.guest .hd{gap:12px}.guest .pic{width:40px;height:40px;border-radius:20px;display:grid;place-items:center;background:var(--eh-track,rgba(127,127,127,.12));flex:none}
.gnew{display:flex;flex-wrap:wrap;gap:16px;align-items:flex-start;margin:4px 0 14px;padding:14px;border-radius:18px;background:color-mix(in srgb,var(--eh-amber,#E8901F) 10%,transparent)}
.gnew .qr{width:184px;max-width:100%;flex:none;border-radius:12px;overflow:hidden;background:#fff}.gnew .qr svg{display:block;width:100%;height:auto}
.gnl{flex:1;min-width:200px;display:flex;flex-direction:column;gap:6px}.gnl b{font:600 15px Onest,sans-serif}.gnl input{width:100%;font-size:13px;height:40px}.gnl .row{display:flex;flex-wrap:wrap;gap:8px}
.gls{list-style:none;margin:6px 0 0;padding:0;display:grid;gap:6px}.gls li{display:flex;align-items:center;gap:10px;padding:10px 12px;border-radius:14px;background:var(--eh-track,rgba(127,127,127,.08))}
.gls li.dis{opacity:.6}.gtx{flex:1;min-width:0;display:flex;flex-direction:column}.gtx b{font:600 14px/1.3 Onest,sans-serif;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.gls .btn.sm{align-self:center}
.guest .row{display:flex;flex-wrap:wrap;gap:8px;align-items:center}.guest .fld+.row{margin-top:16px}.guest input[data-glabel]{width:100%}
ha-icon{--mdc-icon-size:20px}`;

    class Automations extends HTMLElement {
      constructor() {
        super();
        this.attachShadow({ mode: "open" });
        this.view = "list";
        this.data = null;
        this.msg = "";
        this.bad = false;
      }
      setConfig(c) {
        this._config = c || {};
        const l = cfgLang(this._config);
        if (l) HOUSE_LANG = l;
      }
      getCardSize() {
        return 12;
      }
      getGridOptions() {
        return { columns: "full" };
      }
      set hass(h) {
        const first = !this._hass;
        this._hass = h;
        if (first) {
          this.lang = langOf(h);
          this.load();
          return;
        }
        // Only the list follows HA (last run, the phone's next alarm); an open editor is never redrawn under a finger.
        const sig = (this.data?.rules || []).map((r) => `${h.states[r.entity_id]?.state}${h.states[r.entity_id]?.attributes?.last_triggered}${r.when.sensor ? h.states[r.when.sensor]?.state : ""}`).join();
        if (sig !== this._sig && this.view === "list") {
          this._sig = sig;
          this.draw();
        }
      }
      connectedCallback() {
        clearInterval(this._tick);
        this._tick = setInterval(() => this.view === "list" && this.data && this.draw(), 30000);
      }
      disconnectedCallback() {
        clearInterval(this._tick);
      }
      get w() {
        return W[this.lang || "ru"];
      }
      async api(method, body) {
        return this._hass.callApi(method, "hauflo/automations", body);
      }
      isAdmin() {
        return this._hass?.user?.is_admin === true;
      }
      /** The house's settings came (HOUSE): the list is drawn again — never an open editor under a finger. */
      _update() {
        if (this.view === "list" && this.data) this.draw();
      }
      fail(e) {
        if (e?.body?.code === "not_plain") {
          this.msg = this.w.notPlain;
          this.bad = true;
          return;
        }
        const m = e?.body?.message || e?.message || "";
        const gone = agentErr(e, this._hass);
        this.msg = gone === "gone" ? this.w.agentGone : gone === "old" ? this.w.noAgent : m ? this.w.err + m : this.w.errNet;
        this.bad = true;
      }
      async load() {
        try {
          this.data = await this.api("GET");
          this.noAgent = false;
        } catch (e) {
          // «gone» / «old» / no link — each said as it is (HA expert 28.10: a removed agent read «обновите»).
          this.noAgent = agentErr(e, this._hass) || "net";
        }
        await this.loadPres();
        await this.loadGuest();
        this.draw();
      }
      // --- «Гостевой доступ» (guest.py, docs/guest-access.md) -----------------------------------------------------------
      async loadGuest() {
        try {
          this.gst = await this._hass.callApi("GET", "hauflo/guest?rooms=1");
        } catch (e) {
          this.gst = null; // an agent before 0.16.10: no section
        }
      }
      async guestDo(body) {
        this.gBusy = true;
        this.gMsg = "";
        this.draw();
        try {
          const r = await this._hass.callApi("POST", "hauflo/guest", body);
          if (body.op === "issue") {
            this.gNew = r;
            this.gForm = false;
            this.gCopied = false;
            await this.loadGuest();
          } else this.gst = { ...this.gst, ...r };
        } catch (e) {
          const m = e?.body?.message || e?.message || "";
          this.gMsg = m ? this.w.err + m : this.w.errNet;
        }
        this.gBusy = false;
        this.draw();
      }
      qrSvg(rows) {
        const n = rows.length;
        let d = "";
        rows.forEach((row, y) => {
          for (let x = 0; x < n; x++) if (row[x] === "1") d += `M${x + 4} ${y + 4}h1v1h-1z`;
        });
        // Always dark on white with a quiet zone: a phone camera reads it in the dark theme too.
        return `<svg viewBox="0 0 ${n + 8} ${n + 8}" role="img" aria-label="QR" shape-rendering="crispEdges"><rect width="${n + 8}" height="${n + 8}" fill="#fff"/><path d="${d}" fill="#1F1E1C"/></svg>`;
      }
      guestHtml() {
        const gs = this.gst;
        if (!gs) return "";
        const g = this.w.g;
        const date = (iso) => this.fmtParts(Date.parse(iso), { day: "numeric", month: "long", hour: "2-digit", minute: "2-digit" });
        const roomNames = (x) => (x.rooms || []).map((r) => r.name).join(", ");
        const active = (gs.grants || []).filter((x) => x.status === "active");
        const ended = (gs.grants || []).filter((x) => x.status !== "active").slice(0, 3);
        const rooms = gs.rooms || [];
        const sel = this.gSel || new Set();
        const hours = this.gHours || 24;
        const dis = this.gBusy ? "disabled" : "";
        let fresh = "";
        if (this.gNew) {
          const share = typeof navigator !== "undefined" && navigator.share ? `<button class="btn sm" data-gshare>${esc(g.share)}</button>` : "";
          fresh = `<div class="gnew"><div class="qr">${this.qrSvg(this.gNew.qr || [])}</div><div class="gnl"><b>${esc(g.ready)}</b><p class="hint">${esc(g.once)}</p><input readonly data-glink value="${esc(this.gNew.link)}" aria-label="${esc(g.ready)}"><div class="row"><button class="btn sm" data-gcopy>${esc(this.gCopied ? g.copied : g.copy)}</button>${share}<button class="btn sm pri" data-gdone>${esc(g.done)}</button></div></div></div>`;
        }
        let form = "";
        // A restricted (child) profile asks an admin instead of a dead end (family review round 3, item 8).
        if (!gs.can_issue)
          form = `<p class="hint">${esc(g.cant)}</p>${gs.can_ask ? (this.gAsked ? `<p class="msg ok" role="status">${esc(this.gAsked)}</p>` : `<div class="chips">${Object.entries(g.askFor).map(([k, v]) => `<button aria-pressed="${(this.gAskFor ?? "на вечер") === k}" data-gaskfor="${esc(k)}">${esc(v)}</button>`).join("")}</div><div class="row"><button class="btn sm pri" data-gask ${dis}>${esc(g.ask)}</button></div>`) : ""}`;
        else if (!rooms.length) form = `<p class="hint">${esc(g.noRooms)}</p>`;
        else if (this.gForm)
          form = `<div class="fld"><div class="caps">${esc(g.who)}</div><input data-glabel maxlength="40" placeholder="${esc(g.whoPh)}" value="${esc(this.gLabel || "")}" aria-label="${esc(g.who)}"></div>
<div class="fld"><div class="caps">${esc(g.rooms)}</div><div class="chips">${rooms.map((r) => `<button aria-pressed="${sel.has(r.id)}" data-groom="${esc(r.id)}"><span data-own>${esc(r.name)}</span></button>`).join("")}</div><p class="hint">${esc(g.roomsHint)}</p></div>
<div class="fld"><div class="caps">${esc(g.howLong)}</div><div class="seg">${[3, 24, 72, 168].map((h) => `<button aria-pressed="${hours === h}" data-ghours="${h}">${esc(g.hours[h])}</button>`).join("")}</div></div>
<div class="row"><button class="btn pri" data-gissue ${sel.size && !this.gBusy ? "" : "disabled"}>${esc(g.create)}</button><button class="btn" data-gcancel>${esc(g.cancel)}</button></div>`;
        else if (!this.gNew) form = `<button class="btn sm pri" data-ginvite ${dis}>+ ${esc(g.invite)}</button>`;
        const grantRow = (x, live) =>
          `<li class="${live ? "" : "dis"}"><div class="gtx"><b>${esc(x.label || g.guest)}</b><span class="muted">${esc([roomNames(x), live ? g.until(date(x.expires)) : g[x.status] || x.status, live ? g.devices(x.devices, x.max_devices) : ""].filter(Boolean).join(" · "))}</span></div>${live ? `<button class="btn sm" data-grevoke="${esc(x.id)}" ${dis}>${esc(g.close)}</button>` : ""}</li>`;
        const list = active.length
          ? `<div class="fld"><div class="caps">${esc(g.active)}</div><ul class="gls">${active.map((x) => grantRow(x, true)).join("")}</ul>${active.length > 1 ? `<button class="lnk" data-grevall ${dis}>${esc(g.closeAll)}</button>` : ""}</div>`
          : `<p class="muted">${esc(g.none)}</p>`;
        const endedHtml = ended.length ? `<div class="fld"><div class="caps">${esc(g.ended)}</div><ul class="gls">${ended.map((x) => grantRow(x, false)).join("")}</ul></div>` : "";
        const when = (iso) => {
          const t = Date.parse(iso);
          return this.key(t) === this.key(Date.now()) ? this.hm(t) : `${this.dayWord(t)} ${this.hm(t)}`;
        };
        const line = (x) => {
          const who = x.label || g.guest;
          if (x.sys) return `<b>${esc(who)}</b>: ${esc(g.sys[x.sys] || x.sys)}${x.by ? ` — ${esc(x.by)}` : ""}`;
          const v = x.value === null || x.value === undefined ? "" : x.do === "temperature" ? ` ${String(x.value).replace(".", this.lang === "ru" ? "," : ".")}°` : ` ${x.value} %`;
          return `<b>${esc(who)}</b>: ${esc(x.name || x.entity_id)} — ${esc((g.act[x.do] || x.do) + v)}${x.ok === false ? ` — ${esc(g.failed)}` : ""}`;
        };
        const log = (gs.log || []).filter((x) => x.sys !== "revoked_all" || x.n);
        const logHtml = log.length
          ? `<div class="fld"><div class="caps">${esc(g.log)}</div><ul class="tl pl">${log
              .slice(0, this.gAll ? 50 : 5)
              .map((x) => `<li class="${x.ok === false ? "dis" : ""}"><span class="rel">${esc(when(x.t))}</span><span class="txt">${line(x)}</span></li>`)
              .join("")}</ul>${log.length > 5 ? `<button class="lnk" data-gall>${esc(this.gAll ? g.less : g.all)}</button>` : ""}</div>`
          : "";
        return `<section class="box guest" aria-label="${esc(g.title)}"><div class="hd"><span class="pic"><ha-icon icon="mdi:account-key-outline"></ha-icon></span><h2>${esc(g.title)}</h2></div>
<p class="sub">${esc(g.sub)}</p>${this.gMsg ? `<p class="msg bad" role="status">${esc(this.gMsg)}</p>` : ""}
${fresh}${form}${list}${endedHtml}${logHtml}</section>`;
      }
      // --- «Нет дома — имитация присутствия» (presence.py) --------------------------------------------------------------
      async loadPres() {
        try {
          this.pres = await this._hass.callApi("GET", "hauflo/presence");
          PRES.set(this.pres);
        } catch (e) {
          this.pres = null; // an agent before 0.14.5: no section
        }
      }
      async setPres(body) {
        this.pBusy = true;
        this.draw();
        try {
          this.pres = await this._hass.callApi("POST", "hauflo/presence", { op: "set", ...body });
          PRES.set(this.pres);
          this.pMsg = "";
        } catch (e) {
          const m = e?.body?.message || e?.message || "";
          this.pMsg = m ? this.w.err + m : this.w.errNet;
        }
        this.pBusy = false;
        this.draw();
      }
      presHtml() {
        const pr = this.pres;
        if (!pr) return "";
        const w = this.w;
        const p = w.p;
        const t = (iso) => (iso ? this.hm(Date.parse(iso)) : "");
        const date = (d) => this.fmtParts(Date.parse(`${d}T12:00:00Z`), { day: "numeric", month: "long" });
        const on = pr.enabled;
        const tn = pr.tonight;
        // Working only in part (final review 28.10: a plain green switch over «свет не включится» looked fully working):
        // amber, said right under the switch with the reason and what to do.
        const part = presPartial(pr);
        let state = "";
        if (on) {
          const head = `${pr.since ? p.since(this.when(Date.parse(pr.since))) : ""} · ${pr.until ? p.untilLine(date(pr.until)) : p.untilNone}`;
          let line = "";
          if (tn && pr.status !== "no_devices" && pr.status !== "no_recorder") {
            const rooms = (tn.rooms || []).map((r) => `${r.name} ${t(r.from)}–${t(r.to)}`).join(", ");
            const bl = p.blinds(t(tn.covers?.open), t(tn.covers?.close));
            const today = [(tn.lights === "learned" || tn.lights === "sun") && rooms ? rooms : "", bl ? `${bl}${tn.covers?.by === "sun" ? ` (${p.bySun})` : ""}` : ""].filter(Boolean).join(" · ");
            line = today ? `<p class="tn"><b>${esc(p.today)}:</b> ${esc(today)}</p>` : "";
          }
          state = `<p class="muted">${esc(head)}</p>${line}`;
        }
        const why = !part
          ? ""
          : `<div class="pwarn" role="status"><b>${esc(part.kind === "none" ? p.partNone : part.kind === "lights" ? p.partLights : p.partCovers)}</b><span>${esc(
              part.why === "few" ? p.whyFew(tn?.evenings ?? 0, pr.min_evenings) : part.why === "no_location" ? p.whyLoc : part.why === "late" ? p.whyLate : part.why === "no_devices" ? p.noDev : part.why === "no_recorder" ? p.noRec : part.why === "covers" ? p.whyCovers : p.whyNone,
            )}</span>${part.sun && pr.mode !== "sun" ? `<button class="btn sm pri" data-pmode="sun">${esc(p.useSun)}</button>` : ""}</div>`;
        // Rooms the family leaves out (a façade, a guest room): on / off chips, kept by the agent.
        const roomList = Array.isArray(pr.rooms) && pr.rooms.length > 1
          ? `<div class="fld"><div class="caps">${esc(p.rooms)}</div><div class="chips">${pr.rooms.map((r) => `<button aria-pressed="${r.on !== false}" data-proom="${esc(r.id)}" ${this.pBusy || !canEdit(this._hass) ? "disabled" : ""}><span data-own>${esc(r.name)}</span></button>`).join("")}</div><p class="hint">${esc(p.roomsHint)}</p></div>`
          : "";
        const ico = { on: "mdi:lightbulb-on-outline", off: "mdi:lightbulb-outline", open: "mdi:blinds-open", close: "mdi:blinds", position: "mdi:blinds" };
        const act = (x) => {
          const n = `${x.name || x.entity_id}${x.room ? ` · ${x.room}` : ""}`;
          return x.do === "on" ? w.act.on(n, x.value) : x.do === "off" ? w.act.off(n) : x.do === "position" ? w.act.position(n, x.value) : w.act[x.do] ? w.act[x.do](n) : n;
        };
        const log = (pr.log || []).slice(0, this.pAll ? 20 : 5);
        const when = (iso) => {
          const x = Date.parse(iso);
          return this.key(x) === this.key(Date.now()) ? this.hm(x) : `${this.dayWord(x)} ${this.hm(x)}`;
        };
        const logHtml = log.length
          ? `<div class="fld"><div class="caps">${esc(p.log)}</div><ul class="tl pl">${log
              .map((x) => `<li class="${x.skip ? "dis" : ""}"><span class="rel">${esc(when(x.t))}</span><span class="txt">${x.sys ? `<b>${esc(p.sys[x.sys] || x.sys)}</b>${x.by ? ` — ${esc(x.by)}` : ""}` : `<ha-icon icon="${ico[x.do] || "mdi:home"}"></ha-icon> ${esc(act(x))}${x.skip ? ` — ${esc(p.skip[x.skip] || x.skip)}` : ""}`}</span></li>`)
              .join("")}</ul>${(pr.log || []).length > 5 ? `<button class="lnk" data-pall>${esc(this.pAll ? p.less : p.all)}</button>` : ""}</div>`
          : "";
        const today = this.key(Date.now());
        const max = this.key(Date.now() + 90 * 86400000);
        const hl = String(this._hass?.locale?.language || this._hass?.language || this.lang || "ru");
        // «Кто может менять настройки дома» (family review round 2: a child switched it): others see it, can't change it.
        const ro = !canEdit(this._hass);
        const lock = this.pBusy || ro ? "disabled" : "";
        // The switch always asks «Точно?» — it changes what the whole house does every evening.
        const ask = this.pAsk && !ro ? `<div class="pwarn pask" role="alertdialog"><b>${esc(this.pAsk === "on" ? p.askOn : p.askOff)}</b><div class="row"><button class="btn sm pri" data-pyes>${esc(this.pAsk === "on" ? p.yesOn : p.yesOff)}</button><button class="btn sm" data-pno>${esc(p.no)}</button></div></div>` : "";
        return `<section class="box pres${on ? " on" : ""}${part ? " pp" : ""}${ro ? " ro" : ""}" aria-label="${esc(p.title)}"><div class="hd"><span class="pic"><ha-icon icon="mdi:home-export-outline"></ha-icon></span><h2>${esc(p.title)}</h2><button class="sw" role="switch" aria-checked="${on}" aria-label="${esc(p.title)}" data-psw ${lock}></button></div>
${ask}${ro ? `<p class="hint">${esc(w.ro)}</p>` : ""}${why}<p class="sub">${esc(p.sub)}</p>${state}
${this.pMsg ? `<p class="msg bad" role="status">${esc(this.pMsg)}</p>` : ""}
<div class="fld"><div class="caps">${esc(p.what)}</div><div class="chips"><button aria-pressed="${pr.lights}" data-pk="lights" ${pr.devices?.lights && !ro ? "" : "disabled"}>${esc(p.lights)}${pr.devices?.lights ? ` · ${pr.devices.lights}` : ""}</button><button aria-pressed="${pr.covers}" data-pk="covers" ${pr.devices?.covers && !ro ? "" : "disabled"}>${esc(p.covers)}${pr.devices?.covers ? ` · ${pr.devices.covers}` : ""}</button></div></div>
${roomList}
<div class="fld"><div class="caps">${esc(p.mode)}</div><div class="seg"><button aria-pressed="${pr.mode === "learned"}" data-pmode="learned" ${ro ? "disabled" : ""}>${esc(p.learned)}</button><button aria-pressed="${pr.mode === "sun"}" data-pmode="sun" ${ro ? "disabled" : ""}>${esc(p.sun)}</button></div><p class="hint">${esc(pr.mode === "sun" ? p.sunHow : p.how(pr.max_rooms || 3, pr.jitter_min || 15))}</p></div>
<div class="fld"><div class="caps">${esc(p.until)}</div><div class="row"><label class="dt${ro ? " off" : ""}"><span>${esc(pr.until ? date(pr.until) : p.pick)}</span><ha-icon icon="mdi:calendar-month-outline"></ha-icon><input type="date" lang="${esc(hl)}" data-puntil min="${today}" max="${max}" value="${esc(pr.until || "")}" aria-label="${esc(p.until)}" ${ro ? "disabled" : ""}></label><button class="opt" data-puclear aria-pressed="${!pr.until}" ${ro ? "disabled" : ""}>${esc(p.noDate)}</button></div></div>
${logHtml}</section>`;
      }
      // --- time ------------------------------------------------------------------------------------------------------
      get tz() {
        return this._hass?.config?.time_zone;
      }
      fmtParts(t, opts) {
        try {
          return new Intl.DateTimeFormat(this.lang === "ru" ? "ru-RU" : "en-GB", { timeZone: this.tz, ...opts }).format(t);
        } catch (e) {
          return new Intl.DateTimeFormat(this.lang === "ru" ? "ru-RU" : "en-GB", opts).format(t);
        }
      }
      key(t) {
        try {
          return new Intl.DateTimeFormat("en-CA", { timeZone: this.tz, year: "numeric", month: "2-digit", day: "2-digit" }).format(t);
        } catch (e) {
          return new Date(t).toISOString().slice(0, 10);
        }
      }
      wdOf(t) {
        const day = this.data?.days?.find((d) => d.date === this.key(t));
        if (day) return day.wd;
        const n = new Date(this.key(t) + "T12:00:00Z").getUTCDay();
        return DAYS[(n + 6) % 7];
      }
      hm(t) {
        return this.fmtParts(t, { hour: "2-digit", minute: "2-digit" });
      }
      dayWord(t) {
        const now = Date.now();
        const k = this.key(t);
        if (k === this.key(now)) return this.w.today;
        if (k === this.key(now + 86400000)) return this.w.tomorrow;
        return this.w.wd[DAYS.indexOf(this.wdOf(t))];
      }
      when(t) {
        return `${this.dayWord(t)} ${this.w.at} ${this.hm(t)}`;
      }
      rel(t) {
        const w = this.w;
        const m = Math.round((t - Date.now()) / 60000);
        if (m < 1) return w.now;
        if (m < 60) return w.in(`${m} ${w.min}`);
        if (m < 1440) return w.in(`${Math.floor(m / 60)} ${w.h}${m % 60 ? ` ${m % 60} ${w.min}` : ""}`);
        const d = Math.floor(m / 1440);
        const h = Math.floor((m % 1440) / 60);
        return w.in(`${d} ${w.d}${h ? ` ${h} ${w.h}` : ""}`);
      }
      nextOf(rule) {
        const now = Date.now();
        const w = rule.when;
        const days = rule.days?.length ? rule.days : DAYS;
        if (w.event === "alarm_iphone") return null;
        if (w.event === "alarm_android") {
          const t = Date.parse(this._hass?.states[w.sensor]?.state) + (w.offset || 0) * 60000;
          return isFinite(t) && t > now && days.includes(this.wdOf(t)) ? t : null;
        }
        for (const d of this.data?.days || []) {
          let t;
          if (w.event === "time") {
            const [h, m] = String(w.at || "").split(":").map(Number);
            t = Date.parse(d.midnight) + (h * 60 + m) * 60000;
          } else {
            if (!d[w.event]) continue;
            t = Date.parse(d[w.event]) + (w.offset || 0) * 60000;
          }
          if (isFinite(t) && t > now && days.includes(this.wdOf(t))) return t;
        }
        return null;
      }
      // --- words -------------------------------------------------------------------------------------------------------
      name(eid) {
        const t = this.data?.targets || {};
        const all = [...(t.covers || []), ...(t.lights || []), ...(t.scenes || [])];
        const it = all.find((x) => x.entity_id === eid);
        return it ? clientName(it.name, eid) : nameOf(this._hass?.states[eid], eid);
      }
      room(a) {
        return this.data?.targets?.areas?.[a] || a;
      }
      // A device always with its room: «Штора 1 · Спальня» (family review 28.10: «Штора 1, Штора 2, Штора 1…»).
      nameIn(eid) {
        const t = this.data?.targets || {};
        const it = [...(t.covers || []), ...(t.lights || [])].find((x) => x.entity_id === eid);
        return it?.area ? `${this.name(eid)} · ${this.room(it.area)}` : this.name(eid);
      }
      roomsList(ids) {
        const names = ids.map((a) => this.room(a));
        return names.length > 3 ? `${names.slice(0, 2).join(", ")}${this.w.moreRooms(names.length - 2)}` : names.join(", ");
      }
      coverRoom(a) {
        return (this.data?.targets?.cover_rooms || []).find((r) => r.area_id === a);
      }
      phone(sensor) {
        return this.data?.targets?.alarms?.find((a) => a.entity_id === sensor)?.phone || sensor;
      }
      whenText(w) {
        const t = this.w;
        if (w.event === "time") return t.atTime(w.at);
        if (w.event === "alarm_iphone") return t.alarmOf("iPhone");
        const base = w.event === "alarm_android" ? t.alarmOf(this.phone(w.sensor)) : t.ev[w.event];
        return w.offset ? `${base} ${sign(w.offset)} ${t.min}` : base;
      }
      daysText(days) {
        const t = this.w;
        if (!days || days.length === 7) return t.every;
        if (days.join() === "mon,tue,wed,thu,fri") return t.workdays;
        if (days.join() === "sat,sun") return t.weekend;
        return days.map((d) => t.wd[DAYS.indexOf(d)]).join(", ");
      }
      actText(a) {
        const t = this.w.act;
        if (a.do === "room_off") return t.room_off(this.room(a.area_id));
        if (a.do === "room_covers") return this.w.roomCovers[a.cover](this.room(a.area_id), a.value);
        const n = this.nameIn(a.entity_id);
        if (a.entity_id.startsWith("scene.")) return t.scene(n);
        return t[a.do] ? t[a.do](n, a.value, a.minutes) : n;
      }
      // Blinds that do the same go as one: «Закрыть Деверь, Штора 1 и ещё 6» — not eight «Закрыть …».
      actsText(r) {
        const groups = new Map();
        const order = [];
        // «Закрыть шторы: Гостиная, Кухня и ещё 4 комнаты» — rooms, not twenty blinds.
        const rooms = new Map();
        for (const a of r.actions || []) {
          if (a.do === "room_covers" && a.cover !== "position") {
            if (!rooms.has(a.cover)) {
              rooms.set(a.cover, []);
              order.push({ rooms: a.cover });
            }
            rooms.get(a.cover).push(a.area_id);
          } else if (a.entity_id?.startsWith("cover.") && (a.do === "open" || a.do === "close")) {
            if (!groups.has(a.do)) {
              groups.set(a.do, []);
              order.push(a.do);
            }
            groups.get(a.do).push(this.nameIn(a.entity_id));
          } else if (a.do === "room_off") {
            // One «Весь свет выкл: Комната 1, Комната 2…», not a «Весь свет выкл:» per room.
            if (!rooms.has("_off")) {
              rooms.set("_off", []);
              order.push({ roomOff: true });
            }
            rooms.get("_off").push(a.area_id);
          } else order.push(a);
        }
        const list = (n) => (n.length > 3 ? `${n.slice(0, 2).join(", ")}${this.w.more(n.length - 2)}` : n.join(", "));
        return order.map((x) => (typeof x === "string" ? this.w.act[x](list(groups.get(x))) : x.rooms ? this.w.roomCovers[x.rooms](this.roomsList(rooms.get(x.rooms))) : x.roomOff ? this.w.act.room_offs(this.roomsList(rooms.get("_off"))) : this.actText(x))).join(", ");
      }
      icon(w) {
        return w.event === "sunrise" ? "mdi:weather-sunset-up" : w.event === "sunset" ? "mdi:weather-sunset-down" : w.event === "time" ? "mdi:clock-outline" : "mdi:alarm";
      }
      // --- suggestions ---------------------------------------------------------------------------------------------------
      suggestions() {
        const t = this.data?.targets || {};
        const w = this.w;
        const have = new Set((this.data?.rules || []).map((r) => r.from).filter(Boolean));
        const out = [];
        const areaName = (a) => (a ? t.areas?.[a] || "" : "");
        const android = (t.alarms || [])[0];
        const alarmWhen = (off) => (android ? { event: "alarm_android", sensor: android.entity_id, offset: off } : { event: "alarm_iphone" });
        const src = android ? w.srcAndroid(android.phone) : w.srcIphone;
        // Blinds by rooms (family review 28.10: «шторы: 81» — and a rule of 20 of them): every room ticked, seen in the editor.
        const cr = (t.cover_rooms || []).slice(0, 20);
        if (cr.length)
          out.push({ key: "sunset_covers", title: w.s.sunset_covers[0], sub: w.s.sunset_covers[1](this.roomsList(cr.map((r) => r.area_id)), w.blindsN(cr.reduce((n, r) => n + r.covers, 0))), rule: { when: { event: "sunset", offset: 0 }, days: DAYS, actions: cr.map((r) => ({ do: "room_covers", area_id: r.area_id, cover: "close" })) } });
        const bed = cr.filter((r) => BEDROOM.test(r.name));
        if (bed.length)
          out.push({ key: "wake_covers", title: w.s.wake_covers[0], sub: w.s.wake_covers[1](android ? w.alarmOf(android.phone) : w.srcIphone), rule: { when: alarmWhen(0), days: DAYS, actions: bed.map((r) => ({ do: "room_covers", area_id: r.area_id, cover: "open" })) } });
        const dim = (t.lights || []).filter((l) => l.dim);
        const bedLight = dim.find((l) => BEDROOM.test(areaName(l.area)));
        if (bedLight)
          out.push({ key: "wake_light", title: w.s.wake_light[0], sub: w.s.wake_light[1](src), rule: { when: alarmWhen(android ? -20 : 0), days: DAYS, actions: [{ entity_id: bedLight.entity_id, do: "ramp", value: 25, minutes: android ? 20 : 15 }] } });
        if ((t.rooms || []).length)
          out.push({ key: "night_off", title: w.s.night_off[0], sub: w.s.night_off[1](), rule: { when: { event: "time", at: "01:00" }, days: DAYS, actions: t.rooms.slice(0, 20).map((r) => ({ do: "room_off", area_id: r.area_id })) } });
        const hall = dim.find((l) => HALL.test(areaName(l.area)));
        if (hall)
          out.push({ key: "corridor_night", title: w.s.corridor_night[0], sub: w.s.corridor_night[1](areaName(hall.area)), rule: { when: { event: "time", at: "23:00" }, days: DAYS, actions: [{ entity_id: hall.entity_id, do: "on", value: 10 }] } });
        return out.filter((s) => !have.has(s.key));
      }
      autoName(r) {
        return this.whenText(r.when);
      }
      // --- drawing ------------------------------------------------------------------------------------------------------
      // Where the page is scrolled — HA's view scrolls in its own container, not the window (owner 28.10: «удалить —
      // кидает на верх страницы»).
      scrollers() {
        const out = [];
        let n = this;
        while (n) {
          n = n.assignedSlot || n.parentNode || n.host;
          if (n && n.nodeType === 1 && n.scrollHeight > n.clientHeight + 1 && /(auto|scroll)/.test(getComputedStyle(n).overflowY)) out.push([n, n.scrollTop]);
        }
        const se = document.scrollingElement;
        if (se) out.push([se, se.scrollTop]);
        return out;
      }
      toList(extra) {
        this.view = "list";
        this.asking = null;
        this.warn = false; // a caution (amber), not an error: only when asked for
        Object.assign(this, extra || {});
        this.draw({ restore: this._listScroll });
      }
      draw(opts = {}) {
        const sr = this.shadowRoot;
        const w = this.w;
        const keep = this.isConnected ? this.scrollers() : [];
        // WebKit lays the page out with the card empty while it's replaced: the card keeps its height till it's back.
        const h = this.isConnected ? this.getBoundingClientRect().height : 0;
        if (h) this.style.minHeight = `${h}px`;
        const back = opts.restore || keep;
        const put = () => !opts.top && back.forEach(([el, top]) => Math.abs(el.scrollTop - top) > 1 && (el.scrollTop = top));
        const done = () => {
          put();
          requestAnimationFrame(() => {
            this.style.minHeight = "";
            if (opts.top) this.scrollIntoView?.({ block: "start" });
            else put();
          });
        };
        if (this.noAgent) {
          const why = this.noAgent === "gone" ? w.agentGone : this.noAgent === "old" ? w.noAgent : w.errNet;
          sr.innerHTML = `<style>${TZ(CSS3)}</style><div class="pg"><h1>${esc(w.title)}</h1><p class="sub">${esc(why)}</p></div>`;
          return done();
        }
        if (!this.data) {
          sr.innerHTML = `<style>${TZ(CSS3)}</style><div class="pg"><h1>${esc(w.title)}</h1></div>`;
          return done();
        }
        const body = this.view === "edit" ? this.editHtml() : this.view === "loc" ? this.locHtml() : this.listHtml();
        sr.innerHTML = `<style>${TZ(CSS3)}</style><div class="pg">${this._config?.overlay ? `<div class="row end"><button class="btn sm" data-close>${esc(w.close)}</button></div>` : ""}${body}</div>`;
        sr.querySelector("[data-close]")?.addEventListener("click", () => this.dispatchEvent(new CustomEvent("hauflo-close", { bubbles: true, composed: true })));
        this.bind();
        done();
      }
      listHtml() {
        const w = this.w;
        const d = this.data;
        const now = Date.now();
        const rise = (d.days || []).map((x) => Date.parse(x.sunrise)).find((t) => t > now);
        const set = (d.days || []).map((x) => Date.parse(x.sunset)).find((t) => t > now);
        const items = d.rules
          .map((r) => ({ r, t: this.nextOf(r), on: r.state === "on" }))
          .filter((x) => x.t !== null || x.r.when.event === "alarm_iphone")
          .sort((a, b) => Number(b.on) - Number(a.on) || (a.t ?? Infinity) - (b.t ?? Infinity))
          .slice(0, 6);
        const tl = items
          .map(({ r, t, on }) => {
            const abs = t ? (this.key(t) === this.key(now) ? this.hm(t) : `${this.dayWord(t)} ${this.hm(t)}`) : "";
            const lead = !on ? w.off : t ? this.rel(t) : w.iphone;
            // A clock rule: its time is the time («06:45, в 06:45» said it twice — final review 28.10).
            const how = r.when.event === "time" && abs ? "" : this.whenText(r.when).replace(/^./, (c) => c.toLowerCase());
            return `<li class="${on ? "" : "dis"}" data-open="${esc(r.id)}"><span class="rel">${esc(lead)}</span><span class="txt"><span class="abs">${esc([abs, how].filter(Boolean).join(", "))}:</span> ${esc(this.actsText(r))}</span></li>`;
          })
          .join("");
        const sugg = this.suggestions();
        const loc = d.location;
        // «Кто обслуживает дом» as one small line (family review round 2) — the house's contact from the deploy.
        const svc = serviceOf(this);
        const svcLine = svc && (svc.phone || svc.email) ? `<p class="svcl"><ha-icon icon="mdi:phone"></ha-icon><span>${esc(w.svc)}:</span><span data-own>${esc(svc.company || svc.name || "")}</span>${svc.phone ? `<a href="tel:${esc(telOf(svc.phone))}">${esc(svc.phone)}</a>` : `<a href="mailto:${esc(svc.email)}">${esc(svc.email)}</a>`}</p>` : "";
        return `<h1>${esc(w.title)}</h1><p class="sub">${esc(w.sub)}</p>${svcLine}
${!loc.set ? this.locCard() : ""}
<section class="box" aria-label="${esc(w.upcoming)}"><div class="caps">${esc(w.upcoming)}</div>
<p class="sunl"><ha-icon icon="mdi:weather-sunset"></ha-icon><span>${rise && set ? esc(w.sunLine(this.when(rise), this.when(set))) : esc(w.noSun)}</span></p>
${this.tz && this.tz !== VIEWER_TZ ? `<p class="hint">${esc(w.tzNote(this.isAdmin() ? this.tz : tzName(this.tz, this.lang)))}</p>` : ""}
${this.tzAsk ? this.tzHtml() : ""}
${tl ? `<ul class="tl">${tl}</ul>` : `<p class="muted">${esc(w.nothing)}</p>`}</section>
${this.presHtml()}
${this.guestHtml()}
${sugg.length && canEdit(this._hass) ? `<section class="box"><div class="caps">${esc(w.sugg)}</div><div class="sg">${sugg.map((s) => `<div class="card"><b>${esc(s.title)}</b><span class="muted">${esc(s.sub)}</span><button class="btn sm pri" data-sugg="${esc(s.key)}">+ ${esc(w.addOne)}</button></div>`).join("")}</div></section>` : ""}
<section class="box"><div class="hd"><h2>${esc(w.mine)}</h2>${canEdit(this._hass) ? `<button class="btn sm pri" data-new>+ ${esc(w.add)}</button>` : ""}</div>${canEdit(this._hass) ? "" : `<p class="hint" data-ro>${esc(w.ro)}</p>`}
${this.msg ? `<p class="msg ${this.bad ? "bad" : this.warn ? "warn" : ""}" role="status">${esc(this.msg)}</p>` : ""}
${d.rules.length || this.gone ? `<ul class="rl">${(() => {
          const rows = d.rules.map((r) => this.ruleRow(r));
          if (this.gone) rows.splice(Math.min(this.gone.at, rows.length), 0, this.ruleRow(this.gone.rule).replace(/^<li class="/, '<li aria-hidden="true" class="bye '));
          return rows.join("");
        })()}</ul>` : `<p class="muted">${esc(w.empty)}</p>`}</section>
${loc.set && this.isAdmin() ? `<p class="muted">${esc(w.locNow(loc.latitude.toFixed(3), loc.longitude.toFixed(3)))} · <button class="lnk" data-loc>${esc(w.change)}</button></p>` : ""}`;
      }
      ruleRow(r) {
        const w = this.w;
        const s = this._hass?.states[r.entity_id];
        const on = r.state === "on";
        const t = this.nextOf(r);
        const last = s?.attributes?.last_triggered;
        const meta = r.state === "missing" ? w.missing : [r.changed ? w.changedTag : "", this.daysText(r.days), (r.when.event === "sunrise" || r.when.event === "sunset") && !this.data.location.set ? `⚠ ${w.approx}` : "", t && on ? `→ ${this.when(t)}` : "", last ? w.last(this.when(Date.parse(last))) : w.never].filter(Boolean).join(" · ");
        return `<li class="${on ? "" : "dis"}"><span class="ic"><ha-icon icon="${this.icon(r.when)}"></ha-icon></span>
<button class="bd" ${canEdit(this._hass) ? `data-open="${esc(r.id)}"` : "disabled"}><div class="nm">${esc(r.name || this.autoName(r))}</div><div class="ds">${esc(this.actsText(r))}</div><div class="ds">${esc(meta)}</div></button>
<button class="sw" role="switch" aria-checked="${on}" aria-label="${esc(on ? w.on : w.offShort)}" data-sw="${esc(r.id)}" ${r.state === "missing" || !canEdit(this._hass) ? "disabled" : ""}></button></li>`;
      }
      // Family review 28.10: three equal buttons and two empty boxes lost a person. One way first (by the internet, on a
      // map, «Да, дом здесь»); a town search second; the coordinates tucked away with an example.
      locCard() {
        const w = this.w;
        const loc = this.data.location;
        const c = this.cand;
        if (loc.yaml) return `<section class="box"><div class="caps">${esc(w.loc)}</div><p class="muted">${esc(w.yamlLoc)}</p></section>`;
        // HA's home location moves zone.home (presence, every sun rule): an administrator's (security review 28.10).
        if (!this.isAdmin()) return `<section class="box"><div class="caps">${esc(w.loc)}</div><p class="muted">${esc(w.adminLoc)}</p></section>`;
        const hits = this.hits || [];
        return `<section class="box" data-loccard><div class="caps">${esc(w.loc)}</div><p class="muted">${esc(w.locWhy)}</p>
${c ? `${this.mapHtml(c.latitude, c.longitude)}<p class="msg">${esc(w.detected([c.city, c.country].filter(Boolean).join(", ")))} <span class="muted">${esc(`${c.latitude.toFixed(3)}, ${c.longitude.toFixed(3)}`)}</span></p>
<div class="row"><button class="btn pri" data-locok>${esc(w.confirm)}</button><button class="btn" data-again>${esc(w.notThis)}</button></div>`
          : `<div class="row"><button class="btn pri" data-detect ${this.busy ? "disabled" : ""}>${esc(this.busy ? w.detecting : w.detect)}</button></div>
<div class="alt"><span class="muted">${esc(w.or)}</span><input type="search" data-q placeholder="${esc(w.cityPh)}" aria-label="${esc(w.findCity)}" value="${esc(this.q || "")}"><button class="btn" data-find>${esc(w.findCity)}</button></div>
${hits.length ? `<ul class="hits">${hits.map((h, i) => `<li><button data-hit="${i}">${esc(h.label)}</button></li>`).join("")}</ul>` : ""}
<details class="man"${this.manual ? " open" : ""}><summary>${esc(w.manualLink)}</summary><div class="row fld"><input class="grow" inputmode="decimal" data-ll placeholder="${esc(w.llPh)}" aria-label="${esc(w.manualLink)}"><button class="btn" data-mansave>${esc(w.saveLoc)}</button><button class="lnk" data-geo>${esc(w.byPhone)}</button></div></details>`}
${this.locMsg ? `<p class="msg ${this.locBad ? "bad" : ""}" role="status">${esc(this.locMsg)}</p>` : ""}</section>`;
      }
      tzHtml() {
        const w = this.w;
        return `<div class="ask"><p class="msg">${esc(w.tzAsk(this.tzAsk, this.data.location.time_zone || "UTC"))}</p><div class="row"><button class="btn pri" data-tzfix>${esc(w.tzFix(this.tzAsk))}</button><button class="btn" data-tzno>${esc(w.cancel)}</button></div></div>`;
      }
      async findCity(q) {
        this.q = q;
        if (!q.trim()) return;
        try {
          const r = await fetch(`https://nominatim.openstreetmap.org/search?format=jsonv2&limit=5&addressdetails=1&q=${encodeURIComponent(q)}&accept-language=${this.lang}`);
          const list = await r.json();
          this.hits = (Array.isArray(list) ? list : []).map((x) => ({ latitude: Math.round(Number(x.lat) * 1000) / 1000, longitude: Math.round(Number(x.lon) * 1000) / 1000, label: String(x.display_name || "").split(",").slice(0, 3).join(","), city: x.address?.city || x.address?.town || x.address?.village || x.name || "", country: String(x.address?.country_code || "").toUpperCase() })).filter((x, i, all) => isFinite(x.latitude) && isFinite(x.longitude) && all.findIndex((y) => y.label === x.label) === i);
          this.locMsg = this.hits.length ? "" : this.w.noCity;
          this.locBad = !this.hits.length;
        } catch (e) {
          this.locMsg = this.w.errNet;
          this.locBad = true;
        }
        this.draw();
      }
      async fixTz() {
        try {
          await this.api("POST", { op: "timezone", time_zone: this.tzAsk });
          this.data = await this.api("GET");
          this.tzAsk = null;
          this.msg = this.w.tzDone;
          this.bad = this.warn = false;
        } catch (e) {
          this.fail(e);
        }
        this.draw();
      }
      locHtml() {
        return `<div class="row"><button class="btn sm" data-back>← ${esc(this.w.back)}</button></div>${this.locCard()}`;
      }
      mapHtml(lat, lon) {
        // OpenStreetMap tiles (as HA's own location picker uses OSM); asked only on this step, after the person pressed a
        // button. Dark: the same tiles inverted.
        const z = 12;
        const n = 2 ** z;
        const x = ((lon + 180) / 360) * n;
        const r = (lat * Math.PI) / 180;
        const y = ((1 - Math.log(Math.tan(r) + 1 / Math.cos(r)) / Math.PI) / 2) * n;
        const tx = Math.floor(x);
        const ty = Math.floor(y);
        let tiles = "";
        for (let dx = -2; dx <= 2; dx++)
          for (let dy = -1; dy <= 1; dy++) {
            const left = Math.round((tx + dx - x) * 256);
            const top = Math.round((ty + dy - y) * 256);
            tiles += `<img alt="" src="https://tile.openstreetmap.org/${z}/${(tx + dx + n) % n}/${ty + dy}.png" style="left:calc(50% + ${left}px);top:calc(50% + ${top}px)">`;
          }
        return `<div class="map ${this._hass?.themes?.darkMode ? "dk" : ""}" aria-hidden="true">${tiles}<span class="pin"></span><span class="attr">${esc(this.w.mapNote)}</span></div>`;
      }
      editHtml() {
        const w = this.w;
        const r = this.draft;
        const ev = r.when.event.startsWith("alarm") ? "alarm" : r.when.event;
        const t = this.data.targets;
        const off = r.when.offset || 0;
        const offRow = (evName) => `<div class="step fld"><button data-off="-5" aria-label="−5">−</button><span data-offtxt>${esc(w.offLong(evName, off))}</span><button data-off="5" aria-label="+5">+</button></div>`;
        let whenBody = "";
        if (ev === "sunrise" || ev === "sunset") whenBody = (this.data.location.set ? "" : `<p class="tn">${esc(w.needLoc)} ${esc(this.isAdmin() ? w.needLocAdmin : w.needLocUser)}</p>`) + offRow(ev);
        else if (ev === "time") {
          // 24-hour in every language of the house (family review round 3: «01:00 AM» in a Russian house).
          const [hh, mm] = String(r.when.at || "07:00").split(":").map(Number);
          const opt = (n, on) => `<option value="${String(n).padStart(2, "0")}"${n === on ? " selected" : ""}>${String(n).padStart(2, "0")}</option>`;
          const mins = [...new Set([...Array.from({ length: 12 }, (_, i) => i * 5), mm || 0])].sort((a, b) => a - b);
          whenBody = `<div class="fld tm"><select data-ath aria-label="${esc(this.lang === "en" ? "Hours" : "Часы")}">${Array.from({ length: 24 }, (_, i) => opt(i, hh || 0)).join("")}</select><b>:</b><select data-atm aria-label="${esc(this.lang === "en" ? "Minutes" : "Минуты")}">${mins.map((m) => opt(m, mm || 0)).join("")}</select></div>`;
        }
        else {
          const opts = [...(t.alarms || []).map((a) => `<option value="${esc(a.entity_id)}" ${r.when.sensor === a.entity_id ? "selected" : ""}>${esc(`Android · ${a.phone}`)}</option>`), `<option value="iphone" ${r.when.event === "alarm_iphone" ? "selected" : ""}>${esc(w.iphoneOpt)}</option>`];
          whenBody = `<div class="fld"><div class="caps">${esc(w.phone)}</div><select data-phone>${opts.join("")}</select>${r.when.event === "alarm_android" || !(t.alarms || []).length ? `<ol class="steps">${w.androidSteps.map((x, i) => `<li><span class="stt"><b>${i + 1}.</b> ${esc(x)}</span></li>`).join("")}</ol>` : ""}</div>`;
          if (r.when.event === "alarm_android") whenBody += offRow("alarm") + this.checkHtml();
          else {
            const code = r.when.code;
            whenBody += `<div class="fld"><div class="caps">${esc(w.iphoneTitle)}</div>${code ? `<ol class="steps">${w.iphoneSteps.map((s, i) => `<li><span class="sti" aria-hidden="true">${STEP_ART[i]}</span><span class="stt"><b>${i + 1}.</b> ${esc(s)}${i === 4 ? `<span class="row"><code>${esc(code)}</code><button class="btn sm" data-copy="${esc(code)}">${esc(w.copy)}</button></span>` : ""}</span></li>`).join("")}</ol><button class="btn sm" data-share="${esc(code)}">${esc(w.share)}</button>` : `<p class="hint">${esc(w.iphoneLater)}</p>`}</div>` + this.checkHtml();
          }
        }
        const days = r.days || DAYS;
        const acts = r.actions.map((a, i) => this.actHtml(a, i)).join("");
        const opt = (v, label) => `<option value="${esc(v)}">${esc(label)}</option>`;
        const picker = `<select data-addact aria-label="${esc(w.addAction)}"><option value="">${esc(w.addAction)}</option>
${(t.cover_rooms || []).length ? `<optgroup label="${esc(w.grp.coverRooms)}">${t.cover_rooms.map((c) => opt(`rc:${c.area_id}`, `${c.name} · ${c.covers}`)).join("")}</optgroup>` : ""}
${t.covers.length ? `<optgroup label="${esc(w.grp.covers)}">${t.covers.map((c) => opt(c.entity_id, this.name(c.entity_id) + (c.area ? ` · ${this.room(c.area)}` : ""))).join("")}</optgroup>` : ""}
${t.lights.length ? `<optgroup label="${esc(w.grp.lights)}">${t.lights.map((c) => opt(c.entity_id, this.name(c.entity_id) + (c.area ? ` · ${this.room(c.area)}` : ""))).join("")}</optgroup>` : ""}
${t.scenes.length ? `<optgroup label="${esc(w.grp.scenes)}">${t.scenes.map((c) => opt(c.entity_id, this.name(c.entity_id))).join("")}</optgroup>` : ""}
${t.rooms.length ? `<optgroup label="${esc(w.grp.rooms)}">${t.rooms.map((c) => opt(`room:${c.area_id}`, c.name)).join("")}</optgroup>` : ""}</select>`;
        const next = this.nextOf(r);
        const saved = !!r.id;
        return `<div class="row"><button class="btn sm" data-back>← ${esc(w.back)}</button></div>
<section class="box"><h2>${esc(saved ? r.name || this.autoName(r) : w.newRule)}</h2>
<div class="fld"><div class="caps">${esc(w.when)}</div><div class="seg" role="group">${["sunrise", "sunset", "time", "alarm"].map((e) => `<button data-ev="${e}" aria-pressed="${ev === e}">${esc(w.ev[e])}</button>`).join("")}</div>${whenBody}</div>
<div class="fld"><div class="caps">${esc(w.days)}</div><div class="chips">${DAYS.map((d, i) => `<button data-day="${d}" aria-pressed="${days.includes(d)}">${esc(w.wd[i])}</button>`).join("")}</div></div>
<div class="fld"><div class="caps">${esc(w.what)}</div>${acts}${picker}</div>
<p class="msg" data-next>${esc(r.when.event === "alarm_iphone" ? w.willNot : next ? `${w.willRun(this.when(next))} (${this.rel(next)})` : "")}</p>
${this.msg ? `<p class="msg ${this.bad ? "bad" : this.warn ? "warn" : ""}" role="status">${esc(this.msg)}</p>` : ""}
${this.asking === "run" ? `<div class="ask"><p class="msg">${esc(w.runAsk(this.actsText(r)))}${r.actions.some((a) => a.do === "ramp") ? ` ${esc(w.rampNote)}` : ""}</p><div class="row"><button class="btn pri" data-runyes>${esc(w.runYes)}</button><button class="btn" data-no>${esc(w.cancel)}</button></div></div>` : ""}
${r.changed ? `<div class="ask"><p class="msg">${esc(w.changedNote)}</p>${this.asking === "restore" ? `<p class="msg">${esc(w.restoreAsk)}</p><div class="row"><button class="btn dan" data-restoreyes>${esc(w.restoreYes)}</button><button class="btn" data-no>${esc(w.cancel)}</button></div>` : `<button class="btn sm" data-restore>${esc(w.restore)}</button>`}</div>` : ""}
${this.asking === "del" ? `<div class="ask"><p class="msg">${esc(w.delAsk)}</p><div class="row"><button class="btn dan" data-delyes>${esc(w.delYes)}</button><button class="btn" data-no>${esc(w.cancel)}</button></div></div>` : ""}
<div class="row fld">${r.changed ? "" : `<button class="btn pri" data-save ${this.busy ? "disabled" : ""}>${esc(w.save)}</button>`}<button class="btn" data-back>${esc(w.cancel)}</button><span class="grow"></span>
${saved ? `<button class="btn" data-run ${this.busy ? "disabled" : ""}>${esc(w.run)}</button><button class="btn dan" data-del>${esc(w.del)}</button>` : ""}</div></section>`;
      }
      /** «Проверить» on an alarm-clock rule: what Hauflo sees right now — the phone's next-alarm sensor (Android) or the
       * last command from Shortcuts (iPhone) — else the step to do (owner 28.10: «не понял, как включить/проверить»). */
      checkHtml() {
        const w = this.w;
        const c = this.chk;
        return `<div class="fld"><div class="row"><button class="btn sm" data-acheck>${esc(w.chk)}</button></div>${c ? `<p class="msg ${c.ok ? "" : "warn"}" role="status" data-chk><b>${esc(w.chkTitle)}:</b> ${esc(c.text)}</p>` : ""}</div>`;
      }
      check() {
        const w = this.w;
        const r = this.draft;
        const h = this._hass;
        let ok = false;
        let text;
        if (r.when.event === "alarm_android") {
          const st = r.when.sensor ? h.states[r.when.sensor] : null;
          const at = st && Date.parse(st.state);
          if (!st) text = w.chkAndroidMissing;
          else if (st.state === "unavailable") text = w.chkAndroidOff;
          else if (!at || isNaN(at)) text = w.chkAndroidNone;
          else {
            ok = true;
            text = w.chkAndroidOk(this.when(at));
          }
        } else if (!r.id || !r.when.code) text = w.chkIphoneSave;
        else {
          const rule = this.data.rules.find((x) => x.id === r.id);
          const a = rule && h.states[rule.entity_id];
          const last = a?.attributes?.last_triggered;
          if (a && a.state === "off") text = w.chkIphoneOff;
          else if (last) {
            ok = true;
            text = w.chkIphoneOk(this.when(Date.parse(last)));
          } else text = w.chkIphoneNo;
        }
        this.chk = { ok, text };
        this.draw();
      }
      actHtml(a, i) {
        const w = this.w;
        const seg = (opts) => `<div class="seg" role="group">${opts.map(([v, l]) => `<button data-do="${i}:${v}" aria-pressed="${a.do === v}">${esc(l)}</button>`).join("")}</div>`;
        const stepper = (field, val, unit, label) => `<div class="step"><button data-val="${i}:${field}:-1" aria-label="−">−</button><span>${esc(`${label} ${val}${unit}`)}</span><button data-val="${i}:${field}:1" aria-label="+">+</button></div>`;
        let title = "";
        let body = "";
        if (a.do === "room_off") title = `${w.grp.rooms}: ${this.room(a.area_id)}`;
        else if (a.do === "room_covers") {
          const cr = this.coverRoom(a.area_id);
          title = `${w.coverRooms(this.room(a.area_id))}${cr ? ` · ${cr.covers}` : ""}`;
          body = `<div class="seg" role="group">${[["open", w.do.open], ["close", w.do.close], ...(cr?.position ? [["position", w.do.position]] : [])].map(([v, l]) => `<button data-rc="${i}:${v}" aria-pressed="${a.cover === v}">${esc(l)}</button>`).join("")}</div>`;
          if (a.cover === "position") body += stepper("value", a.value ?? 50, " %", "");
          if ((cr?.covers || 0) > 1) body += `<button class="lnk" data-split="${i}">${esc(w.split)}</button>`;
        } else {
          title = this.nameIn(a.entity_id);
          const d = a.entity_id.split(".")[0];
          if (d === "cover") {
            body = seg([["open", w.do.open], ["close", w.do.close], ["position", w.do.position]]);
            if (a.do === "position") body += stepper("value", a.value ?? 50, " %", "");
          } else if (d === "light") {
            const dim = this.data.targets.lights.find((l) => l.entity_id === a.entity_id)?.dim;
            body = seg([["on", w.do.on], ["off", w.do.off], ...(dim ? [["ramp", w.do.ramp]] : [])]);
            if (a.do === "on" && dim) body += stepper("value", a.value ?? 100, " %", w.bright);
            if (a.do === "ramp") body += stepper("value", a.value ?? 25, " %", w.upTo) + stepper("minutes", a.minutes ?? 15, ` ${w.min}`, w.over);
          }
        }
        return `<div class="act"><div class="top"><b>${esc(title)}</b><button class="x" data-rm="${i}" aria-label="${esc(w.remove)}"><ha-icon icon="mdi:close"></ha-icon></button></div>${body}</div>`;
      }
      // --- events -------------------------------------------------------------------------------------------------------
      bind() {
        const sr = this.shadowRoot;
        const on = (sel, fn) => sr.querySelectorAll(sel).forEach((el) => el.addEventListener("click", (e) => fn(el, e)));
        on("[data-open]", (el) => this.edit(this.data.rules.find((r) => r.id === el.dataset.open)));
        on("[data-new]", () => this.edit(null));
        on("[data-back]", () => this.toList({ msg: "" }));
        on("[data-loc]", () => {
          this._listScroll = this.scrollers();
          this.view = "loc";
          this.cand = null;
          this.locMsg = "";
          this.draw({ top: true });
        });
        on("[data-sw]", (el) => this.toggle(el.dataset.sw, el.getAttribute("aria-checked") !== "true"));
        on("[data-sugg]", (el) => this.addSuggestion(el.dataset.sugg));
        on("[data-psw]", (el) => {
          this.pAsk = el.getAttribute("aria-checked") !== "true" ? "on" : "off";
          this.draw();
        });
        on("[data-pyes]", () => {
          const want = this.pAsk === "on";
          this.pAsk = "";
          this.setPres({ enabled: want });
        });
        on("[data-pno]", () => {
          this.pAsk = "";
          this.draw();
        });
        // «Гостевой доступ»: the form keeps what's typed across redraws.
        const gl = sr.querySelector("[data-glabel]");
        gl?.addEventListener("input", () => (this.gLabel = gl.value));
        on("[data-ginvite]", () => {
          this.gForm = true;
          this.gSel = new Set((this.gst?.rooms || []).length === 1 ? [this.gst.rooms[0].id] : []);
          this.gHours = 24;
          this.gLabel = "";
          this.gMsg = "";
          this.draw();
        });
        on("[data-gcancel]", () => {
          this.gForm = false;
          this.draw();
        });
        on("[data-gaskfor]", (el) => {
          this.gAskFor = el.dataset.gaskfor;
          this.draw();
        });
        on("[data-gask]", async () => {
          const g = this.w.g;
          this.gBusy = true;
          this.gMsg = "";
          this.draw();
          try {
            const k = this.gAskFor ?? "на вечер";
            const note = k ? String(g.askFor[k] || "").toLowerCase() : "";
            const r = await this._hass.callApi("POST", "hauflo/guest", { op: "ask", ...(note ? { note } : {}) });
            this.gAsked = r?.phones ? g.asked : g.askedNoPhone;
          } catch (e) {
            if (e?.body?.code === "too_soon" || /429/.test(String(e?.status || e?.message || ""))) this.gAsked = g.askSoon;
            else this.gMsg = (e?.body?.message || e?.message) ? this.w.err + (e?.body?.message || e?.message) : this.w.errNet;
          }
          this.gBusy = false;
          this.draw();
        });
        on("[data-groom]", (el) => {
          const s = (this.gSel ||= new Set());
          s.has(el.dataset.groom) ? s.delete(el.dataset.groom) : s.add(el.dataset.groom);
          this.draw();
        });
        on("[data-ghours]", (el) => {
          this.gHours = Number(el.dataset.ghours);
          this.draw();
        });
        on("[data-gissue]", () => this.guestDo({ op: "issue", label: (this.gLabel || "").trim(), rooms: [...(this.gSel || [])], hours: this.gHours || 24 }));
        on("[data-grevoke]", (el) => this.guestDo({ op: "revoke", id: el.dataset.grevoke }));
        on("[data-grevall]", (el) => {
          if (el.dataset.arm !== "1") {
            el.dataset.arm = "1";
            el.textContent = this.w.g.closeAllAsk;
            setTimeout(() => {
              if (el.isConnected && el.dataset.arm === "1") {
                el.dataset.arm = "";
                el.textContent = this.w.g.closeAll;
              }
            }, 4000);
            return;
          }
          this.guestDo({ op: "revoke_all" });
        });
        on("[data-gall]", () => {
          this.gAll = !this.gAll;
          this.draw();
        });
        on("[data-gdone]", () => {
          this.gNew = null;
          this.draw();
        });
        on("[data-gcopy]", async () => {
          const link = this.gNew?.link || "";
          try {
            await navigator.clipboard.writeText(link);
          } catch (e) {
            const inp = sr.querySelector("[data-glink]");
            inp?.select();
            document.execCommand?.("copy");
          }
          this.gCopied = true;
          this.draw();
        });
        on("[data-gshare]", () => navigator.share?.({ title: this.w.g.title, url: this.gNew?.link }).catch(() => undefined));
        on("[data-pk]", (el) => this.setPres({ [el.dataset.pk]: el.getAttribute("aria-pressed") !== "true" }));
        on("[data-pmode]", (el) => this.pres?.mode !== el.dataset.pmode && this.setPres({ mode: el.dataset.pmode }));
        on("[data-puclear]", () => this.pres?.until && this.setPres({ until: null }));
        on("[data-proom]", (el) => {
          const id = el.dataset.proom;
          const skip = new Set(this.pres?.skip_rooms || []);
          skip.has(id) ? skip.delete(id) : skip.add(id);
          this.setPres({ skip_rooms: [...skip] });
        });
        on("[data-pall]", () => {
          this.pAll = !this.pAll;
          this.draw();
        });
        sr.querySelector("[data-puntil]")?.addEventListener("change", (e) => e.target.value && this.setPres({ until: e.target.value }));
        sr.querySelector("[data-puntil]")?.addEventListener("click", (e) => {
          try {
            e.target.showPicker?.();
          } catch (err) {
            /* not from a user gesture / not supported: the field itself opens it */
          }
        });
        on("[data-detect]", () => this.detect());
        on("[data-geo]", () => this.geo());
        on("[data-locok]", () => this.saveLoc(this.cand.latitude, this.cand.longitude, this.cand.elevation));
        on("[data-again]", () => {
          this.cand = null;
          this.hits = null;
          this.draw();
        });
        on("[data-find]", () => this.findCity(sr.querySelector("[data-q]")?.value || ""));
        sr.querySelector("[data-q]")?.addEventListener("keydown", (e) => e.key === "Enter" && this.findCity(e.target.value));
        on("[data-hit]", (el) => {
          this.cand = this.hits[Number(el.dataset.hit)];
          this.draw();
        });
        on("[data-mansave]", () => {
          // «55.75, 37.62» in one box (a comma or a space between, a decimal comma too).
          const nums = String(sr.querySelector("[data-ll]")?.value || "").replace(/(\d),(\d)/g, "$1.$2").match(/-?\d+(?:\.\d+)?/g) || [];
          this.manual = true;
          this.saveLoc(Number(nums[0]), Number(nums[1]));
        });
        on("[data-tzfix]", () => this.fixTz());
        on("[data-tzno]", () => {
          this.tzAsk = null;
          this.draw();
        });
        if (this.view !== "edit") return;
        const r = this.draft;
        const redraw = () => this.draw();
        on("[data-ev]", (el) => {
          const e = el.dataset.ev;
          if (e === "alarm") {
            const a = this.data.targets.alarms?.[0];
            r.when = a ? { event: "alarm_android", sensor: a.entity_id, offset: 0 } : { event: "alarm_iphone" };
          } else if (e === "time") r.when = { event: "time", at: r.when.at || "07:00" };
          else r.when = { event: e, offset: r.when.offset || 0 };
          redraw();
        });
        on("[data-off]", (el) => {
          r.when.offset = Math.max(-120, Math.min(120, (r.when.offset || 0) + Number(el.dataset.off)));
          redraw();
        });
        const atSet = () => {
          r.when.at = `${sr.querySelector("[data-ath]")?.value || "07"}:${sr.querySelector("[data-atm]")?.value || "00"}`;
          redraw();
        };
        sr.querySelector("[data-ath]")?.addEventListener("change", atSet);
        sr.querySelector("[data-atm]")?.addEventListener("change", atSet);
        sr.querySelector("[data-phone]")?.addEventListener("change", (e) => {
          const v = e.target.value;
          r.when = v === "iphone" ? { event: "alarm_iphone", code: r.when.code } : { event: "alarm_android", sensor: v, offset: r.when.offset || 0 };
          redraw();
        });
        on("[data-day]", (el) => {
          const d = el.dataset.day;
          const days = new Set(r.days || DAYS);
          days.has(d) ? days.delete(d) : days.add(d);
          r.days = DAYS.filter((x) => days.has(x));
          redraw();
        });
        sr.querySelector("[data-addact]")?.addEventListener("change", (e) => {
          const v = e.target.value;
          if (!v) return;
          if (v.startsWith("room:")) r.actions.push({ do: "room_off", area_id: v.slice(5) });
          else if (v.startsWith("rc:")) r.actions.push({ do: "room_covers", area_id: v.slice(3), cover: "close" });
          else r.actions.push({ entity_id: v, do: v.startsWith("cover.") ? "close" : "on" });
          redraw();
        });
        on("[data-rc]", (el) => {
          const [i, v] = el.dataset.rc.split(":");
          const a = r.actions[Number(i)];
          a.cover = v;
          if (v === "position") a.value = a.value ?? 50;
          else delete a.value;
          redraw();
        });
        on("[data-split]", (el) => {
          // «По отдельности»: the room's blinds as their own actions, doing the same.
          const i = Number(el.dataset.split);
          const a = r.actions[i];
          const one = (this.data.targets.covers || []).filter((c) => c.area === a.area_id && !c.group && (a.cover !== "position" || c.position));
          r.actions.splice(i, 1, ...one.map((c) => ({ entity_id: c.entity_id, do: a.cover, ...(a.cover === "position" ? { value: a.value ?? 50 } : {}) })));
          redraw();
        });
        on("[data-rm]", (el) => {
          r.actions.splice(Number(el.dataset.rm), 1);
          redraw();
        });
        on("[data-do]", (el) => {
          const [i, v] = el.dataset.do.split(":");
          const a = r.actions[Number(i)];
          a.do = v;
          if (v === "position") a.value = a.value ?? 50;
          else if (v === "ramp") {
            a.value = Math.min(a.value ?? 25, 100);
            a.minutes = a.minutes ?? 15;
          } else if (v !== "on") delete a.value;
          if (v !== "ramp") delete a.minutes;
          redraw();
        });
        on("[data-val]", (el) => {
          const [i, field, dir] = el.dataset.val.split(":");
          const a = r.actions[Number(i)];
          const step = field === "minutes" ? 5 : 5;
          const pos = a.do === "position" || a.do === "room_covers";
          const lo = field === "minutes" ? 5 : pos ? 0 : 5;
          const hi = field === "minutes" ? 60 : 100;
          const cur = a[field] ?? (field === "minutes" ? 15 : pos ? 50 : a.do === "ramp" ? 25 : 100);
          a[field] = Math.max(lo, Math.min(hi, cur + Number(dir) * step));
          redraw();
        });
        on("[data-share]", async () => {
          const w = this.w;
          const text = `${w.shareTitle}\n\n${w.iphoneSteps.map((x, i) => `${i + 1}. ${x}${i === 4 ? ` ${el_code(this)}` : ""}`).join("\n")}`;
          try {
            if (navigator.share) await navigator.share({ title: w.shareTitle, text });
            else {
              await navigator.clipboard.writeText(text);
              this.msg = w.shared;
              this.bad = this.warn = false;
              redraw();
            }
          } catch (e) {
            /* the share sheet closed */
          }
        });
        on("[data-copy]", async (el) => {
          try {
            await navigator.clipboard.writeText(el.dataset.copy);
            el.textContent = this.w.copied;
          } catch (e) {
            const range = document.createRange();
            range.selectNodeContents(el.previousElementSibling);
            const s = window.getSelection();
            s.removeAllRanges();
            s.addRange(range);
          }
        });
        on("[data-save]", () => this.save());
        on("[data-acheck]", () => this.check());
        on("[data-run]", () => {
          this.asking = "run";
          redraw();
        });
        on("[data-del]", () => {
          this.asking = "del";
          redraw();
        });
        on("[data-no]", () => {
          this.asking = null;
          redraw();
        });
        on("[data-runyes]", () => this.run());
        on("[data-restore]", () => {
          this.asking = "restore";
          redraw();
        });
        on("[data-restoreyes]", () => {
          this.asking = null;
          this.save(true);
        });
        on("[data-delyes]", () => this.del());
      }
      edit(rule) {
        if (this.view === "list") this._listScroll = this.scrollers();
        this.draft = rule ? JSON.parse(JSON.stringify({ id: rule.id, name: rule.name, when: rule.when, days: rule.days, actions: rule.actions, enabled: rule.enabled, from: rule.from, changed: rule.changed })) : { when: { event: "sunset", offset: 0 }, days: [...DAYS], actions: [], enabled: true };
        this.view = "edit";
        this.chk = null;
        this.msg = "";
        this.asking = null;
        this.draw({ top: true });
      }
      body(r) {
        const out = { when: r.when, days: r.days, actions: r.actions, enabled: r.enabled !== false, name: r.from && r.name ? r.name : this.autoName(r) };  // a ready one keeps its title
        if (r.id) out.id = r.id;
        if (r.from) out.from = r.from;
        return out;
      }
      async save(force = false) {
        const r = this.draft;
        const w = this.w;
        this.bad = true;
        this.warn = false;
        if (!r.actions.length) this.msg = w.needAction;
        else if (!(r.days || DAYS).length) this.msg = w.needDay;
        else {
          this.busy = true;
          this.msg = "";
          this.draw();
          try {
            const saved = await this.api("POST", { op: "save", rule: this.body(r), ...(force ? { force: true } : {}) });
            this.data = await this.api("GET");
            this.busy = false;
            if (saved.when?.event === "alarm_iphone" && !r.id) {
              // The code for Shortcuts is the agent's: show the steps on the saved rule.
              this.edit(this.data.rules.find((x) => x.id === saved.id));
              this.msg = w.saved;
              this.bad = this.warn = false;
              this.draw();
              return;
            }
            this.toList(saved?.warning === "no_location" ? { msg: `${w.locWarn} ${this.isAdmin() ? w.needLocAdmin : w.needLocUser}`, bad: false, warn: true } : { msg: w.saved, bad: false });
            return;
          } catch (e) {
            this.busy = false;
            this.fail(e);
          }
        }
        this.draw();
      }
      addSuggestion(key) {
        // A ready rule opens filled in: the family sees which rooms before it's saved.
        const s = this.suggestions().find((x) => x.key === key);
        if (!s) return;
        this.edit({ ...s.rule, enabled: true, name: s.title, from: key });
        this.msg = this.w.review;
        this.bad = this.warn = false;
        this.draw({ top: true });
      }
      async toggle(id, enabled) {
        try {
          await this.api("POST", { op: "enable", id, enabled });
          this.data = await this.api("GET");
          this.msg = "";
        } catch (e) {
          this.fail(e);
        }
        this.draw();
      }
      async run() {
        this.asking = null;
        this.busy = true;
        this.draw();
        try {
          await this.api("POST", { op: "run", id: this.draft.id });
          this.msg = this.w.ran;
          this.bad = this.warn = false;
        } catch (e) {
          this.fail(e);
        }
        this.busy = false;
        this.draw();
      }
      async del() {
        this.asking = null;
        const at = this.data.rules.findIndex((x) => x.id === this.draft.id);
        const gone = this.data.rules[at];
        try {
          await this.api("POST", { op: "delete", id: this.draft.id });
          this.data = await this.api("GET");
          // Back where the list was; the rule folds away in its place.
          this.gone = gone ? { rule: gone, at } : null;
          // Said, not only folded away (final review 28.10).
          this.toList({ msg: this.w.deleted, bad: false });
          setTimeout(() => {
            this.gone = null;
            if (this.view === "list") this.draw();
          }, 420);
          setTimeout(() => {
            if (this.msg === this.w.deleted) {
              this.msg = "";
              if (this.view === "list") this.draw();
            }
          }, 4000);
          return;
        } catch (e) {
          this.fail(e);
        }
        this.draw();
      }
      async detect() {
        this.busy = true;
        this.locMsg = "";
        this.draw();
        try {
          this.cand = await this.api("POST", { op: "detect" });
        } catch (e) {
          this.locMsg = this.w.detectFail;
          this.locBad = true;
        }
        this.busy = false;
        this.draw();
      }
      geo() {
        const w = this.w;
        if (!navigator.geolocation || !window.isSecureContext) {
          this.manual = true;
          this.locMsg = w.geoNo;
          this.locBad = true;
          this.draw();
          return;
        }
        navigator.geolocation.getCurrentPosition(
          (p) => {
            this.cand = { latitude: p.coords.latitude, longitude: p.coords.longitude, elevation: p.coords.altitude ?? undefined, city: "" };
            this.locMsg = "";
            this.draw();
            this.cityOf(this.cand);
          },
          () => {
            this.manual = true;
            this.locMsg = w.geoNo;
            this.locBad = true;
            this.draw();
          },
          { enableHighAccuracy: false, timeout: 15000, maximumAge: 600000 },
        );
      }
      // The town under the phone's coordinates — OpenStreetMap's Nominatim, as HA's own location picker searches it;
      // only to show the person, never saved. Without the internet: the coordinates alone.
      async cityOf(c) {
        try {
          // Rounded to 2 decimals (~1 km) — the town is enough; the exact place never leaves for OpenStreetMap.
          const r = await fetch(`https://nominatim.openstreetmap.org/reverse?format=jsonv2&zoom=10&lat=${Number(c.latitude).toFixed(2)}&lon=${Number(c.longitude).toFixed(2)}&accept-language=${this.lang}`);
          const a = (await r.json())?.address || {};
          const city = a.city || a.town || a.village || a.municipality || a.state || "";
          if (city && this.cand === c) {
            c.city = city;
            c.country = a.country_code ? a.country_code.toUpperCase() : "";
            this.draw();
          }
        } catch (e) {
          /* coordinates alone */
        }
      }
      async saveLoc(lat, lon, elevation) {
        if (!isFinite(lat) || !isFinite(lon)) {
          this.locMsg = this.w.geoNo;
          this.locBad = true;
          this.draw();
          return;
        }
        try {
          const body = { op: "location", latitude: Number(lat.toFixed(5)), longitude: Number(lon.toFixed(5)) };
          if (isFinite(elevation)) body.elevation = Math.round(elevation);
          await this.api("POST", body);
          this.data = await this.api("GET");
          // The house's clock: the zone of the place found by the internet, else the phone's — offered when HA's differs.
          const zone = this.cand?.time_zone || VIEWER_TZ;
          this.tzAsk = zone && this.data.location.time_zone !== zone && this._hass?.user?.is_admin ? zone : null;
          this.cand = null;
          this.hits = null;
          this.manual = false;
          this.toList({ msg: this.w.locSaved, bad: false });
          return;
        } catch (e) {
          const m = e?.body?.message || e?.message;
          this.locMsg = m ? this.w.err + m : this.w.errNet;
          this.locBad = true;
        }
        this.draw();
      }
    }
    // Defined with the other cards (define() below, once HA's own elements are there): an element defined at this
    // file's load went into a registry HA no longer looked cards up in — «Configuration error» on 2 of 8 cold loads (HA
    // expert 28.10).
    // HA draws «Custom element doesn't exist» when a card comes before this file and redraws it once the element is
    // defined — but its redraw event is lost when the card isn't on the page yet (live 28.10: «Ошибка конфигурации» now
    // and then over the whole «Автоматизации» tab). Such a card of ours is asked to redraw again.
    // Family review 28.10: a reload of the tab left the pink card for good — HA's error card keeps the message in `error`
    // now (it was `message`), so the old check never matched. Matched by the card's own type; rebuilt once the element is
    // defined; after three tries the card says to refresh instead of HA's error.
    const tries = new WeakMap();
    const heal = () => {
      const walk = (root) => {
        for (const el of root.querySelectorAll("*")) {
          if (el.tagName === "HUI-ERROR-CARD") {
            const cfg = el._config || el.config || {};
            const text = `${cfg.error || ""} ${cfg.message || ""} ${el.error || ""}`;
            const type = String(cfg.origConfig?.type || "");
            const tag = (type.match(/^custom:(hauflo-[a-z-]+)$/) || text.match(/\b(hauflo-[a-z-]+)\b/) || [])[1];
            if (tag && text.includes(tag)) {
              const n = (tries.get(el) || 0) + 1;
              tries.set(el, n);
              const rebuild = () => el.dispatchEvent(new Event("ll-rebuild", { bubbles: true, composed: true }));
              if (n > 3 && !el.nextElementSibling?.classList?.contains("hauflo-fail")) {
                const ru = langOf(AGENT.hass) === "ru";
                const box = document.createElement("div");
                box.className = "hauflo-fail";
                box.style.cssText = "padding:16px;border-radius:20px;background:var(--ha-card-background,var(--card-background-color,#fff));font:500 15px/1.4 system-ui,sans-serif;display:flex;gap:12px;align-items:center;flex-wrap:wrap";
                box.innerHTML = `<span style="flex:1;min-width:200px">${ru ? "Не загрузилось — обновите страницу." : "Didn't load — refresh the page."}</span><button style="font:inherit;border:0;border-radius:12px;min-height:44px;padding:8px 16px;cursor:pointer">${ru ? "Обновить" : "Refresh"}</button>`;
                box.querySelector("button").addEventListener("click", () => location.reload());
                el.style.display = "none";
                el.insertAdjacentElement("afterend", box);
              } else if (customElements.get(tag)) rebuild();
              else customElements.whenDefined(tag).then(rebuild);
            }
          }
          if (el.shadowRoot) walk(el.shadowRoot);
        }
      };
      if (/^\/hauflo-/.test(location.pathname)) walk(document);
    };
    for (const ms of [800, 1500, 3000, 5000, 9000, 15000]) setTimeout(heal, ms);
    window.addEventListener("location-changed", () => [600, 1500, 4000].forEach((ms) => setTimeout(heal, ms)));

    // From the settings sheet: the dashboard's own tab when it has one, otherwise the same page over the screen.
    const find = (root, sel) => {
      for (const el of root.querySelectorAll("*")) {
        if (el.matches(sel)) return el;
        const inner = el.shadowRoot && find(el.shadowRoot, sel);
        if (inner) return inner;
      }
      return null;
    };
    // The card inside «Сервис» (or its own tab): scrolled to once it's drawn — to «Нет дома» when asked.
    const reach = (part, left = 30) => {
      const el = find(document, "hauflo-automations");
      const target = el && (part ? el.shadowRoot?.querySelector(part) : el);
      if (target && el.data) return target.scrollIntoView({ behavior: REDUCED ? "auto" : "smooth", block: "start" });
      if (left > 0) setTimeout(() => reach(part, left - 1), 200);
    };
    const open = (hass, part) => {
      const panel = document.querySelector("home-assistant")?.shadowRoot?.querySelector("home-assistant-main")?.shadowRoot?.querySelector("ha-drawer partial-panel-resolver ha-panel-lovelace");
      const views = panel?.lovelace?.config?.views || [];
      const base = location.pathname.split("/")[1];
      const page = views.find((v) => JSON.stringify(v.cards || v.sections || []).includes("hauflo-automations"));
      if (page?.path) {
        if (location.pathname !== `/${base}/${page.path}`) {
          history.pushState(null, "", `/${base}/${page.path}`);
          window.dispatchEvent(new CustomEvent("location-changed", { detail: { replace: false } }));
        }
        setTimeout(() => reach(part), 300);
        return;
      }
      const wrap = document.createElement("div");
      wrap.style.cssText = "position:fixed;inset:0;z-index:9999;overflow:auto;background:var(--primary-background-color,#f4f2ee);padding:12px 16px calc(24px + env(safe-area-inset-bottom))";
      const el = document.createElement("hauflo-automations");
      el.setConfig({ overlay: true });
      el.hass = hass;
      const keep = setInterval(() => {
        const h = document.querySelector("home-assistant")?.hass;
        if (h && h !== el._hass) el.hass = h;
      }, 1000);
      const close = () => {
        clearInterval(keep);
        wrap.remove();
        document.removeEventListener("keydown", onKey);
      };
      const onKey = (e) => e.key === "Escape" && close();
      document.addEventListener("keydown", onKey);
      el.addEventListener("hauflo-close", close);
      wrap.appendChild(el);
      document.body.appendChild(wrap);
    };
    return { open, Automations };
  })();

  // --- «Настройки» of the client's dashboard (owner 27.10: «непонятно, как переключить тему»; «видимый переключатель не
  // стоит — в настройках»; «кнопка зима/лето»): a gear in the header of a Hauflo dashboard → the theme (HA's own choice,
  // the one of the profile page: kept for the person, «как на телефоне» follows the phone) and the house's season.
  (() => {
    const W = {
      ru: {
        title: "Настройки", close: "Закрыть", theme: "Тема", light: "Светлая", dark: "Тёмная", auto: "Как на телефоне",
        themeHint: "Запоминается для вас. «Как на телефоне» — днём светлая, ночью тёмная, если так настроен телефон.",
        season: "Сезон", winter: "Зима", winterSub: "кондиционеры греют", summer: "Лето", summerSub: "кондиционеры охлаждают",
        now: (m) => `Сейчас: ${m === "heat" ? "зима — кондиционеры на обогреве" : "лето — кондиционеры на охлаждении"}.`,
        mixed: (h, c) => `Сейчас по-разному: на обогреве ${h}, на охлаждении ${c}.`,
        allOff: (m) => `Кондиционеры выключены.${m ? ` Последний выбор — ${m === "heat" ? "зима" : "лето"}.` : ""}`,
        seasonHint: (n) => `Одной кнопкой — все ${n} кондиционер${n % 10 === 1 && n % 100 !== 11 ? "" : n % 10 >= 2 && n % 10 <= 4 && (n % 100 < 10 || n % 100 >= 20) ? "а" : "ов"} дома. Выключенные не включаются. Тёплые полы и батареи не трогаем.`,
        busy: "Переключаем…",
        done: (r) => [r.changed.length && `Переключили: ${r.changed.length}.`, r.sent.length && `Выключенным задан режим (не включали): ${r.names(r.sent)}.`, r.skipped.length && `Выключены, не трогали: ${r.names(r.skipped)} — включите и выберите режим в комнате.`, r.failed.length && `Не ответили: ${r.names(r.failed)}.`].filter(Boolean).join(" ") || "Уже так.",
        error: "Не получилось — проверьте связь с домом и попробуйте ещё раз.",
        list: "Кондиционеры",
        autos: "Автоматизации", autosSub: "Шторы и свет по рассвету, закату, времени, будильнику",
        heat: "обогрев", cool: "охлаждение", off: "выкл.",
        view: "Вид", scenes: "Сценарии", scenesLight: "Сценарии света", scenesCover: "Сценарии штор", inBlocks: "В своих блоках", onTop: "Сверху комнаты", climate: "Кондиционеры и термостаты", floor: "Тёплые полы",
        textSize: "Размер текста", tsPhone: "Как в телефоне", tsNormal: "Обычный", tsBig: "Крупнее", tsSmall: "Мельче",
        tsHint: "Только на этом устройстве. «Обычный» — не зависит от крупного шрифта в телефоне; «Как в телефоне» — как приложение Home Assistant показывает сейчас.",
        large: "Крупно", compact: "Компактно", reset: "Как настроил интегратор",
        viewHint: (d) => `Только для вас, на этом и других ваших устройствах. Интегратор настроил: ${d}.`,
        saved: "Сохранено", viewError: "Не сохранилось — проверьте связь с домом.",
        names: "Названия блоков", namesHint: "Сохраняются сразу, для всего дома — так увидят все жители. Пусто — как у интегратора. Названия остаются при следующих выкатках.",
        namesError: "Не сохранилось — нет связи с домом или агент Hauflo устарел (нужен 0.14.7+).",
        ro: "Детский профиль: настройки дома меняют взрослые. Свет, шторы и температура — ваши.",
        tsHuge: "Ещё крупнее", pro: "Для специалиста", guest: "Гостевой доступ", guestSub: "Ссылка или QR для гостя на время",
        fav: "Избранное", unfav: "Убрать", favHint: "Долго нажмите на свет, штору или сценарий → «В избранное». Избранное — сверху «Главной», только у вас.",
        myRoom: "Моя комната", myRoomHint: "Ваша комната — первой в «Избранном» на «Главной».",
        seasonAsk: (m, n) => `Точно? Все ${n} кондиционер${n % 10 === 1 && n % 100 !== 11 ? "" : n % 10 >= 2 && n % 10 <= 4 && (n % 100 < 10 || n % 100 >= 20) ? "а" : "ов"} дома переключатся на ${m === "heat" ? "обогрев" : "охлаждение"}.`,
        yesWinter: "Да, зима", yesSummer: "Да, лето", cancel: "Отмена",
        editors: "Кто может менять настройки дома", editorsHint: "Как в Google Home: все взрослые дома равны. Отметьте ребёнка — «Ограниченный (детский) профиль»: он управляет светом, шторами и температурой и принимает аварии, но не меняет автоматизации, сценарии, сезон, «Нет дома», гостевые ссылки, названия и порядок блоков.",
        restricted: "Ограниченный (детский) профиль",
        editorsMe: (ok) => (ok ? "Вы можете менять настройки дома." : "У вас детский профиль: свет, шторы и температура — ваши, настройки дома меняют взрослые."), admin: "администратор", editorsError: "Не сохранилось — проверьте связь с домом.",
        service: "Кто обслуживает дом", noService: "Контакт сервиса пока не указан — интегратор добавит его при следующей выкатке.", serviceHint: "Неисправность, авария, вопрос по дому — звоните сюда.",
      },
      en: {
        title: "Settings", close: "Close", theme: "Theme", light: "Light", dark: "Dark", auto: "As the phone",
        themeHint: "Kept for you. «As the phone» follows the phone's light or dark mode.",
        season: "Season", winter: "Winter", winterSub: "ACs heat", summer: "Summer", summerSub: "ACs cool",
        now: (m) => `Now: ${m === "heat" ? "winter — the ACs heat" : "summer — the ACs cool"}.`,
        mixed: (h, c) => `Now mixed: ${h} heating, ${c} cooling.`,
        allOff: (m) => `The ACs are off.${m ? ` Last chosen: ${m === "heat" ? "winter" : "summer"}.` : ""}`,
        seasonHint: (n) => `One tap for all ${n} AC${n === 1 ? "" : "s"} of the house. The ones off stay off. Floor heating and radiators aren't touched.`,
        busy: "Switching…",
        done: (r) => [r.changed.length && `Switched: ${r.changed.length}.`, r.sent.length && `Mode set on the ones off (not switched on): ${r.names(r.sent)}.`, r.skipped.length && `Off, left as they are: ${r.names(r.skipped)} — switch on and pick the mode in the room.`, r.failed.length && `No answer: ${r.names(r.failed)}.`].filter(Boolean).join(" ") || "Already so.",
        error: "Didn't work — check the link to the house and try again.",
        list: "ACs",
        autos: "Automations", autosSub: "Blinds and lights by sunrise, sunset, the clock, your alarm",
        heat: "heating", cool: "cooling", off: "off",
        view: "View", scenes: "Scenes", scenesLight: "Light scenes", scenesCover: "Blind scenes", inBlocks: "In their blocks", onTop: "On top of the room", climate: "ACs and thermostats", floor: "Floor heating",
        textSize: "Text size", tsPhone: "As the phone", tsNormal: "Normal", tsBig: "Larger", tsSmall: "Smaller",
        tsHint: "On this device only. «Normal» ignores the large font of the phone; «As the phone» is how the Home Assistant app shows it now.",
        large: "Large", compact: "Compact", reset: "As the integrator set it",
        viewHint: (d) => `Just for you, on this and your other devices. The integrator set: ${d}.`,
        saved: "Saved", viewError: "Not saved — check the link to the house.",
        names: "Block titles", namesHint: "Saved at once, for the whole house — everyone living here sees them. Empty — as the integrator set it. The titles stay over the next deploys.",
        namesError: "Not saved — no link to the house, or the Hauflo agent is out of date (0.14.7+ needed).",
        ro: "Child profile: the home's settings are changed by the adults. Lights, blinds and temperature are yours.",
        tsHuge: "Largest", pro: "For the installer", guest: "Guest access", guestSub: "A link or QR for a guest, for a while",
        fav: "Favourites", unfav: "Remove", favHint: "Long-press a light, blind or scene → «Add to favourites». Favourites are on top of «Home», just for you.",
        myRoom: "My room", myRoomHint: "Your room — first among the favourites on «Home».",
        seasonAsk: (m, n) => `Sure? All ${n} AC${n === 1 ? "" : "s"} of the home switch to ${m === "heat" ? "heating" : "cooling"}.`,
        yesWinter: "Yes, winter", yesSummer: "Yes, summer", cancel: "Cancel",
        editors: "Who may change the home's settings", editorsHint: "As in Google Home: all adults of the home are equal. Tick a child — «Restricted (child) profile»: they control lights, blinds and temperature and accept alarms, but don't change automations, scenes, the season, «Away», guest links, block titles or order.",
        restricted: "Restricted (child) profile",
        editorsMe: (ok) => (ok ? "You may change the home's settings." : "You have a child profile: lights, blinds and temperature are yours, the adults change the home's settings."), admin: "administrator", editorsError: "Not saved — check the link to the house.",
        service: "Who looks after the house", noService: "No service contact yet — the integrator adds it with the next deploy.", serviceHint: "A fault, an alarm, a question about the house — call here.",
      },
    };
    const root = () => document.querySelector("home-assistant");
    const hassOf = () => root()?.hass;
    const words = () => W[langOf(hassOf())];
    const themeNow = () => {
      const d = hassOf()?.selectedTheme?.dark;
      return d === true ? "dark" : d === false ? "light" : "auto";
    };
    // HA's own mechanism (its profile page fires the same): applied at once, saved for the person, kept over reloads.
    const setTheme = (v) => root()?.dispatchEvent(new CustomEvent("settheme", { detail: { dark: v === "dark" ? true : v === "light" ? false : undefined }, bubbles: true, composed: true }));
    /** The ACs (heat + cool) — from the agent; without it (an older agent) from the states. */
    const localAcs = (h) =>
      Object.values(h?.states || {})
        .filter((s) => s.entity_id.startsWith("climate.") && (s.attributes.hvac_modes || []).includes("heat") && (s.attributes.hvac_modes || []).includes("cool") && !nd(s))
        .map((s) => ({ entity_id: s.entity_id, name: nameOf(s, s.entity_id), state: s.state }));
    const loadSeason = async (h) => {
      try {
        return { ...(await h.callApi("GET", "hauflo/season")), agent: true };
      } catch (e) {
        const list = localAcs(h);
        const on = list.filter((a) => a.state === "heat" || a.state === "cool").map((a) => a.state);
        return { acs: list, season: on.length ? (on.filter((m) => m === "heat").length >= on.length / 2 ? "heat" : "cool") : null, agent: false };
      }
    };
    const applySeason = async (h, mode, info) => {
      if (info.agent) return h.callApi("POST", "hauflo/season", { mode });
      const r = { changed: [], sent: [], skipped: [], failed: [] };
      for (const a of info.acs) {
        if (a.state === mode) continue;
        if (a.state === "off") {
          r.skipped.push(a.entity_id);
          continue;
        }
        try {
          await call(h, "climate", "set_hvac_mode", { entity_id: a.entity_id, hvac_mode: mode });
          r.changed.push(a.entity_id);
        } catch (e) {
          r.failed.push(a.entity_id);
        }
      }
      return r;
    };
    const CSS2 = `:host{position:fixed;inset:0;z-index:9999;display:grid;place-items:end center;background:rgba(0,0,0,.42);font-family:Onest,var(--ha-font-family-body),system-ui,sans-serif;color:var(--eh-ink,var(--primary-text-color));-webkit-tap-highlight-color:transparent}
@media (min-width:600px){:host{place-items:center}}
*{box-sizing:border-box}
.sh{width:min(100%,460px);max-height:92vh;overflow:auto;background:var(--card-background-color,#fff);background-image:var(--eh-glass,none);border-radius:28px 28px 0 0;padding:20px 20px calc(20px + env(safe-area-inset-bottom));box-shadow:0 20px 60px rgba(0,0,0,.35)}
@media (min-width:600px){.sh{border-radius:28px}}
.hd{display:flex;align-items:center;justify-content:space-between;margin-bottom:8px}.hd h2{font:600 20px/1.2 Onest,sans-serif;margin:0;letter-spacing:-.01em}
.x{width:44px;height:44px;border-radius:22px;border:0;background:var(--eh-track,rgba(127,127,127,.12));color:inherit;font-size:22px;cursor:pointer;display:grid;place-items:center}.x ha-icon{--mdc-icon-size:22px}
.caps{font:500 11px/1.2 Onest,sans-serif;letter-spacing:.14em;text-transform:uppercase;color:var(--eh-ink2,var(--secondary-text-color));margin:18px 0 8px}
.seg{display:grid;grid-template-columns:repeat(3,1fr);gap:6px;padding:4px;border-radius:18px;background:var(--eh-track,rgba(127,127,127,.12))}
.seg button,.big button{font:500 14px Onest,sans-serif;color:inherit;border:0;border-radius:14px;min-height:44px;padding:8px;background:none;cursor:pointer}
.seg button[aria-pressed="true"]{background:var(--card-background-color,#fff);box-shadow:0 1px 3px rgba(0,0,0,.18);font-weight:600}
.hint{font:400 13px/1.45 Onest,sans-serif;color:var(--eh-ink2,var(--secondary-text-color));margin:8px 2px 0}
.big{display:grid;grid-template-columns:1fr 1fr;gap:8px}
.big button{display:flex;flex-direction:column;align-items:flex-start;gap:4px;padding:14px;border-radius:20px;background:var(--eh-track,rgba(127,127,127,.12));text-align:left;min-height:84px}
.big button b{display:flex;align-items:center;gap:8px;font:600 17px Onest,sans-serif}.big button span{font:400 12px Onest,sans-serif;color:var(--eh-ink2,var(--secondary-text-color))}
.big button[aria-pressed="true"].heat{background:color-mix(in srgb,var(--eh-amber,#E8901F) 22%,transparent);box-shadow:inset 0 0 0 2px var(--eh-amber,#E8901F)}
.big button[aria-pressed="true"].cool{background:color-mix(in srgb,var(--eh-ice,#3C82F0) 22%,transparent);box-shadow:inset 0 0 0 2px var(--eh-ice,#3C82F0)}
.big button:disabled{opacity:.55;cursor:default}
ha-icon{--mdc-icon-size:20px}
.now{font:500 14px/1.4 Onest,sans-serif;margin:0 2px 10px}.res{font:400 13px/1.45 Onest,sans-serif;margin:10px 2px 0}
details{margin-top:10px;font:400 13px Onest,sans-serif;color:var(--eh-ink2,var(--secondary-text-color))}summary{cursor:pointer;min-height:32px;display:flex;align-items:center}
ul{margin:4px 0 0;padding:0 0 0 18px}li{margin:2px 0}
.seg.two{grid-template-columns:1fr 1fr}.seg.ts button:first-child{grid-column:span 2}.vl{font:500 13px Onest,sans-serif;margin:12px 2px 6px}
.nm{display:flex;flex-direction:column;gap:8px}.nm label{display:flex;flex-direction:column;gap:4px;font:400 12px Onest,sans-serif;color:var(--eh-ink2,var(--secondary-text-color))}
.nm input{min-height:44px;padding:0 12px;border-radius:14px;border:1px solid var(--divider-color,rgba(127,127,127,.3));background:var(--eh-track,rgba(127,127,127,.08));color:inherit;font:500 15px Onest,sans-serif;width:100%}
.nm input:focus{outline:2px solid var(--eh-ice,#3C82F0);outline-offset:1px}
.reset{margin-top:10px;min-height:40px;padding:0 14px;border:0;border-radius:14px;background:var(--eh-track,rgba(127,127,127,.12));color:inherit;font:500 13px Onest,sans-serif;cursor:pointer}.reset:disabled{opacity:.45;cursor:default}
.go{display:flex;align-items:center;gap:12px;width:100%;margin:18px 0 0;padding:14px;border:0;border-radius:20px;background:var(--eh-track,rgba(127,127,127,.12));color:inherit;text-align:left;cursor:pointer;min-height:64px}
.go.ord{margin-top:8px;min-height:56px}.go.ord ha-icon{color:var(--eh-ink2,var(--secondary-text-color))}
.call{display:flex;align-items:center;gap:12px;min-height:56px;padding:8px 16px;border-radius:18px;text-decoration:none;color:inherit;background:color-mix(in srgb,var(--eh-mint,#2FA67A) 16%,transparent);box-shadow:inset 0 0 0 1px color-mix(in srgb,var(--eh-mint,#2FA67A) 45%,transparent);font:600 15px Onest,sans-serif}.call ha-icon{--mdc-icon-size:22px;color:var(--eh-mint-text,#1F7A57)}.call .cl{flex:1;min-width:0;display:flex;flex-direction:column;gap:2px}.call .cl span{overflow-wrap:break-word;hyphens:auto}.call b{font:400 14px Onest,sans-serif;color:var(--eh-ink2,var(--secondary-text-color))}
.ask{margin-top:10px;min-height:120px;padding:12px;border-radius:16px;background:color-mix(in srgb,var(--eh-amber,#E8901F) 16%,transparent);box-shadow:inset 3px 0 0 var(--eh-amber,#E8901F)}.ask b{display:block;font:600 14px Onest,sans-serif;line-height:1.4}.ask .r2{display:flex;gap:8px;margin-top:10px;flex-wrap:wrap}.ask button{min-height:44px;padding:0 16px;border:0;border-radius:14px;font:600 14px Onest,sans-serif;cursor:pointer;background:var(--eh-track,rgba(127,127,127,.12));color:inherit}.ask button.yes{background:var(--eh-ink,#1c1c1e);color:var(--card-background-color,#fff)}
.fl{display:flex;flex-direction:column;gap:6px}.fi{display:flex;align-items:center;gap:8px;min-height:48px;padding:0 4px 0 14px;border-radius:14px;background:var(--eh-track,rgba(127,127,127,.08));font:500 15px Onest,sans-serif}.fi span{flex:1;min-width:0;overflow-wrap:break-word;hyphens:auto}.fi button{width:44px;height:44px;border:0;border-radius:12px;background:none;color:inherit;cursor:pointer;display:grid;place-items:center}
.rch{display:flex;flex-wrap:wrap;gap:6px}.rch button{min-height:44px;padding:0 14px;border:0;border-radius:14px;background:var(--eh-track,rgba(127,127,127,.12));color:inherit;font:500 14px Onest,sans-serif;cursor:pointer}.rch button[aria-pressed="true"]{background:var(--eh-ink,#1c1c1e);color:var(--card-background-color,#fff)}
details.pro{margin-top:22px;border-top:1px solid var(--divider-color,rgba(127,127,127,.25));padding-top:6px}details.pro>summary{font:600 15px Onest,sans-serif;color:var(--eh-ink,var(--primary-text-color));min-height:48px;list-style-position:inside}
.ed{display:flex;flex-direction:column;gap:6px}.ed label{display:flex;align-items:center;gap:12px;min-height:48px;padding:6px 12px;border-radius:14px;background:var(--eh-track,rgba(127,127,127,.08));font:500 15px Onest,sans-serif;cursor:pointer}.ed input{width:22px;height:22px;flex:none;accent-color:var(--eh-ink,#1c1c1e);margin-left:auto}.ed .en{display:flex;flex-direction:column;min-width:0}.ed small{color:var(--eh-ink2,var(--secondary-text-color));font:400 max(13px,.85em) Onest,sans-serif}
.nm input:disabled,.go:disabled{opacity:.55;cursor:default}
.go span{flex:1;display:flex;flex-direction:column;gap:2px}.go b{font:600 15px Onest,sans-serif}.go small{font:400 12px/1.35 Onest,sans-serif;color:var(--eh-ink2,var(--secondary-text-color))}.go i{font-style:normal;font-size:22px;opacity:.6}.go ha-icon{color:var(--eh-amber,#E8901F)}
`;
    /** What this dashboard shows that the client may switch, and the integrator's choice for each. */
    const viewDefaults = async (h) => {
      const out = {};
      try {
        const cfg = await h.callWS({ type: "lovelace/config", url_path: location.pathname.split("/")[1] });
        const walk = (v) => {
          if (Array.isArray(v)) return v.forEach(walk);
          if (!v || typeof v !== "object") return;
          if (v.scene_layout && out.scenes === undefined) out.scenes = v.scene_layout;
          if (v.scene_layout && out.cover_scenes === undefined) out.cover_scenes = v.cover_scene_layout || v.scene_layout;
          // The blocks the client may rename, each with the title the integrator's deploy gave it (the first one seen).
          if (typeof v.block === "string" && typeof v.type === "string" && v.type.startsWith("custom:hauflo-") && !(v.block in (out._blocks ||= {}))) out._blocks[v.block] = String(v.title || v.block);
          if (v.type === "custom:hauflo-climate" && v.size_class && !v.zones) {
            const k = v.size_class === "floor" ? "floor" : "climate";
            // The room's main one says it: a second thermostat of a room is a line anyway.
            if (out[k] === undefined || (out[k] === "compact" && !v.compact)) out[k] = v.compact ? "compact" : "large";
          }
          Object.values(v).forEach(walk);
        };
        walk(cfg);
      } catch (e) {
        /* no config: nothing to switch */
      }
      return out;
    };
    let sheet = null;
    // Every change applies at once and nothing moves (owner 29.10, phone: «в настройках любое действие отбрасывает вверх»,
    // «сохранение выходит из настроек — бесит»): iOS / HA settings — a control changes in place, a small «Сохранено»
    // says it's kept; the sheet is drawn once, its parts that load later are put in their boxes without a redraw; it
    // closes only by ✕, the backdrop or Escape. «Порядок» opens over it and comes back to it.
    const openSettings = async (focus) => {
      if (sheet) return;
      const h = hassOf();
      if (!h) return;
      const w = words();
      sheet = document.createElement("div");
      sheet.className = "hauflo-settings";
      const sr = sheet.attachShadow({ mode: "open" });
      const close = () => {
        sheet?.remove();
        sheet = null;
        clearTimeout(namesT);
        document.removeEventListener("keydown", onKey);
      };
      // Escape closes the sheet on top: «Порядок» over the settings goes first.
      const onKey = (e) => e.key === "Escape" && !orderOpen && close();
      document.addEventListener("keydown", onKey);
      let info = null;
      let res = "";
      let busy = false;
      let defs = {};
      let namesT = 0;
      let namesQ = Promise.resolve();
      loadTitles(h);
      const saved = (ok, err) => sheetToast(sr, ok ? w.saved : err, !ok);
      const pressed = (sel, on) => sr.querySelectorAll(sel).forEach((b) => b.setAttribute("aria-pressed", String(on(b))));
      const VIEW_OPTS = () =>
        [
          ["scenes", w.scenesLight, [["blocks", w.inBlocks], ["top", w.onTop]]],
          ["cover_scenes", w.scenesCover, [["blocks", w.inBlocks], ["top", w.onTop]]],
          ["climate", w.climate, [["large", w.large], ["compact", w.compact]]],
          ["floor", w.floor, [["large", w.large], ["compact", w.compact]]],
        ].filter(([k]) => defs[k] !== undefined);
      const viewNow = (k) => {
        const mine = VIEW.data || {};
        return (k === "cover_scenes" ? mine.cover_scenes ?? mine.scenes : mine[k]) ?? defs[k];
      };
      const canReset = () => Object.keys(VIEW.data || {}).some((k) => defs[k] !== undefined) || (Object.keys(TITLES.data || {}).length > 0 && canEdit(hassOf()));
      /** «Названия блоков» (owner 28.10): for the whole house, kept by the agent; the integrator's title as the placeholder. */
      const namesBlock = () => {
        const blocks = Object.entries(defs._blocks || {});
        if (!blocks.length) return "";
        const own = TITLES.data || {};
        const ro = !canEdit(hassOf());
        return `<p class="vl">${esc(w.names)}</p><div class="nm">${blocks
          .map(([k, t]) => `<label><span>${esc(t)}</span><input data-name="${esc(k)}" maxlength="40" placeholder="${esc(t)}" value="${esc(own[k] ?? "")}" autocomplete="off" enterkeyhint="done"${ro ? " disabled" : ""}></label>`)
          .join("")}</div><p class="hint">${esc(ro ? w.ro : w.namesHint)}</p>`;
      };
      /** «Вид»: scenes in blocks / on top, ACs and floor heating large / compact — what this dashboard has. */
      const viewBlock = () => {
        const opts = VIEW_OPTS();
        const names = namesBlock();
        if (!opts.length && !names) return "";
        const said = opts.map(([k, label, vs]) => `${label.toLowerCase()} — ${vs.find(([v]) => v === defs[k])[1].toLowerCase()}`).join("; ");
        return `<div class="caps">${esc(w.view)}</div>${opts
          .map(([k, label, vs]) => `<p class="vl">${esc(label)}</p><div class="seg two" role="group" aria-label="${esc(label)}">${vs.map(([v, l]) => `<button data-view="${k}:${v}" aria-pressed="${viewNow(k) === v}">${esc(l)}</button>`).join("")}</div>`)
          .join("")}${opts.length ? `<p class="hint">${esc(w.viewHint(said))}</p>` : ""}${names}<button class="reset" data-view-reset${canReset() ? "" : " disabled"}>${esc(w.reset)}</button>`;
      };
      /** «Порядок элементов» (owner 28.10: «как мне поменять последовательность элементов любого блока»): the blocks of
       * this page, each opens its sheet (order of the block, for the whole house) over this one. */
      let blocksHere = [];
      const orderMenu = () => {
        blocksHere = ORDER.ok ? orderBlocksHere() : [];
        if (!blocksHere.length) return "";
        const ow = OW[langOf(h)];
        const ro = !canEdit(h);
        return `<div class="caps">${esc(ow.menu)}</div>${ro ? `<p class="hint">${esc(w.ro)}</p>` : ""}${blocksHere
          .map((b, i) => `<button class="go ord" data-order="${i}"${ro ? " disabled" : ""}><ha-icon icon="mdi:drag-variant"></ha-icon><span><b>${esc(orderName(b.card))}</b><small>${esc(ow.items(b.n))}</small></span><i aria-hidden="true">›</i></button>`)
          .join("")}<p class="hint">${esc(ow.menuHint)}</p>`;
      };
      const seasonBlock = () => {
        const acs = info?.acs || [];
        if (!acs.length) return "";
        const on = acs.filter((a) => a.state === "heat" || a.state === "cool");
        const heat = on.filter((a) => a.state === "heat").length;
        const cool = on.length - heat;
        const now = !on.length ? w.allOff(info.chosen ?? info.season) : heat && cool ? w.mixed(heat, cool) : w.now(heat ? "heat" : "cool");
        const pr = on.length && !(heat && cool) ? (heat ? "heat" : "cool") : !on.length ? (info.chosen ?? info.season) : null;
        // House-wide: only people allowed to change the house's settings, and always «Точно?» first (family review round 2:
        // a 9-year-old switched every AC to cooling with one tap).
        const ro = !canEdit(hassOf());
        const off = busy || ro ? "disabled" : "";
        const askHtml = ask && !ro ? `<div class="ask" role="alertdialog"><b>${esc(w.seasonAsk(ask, acs.length))}</b><div class="r2"><button class="yes" data-syes>${esc(ask === "heat" ? w.yesWinter : w.yesSummer)}</button><button data-sno>${esc(w.cancel)}</button></div></div>` : "";
        return `<div class="caps">${esc(w.season)}</div><p class="now" data-now>${esc(now)}</p>
<div class="big" role="group" aria-label="${esc(w.season)}">
<button class="heat" data-season="heat" aria-pressed="${pr === "heat"}" ${off}><b><ha-icon icon="hauflo:snow"></ha-icon>${esc(w.winter)}</b><span>${esc(w.winterSub)}</span></button>
<button class="cool" data-season="cool" aria-pressed="${pr === "cool"}" ${off}><b><ha-icon icon="hauflo:sun"></ha-icon>${esc(w.summer)}</b><span>${esc(w.summerSub)}</span></button></div>
${askHtml || (res ? `<div class="ask done"><p class="res" data-res role="status" style="margin:0">${esc(res)}</p></div>` : "")}<p class="hint">${esc(ro ? w.ro : w.seasonHint(acs.length))}</p>
<details><summary>${esc(w.list)} · ${acs.length}</summary><ul>${acs.map((a) => `<li>${esc(a.name)} — ${esc(w[a.state] ?? a.state)}</li>`).join("")}</ul></details>`;
      };
      // The «Вид» controls follow what's kept (VIEW / TITLES) — in place.
      const syncView = () => {
        pressed("[data-view]", (b) => {
          const [k, v] = b.dataset.view.split(":");
          return viewNow(k) === v;
        });
        // Disabled, not hidden: nothing below it moves up under the finger.
        const r = sr.querySelector("[data-view-reset]");
        if (r) r.disabled = !canReset();
      };
      // Names: kept as typed — a pause in typing, leaving the field, Enter; one save at a time, the last one wins.
      const saveNames = () => {
        clearTimeout(namesT);
        namesQ = namesQ.then(async () => {
          const next = {};
          sr.querySelectorAll("input[data-name]").forEach((i) => {
            const v = i.value.replace(/\s+/g, " ").trim().slice(0, 40);
            if (v) next[i.dataset.name] = v;
          });
          const norm = (o) => JSON.stringify(Object.keys(o).sort().map((k) => [k, o[k]]));
          if (norm(next) === norm(TITLES.data || {})) return;
          try {
            await setTitles(hassOf(), next);
            if (sheet) saved(true);
          } catch (e) {
            if (sheet) saved(false, w.namesError);
          }
          if (sheet) syncView();
        });
        return namesQ;
      };
      const bindView = () => {
        sr.querySelectorAll("[data-view]").forEach((b) => (b.onclick = async () => {
          const [k, v] = b.dataset.view.split(":");
          const mineNow = VIEW.data || {};
          const next = { ...mineNow };
          // An older version kept one choice for both kinds: it stays the blinds' until they get their own (owner 28.10).
          const coverNow = mineNow.cover_scenes || mineNow.scenes || defs.cover_scenes;
          if (v === defs[k]) delete next[k];
          else next[k] = v;
          if (k === "scenes" && next.cover_scenes === undefined) next.cover_scenes = coverNow;
          if (next.cover_scenes === defs.cover_scenes && next.scenes === undefined) delete next.cover_scenes;
          else if (k === "cover_scenes" && next.scenes !== undefined && next.cover_scenes === undefined) next.cover_scenes = v;
          const done = setViewPrefs(hassOf(), next); // VIEW is the new one at once
          syncView();
          haptic(b, "selection");
          try {
            await done;
            saved(true);
          } catch (e) {
            saved(false, w.viewError);
          }
        }));
        const reset = sr.querySelector("[data-view-reset]");
        if (reset)
          reset.onclick = async () => {
            // «Как настроил интегратор»: the person's view and the house's block titles.
            clearTimeout(namesT);
            sr.querySelectorAll("input[data-name]").forEach((i) => (i.value = ""));
            try {
              const done = setViewPrefs(hassOf(), {});
              syncView();
              await done;
              if (Object.keys(TITLES.data || {}).length && canEdit(hassOf())) await setTitles(hassOf(), {});
              saved(true);
            } catch (e) {
              saved(false, w.viewError);
            }
            syncView();
          };
        sr.querySelectorAll("input[data-name]").forEach((i) => {
          i.addEventListener("input", () => {
            clearTimeout(namesT);
            namesT = setTimeout(saveNames, 900);
          });
          i.addEventListener("keydown", (ev) => {
            if (ev.key !== "Enter") return;
            ev.preventDefault();
            i.blur();
          });
          i.addEventListener("blur", () => saveNames());
        });
      };
      const bindOrder = () =>
        sr.querySelectorAll("[data-order]").forEach((b) => (b.onclick = () => {
          const it = blocksHere[Number(b.dataset.order)];
          if (it) openOrder(it.card);
        }));
      let ask = null;
      const bindSeason = () => {
        sr.querySelectorAll("[data-season]").forEach((b) => (b.onclick = () => {
          if (busy || !canEdit(hassOf())) return;
          ask = b.dataset.season;
          haptic(b, "selection");
          fill("season", "[data-syes]");
        }));
        const no = sr.querySelector("[data-sno]");
        if (no)
          no.onclick = () => {
            const was = ask;
            ask = null;
            fill("season", `[data-season="${was}"]`);
          };
        const yes = sr.querySelector("[data-syes]");
        if (yes) yes.onclick = async () => {
          if (busy || !ask) return;
          const b = { dataset: { season: ask } };
          ask = null;
          busy = true;
          res = w.busy;
          fill("season");
          try {
            const r = await applySeason(hassOf(), b.dataset.season, info);
            const names = (ids) => ids.map((id) => (info.acs.find((a) => a.entity_id === id)?.name ?? id)).join(", ");
            res = w.done({ changed: r.changed || [], sent: r.sent || [], skipped: r.skipped || [], failed: r.failed || [], names });
          } catch (e) {
            res = w.error;
          }
          busy = false;
          // HA's states follow the bus: read again a moment later.
          await new Promise((ok) => setTimeout(ok, 800));
          info = await loadSeason(hassOf());
          if (sheet) fill("season", `[data-season="${b.dataset.season}"]`);
        };
      };
      /** «Кто обслуживает дом» (family review round 2): the company and «Позвонить», from the deploy (agent house.py). */
      const serviceBlock = () => {
        const svc = HOUSE.data?.service;
        return `<div class="caps">${esc(w.service)}</div>${svc ? `${svc.name && svc.company ? `<p class="now" data-own>${esc(svc.name)}</p>` : ""}${callLink(svc)}${svc.phone && svc.email ? `<p class="hint"><a href="mailto:${esc(svc.email)}" style="color:inherit">${esc(svc.email)}</a></p>` : ""}<p class="hint">${esc(w.serviceHint)}</p>` : `<p class="hint">${esc(w.noService)}</p>`}`;
      };
      /** «Кто может менять настройки дома» (family review round 3, Google Home): adults are equal; the admin marks a person
       * «Ограниченный (детский) профиль». Others see their own state. */
      const editorsBlock = () => {
        const d = HOUSE.data;
        if (!d) return "";
        if (!d.admin || !Array.isArray(d.users)) return `<div class="caps">${esc(w.editors)}</div><p class="hint">${esc(w.editorsMe(!!d.can_edit))}</p>`;
        const on = new Set(d.restricted || []);
        return `<div class="caps">${esc(w.editors)}</div><div class="ed">${d.users
          .map((u) => `<label><span class="en"><span data-own>${esc(u.name || "—")}</span>${u.admin ? `<small>${esc(w.admin)}</small>` : `<small>${esc(w.restricted)}</small>`}</span><input type="checkbox" role="switch" data-restrict="${esc(u.id)}" aria-label="${esc(`${u.name || "—"}: ${w.restricted}`)}" ${u.admin ? "disabled" : on.has(u.id) ? "checked" : ""}></label>`)
          .join("")}</div><p class="hint">${esc(w.editorsHint)}</p>`;
      };
      const bindEditors = () =>
        sr.querySelectorAll("[data-restrict]").forEach((i) => (i.onchange = async () => {
          const ids = [...sr.querySelectorAll("[data-restrict]:not(:disabled)")].filter((x) => x.checked).map((x) => x.dataset.restrict);
          try {
            const r = await hassOf().callApi("POST", "hauflo/house", { restricted: ids });
            HOUSE.data = { ...HOUSE.data, restricted: r.restricted };
            saved(true);
          } catch (e) {
            i.checked = !i.checked;
            saved(false, w.editorsError);
          }
        }));
      /** «Избранное» (family review round 2): what this person pinned (a long press → «В избранное»), and «Моя комната» —
       * a room tile of this dashboard, first on «Главная». Only for this person (the agent keeps it per HA user). */
      let roomsHere = [];
      const favBlock = () => {
        const f = HOUSE.data?.favorites;
        if (!HOUSE.data || HOUSE.off) return "";
        const views = lovelacePanel()?.lovelace?.config?.views || [];
        const seen = new Map();
        const walk = (v) => {
          if (Array.isArray(v)) return v.forEach(walk);
          if (!v || typeof v !== "object") return;
          if (v.type === "custom:hauflo-room" && typeof v.path === "string" && v.name && !seen.has(v.path) && /^\/hauflo-[a-z0-9_-]+\/[A-Za-z0-9_%.-]+$/.test(v.path)) seen.set(v.path, String(v.name));
          Object.values(v).forEach(walk);
        };
        walk(views);
        roomsHere = [...seen].map(([path, name]) => ({ path, name }));
        const items = (f?.items || []).filter((id) => st(hassOf(), id));
        const mine = f?.room?.path;
        return `<div class="caps">${esc(w.fav)}</div>${items.length ? `<div class="fl">${items.map((id) => `<div class="fi"><span data-own>${esc(nameOf(st(hassOf(), id), id))}</span><button data-unfav="${esc(id)}" aria-label="${esc(w.unfav)}"><ha-icon icon="mdi:close"></ha-icon></button></div>`).join("")}</div>` : ""}<p class="hint">${esc(w.favHint)}</p>${roomsHere.length ? `<p class="vl">${esc(w.myRoom)}</p><div class="rch">${roomsHere.map((r, i) => `<button data-myroom="${i}" aria-pressed="${r.path === mine}"><span data-own>${esc(r.name)}</span></button>`).join("")}</div><p class="hint">${esc(w.myRoomHint)}</p>` : ""}`;
      };
      const bindFav = () => {
        const save = async (next) => {
          try {
            await favSave(hassOf(), next);
            saved(true);
          } catch (e) {
            saved(false, w.editorsError);
          }
          fill("fav");
        };
        sr.querySelectorAll("[data-unfav]").forEach((b) => (b.onclick = () => {
          const f = HOUSE.data?.favorites || { items: [] };
          save({ ...f, items: f.items.filter((x) => x !== b.dataset.unfav) });
        }));
        sr.querySelectorAll("[data-myroom]").forEach((b) => (b.onclick = () => {
          const f = { ...(HOUSE.data?.favorites || { items: [] }) };
          const r = roomsHere[Number(b.dataset.myroom)];
          if (!r) return;
          if (f.room?.path === r.path) delete f.room;
          else f.room = r;
          haptic(b, "selection");
          save(f);
        }));
      };
      const BOX = { fav: [favBlock, bindFav], editors: [editorsBlock, bindEditors], service: [serviceBlock, () => {}], view: [viewBlock, bindView], order: [orderMenu, bindOrder], season: [seasonBlock, bindSeason] };
      /** One box drawn again, the sheet where it was (its scroll, the focused control). */
      const fill = (name, focus) => {
        const box = sr.querySelector(`[data-box="${name}"]`);
        if (!box) return;
        const [html, bind] = BOX[name];
        // A name being typed isn't drawn over (the view box loads once; a late redraw would drop the letters).
        if (name === "view" && sr.activeElement?.matches?.("input[data-name]")) return;
        keepPlace(sr, () => {
          box.innerHTML = html();
          bind();
          if (focus) box.querySelector(focus)?.focus({ preventScroll: true });
        });
      };
      const th = themeNow();
      sr.innerHTML = `<style>${TZ(CSS2 + TOAST_CSS)}</style><div class="sh" role="dialog" aria-modal="true" aria-label="${esc(w.title)}">
<div class="hd"><h2>${esc(w.title)}</h2><button class="x" data-x aria-label="${esc(w.close)}"><ha-icon icon="mdi:close"></ha-icon></button></div>
<div class="caps">${esc(w.textSize)}</div>
<div class="seg two ts" role="group" aria-label="${esc(w.textSize)}">${[["phone", w.tsPhone], ["small", w.tsSmall], ["normal", w.tsNormal], ["big", w.tsBig], ["huge", w.tsHuge]].map(([v, l]) => `<button data-ts="${v}" aria-pressed="${TS.mode === v}">${esc(l)}</button>`).join("")}</div>
<p class="hint">${esc(w.tsHint)}</p>
<div class="caps">${esc(w.theme)}</div>
<div class="seg" role="group" aria-label="${esc(w.theme)}">${["light", "dark", "auto"].map((v) => `<button data-theme="${v}" aria-pressed="${th === v}">${esc(w[v])}</button>`).join("")}</div>
<p class="hint">${esc(w.themeHint)}</p>
<div data-box="season"></div><div data-box="service"></div><div data-box="fav"></div>
<button class="go" data-guest><ha-icon icon="mdi:account-key-outline"></ha-icon><span><b>${esc(w.guest)}</b><small>${esc(w.guestSub)}</small></span><i aria-hidden="true">›</i></button>
<details class="pro" data-pro><summary>${esc(w.pro)}</summary>
<button class="go" data-autos><ha-icon icon="mdi:weather-sunset"></ha-icon><span><b>${esc(w.autos)}</b><small>${esc(w.autosSub)}</small></span><i aria-hidden="true">›</i></button>
<div data-box="view"></div><div data-box="order"></div><div data-box="editors"></div>
</details>
</div>`;
      sr.querySelector("[data-x]").onclick = close;
      sr.querySelector("[data-autos]").onclick = () => {
        close();
        AUTO.open(hassOf());
      };
      sr.querySelector("[data-guest]").onclick = () => {
        close();
        AUTO.open(hassOf(), ".guest");
      };
      sr.querySelectorAll("[data-ts]").forEach((b) => (b.onclick = () => {
        tsSet(b.dataset.ts);
        pressed("[data-ts]", (x) => x.dataset.ts === TS.mode);
        haptic(b, "selection");
        saved(true);
      }));
      sr.querySelectorAll("[data-theme]").forEach((b) => (b.onclick = () => {
        setTheme(b.dataset.theme);
        pressed("[data-theme]", (x) => x === b);
        haptic(b, "selection");
        saved(true);
      }));
      // A tap on the dim backdrop closes; inside the sheet (a shadow root — retargeted to the host) it doesn't.
      sheet.addEventListener("click", (e) => e.composedPath()[0] === sheet && close());
      document.body.appendChild(sheet);
      sr.querySelector("[data-x]")?.focus({ preventScroll: true });
      const ordered = loadOrder(h);
      if (ordered) ordered.then(() => sheet && fill("order"));
      else fill("order");
      fill("service");
      fill("fav");
      fill("editors");
      const toEditors = () => {
        if (focus !== "editors" || !sheet) return;
        const d = sr.querySelector("details[data-pro]");
        if (d) d.open = true;
        sr.querySelector('[data-box="editors"]')?.scrollIntoView({ block: "start" });
      };
      // After the house's answer came (the list is drawn), at most 3 s.
      if (focus === "editors") {
        const t0 = Date.now();
        const tick = () => (HOUSE.data || Date.now() - t0 > 3000 ? setTimeout(toEditors, 100) : setTimeout(tick, 150));
        tick();
      }
      if (!HOUSE.data) {
        HOUSE.get();
        // Who may change what came: the parts that depend on it are drawn again (in place).
        HOUSE.wait?.then(() => sheet && ["service", "fav", "editors", "view", "order", "season"].forEach((b) => fill(b)));
      }
      defs = await viewDefaults(h);
      if (!sheet) return;
      fill("view");
      info = await loadSeason(h);
      if (sheet) fill("season");
    };
    const GEAR = ".hauflo-gear{display:inline-flex;align-items:center;justify-content:center;width:48px;height:48px;margin:0 2px;border:0;border-radius:24px;background:none;color:var(--app-header-text-color,var(--primary-text-color));cursor:pointer;flex:none;-webkit-tap-highlight-color:transparent}.hauflo-gear:hover{background:rgba(127,127,127,.12)}.hauflo-gear ha-icon{--mdc-icon-size:calc(22px*min(var(--hf-ts,1),1.3))}";
    let deep = (() => {
      try {
        return new URLSearchParams(location.search).get("hauflo") === "editors" ? "editors" : null;
      } catch (e) {
        return null;
      }
    })();
    setInterval(() => {
      if (!/^\/hauflo-/.test(location.pathname)) return;
      // Opened from HA's Repairs note («открыть настройку»): the settings at that place, once.
      if (deep && document.querySelector("home-assistant")?.hass) {
        const at = deep;
        deep = null;
        void openSettings(at);
      }
      const panel = document.querySelector("home-assistant")?.shadowRoot?.querySelector("home-assistant-main")?.shadowRoot?.querySelector("ha-drawer partial-panel-resolver ha-panel-lovelace");
      const rs = panel?.shadowRoot?.querySelector("hui-root")?.shadowRoot;
      const bar = rs?.querySelector(".toolbar");
      if (!bar || bar.querySelector(".hauflo-gear")) return;
      if (!rs.querySelector("style[data-hauflo-gear]")) {
        const s = document.createElement("style");
        s.dataset.haufloGear = "1";
        s.textContent = GEAR;
        rs.appendChild(s);
      }
      const b = document.createElement("button");
      b.className = "hauflo-gear";
      b.setAttribute("aria-label", words().title);
      b.title = words().title;
      b.innerHTML = `<ha-icon icon="mdi:cog-outline"></ha-icon>`; // a plain gear — Hauflo's own one reads as a sun
      b.addEventListener("click", () => void openSettings());
      // Beside HA's own buttons, not inside them: the wall tablet (kiosk) hides those, not this one.
      const items = bar.querySelector(".action-items");
      if (items) bar.insertBefore(b, items);
      else bar.appendChild(b);
    }, 700);
  })();

  // --- kiosk: a wall tablet without HA's sidebar and buttons (owner 2026-10-25: «киоск режим важно») ----
  // Opened once with «?kiosk» the tablet remembers it: no sidebar, no menu/search/Assist/edit — the floor
  // tabs and the back arrow stay. «?kiosk=full» hides the whole header; «?kiosk=0» turns it off.
  (() => {
    let mode = null;
    try {
      const q = new URLSearchParams(location.search).get("kiosk");
      if (q !== null) {
        if (q === "0" || q === "off") localStorage.removeItem("hauflo_kiosk");
        else localStorage.setItem("hauflo_kiosk", q === "full" ? "full" : "1");
      }
      mode = localStorage.getItem("hauflo_kiosk");
    } catch (e) {
      mode = null;
    }
    if (!mode) return;
    // No sidebar in kiosk: the room layouts' breakpoints (generator) move down by its width.
    const KIOSK_MQ = { "(min-width: 1300px)": "(min-width: 1024px)", "(min-width: 700px) and (max-width: 1299.98px)": "(min-width: 700px) and (max-width: 1023.98px)" };
    const mm = window.matchMedia.bind(window);
    window.matchMedia = (q) => mm(KIOSK_MQ[q] ?? q);
    // Nothing of HA's chrome flashes on a (night) reload: the page stays hidden until kiosk is in place (≤4 s).
    document.documentElement.style.visibility = "hidden";
    const reveal = () => (document.documentElement.style.visibility = "");
    setTimeout(reveal, 4000);
    const style = (root, css) => {
      if (!root) return false;
      if (root.querySelector?.("style[data-hauflo-kiosk]")) return true;
      const el = document.createElement("style");
      el.dataset.haufloKiosk = "1";
      el.textContent = css;
      root.appendChild(el);
      return true;
    };
    const tick = () => {
      const main = document.querySelector("home-assistant")?.shadowRoot?.querySelector("home-assistant-main");
      const ms = main?.shadowRoot;
      const a = style(ms, "ha-sidebar{display:none!important}ha-drawer{--mdc-drawer-width:0px!important;--ha-sidebar-width:0px!important;border:0!important;border-inline-end:0!important}");
      const drawer = ms?.querySelector("ha-drawer");
      style(drawer?.shadowRoot, "aside,.mdc-drawer{display:none!important;width:0!important;border:0!important;border-inline-end:0!important}.mdc-drawer-app-content,.app-content{margin-left:0!important;margin-inline-start:0!important;padding-left:0!important;border:0!important;box-shadow:none!important}");
      const panel = drawer?.querySelector("partial-panel-resolver")?.querySelector("ha-panel-lovelace");
      const rs = panel?.shadowRoot?.querySelector("hui-root")?.shadowRoot;
      // Evening: after sunset the wall tablet is a little warmer and dimmer (designer review 25.10).
      const hass = document.querySelector("home-assistant")?.hass;
      const evening = hass?.states?.["sun.sun"]?.state === "below_horizon";
      // A layer over the page that touches nothing (a filter on HA's root would move its dialogs).
      let dusk = document.getElementById("hauflo-dusk");
      if (!dusk && document.body) {
        dusk = document.createElement("div");
        dusk.id = "hauflo-dusk";
        dusk.setAttribute("style", "position:fixed;inset:0;z-index:9998;pointer-events:none;background:linear-gradient(180deg,rgba(40,20,0,.10),rgba(20,10,0,.16));mix-blend-mode:multiply;opacity:0;transition:opacity 2s ease");
        document.body.appendChild(dusk);
      }
      if (dusk) dusk.style.opacity = evening ? "1" : "0";
      const b = style(rs, mode === "full" ? ".header{display:none!important}#view{padding-top:env(safe-area-inset-top)!important;min-height:100vh}" : "ha-menu-button,.action-items,ha-button-menu,ha-icon-button.menu{display:none!important}");
      if (a && b) setTimeout(reveal, 60);
      if (mode === "full") nav(panel);
    };
    // «?kiosk=full» has no HA header: our own slim bar — back on a room page, the floors (integrator run 25.10: a dead end).
    let navKey = "";
    const nav = (panel) => {
      const views = panel?.lovelace?.config?.views;
      if (!views || !document.body) return;
      const [, dash, cur] = location.pathname.split("/");
      const view = views.find((v, i) => (v.path ?? String(i)) === cur) || views[0];
      const key = location.pathname + views.length;
      if (key === navKey) return;
      navKey = key;
      let bar = document.getElementById("hauflo-nav");
      if (!bar) {
        bar = document.createElement("div");
        bar.id = "hauflo-nav";
        bar.setAttribute("style", "position:fixed;left:50%;bottom:calc(16px + env(safe-area-inset-bottom));transform:translateX(-50%);z-index:9000;display:flex;gap:4px;padding:5px;border-radius:26px;background:var(--card-background-color,#fff);box-shadow:0 8px 30px rgba(0,0,0,.18),inset 0 0 0 1px rgba(127,127,127,.15);font:500 13px Onest,sans-serif;max-width:calc(100vw - 24px);overflow-x:auto;scrollbar-width:none");
        document.body.appendChild(bar);
      }
      const go = (path) => {
        history.pushState(null, "", path);
        window.dispatchEvent(new CustomEvent("location-changed", { detail: { replace: false } }));
      };
      // A phone: short labels («1», «2», «Здание») and tighter buttons; the bar scrolls sideways if still too wide.
      const narrow = window.innerWidth < 600;
      const btn = `border:0;background:none;color:var(--primary-text-color,#111);height:42px;padding:0 ${narrow ? 12 : 16}px;border-radius:21px;font:inherit;white-space:nowrap;cursor:pointer;flex:0 0 auto`;
      const short = (t) => (narrow ? String(t || "").replace(/^(этаж|floor)\s*/i, "").replace(/\s*(этаж|floor)$/i, "").replace(/^Всё здание$/, "Здание") : t || "");
      const items = view?.subview
        ? [`<button data-p="${esc(view.back_path || `/${dash}`)}" style="${btn}">← Назад</button>`]
        : views.filter((v) => !v.subview).map((v, i) => `<button data-p="/${dash}/${esc(v.path ?? String(i))}" style="${btn}${v === view ? ";background:rgba(127,127,127,.16)" : ""}">${esc(short(v.title))}</button>`);
      bar.innerHTML = items.join("");
      bar.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => go(b.dataset.p)));
    };
    tick();
    setInterval(tick, 100);

    // Night face: after some minutes without a touch the wall tablet shows only the time, the weather and
    // alarms, dimmed; a touch brings the house back (designer review 25.10). «?kiosk&idle=5» sets the minutes.
    let idleMin = 10;
    try {
      const v = Number(new URLSearchParams(location.search).get("idle") ?? localStorage.getItem("hauflo_kiosk_idle"));
      if (v > 0) {
        idleMin = v;
        localStorage.setItem("hauflo_kiosk_idle", String(v));
      }
    } catch (e) {
      /* default */
    }
    let last = Date.now();
    let face = null;
    const wake = () => {
      last = Date.now();
      if (face) {
        face.remove();
        face = null;
      }
    };
    ["pointerdown", "keydown", "touchstart"].forEach((ev) => window.addEventListener(ev, wake, { capture: true, passive: true }));
    const draw = () => {
      const hass = document.querySelector("home-assistant")?.hass;
      const st = hass?.states || {};
      const w = Object.values(st).find((x) => x.entity_id.startsWith("weather.") && x.entity_id.includes("forecast")) || Object.values(st).find((x) => x.entity_id.startsWith("weather."));
      const alarms = Object.values(st).filter((x) => x.entity_id.startsWith("binary_sensor.") && x.state === "on" && ["moisture", "smoke", "gas", "carbon_monoxide", "safety", "problem"].includes(x.attributes.device_class));
      // «Ошибка тёплого пола 1 · Гостиная» — the room from HA's registry (integrator run 25.10: two identical names).
      const roomOf = (id) => {
        const e = hass?.entities?.[id];
        const area = e?.area_id || (e?.device_id && hass?.devices?.[e.device_id]?.area_id);
        return area ? hass?.areas?.[area]?.name : "";
      };
      const now = new Date();
      const m = now.getMinutes();
      face.innerHTML = `<div style="display:flex;flex-direction:column;align-items:center;transform:translate(${((m * 7) % 13) - 6}px,${((m * 11) % 13) - 6}px)"><div style="font:200 min(24vw,180px)/1 Onest,sans-serif;letter-spacing:-.05em;font-variant-numeric:tabular-nums">${String(now.getHours()).padStart(2, "0")}:${String(now.getMinutes()).padStart(2, "0")}</div>
<div style="font:300 22px Onest,sans-serif;margin-top:18px;opacity:.6">${now.toLocaleDateString("ru-RU", { weekday: "long", day: "numeric", month: "long" })}${w && w.attributes.temperature !== undefined ? ` · ${Math.round(w.attributes.temperature)}° на улице` : ""}</div>
${alarms.length ? `<div style="margin-top:34px;font:500 20px Onest,sans-serif;color:#FF5A5F">● ${alarms.map((a) => esc((a.attributes.friendly_name || a.entity_id) + (roomOf(a.entity_id) ? ` (${roomOf(a.entity_id)})` : ""))).slice(0, 3).join(" · ")}</div>` : ""}</div>`;
    };
    setInterval(() => {
      if (!face && Date.now() - last > idleMin * 60000) {
        face = document.createElement("div");
        face.setAttribute("style", "position:fixed;inset:-16px;z-index:9999;background:#000;color:rgba(255,236,210,.74);display:flex;flex-direction:column;align-items:center;justify-content:center;cursor:none;opacity:0;transition:opacity .6s ease");
        document.body.appendChild(face);
        requestAnimationFrame(() => (face.style.opacity = "1"));
      }
      // The clock moves a few pixels every minute (draw), so an OLED panel doesn't keep it burned in.
      if (face) draw();
    }, 5000);
  })();

  // HA swaps in its own element registry when its core boots: what's defined before that is lost.
  // So the cards are defined once HA's own app element is there (checked against the registry of the moment).
  // A card added from HA's card picker starts filled with this home's devices (HA core review 25.10: no stub).
  const ids = (hass, domain, n, test = () => true) => Object.keys(hass?.states || {}).filter((id) => id.startsWith(domain + ".") && test(hass.states[id])).slice(0, n);
  const STUB = {
    "hauflo-hero": (h) => ({ label: "Дома", temperature: ids(h, "climate", 6), lights: ids(h, "light", 20), weather: ids(h, "weather", 1)[0] }),
    "hauflo-room": (h) => ({ name: "Гостиная", lights: ids(h, "light", 4), climate: ids(h, "climate", 1)[0], covers: ids(h, "cover", 2) }),
    "hauflo-scenes": (h) => ({ scenes: ids(h, "scene", 6).map((entity) => ({ entity })) }),
    "hauflo-climate": (h) => ({ entity: ids(h, "climate", 1)[0] }),
    "hauflo-sliders": (h) => ({ entities: [...ids(h, "light", 2, (s) => s.attributes.supported_color_modes?.some((m) => m !== "onoff")), ...ids(h, "cover", 2)] }),
    "hauflo-alert": (h) => ({ alarms: ids(h, "binary_sensor", 4, (s) => ["moisture", "smoke", "gas", "problem"].includes(s.attributes.device_class)) }),
    "hauflo-lights": (h) => ({ title: "Свет", entities: ids(h, "light", 12) }),
    "hauflo-covers": (h) => ({ title: "Шторы", entities: ids(h, "cover", 8) }),
    "hauflo-weather": (h) => ({ entity: ids(h, "weather", 1)[0] }),
    "hauflo-air": (h) => ({ pm25: ids(h, "sensor", 1, (s) => s.attributes.device_class === "pm25")[0], co2: ids(h, "sensor", 1, (s) => s.attributes.device_class === "carbon_dioxide")[0] }),
    "hauflo-media": (h) => ({ entity: ids(h, "media_player", 1)[0] }),
    "hauflo-graph": (h) => ({ entities: ids(h, "sensor", 2, (s) => s.attributes.device_class === "temperature").map((entity) => ({ entity })) }),
    "hauflo-auto": (h) => ({ entities: [...ids(h, "automation", 4), ...ids(h, "script", 2)] }),
    "hauflo-status": (h) => ({ entities: ids(h, "binary_sensor", 8, (s) => ["problem", "connectivity", "battery"].includes(s.attributes.device_class)) }),
    "hauflo-security": (h) => ({ entity: ids(h, "alarm_control_panel", 1)[0], locks: ids(h, "lock", 4), openings: ids(h, "binary_sensor", 6, (s) => ["door", "window", "motion"].includes(s.attributes.device_class)) }),
    "hauflo-camera": (h) => ({ entity: ids(h, "camera", 1)[0] }),
    "hauflo-acs": (h) => ({ title: "Кондиционеры", entities: ids(h, "climate", 8) }),
    "hauflo-devices": (h) => ({ entities: [...ids(h, "fan", 3), ...ids(h, "valve", 2), ...ids(h, "vacuum", 1), ...ids(h, "water_heater", 1)] }),
  };
  // --- hauflo-entity: any one entity of any integration as a Hauflo tile (owner 28.10: «как у Mushroom — на все
  // возможные типы»). Icon / picture, name, state; tap does what the domain does (switch, run, lock…) or the action set
  // in the editor; hold — the entity's dialog. hauflo-title: a section heading in the same type.
  Object.assign(EN, { "включён": "on", "едет на базу": "returning to dock", "косит": "mowing", "есть обновление": "update available", "актуально": "up to date", "пауза": "paused", "не дома": "away", "выполнить": "run", "нажать": "press", "позвонить": "call", "открыть": "open", "идёт": "playing", "на паузе": "paused", "включено": "on" });
  // The graph's own title («За 24 ч») before the fragments make it «За 24 h» (seen on the demo HA, 28.10).
  EN_RX.unshift([/^За (\d+) ч$/, "Last $1 h"]);
  Object.assign(EN, { "без протечек": "no leaks", "датчик: нет связи": "sensor: no signal" });
  EN_RX.unshift([/^Протечка: (.+?)( и ещё (\d+))?$/, (m, a, b, n) => `Leak: ${a}${n ? ` and ${n} more` : ""}`]);
  const TONE = { amber: ["var(--amber)", "var(--amberGlow)"], ice: ["var(--ice)", "var(--iceGlow)"], mint: ["var(--mint)", "color-mix(in srgb,var(--mint) 50%,transparent)"], alert: ["var(--alert)", "var(--alertGlow)"] };
  const ONE = {
    light: { icon: (s) => (s.state === "on" ? "hauflo:bulb-on" : "hauflo:bulb"), tone: "amber", word: (s) => (s.state === "on" ? (num(s.attributes.brightness) !== undefined ? `${Math.round((num(s.attributes.brightness) / 255) * 100)} %` : "вкл") : "выкл"), level: (s) => (s.state === "on" ? (num(s.attributes.brightness) ?? 255) / 255 : 0), tap: ["light", "toggle"] },
    cover: { icon: () => "hauflo:blind", tone: "ice", word: (s) => (num(s.attributes.current_position) !== undefined && !["opening", "closing"].includes(s.state) ? (s.attributes.current_position ? `${s.attributes.current_position} %` : "закрыта") : ({ open: "открыта", closed: "закрыта", opening: "открывается…", closing: "закрывается…" })[s.state] || s.state), level: (s) => (num(s.attributes.current_position) ?? (s.state === "open" ? 100 : 0)) / 100, on: (s) => s.state !== "closed", tap: ["cover", "toggle"] },
    // The big number is what is now, what it does is the word, the setpoint small (owner 28.10, Д16).
    climate: { icon: () => "hauflo:thermo", tone: "ice", word: (s) => { const k = climateNow(null, s); const cur = num(s.attributes.current_temperature); const t = num(s.attributes.temperature); return k === "off" ? "Выключено" : `${cur !== undefined ? `${fmt(cur, 1)}° · ` : ""}${climateLine(k, t, activeMode(s)) || `${modeSmall(s.state) || HVAC[s.state] || s.state}${t !== undefined ? ` · → ${fmt(t, t % 1 ? 1 : 0)}°` : ""}`}`; }, on: (s) => { const k = climateNow(null, s); return acting(k) || k === "idle"; }, tap: null },
    media_player: { icon: () => "hauflo:speaker", tone: "amber", word: (s) => (s.state === "playing" ? s.attributes.media_title || "идёт" : s.state === "paused" ? "на паузе" : s.state === "off" ? "выкл" : s.state === "on" ? "включён" : s.state === "idle" || s.state === "standby" ? "ждёт" : s.state === "buffering" ? "загрузка…" : s.state), on: (s) => s.state === "playing", tap: (s) => ["media_player", s.state === "off" ? "turn_on" : "media_play_pause"] },
    weather: { icon: (s) => WEATHER[s.state]?.[1] || "hauflo:sun", tone: "ice", word: (s) => `${num(s.attributes.temperature) !== undefined ? `${fmt(num(s.attributes.temperature), 0)}° · ` : ""}${WEATHER[s.state]?.[0] || s.state}`, on: () => false, tap: null },
    scene: { icon: () => "hauflo:scene", tone: "amber", word: () => "выполнить", on: () => false, tap: ["scene", "turn_on"] },
    script: { icon: () => "hauflo:scene", tone: "amber", word: (s) => (s.state === "on" ? "выполняется…" : "выполнить"), tap: ["script", "turn_on"] },
    automation: { icon: () => "hauflo:clock", tone: "mint", word: (s) => (s.state === "on" ? "вкл" : "выкл"), tap: ["automation", "toggle"] },
    person: { icon: () => "hauflo:user", tone: "mint", word: (s) => (s.state === "home" ? "дома" : s.state === "not_home" ? "не дома" : s.state), on: (s) => s.state === "home", tap: null },
    camera: { icon: () => "hauflo:camera", tone: "alert", word: (s) => (s.state === "recording" ? "запись" : s.state === "streaming" ? "трансляция" : s.state), on: () => false, tap: null },
    alarm_control_panel: { icon: (s) => (s.state.startsWith("armed") ? "hauflo:shield" : s.state === "triggered" ? "hauflo:shield-alert" : "hauflo:shield"), tone: "mint", word: (s) => ({ disarmed: "Снята", armed_away: "Под охраной", armed_home: "Под охраной · дома", armed_night: "Ночной режим", arming: "Ставится…", disarming: "Снимается…", pending: "Задержка входа…", triggered: "ТРЕВОГА" })[s.state] || s.state, on: (s) => s.state.startsWith("armed"), alarm: (s) => s.state === "triggered", tap: null },
  };
  const domainOf = (id) => String(id || "").split(".")[0];
  const kindOf = (s) => {
    const d = domainOf(s.entity_id);
    if (ONE[d]) return ONE[d];
    const v = DEV[d];
    if (v) return { icon: () => (v.icon === "hauflo:info" && (SENSOR_ICON[s.attributes.device_class] || AREA_ICON[d])) || v.icon, tone: "mint", word: (x) => v.word(x, nameOf(x, x.entity_id)), on: v.on, alarm: (x) => v.tone?.(x) === "alarm", warn: (x) => v.tone?.(x) === "warn", tap: v.tap };
    return { icon: () => s.attributes.icon || SENSOR_ICON[s.attributes.device_class] || AREA_ICON[d] || "hauflo:info", tone: "mint", word: (x) => `${x.state}${unitOf(x) ? ` ${unitOf(x)}` : ""}`, on: (x) => x.state === "on", tap: null };
  };
  // What a tap / hold / double tap does when the editor set it (HA's own action format); else the domain's default.
  const runAction = (card, a, entity) => {
    const h = card._hass;
    if (!a || a.action === "none") return;
    if (a.confirmation && !window.confirm(typeof a.confirmation === "object" && a.confirmation.text ? a.confirmation.text : LANG === "ru" ? "Выполнить?" : "Are you sure?")) return;
    haptic(card);
    if (a.action === "more-info") return moreInfo(card, a.entity || entity);
    if (a.action === "toggle") return call(h, "homeassistant", "toggle", { entity_id: entity });
    if (a.action === "navigate" && a.navigation_path) return navigate(a.navigation_path);
    if (a.action === "url" && a.url_path) return window.open(a.url_path, "_blank", "noopener");
    const svc = a.perform_action || a.service;
    if ((a.action === "perform-action" || a.action === "call-service") && svc && svc.includes(".")) {
      const [d, s] = svc.split(".");
      return call(h, d, s, { ...(a.data || a.service_data || {}), ...(a.target || {}) });
    }
    // Anything newer (assist…) — HA's own handler.
    card.dispatchEvent(new CustomEvent("hass-action", { bubbles: true, composed: true, detail: { config: { entity, tap_action: a }, action: "tap" } }));
  };
  const INFO = (s, what, k) => {
    if (what === "none") return "";
    if (what === "name") return null;
    if (what === "last_changed" || what === "last_updated") return ago(s[what]);
    return (k.action ? !s || s.state === "unavailable" : nd(s)) ? "нет связи" : k.word(s);
  };
  // The controls under an entity's tile, as Mushroom has them (owner 28.10: «их карточки я могу применить к любой
  // интеграции, и всё выглядит классно»): one look per domain — a light's brightness / white / colour, a blind's buttons
  // and position, a thermostat's ± and modes, a player's transport and volume, a fan's speed, a lock, a vacuum, an alarm
  // panel, a number's ±, a list. Only what the entity says it supports (its features / modes), whatever the integration.
  // This card is never written by the cloud (the agent's tunnel refuses it): the family picked the entity, it is theirs.
  const B_ = (svc, icon, label, data, on) => `<button class="cb${on ? " on" : ""}" data-svc="${svc}"${data ? ` data-x="${esc(JSON.stringify(data))}"` : ""} aria-label="${esc(label)}" title="${esc(label)}">${icon.startsWith("mdi:") || icon.startsWith("hauflo:") ? `<ha-icon icon="${icon}"></ha-icon>` : `<span>${esc(icon)}</span>`}</button>`;
  const SL_ = (svc, field, min, max, step, v, label, kind = "") => `<div class="sl ${kind}" data-svc="${svc}" data-f="${field}" data-min="${min}" data-max="${max}" data-step="${step}" data-v="${v ?? min}" role="slider" aria-label="${esc(label)}" aria-valuenow="${v ?? ""}"><i style="width:${v === undefined ? 0 : Math.round(((v - min) / (max - min || 1)) * 100)}%"></i><span class="sv">${esc(label)}</span></div>`;
  const PM_ = (svc, field, v, step, min, max, unit, digits = 0) =>
    `<div class="pmr">${B_(svc, "−", "−", { [field]: v === undefined ? undefined : Math.max(min ?? -Infinity, +(v - step).toFixed(4)) })}<span class="pmv thin">${v === undefined ? "—" : `${fmt(v, digits)}<small>${esc(unit)}</small>`}</span>${B_(svc, "+", "+", { [field]: v === undefined ? undefined : Math.min(max ?? Infinity, +(v + step).toFixed(4)) })}</div>`;
  const CTL = {
    light: (s, card) => {
      const a = s.attributes;
      const modes = a.supported_color_modes || [];
      const can = { bri: modes.some((m) => m !== "onoff"), ct: modes.includes("color_temp"), hue: modes.some((m) => ["hs", "rgb", "rgbw", "rgbww", "xy"].includes(m)) };
      const tabs = Object.keys(can).filter((k) => can[k]);
      if (!tabs.length) return "";
      const cur = tabs.includes(card._lt) ? card._lt : tabs[0];
      const on = s.state === "on";
      const bri = on ? Math.max(1, Math.round(((num(a.brightness) ?? 255) / 255) * 100)) : 0;
      const ctMin = num(a.min_color_temp_kelvin) ?? 2000;
      const ctMax = num(a.max_color_temp_kelvin) ?? 6500;
      const ct = num(a.color_temp_kelvin);
      const hue = Array.isArray(a.hs_color) ? Math.round(a.hs_color[0]) : undefined;
      const slider =
        cur === "bri" ? SL_("light.turn_on", "brightness_pct", 1, 100, 1, bri || undefined, on ? `${bri} %` : "выкл", "bri") : cur === "ct" ? SL_("light.turn_on", "color_temp_kelvin", ctMin, ctMax, 50, ct, ct ? `${ct} K` : "белый", "ct") : SL_("light.turn_on", "hs_color", 0, 360, 1, hue, "цвет", "hue");
      const tab = (k, icon, label) => `<button class="cb tab${k === cur ? " on" : ""}" data-tab="${k}" aria-label="${label}" title="${label}"><ha-icon icon="${icon}"></ha-icon></button>`;
      return `<div class="row">${slider}${tabs.length > 1 ? tabs.map((k) => tab(k, { bri: "mdi:brightness-6", ct: "mdi:thermometer", hue: "mdi:palette-outline" }[k], { bri: "Яркость", ct: "Белый", hue: "Цвет" }[k])).join("") : ""}</div>`;
    },
    cover: (s) => {
      const f = s.attributes.supported_features || 0;
      const pos = num(s.attributes.current_position);
      const btns = `${f & 1 ? B_("cover.open_cover", "mdi:arrow-up", "Открыть") : ""}${f & 8 ? B_("cover.stop_cover", "mdi:stop", "Стоп") : ""}${f & 2 ? B_("cover.close_cover", "mdi:arrow-down", "Закрыть") : ""}`;
      const tilt = `${f & 16 ? B_("cover.open_cover_tilt", "mdi:arrow-top-right", "Открыть ламели") : ""}${f & 32 ? B_("cover.close_cover_tilt", "mdi:arrow-bottom-left", "Закрыть ламели") : ""}`;
      return `${f & 4 ? `<div class="row">${SL_("cover.set_cover_position", "position", 0, 100, 1, pos, pos === undefined ? "—" : `${pos} %`, "pos")}</div>` : ""}${btns || tilt ? `<div class="row btns">${btns}${tilt}</div>` : ""}`;
    },
    climate: (s) => {
      const a = s.attributes;
      const step = Math.max(num(a.target_temp_step) || 0.5, 0.1);
      const dg = step < 1 ? 1 : 0;
      const lo = num(a.target_temp_low);
      const hi = num(a.target_temp_high);
      const t = num(a.temperature);
      const set =
        t !== undefined
          ? PM_("climate.set_temperature", "temperature", t, step, num(a.min_temp), num(a.max_temp), "°", dg)
          : lo !== undefined && hi !== undefined
            ? `<div class="pmr">${B_("climate.set_temperature", "−", "−", { target_temp_low: +(lo - step).toFixed(2), target_temp_high: +(hi - step).toFixed(2) })}<span class="pmv thin">${fmt(lo, dg)}–${fmt(hi, dg)}<small>°</small></span>${B_("climate.set_temperature", "+", "+", { target_temp_low: +(lo + step).toFixed(2), target_temp_high: +(hi + step).toFixed(2) })}</div>`
            : "";
      const modes = (a.hvac_modes || []).filter(Boolean);
      const chips = modes.length > 1 ? `<div class="row chips">${modes.slice(0, 6).map((m) => `<button class="chip${m === s.state ? " on" : ""}" data-svc="climate.set_hvac_mode" data-x="${esc(JSON.stringify({ hvac_mode: m }))}">${esc(HVAC[m] || m)}</button>`).join("")}</div>` : "";
      const presets = (a.preset_modes || []).length > 1 ? `<div class="row chips">${a.preset_modes.slice(0, 6).map((m) => `<button class="chip${m === a.preset_mode ? " on" : ""}" data-svc="climate.set_preset_mode" data-x="${esc(JSON.stringify({ preset_mode: m }))}">${esc(PRESET[m] || m)}</button>`).join("")}</div>` : "";
      return `${set ? `<div class="row">${set}</div>` : ""}${chips}${presets}`;
    },
    water_heater: (s) => {
      const a = s.attributes;
      const t = num(a.temperature);
      const ops = a.operation_list || [];
      return `${t !== undefined ? `<div class="row">${PM_("water_heater.set_temperature", "temperature", t, Math.max(num(a.target_temp_step) || 1, 0.5), num(a.min_temp), num(a.max_temp), "°")}</div>` : ""}${ops.length > 1 ? `<div class="row chips">${ops.slice(0, 6).map((m) => `<button class="chip${m === a.operation_mode ? " on" : ""}" data-svc="water_heater.set_operation_mode" data-x="${esc(JSON.stringify({ operation_mode: m }))}">${esc(WH_MODE[m] || m)}</button>`).join("")}</div>` : ""}`;
    },
    humidifier: (s) => {
      const a = s.attributes;
      const h = num(a.humidity);
      return h === undefined ? "" : `<div class="row">${PM_("humidifier.set_humidity", "humidity", h, 1, num(a.min_humidity), num(a.max_humidity), "%")}</div>`;
    },
    fan: (s) => {
      const a = s.attributes;
      const f = a.supported_features || 0;
      const pct = s.state === "on" ? num(a.percentage) : 0;
      const step = num(a.percentage_step) || 1;
      const presets = a.preset_modes || [];
      return `${f & 1 ? `<div class="row">${SL_("fan.set_percentage", "percentage", 0, 100, step, pct, s.state === "on" ? `${Math.round(pct ?? 0)} %` : "выкл", "pos")}${f & 2 ? B_("fan.oscillate", "mdi:arrow-oscillating", "Поворот", { oscillating: !a.oscillating }, a.oscillating) : ""}</div>` : f & 2 ? `<div class="row btns">${B_("fan.oscillate", "mdi:arrow-oscillating", "Поворот", { oscillating: !a.oscillating }, a.oscillating)}</div>` : ""}${!(f & 1) && presets.length ? `<div class="row chips">${presets.slice(0, 6).map((m) => `<button class="chip${m === a.preset_mode ? " on" : ""}" data-svc="fan.set_preset_mode" data-x="${esc(JSON.stringify({ preset_mode: m }))}">${esc(m)}</button>`).join("")}</div>` : ""}`;
    },
    media_player: (s) => {
      const a = s.attributes;
      const f = a.supported_features || 0;
      if (s.state === "off") return f & 128 ? `<div class="row btns">${B_("media_player.turn_on", "mdi:power", "Включить")}</div>` : "";
      const playing = s.state === "playing";
      const btns = `${f & 16 ? B_("media_player.media_previous_track", "mdi:skip-previous", "Назад") : ""}${f & 16384 || f & 1 ? B_(playing ? "media_player.media_pause" : "media_player.media_play", playing ? "mdi:pause" : "mdi:play", playing ? "Пауза" : "Играть") : ""}${f & 32 ? B_("media_player.media_next_track", "mdi:skip-next", "Вперёд") : ""}${f & 256 ? B_("media_player.turn_off", "mdi:power", "Выключить") : ""}`;
      const vol = num(a.volume_level);
      return `<div class="row btns">${btns}</div>${f & 4 ? `<div class="row">${SL_("media_player.volume_set", "volume_level", 0, 100, 1, vol === undefined ? undefined : Math.round(vol * 100), vol === undefined ? "громкость" : `${Math.round(vol * 100)} %`, "pos")}${f & 8 ? B_("media_player.volume_mute", a.is_volume_muted ? "mdi:volume-off" : "mdi:volume-high", "Без звука", { is_volume_muted: !a.is_volume_muted }, a.is_volume_muted) : ""}</div>` : ""}`;
    },
    lock: (s) => (nd(s) ? "" : `<div class="row btns">${s.state === "locked" ? B_("lock.unlock", "mdi:lock-open-variant-outline", "Отпереть") : B_("lock.lock", "mdi:lock-outline", "Запереть")}${(s.attributes.supported_features || 0) & 1 ? B_("lock.open", "mdi:door-open", "Открыть дверь") : ""}</div>`),
    vacuum: (s) => {
      const f = s.attributes.supported_features || 0;
      const cleaning = s.state === "cleaning";
      return `<div class="row btns">${cleaning ? (f & 4 ? B_("vacuum.pause", "mdi:pause", "Пауза") : "") : f & 8192 ? B_("vacuum.start", "mdi:play", "Убрать") : ""}${f & 8 ? B_("vacuum.stop", "mdi:stop", "Стоп") : ""}${f & 16 ? B_("vacuum.return_to_base", "mdi:home-import-outline", "На базу") : ""}${f & 512 ? B_("vacuum.locate", "mdi:map-marker-outline", "Найти") : ""}</div>`;
    },
    alarm_control_panel: (s) => {
      const a = s.attributes;
      // A code: HA's own keypad (the dialog), not a code in a card.
      if (a.code_format || a.code_arm_required) return `<div class="row btns">${B_("more-info", "mdi:dialpad", "Пульт")}</div>`;
      const f = a.supported_features || 0;
      return `<div class="row chips">${f & 1 ? `<button class="chip${s.state === "armed_home" ? " on" : ""}" data-svc="alarm_control_panel.alarm_arm_home">Дома</button>` : ""}${f & 2 ? `<button class="chip${s.state === "armed_away" ? " on" : ""}" data-svc="alarm_control_panel.alarm_arm_away">Уйти</button>` : ""}${f & 4 ? `<button class="chip${s.state === "armed_night" ? " on" : ""}" data-svc="alarm_control_panel.alarm_arm_night">Ночь</button>` : ""}<button class="chip${s.state === "disarmed" ? " on" : ""}" data-svc="alarm_control_panel.alarm_disarm">Снять</button></div>`;
    },
    number: (s) => {
      const a = s.attributes;
      const step = num(a.step) || 1;
      const dg = String(step).includes(".") ? String(step).split(".")[1].length : 0;
      return `<div class="row">${PM_(`${domainOf(s.entity_id)}.set_value`, "value", num(s.state), step, num(a.min), num(a.max), unitOf(s), dg)}</div>`;
    },
    select: (s) => `<div class="row"><select class="dd" data-svc="${domainOf(s.entity_id)}.select_option"${nd(s) ? " disabled" : ""}>${(s.attributes.options || []).map((o) => `<option value="${esc(o)}"${o === s.state ? " selected" : ""}>${esc(o)}</option>`).join("")}</select></div>`,
    valve: (s) => {
      const f = s.attributes.supported_features || 0;
      return `<div class="row btns">${f & 1 ? B_("valve.open_valve", "mdi:valve-open", "Открыть") : ""}${f & 8 ? B_("valve.stop_valve", "mdi:stop", "Стоп") : ""}${f & 2 ? B_("valve.close_valve", "mdi:valve-closed", "Закрыть") : ""}</div>`;
    },
  };
  Object.assign(EN, { "Открыть ламели": "Open slats", "Закрыть ламели": "Close slats", Поворот: "Oscillate", "Без звука": "Mute", Выключить: "Turn off", Отпереть: "Unlock", Запереть: "Lock", "Открыть дверь": "Open the door", Убрать: "Clean", "На базу": "Dock", Найти: "Locate", Пульт: "Keypad", белый: "white", цвет: "colour", громкость: "volume", Белый: "White", Цвет: "Colour", Обычный: "Normal", Активный: "Activity", Ночь: "Night" });
  CTL.input_number = CTL.number;
  CTL.input_select = CTL.select;
  Object.assign(PRESET, { none: "Обычный", activity: "Активный" });
  /** A horizontal slider in Hauflo's look: drag (or tap) along it; sent when let go. */
  const bindSlider = (card, el, entity) => {
    const mn = Number(el.dataset.min);
    const mx = Number(el.dataset.max);
    const stp = Number(el.dataset.step) || 1;
    const fill = el.querySelector("i");
    const lab = el.querySelector(".sv");
    let active = false;
    let val;
    const at = (ev) => {
      const r = el.getBoundingClientRect();
      const raw = mn + Math.min(1, Math.max(0, (ev.clientX - r.left) / r.width)) * (mx - mn);
      return Math.round(raw / stp) * stp;
    };
    const show = (v) => {
      fill.style.width = `${Math.round(((v - mn) / (mx - mn || 1)) * 100)}%`;
      const f = el.dataset.f;
      lab.textContent = f === "color_temp_kelvin" ? `${v} K` : f === "hs_color" ? `${v}°` : `${v} %`;
    };
    // Sideways swipe only (a tap or a page scroll sets nothing); sent when let go.
    hdrag(el, {
      down: (ev) => void ev.stopPropagation(),
      start: (ev) => {
        active = true;
        card._busy = true;
        val = at(ev);
        show(val);
      },
      move: (ev) => {
        const v = at(ev);
        if (v !== val) {
          val = v;
          show(v);
          haptic(card, "selection");
        }
      },
      end: (ev, ok) => {
        ev.stopPropagation();
        if (!active) return;
        active = false;
        if (ok) {
          const [d, svc] = el.dataset.svc.split(".");
          const f = el.dataset.f;
          const data = { entity_id: entity, [f]: f === "volume_level" ? val / 100 : f === "hs_color" ? [val, 100] : val };
          call(card._hass, d, svc, data);
        }
        setTimeout(() => {
          card._busy = false;
          card._sig = "";
          card._update();
        }, 600);
      },
    });
  };
  const CTL_CSS = `.e.hasctl{flex-wrap:wrap;row-gap:12px}.ctl{flex-basis:100%;display:flex;flex-direction:column;gap:8px;position:relative;z-index:1;cursor:default}
.ctl .row{display:flex;align-items:center;gap:8px;min-width:0}.ctl .row.btns{justify-content:flex-start;flex-wrap:wrap}.ctl .row.chips{flex-wrap:wrap;gap:6px}
.cb{width:44px;height:44px;border-radius:15px;display:grid;place-items:center;flex:0 0 auto;box-shadow:var(--eh-edge,inset 0 0 0 1px var(--line));background:var(--eh-glass,transparent);font:300 22px Onest,sans-serif;color:var(--ink)}
.cb ha-icon{--mdc-icon-size:20px;color:var(--ink2)}.cb.on{background:color-mix(in srgb,var(--acc) 22%,transparent);box-shadow:none}.cb.on ha-icon{color:var(--acc)}.cb:disabled{opacity:.35}
.chip{min-height:36px;padding:0 12px;border-radius:12px;font:500 12px Onest,sans-serif;color:var(--ink2);box-shadow:inset 0 0 0 1px var(--line)}.chip.on{color:var(--ink);background:var(--track);box-shadow:none}
.sl{flex:1;min-width:0;height:44px;border-radius:15px;background:var(--track);position:relative;overflow:hidden;touch-action:pan-y;cursor:ew-resize;user-select:none;-webkit-user-select:none}
.sl i{position:absolute;left:0;top:0;bottom:0;background:color-mix(in srgb,var(--acc) 55%,transparent);border-radius:15px 0 0 15px;transition:width .2s}.sl .sv{position:absolute;left:14px;top:50%;transform:translateY(-50%);font:500 13px Onest,sans-serif;color:var(--ink)}
.sl.ct{background:linear-gradient(90deg,#ffb15a,#fff3e0 55%,#cfe3ff)}.sl.ct i,.sl.hue i{background:transparent;border-right:3px solid var(--ink);border-radius:0}
.sl.hue{background:linear-gradient(90deg,#f00,#ff0 17%,#0f0 33%,#0ff 50%,#00f 67%,#f0f 83%,#f00)}.sl.ct .sv,.sl.hue .sv{color:#222}
.pmr{display:flex;align-items:center;gap:8px;flex:1}.pmv{flex:1;text-align:center;font-size:30px;line-height:1}.pmv small{font:300 14px Onest,sans-serif;color:var(--ink2);margin-left:2px}
.dd{flex:1;min-height:44px;border-radius:15px;border:0;padding:0 12px;font:500 14px Onest,sans-serif;color:var(--ink);background:var(--track)}`;
  class Entity extends EHCard {
    watched() {
      return [this._config.entity];
    }
    css() {
      return `.e{padding:14px 16px;display:flex;align-items:center;gap:14px;cursor:pointer;user-select:none;-webkit-user-select:none;min-height:76px;height:100%}
.e.v{flex-direction:column;justify-content:center;text-align:center;gap:10px;padding:18px 12px}
.ic{width:46px;height:46px;border-radius:16px;flex:0 0 auto;display:grid;place-items:center;background:var(--track);color:var(--ink3);background-size:cover;background-position:center;position:relative;z-index:1}
.ic ha-icon{--mdc-icon-size:22px}.on .ic{background-color:color-mix(in srgb,var(--acc) 22%,transparent);color:var(--acc);box-shadow:0 0 22px color-mix(in srgb,var(--accGlow) 70%,transparent)}
.ic.pic{border-radius:50%}.tx{min-width:0;flex:1;position:relative;z-index:1}.v .tx{flex:none;width:100%}
.p1{font:500 15px/1.25 Onest,sans-serif;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.p2{font:400 13px/1.3 Onest,sans-serif;color:var(--ink2);margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.on .p2{color:var(--acc)}.lvl{position:absolute;left:0;bottom:0;height:3px;background:var(--acc);opacity:.8;border-radius:0 2px 2px 0}
.e.alarm{box-shadow:inset 0 0 0 1.5px color-mix(in srgb,var(--alert) 60%,transparent),var(--eh-deep,none)}.e.alarm .ic{background:color-mix(in srgb,var(--alert) 22%,transparent);color:var(--alertT)}.e.alarm .p2{color:var(--alertT);font-weight:600}
.e.warn .ic,.e.warn .p2{color:var(--amberT)}.un{opacity:.55}.e .glow{background:radial-gradient(circle at 30px 50%,color-mix(in srgb,var(--accGlow) 45%,transparent),transparent 55%);opacity:0}.on .glow{opacity:.6}${CTL_CSS}`;
    }
    render() {
      const c = this._config;
      const s = st(this._hass, c.entity);
      if (!s) return `<div class="glass e un"><div class="ic"><ha-icon icon="hauflo:info"></ha-icon></div><div class="tx"><div class="p1">${esc(c.name || c.entity || "—")}</div><div class="p2">нет данных</div></div></div>`;
      const k = kindOf(s);
      const on = !nd(s) && (k.on ? k.on(s) : s.state === "on");
      const alarm = !!k.alarm?.(s);
      const warn = !alarm && !!k.warn?.(s);
      const [acc, glow] = TONE[c.color] || TONE[k.tone] || TONE.mint;
      const name = c.name || nameOf(s, s.entity_id);
      const pick = (what, dflt) => (what === undefined ? dflt : what);
      const p1 = INFO(s, pick(c.primary_info, "name"), k);
      const p2 = INFO(s, pick(c.secondary_info, "state"), k);
      // A player shows what plays (its cover) unless the card says otherwise, as Mushroom's media card.
      const pic = (c.icon_type === "entity-picture" || (c.icon_type === undefined && domainOf(s.entity_id) === "media_player" && s.state !== "off")) && s.attributes.entity_picture;
      const icon = c.icon_type === "none" ? "" : `<div class="ic${pic ? " pic" : ""}"${pic ? ` style="background-image:url('${esc(s.attributes.entity_picture)}')"` : ""}>${pic ? "" : `<ha-icon icon="${esc(c.icon || s.attributes.icon || k.icon(s))}"></ha-icon>`}</div>`;
      const ctl = this.controls(s);
      const level = k.level && c.show_level !== false && on && !ctl ? Math.max(0, Math.min(1, k.level(s))) : 0;
      return `<div class="glass e${ctl ? " hasctl" : ""}${c.layout === "vertical" ? " v" : ""}${on ? " on" : ""}${alarm ? " alarm" : ""}${warn ? " warn" : ""}${nd(s) ? " un" : ""}" style="--acc:${acc};--accGlow:${glow}" role="button" tabindex="0" aria-label="${esc(name)}"><div class="glow"></div>${icon}<div class="tx">${p1 === "" ? "" : `<div class="p1">${esc(p1 ?? name)}</div>`}${p2 === "" ? "" : `<div class="p2">${esc(p2 ?? name)}</div>`}</div>${level ? `<i class="lvl" style="width:${Math.round(level * 100)}%"></i>` : ""}${ctl ? `<div class="ctl">${ctl}</div>` : ""}</div>`;
    }
    /** The domain's controls (CTL) unless the card turns them off; none while the entity is unavailable. */
    controls(s) {
      const f = CTL[domainOf(s?.entity_id)];
      if (!f || this._config.controls === false || !s || s.state === "unavailable") return "";
      return f(s, this).trim();
    }
    bindControls(ctl, id) {
      // The controls are the card's own: a press there is never the tile's tap / hold.
      ["pointerdown", "pointerup", "click", "keydown"].forEach((t) => ctl.addEventListener(t, (ev) => ev.stopPropagation()));
      ctl.querySelectorAll("button[data-tab]").forEach((b) =>
        b.addEventListener("click", () => {
          this._lt = b.dataset.tab;
          this._sig = "";
          this._update();
        }),
      );
      ctl.querySelectorAll("button[data-svc]").forEach((b) =>
        b.addEventListener("click", () => {
          const svc = b.dataset.svc;
          if (svc === "more-info") return moreInfo(this, id);
          const x = b.dataset.x ? JSON.parse(b.dataset.x) : {};
          if (Object.values(x).some((v) => v === undefined || v === null)) return;
          // What opens a door asks first.
          if ((svc === "lock.unlock" || svc === "lock.open") && !window.confirm(LANG === "ru" ? "Отпереть?" : "Unlock?")) return;
          const [d, s] = svc.split(".");
          haptic(this);
          call(this._hass, d, s, { entity_id: id, ...x });
        }),
      );
      ctl.querySelectorAll("select[data-svc]").forEach((sel) =>
        sel.addEventListener("change", () => {
          const [d, s] = sel.dataset.svc.split(".");
          haptic(this);
          call(this._hass, d, s, { entity_id: id, option: sel.value });
        }),
      );
      ctl.querySelectorAll(".sl").forEach((el) => bindSlider(this, el, id));
    }
    bind() {
      const el = this.shadowRoot.querySelector(".e");
      if (!el) return;
      const c = this._config;
      const id = c.entity;
      const ctl = el.querySelector(".ctl");
      if (ctl) this.bindControls(ctl, id);
      const tap = () => {
        if (c.tap_action) return runAction(this, c.tap_action, id);
        const s = st(this._hass, id);
        const k = s && kindOf(s);
        const t = k && (typeof k.tap === "function" ? k.tap(s) : k.tap);
        if (!t) return moreInfo(this, id);
        haptic(this);
        call(this._hass, t[0], t[1], { entity_id: id });
      };
      let timer;
      let held = false;
      let lastTap = 0;
      let single;
      let at = null;
      el.addEventListener("pointerdown", (ev) => {
        held = false;
        at = [ev.clientX, ev.clientY];
        timer = setTimeout(() => {
          held = true;
          runAction(this, c.hold_action || { action: "more-info" }, id);
        }, 500);
      });
      el.addEventListener("pointermove", (ev) => at && Math.hypot(ev.clientX - at[0], ev.clientY - at[1]) > 8 && (clearTimeout(timer), (at = null)));
      el.addEventListener("pointercancel", () => (clearTimeout(timer), (at = null)));
      el.addEventListener("pointerup", () => {
        clearTimeout(timer);
        const still = at !== null;
        at = null;
        if (held || !still || !GST.ok) return;
        // A double tap only waits when one is set — otherwise a tap is instant.
        if (!c.double_tap_action) return tap();
        if (Date.now() - lastTap < 250) {
          clearTimeout(single);
          lastTap = 0;
          return runAction(this, c.double_tap_action, id);
        }
        lastTap = Date.now();
        single = setTimeout(tap, 250);
      });
      el.addEventListener("pointerleave", () => clearTimeout(timer));
      el.addEventListener("contextmenu", (ev) => ev.preventDefault());
      el.addEventListener("keydown", (e) => e.key === "Enter" && tap());
    }
    getCardSize() {
      return this._config?.layout === "vertical" ? 2 : 1;
    }
    getGridOptions() {
      // With its controls the tile takes the section's width and its own height.
      if (this._hass && this.controls(st(this._hass, this._config?.entity))) return { columns: 12, rows: "auto", min_columns: 6 };
      return this._config?.layout === "vertical" ? { columns: 3, rows: 2, min_columns: 2 } : { columns: 6, rows: 1, min_columns: 4 };
    }
  }
  class Title extends EHCard {
    css() {
      return `.tt{padding:18px 4px 2px}.h{font:300 30px/1.1 Onest,sans-serif;letter-spacing:-.03em}.s{font:400 14px Onest,sans-serif;color:var(--ink2);margin-top:6px}`;
    }
    render() {
      const c = this._config;
      return `<div class="tt">${c.title ? `<div class="h">${esc(c.title)}</div>` : ""}${c.subtitle ? `<div class="s">${esc(c.subtitle)}</div>` : ""}</div>`;
    }
    getCardSize() {
      return 1;
    }
    getGridOptions() {
      return { columns: 12, rows: "auto" };
    }
  }
  const MORE = [
    ["hauflo-entity", Entity, "Hauflo · Объект", "Любой объект любой интеграции"],
    ["hauflo-title", Title, "Hauflo · Заголовок", "Заголовок раздела"],
  ];
  STUB["hauflo-entity"] = (h) => ({ entity: ids(h, "light", 1)[0] || ids(h, "switch", 1)[0] || Object.keys(h?.states || {})[0] });
  STUB["hauflo-title"] = (h) => ({ title: langOf(h) === "ru" ? "Гостиная" : "Living room" });
  STUB["hauflo-leaks"] = (h) => ({ sensors: ids(h, "binary_sensor", 4, (s) => s.attributes.device_class === "moisture"), valves: ids(h, "valve", 2) });
  STUB["hauflo-area"] = (h) => ({ area: Object.keys(h?.areas || {})[0] });

  // --- HA's card picker (owner 28.10: «как сделать любой элемент в нашем стиле с любой интеграцией»): every card is in
  // «Добавить карточку» → search «Hauflo», named in the HA user's language, starts filled with this home's entities of the
  // right kind, and has a small visual editor of its main keys (the rest in YAML; keys: docs/cards.md, /help/cards).
  const uiLang = () => userLang(document.querySelector("home-assistant")?.hass);
  const PICK = {
    "hauflo-entity": [["Hauflo · Объект", "Любой объект любой интеграции: свет, розетка, вентилятор, пылесос, замок, человек…"], ["Hauflo · Entity", "Any entity of any integration: light, plug, fan, vacuum, lock, person…"]],
    "hauflo-title": [["Hauflo · Заголовок", "Заголовок раздела в стиле Hauflo"], ["Hauflo · Title", "A section heading in the Hauflo style"]],
    "hauflo-hero": [["Hauflo · Дом", "Температура дома, свет и охрана одной цифрой"], ["Hauflo · Home", "The home at a glance: temperature, lights, alarms"]],
    "hauflo-room": [["Hauflo · Комната", "Комната светится своим состоянием; тап — её страница, удержание — свет"], ["Hauflo · Room", "A room glowing with its state; tap opens its page, hold switches the lights"]],
    "hauflo-scenes": [["Hauflo · Сценарии", "Сценарии в одно касание"], ["Hauflo · Scenes", "Scenes in one tap"]],
    "hauflo-climate": [["Hauflo · Климат", "Термостат: температура крупно, режимы, график"], ["Hauflo · Climate", "A thermostat: big setpoint, modes, chart"]],
    "hauflo-sliders": [["Hauflo · Ползунки", "Свет, шторы, вентиляторы высокими ползунками"], ["Hauflo · Sliders", "Lights, blinds, fans as tall sliders"]],
    "hauflo-alert": [["Hauflo · Авария", "Протечка, дым, газ — на весь экран, с действием"], ["Hauflo · Alarm", "Leak, smoke, gas — full width, with an action"]],
    "hauflo-lights": [["Hauflo · Пульт света", "Много светильников: всё разом, диммер, капсулы"], ["Hauflo · Lights console", "Many lights: all at once, dimmer, capsules"]],
    "hauflo-covers": [["Hauflo · Пульт штор", "Много штор: все разом, положение"], ["Hauflo · Blinds console", "Many blinds: all at once, position"]],
    "hauflo-weather": [["Hauflo · Погода", "Сейчас и на несколько дней — любая weather-интеграция"], ["Hauflo · Weather", "Now and the next days — any weather integration"]],
    "hauflo-air": [["Hauflo · Воздух", "PM2.5 на шкале, PM10, CO₂, влажность — любые датчики"], ["Hauflo · Air", "PM2.5 on a scale, PM10, CO₂, humidity — any sensors"]],
    "hauflo-media": [["Hauflo · Мультимедиа", "Любой media_player: обложка, управление, громкость"], ["Hauflo · Media", "Any media_player: artwork, controls, volume"]],
    "hauflo-graph": [["Hauflo · График", "Датчики за последние часы — энергия, температура, что угодно"], ["Hauflo · Graph", "Sensors over the last hours — energy, temperature, anything"]],
    "hauflo-auto": [["Hauflo · Автоматизации", "Автоматизации и скрипты: вкл/выкл, запуск, когда сработали"], ["Hauflo · Automations", "Automations and scripts: on/off, run, last triggered"]],
    "hauflo-automations": [["Hauflo · Правила дома", "Рассвет, закат, время, будильник и «Нет дома» — правила семьи"], ["Hauflo · Home rules", "Sunrise, sunset, time, alarm clock and «Away» — the family's rules"]],
    "hauflo-status": [["Hauflo · Диспетчеризация", "Инженерные системы: работает, авария, нет связи"], ["Hauflo · Building systems", "Plant status: running, fault, offline"]],
    "hauflo-security": [["Hauflo · Охрана", "Любая охранная панель, замки, двери и окна"], ["Hauflo · Security", "Any alarm panel, locks, doors and windows"]],
    "hauflo-camera": [["Hauflo · Камера", "Любая камера: кадр, тап — трансляция"], ["Hauflo · Camera", "Any camera: snapshot, tap for live view"]],
    "hauflo-acs": [["Hauflo · Кондиционеры", "Все кондиционеры: какие работают, температура, выключить все"], ["Hauflo · Air conditioners", "All ACs: which run, temperature, all off"]],
    "hauflo-area": [["Hauflo · Комната HA", "Всё, что Home Assistant отнёс к комнате (любые интеграции), — в стиле Hauflo"], ["Hauflo · HA area", "Everything Home Assistant puts in an area (any integration), in the Hauflo style"]],
    "hauflo-leaks": [["Hauflo · Защита от протечек", "Датчики протечки и кран любой интеграции: всё в порядке или где протечка; кран — только кнопкой"], ["Hauflo · Leak protection", "Leak sensors and a valve of any integration: all is well, or where the leak is; the valve only by a button"]],
    "hauflo-devices": [["Hauflo · Устройства", "Вентиляторы, клапаны, замки, пылесосы, бойлеры, розетки…"], ["Hauflo · Devices", "Fans, valves, locks, vacuums, boilers, plugs…"]],
  };
  const dc = (...classes) => (s) => classes.includes(s.attributes.device_class);
  // Stubs that suit any integration's entities (HA's demo, owner 28.10), over the ones above.
  Object.assign(STUB, {
    "hauflo-air": (h) => {
      const one = (cls) => ids(h, "sensor", 1, dc(cls))[0];
      const c = { entity: ids(h, "air_quality", 1)[0], pm25: one("pm25"), pm10: one("pm10"), co2: one("carbon_dioxide"), humidity: one("humidity"), temperature: one("temperature") };
      return Object.fromEntries(Object.entries(c).filter(([, v]) => v));
    },
    "hauflo-graph": (h) => ({ hours: 24, entities: ids(h, "sensor", 2, (s) => !!s.attributes.unit_of_measurement && !isNaN(Number(s.state))) }),
    "hauflo-status": (h) => ({ entities: [...ids(h, "binary_sensor", 6, dc("problem", "connectivity", "battery", "moisture", "running", "power")), ...ids(h, "switch", 2)] }),
    "hauflo-devices": (h) => ({ entities: ["fan", "valve", "lock", "vacuum", "humidifier", "water_heater", "siren"].flatMap((d) => ids(h, d, 2)).slice(0, 8) }),
    "hauflo-media": (h) => ({ entity: ids(h, "media_player", 1, (s) => s.state === "playing")[0] || ids(h, "media_player", 1)[0] }),
  });
  const STUB_EN = { Дома: "Home", Гостиная: "Living room", Свет: "Lights", Шторы: "Blinds", Кондиционеры: "Air conditioners" };
  const stubOf = (tag, hass) => {
    const c = JSON.parse(JSON.stringify(STUB[tag](hass)));
    for (const k of ["label", "name", "title"]) if (langOf(hass) !== "ru" && STUB_EN[c[k]]) c[k] = STUB_EN[c[k]];
    return Object.fromEntries(Object.entries(c).filter(([, v]) => v !== undefined && !(Array.isArray(v) && !v.length)));
  };
  // The visual editor: HA's own ha-form with entity pickers of the right domains. Lists of {entity, name…} keep their
  // extra keys; anything else set in YAML stays as it is.
  const E = (name, domain, multiple = false) => ({ name, selector: { entity: { ...(domain ? { domain } : {}), ...(multiple ? { multiple: true } : {}) } } });
  const TXT = (name) => ({ name, selector: { text: {} } });
  const NUM = (name, min, max) => ({ name, selector: { number: { min, max, mode: "box" } } });
  // Options as [value, ru, en] — put into the editor's language when it opens.
  const SEL = (name, options) => ({ name, selector: { select: { mode: "dropdown", options } } });
  const BOOL = (name) => ({ name, selector: { boolean: {} } });
  const ICON = (name = "icon") => ({ name, selector: { icon: {} } });
  const ACT = (name) => ({ name, selector: { ui_action: {} } });
  // Two fields in a row, as HA's own editors (and Mushroom's) lay them out.
  const G = (...schema) => ({ type: "grid", name: "", schema });
  const INFO_OPT = [["name", "Название", "Name"], ["state", "Состояние", "State"], ["last_changed", "Когда изменилось", "Last changed"], ["none", "Ничего", "None"]];
  const COLOR_OPT = [["amber", "Тёплый (свет)", "Warm (light)"], ["ice", "Холодный (шторы, климат)", "Cool (blinds, climate)"], ["mint", "Мятный (работает)", "Mint (running)"], ["alert", "Красный (тревога)", "Red (alarm)"]];
  const FORM = {
    "hauflo-entity": [
      E("entity", null),
      G(TXT("name"), ICON()),
      G(SEL("icon_type", [["icon", "Иконка", "Icon"], ["entity-picture", "Картинка объекта", "Entity picture"], ["none", "Без иконки", "No icon"]]), SEL("color", COLOR_OPT)),
      G(SEL("layout", [["horizontal", "В строку", "Horizontal"], ["vertical", "Столбиком", "Vertical"]]), BOOL("show_level")),
      G(SEL("primary_info", INFO_OPT), SEL("secondary_info", INFO_OPT)),
      BOOL("controls"),
      ACT("tap_action"),
      ACT("hold_action"),
      ACT("double_tap_action"),
    ],
    "hauflo-title": [TXT("title"), TXT("subtitle")],
    "hauflo-hero": [TXT("label"), E("temperature", ["climate", "sensor"], true), E("humidity", "sensor", true), E("lights", ["light", "switch"], true), E("alarms", "binary_sensor", true), E("devices", null, true), E("weather", "weather")],
    "hauflo-room": [G(TXT("name"), ICON()), E("lights", ["light", "switch"], true), E("climate", "climate"), E("covers", "cover", true), E("alarms", "binary_sensor", true), G(TXT("path"), TXT("picture"))],
    "hauflo-scenes": [G(TXT("title"), SEL("kind", [["light", "Свет", "Lights"], ["cover", "Шторы", "Blinds"]])), E("scenes", ["scene", "script"], true)],
    "hauflo-climate": [E("entity", "climate"), G(TXT("label"), BOOL("chart")), G(BOOL("compact"), E("humidity", "sensor")), G(E("heat_setpoint", "number"), E("cool_setpoint", "number"))],
    "hauflo-sliders": [E("entities", ["light", "cover", "fan", "media_player", "switch", "input_boolean"], true)],
    "hauflo-alert": [E("alarms", "binary_sensor", true), BOOL("hide_ok")],
    "hauflo-lights": [TXT("title"), E("entities", ["light", "switch"], true)],
    "hauflo-covers": [TXT("title"), E("entities", "cover", true)],
    "hauflo-weather": [E("entity", "weather"), TXT("name"), G(NUM("days", 1, 7), SEL("forecast", [["daily", "По дням", "Daily"], ["hourly", "По часам", "Hourly"]]))],
    "hauflo-air": [TXT("name"), E("entity", "air_quality"), G(E("pm25", "sensor"), E("pm10", "sensor")), G(E("co2", "sensor"), E("voc", "sensor")), G(E("humidity", "sensor"), E("temperature", "sensor"))],
    "hauflo-media": [E("entity", "media_player"), TXT("name")],
    "hauflo-graph": [G(TXT("title"), NUM("hours", 1, 168)), E("entities", ["sensor", "number", "input_number"], true)],
    "hauflo-auto": [TXT("title"), E("entities", ["automation", "script"], true)],
    "hauflo-status": [TXT("title"), E("entities", ["binary_sensor", "sensor", "switch", "input_boolean"], true)],
    "hauflo-security": [TXT("name"), E("entity", "alarm_control_panel"), E("locks", "lock", true), E("openings", "binary_sensor", true)],
    "hauflo-camera": [E("entity", "camera"), G(TXT("name"), NUM("refresh", 2, 600))],
    "hauflo-acs": [G(TXT("title"), SEL("kind", [["ac", "Кондиционеры", "Air conditioners"], ["floor_heating", "Тёплые полы", "Floor heating"]])), E("entities", "climate", true)],
    "hauflo-devices": [TXT("title"), E("entities", null, true)],
    "hauflo-leaks": [TXT("title"), { name: "area", selector: { area: {} } }, E("sensors", "binary_sensor", true), E("valves", ["valve", "switch"], true)],
    "hauflo-area": [{ name: "area", selector: { area: {} } }, TXT("title")],
  };
  const LABEL = {
    ru: { sensors: "Датчики протечки", valves: "Кран (клапан)", area: "Комната в Home Assistant", label: "Подпись", name: "Название", title: "Заголовок", subtitle: "Подзаголовок", icon: "Иконка", icon_type: "Что слева", color: "Цвет, когда включено", layout: "Расположение", show_level: "Полоска уровня (яркость, положение)", controls: "Управление под плиткой (яркость, шторы, ±, плеер…)", primary_info: "Первая строка", secondary_info: "Вторая строка", tap_action: "Нажатие", hold_action: "Удержание", double_tap_action: "Двойное нажатие", path: "Страница по тапу (путь)", picture: "Фото комнаты (URL)", temperature: "Температура (термостаты, датчики)", humidity: "Влажность", lights: "Свет", alarms: "Аварийные датчики", devices: "Устройства (нет связи)", weather: "Погода", climate: "Термостат", covers: "Шторы", kind: "Вид", scenes: "Сценарии", entity: "Объект", entities: "Объекты", days: "Дней прогноза", forecast: "Прогноз", pm25: "PM2.5", pm10: "PM10", co2: "CO₂", voc: "Летучие в-ва", hours: "Часов", locks: "Замки", openings: "Двери, окна, движение", refresh: "Обновлять кадр, с", chart: "График за сутки", hide_ok: "Прятать, когда всё в норме", compact: "Компактно (строкой)", heat_setpoint: "Уставка нагрева (number)", cool_setpoint: "Уставка охлаждения (number)", scene_layout: "Где сценарии" },
    en: { sensors: "Leak sensors", valves: "Valve", area: "Home Assistant area", label: "Label", name: "Name", title: "Title", subtitle: "Subtitle", icon: "Icon", icon_type: "On the left", color: "Colour when on", layout: "Layout", show_level: "Level bar (brightness, position)", controls: "Controls under the tile (brightness, blinds, ±, player…)", primary_info: "First line", secondary_info: "Second line", tap_action: "Tap", hold_action: "Hold", double_tap_action: "Double tap", path: "Page on tap (path)", picture: "Room photo (URL)", temperature: "Temperature (thermostats, sensors)", humidity: "Humidity", lights: "Lights", alarms: "Alarm sensors", devices: "Devices (offline check)", weather: "Weather", climate: "Thermostat", covers: "Blinds", kind: "Kind", scenes: "Scenes", entity: "Entity", entities: "Entities", days: "Forecast days", forecast: "Forecast", pm25: "PM2.5", pm10: "PM10", co2: "CO₂", voc: "VOC", hours: "Hours", locks: "Locks", openings: "Doors, windows, motion", refresh: "Snapshot refresh, s", chart: "24 h chart", hide_ok: "Hide when all is OK", compact: "Compact (one row)", heat_setpoint: "Heating setpoint (number)", cool_setpoint: "Cooling setpoint (number)", scene_layout: "Where scenes go" },
  };
  const idOf = (x) => (typeof x === "string" ? x : x?.entity);
  const flat = (schema) => schema.flatMap((s) => (s.type === "grid" ? flat(s.schema) : [s]));
  // What a card does without the key — shown in the form, written only when changed.
  const DEFAULTS = {
    "hauflo-entity": { icon_type: "icon", layout: "horizontal", show_level: true, primary_info: "name", secondary_info: "state", controls: true },
    "hauflo-climate": { chart: true, compact: false },
    "hauflo-scenes": { kind: "light" },
    "hauflo-weather": { days: 5, forecast: "daily" },
    "hauflo-graph": { hours: 24 },
    "hauflo-camera": { refresh: 10 },
    "hauflo-acs": { kind: "ac" },
  };
  const localize = (schema, lang) =>
    schema.map((s) =>
      s.type === "grid"
        ? { ...s, schema: localize(s.schema, lang) }
        : s.selector?.select
          ? { ...s, selector: { select: { ...s.selector.select, options: s.selector.select.options.map((o) => (Array.isArray(o) ? { value: o[0], label: lang === "ru" ? o[1] : o[2] } : o)) } } }
          : s,
    );
  class Editor extends HTMLElement {
    setConfig(config) {
      this._config = config;
      this._render();
    }
    set hass(hass) {
      this._hass = hass;
      this._render();
    }
    _render() {
      const schema = FORM[this.tag];
      if (!this._hass || !this._config || !schema) return;
      // ha-form is HA's lazy part: a built-in card's editor brings it in.
      if (!customElements.get("ha-form")) {
        if (!this._waiting) {
          this._waiting = true;
          customElements.get("hui-entities-card")?.getConfigElement?.();
          customElements.whenDefined("ha-form").then(() => this._render());
        }
        return;
      }
      const fields = flat(schema);
      // On a dashboard Hauflo made: what a change here means (owner 28.10: «не понял, почему карточки не редактируются»).
      if (!this._note && /^\/hauflo-/.test(location.pathname)) {
        this._note = document.createElement("div");
        this._note.style.cssText = "margin:0 0 16px;padding:10px 14px;border-radius:12px;background:rgba(127,127,127,.1);font-size:13px;line-height:1.45;color:var(--secondary-text-color)";
        this._note.textContent =
          userLang(this._hass) === "ru"
            ? "Свои карточки можно добавлять — Hauflo сохранит их при следующей выкатке. Названия блоков — «Настройки → Вид» на панели."
            : "You can add your own cards — Hauflo keeps them on the next deploy. Block titles — «Settings → View» on the dashboard.";
        this.prepend(this._note);
        this._noteBase = this._note.textContent;
      }
      // A house name for this block wins over the «Заголовок» here — said, or a changed title looks ignored.
      if (this._note) {
        loadTitles(this._hass);
        const own = this._config.block && TITLES.data?.[this._config.block];
        const ru = userLang(this._hass) === "ru";
        const text = own ? `${this._noteBase} ${ru ? `Для этого блока в доме задано название «${own}» — оно показывается вместо заголовка (сбросить — там же).` : `This block has a house name «${own}» — it is shown instead of the title (reset it there).`}` : this._noteBase;
        if (this._note.textContent !== text) this._note.textContent = text;
      }
      if (!this._form) {
        this._form = document.createElement("ha-form");
        this._form.computeLabel = (s) => LABEL[userLang(this._hass)][s.name] || s.name;
        this._form.addEventListener("value-changed", (ev) => {
          ev.stopPropagation();
          const v = ev.detail.value;
          const next = { ...this._config };
          for (const s of fields) {
            const val = v[s.name];
            const dflt = DEFAULTS[this.tag]?.[s.name];
            if (val === undefined || val === "" || (Array.isArray(val) && !val.length) || (dflt !== undefined && val === dflt && !(s.name in this._config))) delete next[s.name];
            else if (s.selector.entity?.multiple) {
              // The picked ids, each keeping its item from before ({entity, name, icon}); new scenes as {entity}.
              const was = new Map((this._config[s.name] || []).map((x) => [idOf(x), x]));
              next[s.name] = val.map((id) => was.get(id) ?? (s.name === "scenes" ? { entity: id } : id));
            } else next[s.name] = val;
          }
          this._config = next;
          this.dispatchEvent(new CustomEvent("config-changed", { detail: { config: next }, bubbles: true, composed: true }));
        });
        this.appendChild(this._form);
      }
      const data = { ...DEFAULTS[this.tag], ...this._config };
      for (const s of fields) if (s.selector.entity?.multiple && Array.isArray(data[s.name])) data[s.name] = data[s.name].map(idOf).filter(Boolean);
      this._form.hass = this._hass;
      this._form.schema = localize(schema, userLang(this._hass));
      this._form.data = data;
    }
  }
  const picker = (tag, cls, name, description) => {
    if (STUB[tag] && !Object.hasOwn(cls, "getStubConfig")) cls.getStubConfig = (hass) => stubOf(tag, hass);
    if (FORM[tag] && !Object.hasOwn(cls, "getConfigElement"))
      cls.getConfigElement = () => {
        const e = document.createElement("hauflo-card-editor");
        e.tag = tag;
        return e;
      };
    if (window.customCards.some((c) => c.type === tag)) return;
    const [ru, en] = PICK[tag] || [[name, description], [name, description]];
    // Read when the picker opens — by then HA knows the user's language.
    window.customCards.push({
      type: tag,
      get name() {
        return (uiLang() === "ru" ? ru : en)[0];
      },
      get description() {
        return (uiLang() === "ru" ? ru : en)[1];
      },
      preview: true,
      get documentationURL() {
        return uiLang() === "ru" ? "https://app.hauflo.ru/help/cards" : "https://app.hauflo.com/help/cards";
      },
    });
  };
  const define = () => {
    const reg = window.customElements;
    if (!reg.get("home-assistant") && !reg.get("hui-view")) return setTimeout(define, 120);
    window.customCards = window.customCards || [];
    if (!reg.get("hauflo-card-editor")) reg.define("hauflo-card-editor", Editor);
    for (const [tag, cls, name, description] of [...MORE, ...cards, ["hauflo-automations", AUTO.Automations]]) {
      if (reg.get(tag)) continue;
      picker(tag, cls, name, description);
      reg.define(tag, cls);
    }
    // The whole page in a picker preview is too much.
    const auto = window.customCards.find((c) => c.type === "hauflo-automations");
    if (auto) Object.defineProperty(auto, "preview", { value: false });
  };
  define();
})();
