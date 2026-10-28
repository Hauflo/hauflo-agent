// Hauflo «Atelier» cards for Home Assistant (owner 2026-10-24) — served by the Hauflo agent.
// Own cards only (no third-party code, nothing loaded from the internet): hauflo-hero, hauflo-room,
// hauflo-scenes, hauflo-climate, hauflo-sliders, hauflo-alert. Plain web components; HA calls
// setConfig() once and sets `hass` on every state change.
(() => {
  // Served by the agent (/hauflo_static) or, after it was removed, from /local/hauflo (keep.py).
  const BASE = new URL(".", import.meta.url).pathname.replace(/\/$/, "");

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
 --mint:var(--eh-mint,#5CE0B4);--track:var(--eh-track,rgba(127,127,127,.12))}
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
.scn{display:flex;flex-wrap:wrap;gap:8px}.scn button{height:44px;padding:0 15px 0 12px;border-radius:15px;display:flex;align-items:center;gap:8px;flex:0 1 auto;max-width:100%;min-width:0;color:var(--ink);box-shadow:var(--eh-edge,inset 0 0 0 1px var(--hair));background:var(--eh-glass,transparent);font:500 13px Onest,sans-serif;transition:background .3s,box-shadow .3s}
.scn button span{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.scn button ha-icon{color:var(--sacc);flex:0 0 auto;--mdc-icon-size:17px}
.scn button.go{background:color-mix(in srgb,var(--sacc) 26%,transparent)}.scn.light{--sacc:var(--amber)}.scn.cover{--sacc:var(--ice)}.scn.mixed{--sacc:var(--mint)}
.srow{margin-top:14px}.srow>.caps{display:block;margin-bottom:8px}`;

  const REDUCED = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
  const st = (hass, id) => (id && hass ? hass.states[id] : undefined);
  const num = (v) => (v === undefined || v === null || v === "" || isNaN(Number(v)) ? undefined : Number(v));
  // No data (gone, unavailable, unknown) is never shown as normal — no «Всё спокойно» over a leak sensor that lost
  // the bus (SCADA expert 25.10, ISA-101: three states of a value — good, bad, no data).
  const nd = (s) => !s || s.state === "unavailable" || s.state === "unknown";
  // The agent's list of open alarms — the one set every card shows (SCADA expert 25.10, ISA-18.2): what the rules call
  // an alarm (a link rule's «off» is its alarm, not «on»), with priority, since, who accepted it. Fetched again only
  // when an alarm sensor of some card changes, or once a minute; without the agent — the sensor's «on».
  const AGENT = { at: 0, sig: "", data: null, wait: null, ids: new Set(), cards: new Set() };
  // «Нет дома — имитация» (presence.py): the header says it, so nobody wonders why the lights move. Asked once a minute
  // for all the headers on the page; the «Автоматизации» page puts its answer here at once.
  const PRES = {
    at: 0, data: null, wait: null, cards: new Set(),
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
      if (h && !this.wait && Date.now() - this.at > 60000) {
        this.at = Date.now();
        this.wait = h.callApi("GET", "hauflo/presence").then((d) => this.set(d), () => (this.data = null)).finally(() => (this.wait = null));
      }
      return this.data;
    },
  };
  const refreshAlarms = (hass) => {
    const sig = [...AGENT.ids].map((id) => hass.states[id]?.state).join();
    if (AGENT.wait || (sig === AGENT.sig && Date.now() - AGENT.at < 60000)) return;
    AGENT.sig = sig;
    AGENT.wait = hass
      .callApi("GET", "hauflo/alarms")
      .then((d) => (AGENT.data = d))
      .catch(() => (AGENT.data = null))
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
      .sort((a, b) => Number(!!a.returned) - Number(!!b.returned) || Number(!!b.critical) - Number(!!a.critical) || Number(!!a.ackedBy) - Number(!!b.ackedBy) || String(a.since).localeCompare(String(b.since)));
  };
  const pluralAlarms = (n) => (n % 10 === 1 && n % 100 !== 11 ? "авария" : [2, 3, 4].includes(n % 10) && ![12, 13, 14].includes(n % 100) ? "аварии" : "аварий");
  const lampsWord = (n) => (n % 10 === 1 && n % 100 !== 11 ? "светильник" : [2, 3, 4].includes(n % 10) && ![12, 13, 14].includes(n % 100) ? "светильника" : "светильников");
  const sensorsWord = (n) => (n % 10 === 1 && n % 100 !== 11 ? "датчик" : [2, 3, 4].includes(n % 10) && ![12, 13, 14].includes(n % 100) ? "датчика" : "датчиков");
  const fmt = (v, digits = 0) => (v === undefined ? "—" : LANG === "ru" ? Number(v).toFixed(digits).replace(".", ",") : Number(v).toFixed(digits));
  // What to do now, by the kind of alarm (persona round 5: «опасно ли это и что делать прямо сейчас»); no «call service»
  // when nobody's number is set.
  const TODO = {
    moisture: "Проверьте место протечки.",
    smoke: "Если видите дым или огонь — выйдите из дома и звоните 112.",
    gas: "Не включайте свет и огонь, откройте окна, выйдите и звоните 112.",
    carbon_monoxide: "Откройте окна, выйдите на воздух и звоните 112.",
  };
  // «The phones got a notice» only when a phone got it (integrator run 22: said over a leak no rule watched; run 23:
  // a rule to a person without a phone).
  const alarmTodo = (dc, phone, pushed) =>
    TODO[dc]
      ? `${TODO[dc]}${pushed && dc === "moisture" ? " Уведомление отправлено на телефоны (получено ли — неизвестно, пока кто-то не нажмёт «Принято»); «Принято» снимает повторы." : ""}`
      : `Это неисправность оборудования, людям она не опасна. ${phone ? "Позвоните сервису — кнопка ниже." : "Сообщите тому, кто обслуживает дом."}${pushed ? " Уведомление отправлено на телефоны (получено ли — неизвестно, пока кто-то не нажмёт «Принято»)." : ""}`;
  // --- English (owner 25.10: Dubai is the main market) ------------------------------------------------
  // The cards are written in Russian; with HA's language not Russian, a rendered card's own words are put into
  // English: whole phrases by the table, phrases with numbers by patterns, then known fragments. Names the
  // integrator gave (rooms, devices) stay as they are unless a whole name is one of the card's own words.
  let LANG = "ru";
  const langOf = (hass) => {
    const l = String(hass?.locale?.language || hass?.language || document.documentElement.lang || "ru").toLowerCase();
    return l.startsWith("ru") ? "ru" : "en";
  };
  const EN = {
    "Дома": "Home", "Дом": "Home", "Главная": "Home", "Нет дома · имитация": "Away · simulation", "Всё здание": "Whole building", "Всё спокойно": "All quiet", "Авария": "Alarm", "Аварий нет": "No alarms",
    "В комнатах": "Rooms", "Влажность": "Humidity", "Свет": "Lights", "Датчики": "Sensors", "в норме": "OK", "на улице": "outside",
    "Свет выключен": "Lights off", "Свет включён": "Lights on", "Всё выключено": "All off", "Свет: нет данных": "Lights: no data", "Всё в норме": "All OK", "всё в норме": "all OK",
    "Сценарии света": "Light scenes", "Сценарии штор": "Blind scenes", "Сценариев нет": "No scenes", "Климат": "Climate", "Тёплый пол": "Floor heating",
    "цель": "target", "нет данных из комнаты": "no room reading", "нет связи с термостатом": "thermostat offline", "проведите, чтобы изменить": "swipe to change",
    "Датчики состояния": "Status sensors", "Значения": "Values", "Режимы": "Modes", "Кнопки": "Buttons", "Вентиляция": "Ventilation", "Выключатели": "Switches",
    "Освещение": "Lighting", "Сцены": "Scenes", "Уведомления": "Notifications", "Без комнаты": "No room", "Нет связи": "Offline", "Теплее": "Warmer", "Холоднее": "Cooler", "нет связи": "offline", "Нет термостата": "No thermostat", "Держит": "Holding", "Нагревает": "Heating", "Охлаждает": "Cooling",
    "Осушает": "Drying", "Вентилирует": "Ventilating", "Выключен": "Off", "выключен": "off", "выключена": "off", "Нагрев": "Heat", "Холод": "Cool", "Охлаждение": "Cooling",
    "Авто": "Auto", "Диапазон": "Range", "Осушение": "Dry", "Вент.": "Fan", "Комфорт": "Comfort", "Эко": "Eco", "Эконом": "Economy", "Ночь": "Night", "Сон": "Sleep",
    "Буст": "Boost", "Кондиционеры": "Air conditioners", "Отопление": "Heating", "Изменить уставку": "Change the setpoint", "Все выключены": "All off", "Тихо": "Quiet", "Средне": "Medium", "Сильно": "High", "Макс": "Max", "Мягко": "Diffuse", "Фокус": "Focus", "Турбо": "Turbo", "Обдув": "Swing", "Отпуск": "Away", "Нет дома": "Away", "Защита": "Protection", "Вкл": "On", "Выкл": "Off", "вкл": "on", "выкл": "off", "Вкл. все": "All on",
    "Выкл. все": "All off", "Все": "All", "Все закрыты": "All closed", "Нет данных": "No data", "нет данных": "no data", "Яркость": "Brightness", "Группы": "Groups",
    "Прочее": "Other", "Откр.": "Open", "Закр.": "Closed", "закр.": "closed", "Открыть": "Open", "Закрыть": "Close", "Стоп": "Stop", "открыта": "open", "закрыта": "closed",
    "открыт": "open", "закрыт": "closed", "открыто": "open", "закрыто": "closed", "открывается…": "opening…", "закрывается…": "closing…", "Откр…": "Opening", "Закр…": "Closing", "Шторы": "Blinds",
    "Свернуть": "Collapse", "Назад": "Back", "← Назад": "← Back", "Вперёд": "Next", "Выполнено": "Done", "выполнить": "run", "Включить": "Turn on",
    "Проверьте место протечки. Уведомление отправлено на телефоны (получено ли — неизвестно, пока кто-то не нажмёт «Принято»); «Принято» снимает повторы.": "Check where it leaks. Sent to the phones (whether it arrived isn't known until someone taps «Accept»); «Accept» stops the repeats.",
    "Проверьте место протечки.": "Check where it leaks.",
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
    "Снята с охраны": "Disarmed", "Ставится…": "Arming…", "Снимается…": "Disarming…", "Задержка входа…": "Entry delay…", "ТРЕВОГА": "ALARM", "Позвонить сервису": "Call service", "Тревога · охрана": "Alarm · security", "Звучит сирена": "Siren sounding", "Уйти": "Away",
    "Ночной режим": "Night", "двери заперты": "doors locked", "заперт": "locked", "не заперт": "unlocked", "есть движение": "motion", "нет движения": "no motion",
    "сработал": "triggered", "звучит": "sounding", "ждёт": "idle", "на базе": "docked", "едет на базе": "returning to dock", "убирает": "cleaning", "заклинило": "jammed",
    "Ожидание": "Idle", "Вода": "Water", "Газ": "Gas", "Дым": "Smoke", "Угарный газ": "Carbon monoxide", "Перегрев": "Overheat", "Вскрытие": "Tamper", "Необычно": "Unusual",
    "загрузка…": "loading…", "истории пока нет": "no history yet", "только что": "just now", "Кухня": "Kitchen", "Гостиная зона": "Living area", "Обеденная": "Dining",
    "Остров": "Island", "ТВ": "TV", "Здание": "Building", "Устройства": "Devices", "Зима": "Winter", "Лето": "Summer",
    "Принято": "Accept", "отправлено": "sent", "Не получилось — ещё раз": "Didn't work — try again",
    "не отправлено — нет связи с домом": "not sent — no link to the house", "Показания": "Readings", "Комната": "Room", "Здесь пока ничего нет — добавьте устройства в проект или свои карточки в Home Assistant": "Nothing here yet — add devices to the project or your own cards in Home Assistant",
    "Тёплые полы": "Floor heating", "цель · обогрев": "target · heating", "цель · охлаждение": "target · cooling", "Сценарии": "Scenes", "Светильник": "Light", "Выключатель": "Switch", "Штора": "Blind", "Датчик": "Sensor", "Сцена": "Scene", "Кран": "Valve", "Замок": "Lock", "Устройство": "Device", "уставка ещё не пришла с шины": "setpoint not received from the bus yet", "уставка ещё не пришла": "no setpoint yet", "нет уставки": "no setpoint", "греет": "heating", "держит": "holding", "Сейчас не греют": "Not heating now", "сейчас": "now", "Пол": "Floor", "В комнате": "Room",
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
    [/^(\d+) из (\d+)/, "$1 of $2"],
    [/^(\d+) авари[яийе]+$/, (m, a) => `${a} alarm${a === "1" ? "" : "s"}`],
    [/^Показать все \((\d+)\)$/, "Show all ($1)"],
    [/^Нет данных · (\d+) светильник(?:а|ов)?$/, (m, a) => `No data · ${a} light${a === "1" ? "" : "s"}`],
    [/^Нет данных · свет$/, "No data · lights"],
    [/^Нет связи · (\d+) датчик(?:а|ов)?$/, (m, a) => `Offline · ${a} sensor${a === "1" ? "" : "s"}`],
    [/^(\d+) датчик(?:а|ов)? без связи$/, (m, a) => `${a} sensor${a === "1" ? "" : "s"} offline`],
    [/^выключен · (\d+) без данных$/, "off · $1 no data"],
    [/^Климат · нет данных$/, "Climate · no data"],
    [/^Нет связи · (\d+) устройств[оа]?$/, (m, a) => `Offline · ${a} device${a === "1" ? "" : "s"}`],
    [/^Ждём уставку · (\d+) термостат(?:а|ов)?$/, (m, a) => `Waiting for the setpoint · ${a} thermostat${a === "1" ? "" : "s"}`],
    [/^По одной \((\d+)\)$/, "One by one ($1)"],
    [/^Свет в (\d+) комнат[аеху]*$/, (m, a) => `Lights in ${a} room${a === "1" ? "" : "s"}`],
  ];
  // Fragments inside longer texts (between Cyrillic boundaries), longest first.
  const B = "(^|[^А-Яа-яЁё])"; // a Cyrillic word boundary (\\b doesn't know Cyrillic)
  const EN_FRAG = [
    [/^Позвонить · /, "Call · "], [new RegExp(`${B}ещё аварий:`, "g"), "$1more alarms:"], [new RegExp(`${B}и ещё (\\d+)`, "g"), "$1and $2 more"], [new RegExp(`${B}за сутки`, "g"), "$124 h"],
    [new RegExp(`${B}В комнате `, "g"), "$1Now "], [new RegExp(`${B}пол (?=\\d)`, "g"), "$1floor "], [new RegExp(`${B}Сейчас `, "g"), "$1Now "], [new RegExp(`${B}влажность `, "g"), "$1humidity "],
    [new RegExp(`${B}уставка ещё не пришла`, "g"), "$1no setpoint yet"],
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
    if (EN[k]) return text.replace(k, EN[k]);
    // «· солнечно» (the header's weather, lower case): the word's own translation, lower case too.
    const w = /^(·\s*)?([а-яё][а-яё ,]*)$/.exec(k);
    const cap = w && EN[w[2][0].toUpperCase() + w[2].slice(1)];
    if (cap) return text.replace(w[2], cap.toLowerCase());
    for (const [rx, rep] of EN_RX) if (rx.test(k)) return text.replace(k, k.replace(rx, rep));
    // «Охлаждает · 22.0°», «Нагрев · 25°»: a known word before « · », the rest on its own.
    const dot = /^([^·]+?) · (.+)$/.exec(k);
    if (dot && EN[dot[1]]) return text.replace(k, `${EN[dot[1]]} · ${tr(dot[2])}`);
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
    return base && sameBase(id, base) < 2 ? base : n;
  };
  const nameOf = (s, id) => (s && s.attributes.friendly_name ? clientName(s.attributes.friendly_name, id) : id);
  // Device codes («DKP1-1», «B1-2») keep a non-breaking hyphen: «Протечка (DKP1-» / «1)» on two lines read as
  // nonsense (integrator run 15). Only upper-case codes — icon names and entity ids are lower case.
  const esc = (t) => String(t ?? "").replace(/\b([A-Z]{1,6}\d*)-(\d+)\b/g, "$1\u2011$2").replace(/[&<>"'()]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;", "(": "&#40;", ")": "&#41;" })[c]);
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
  const navigate = (path) => {
    history.pushState(null, "", path);
    window.dispatchEvent(new CustomEvent("location-changed", { detail: { replace: false } }));
  };
  const haptic = (el, type = "light") => el.dispatchEvent(new CustomEvent("haptic", { detail: type, bubbles: true, composed: true }));
  const moreInfo = (el, entityId) => el.dispatchEvent(new CustomEvent("hass-more-info", { detail: { entityId }, bubbles: true, composed: true }));

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
  /** Scene buttons of one kind (light amber, blinds ice, both mint) — the room's top card and the blocks share them. */
  const sceneChips = (hass, list, kind) =>
    `<div class="scn ${kind}">${(list || [])
      .filter((x) => st(hass, x.entity))
      .map((x) => {
        const label = x.name || nameOf(st(hass, x.entity), x.entity);
        return `<button data-scene="${esc(x.entity)}" title="${esc(label)}"><ha-icon icon="${esc(x.icon || (kind === "cover" ? "hauflo:blind" : "hauflo:scene"))}"></ha-icon><span>${esc(label)}</span></button>`;
      })
      .join("")}</div>`;
  const bindScenes = (card) =>
    card.shadowRoot.querySelectorAll("button[data-scene]").forEach((b) =>
      b.addEventListener("click", (ev) => {
        ev.stopPropagation();
        const id = b.dataset.scene;
        haptic(card);
        card._busy = true;
        b.classList.add("go");
        // A tick where its icon was: the tap took (persona round 5: «нажал — ничего не подсветилось»).
        const ic = b.querySelector("ha-icon");
        const was = ic?.getAttribute("icon");
        ic?.setAttribute("icon", "mdi:check");
        setTimeout(() => {
          b.classList.remove("go");
          if (was) ic?.setAttribute("icon", was);
          card._busy = false;
        }, 1600);
        call(card._hass, id.split(".")[0] === "script" ? "script" : "scene", "turn_on", { entity_id: id });
      }),
    );
  const OPENING = ["window", "door", "garage_door", "opening"];
  const FAN = { auto: "Авто", on_low: "Тихо", on_high: "Сильно", auto_low: "Авто ↓", auto_high: "Авто ↑", low: "Тихо", medium: "Средне", middle: "Средне", mid: "Средне", high: "Сильно", top: "Макс", quiet: "Тихо", diffuse: "Мягко", focus: "Фокус", turbo: "Турбо" };
  /** climate.turn_off is there: the TURN_OFF feature (128) or «off» among its modes. */
  const canTurnOff = (s) => ((Number(s.attributes.supported_features) || 0) & 128) !== 0 || (s.attributes.hvac_modes || []).includes("off");
  const HVAC = { heat: "Нагрев", cool: "Холод", auto: "Авто", heat_cool: "Диапазон", off: "Выкл", fan_only: "Вент.", dry: "Осушение" };
  // Units in Russian; a value outside what's physically plausible is «—», not «33424 гПа» (designer review 25.10).
  const UNIT = { "km/h": "км/ч", "m/s": "м/с", hPa: "гПа", mbar: "мбар", mmHg: "мм рт. ст.", "µg/m³": "мкг/м³", ppm: "ppm", ppb: "ppb" };
  const ru = (u) => UNIT[u] || u || "";
  const sane = (v, lo, hi) => (v === undefined || v < lo || v > hi ? undefined : v);
  const PRESET = { comfort: "Комфорт", standby: "Ожидание", economy: "Эконом", building_protection: "Защита", eco: "Эко", away: "Нет дома", home: "Дома", sleep: "Сон", boost: "Буст" };
  const ALARM_WORD = { moisture: "Вода", smoke: "Дым", gas: "Газ", carbon_monoxide: "Угарный газ", heat: "Перегрев", cold: "Холод", problem: "Авария", safety: "Опасность", tamper: "Вскрытие" };

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
    return shell(`<div class="tlg"><span><i class="ln"></i>${esc(label)}</span>${sp.length ? `<span><i class="ds"></i>цель</span>` : ""}<span class="per">${period}</span></div>
<div class="tpl" style="height:${H}px"><svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-hidden="true">${ticksY.map((v) => `<line class="gr" x1="0" x2="${W}" y1="${y(v).toFixed(1)}" y2="${y(v).toFixed(1)}" vector-effect="non-scaling-stroke"/>`).join("")}<path class="ar" d="${area}"/>${sp.length ? `<path class="sp" d="${spLine}" vector-effect="non-scaling-stroke"/>` : ""}<path class="cu" d="${line}" vector-effect="non-scaling-stroke"/></svg>${ticksY.map((v) => `<span class="ty" style="top:${((y(v) / H) * 100).toFixed(1)}%">${fmt(v, digits)}°</span>`).join("")}</div>
<div class="tx">${ticksX.map((t) => `<span style="left:${(((t - t0) / span) * 100).toFixed(1)}%">${hm(t)}</span>`).join("")}<span class="now">сейчас</span></div>`);
  };
  const TCHART_CSS = `.tch{position:relative;margin-top:14px;--tc:var(--ink2)}.tch.heat{--tc:var(--amber)}.tch.cool{--tc:var(--ice)}
.tlg{display:flex;align-items:center;gap:14px;height:16px;margin-bottom:6px;font:400 11px Onest,sans-serif;color:var(--ink3);white-space:nowrap}.tlg .per{margin-left:auto;margin-right:34px}
.tlg i{display:inline-block;width:14px;border-top:2px solid var(--tc);vertical-align:middle;margin:-2px 6px 0 0}.tlg .ds{border-top:2px dashed var(--ink2);opacity:.8}.tlg span:first-child{color:var(--ink2)}
.tpl{position:relative;margin-right:34px}.tpl svg{display:block;width:100%;height:100%;overflow:visible}
.tpl .gr{stroke:var(--hair);stroke-width:1}.tpl .cu{fill:none;stroke:var(--tc);stroke-width:2;stroke-linejoin:round;stroke-linecap:round}.tpl .ar{fill:var(--tc);opacity:.10;stroke:none}
.tpl .sp{fill:none;stroke:var(--ink2);stroke-width:1.25;stroke-dasharray:5 4;opacity:.8}
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
      this._sig = "";
      if (this._hass) this._update();
    }
    set hass(hass) {
      this._hass = hass;
      AGENT.hass = hass;
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
    /** The states this card shows — re-rendered only when they change (not while dragging). */
    watched() {
      return [];
    }
    _update() {
      if (!this._config || !this._hass || this._busy) return;
      // HA replaces a state object when it changes: comparing references is enough — no JSON of every
      // attribute on every hass update (HA core review 25.10). `_sig = ""` elsewhere forces a render.
      const refs = this.watched().map((id) => this._hass.states[id]);
      const dark = !!this._hass.themes?.darkMode;
      if (this._sig && dark === this._dark && refs.length === this._refs?.length && refs.every((r, i) => r === this._refs[i])) return;
      const forced = !this._sig;
      this._sig = "r";
      this._refs = refs;
      this._dark = dark;
      this.lang = LANG; // hyphenation in the card's language («Полотенце-сушитель»)
      if (this._config.panel) this.style.cssText = "display:block;max-width:980px;margin:24px auto;padding:0 16px";
      const html = `<style>${CSS}${this.css ? this.css() : ""}</style>${this.render()}`;
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
      if (!c.scenes?.length) return "";
      const where = viewPrefs(this).scenes || c.scene_layout || "blocks";
      if (where !== "blocks") return "";
      return `<div class="srow"><span class="caps">${blockTitle(this, this.kind === "cover" ? "Сценарии штор" : "Сценарии света", this.kind === "cover" ? "cover_scenes" : "light_scenes")}</span>${sceneChips(this._hass, c.scenes, this.kind === "cover" ? "cover" : "light")}</div>`;
    }
  }

  // --- hauflo-hero: the house in one number ------------------------------------------------------
  class Hero extends EHCard {
    watched() {
      const c = this._config;
      const loud = Object.keys(this._hass?.states || {}).filter((id) => id.startsWith("siren.") || id.startsWith("alarm_control_panel."));
      return [...(c.temperature || []), ...(c.humidity || []), ...(c.lights || []), ...Object.values(c.rooms_lights || {}).flat(), ...(c.alarms || []), ...(c.devices || []), c.weather, ...loud].filter(Boolean);
    }
    css() {
      return `.top{display:flex;justify-content:space-between;align-items:center;gap:12px}
.out{display:inline-flex;align-items:center;gap:8px;padding:6px 12px 6px 8px;border-radius:16px;background:var(--track);font:400 13px Onest,sans-serif;color:var(--ink2);white-space:nowrap;cursor:pointer}
.out ha-icon{--mdc-icon-size:20px;color:var(--amber)}.out b{font:500 15px Onest,sans-serif;color:var(--ink)}
.lab{display:flex;align-items:center;gap:10px;flex-wrap:wrap;min-width:0}.away{display:inline-flex;align-items:center;gap:6px;padding:4px 10px 4px 8px;border-radius:12px;background:color-mix(in srgb,var(--amber) 20%,transparent);color:var(--ink);font:500 12px Onest,sans-serif;white-space:nowrap;cursor:pointer}.away ha-icon{--mdc-icon-size:15px;color:var(--amber)}
.hl{font:300 34px/1.1 Onest,sans-serif;letter-spacing:-.03em;margin-top:10px;display:flex;align-items:center;gap:12px}.hl.bad{color:var(--alert)}.hl .dot{width:10px;height:10px;border-radius:5px;flex:0 0 auto}
.h{padding:6px 8px 4px}.t{font-size:112px;line-height:.9;margin-top:4px}.t sup{font:300 34px Onest,sans-serif;vertical-align:top;position:relative;top:10px;margin-left:4px;letter-spacing:0;color:var(--ink2)}
.none{font:300 30px/1.2 Onest,sans-serif;letter-spacing:-.02em;color:var(--ink2);margin:14px 0 6px}
.f{display:flex;gap:20px;margin-top:16px;flex-wrap:wrap}.f div{display:flex;flex-direction:column;gap:5px}.f b{font:400 17px Onest,sans-serif;letter-spacing:-.01em}
.dot{display:inline-block;width:6px;height:6px;border-radius:3px;margin-right:7px;vertical-align:middle}`;
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
      // A siren sounding or the security alarm triggered is no «Всё спокойно» (persona round 3: one source of truth).
      const loud = Object.values(h.states).filter((s) => (s.entity_id.startsWith("siren.") && s.state === "on") || (s.entity_id.startsWith("alarm_control_panel.") && s.state === "triggered"));
      const ok = !alarms.length && !loud.length;
      // A thermostat / AC with no setpoint heard, a blind HA lost: no «Всё спокойно» over them (persona round 5).
      // On the bus but no setpoint heard yet (right after a restart) is not «no link» (review round 2): said apart.
      const waiting = (c.devices || []).filter((id) => {
        const s = st(h, id);
        return id.startsWith("climate.") && !!s && !nd(s) && num(s.attributes.temperature) === undefined && num(s.attributes.target_temp_low) === undefined;
      });
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
      const lightsDeaf = lightIds.filter((id) => nd(st(h, id))).length;
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
      const headline = ok && offline.length
        ? `<div class="hl"><span class="dot" style="background:var(--amber);box-shadow:0 0 12px var(--amberGlow)"></span>Нет связи · ${offline.length} ${devWord(offline.length)}</div>`
        : ok && waiting.length
        ? `<div class="hl"><span class="dot" style="background:var(--ink2)"></span>Ждём уставку · ${waiting.length} ${thermoWord(waiting.length)}</div>`
        : ok && lightsDeaf
        ? // Lights without data: no «Всё спокойно» over what isn't known (narrow rounds 1–2, D9).
          `<div class="hl"><span class="dot" style="background:var(--ink2)"></span>Нет данных · ${lightsUnknown ? "свет" : `${lightsDeaf} ${lampsWord(lightsDeaf)}`}</div>`
        : ok
        ? `<div class="hl">Всё спокойно</div>`
        : !alarms.length
        ? `<div class="hl bad"><span class="dot" style="background:var(--alert);box-shadow:0 0 12px var(--alert)"></span>${loud.some((s) => s.entity_id.startsWith("alarm_control_panel.")) ? "Тревога · охрана" : "Звучит сирена"}</div>`
        : `<div class="hl bad"${critical ? "" : ' style="color:var(--amber)"'}><span class="dot" style="background:${critical ? "var(--alert)" : "var(--amber)"};box-shadow:0 0 12px ${critical ? "var(--alert)" : "var(--amber)"}"></span>${esc(alarms.length === 1 ? `Авария${c.rooms?.[alarms[0]] ? ` · ${c.rooms[alarms[0]]}` : ""}` : `${alarms.length} ${pluralAlarms(alarms.length)}`)}</div>`;
      const away = PRES.get(this)?.enabled;
      return `<div class="h"><div class="top"><span class="lab"><span class="caps">${esc(c.label || "Дома")}</span>${away ? `<span class="away" role="link" tabindex="0" data-away><ha-icon icon="mdi:home-export-outline"></ha-icon>Нет дома · имитация</span>` : ""}</span>${outside}</div>
${headline}
<div class="f">${tmin !== undefined ? `<div><span class="caps">В комнатах</span><b>${tmin === tmax ? `${fmt(tmin)}°` : `${fmt(Math.floor(tmin))}–${fmt(Math.ceil(tmax))}°`}</b></div>` : ""}
${hum !== undefined ? `<div><span class="caps">Влажность</span><b>${fmt(hum)} %</b></div>` : ""}
<div><span class="caps">Свет</span><b>${lightsUnknown ? "нет данных" : litRooms ? `${lamps} ${lamps % 10 === 1 && lamps % 100 !== 11 ? "лампа" : [2, 3, 4].includes(lamps % 10) && ![12, 13, 14].includes(lamps % 100) ? "лампы" : "ламп"} в ${litRooms} ${litRooms === 1 ? "комнате" : "комнатах"}${outerOn.length ? ` · ${esc(nameOf(st(h, outerOn[0]), outerOn[0]))}${outerOn.length > 1 ? ` +${outerOn.length - 1}` : ""}` : ""}` : outerOn.length ? `включён: ${esc(nameOf(st(h, outerOn[0]), outerOn[0]))}${outerOn.length > 1 ? ` +${outerOn.length - 1}` : ""}` : lightsDeaf ? `выключен · ${lightsDeaf} без данных` : "выключен"}</b></div>
${(c.alarms || []).length && ok ? `<div><span class="caps">Датчики</span><b>${deaf.length ? `<span class="dot" style="background:var(--ink2)"></span>${deaf.length} ${sensorsWord(deaf.length)} без связи` : "в норме"}</b></div>` : ""}</div></div>`;
    }
    getGridOptions() {
      return { columns: 12, rows: "auto" };
    }
  }

  Hero.prototype.bind = function () {
    this.shadowRoot.querySelector("[data-w]")?.addEventListener("click", () => moreInfo(this, this._config.weather));
    // The chip opens the dashboard's «Автоматизации» tab, where the simulation is switched.
    const away = this.shadowRoot.querySelector("[data-away]");
    const open = () => AUTO.open(this._hass, ".pres");
    away?.addEventListener("click", open);
    away?.addEventListener("keydown", (e) => e.key === "Enter" && open());
  };

  // --- hauflo-room: a room that glows with its state; tap = its page, hold = its light -----------
  class Room extends EHCard {
    watched() {
      const c = this._config;
      return [...(c.lights || []), c.climate, ...(c.alarms || []), ...(c.covers || []), ...(c.temps || []).map((x) => x.entity)].filter(Boolean);
    }
    css() {
      return `.r{height:172px;padding:16px;display:flex;flex-direction:column;justify-content:space-between;cursor:pointer;user-select:none;-webkit-user-select:none}
.r>*:not(.glow):not(.ph){position:relative}.top{display:flex;justify-content:space-between;align-items:flex-start;color:var(--ink2)}
.mid{display:flex;align-items:center;justify-content:space-between}
.ri{width:46px;height:46px;border-radius:16px;display:grid;place-items:center;background:var(--track);color:var(--ink2)}.ri ha-icon{--mdc-icon-size:24px}.rb{font:500 20px Onest,sans-serif;letter-spacing:-.02em;color:var(--ink)}
.lit .ri{background:color-mix(in srgb,var(--amber) 22%,transparent);color:var(--amber)}.cold .ri{background:color-mix(in srgb,var(--ice) 22%,transparent);color:var(--ice)}
.alarm .ri{background:color-mix(in srgb,var(--alert) 18%,transparent);color:var(--alert)}
.t{font-size:40px;line-height:1}.t small{font:300 15px Onest,sans-serif;color:var(--ink2);letter-spacing:0;margin-left:2px}
.tt{display:flex;flex-direction:column;align-items:flex-end;min-width:0}.t2+.t2{margin-top:1px}.t2{font:400 12px Onest,sans-serif;color:var(--ink2);margin-top:3px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:100%}.pic .t2{color:#fff;text-shadow:0 1px 2px rgba(0,0,0,.55)}
.s{font:400 13px Onest,sans-serif;color:var(--ink2);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.chips{display:flex;gap:10px;margin-top:5px;font:500 12px Onest,sans-serif;color:var(--ink2);white-space:nowrap;overflow:hidden}
.chips span{display:inline-flex;align-items:center;gap:3px}:host{container-type:inline-size}@container (max-width:200px){.chips span:nth-child(n+3){display:none}}.chips ha-icon{--mdc-icon-size:14px}.chips .on{color:var(--amber)}.chips .op{color:var(--ice)}
.lamp{width:8px;height:8px;border-radius:4px;background:var(--amber);box-shadow:0 0 12px var(--amber),0 0 3px var(--amber)}
.lit .glow{background:radial-gradient(45% 45% at 75% 22%,var(--amberGlow),transparent 70%)}
.cold .glow{background:radial-gradient(45% 45% at 75% 22%,var(--iceGlow),transparent 70%)}
.alarm .glow{background:radial-gradient(50% 50% at 70% 25%,var(--alertGlow),transparent 70%)}
.alarm::after{content:"";position:absolute;inset:0;border-radius:inherit;box-shadow:inset 0 0 0 1.5px var(--alert);pointer-events:none}
.alarm .s{color:var(--alert);font-weight:600}
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
      const action = clim && num(clim.attributes.current_temperature) !== undefined ? clim.attributes.hvac_action : undefined;
      const cooling = clim && action !== undefined && (action === "cooling" || (clim.state === "cool" && action !== "idle" && action !== "off"));
      // A room in alarm: the red frame and the sign say it; a floor tile names where (persona review 25.10: said once).
      // Priority by colour (ISA-101): only a critical one is red, the rest amber — as on the alert card.
      if (alarm) return { kind: openList.some((a) => a.critical !== false && !a.ackedBy) ? "alarm" : "alarm warn", text: c.rooms?.[alarm.entity_id] ? `${c.rooms[alarm.entity_id]}${alarmsOn.length > 1 ? ` и ещё ${alarmsOn.length - 1}` : ""}` : alarmsOn.length > 1 ? `${alarmsOn.length} ${pluralAlarms(alarmsOn.length)}` : "" };
      const deaf = (c.alarms || []).filter((id) => nd(st(h, id))).length;
      if (deaf) return { kind: "", text: `Нет связи · ${deaf} ${sensorsWord(deaf)}` };
      if (on.length) {
        const dim = on.map((s) => (num(s.attributes.brightness) !== undefined ? brightnessPct(s) : undefined)).filter((x) => x !== undefined);
        return { kind: "lit", text: dim.length ? `Свет · ${Math.round(avg(dim))} %` : "Свет включён" };
      }
      if (cooling) return { kind: "cold", text: `Охлаждение${target !== undefined ? ` до ${fmt(target)}°` : ""}` };
      if (clim && action === "heating") return { kind: "", text: `Нагрев${target !== undefined ? ` до ${fmt(target)}°` : ""}` };
      const lights = (c.lights || []).map((id) => st(h, id));
      const watched = [...lights, ...(c.climate ? [clim] : [])];
      if (watched.length && watched.every(nd)) return { kind: "", text: "Нет данных" };
      // Some lights silent: «Всё выключено» would be a guess (round 3, D9) — say how many have no data, as the hero does.
      const lightsDeaf = lights.filter(nd).length;
      // Short enough for a 390 px tile (round 4, D1: «Нет данных · 3 светил…»); the hero gives the count.
      if (lightsDeaf) return { kind: "", text: "Свет: нет данных" };
      if (clim && nd(clim)) return { kind: "", text: "Климат · нет данных" };
      return { kind: "", text: lights.length ? "Всё выключено" : "Всё в норме" };
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
      const mark = s.kind === "lit" ? `<span class="lamp"></span>` : s.kind === "cold" ? `<ha-icon icon="hauflo:snow" style="color:var(--ice)"></ha-icon>` : s.kind.startsWith("alarm") ? `<ha-icon icon="hauflo:warning" style="color:var(--alert)"></ha-icon>` : "";
      // What's in the room at a glance (owner 2026-10-25: the tiles looked empty) — the room's icon, and a photo when its HA area has one.
      const chips = [
        // «выкл» only when every lamp said so — not under «Свет: нет данных» (round 4).
        (c.lights || []).length && (lightsOn || !(c.lights || []).some((id) => nd(st(h, id)))) ? `<span class="${lightsOn ? "on" : ""}"><ha-icon icon="${lightsOn ? "hauflo:bulb-on" : "hauflo:bulb"}"></ha-icon>${lightsOn ? lightsOn : "выкл"}</span>` : "",
        // «закр.» only when the blinds said so: unknown ones say nothing (persona round 3: tile «закр.», card «Нет данных»).
        (c.covers || []).some((id) => ["open", "opening", "closed", "closing"].includes(st(h, id)?.state)) ? `<span class="${coversOpen ? "op" : ""}"><ha-icon icon="hauflo:blind"></ha-icon>${coversOpen ? `откр. ${coversOpen}` : "закр."}</span>` : "",
        hum !== undefined ? `<span><ha-icon icon="hauflo:humidity"></ha-icon>${fmt(hum)} %</span>` : "",
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
      el.addEventListener("pointerdown", () => {
        held = false;
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
      el.addEventListener("pointerleave", cancel);
      el.addEventListener("pointercancel", cancel);
      el.addEventListener("pointerup", () => {
        cancel();
        if (!held && this._config.path) {
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
.row button{height:46px;padding:0 16px 0 13px;border-radius:15px;display:flex;align-items:center;gap:8px;flex:0 1 auto;max-width:100%;color:var(--ink);box-shadow:var(--eh-edge,inset 0 0 0 1px var(--hair));background:var(--eh-glass,transparent);font:500 13px Onest,sans-serif;min-width:0;transition:background .3s,box-shadow .3s,color .3s}
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
        const where = viewPrefs(this).scenes || c.scene_layout || "blocks";
        const rows = c.rows.filter((r) => (r.always || where === "top") && (r.scenes || []).some((x) => st(this._hass, x.entity)));
        this.hideCard(!rows.length);
        if (!rows.length) return "";
        return `<div class="glass m rows"><div class="hd"><span class="ic"><ha-icon icon="hauflo:scene"></ha-icon></span><span class="caps">${blockTitle(this, "Сценарии")}</span></div>
${rows.map((r) => `<div class="rw"><span class="caps sub">${esc(r.title || (r.kind === "cover" ? "Шторы" : "Свет"))}</span>${sceneChips(this._hass, r.scenes, r.kind === "cover" ? "cover" : r.kind === "mixed" ? "mixed" : "light")}</div>`).join("")}</div>`;
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
.x{align-self:flex-end;width:44px;height:44px;border-radius:22px;border:0;background:var(--eh-track,rgba(127,127,127,.12));color:var(--primary-text-color);font-size:22px;cursor:pointer}`;
  const openClimateSheet = (card) => {
    if (card._pop) return;
    const host = document.createElement("div");
    host.className = "hauflo-climate-sheet";
    const sr = host.attachShadow({ mode: "open" });
    sr.innerHTML = `<style>${SHEET_CSS}</style><div class="sh" role="dialog" aria-modal="true" aria-label="${esc(card._config.label || "Климат")}"><button class="x" aria-label="Закрыть">×</button></div>`;
    const el = document.createElement("hauflo-climate");
    const { compact: _c, size_class: _s, ...rest } = card._config;
    el.setConfig({ ...rest, popup: true });
    sr.querySelector(".sh").appendChild(el);
    const close = () => {
      host.remove();
      card._pop = null;
      document.removeEventListener("keydown", onKey);
    };
    const onKey = (e) => e.key === "Escape" && close();
    document.addEventListener("keydown", onKey);
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
      return [this._config.entity, this._config.cool_setpoint, this._config.heat_setpoint, ...(this._config.extra || []), ...readingsOf(this._config).map((r) => r.entity), ...(this._config.zones || []).map((z) => z.entity)].filter(Boolean);
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
    /** The setpoint ± and the scale move: the one chosen of two (heating / cooling), else the climate's own. */
    setSetpoint(kind, v, eid) {
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
      return tchart(this._histId === id ? this._hist : undefined, { cur, sp: num(a.temperature) }, { H, label, tone });
    }
    css() {
      return `${TCHART_CSS}.c{padding:20px 22px}.row{display:flex;justify-content:space-between;align-items:flex-end}
.t{font-size:76px;line-height:.9}.t sup{font:300 20px Onest,sans-serif;vertical-align:top;position:relative;top:8px;color:var(--ink2);margin-left:2px}
.sub{font:400 13px Onest,sans-serif;color:var(--ink2);margin-top:8px}.st{display:flex;flex-direction:column;gap:8px}
.none{font:300 20px/1.2 Onest,sans-serif;color:var(--ink2);margin:6px 0}
.st button{width:48px;height:48px;border-radius:24px;display:grid;place-items:center;box-shadow:var(--eh-edge,inset 0 0 0 1px var(--hair));background:var(--eh-glass,transparent);font:300 26px Onest,sans-serif}
.st button:active{transform:scale(.94)}.st button:disabled{opacity:.35;pointer-events:none}
.top{display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;min-height:22px}
.nos{font:400 13px Onest,sans-serif;color:var(--ink2);margin-top:4px}.goal{font:500 11px Onest,sans-serif;letter-spacing:.12em;text-transform:uppercase;color:var(--ink3);margin-left:8px;vertical-align:top;position:relative;top:14px}
.day{font:400 13px Onest,sans-serif;color:var(--ink2);margin-top:3px}
.act{display:inline-flex;align-items:center;gap:7px;font:500 12px Onest,sans-serif;color:var(--ink2);padding:4px 10px;border-radius:11px;background:var(--track)}
.act i{width:6px;height:6px;border-radius:3px;background:var(--ink3)}.heat .act i{background:var(--amber);box-shadow:0 0 8px var(--amber)}.cool .act i{background:var(--ice);box-shadow:0 0 8px var(--ice)}
.scale{margin-top:18px;touch-action:pan-y;cursor:ew-resize;user-select:none;-webkit-user-select:none;padding:6px 0}
.ends{display:flex;justify-content:space-between;font:400 11px Onest,sans-serif;color:var(--ink3);margin-top:6px}
.ticks{display:flex;justify-content:space-between;align-items:flex-end;height:34px}
.ticks i{width:2px;border-radius:1px;background:var(--ink3);height:10px;position:relative}.ticks i.m{height:16px}.ticks i.a{height:34px;width:3px;background:var(--amber);box-shadow:0 0 10px var(--amberGlow)}
.ticks i.cur::after{content:"";position:absolute;left:50%;bottom:-9px;width:5px;height:5px;border-radius:3px;transform:translateX(-50%);background:var(--ink2)}
.dragging .t .v{color:var(--amber)}.cool.dragging .t .v{color:var(--ice)}
.cool .ticks i.a{background:var(--ice);box-shadow:0 0 8px var(--iceGlow)}
.ticks i.ph,.ticks i.pc{height:24px;width:3px;opacity:.55}.ticks i.ph{background:var(--amber)}.ticks i.pc{background:var(--ice)}
.ticks i.ph.sel,.ticks i.pc.sel{height:34px;opacity:1}.ticks i.ph.sel{box-shadow:0 0 10px var(--amberGlow)}.ticks i.pc.sel{box-shadow:0 0 10px var(--iceGlow)}
.prs{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px}.pr{display:inline-flex;align-items:center;gap:7px;min-height:36px;padding:0 12px;border-radius:12px;font:500 13px Onest,sans-serif;color:var(--ink2);box-shadow:inset 0 0 0 1px var(--hair)}
.pr::before{content:"";width:8px;height:8px;border-radius:4px;opacity:.45}.pr.heat::before{background:var(--amber)}.pr.cool::before{background:var(--ice)}
.pr.on{color:var(--ink);background:var(--track);box-shadow:none}.pr.on::before{opacity:1}.pr.act::after{content:"●";font-size:8px;color:var(--ink3);margin-left:2px}
.ccn[data-open]{cursor:pointer}.ccn[data-open] .caps::after{content:"›";margin-left:6px;font-size:14px;letter-spacing:0;color:var(--ink3)}
.pair2{font:400 12px/1.3 Onest,sans-serif;color:var(--ink3)}
.modes{display:flex;gap:6px;margin-top:16px}.modes.fan{margin-top:8px;align-items:center}.modes.fan button{white-space:nowrap;padding:0 4px;font-size:12px;min-width:0}.modes .fl{flex:0 0 auto;color:var(--ink3);--mdc-icon-size:18px;padding:0 2px}.modes button{flex:1;min-height:44px;padding:0 6px;border-radius:14px;font:500 13px Onest,sans-serif;color:var(--ink2);box-shadow:inset 0 0 0 1px var(--hair)}
.modes .on{color:var(--ink);background:var(--track);box-shadow:none}
.heat .glow{background:radial-gradient(50% 50% at 20% 10%,var(--amberGlow),transparent 70%);opacity:.6}
.cc{padding:14px 14px 14px 18px}.ccrow{display:flex;align-items:center;gap:12px;position:relative}.ccn{flex:1;min-width:0;display:flex;flex-direction:column;gap:4px}
.ccn .caps{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.ccn .sub{margin:0}
.cct{font-size:40px;line-height:1}.cct small{font:300 15px Onest,sans-serif;color:var(--ink2);margin-left:1px}
.sens{display:flex;gap:6px;margin-top:2px;flex-wrap:wrap}.sens b{font:500 12px Onest,sans-serif;padding:3px 7px;border-radius:8px;background:var(--track);color:var(--ink2);white-space:nowrap}.sens i{font-style:normal;color:var(--ink3);margin-right:5px}.c .sens{margin-top:8px}
.st.row2{flex-direction:row}.st.row2 button{width:44px;height:44px;border-radius:22px;font-size:22px}
.cool .glow{background:radial-gradient(50% 50% at 20% 10%,var(--iceGlow),transparent 70%);opacity:.6}
.zs .caps{display:block;margin-bottom:4px}.zr{display:flex;align-items:center;gap:12px;padding:8px 0;border-top:1px solid var(--hair)}.zr:nth-child(2){border-top:0}
.zn{font:500 15px Onest,sans-serif;color:var(--ink);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.zs .zr .ccn{cursor:pointer}.zs .zr:not(.sel) .zn{color:var(--ink2)}.zs .cct{font-size:32px}.zr.heat .zn::after{content:"";display:inline-block;width:6px;height:6px;border-radius:3px;background:var(--amber);margin-left:7px;vertical-align:middle}`;
    }
    render() {
      const s = st(this._hass, this._config.entity);
      if (!s) return `<div class="glass c"><div class="caps">Нет термостата</div></div>`;
      // No link and nothing known: one honest line, not an empty dial (persona round 3).
      const compact = !this._config.zones?.length && this.isCompact();
      if (!this._config.zones?.length && !compact && ["unavailable", "unknown"].includes(s.state) && num(s.attributes.temperature) === undefined && num(s.attributes.current_temperature) === undefined)
        return `<div class="glass cc"><div class="ccrow"><div class="ccn"><span class="caps">${esc(this._config.label || "Климат")}</span><span class="sub">нет связи с термостатом</span></div></div></div>`;
      if (this._config.zones?.length) return this.zones();
      if (compact) return this.compact(s);
      const a = s.attributes;
      // Two setpoints (heating / cooling, or HA's heat_cool range): the big number is the one the ± and the scale move —
      // the mode's own unless the client tapped the other; both are on the scale.
      const pair = setpointPair(this, s);
      const sel = this.selOf(pair, s);
      const act = pair ? activeMode(s) : null;
      const target = pair ? pair[sel] : num(a.temperature);
      const lo = undefined;
      const hi = undefined;
      const range = false;
      const cur = num(a.current_temperature);
      const hum = num(a.current_humidity) ?? num(st(this._hass, this._config.humidity)?.state);
      const step = Math.max(num(a.target_temp_step) || 0.5, 0.5);
      const min = num(a.min_temp) ?? 5;
      const max = num(a.max_temp) ?? 35;
      const action = a.hvac_action;
      const known = target !== undefined || cur !== undefined;
      const mood = !known ? "" : pair && !act ? (sel === "cool" ? "cool" : "heat") : action === "cooling" || s.state === "cool" ? "cool" : action === "heating" || s.state === "heat" ? "heat" : "";
      // «Нагревает» when it's warmer than the goal isn't believable (persona review 25.10): said only when it fits.
      const fits = (act) => cur === undefined || target === undefined || (act === "heating" ? cur < target + 0.5 : act === "cooling" ? cur > target - 0.5 : true);
      const doing = !known ? "" : action === "heating" && fits("heating") ? "Нагревает" : action === "cooling" && fits("cooling") ? "Охлаждает" : action === "drying" ? "Осушает" : action === "fan" ? "Вентилирует" : action === "idle" ? "Держит" : s.state === "off" ? "Выключен" : "";
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
      const opts = useModes ? modes.map((m) => [m, HVAC[m], known && s.state === m]) : presets.map((p) => [p, PRESET[p] || p, known && a.preset_mode === p]);
      const digits = step < 1 && target !== undefined && target % 1 ? 1 : 0;
      const readings = this.readings();
      // «Комфорт» of the agent (comfort.py): the house's season beside the label, and in winter the floor's setpoint
      // the agent keeps now — the client sees why the room warms (owner 25.10).
      const season = a.house_mode === "summer" ? "Лето" : a.house_mode === "winter" ? "Зима" : "";
      const fl = season === "Зима" && Array.isArray(a.floor_targets) ? a.floor_targets.map(num).filter((v) => v !== undefined) : [];
      const floorNow = fl.length ? ` · пол ${Math.min(...fl) === Math.max(...fl) ? fmt(fl[0]) : `${fmt(Math.min(...fl))}–${fmt(Math.max(...fl))}`}°` : "";
      const chart = this.chart(this._config.entity, this._config.floor ? "Пол" : "В комнате", mood, 96);
      return `<div class="glass c ${mood}"><div class="glow"></div><div style="position:relative">
<div class="top"><span class="caps">${esc(this._config.label || "Климат")}${season ? ` · ${season}` : ""}</span>${doing ? `<span class="act"><i></i>${doing}</span>` : ""}</div>
<div class="row"><div>${range ? `<div class="t thin"><span class="v">${fmt(lo)}–${fmt(hi)}</span><sup>°C</sup></div>` : target === undefined ? `<div class="t thin"><span class="v">—</span></div><div class="nos">${["unavailable", "unknown"].includes(s.state) ? "нет связи с термостатом" : "уставка ещё не пришла с шины"}</div>` : `<div class="t thin"><span class="v">${fmt(target, digits)}</span><sup>°C</sup><span class="goal">${pair ? (sel === "heat" ? "цель · обогрев" : "цель · охлаждение") : "цель"}</span></div>`}
<div class="sub">${cur === undefined ? (this._config.floor ? "нет данных" : "нет данных из комнаты") : `${this._config.floor ? "Сейчас" : "В комнате"} ${fmt(cur, 1)}°`}${floorNow}${hum !== undefined ? ` · влажность ${fmt(hum)} %` : ""}</div>${readings.length ? this.readingsRow(readings) : ""}${pairRow}</div>
<div class="st"><button data-d="${step}" aria-label="Теплее"${target === undefined ? " disabled" : ""}>+</button><button data-d="${-step}" aria-label="Холоднее"${target === undefined ? " disabled" : ""}>−</button></div></div>
${chart}
<div class="scale" data-min="${min}" data-max="${max}" data-step="${step}"${pair ? ` data-pair="1"` : ""}><div class="ticks">${ticks}</div><div class="ends"><span>${fmt(min)}°</span><span>проведите, чтобы изменить</span><span>${fmt(max)}°</span></div></div>
${opts.length ? `<div class="modes">${opts.slice(0, 5).map(([k, l, on]) => `<button class="${on ? "on" : ""}" data-${useModes ? "m" : "p"}="${esc(k)}">${esc(l)}</button>`).join("")}</div>` : ""}
${fans.length > 1 || swing ? `<div class="modes fan"><span class="fl"><ha-icon icon="hauflo:fan"></ha-icon></span>${fans.slice(0, 5).map((f) => `<button class="${known && a.fan_mode === f ? "on" : ""}" data-f="${esc(f)}">${esc(FAN[f] || f)}</button>`).join("")}${swing ? `<button class="${known && a.swing_mode && a.swing_mode !== "off" ? "on" : ""}" data-sw="${esc(a.swing_mode && a.swing_mode !== "off" ? "off" : swing)}">Обдув</button>` : ""}</div>` : ""}</div></div>`;
    }
    /** Floor-heating zones of one room in one card: a line each — name, now, setpoint, ± (persona review 25.10). */
    zones() {
      const zi = Math.min(this._zi || 0, this._config.zones.length - 1);
      const rows = this._config.zones.map((z, i) => {
        const s = st(this._hass, z.entity);
        const a = s?.attributes || {};
        const target = num(a.temperature);
        const cur = num(a.current_temperature);
        const step = Math.max(num(a.target_temp_step) || 0.5, 0.5);
        const heating = a.hvac_action === "heating" && (cur === undefined || target === undefined || cur < target + 0.5);
        return `<div class="zr ${heating ? "heat" : ""}${i === zi ? " sel" : ""}"><div class="ccn" data-z="${i}"><span class="zn">${esc(z.name || nameOf(s, z.entity))}</span><span class="sub">${!s || ["unavailable", "unknown"].includes(s.state) ? "нет связи" : cur === undefined ? "нет данных" : `Сейчас ${fmt(cur, 1)}°`}${heating ? " · нагрев" : ""}${s && !["unavailable", "unknown"].includes(s.state) && target === undefined ? " · уставка ещё не пришла" : ""}</span></div>
<div class="cct thin">${target === undefined ? "—" : `${fmt(target, step < 1 && target % 1 ? 1 : 0)}<small>°</small>`}</div>
<div class="st row2"><button data-e="${esc(z.entity)}" data-d="${-step}" aria-label="Холоднее"${target === undefined ? " disabled" : ""}>−</button><button data-e="${esc(z.entity)}" data-d="${step}" aria-label="Теплее"${target === undefined ? " disabled" : ""}>+</button></div></div>`;
      });
      const any = this._config.zones.some((z) => st(this._hass, z.entity)?.attributes?.hvac_action === "heating");
      // One chart for the zone tapped (the first at the start): a chart per line would be a wall of lines.
      const z = this._config.zones[zi];
      const zs = st(this._hass, z.entity);
      const chart = this.chart(z.entity, `Пол · ${z.name || nameOf(zs, z.entity)}`, zs?.attributes?.hvac_action === "heating" ? "heat" : "", 64);
      return `<div class="glass cc zs ${any ? "heat" : ""}"><div class="glow"></div><div style="position:relative"><div class="caps">${esc(this._config.label || "Тёплый пол")}</div>${rows.join("")}${chart}</div></div>`;
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
      const target = pair ? pair[sel] : num(a.temperature);
      const cur = num(a.current_temperature);
      const step = Math.max(num(a.target_temp_step) || 0.5, 0.5);
      const heating = a.hvac_action === "heating" && (num(a.current_temperature) === undefined || num(a.temperature) === undefined || num(a.current_temperature) < num(a.temperature) + 0.5);
      // A floor with several sensors: each one's reading, by its name (owner 2026-10-25, 27.10).
      const sensors = this.readings();
      // The line is always there (no data says so): a card that grows and shrinks with the state moves the page.
      return `<div class="glass cc ${heating ? "heat" : ""}"><div class="glow"></div><div class="ccrow">
<div class="ccn" data-open="1" role="button" tabindex="0"><span class="caps">${esc(this._config.label || nameOf(s, this._config.entity))}</span><span class="sub">${cur === undefined ? "нет данных" : `Сейчас ${fmt(cur, 1)}°`}${heating ? " · нагрев" : ""}${target === undefined && !["unavailable", "unknown"].includes(s.state) ? " · уставка ещё не пришла" : ""}</span>${pair ? `<span class="pair2">${["heat", "cool"].filter((k) => pair[k] !== undefined).map((k) => `${k === "heat" ? "обогрев до" : "охлаждение от"} ${fmt(pair[k])}°`).join(" · ")}</span>` : ""}${sensors.length > 1 ? this.readingsRow(sensors) : ""}</div>
<div class="cct thin">${target === undefined ? "—" : `${fmt(target, step < 1 && target % 1 ? 1 : 0)}<small>°</small>`}</div>
<div class="st row2"><button data-d="${-step}" aria-label="Холоднее"${target === undefined ? " disabled" : ""}>−</button><button data-d="${step}" aria-label="Теплее"${target === undefined ? " disabled" : ""}>+</button></div></div>${this.chart(this._config.entity, this._config.floor ? "Пол" : "Температура", heating ? "heat" : "", 56)}</div>`;
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
          const base = pair ? pair[sel] : num(a.temperature);
          if (base === undefined) return;
          const v = Math.min(num(a.max_temp) ?? 35, Math.max(num(a.min_temp) ?? 5, base + Number(b.dataset.d)));
          haptic(this);
          this.setSetpoint(sel, v, b.dataset.e);
        }),
      );
      // A swipe along the scale sets the temperature (owner 2026-10-25: tapping ± only is tiresome).
      const sc = this.shadowRoot.querySelector(".scale");
      if (sc) {
        const box = this.shadowRoot.querySelector(".c");
        const v = this.shadowRoot.querySelector(".t .v");
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
            if (dx < 8 || dx < Math.abs(ev.clientY - y0) * 1.5) return;
            sliding = true;
            this._busy = true;
            sc.setPointerCapture(ev.pointerId);
          }
          box.classList.add("dragging");
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
      this.shadowRoot.querySelectorAll("button[data-m]").forEach((b) => b.addEventListener("click", () => call(this._hass, "climate", "set_hvac_mode", { entity_id: id, hvac_mode: b.dataset.m })));
      this.shadowRoot.querySelectorAll("button[data-p]").forEach((b) => b.addEventListener("click", () => call(this._hass, "climate", "set_preset_mode", { entity_id: id, preset_mode: b.dataset.p })));
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
.v{height:216px;padding:14px;display:flex;flex-direction:column;justify-content:space-between;touch-action:none;user-select:none;-webkit-user-select:none;cursor:pointer}
.v>*{position:relative}.lvl{position:absolute !important;left:0;right:0;bottom:0;transition:height .25s}
.light .lvl{background:linear-gradient(0deg,color-mix(in srgb,var(--amber) 85%,transparent),color-mix(in srgb,var(--amber) 50%,transparent));box-shadow:0 -10px 40px var(--amberGlow)}
.cover .lvl{background:linear-gradient(0deg,color-mix(in srgb,var(--ice) 70%,transparent),color-mix(in srgb,var(--ice) 35%,transparent));box-shadow:0 -10px 40px var(--iceGlow)}
.switch .lvl{background:linear-gradient(0deg,color-mix(in srgb,var(--mint) 70%,transparent),color-mix(in srgb,var(--mint) 35%,transparent))}
.dragging .lvl{transition:none}.p{font-size:30px;line-height:1}.off .p{font:300 20px Onest,sans-serif;color:var(--ink3);letter-spacing:0}
.n{font:500 12px Onest,sans-serif;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-top:4px}
.ic{color:var(--ink2)}.unavail{opacity:.45}
.sc{padding:2px 16px 16px;margin-bottom:12px;border-radius:24px}.ud3{display:flex;gap:6px;margin-top:10px}.ud3 button{flex:1;height:44px;border-radius:13px;display:grid;place-items:center;color:var(--ink);box-shadow:inset 0 0 0 1px var(--hair);background:var(--eh-glass,transparent)}.ud3 button ha-icon{--mdc-icon-size:20px}`;
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
          return `<div class="glass v ${m.dom} ${m.on ? "" : "off"} ${moving ? "mv" : ""} ${unavailable ? "unavail" : ""}" data-e="${esc(e.entity)}" role="slider" aria-label="${esc(m.name)}" aria-valuenow="${m.pct}" tabindex="0">
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
            call(this._hass, "cover", b.dataset.c, { entity_id: id });
          }),
        );
        el.addEventListener("pointerdown", (ev) => {
          if (ev.target.closest("button")) return;
          start = ev.clientY;
          moved = false;
          el.setPointerCapture(ev.pointerId);
        });
        el.addEventListener("pointermove", (ev) => {
          if (start === null) return;
          const m = this.model(this.items().find((x) => x.entity === id) || { entity: id });
          if (!m.dimmable) return;
          if (!moved && Math.abs(ev.clientY - start) < 6) return;
          moved = true;
          this._busy = true;
          el.classList.add("dragging");
          pct = at(ev);
          lvl.style.height = `${pct}%`;
          p.textContent = String(pct);
          el.classList.toggle("off", pct === 0 && m.dom === "light");
        });
        const end = () => {
          if (start === null) return;
          start = null;
          el.classList.remove("dragging");
          const m = this.model(this.items().find((x) => x.entity === id) || { entity: id });
          haptic(this);
          if (moved) {
            if (m.dom === "light") call(this._hass, "light", pct ? "turn_on" : "turn_off", pct ? { entity_id: id, brightness_pct: Math.max(1, pct) } : { entity_id: id });
            else if (m.dom === "cover") call(this._hass, "cover", "set_cover_position", { entity_id: id, position: pct });
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
          moreInfo(this, id);
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
.tag{display:inline-flex;align-items:center;gap:8px;font:500 12px Onest,sans-serif;letter-spacing:.18em;text-transform:uppercase;color:var(--alert)}
.tag i{width:8px;height:8px;border-radius:4px;background:var(--alert);box-shadow:0 0 14px var(--alert)}
.call{display:flex;align-items:center;gap:10px;min-height:48px;margin-top:10px;padding:0 16px;border-radius:16px;text-decoration:none;color:var(--ink);background:color-mix(in srgb,var(--alert) 10%,transparent);box-shadow:inset 0 0 0 1px color-mix(in srgb,var(--alert) 35%,transparent);font:500 14px Onest,sans-serif}
.call ha-icon{--mdc-icon-size:20px;color:var(--alert)}.call .cl{flex:1;min-width:0;display:flex;flex-direction:column;padding:8px 0}.call .cl span{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.call b{font:400 13px Onest,sans-serif;color:var(--ink2);white-space:nowrap}.call:active{transform:scale(.98)}
h2{font:300 30px/1.1 Onest,sans-serif;letter-spacing:-.03em;margin:14px 0 0}
.w{font:400 15px/1.5 Onest,sans-serif;color:var(--ink2);margin-top:12px}.todo{font:500 13px/1.45 Onest,sans-serif;color:var(--ink);margin-top:8px;padding:10px 12px;border-radius:14px;background:var(--track)}
.sl{margin-top:22px;height:68px;border-radius:34px;position:relative;display:flex;align-items:center;justify-content:center;font:500 15px Onest,sans-serif;color:var(--ink2);box-shadow:var(--eh-edge,inset 0 0 0 1px var(--hair));background:var(--eh-glass,transparent);touch-action:none;user-select:none;-webkit-user-select:none;overflow:hidden}
.sl span{padding-left:56px}.k{position:absolute;left:5px;top:5px;width:58px;height:58px;border-radius:29px;background:var(--alert);display:grid;place-items:center;color:#fff;box-shadow:0 0 30px var(--alertGlow);cursor:grab}
.k ha-icon{--mdc-icon-size:24px}.done{color:var(--ink)}
.ok{display:flex;align-items:center;gap:10px;font:500 15px Onest,sans-serif;color:var(--ink2)}
.ack{margin-top:12px;align-self:flex-start;height:40px;padding:0 18px;border-radius:14px;font:500 14px Onest,sans-serif;background:var(--track);color:var(--ink);box-shadow:inset 0 0 0 1px var(--hair)}
.more{margin-top:12px;display:flex;flex-direction:column;gap:6px;font:400 13px/1.4 Onest,sans-serif;color:var(--ink2)}.more [data-e]{cursor:pointer}
.nrm{--alert:var(--amber);--alertGlow:var(--amberGlow)}.nrm .tag{color:var(--amber)}.nrm .tag i{background:var(--amber);box-shadow:0 0 14px var(--amberGlow)}.nrm .glow{background:radial-gradient(60% 50% at 20% 0%,var(--amberGlow),transparent 75%)}`;
    }
    render() {
      const h = this._hass;
      const c = this._config;
      const open = openAlarms(this, c.alarms || []);
      const on = open.map((a) => st(h, a.entity)).filter(Boolean);
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
      const since = new Date(top.since || s.last_changed);
      const hhmm = `${String(since.getHours()).padStart(2, "0")}:${String(since.getMinutes()).padStart(2, "0")}`;
      const today = since.toDateString() === new Date().toDateString();
      const when = today ? hhmm : `${String(since.getDate()).padStart(2, "0")}.${String(since.getMonth() + 1).padStart(2, "0")} ${hhmm}`;
      // Priority by colour (ISA-101): critical red, the rest amber — a floor heating fault isn't a leak.
      // Accepted (owner 28.10: «нажимаешь Принято — толку ноль»): calm until it clears; a new alarm comes back red.
      const normal = AGENT.data && (top.critical === false || !!top.ackedBy);
      this.classList.toggle("normal", !!normal);
      const act = c.action && st(h, c.action.entity_id);
      const rest = open.slice(1, 5);
      return `<div class="glass a${normal ? " nrm" : ""}"><div class="glow"></div><div style="position:relative;display:flex;flex-direction:column;flex:1">
<div class="tag"><i></i>${esc(word)}${on.length < 2 ? "" : c.rooms?.[s.entity_id] || c.room ? ` · ${esc(c.rooms?.[s.entity_id] || c.room)}` : c.where ? ` · ${esc(c.where)}` : ""}</div>
<h2>${esc(nameOf(s, s.entity_id))}</h2>
<div class="w">с ${when}${top.returned ? ` · в норме с ${(() => { const r = new Date(top.returned); return `${String(r.getHours()).padStart(2, "0")}:${String(r.getMinutes()).padStart(2, "0")}`; })()} — подтвердите, что видели` : ""}${top.ackedBy ? ` · принял ${esc(top.ackedBy)}` : ""}</div>
${top.key && !top.ackedBy ? `<button class="ack" data-ack="${esc(top.key)}">Принято</button>` : ""}
${rest.length ? `<div class="more">${rest.map((a) => `<div data-e="${esc(a.entity)}">${a.critical === false ? "○" : "●"} ${esc(nameOf(st(h, a.entity), a.entity))}${c.rooms?.[a.entity] ? ` · ${esc(c.rooms[a.entity])}` : ""}${a.ackedBy ? ` · принял ${esc(a.ackedBy)}` : ""}</div>`).join("")}${open.length > 5 ? `<div>ещё ${open.length - 5} ${pluralAlarms(open.length - 5)}</div>` : ""}</div>` : ""}
<div class="todo">${esc(c.todo || alarmTodo(s.attributes.device_class, !!c.service?.phone, !!AGENT.data?._pushed?.includes(s.entity_id)))}</div>
${c.service?.phone ? `<a class="call" href="tel:${esc(String(c.service.phone).replace(/[^\d+]/g, ""))}"><ha-icon icon="mdi:phone"></ha-icon><span class="cl"><span>${c.service.name ? `Позвонить · ${esc(c.service.name)}` : "Позвонить сервису"}</span><b>${esc(c.service.phone)}</b></span></a>` : ""}
${act ? `<div class="sl"><div class="k"><ha-icon icon="${esc(c.action.icon || "hauflo:valve")}"></ha-icon></div><span>Сдвиньте — ${esc((c.action.title || "выполнить").toLowerCase())}</span></div>` : ""}</div></div>`;
    }
    bind() {
      // A tap on the alarm opens the sensor (its history, since when); the swipe stays the action.
      this.shadowRoot.querySelector(".a h2, .a .w")?.closest(".a")?.addEventListener("click", (ev) => {
        if (ev.target.closest(".sl")) return;
        if (ev.target.closest(".ack, .more")) return;
        const on = openAlarms(this, this._config.alarms || [])[0]?.entity;
        if (on) moreInfo(this, on);
      });
      // «Принято» from the screen (SCADA expert 25.10: only a phone's push could accept): the agent records who.
      this.shadowRoot.querySelector("[data-ack]")?.addEventListener("click", async (ev) => {
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
          const again = this.shadowRoot.querySelector(`[data-ack="${CSS.escape(b.dataset.ack)}"]`);
          if (again) again.textContent = tr("Не получилось — ещё раз");
          return;
        }
        AGENT.at = 0;
        AGENT.sig = "";
        refreshAlarms(this._hass);
      });
      this.shadowRoot.querySelectorAll(".more [data-e]").forEach((el) => el.addEventListener("click", (ev) => (ev.stopPropagation(), moreInfo(this, el.dataset.e))));
      const sl = this.shadowRoot.querySelector(".sl");
      if (!sl) return;
      const k = sl.querySelector(".k");
      let x0 = null;
      let dx = 0;
      k.addEventListener("pointerdown", (ev) => {
        x0 = ev.clientX;
        this._busy = true;
        k.setPointerCapture(ev.pointerId);
      });
      k.addEventListener("pointermove", (ev) => {
        if (x0 === null) return;
        const max = sl.clientWidth - k.clientWidth - 10;
        dx = Math.min(max, Math.max(0, ev.clientX - x0));
        k.style.transform = `translateX(${dx}px)`;
      });
      const end = () => {
        if (x0 === null) return;
        x0 = null;
        const max = sl.clientWidth - k.clientWidth - 10;
        const a = this._config.action;
        if (dx >= max * 0.85 && a) {
          haptic(this, "success");
          // Only shutting something off — never a service of the config's choosing (security expert 25.10).
          if (!["switch.turn_off", "valve.close_valve"].includes(a.service)) return;
          const [domain, service] = a.service.split(".");
          call(this._hass, domain, service, { entity_id: a.entity_id });
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
      };
      k.addEventListener("pointerup", end);
      k.addEventListener("pointercancel", end);
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
  const coverDir = (e, cfg) => {
    const d = e.direction || cfg.direction;
    if (d === "horizontal" || d === "vertical") return d;
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
  const coverWord = (s) => (["unknown", "unavailable"].includes(s.state) ? "нет данных" : s.state === "opening" ? "открывается…" : s.state === "closing" ? "закрывается…" : s.state === "closed" ? "закрыта" : "открыта");
  const COVER_BTN = {
    vertical: [["open_cover", "mdi:chevron-up", "Открыть"], ["stop_cover", "mdi:stop", "Стоп"], ["close_cover", "mdi:chevron-down", "Закрыть"]],
    horizontal: [["open_cover", "mdi:curtains", "Открыть"], ["stop_cover", "mdi:stop", "Стоп"], ["close_cover", "mdi:curtains-closed", "Закрыть"]],
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
.pill.wide{grid-column:span 2}.more{margin-top:10px;width:100%;height:44px;border-radius:14px;font:500 13px Onest,sans-serif;color:var(--ink2);box-shadow:inset 0 0 0 1px var(--hair)}
.cover .hd{flex-wrap:nowrap}.cover .hd .sum{flex:1 1 auto;min-width:0}
.hd b{font:300 24px Onest,sans-serif;letter-spacing:-.03em;display:block;line-height:1.05;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.hd b.nd{font:400 15px Onest,sans-serif;letter-spacing:0;color:var(--ink3);margin-top:4px}
.grp{margin-top:14px}.grp .caps{display:block;margin-bottom:8px}.grow{display:flex;gap:6px;flex-wrap:wrap}
.g{display:flex;align-items:center;gap:6px;padding:5px 5px 5px 14px;border-radius:18px;box-shadow:inset 0 0 0 1px var(--hair);font:500 12px Onest,sans-serif;max-width:100%}
.g span{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;hyphens:manual;overflow-wrap:break-word;line-height:1.25}.g.on{background:var(--track);box-shadow:none}.g.on.lt{background:color-mix(in srgb,var(--amber) 22%,transparent)}
.g button{width:44px;height:44px;border-radius:14px;display:grid;place-items:center;color:var(--ink);box-shadow:inset 0 0 0 1px var(--hair);background:var(--eh-glass,transparent);flex:0 0 auto}.g button ha-icon{--mdc-icon-size:20px}.g button:active,.ud button:active{transform:scale(.92)}
.g.lt{padding:0 16px;height:44px;cursor:pointer}
.g.gc{display:grid;grid-template-columns:1fr repeat(3,44px);gap:6px;width:100%;padding:5px 5px 5px 16px;font:500 13px Onest,sans-serif}
.g.gc span{min-width:0;overflow:hidden;white-space:normal;line-height:1.25;display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:2;align-self:center;overflow-wrap:anywhere}.g.gc small{font:400 12px Onest,sans-serif;color:var(--ice)}
.g.gc.mv{background:color-mix(in srgb,var(--ice) 14%,transparent);box-shadow:none}.hd .caps{display:block;margin-bottom:4px}
.mst{height:44px;border-radius:22px;display:flex;align-items:center;gap:8px;padding:0 6px}
.mst button{height:44px;min-width:44px;padding:0 16px;border-radius:22px;font:500 13px Onest,sans-serif;color:var(--ink);box-shadow:inset 0 0 0 1px color-mix(in srgb,var(--ink) 22%,transparent)}
.mst button.on{background:color-mix(in srgb,var(--amber) 16%,transparent);color:var(--amber);box-shadow:inset 0 0 0 1.5px color-mix(in srgb,var(--amber) 70%,transparent)}
.mst button:active,.pill:active{transform:scale(.97)}
.bar{margin-top:14px;height:40px;border-radius:14px;background:var(--track);position:relative;overflow:hidden;touch-action:none;cursor:ew-resize}
.bar i{position:absolute;left:0;top:0;bottom:0;background:linear-gradient(90deg,color-mix(in srgb,var(--amber) 55%,transparent),color-mix(in srgb,var(--amber) 85%,transparent));box-shadow:10px 0 30px var(--amberGlow)}
.cover .bar i{background:linear-gradient(90deg,color-mix(in srgb,var(--ice) 35%,transparent),color-mix(in srgb,var(--ice) 65%,transparent));box-shadow:10px 0 30px var(--iceGlow)}
.mst button.ico{width:44px;height:44px;padding:0;border-radius:22px;display:grid;place-items:center;color:var(--ink)}.mst button.ico ha-icon{--mdc-icon-size:21px}.cover .mst{height:auto}
.bar span{position:absolute;left:14px;top:11px;font:500 12px Onest,sans-serif}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:14px}
.chips button{flex:0 0 auto;min-height:44px;padding:0 15px;display:inline-flex;align-items:center;border-radius:14px;font:500 12px Onest,sans-serif;color:var(--ink2);box-shadow:inset 0 0 0 1px var(--hair)}
.chips button.on{color:var(--ink);background:var(--track);box-shadow:none}.chips em{font-style:normal;color:var(--amber);margin-left:5px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:12px}
.pill{position:relative;min-height:56px;padding-top:7px;padding-bottom:7px;border-radius:18px;overflow:hidden;display:flex;align-items:center;gap:10px;padding:0 12px;box-shadow:inset 0 0 0 1px var(--hair);touch-action:pan-y;user-select:none;-webkit-user-select:none;cursor:pointer}
.pill .f{position:absolute;left:0;top:0;bottom:0;transition:width .25s}
.pill.dragging .f{transition:none}.pill>*:not(.f){position:relative}
.pill ha-icon{color:var(--ink3);--mdc-icon-size:18px}.pill .tx{min-width:0;flex:1}
.pill .nm{font:500 13px/1.25 Onest,sans-serif;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;overflow-wrap:break-word;hyphens:manual}
.pill .lv{font:400 13px Onest,sans-serif;color:var(--ink2);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.pill.on{box-shadow:none;background:var(--track)}.pill.on>ha-icon{color:var(--accentC)}
.light .pill.on .f{background:linear-gradient(90deg,color-mix(in srgb,var(--amber) 42%,transparent),color-mix(in srgb,var(--amber) 22%,transparent))}
.cover .pill.on .f{background:linear-gradient(90deg,color-mix(in srgb,var(--ice) 36%,transparent),color-mix(in srgb,var(--ice) 18%,transparent))}
.light{--accentC:var(--amber)}.cover{--accentC:var(--ice)}
.pill.solo.on{background:color-mix(in srgb,var(--amber) 22%,transparent);box-shadow:0 0 22px color-mix(in srgb,var(--amberGlow) 50%,transparent)}
.ud{display:flex;gap:6px}.ud button{width:44px;height:44px;border-radius:14px;display:grid;place-items:center;color:var(--ink);box-shadow:inset 0 0 0 1px var(--hair);background:var(--eh-glass,transparent)}.ud button ha-icon{--mdc-icon-size:19px}
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
      const summary = unknownAll && total ? "Нет данных" : isCover ? (n ? `${n} из ${total} открыто` : "Все закрыты") : shownN ? `${shownN} из ${shownTotal}${lvl !== undefined ? ` · ${lvl} %` : ""}` : "Выключен";
      const master = isCover
        ? coverButtons(all.length && all.filter((e) => coverDir(e, this._config) === "horizontal").length * 2 >= all.length ? "horizontal" : "vertical", "data-all").replace(/<button /g, '<button class="ico" ')
        : `<button data-all="${n ? "turn_off" : "turn_on"}" class="${n ? "on" : ""}">${n ? "Выкл. все" : "Вкл. все"}</button>`;
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
<ha-icon icon="${esc(e.icon || (coverDir(e, this._config) === "horizontal" ? "hauflo:curtain" : "hauflo:blind"))}"></ha-icon><div class="tx"><div class="nm">${esc(name)}</div><div class="lv">${esc(zoneTagAll)}${state}</div></div>
${settable ? "" : `<div class="ud">${coverButtons(coverDir(e, this._config), "data-c")}</div>`}</div>`;
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
${hidden ? `<button class="more" data-more="1">${coversFolded ? `По одной (${zoneItems.length})` : `Показать все (${total})`}</button>` : this._expanded && (total > LIMIT || coversFolded) ? `<button class="more" data-more="0">Свернуть</button>` : ""}</div>`;
    }
    /** Commands for several devices at once («Шторы 4, 6», «Подсветка (вся)») on their own row. */
    groupsRow() {
      const gs = (this._config.groups || []).map((id) => ({ id, s: st(this._hass, id) })).filter((g) => g.s);
      if (!gs.length) return "";
      const label = (g) => nameOf(g.s, g.id).replace(/\s*\((вс[яеё]|весь|all)\)\s*$/i, "");
      return `<div class="grp"><span class="caps">Группы</span><div class="grow">${gs
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
          haptic(this);
          call(this._hass, domain, b.dataset.all, { entity_id: shownIds() });
        }),
      );
      root.querySelectorAll("button[data-gc]").forEach((b) =>
        b.addEventListener("click", () => {
          haptic(this);
          call(this._hass, "cover", b.dataset.gc, { entity_id: b.dataset.g });
        }),
      );
      root.querySelectorAll("[data-gt]").forEach((b) =>
        b.addEventListener("click", () => {
          haptic(this);
          call(this._hass, "homeassistant", "toggle", { entity_id: b.dataset.gt });
        }),
      );
      root.querySelectorAll(".ud button").forEach((b) =>
        b.addEventListener("click", (ev) => {
          ev.stopPropagation();
          haptic(this);
          call(this._hass, "cover", b.dataset.c, { entity_id: b.closest(".pill").dataset.e });
        }),
      );
      // Sideways drag on a pill (or the master bar) sets the level; a tap toggles.
      const drag = (el, apply, tap) => {
        let x0 = null;
        let moved = false;
        let pct = 0;
        const f = el.querySelector(".f, i");
        const lv = el.querySelector(".lv, span");
        el.addEventListener("pointerdown", (ev) => {
          if (ev.target.closest("button")) return;
          x0 = ev.clientX;
          moved = false;
          this._busy = true;
        });
        el.addEventListener("pointermove", (ev) => {
          if (x0 === null) return;
          if (!moved && Math.abs(ev.clientX - x0) < 8) return;
          if (!moved) {
            moved = true;
            this._busy = true;
            el.setPointerCapture(ev.pointerId);
            el.classList.add("dragging");
          }
          const r = el.getBoundingClientRect();
          pct = Math.round(Math.min(100, Math.max(0, ((ev.clientX - r.left) / r.width) * 100)));
          f.style.width = `${pct}%`;
          if (lv) lv.textContent = tr(el.dataset.bar ? `${isCover ? "Все" : "Яркость"} · ${pct} %` : `${pct} %`);
        });
        const end = () => {
          if (x0 === null) return;
          x0 = null;
          el.classList.remove("dragging");
          haptic(this);
          if (moved) apply(pct);
          else tap && tap();
          setTimeout(() => {
            this._busy = false;
            this._sig = "";
            this._update();
          }, 450);
        };
        el.addEventListener("pointerup", end);
        el.addEventListener("pointercancel", end);
      };
      root.querySelectorAll(".pill").forEach((el) => {
        const id = el.dataset.e;
        const settable = el.dataset.set === "1";
        el.addEventListener("contextmenu", (ev) => {
          ev.preventDefault();
          moreInfo(this, id);
        });
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
.ic{--mdc-icon-size:46px;color:var(--amber);filter:drop-shadow(0 0 16px var(--amberGlow))}.cond{font:400 15px Onest,sans-serif;margin-top:6px}
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
.vol{margin-top:12px;height:44px;border-radius:12px;background:var(--track);position:relative;overflow:hidden;touch-action:none;cursor:ew-resize}
.vol i{position:absolute;left:0;top:0;bottom:0;background:color-mix(in srgb,var(--ink) 18%,transparent)}.vol span{position:absolute;left:14px;top:13px;font:500 12px Onest,sans-serif;display:flex;gap:6px;align-items:center}.vol ha-icon{--mdc-icon-size:15px}
.off{display:flex;align-items:center;justify-content:space-between;position:relative}.pw{width:46px;height:46px;border-radius:23px;display:grid;place-items:center;box-shadow:inset 0 0 0 1px var(--hair)}`;
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
      let on = false;
      const set = (ev, send) => {
        const r = vol.getBoundingClientRect();
        const v = Math.min(1, Math.max(0, (ev.clientX - r.left) / r.width));
        vol.querySelector("i").style.width = `${Math.round(v * 100)}%`;
        vol.querySelector("span").lastChild.textContent = `${Math.round(v * 100)} %`;
        if (send) call(this._hass, "media_player", "volume_set", { entity_id: id, volume_level: Math.round(v * 100) / 100 });
      };
      vol.addEventListener("pointerdown", (ev) => {
        on = true;
        this._busy = true;
        vol.setPointerCapture(ev.pointerId);
        set(ev);
      });
      vol.addEventListener("pointermove", (ev) => on && set(ev));
      const end = (ev) => {
        if (!on) return;
        on = false;
        set(ev, true);
        setTimeout(() => {
          this._busy = false;
          this._sig = "";
          this._update();
        }, 400);
      };
      vol.addEventListener("pointerup", end);
      vol.addEventListener("pointercancel", end);
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
.run{height:44px;padding:0 16px;border-radius:14px;font:500 12px Onest,sans-serif;box-shadow:inset 0 0 0 1px var(--hair)}.run.go{background:color-mix(in srgb,var(--mint) 25%,transparent)}
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
      if (["sensor", "number", "input_number"].includes(d)) return [`${fmt(num(s.state), num(s.state) % 1 ? 1 : 0)} ${unitOf(s)}`.trim(), "val"];
      if (d === "climate") return [s.state === "off" ? "выключен" : `${HVAC[s.state] || s.state}${num(s.attributes.current_temperature) !== undefined ? ` · ${fmt(s.attributes.current_temperature, 1)}°` : ""}`, s.state === "off" ? "stop" : "run"];
      if (d === "water_heater") return [`${s.state === "off" ? "выключен" : "работает"}${num(s.attributes.current_temperature) !== undefined ? ` · ${fmt(s.attributes.current_temperature)}°` : ""}`, s.state === "off" ? "stop" : "run"];
      if (d === "valve") return [s.state === "open" ? "открыт" : s.state === "closed" ? "закрыт" : s.state, s.state === "open" ? "run" : "stop"];
      return isOn(s) ? ["работает", "run"] : ["остановлен", "stop"];
    }
    css() {
      return `.d{padding:16px}.hd{display:flex;justify-content:space-between;align-items:center;margin-bottom:6px}.sum{font:500 12px Onest,sans-serif;display:flex;align-items:center;gap:6px}
.sum i,.r i{width:8px;height:8px;border-radius:4px;flex:0 0 auto}.ok i,.run i,i.ok{background:var(--mint);box-shadow:0 0 8px var(--mint)}.stop i{background:var(--ink3)}.fault i,i.fault{background:var(--alert);box-shadow:0 0 10px var(--alert)}.nolink i{background:transparent;box-shadow:inset 0 0 0 1.5px var(--ink3)}.val i{background:var(--ice)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px;margin-top:8px}
.r{display:flex;align-items:center;gap:10px;padding:10px 12px;min-height:56px;border-radius:18px;box-shadow:inset 0 0 0 1px var(--hair);cursor:pointer;min-width:0}.r .tx{min-width:0}
.nm{font:500 13px/1.25 Onest,sans-serif;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;overflow-wrap:break-word;hyphens:manual}
.fault,.warn{box-shadow:none}.w{font:400 13px Onest,sans-serif;color:var(--ink2)}
.fault{background:color-mix(in srgb,var(--alert) 14%,transparent)}.fault .w{color:var(--alert);font-weight:600}
.warn i,i.warn{background:var(--amber);box-shadow:0 0 8px var(--amberGlow)}.warn{background:color-mix(in srgb,var(--amber) 14%,transparent)}.warn .w{color:var(--amber);font-weight:600}`;
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
          return `<div class="r ${k}" data-e="${esc(e.entity)}"><i></i><div class="tx"><div class="nm">${esc(e.name || nameOf(e.s, e.entity))}</div><div class="w">${esc(w)}</div></div></div>`;
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
  // integration's entities in that HA area). The cloud sends only the area id, never foreign entity ids: the card reads
  // HA's registry here. A tap on anything not Hauflo's opens HA's own dialog (its controls, its labels) — a config made
  // in the cloud can't dress a gate up as a lamp; Hauflo's own (on this Hauflo dashboard already) keeps its one tap.
  const AREA_GROUPS = [
    ["climate", "Климат", (d) => ["climate", "water_heater", "humidifier"].includes(d)],
    ["values", "Показания", (d) => ["sensor", "number", "input_number"].includes(d)],
    ["switches", "Выключатели", (d) => ["switch", "light", "fan", "input_boolean", "valve", "cover"].includes(d)],
    ["status", "Датчики", (d) => d === "binary_sensor"],
    ["other", "Прочее", () => true],
  ];
  const AREA_ICON = { climate: "mdi:thermostat", water_heater: "mdi:water-boiler", sensor: "mdi:gauge", number: "mdi:tune-variant", switch: "mdi:toggle-switch-outline", light: "mdi:lightbulb-outline", fan: "mdi:fan", binary_sensor: "mdi:checkbox-blank-circle-outline", valve: "mdi:valve", cover: "mdi:window-shutter", lock: "mdi:lock-outline", camera: "mdi:cctv", media_player: "mdi:speaker" };
  /** Entity ids on the open Hauflo dashboard's config: the agent let them through as Hauflo's own (or read-only). */
  const OWNED = { url: "", ids: null, wait: null };
  const ownedOnDashboard = (card) => {
    const url = location.pathname.split("/")[1] || "";
    const h = card._hass;
    if (!/^hauflo-/.test(url) || !h?.callWS) return new Set();
    if (OWNED.url !== url && !OWNED.wait) {
      OWNED.url = url;
      OWNED.wait = h
        .callWS({ type: "lovelace/config", url_path: url })
        .then((cfg) => (OWNED.ids = new Set(JSON.stringify(cfg).match(/"[a-z_]+\.[a-z0-9_]+"/g)?.map((x) => x.slice(1, -1)) ?? [])))
        .catch(() => (OWNED.ids = new Set()))
        .finally(() => {
          OWNED.wait = null;
          card._sig = "";
          card._update();
        });
    }
    return OWNED.ids || new Set();
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
        .sort((a, b) => nameOf(h.states[a], a).localeCompare(nameOf(h.states[b], b)));
      this._memo = { ents: h.entities, devs: h.devices, area, ids };
      return ids;
    }
    watched() {
      return this.members();
    }
    word(s) {
      const d = s.entity_id.split(".")[0];
      if (["switch", "light", "fan", "input_boolean"].includes(d) && !["unavailable", "unknown"].includes(s.state)) return s.state === "on" ? ["вкл", "run"] : ["выкл", "stop"];
      if (d === "cover" && !["unavailable", "unknown"].includes(s.state)) return [s.state === "open" ? "открыта" : s.state === "closed" ? "закрыта" : s.state, s.state === "open" ? "run" : "stop"];
      return super.word(s);
    }
    css() {
      return `${super.css()}.d .sub{display:block;margin:14px 0 0 2px;font-size:10px}.r ha-icon{--mdc-icon-size:18px;color:var(--ink3);flex:0 0 auto}.r.run ha-icon{color:var(--mint)}.r.fault ha-icon{color:var(--alert)}
.empty{font:400 14px/1.5 Onest,sans-serif;color:var(--ink2);margin:6px 2px 2px}.r{min-height:56px}`;
    }
    render() {
      const h = this._hass;
      const ids = this.members().filter((id) => h.states[id]);
      const areaName = h.areas?.[this._config.area]?.name;
      const title = this._config.title || areaName || "Комната";
      this.hideCard(false);
      if (!ids.length) return `<div class="glass d"><span class="caps">${esc(title)}</span><p class="empty">Здесь пока ничего нет — добавьте устройства в проект или свои карточки в Home Assistant</p></div>`;
      const owned = ownedOnDashboard(this);
      const left = new Set(ids);
      const groups = AREA_GROUPS.map(([k, label, fits]) => {
        const list = [...left].filter((id) => fits(id.split(".")[0]));
        list.forEach((id) => left.delete(id));
        return [k, label, list];
      }).filter(([, , list]) => list.length);
      const faults = ids.filter((id) => this.word(h.states[id])[1] === "fault").length;
      return `<div class="glass d"><div class="hd"><span class="caps">${esc(title)}</span>${faults ? `<span class="sum"><i class="fault"></i>аварий: ${faults}</span>` : ""}</div>${groups
        .map(
          ([, label, list]) =>
            `${groups.length > 1 ? `<span class="caps sub">${esc(label)}</span>` : ""}<div class="grid">${list
              .map((id) => {
                const s = h.states[id];
                const [w, k] = this.word(s);
                const d = id.split(".")[0];
                const tap = owned.has(id) && ["switch", "light", "fan", "input_boolean"].includes(d) && !nd(s) ? "toggle" : "info";
                return `<div class="r ${k}" data-e="${esc(id)}" data-tap="${tap}" role="button" tabindex="0"><ha-icon icon="${esc(s.attributes.icon || AREA_ICON[d] || "mdi:circle-medium")}"></ha-icon><div class="tx"><div class="nm">${esc(nameOf(s, id))}</div><div class="w">${esc(w)}</div></div></div>`;
              })
              .join("")}</div>`,
        )
        .join("")}</div>`;
    }
    bind() {
      this.shadowRoot.querySelectorAll(".r").forEach((r) =>
        r.addEventListener("click", () => {
          if (r.dataset.tap === "toggle") {
            haptic(this);
            call(this._hass, "homeassistant", "toggle", { entity_id: r.dataset.e });
          } else moreInfo(this, r.dataset.e);
        }),
      );
    }
  }

  // --- hauflo-security: the alarm system, locks, doors and windows --------------------------------
  const ALARM_STATE = { disarmed: ["Снята с охраны", "ok"], armed_home: ["Под охраной · дома", "arm"], armed_away: ["Под охраной", "arm"], armed_night: ["Ночной режим", "arm"], armed_vacation: ["Отпуск", "arm"], armed_custom_bypass: ["Под охраной", "arm"], arming: ["Ставится…", "wait"], disarming: ["Снимается…", "wait"], pending: ["Задержка входа…", "wait"], triggered: ["ТРЕВОГА", "alarm"] };
  class Security extends EHCard {
    watched() {
      const c = this._config;
      return [c.entity, ...(c.locks || []), ...(c.openings || [])].map((e) => (typeof e === "string" ? e : e?.entity)).filter(Boolean);
    }
    css() {
      return `.s{padding:18px 20px}.big{display:flex;align-items:center;gap:14px;margin-top:8px}.sh{width:56px;height:56px;border-radius:20px;display:grid;place-items:center}.sh ha-icon{--mdc-icon-size:28px}
.ok .sh{background:var(--track);color:var(--ink2)}.arm .sh{background:color-mix(in srgb,var(--mint) 22%,transparent);color:var(--mint)}.alarm .sh{background:var(--alert);color:#fff;box-shadow:0 0 30px var(--alertGlow)}.wait .sh{background:color-mix(in srgb,var(--amber) 22%,transparent);color:var(--amber)}
.st{font:300 24px Onest,sans-serif;letter-spacing:-.02em}.sub{font:400 13px Onest,sans-serif;color:var(--ink2);margin-top:2px}
.btns{display:flex;gap:6px;margin-top:14px}.btns button{flex:1;height:44px;border-radius:14px;font:500 13px Onest,sans-serif;box-shadow:inset 0 0 0 1px var(--hair)}.btns button.on{background:var(--track);box-shadow:none}
.lst{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:12px}.it{display:flex;align-items:center;gap:10px;padding:10px 12px;border-radius:14px;background:var(--track);cursor:pointer;min-width:0}
.it .nm{font:500 13px Onest,sans-serif;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;hyphens:manual;overflow-wrap:break-word;line-height:1.25}.it .w{font:400 13px Onest,sans-serif;color:var(--ink2)}.it.bad{background:color-mix(in srgb,var(--amber) 18%,transparent)}.it.bad ha-icon{color:var(--amber)}
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
    input_boolean: { icon: "hauflo:switch", word: (s) => (s.state === "on" ? "вкл" : "выкл"), tap: ["input_boolean", "toggle"] },
    button: { icon: "mdi:gesture-tap-button", word: (s, name = "") => (/звон|bell/i.test(name) ? "позвонить" : /калит|ворот|двер|замок|gate|door/i.test(name) ? "открыть" : "нажать"), tap: ["button", "press"], on: () => false, action: true },
    binary_sensor: { icon: "hauflo:info", word: (s) => (OPENING.includes(s.attributes.device_class) ? (s.state === "on" ? "открыто" : "закрыто") : s.state === "on" ? "сработал" : "в норме"), tap: null, tone: (s) => (s.state === "on" && ["moisture", "smoke", "gas", "problem", "safety", "carbon_monoxide"].includes(s.attributes.device_class) ? "alarm" : ""), on: (s) => s.state === "on" && !["moisture", "smoke", "gas", "problem", "safety", "carbon_monoxide"].includes(s.attributes.device_class) },
    number: { icon: "hauflo:info", word: (s) => (num(s.state) === undefined ? "нет данных" : `${fmt(num(s.state), num(s.state) % 1 ? 1 : 0)} ${unitOf(s) || (num(s.attributes.max) === 100 && num(s.attributes.min) === 0 ? "%" : "")}`.trim()), tap: null, on: () => false },
    sensor: { icon: "hauflo:info", word: (s) => `${fmt(num(s.state), num(s.state) % 1 ? 1 : 0)} ${unitOf(s)}`.trim(), tap: null, on: () => false, tone: (s) => (s.attributes.device_class === "battery" && num(s.state) !== undefined && num(s.state) < 20 ? "warn" : "") },
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
      return (this._config.entities || []).map((e) => (typeof e === "string" ? e : e.entity));
    }
    disconnectedCallback() {
      this.closeEdit();
    }
    css() {
      return `.k{padding:16px}.hd{display:flex;align-items:center;gap:10px}.hd .caps{flex:1}.sum{font:400 13px Onest,sans-serif;color:var(--ink2);margin-top:2px}
.off{min-height:40px;padding:0 14px;border-radius:20px;font:500 13px Onest,sans-serif;color:var(--ink2);box-shadow:inset 0 0 0 1px var(--hair)}
.rows{margin-top:10px;display:flex;flex-direction:column}.r{display:flex;align-items:center;gap:12px;min-height:52px;border-top:1px solid var(--hair);cursor:pointer}.r:first-child,.fl+.r{border-top:0}
.r ha-icon{--mdc-icon-size:20px;color:var(--ink3)}.r.on ha-icon{color:var(--ice)}.r .nm{flex:1;min-width:0;font:500 14px Onest,sans-serif;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.r .w{font:400 13px Onest,sans-serif;color:var(--ink2);white-space:nowrap}.r .t{font:300 22px Onest,sans-serif;min-width:52px;text-align:right}.r.un{opacity:.5}
.r button.t{min-height:44px;padding:0 6px;margin-right:-6px;border-radius:14px;color:var(--ink);font-variant-numeric:tabular-nums}.r button.t:hover{background:var(--track)}.r .t.pend{color:var(--ice)}.fh .r .t.pend{color:var(--amber)}
.r .ed{display:flex;align-items:center;gap:2px;margin-right:-6px}.r .ed button{width:44px;height:44px;border-radius:22px;background:var(--track);color:var(--ink);font:400 22px/1 Onest,sans-serif;display:grid;place-items:center}
.r .ed b{font:300 22px Onest,sans-serif;min-width:58px;text-align:center;color:var(--ice)}.fh .r .ed b{color:var(--amber)}.r .ed button:disabled{opacity:.35;cursor:default}
.r .nm small{display:block;font:400 12px Onest,sans-serif;color:var(--ink2);margin-top:1px;overflow:hidden;text-overflow:ellipsis}.r .nm small.err{color:var(--alert)}.fh .r.on ha-icon{color:var(--amber)}.fl{font:500 11px/1.2 Onest,sans-serif;letter-spacing:.14em;text-transform:uppercase;color:var(--ink3);padding:14px 0 6px;border-top:1px solid var(--hair)}.fl:first-child{border-top:0;padding-top:4px}`;
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
      const heats = (s) => {
        const a = s.attributes;
        const cur = num(a.current_temperature);
        const t = num(a.temperature);
        return known(s) && s.state !== "off" && a.hvac_action === "heating" && (cur === undefined || t === undefined || cur < t + 0.5);
      };
      const on = items.filter((e) => known(e.s) && e.s.state !== "off" && (!fh || heats(e.s)));
      const offable = fh ? [] : on.filter((e) => canTurnOff(e.s));
      // Sub-headers by the building's floor when the rows say it and there's more than one.
      const floors = [...new Set(items.map((e) => e.floor).filter(Boolean))];
      // A value sent is shown at once and until the thermostat says it (or 10 s — then its own again).
      const now = Date.now();
      for (const [id, p] of Object.entries(this._pend)) {
        const sp = this.sp(st(this._hass, id));
        if (now - p.at > 10000 || (sp && Math.abs(sp.v - p.v) < 0.01 && p.sent)) delete this._pend[id];
      }
      if (this._ed && !items.some((e) => e.entity === this._ed.id && known(e.s) && e.s.state !== "off")) this._ed = null;
      let last;
      const rows = items.map((e) => {
        const a = e.s.attributes;
        const cur = num(a.current_temperature);
        const sp = known(e.s) ? this.sp(e.s) : null;
        const pend = this._pend[e.entity];
        const target = pend ? pend.v : sp?.v;
        // The mode on every row — «Нагрев» next to «Нагревает» in one list read as two different things (integrator run 17).
        const word = !known(e.s) ? (["unavailable", "unknown"].includes(e.s.state) ? "нет связи" : "нет уставки") : e.s.state === "off" ? "Выкл" : fh ? (heats(e.s) ? "греет" : "держит") : HVAC[e.s.state] || e.s.state;
        const head = floors.length > 1 && e.floor && e.floor !== last ? `<div class="fl">${esc(e.floor)}</div>` : "";
        last = e.floor;
        const lit = fh ? heats(e.s) : known(e.s) && e.s.state !== "off";
        const editing = this._ed?.id === e.entity;
        const state = `${word}${cur !== undefined ? ` · ${fmt(cur, 1)}°` : ""}`;
        const failed = this._failed?.id === e.entity && now - this._failed.at < 6000;
        const name = e.zone && String(e.name || "").endsWith(` · ${e.zone}`) ? e.name.slice(0, -e.zone.length - 3) : e.name || nameOf(e.s, e.entity);
        // Under the name: the zone; while the stepper is open — the state (no room for both on a phone); a failed send.
        const sub = failed ? `<small class="err">не отправлено — нет связи с домом</small>` : editing ? `<small>${esc(state)}</small>` : e.zone && String(e.name || "").endsWith(` · ${e.zone}`) ? `<small>${esc(e.zone)}</small>` : "";
        const digits = (v) => fmt(v, sp && sp.step < 1 && v % 1 ? 1 : Number.isInteger(v) ? 0 : 1);
        // The setpoint: a tap on it opens − 26° + for this row only; the rest of the row opens the room / the dialog.
        const tail = editing
          ? `<div class="ed" data-ed="1"><button data-sd="-1" aria-label="Холоднее"${this._ed.v - sp.step < sp.min - 0.001 ? " disabled" : ""}>−</button><b aria-live="polite">${digits(this._ed.v)}°</b><button data-sd="1" aria-label="Теплее"${this._ed.v + sp.step > sp.max + 0.001 ? " disabled" : ""}>+</button></div>`
          : target === undefined || e.s.state === "off" || !sp
            ? `<div class="t">—</div>`
            : `<button class="t${pend ? " pend" : ""}" data-t="${esc(e.entity)}" aria-label="Изменить уставку">${digits(target)}°</button>`;
        return `${head}<div class="r ${lit ? "on" : ""} ${known(e.s) ? "" : "un"}" data-i="${esc(e.entity)}"${e.path ? ` data-p="${esc(e.path)}"` : ""}><ha-icon icon="hauflo:${fh ? "floor-heat" : "ac"}"></ha-icon><div class="nm">${esc(name)}${sub}</div>${editing ? "" : `<div class="w">${esc(state)}</div>`}${tail}</div>`;
      });
      const sum = fh
        ? on.length ? `${on.length} из ${items.length} греют` : items.some((e) => known(e.s)) ? "Сейчас не греют" : "Нет связи"
        : on.length ? `${on.length} из ${items.length} работают` : items.some((e) => known(e.s)) ? "Все выключены" : "Нет связи";
      return `<div class="glass k${fh ? " fh" : ""}"><div class="hd"><div><span class="caps">${blockTitle(this, this.defaultTitle(fh, items))}</span><div class="sum">${sum}</div></div>${offable.length ? `<button class="off" data-off="1">Выкл. все</button>` : ""}</div><div class="rows">${rows.join("")}</div></div>`;
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
      // A row with its room's page opens the room (the floor heating); otherwise the device's own dialog.
      this.shadowRoot.querySelectorAll("[data-i]").forEach((r) =>
        r.addEventListener("click", (ev) => {
          if (ev.composedPath().some((n) => n.dataset && (n.dataset.t || n.dataset.ed))) return;
          r.dataset.p ? navigate(r.dataset.p) : moreInfo(this, r.dataset.i);
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
      this.shadowRoot.querySelector("[data-off]")?.addEventListener("click", () => {
        haptic(this);
        // Only those that can be switched off (a KNX AC with cool/heat only can't: HA answered with an error).
        const ids = (this._config.entities || []).map((e) => (typeof e === "string" ? e : e.entity)).filter((id) => { const s = st(this._hass, id); return s && !["off", "unavailable", "unknown"].includes(s.state) && canTurnOff(s); });
        if (ids.length) call(this._hass, "climate", "turn_off", { entity_id: ids });
      });
    }
  }

  class Devices extends EHCard {
    watched() {
      return (this._config.entities || []).map((e) => (typeof e === "string" ? e : e.entity));
    }
    css() {
      return `.k{padding:16px}.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px;margin-top:10px}
.p{display:flex;align-items:center;gap:10px;min-height:58px;padding:0 12px;border-radius:18px;box-shadow:inset 0 0 0 1px var(--hair);cursor:pointer;user-select:none;-webkit-user-select:none;min-width:0}
.p ha-icon{color:var(--ink3);--mdc-icon-size:20px;flex:0 0 auto}.p .tx{min-width:0}.nm{font:500 13px/1.25 Onest,sans-serif;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;overflow-wrap:break-word;hyphens:manual}.w{font:400 13px Onest,sans-serif;color:var(--ink2)}
.p.on{background:color-mix(in srgb,var(--mint) 16%,transparent);box-shadow:none}.p.on ha-icon{color:var(--mint)}
.p.act{box-shadow:inset 0 0 0 1.5px color-mix(in srgb,var(--amber) 55%,transparent)}.p.act ha-icon{color:var(--amber)}.p.act .w{color:var(--ink2)}.p.act.sent{background:color-mix(in srgb,var(--amber) 14%,transparent)}.p.act:active{transform:scale(.97)}
.p.warn{background:color-mix(in srgb,var(--amber) 18%,transparent);box-shadow:none}.p.warn ha-icon,.p.warn .w{color:var(--amber)}
.p.alarm{background:color-mix(in srgb,var(--alert) 16%,transparent);box-shadow:inset 0 0 0 1.5px color-mix(in srgb,var(--alert) 55%,transparent)}.p.alarm ha-icon,.p.alarm .w{color:var(--alert);font-weight:600}.p:active{transform:scale(.97)}.un{opacity:.45}`;
    }
    render() {
      // What needs attention first: alarm → warning → working → resting (designer review 25.10); the list order otherwise.
      const rank = (e) => {
        const d = DEV[e.entity.split(".")[0]] || {};
        const tone = d.tone ? d.tone(e.s) : "";
        return tone === "alarm" ? 0 : tone === "warn" ? 1 : (d.on ? d.on(e.s) : e.s.state === "on") ? 2 : 3;
      };
      const items = (this._config.entities || []).map((e) => (typeof e === "string" ? { entity: e } : e)).map((e, i) => ({ ...e, s: st(this._hass, e.entity), i })).filter((e) => e.s).sort((a, b) => rank(a) - rank(b) || a.i - b.i);
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
        el.addEventListener("pointerdown", () => {
          held = false;
          t = setTimeout(() => {
            held = true;
            moreInfo(this, id);
          }, 500);
        });
        el.addEventListener("pointerup", () => {
          clearTimeout(t);
          if (held) return;
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
      const css = (atelier ? TABS : "") + (mode ? HEAD[mode].replace("ha-tab-group,ha-tab-group-tab,", "") + (mode === "dark" ? "ha-tab-group{--wa-color-neutral-on-quiet:#F4F2EE!important}ha-tab-group-tab{color:rgba(244,242,238,.64)!important}ha-tab-group-tab[active],ha-tab-group-tab:hover{color:#F4F2EE!important}" : "ha-tab-group{--wa-color-neutral-on-quiet:#121212!important}ha-tab-group-tab{color:rgba(18,18,18,.64)!important}ha-tab-group-tab[active],ha-tab-group-tab:hover{color:#121212!important}") : "");
      if (el.textContent !== css) el.textContent = css;
    }, 400);
  })();

  // --- «Автоматизации» (owner 28.10; docs/automations.md): the client's own rules — sunrise / sunset ± minutes, a time,
  // a phone's alarm clock — over Hauflo's covers, lights and scenes. Every rule is a real HA automation the agent writes
  // (/api/hauflo/automations); this page shows what runs next, proposes ready rules for this house, edits them.
  const AUTO = (() => {
    const W = {
      ru: {
        title: "Автоматизации", close: "Закрыть",
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
          off: (n) => `Выключить ${n}`, ramp: (n, v, m) => `${n} плавно до ${v} % за ${dur(m, "ru")}`, scene: (n) => `Сцена «${n}»`, room_off: (n) => `Весь свет выкл: ${n}`,
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
        androidHint: "Android: откройте приложение Home Assistant → Настройки → Приложение → Управление датчиками → «Следующий будильник» → включить. Больше ничего не нужно — время будильника Hauflo узнает сам.",
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
        copy: "Скопировать", copied: "Скопировано",
        willRun: (x) => `Сработает: ${x}`, willNot: "Сработает: по будильнику iPhone",
        save: "Сохранить", cancel: "Отмена", saved: "Сохранено.", del: "Удалить", delAsk: "Удалить это правило?", delYes: "Да, удалить",
        run: "Проверить сейчас", runAsk: (x) => `Сейчас выполнится: ${x}. Продолжить?`, runYes: "Выполнить", ran: "Выполнено — смотрите на шторы и свет.",
        rampNote: "Плавный свет займёт заданные минуты.",
        needLoc: "Сначала укажите, где дом, — без этого рассвет и закат не посчитать.", needAction: "Добавьте хотя бы одно действие.", needDay: "Выберите хотя бы один день.",
        err: "Не получилось: ", errNet: "Нет связи с домом — попробуйте ещё раз.", noAgent: "Нужен агент Hauflo новее — обновите его в HACS.",
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
          sub: "Пока вас нет, вечером загорается и гаснет свет, закрываются шторы — как обычно у вас, каждый вечер по-новому.",
          how: (n, j) => `Учится на двух неделях истории дома: где и когда вечером горит свет, когда закрываются шторы. Свет — только в темноте и не больше чем в ${n} комнатах сразу, время каждый раз ±${j} мин. То, что кто-то дома поменял за последние 10 минут, не трогает.`,
          sunHow: "Шторы откроются через 30 мин после рассвета и закроются на закате; вечером спокойный свет в 1–2 комнатах, где он горит чаще всего.",
          since: (d) => `Включено ${d}`, untilLine: (d) => `выключится само после ${d}`, untilNone: "пока не выключите",
          what: "Что участвует", lights: "Свет", covers: "Шторы", mode: "Как", learned: "По привычкам семьи", sun: "По солнцу",
          until: "До (включительно)", noDate: "Без даты",
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
      },
      en: {
        title: "Automations", close: "Close",
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
          off: (n) => `Turn off ${n}`, ramp: (n, v, m) => `${n} slowly up to ${v} % over ${dur(m, "en")}`, scene: (n) => `Scene «${n}»`, room_off: (n) => `All lights off: ${n}`,
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
        androidHint: "Android: open the Home Assistant app → Settings → Companion app → Manage sensors → «Next alarm» → turn it on. Nothing else — Hauflo learns the alarm time by itself.",
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
        copy: "Copy", copied: "Copied",
        willRun: (x) => `Runs: ${x}`, willNot: "Runs: with the iPhone alarm",
        save: "Save", cancel: "Cancel", saved: "Saved.", del: "Delete", delAsk: "Delete this rule?", delYes: "Yes, delete",
        run: "Test now", runAsk: (x) => `This runs now: ${x}. Go on?`, runYes: "Run", ran: "Done — look at the blinds and lights.",
        rampNote: "The slow light takes the minutes set.",
        needLoc: "Set where the house is first — sunrise and sunset need it.", needAction: "Add at least one action.", needDay: "Pick at least one day.",
        err: "Didn't work: ", errNet: "No link to the house — try again.", noAgent: "This needs a newer Hauflo agent — update it in HACS.",
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
          since: (d) => `On since ${d}`, untilLine: (d) => `switches itself off after ${d}`, untilNone: "until you switch it off",
          what: "What takes part", lights: "Lights", covers: "Blinds", mode: "How", learned: "By the family's habits", sun: "By the sun",
          until: "Until (incl.)", noDate: "No date",
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
      },
    };
    const DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
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
    const CSS3 = `:host{display:block;font-family:Onest,var(--ha-font-family-body),system-ui,sans-serif;color:var(--eh-ink,var(--primary-text-color));-webkit-tap-highlight-color:transparent}
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
.sg{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:10px}
.card{display:flex;flex-direction:column;gap:6px;padding:14px;border-radius:20px;background:var(--eh-track,rgba(127,127,127,.1));min-height:112px}
.card b{font:600 15px/1.3 Onest,sans-serif}.card .muted{flex:1}
.btn{font:500 14px Onest,sans-serif;color:inherit;border:0;border-radius:14px;min-height:44px;padding:8px 16px;background:var(--eh-track,rgba(127,127,127,.14));cursor:pointer}
.btn.pri{background:var(--eh-ink,var(--primary-color));color:var(--card-background-color,#fff)}.btn.sm{min-height:36px;padding:6px 12px;font-size:13px;border-radius:12px;align-self:flex-start}
.btn.dan{color:var(--eh-alert,#D93F3F)}.btn:disabled{opacity:.5;cursor:default}
.lnk{font:500 14px Onest,sans-serif;color:var(--eh-ink,var(--primary-color));background:none;border:0;padding:8px 0;cursor:pointer;text-decoration:underline;text-underline-offset:3px;min-height:44px}
.rl{list-style:none;margin:0;padding:0}.rl li{display:flex;align-items:center;gap:12px;padding:12px 0;border-top:1px solid var(--eh-hair,var(--divider-color,rgba(127,127,127,.2)))}
.rl li:first-child{border-top:0}.rl .ic{width:40px;height:40px;border-radius:20px;display:grid;place-items:center;background:var(--eh-track,rgba(127,127,127,.12));flex:none}
.rl .bd{flex:1;min-width:0;text-align:left;background:none;border:0;color:inherit;padding:0;cursor:pointer;font:inherit}.rl .nm{font:600 15px/1.3 Onest,sans-serif}.rl .ds{font:400 13px/1.4 Onest,sans-serif;color:var(--eh-ink2,var(--secondary-text-color));overflow-wrap:anywhere}
.rl li.dis .bd{opacity:.55}
.sw{position:relative;width:52px;height:32px;border-radius:16px;border:0;background:var(--eh-track,rgba(127,127,127,.25));cursor:pointer;flex:none}.sw::after{content:"";position:absolute;top:4px;left:4px;width:24px;height:24px;border-radius:12px;background:#fff;box-shadow:0 1px 3px rgba(0,0,0,.3);transition:left .2s}
.sw[aria-checked="true"]{background:var(--eh-mint,#2FB383)}.sw[aria-checked="true"]::after{left:24px}
.seg{display:grid;grid-auto-flow:column;grid-auto-columns:1fr;gap:4px;padding:4px;border-radius:16px;background:var(--eh-track,rgba(127,127,127,.12))}
.seg button{font:500 14px Onest,sans-serif;color:inherit;border:0;border-radius:12px;min-height:40px;padding:6px 4px;background:none;cursor:pointer}
.seg button[aria-pressed="true"]{background:var(--card-background-color,#fff);box-shadow:0 1px 3px rgba(0,0,0,.18);font-weight:600}
.fld{margin:16px 0 0}.fld>.caps{margin-bottom:8px}
.step{display:flex;align-items:center;gap:10px}.step button{width:44px;height:44px;border-radius:22px;border:0;background:var(--eh-track,rgba(127,127,127,.14));color:inherit;font:500 20px Onest,sans-serif;cursor:pointer;flex:none}
.step span{flex:1;text-align:center;font:500 15px/1.3 Onest,sans-serif}
.chips{display:flex;flex-wrap:wrap;gap:6px}.chips button{min-width:44px;min-height:40px;border-radius:20px;border:0;background:var(--eh-track,rgba(127,127,127,.12));color:inherit;font:500 14px Onest,sans-serif;cursor:pointer;padding:0 12px}
.chips button[aria-pressed="true"]{background:var(--eh-ink,var(--primary-color));color:var(--card-background-color,#fff)}
.act{padding:12px;border-radius:18px;background:var(--eh-track,rgba(127,127,127,.08));margin:0 0 8px;display:grid;gap:8px}.act .top{display:flex;align-items:center;gap:8px}.act .top b{flex:1;font:600 14px/1.3 Onest,sans-serif;min-width:0}
.x{width:36px;height:36px;border-radius:18px;border:0;background:none;color:inherit;font-size:20px;cursor:pointer;flex:none}
select,input{font:400 15px Onest,sans-serif;color:inherit;background:var(--card-background-color,#fff);border:1px solid var(--eh-hair,var(--divider-color,rgba(127,127,127,.35)));border-radius:12px;min-height:44px;padding:8px 12px;width:100%;max-width:100%}
input[type=time]{width:auto;min-width:140px}
.row{display:flex;flex-wrap:wrap;gap:8px;align-items:center}.row.end{justify-content:flex-end}.grow{flex:1}
.msg{font:500 14px/1.4 Onest,sans-serif;margin:10px 0 0}.msg.bad{color:var(--eh-alert,#D93F3F)}
.ask{margin:12px 0 0;padding:12px;border-radius:16px;background:color-mix(in srgb,var(--eh-amber,#E8901F) 14%,transparent)}
.map{position:relative;height:180px;border-radius:18px;overflow:hidden;margin:10px 0;background:var(--eh-track,rgba(127,127,127,.12))}.map img{position:absolute;width:256px;height:256px;max-width:none}
.pin{position:absolute;left:50%;top:50%;width:22px;height:22px;margin:-22px 0 0 -11px;border-radius:11px 11px 11px 0;transform:rotate(-45deg);background:var(--eh-alert,#E5484D);box-shadow:0 2px 6px rgba(0,0,0,.4)}
.attr{position:absolute;right:6px;bottom:4px;font:400 10px Onest,sans-serif;background:rgba(255,255,255,.7);color:#333;padding:1px 4px;border-radius:4px}
ol{margin:6px 0 0;padding:0 0 0 20px;font:400 14px/1.5 Onest,sans-serif}ol li{margin:4px 0}code{font:500 14px ui-monospace,monospace;background:var(--eh-track,rgba(127,127,127,.14));padding:2px 6px;border-radius:6px;overflow-wrap:anywhere}
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
.pres .chips button:disabled{opacity:.45;cursor:default}.pres input[type=date]{width:auto;min-width:170px}
.tl.pl li{grid-template-columns:auto 1fr}.tl.pl .rel{font-size:14px;font-weight:500}.tl.pl ha-icon{--mdc-icon-size:16px;vertical-align:-2px;color:var(--eh-ink2,var(--secondary-text-color))}
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
      fail(e) {
        if (e?.body?.code === "not_plain") {
          this.msg = this.w.notPlain;
          this.bad = true;
          return;
        }
        const m = e?.body?.message || e?.message || "";
        this.msg = e?.status_code === 404 && !m ? this.w.noAgent : m ? this.w.err + m : this.w.errNet;
        this.bad = true;
      }
      async load() {
        try {
          this.data = await this.api("GET");
          this.noAgent = false;
        } catch (e) {
          this.noAgent = true;
        }
        await this.loadPres();
        this.draw();
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
        let state = "";
        if (on) {
          const head = `${pr.since ? p.since(this.when(Date.parse(pr.since))) : ""} · ${pr.until ? p.untilLine(date(pr.until)) : p.untilNone}`;
          let line = "";
          if (pr.status === "no_devices") line = `<p class="msg bad">${esc(p.noDev)}</p>`;
          else if (pr.status === "no_recorder") line = `<p class="msg bad">${esc(p.noRec)}</p>`;
          else if (tn) {
            const rooms = (tn.rooms || []).map((r) => `${r.name} ${t(r.from)}–${t(r.to)}`).join(", ");
            const bl = p.blinds(t(tn.covers?.open), t(tn.covers?.close));
            const parts = [];
            if (tn.lights === "few") parts.push(`<p class="msg">${esc(p.few(tn.evenings, pr.min_evenings))}</p><button class="btn sm pri" data-pmode="sun">${esc(p.useSun)}</button>`);
            if (tn.lights === "no_location") parts.push(`<p class="msg bad">${esc(p.noLoc)}</p>`);
            const today = [tn.lights === "learned" || tn.lights === "sun" ? rooms || p.nothing : "", bl ? `${bl}${tn.covers?.by === "sun" ? ` (${p.bySun})` : ""}` : ""].filter(Boolean).join(" · ");
            line = `${today ? `<p class="tn"><b>${esc(p.today)}:</b> ${esc(today)}</p>` : ""}${parts.join("")}`;
          }
          state = `<p class="muted">${esc(head)}</p>${line}`;
        }
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
              .map((x) => `<li class="${x.skip ? "dis" : ""}"><span class="rel">${esc(when(x.t))}</span><span class="txt">${x.sys ? `<b>${esc(p.sys[x.sys] || x.sys)}</b>` : `<ha-icon icon="${ico[x.do] || "mdi:home"}"></ha-icon> ${esc(act(x))}${x.skip ? ` — ${esc(p.skip[x.skip] || x.skip)}` : ""}`}</span></li>`)
              .join("")}</ul>${(pr.log || []).length > 5 ? `<button class="lnk" data-pall>${esc(this.pAll ? p.less : p.all)}</button>` : ""}</div>`
          : "";
        const today = this.key(Date.now());
        const max = this.key(Date.now() + 90 * 86400000);
        return `<section class="box pres${on ? " on" : ""}" aria-label="${esc(p.title)}"><div class="hd"><span class="pic"><ha-icon icon="mdi:home-export-outline"></ha-icon></span><h2>${esc(p.title)}</h2><button class="sw" role="switch" aria-checked="${on}" aria-label="${esc(p.title)}" data-psw ${this.pBusy ? "disabled" : ""}></button></div>
<p class="sub">${esc(p.sub)}</p>${state}
${this.pMsg ? `<p class="msg bad" role="status">${esc(this.pMsg)}</p>` : ""}
<div class="fld"><div class="caps">${esc(p.what)}</div><div class="chips"><button aria-pressed="${pr.lights}" data-pk="lights" ${pr.devices?.lights ? "" : "disabled"}>${esc(p.lights)}${pr.devices?.lights ? ` · ${pr.devices.lights}` : ""}</button><button aria-pressed="${pr.covers}" data-pk="covers" ${pr.devices?.covers ? "" : "disabled"}>${esc(p.covers)}${pr.devices?.covers ? ` · ${pr.devices.covers}` : ""}</button></div></div>
<div class="fld"><div class="caps">${esc(p.mode)}</div><div class="seg"><button aria-pressed="${pr.mode === "learned"}" data-pmode="learned">${esc(p.learned)}</button><button aria-pressed="${pr.mode === "sun"}" data-pmode="sun">${esc(p.sun)}</button></div><p class="hint">${esc(pr.mode === "sun" ? p.sunHow : p.how(pr.max_rooms || 3, pr.jitter_min || 15))}</p></div>
<div class="fld"><div class="caps">${esc(p.until)}</div><div class="row"><input type="date" data-puntil min="${today}" max="${max}" value="${esc(pr.until || "")}" aria-label="${esc(p.until)}"><button class="btn sm" data-puclear ${pr.until ? "" : "disabled"}>${esc(p.noDate)}</button></div></div>
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
          } else order.push(a);
        }
        const list = (n) => (n.length > 3 ? `${n.slice(0, 2).join(", ")}${this.w.more(n.length - 2)}` : n.join(", "));
        return order.map((x) => (typeof x === "string" ? this.w.act[x](list(groups.get(x))) : x.rooms ? this.w.roomCovers[x.rooms](this.roomsList(rooms.get(x.rooms))) : this.actText(x))).join(", ");
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
          sr.innerHTML = `<style>${CSS3}</style><div class="pg"><h1>${esc(w.title)}</h1><p class="sub">${esc(w.noAgent)}</p></div>`;
          return done();
        }
        if (!this.data) {
          sr.innerHTML = `<style>${CSS3}</style><div class="pg"><h1>${esc(w.title)}</h1></div>`;
          return done();
        }
        const body = this.view === "edit" ? this.editHtml() : this.view === "loc" ? this.locHtml() : this.listHtml();
        sr.innerHTML = `<style>${CSS3}</style><div class="pg">${this._config?.overlay ? `<div class="row end"><button class="btn sm" data-close>${esc(w.close)}</button></div>` : ""}${body}</div>`;
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
            return `<li class="${on ? "" : "dis"}" data-open="${esc(r.id)}"><span class="rel">${esc(lead)}</span><span class="txt"><span class="abs">${esc(`${abs ? `${abs}, ` : ""}${this.whenText(r.when).replace(/^./, (c) => c.toLowerCase())}`)}:</span> ${esc(this.actsText(r))}</span></li>`;
          })
          .join("");
        const sugg = this.suggestions();
        const loc = d.location;
        return `<h1>${esc(w.title)}</h1><p class="sub">${esc(w.sub)}</p>
${!loc.set ? this.locCard() : ""}
<section class="box" aria-label="${esc(w.upcoming)}"><div class="caps">${esc(w.upcoming)}</div>
<p class="sunl"><ha-icon icon="mdi:weather-sunset"></ha-icon><span>${rise && set ? esc(w.sunLine(this.when(rise), this.when(set))) : esc(w.noSun)}</span></p>
${this.tz && this.tz !== VIEWER_TZ ? `<p class="hint">${esc(w.tzNote(this.tz))}</p>` : ""}
${this.tzAsk ? this.tzHtml() : ""}
${tl ? `<ul class="tl">${tl}</ul>` : `<p class="muted">${esc(w.nothing)}</p>`}</section>
${this.presHtml()}
${sugg.length ? `<section class="box"><div class="caps">${esc(w.sugg)}</div><div class="sg">${sugg.map((s) => `<div class="card"><b>${esc(s.title)}</b><span class="muted">${esc(s.sub)}</span><button class="btn sm pri" data-sugg="${esc(s.key)}">+ ${esc(w.addOne)}</button></div>`).join("")}</div></section>` : ""}
<section class="box"><div class="hd"><h2>${esc(w.mine)}</h2><button class="btn sm pri" data-new>+ ${esc(w.add)}</button></div>
${this.msg ? `<p class="msg ${this.bad ? "bad" : ""}" role="status">${esc(this.msg)}</p>` : ""}
${d.rules.length || this.gone ? `<ul class="rl">${(() => {
          const rows = d.rules.map((r) => this.ruleRow(r));
          if (this.gone) rows.splice(Math.min(this.gone.at, rows.length), 0, this.ruleRow(this.gone.rule).replace(/^<li class="/, '<li aria-hidden="true" class="bye '));
          return rows.join("");
        })()}</ul>` : `<p class="muted">${esc(w.empty)}</p>`}</section>
${loc.set ? `<p class="muted">${esc(w.locNow(loc.latitude.toFixed(3), loc.longitude.toFixed(3)))}${this.isAdmin() ? ` · <button class="lnk" data-loc>${esc(w.change)}</button>` : ""}</p>` : ""}`;
      }
      ruleRow(r) {
        const w = this.w;
        const s = this._hass?.states[r.entity_id];
        const on = r.state === "on";
        const t = this.nextOf(r);
        const last = s?.attributes?.last_triggered;
        const meta = r.state === "missing" ? w.missing : [r.changed ? w.changedTag : "", this.daysText(r.days), t && on ? `→ ${this.when(t)}` : "", last ? w.last(this.when(Date.parse(last))) : w.never].filter(Boolean).join(" · ");
        return `<li class="${on ? "" : "dis"}"><span class="ic"><ha-icon icon="${this.icon(r.when)}"></ha-icon></span>
<button class="bd" data-open="${esc(r.id)}"><div class="nm">${esc(r.name || this.autoName(r))}</div><div class="ds">${esc(this.actsText(r))}</div><div class="ds">${esc(meta)}</div></button>
<button class="sw" role="switch" aria-checked="${on}" aria-label="${esc(on ? w.on : w.offShort)}" data-sw="${esc(r.id)}" ${r.state === "missing" ? "disabled" : ""}></button></li>`;
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
          this.bad = false;
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
        if (ev === "sunrise" || ev === "sunset") whenBody = (this.data.location.set ? "" : `<p class="msg bad">${esc(w.needLoc)}</p>`) + offRow(ev);
        else if (ev === "time") whenBody = `<div class="fld"><input type="time" data-at value="${esc(r.when.at || "07:00")}"></div>`;
        else {
          const opts = [...(t.alarms || []).map((a) => `<option value="${esc(a.entity_id)}" ${r.when.sensor === a.entity_id ? "selected" : ""}>${esc(`Android · ${a.phone}`)}</option>`), `<option value="iphone" ${r.when.event === "alarm_iphone" ? "selected" : ""}>${esc(w.iphoneOpt)}</option>`];
          whenBody = `<div class="fld"><div class="caps">${esc(w.phone)}</div><select data-phone>${opts.join("")}</select>${(t.alarms || []).length ? "" : `<p class="hint">${esc(w.androidHint)}</p>`}</div>`;
          if (r.when.event === "alarm_android") whenBody += offRow("alarm");
          else {
            const code = r.when.code;
            whenBody += `<div class="fld"><div class="caps">${esc(w.iphoneTitle)}</div>${code ? `<ol class="steps">${w.iphoneSteps.map((s, i) => `<li><span class="sti" aria-hidden="true">${STEP_ART[i]}</span><span class="stt"><b>${i + 1}.</b> ${esc(s)}${i === 4 ? `<span class="row"><code>${esc(code)}</code><button class="btn sm" data-copy="${esc(code)}">${esc(w.copy)}</button></span>` : ""}</span></li>`).join("")}</ol><button class="btn sm" data-share="${esc(code)}">${esc(w.share)}</button>` : `<p class="hint">${esc(w.iphoneLater)}</p>`}</div>`;
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
${this.msg ? `<p class="msg ${this.bad ? "bad" : ""}" role="status">${esc(this.msg)}</p>` : ""}
${this.asking === "run" ? `<div class="ask"><p class="msg">${esc(w.runAsk(this.actsText(r)))}${r.actions.some((a) => a.do === "ramp") ? ` ${esc(w.rampNote)}` : ""}</p><div class="row"><button class="btn pri" data-runyes>${esc(w.runYes)}</button><button class="btn" data-no>${esc(w.cancel)}</button></div></div>` : ""}
${r.changed ? `<div class="ask"><p class="msg">${esc(w.changedNote)}</p>${this.asking === "restore" ? `<p class="msg">${esc(w.restoreAsk)}</p><div class="row"><button class="btn dan" data-restoreyes>${esc(w.restoreYes)}</button><button class="btn" data-no>${esc(w.cancel)}</button></div>` : `<button class="btn sm" data-restore>${esc(w.restore)}</button>`}</div>` : ""}
${this.asking === "del" ? `<div class="ask"><p class="msg">${esc(w.delAsk)}</p><div class="row"><button class="btn dan" data-delyes>${esc(w.delYes)}</button><button class="btn" data-no>${esc(w.cancel)}</button></div></div>` : ""}
<div class="row fld">${r.changed ? "" : `<button class="btn pri" data-save ${this.busy ? "disabled" : ""}>${esc(w.save)}</button>`}<button class="btn" data-back>${esc(w.cancel)}</button><span class="grow"></span>
${saved ? `<button class="btn" data-run ${this.busy ? "disabled" : ""}>${esc(w.run)}</button><button class="btn dan" data-del>${esc(w.del)}</button>` : ""}</div></section>`;
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
        return `<div class="act"><div class="top"><b>${esc(title)}</b><button class="x" data-rm="${i}" aria-label="${esc(w.remove)}">×</button></div>${body}</div>`;
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
        on("[data-psw]", (el) => this.setPres({ enabled: el.getAttribute("aria-checked") !== "true" }));
        on("[data-pk]", (el) => this.setPres({ [el.dataset.pk]: el.getAttribute("aria-pressed") !== "true" }));
        on("[data-pmode]", (el) => this.pres?.mode !== el.dataset.pmode && this.setPres({ mode: el.dataset.pmode }));
        on("[data-puclear]", () => this.setPres({ until: null }));
        on("[data-pall]", () => {
          this.pAll = !this.pAll;
          this.draw();
        });
        sr.querySelector("[data-puntil]")?.addEventListener("change", (e) => e.target.value && this.setPres({ until: e.target.value }));
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
        sr.querySelector("[data-at]")?.addEventListener("change", (e) => {
          r.when.at = e.target.value || "07:00";
          redraw();
        });
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
              this.bad = false;
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
        if (!r.actions.length) this.msg = w.needAction;
        else if (!(r.days || DAYS).length) this.msg = w.needDay;
        else if ((r.when.event === "sunrise" || r.when.event === "sunset") && !this.data.location.set) this.msg = w.needLoc;
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
              this.bad = false;
              this.draw();
              return;
            }
            this.toList({ msg: w.saved, bad: false });
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
        this.bad = false;
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
          this.bad = false;
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
          this.toList({ msg: "" });
          setTimeout(() => {
            this.gone = null;
            if (this.view === "list") this.draw();
          }, 420);
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
        view: "Вид", scenes: "Сценарии", inBlocks: "В своих блоках", onTop: "Сверху комнаты", climate: "Кондиционеры и термостаты", floor: "Тёплые полы",
        large: "Крупно", compact: "Компактно", reset: "Как настроил интегратор",
        viewHint: (d) => `Только для вас, на этом и других ваших устройствах. Интегратор настроил: ${d}.`,
        viewSaved: "Сохранено для вас.", viewError: "Не сохранилось — проверьте связь с домом.",
        names: "Названия блоков", namesHint: "Для всего дома — так увидят все жители. Пусто — как у интегратора. Названия сохраняются при следующих выкатках.",
        save: "Сохранить названия", namesSaved: "Сохранено для всего дома.", namesError: "Не сохранилось — нет связи с домом или агент Hauflo устарел (нужен 0.14.7+).",
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
        view: "View", scenes: "Scenes", inBlocks: "In their blocks", onTop: "On top of the room", climate: "ACs and thermostats", floor: "Floor heating",
        large: "Large", compact: "Compact", reset: "As the integrator set it",
        viewHint: (d) => `Just for you, on this and your other devices. The integrator set: ${d}.`,
        viewSaved: "Saved for you.", viewError: "Not saved — check the link to the house.",
        names: "Block titles", namesHint: "For the whole house — everyone living here sees them. Empty — as the integrator set it. The titles stay over the next deploys.",
        save: "Save titles", namesSaved: "Saved for the whole house.", namesError: "Not saved — no link to the house, or the Hauflo agent is out of date (0.14.7+ needed).",
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
.x{width:44px;height:44px;border-radius:22px;border:0;background:var(--eh-track,rgba(127,127,127,.12));color:inherit;font-size:22px;cursor:pointer}
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
.seg.two{grid-template-columns:1fr 1fr}.vl{font:500 13px Onest,sans-serif;margin:12px 2px 6px}
.nm{display:flex;flex-direction:column;gap:8px}.nm label{display:flex;flex-direction:column;gap:4px;font:400 12px Onest,sans-serif;color:var(--eh-ink2,var(--secondary-text-color))}
.nm input{min-height:44px;padding:0 12px;border-radius:14px;border:1px solid var(--divider-color,rgba(127,127,127,.3));background:var(--eh-track,rgba(127,127,127,.08));color:inherit;font:500 15px Onest,sans-serif;width:100%}
.nm input:focus{outline:2px solid var(--eh-ice,#3C82F0);outline-offset:1px}
.reset{margin-top:10px;min-height:40px;padding:0 14px;border:0;border-radius:14px;background:var(--eh-track,rgba(127,127,127,.12));color:inherit;font:500 13px Onest,sans-serif;cursor:pointer}
.go{display:flex;align-items:center;gap:12px;width:100%;margin:18px 0 0;padding:14px;border:0;border-radius:20px;background:var(--eh-track,rgba(127,127,127,.12));color:inherit;text-align:left;cursor:pointer;min-height:64px}
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
    const openSettings = async () => {
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
        document.removeEventListener("keydown", onKey);
      };
      const onKey = (e) => e.key === "Escape" && close();
      document.addEventListener("keydown", onKey);
      let info = null;
      let res = "";
      let busy = false;
      let defs = {};
      let viewRes = "";
      let namesRes = "";
      let typed = null;
      loadTitles(h);
      /** «Названия блоков» (owner 28.10): for the whole house, kept by the agent; the integrator's title as the placeholder. */
      const namesBlock = () => {
        const blocks = Object.entries(defs._blocks || {});
        if (!blocks.length) return "";
        const own = TITLES.data || {};
        return `<p class="vl">${esc(w.names)}</p><div class="nm">${blocks
          .map(([k, t]) => `<label><span>${esc(t)}</span><input data-name="${esc(k)}" maxlength="40" placeholder="${esc(t)}" value="${esc(typed?.[k] ?? own[k] ?? "")}" autocomplete="off"></label>`)
          .join("")}</div><p class="hint">${esc(w.namesHint)}</p><button class="reset" data-names-save>${esc(w.save)}</button>${namesRes ? `<p class="res">${esc(namesRes)}</p>` : ""}`;
      };
      /** «Вид»: scenes in blocks / on top, ACs and floor heating large / compact — what this dashboard has. */
      const viewBlock = () => {
        const opts = [
          ["scenes", w.scenes, [["blocks", w.inBlocks], ["top", w.onTop]]],
          ["climate", w.climate, [["large", w.large], ["compact", w.compact]]],
          ["floor", w.floor, [["large", w.large], ["compact", w.compact]]],
        ].filter(([k]) => defs[k] !== undefined);
        const names = namesBlock();
        if (!opts.length) return names ? `<div class="caps">${esc(w.view)}</div>${names}${Object.keys(TITLES.data || {}).length ? `<button class="reset" data-view-reset>${esc(w.reset)}</button>` : ""}${viewRes ? `<p class="res">${esc(viewRes)}</p>` : ""}` : "";
        const mine = VIEW.data || {};
        const said = opts.map(([k, label, vs]) => `${label.toLowerCase()} — ${vs.find(([v]) => v === defs[k])[1].toLowerCase()}`).join("; ");
        return `<div class="caps">${esc(w.view)}</div>${opts
          .map(([k, label, vs]) => `<p class="vl">${esc(label)}</p><div class="seg two" role="group" aria-label="${esc(label)}">${vs.map(([v, l]) => `<button data-view="${k}:${v}" aria-pressed="${(mine[k] ?? defs[k]) === v}">${esc(l)}</button>`).join("")}</div>`)
          .join("")}
<p class="hint">${esc(w.viewHint(said))}</p>${names}${Object.keys(mine).some((k) => defs[k] !== undefined) || Object.keys(TITLES.data || {}).length ? `<button class="reset" data-view-reset>${esc(w.reset)}</button>` : ""}${viewRes ? `<p class="res">${esc(viewRes)}</p>` : ""}`;
      };
      const draw = () => {
        // What's being typed survives a redraw (the season loads after the sheet opens).
        const inputs = [...sr.querySelectorAll("input[data-name]")];
        if (inputs.length) typed = Object.fromEntries(inputs.map((i) => [i.dataset.name, i.value]));
        const th = themeNow();
        const acs = info?.acs || [];
        const on = acs.filter((a) => a.state === "heat" || a.state === "cool");
        const heat = on.filter((a) => a.state === "heat").length;
        const cool = on.length - heat;
        const now = !info ? "" : !on.length ? w.allOff(info.chosen ?? info.season) : heat && cool ? w.mixed(heat, cool) : w.now(heat ? "heat" : "cool");
        const pressed = info && on.length && !(heat && cool) ? (heat ? "heat" : "cool") : info && !on.length ? (info.chosen ?? info.season) : null;
        sr.innerHTML = `<style>${CSS2}</style><div class="sh" role="dialog" aria-modal="true" aria-label="${esc(w.title)}">
<div class="hd"><h2>${esc(w.title)}</h2><button class="x" data-x aria-label="${esc(w.close)}">×</button></div>
<div class="caps">${esc(w.theme)}</div>
<div class="seg" role="group" aria-label="${esc(w.theme)}">${["light", "dark", "auto"].map((v) => `<button data-theme="${v}" aria-pressed="${th === v}">${esc(w[v])}</button>`).join("")}</div>
<p class="hint">${esc(w.themeHint)}</p>
<button class="go" data-autos><ha-icon icon="mdi:weather-sunset"></ha-icon><span><b>${esc(w.autos)}</b><small>${esc(w.autosSub)}</small></span><i aria-hidden="true">›</i></button>
${viewBlock()}
${acs.length ? `<div class="caps">${esc(w.season)}</div><p class="now" data-now>${esc(now)}</p>
<div class="big" role="group" aria-label="${esc(w.season)}">
<button class="heat" data-season="heat" aria-pressed="${pressed === "heat"}" ${busy ? "disabled" : ""}><b><ha-icon icon="hauflo:snow"></ha-icon>${esc(w.winter)}</b><span>${esc(w.winterSub)}</span></button>
<button class="cool" data-season="cool" aria-pressed="${pressed === "cool"}" ${busy ? "disabled" : ""}><b><ha-icon icon="hauflo:sun"></ha-icon>${esc(w.summer)}</b><span>${esc(w.summerSub)}</span></button></div>
<p class="hint">${esc(w.seasonHint(acs.length))}</p>${res ? `<p class="res" data-res>${esc(res)}</p>` : ""}
<details><summary>${esc(w.list)} · ${acs.length}</summary><ul>${acs.map((a) => `<li>${esc(a.name)} — ${esc(w[a.state] ?? a.state)}</li>`).join("")}</ul></details>` : ""}
</div>`;
        sr.querySelector("[data-x]").onclick = close;
        sr.querySelector("[data-autos]").onclick = () => {
          close();
          AUTO.open(hassOf());
        };
        sr.querySelectorAll("[data-view]").forEach((b) => (b.onclick = async () => {
          const [k, v] = b.dataset.view.split(":");
          const next = { ...(VIEW.data || {}) };
          if (v === defs[k]) delete next[k];
          else next[k] = v;
          try {
            await setViewPrefs(hassOf(), next);
            viewRes = w.viewSaved;
          } catch (e) {
            viewRes = w.viewError;
          }
          draw();
        }));
        sr.querySelector("[data-view-reset]")?.addEventListener("click", async () => {
          // «Как настроил интегратор»: the person's view and the house's block titles.
          try {
            await setViewPrefs(hassOf(), {});
            if (Object.keys(TITLES.data || {}).length) await setTitles(hassOf(), {});
            typed = null;
            sr.querySelectorAll("input[data-name]").forEach((i) => (i.value = ""));
            viewRes = w.viewSaved;
          } catch (e) {
            viewRes = w.viewError;
          }
          draw();
        });
        sr.querySelector("[data-names-save]")?.addEventListener("click", async () => {
          const next = {};
          sr.querySelectorAll("input[data-name]").forEach((i) => {
            const v = i.value.replace(/\s+/g, " ").trim().slice(0, 40);
            if (v) next[i.dataset.name] = v;
          });
          try {
            await setTitles(hassOf(), next);
            typed = null;
            sr.querySelectorAll("input[data-name]").forEach((i) => (i.value = TITLES.data?.[i.dataset.name] ?? ""));
            namesRes = w.namesSaved;
          } catch (e) {
            namesRes = w.namesError;
          }
          draw();
        });
        sr.querySelectorAll("[data-theme]").forEach((b) => (b.onclick = () => {
          setTheme(b.dataset.theme);
          setTimeout(draw, 50);
        }));
        sr.querySelectorAll("[data-season]").forEach((b) => (b.onclick = async () => {
          if (busy) return;
          busy = true;
          res = w.busy;
          draw();
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
          if (sheet) draw();
        }));
      };
      // A tap on the dim backdrop closes; inside the sheet (a shadow root — retargeted to the host) it doesn't.
      sheet.addEventListener("click", (e) => e.composedPath()[0] === sheet && close());
      draw();
      document.body.appendChild(sheet);
      defs = await viewDefaults(h);
      if (sheet) draw();
      info = await loadSeason(h);
      if (sheet) draw();
      sr.querySelector("[data-x]")?.focus();
    };
    const GEAR = ".hauflo-gear{display:inline-flex;align-items:center;justify-content:center;width:48px;height:48px;margin:0 2px;border:0;border-radius:24px;background:none;color:var(--app-header-text-color,var(--primary-text-color));cursor:pointer;flex:none;-webkit-tap-highlight-color:transparent}.hauflo-gear:hover{background:rgba(127,127,127,.12)}.hauflo-gear ha-icon{--mdc-icon-size:22px}";
    setInterval(() => {
      if (!/^\/hauflo-/.test(location.pathname)) return;
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
  Object.assign(EN, { "не дома": "away", "выполнить": "run", "нажать": "press", "позвонить": "call", "открыть": "open", "идёт": "playing", "на паузе": "paused", "включено": "on" });
  // The graph's own title («За 24 ч») before the fragments make it «За 24 h» (seen on the demo HA, 28.10).
  EN_RX.unshift([/^За (\d+) ч$/, "Last $1 h"]);
  const TONE = { amber: ["var(--amber)", "var(--amberGlow)"], ice: ["var(--ice)", "var(--iceGlow)"], mint: ["var(--mint)", "color-mix(in srgb,var(--mint) 50%,transparent)"], alert: ["var(--alert)", "var(--alertGlow)"] };
  const ONE = {
    light: { icon: (s) => (s.state === "on" ? "hauflo:bulb-on" : "hauflo:bulb"), tone: "amber", word: (s) => (s.state === "on" ? (num(s.attributes.brightness) !== undefined ? `${Math.round((num(s.attributes.brightness) / 255) * 100)} %` : "вкл") : "выкл"), level: (s) => (s.state === "on" ? (num(s.attributes.brightness) ?? 255) / 255 : 0), tap: ["light", "toggle"] },
    cover: { icon: () => "hauflo:blind", tone: "ice", word: (s) => (num(s.attributes.current_position) !== undefined && !["opening", "closing"].includes(s.state) ? (s.attributes.current_position ? `${s.attributes.current_position} %` : "закрыта") : ({ open: "открыта", closed: "закрыта", opening: "открывается…", closing: "закрывается…" })[s.state] || s.state), level: (s) => (num(s.attributes.current_position) ?? (s.state === "open" ? 100 : 0)) / 100, on: (s) => s.state !== "closed", tap: ["cover", "toggle"] },
    climate: { icon: () => "hauflo:thermo", tone: "ice", word: (s) => `${HVAC[s.state] || s.state}${num(s.attributes.temperature) !== undefined && s.state !== "off" ? ` · ${fmt(num(s.attributes.temperature), 1)}°` : ""}`, on: (s) => s.state !== "off", tap: null },
    media_player: { icon: () => "hauflo:speaker", tone: "amber", word: (s) => (s.state === "playing" ? s.attributes.media_title || "идёт" : s.state === "paused" ? "на паузе" : s.state === "off" ? "выкл" : s.state), on: (s) => s.state === "playing", tap: (s) => ["media_player", s.state === "off" ? "turn_on" : "media_play_pause"] },
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
    if (v) return { icon: () => v.icon, tone: "mint", word: (x) => v.word(x, nameOf(x, x.entity_id)), on: v.on, alarm: (x) => v.tone?.(x) === "alarm", warn: (x) => v.tone?.(x) === "warn", tap: v.tap };
    return { icon: () => s.attributes.icon || "hauflo:info", tone: "mint", word: (x) => `${x.state}${unitOf(x) ? ` ${unitOf(x)}` : ""}`, on: (x) => x.state === "on", tap: null };
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
.e.alarm{box-shadow:inset 0 0 0 1.5px color-mix(in srgb,var(--alert) 60%,transparent),var(--eh-deep,none)}.e.alarm .ic{background:color-mix(in srgb,var(--alert) 22%,transparent);color:var(--alert)}.e.alarm .p2{color:var(--alert);font-weight:600}
.e.warn .ic,.e.warn .p2{color:var(--amber)}.un{opacity:.55}.e .glow{background:radial-gradient(circle at 30px 50%,color-mix(in srgb,var(--accGlow) 45%,transparent),transparent 55%);opacity:0}.on .glow{opacity:.6}`;
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
      const pic = c.icon_type === "entity-picture" && s.attributes.entity_picture;
      const icon = c.icon_type === "none" ? "" : `<div class="ic${pic ? " pic" : ""}"${pic ? ` style="background-image:url('${esc(s.attributes.entity_picture)}')"` : ""}>${pic ? "" : `<ha-icon icon="${esc(c.icon || s.attributes.icon || k.icon(s))}"></ha-icon>`}</div>`;
      const level = k.level && c.show_level !== false && on ? Math.max(0, Math.min(1, k.level(s))) : 0;
      return `<div class="glass e${c.layout === "vertical" ? " v" : ""}${on ? " on" : ""}${alarm ? " alarm" : ""}${warn ? " warn" : ""}${nd(s) ? " un" : ""}" style="--acc:${acc};--accGlow:${glow}" role="button" tabindex="0" aria-label="${esc(name)}"><div class="glow"></div>${icon}<div class="tx">${p1 === "" ? "" : `<div class="p1">${esc(p1 ?? name)}</div>`}${p2 === "" ? "" : `<div class="p2">${esc(p2 ?? name)}</div>`}</div>${level ? `<i class="lvl" style="width:${Math.round(level * 100)}%"></i>` : ""}</div>`;
    }
    bind() {
      const el = this.shadowRoot.querySelector(".e");
      if (!el) return;
      const c = this._config;
      const id = c.entity;
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
      el.addEventListener("pointerdown", () => {
        held = false;
        timer = setTimeout(() => {
          held = true;
          runAction(this, c.hold_action || { action: "more-info" }, id);
        }, 500);
      });
      el.addEventListener("pointerup", () => {
        clearTimeout(timer);
        if (held) return;
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
  STUB["hauflo-area"] = (h) => ({ area: Object.keys(h?.areas || {})[0] });

  // --- HA's card picker (owner 28.10: «как сделать любой элемент в нашем стиле с любой интеграцией»): every card is in
  // «Добавить карточку» → search «Hauflo», named in the HA user's language, starts filled with this home's entities of the
  // right kind, and has a small visual editor of its main keys (the rest in YAML; keys: docs/cards.md, /help/cards).
  const uiLang = () => langOf(document.querySelector("home-assistant")?.hass);
  const PICK = {
    "hauflo-entity": [["Hauflo · Объект", "Любой объект любой интеграции: свет, розетка, вентилятор, пылесос, замок, человек…"], ["Hauflo · Entity", "Any entity of any integration: light, plug, fan, vacuum, lock, person…"]],
    "hauflo-title": [["Hauflo · Заголовок", "Заголовок раздела в стиле Hauflo"], ["Hauflo · Title", "A section heading in the Hauflo style"]],
    "hauflo-hero": [["Hauflo · Дом", "Температура дома, свет и охрана одной цифрой"], ["Hauflo · Home", "The home at a glance: temperature, lights, alarms"]],
    "hauflo-room": [["Hauflo · Комната", "Комната светится своим состоянием; тап — её страница, удержание — свет"], ["Hauflo · Room", "A room glowing with its state; tap opens its page, hold switches the lights"]],
    "hauflo-scenes": [["Hauflo · Сценарии", "Сценарии в одно касание"], ["Hauflo · Scenes", "Scenes in one tap"]],
    "hauflo-climate": [["Hauflo · Климат", "Термостат: уставка крупно, режимы, график"], ["Hauflo · Climate", "A thermostat: big setpoint, modes, chart"]],
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
      ACT("tap_action"),
      ACT("hold_action"),
      ACT("double_tap_action"),
    ],
    "hauflo-title": [TXT("title"), TXT("subtitle")],
    "hauflo-hero": [TXT("label"), E("temperature", ["climate", "sensor"], true), E("humidity", "sensor", true), E("lights", ["light", "switch"], true), E("alarms", "binary_sensor", true), E("devices", null, true), E("weather", "weather")],
    "hauflo-room": [G(TXT("name"), ICON()), E("lights", ["light", "switch"], true), E("climate", "climate"), E("covers", "cover", true), E("alarms", "binary_sensor", true), G(TXT("path"), TXT("picture"))],
    "hauflo-scenes": [G(TXT("title"), SEL("kind", [["light", "Свет", "Lights"], ["cover", "Шторы", "Blinds"]])), E("scenes", ["scene", "script"], true)],
    "hauflo-climate": [E("entity", "climate"), G(TXT("label"), BOOL("chart")), G(BOOL("compact"), E("humidity", "sensor")), G(E("heat_setpoint", "number"), E("cool_setpoint", "number"))],
    "hauflo-sliders": [E("entities", ["light", "cover", "fan", "media_player"], true)],
    "hauflo-alert": [E("alarms", "binary_sensor", true), BOOL("hide_ok")],
    "hauflo-lights": [TXT("title"), E("entities", ["light", "switch"], true)],
    "hauflo-covers": [TXT("title"), E("entities", "cover", true)],
    "hauflo-weather": [E("entity", "weather"), TXT("name"), G(NUM("days", 1, 7), SEL("forecast", [["daily", "По дням", "Daily"], ["hourly", "По часам", "Hourly"]]))],
    "hauflo-air": [TXT("name"), E("entity", "air_quality"), G(E("pm25", "sensor"), E("pm10", "sensor")), G(E("co2", "sensor"), E("voc", "sensor")), G(E("humidity", "sensor"), E("temperature", "sensor"))],
    "hauflo-media": [E("entity", "media_player"), TXT("name")],
    "hauflo-graph": [G(TXT("title"), NUM("hours", 1, 168)), E("entities", "sensor", true)],
    "hauflo-auto": [TXT("title"), E("entities", ["automation", "script"], true)],
    "hauflo-status": [TXT("title"), E("entities", ["binary_sensor", "sensor", "switch", "input_boolean"], true)],
    "hauflo-security": [TXT("name"), E("entity", "alarm_control_panel"), E("locks", "lock", true), E("openings", "binary_sensor", true)],
    "hauflo-camera": [E("entity", "camera"), G(TXT("name"), NUM("refresh", 2, 600))],
    "hauflo-acs": [G(TXT("title"), SEL("kind", [["ac", "Кондиционеры", "Air conditioners"], ["floor_heating", "Тёплые полы", "Floor heating"]])), E("entities", "climate", true)],
    "hauflo-devices": [TXT("title"), E("entities", null, true)],
    "hauflo-area": [{ name: "area", selector: { area: {} } }, TXT("title")],
  };
  const LABEL = {
    ru: { area: "Комната в Home Assistant", label: "Подпись", name: "Название", title: "Заголовок", subtitle: "Подзаголовок", icon: "Иконка", icon_type: "Что слева", color: "Цвет, когда включено", layout: "Расположение", show_level: "Полоска уровня (яркость, положение)", primary_info: "Первая строка", secondary_info: "Вторая строка", tap_action: "Нажатие", hold_action: "Удержание", double_tap_action: "Двойное нажатие", path: "Страница по тапу (путь)", picture: "Фото комнаты (URL)", temperature: "Температура (термостаты, датчики)", humidity: "Влажность", lights: "Свет", alarms: "Аварийные датчики", devices: "Устройства (нет связи)", weather: "Погода", climate: "Термостат", covers: "Шторы", kind: "Вид", scenes: "Сценарии", entity: "Объект", entities: "Объекты", days: "Дней прогноза", forecast: "Прогноз", pm25: "PM2.5", pm10: "PM10", co2: "CO₂", voc: "Летучие в-ва", hours: "Часов", locks: "Замки", openings: "Двери, окна, движение", refresh: "Обновлять кадр, с", chart: "График за сутки", hide_ok: "Прятать, когда всё в норме", compact: "Компактно (строкой)", heat_setpoint: "Уставка нагрева (number)", cool_setpoint: "Уставка охлаждения (number)", scene_layout: "Где сценарии" },
    en: { area: "Home Assistant area", label: "Label", name: "Name", title: "Title", subtitle: "Subtitle", icon: "Icon", icon_type: "On the left", color: "Colour when on", layout: "Layout", show_level: "Level bar (brightness, position)", primary_info: "First line", secondary_info: "Second line", tap_action: "Tap", hold_action: "Hold", double_tap_action: "Double tap", path: "Page on tap (path)", picture: "Room photo (URL)", temperature: "Temperature (thermostats, sensors)", humidity: "Humidity", lights: "Lights", alarms: "Alarm sensors", devices: "Devices (offline check)", weather: "Weather", climate: "Thermostat", covers: "Blinds", kind: "Kind", scenes: "Scenes", entity: "Entity", entities: "Entities", days: "Forecast days", forecast: "Forecast", pm25: "PM2.5", pm10: "PM10", co2: "CO₂", voc: "VOC", hours: "Hours", locks: "Locks", openings: "Doors, windows, motion", refresh: "Snapshot refresh, s", chart: "24 h chart", hide_ok: "Hide when all is OK", compact: "Compact (one row)", heat_setpoint: "Heating setpoint (number)", cool_setpoint: "Cooling setpoint (number)", scene_layout: "Where scenes go" },
  };
  const idOf = (x) => (typeof x === "string" ? x : x?.entity);
  const flat = (schema) => schema.flatMap((s) => (s.type === "grid" ? flat(s.schema) : [s]));
  // What a card does without the key — shown in the form, written only when changed.
  const DEFAULTS = {
    "hauflo-entity": { icon_type: "icon", layout: "horizontal", show_level: true, primary_info: "name", secondary_info: "state" },
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
          langOf(this._hass) === "ru"
            ? "Свои карточки можно добавлять — Hauflo сохранит их при следующей выкатке. Названия блоков — «Настройки → Вид» на панели."
            : "You can add your own cards — Hauflo keeps them on the next deploy. Block titles — «Settings → View» on the dashboard.";
        this.prepend(this._note);
        this._noteBase = this._note.textContent;
      }
      // A house name for this block wins over the «Заголовок» here — said, or a changed title looks ignored.
      if (this._note) {
        loadTitles(this._hass);
        const own = this._config.block && TITLES.data?.[this._config.block];
        const ru = langOf(this._hass) === "ru";
        const text = own ? `${this._noteBase} ${ru ? `Для этого блока в доме задано название «${own}» — оно показывается вместо заголовка (сбросить — там же).` : `This block has a house name «${own}» — it is shown instead of the title (reset it there).`}` : this._noteBase;
        if (this._note.textContent !== text) this._note.textContent = text;
      }
      if (!this._form) {
        this._form = document.createElement("ha-form");
        this._form.computeLabel = (s) => LABEL[langOf(this._hass)][s.name] || s.name;
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
      this._form.schema = localize(schema, langOf(this._hass));
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
