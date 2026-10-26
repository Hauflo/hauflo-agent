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
@media (prefers-reduced-motion: reduce){.mv .f,.g.gc.mv{animation:none}}`;

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
    "Дома": "Home", "Всё здание": "Whole building", "Всё спокойно": "All quiet", "Авария": "Alarm", "Аварий нет": "No alarms",
    "В комнатах": "Rooms", "Влажность": "Humidity", "Свет": "Lights", "Датчики": "Sensors", "в норме": "OK", "на улице": "outside",
    "Свет выключен": "Lights off", "Свет включён": "Lights on", "Всё выключено": "All off", "Свет: нет данных": "Lights: no data", "Всё в норме": "All OK", "всё в норме": "all OK",
    "Сценарии света": "Light scenes", "Сценарии штор": "Blind scenes", "Сценариев нет": "No scenes", "Климат": "Climate", "Тёплый пол": "Floor heating",
    "цель": "target", "нет данных из комнаты": "no room reading", "нет связи с термостатом": "thermostat offline", "проведите, чтобы изменить": "swipe to change",
    "Датчики состояния": "Status sensors", "Значения": "Values", "Режимы": "Modes", "Кнопки": "Buttons", "Вентиляция": "Ventilation", "Выключатели": "Switches",
    "Освещение": "Lighting", "Сцены": "Scenes", "Уведомления": "Notifications", "Без комнаты": "No room", "Нет связи": "Offline", "Теплее": "Warmer", "Холоднее": "Cooler", "нет связи": "offline", "Нет термостата": "No thermostat", "Держит": "Holding", "Нагревает": "Heating", "Охлаждает": "Cooling",
    "Осушает": "Drying", "Вентилирует": "Ventilating", "Выключен": "Off", "выключен": "off", "выключена": "off", "Нагрев": "Heat", "Холод": "Cool", "Охлаждение": "Cooling",
    "Авто": "Auto", "Диапазон": "Range", "Осушение": "Dry", "Вент.": "Fan", "Комфорт": "Comfort", "Эко": "Eco", "Эконом": "Economy", "Ночь": "Night", "Сон": "Sleep",
    "Буст": "Boost", "Кондиционеры": "Air conditioners", "Все выключены": "All off", "Тихо": "Quiet", "Средне": "Medium", "Сильно": "High", "Макс": "Max", "Мягко": "Diffuse", "Фокус": "Focus", "Турбо": "Turbo", "Обдув": "Swing", "Отпуск": "Away", "Нет дома": "Away", "Защита": "Protection", "Вкл": "On", "Выкл": "Off", "вкл": "on", "выкл": "off", "Вкл. все": "All on",
    "Выкл. все": "All off", "Все": "All", "Все закрыты": "All closed", "Нет данных": "No data", "нет данных": "no data", "Яркость": "Brightness", "Группы": "Groups",
    "Прочее": "Other", "Откр.": "Open", "Закр.": "Closed", "закр.": "closed", "Открыть": "Open", "Закрыть": "Close", "Стоп": "Stop", "открыта": "open", "закрыта": "closed",
    "открыт": "open", "закрыт": "closed", "открыто": "open", "закрыто": "closed", "открывается…": "opening…", "закрывается…": "closing…", "Шторы": "Blinds",
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
    "Остров": "Island", "ТВ": "TV", "Здание": "Building", "Устройства": "Devices",
  };
  // Phrases with numbers / declensions (whole text node).
  const EN_RX = [
    [/^включён: /, "on: "],
    [/^(\d+) ламп[аы]? в (\d+) комнат[аеху]*$/, (m, a, b) => `${a} light${a === "1" ? "" : "s"} in ${b} room${b === "1" ? "" : "s"}`],
    [/^(\d+) из (\d+) открыто$/, "$1 of $2 open"],
    [/^(\d+) из (\d+) работают$/, "$1 of $2 running"],
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
    [/^По одной \((\d+)\)$/, "One by one ($1)"],
    [/^Свет в (\d+) комнат[аеху]*$/, (m, a) => `Lights in ${a} room${a === "1" ? "" : "s"}`],
  ];
  // Fragments inside longer texts (between Cyrillic boundaries), longest first.
  const B = "(^|[^А-Яа-яЁё])"; // a Cyrillic word boundary (\\b doesn't know Cyrillic)
  const EN_FRAG = [
    [/^Позвонить · /, "Call · "], [new RegExp(`${B}ещё аварий:`, "g"), "$1more alarms:"], [new RegExp(`${B}и ещё (\\d+)`, "g"), "$1and $2 more"], [new RegExp(`${B}за сутки`, "g"), "$124 h"],
    [new RegExp(`${B}В комнате `, "g"), "$1Now "], [new RegExp(`${B}Сейчас `, "g"), "$1Now "], [new RegExp(`${B}влажность `, "g"), "$1humidity "],
    [new RegExp(`${B}нагрев(?=$|[^А-Яа-яЁё])`, "g"), "$1heating"], [new RegExp(`${B}Охлаждение до `, "g"), "$1Cooling to "], [new RegExp(`${B}Нагрев до `, "g"), "$1Heating to "],
    [new RegExp(`${B}Сдвиньте — `, "g"), "$1Slide — "], [new RegExp(`${B}сегодня в `, "g"), "$1today at "], [new RegExp(`${B}запускался `, "g"), "$1ran "],
    [new RegExp(`${B}сработала `, "g"), "$1triggered "], [new RegExp(`${B}батарея `, "g"), "$1battery "], [new RegExp(`${B}откр\\. `, "g"), "$1open "],
    [/^Авария · /, "Alarm · "], [/^Свет · /, "Lights · "], [new RegExp(`${B}обновлено `, "g"), "$1updated "],
    [/(\d) мин назад/g, "$1 min ago"], [/(\d) мин(?=$|[^А-Яа-яЁё])/g, "$1 min"], [/(\d) ч(?=$|[^А-Яа-яЁё])/g, "$1 h"],
    [new RegExp(`${B}с (?=\\d)`, "g"), "$1since "], [new RegExp(`${B}в (?=\\d)`, "g"), "$1at "], [new RegExp(`${B}до (?=\\d)`, "g"), "$1to "], [new RegExp(`${B}за (?=\\d)`, "g"), "$1in "],
    [new RegExp(`${B}открыто: `, "g"), "$1open: "], [new RegExp(`${B}не заперто: `, "g"), "$1unlocked: "], [new RegExp(`${B}внимание: `, "g"), "$1attention: "],
    [/ · вкл$/, " · on"], [/ · выкл$/, " · off"], [/^Яркость · /, "Brightness · "], [/^Нагрев · /, "Heat · "], [/^Охлаждение · /, "Cooling · "],
    [new RegExp(`${B}мин (?=-?\\d)`, "g"), "$1min "], [new RegExp(`${B}макс (?=-?\\d)`, "g"), "$1max "], [/^За сутки$/, "Last 24 h"], [/^За (\d+) ч$/, "Last $1 h"],
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
    const walk = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, { acceptNode: (n) => (n.parentNode?.nodeName === "STYLE" ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT) });
    for (let n = walk.nextNode(); n; n = walk.nextNode()) {
      const v = tr(n.nodeValue);
      if (v !== n.nodeValue) n.nodeValue = v;
    }
    root.querySelectorAll("[aria-label],[title]").forEach((el) => {
      for (const a of ["aria-label", "title"]) if (el.hasAttribute(a)) el.setAttribute(a, tr(el.getAttribute(a)));
    });
  };
  const nameOf = (s, id) => (s && s.attributes.friendly_name) || id;
  // Device codes («DKP1-1», «B1-2») keep a non-breaking hyphen: «Протечка (DKP1-» / «1)» on two lines read as
  // nonsense (integrator run 15). Only upper-case codes — icon names and entity ids are lower case.
  const esc = (t) => String(t ?? "").replace(/\b([A-Z]{1,6}\d*)-(\d+)\b/g, "$1\u2011$2").replace(/[&<>"'()]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;", "(": "&#40;", ")": "&#41;" })[c]);
  const isOn = (s) => !!s && ["on", "open", "opening", "heat", "cool", "heat_cool", "auto", "dry", "fan_only"].includes(s.state);
  const avg = (xs) => {
    const v = xs.filter((x) => x !== undefined);
    return v.length ? v.reduce((a, b) => a + b, 0) / v.length : undefined;
  };
  const tempOf = (s) => (s ? (s.entity_id.startsWith("climate.") ? num(s.attributes.current_temperature) : num(s.state)) : undefined);
  const brightnessPct = (s) => (s && s.state === "on" ? (num(s.attributes.brightness) !== undefined ? Math.round((num(s.attributes.brightness) / 255) * 100) : 100) : 0);
  const call = (hass, domain, service, data) => hass.callService(domain, service, data);
  const navigate = (path) => {
    history.pushState(null, "", path);
    window.dispatchEvent(new CustomEvent("location-changed", { detail: { replace: false } }));
  };
  const haptic = (el, type = "light") => el.dispatchEvent(new CustomEvent("haptic", { detail: type, bubbles: true, composed: true }));
  const moreInfo = (el, entityId) => el.dispatchEvent(new CustomEvent("hass-more-info", { detail: { entityId }, bubbles: true, composed: true }));
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

  class EHCard extends HTMLElement {
    constructor() {
      super();
      this.attachShadow({ mode: "open" });
      this._sig = "";
      // Parts a card fills in later (history, a list it loads) are put into English too.
      new MutationObserver((ms) => {
        if (LANG === "ru") return;
        for (const m of ms) {
          if (m.type === "characterData") {
            const v = tr(m.target.nodeValue);
            if (v !== m.target.nodeValue) m.target.nodeValue = v;
          } else m.addedNodes.forEach((n) => (n.nodeType === 3 ? (n.nodeValue = tr(n.nodeValue)) : n.nodeType === 1 && n.nodeName !== "STYLE" && translate(n)));
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
      this._sig = "r";
      this._refs = refs;
      this._dark = dark;
      this.lang = LANG; // hyphenation in the card's language («Полотенце-сушитель»)
      if (this._config.panel) this.style.cssText = "display:block;max-width:980px;margin:24px auto;padding:0 16px";
      // Live, not static (designer review 25.10): a value that changed eases in, a room that lit up glows in.
      const SEL = ".t,.cct,.hd b,.s,.lv,.w,.vals b,.v,.cond,.st0";
      const before = [...this.shadowRoot.querySelectorAll(SEL)].map((e) => e.textContent);
      const kind = this.shadowRoot.querySelector(".glass")?.className;
      const first = !this.shadowRoot.querySelector(".glass");
      this.shadowRoot.innerHTML = `<style>${CSS}${this.css ? this.css() : ""}</style>${this.render()}`;
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
      const critical = openList.some((a) => a.critical !== false);
      // A siren sounding or the security alarm triggered is no «Всё спокойно» (persona round 3: one source of truth).
      const loud = Object.values(h.states).filter((s) => (s.entity_id.startsWith("siren.") && s.state === "on") || (s.entity_id.startsWith("alarm_control_panel.") && s.state === "triggered"));
      const ok = !alarms.length && !loud.length;
      // A thermostat / AC with no setpoint heard, a blind HA lost: no «Всё спокойно» over them (persona round 5).
      const offline = (c.devices || []).filter((id) => {
        const s = st(h, id);
        if (!s || s.state === "unavailable") return true;
        return id.startsWith("climate.") && (s.state === "unknown" || num(s.attributes.temperature) === undefined);
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
      const headline = ok && offline.length
        ? `<div class="hl"><span class="dot" style="background:var(--amber);box-shadow:0 0 12px var(--amberGlow)"></span>Нет связи · ${offline.length} ${devWord(offline.length)}</div>`
        : ok && lightsDeaf
        ? // Lights without data: no «Всё спокойно» over what isn't known (narrow rounds 1–2, D9).
          `<div class="hl"><span class="dot" style="background:var(--ink2)"></span>Нет данных · ${lightsUnknown ? "свет" : `${lightsDeaf} ${lampsWord(lightsDeaf)}`}</div>`
        : ok
        ? `<div class="hl">Всё спокойно</div>`
        : !alarms.length
        ? `<div class="hl bad"><span class="dot" style="background:var(--alert);box-shadow:0 0 12px var(--alert)"></span>${loud.some((s) => s.entity_id.startsWith("alarm_control_panel.")) ? "Тревога · охрана" : "Звучит сирена"}</div>`
        : `<div class="hl bad"${critical ? "" : ' style="color:var(--amber)"'}><span class="dot" style="background:${critical ? "var(--alert)" : "var(--amber)"};box-shadow:0 0 12px ${critical ? "var(--alert)" : "var(--amber)"}"></span>${esc(alarms.length === 1 ? `Авария${c.rooms?.[alarms[0]] ? ` · ${c.rooms[alarms[0]]}` : ""}` : `${alarms.length} ${pluralAlarms(alarms.length)}`)}</div>`;
      return `<div class="h"><div class="top"><span class="caps">${esc(c.label || "Дома")}</span>${outside}</div>
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
  };

  // --- hauflo-room: a room that glows with its state; tap = its page, hold = its light -----------
  class Room extends EHCard {
    watched() {
      const c = this._config;
      return [...(c.lights || []), c.climate, ...(c.alarms || []), ...(c.covers || [])].filter(Boolean);
    }
    css() {
      return `.r{height:172px;padding:16px;display:flex;flex-direction:column;justify-content:space-between;cursor:pointer;user-select:none;-webkit-user-select:none}
.r>*:not(.glow):not(.ph){position:relative}.top{display:flex;justify-content:space-between;align-items:flex-start;color:var(--ink2)}
.mid{display:flex;align-items:center;justify-content:space-between}
.ri{width:46px;height:46px;border-radius:16px;display:grid;place-items:center;background:var(--track);color:var(--ink2)}.ri ha-icon{--mdc-icon-size:24px}
.lit .ri{background:color-mix(in srgb,var(--amber) 22%,transparent);color:var(--amber)}.cold .ri{background:color-mix(in srgb,var(--ice) 22%,transparent);color:var(--ice)}
.alarm .ri{background:color-mix(in srgb,var(--alert) 18%,transparent);color:var(--alert)}
.t{font-size:40px;line-height:1}.t small{font:300 15px Onest,sans-serif;color:var(--ink2);letter-spacing:0;margin-left:2px}
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
      if (alarm) return { kind: openList.some((a) => a.critical !== false) ? "alarm" : "alarm warn", text: c.rooms?.[alarm.entity_id] ? `${c.rooms[alarm.entity_id]}${alarmsOn.length > 1 ? ` и ещё ${alarmsOn.length - 1}` : ""}` : alarmsOn.length > 1 ? `${alarmsOn.length} ${pluralAlarms(alarmsOn.length)}` : "" };
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
<div class="mid"><span class="ri"><ha-icon icon="${esc(c.icon || "hauflo:home")}"></ha-icon></span><div class="t thin">${t === undefined ? "" : `${Math.round(t)}<small>°</small>`}</div></div>
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
      return (this._config.scenes || []).map((s) => s.entity);
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
.light{--acc:var(--amber);--accGlow:var(--amberGlow)}.cover{--acc:var(--ice);--accGlow:var(--iceGlow)}`;
    }
    render() {
      const c = this._config;
      const list = (c.scenes || []).map((x) => ({ ...x, st: st(this._hass, x.entity) })).filter((x) => x.st);
      const kind = c.kind === "cover" ? "cover" : "light";
      const icon = kind === "cover" ? "hauflo:blind" : "hauflo:scene";
      const label = (x) => x.name || nameOf(x.st, x.entity);
      if (!list.length) return `<div class="glass m ${kind}"><span class="caps">Сценариев нет</span></div>`;
      return `<div class="glass m ${kind}"><div class="hd"><span class="ic"><ha-icon icon="${icon}"></ha-icon></span><span class="caps">${esc(c.title || (kind === "cover" ? "Сценарии штор" : "Сценарии света"))}</span></div>
<div class="row">${list.map((x) => `<button data-e="${esc(x.entity)}" title="${esc(label(x))}"><ha-icon icon="${esc(x.icon || (kind === "cover" ? "hauflo:blind" : "hauflo:scene"))}"></ha-icon><span>${esc(label(x))}</span></button>`).join("")}</div></div>`;
    }
    bind() {
      this.shadowRoot.querySelectorAll("button[data-e]").forEach((b) =>
        b.addEventListener("click", () => {
          const id = b.dataset.e;
          haptic(this);
          this._busy = true;
          b.classList.add("go");
          // A tick where its icon was: the tap took (persona round 5: «нажал — ничего не подсветилось»).
          const ic = b.querySelector("ha-icon");
          const was = ic?.getAttribute("icon");
          ic?.setAttribute("icon", "mdi:check");
          setTimeout(() => {
            b.classList.remove("go");
            if (was) ic?.setAttribute("icon", was);
            this._busy = false;
          }, 1600);
          call(this._hass, id.split(".")[0] === "script" ? "script" : "scene", "turn_on", { entity_id: id });
        }),
      );
    }
    getGridOptions() {
      return { columns: 12, rows: "auto" };
    }
  }

  // --- hauflo-climate: the setpoint as the one big number ----------------------------------------
  class Climate extends EHCard {
    watched() {
      return [this._config.entity, ...(this._config.extra || []), ...(this._config.sensors || []), ...(this._config.zones || []).map((z) => z.entity)].filter(Boolean);
    }
    set hass(hass) {
      this._hass = hass;
      this._loadHist();
      this._update();
    }
    /** The room's temperature over a day, faint behind the setpoint (owner 2026-10-25: «чтобы клиент видел,
     * что за 24 часа температура в норме»). From `history` (a sensor) or the thermostat's own reading. */
    async _loadHist() {
      const c = this._config;
      if (!c || c.compact || c.chart === false || this._histAt > Date.now() - 5 * 60000) return;
      this._histAt = Date.now();
      this._hist = c.history ? await series(this._hass, c.history, 24) : await series(this._hass, c.entity, 24, "current_temperature");
      this._sig = "";
      this._update();
    }
    css() {
      return `.c{padding:20px 22px}.row{display:flex;justify-content:space-between;align-items:flex-end}
.t{font-size:76px;line-height:.9}.t sup{font:300 20px Onest,sans-serif;vertical-align:top;position:relative;top:8px;color:var(--ink2);margin-left:2px}
.sub{font:400 13px Onest,sans-serif;color:var(--ink2);margin-top:8px}.st{display:flex;flex-direction:column;gap:8px}
.none{font:300 20px/1.2 Onest,sans-serif;color:var(--ink2);margin:6px 0}
.st button{width:48px;height:48px;border-radius:24px;display:grid;place-items:center;box-shadow:var(--eh-edge,inset 0 0 0 1px var(--hair));background:var(--eh-glass,transparent);font:300 26px Onest,sans-serif}
.st button:active{transform:scale(.94)}.st button:disabled{opacity:.35;pointer-events:none}
.top{display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;min-height:22px}
.chart{position:relative;margin-top:12px;height:44px}.chart+.scale{margin-top:8px}.chart span{position:absolute;left:0;top:-2px;font:400 13px Onest,sans-serif;color:var(--ink2)}
.spark{display:block;width:100%;height:44px;color:var(--ink3);opacity:.55;-webkit-mask-image:linear-gradient(90deg,transparent,#000 18%);mask-image:linear-gradient(90deg,transparent,#000 18%);pointer-events:none}
.nos{font:400 13px Onest,sans-serif;color:var(--ink2);margin-top:4px}.goal{font:500 11px Onest,sans-serif;letter-spacing:.12em;text-transform:uppercase;color:var(--ink3);margin-left:8px;vertical-align:top;position:relative;top:14px}
.heat .spark{color:var(--amber)}.cool .spark{color:var(--ice)}.day{font:400 13px Onest,sans-serif;color:var(--ink2);margin-top:3px}
.act{display:inline-flex;align-items:center;gap:7px;font:500 12px Onest,sans-serif;color:var(--ink2);padding:4px 10px;border-radius:11px;background:var(--track)}
.act i{width:6px;height:6px;border-radius:3px;background:var(--ink3)}.heat .act i{background:var(--amber);box-shadow:0 0 8px var(--amber)}.cool .act i{background:var(--ice);box-shadow:0 0 8px var(--ice)}
.scale{margin-top:18px;touch-action:pan-y;cursor:ew-resize;user-select:none;-webkit-user-select:none;padding:6px 0}
.ends{display:flex;justify-content:space-between;font:400 11px Onest,sans-serif;color:var(--ink3);margin-top:6px}
.ticks{display:flex;justify-content:space-between;align-items:flex-end;height:34px}
.ticks i{width:2px;border-radius:1px;background:var(--ink3);height:10px;position:relative}.ticks i.m{height:16px}.ticks i.a{height:34px;width:3px;background:var(--amber);box-shadow:0 0 10px var(--amberGlow)}
.ticks i.cur::after{content:"";position:absolute;left:50%;bottom:-9px;width:5px;height:5px;border-radius:3px;transform:translateX(-50%);background:var(--ink2)}
.dragging .t .v{color:var(--amber)}.cool.dragging .t .v{color:var(--ice)}
.cool .ticks i.a{background:var(--ice);box-shadow:0 0 8px var(--iceGlow)}
.modes{display:flex;gap:6px;margin-top:16px}.modes.fan{margin-top:8px;align-items:center}.modes.fan button{white-space:nowrap;padding:0 4px;font-size:12px;min-width:0}.modes .fl{flex:0 0 auto;color:var(--ink3);--mdc-icon-size:18px;padding:0 2px}.modes button{flex:1;min-height:44px;padding:0 6px;border-radius:14px;font:500 13px Onest,sans-serif;color:var(--ink2);box-shadow:inset 0 0 0 1px var(--hair)}
.modes .on{color:var(--ink);background:var(--track);box-shadow:none}
.heat .glow{background:radial-gradient(50% 50% at 20% 10%,var(--amberGlow),transparent 70%);opacity:.6}
.cc{padding:14px 14px 14px 18px}.ccrow{display:flex;align-items:center;gap:12px;position:relative}.ccn{flex:1;min-width:0;display:flex;flex-direction:column;gap:4px}
.ccn .caps{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.ccn .sub{margin:0}
.cct{font-size:40px;line-height:1}.cct small{font:300 15px Onest,sans-serif;color:var(--ink2);margin-left:1px}
.sens{display:flex;gap:6px;margin-top:2px}.sens b{font:500 12px Onest,sans-serif;padding:3px 7px;border-radius:8px;background:var(--track);color:var(--ink2)}
.st.row2{flex-direction:row}.st.row2 button{width:44px;height:44px;border-radius:22px;font-size:22px}
.cool .glow{background:radial-gradient(50% 50% at 20% 10%,var(--iceGlow),transparent 70%);opacity:.6}
.zs .caps{display:block;margin-bottom:4px}.zr{display:flex;align-items:center;gap:12px;padding:8px 0;border-top:1px solid var(--hair)}.zr:nth-child(2){border-top:0}
.zn{font:500 15px Onest,sans-serif;color:var(--ink);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.zs .cct{font-size:32px}.zr.heat .zn::after{content:"";display:inline-block;width:6px;height:6px;border-radius:3px;background:var(--amber);margin-left:7px;vertical-align:middle}`;
    }
    render() {
      const s = st(this._hass, this._config.entity);
      if (!s) return `<div class="glass c"><div class="caps">Нет термостата</div></div>`;
      // No link and nothing known: one honest line, not an empty dial (persona round 3).
      if (!this._config.zones?.length && !this._config.compact && ["unavailable", "unknown"].includes(s.state) && num(s.attributes.temperature) === undefined && num(s.attributes.current_temperature) === undefined)
        return `<div class="glass cc"><div class="ccrow"><div class="ccn"><span class="caps">${esc(this._config.label || "Климат")}</span><span class="sub">нет связи с термостатом</span></div></div></div>`;
      if (this._config.zones?.length) return this.zones();
      if (this._config.compact) return this.compact(s);
      const a = s.attributes;
      const target = num(a.temperature);
      // Kept between two values (heat_cool): the range is the setpoint, not «не пришла» (persona review 25.10).
      const lo = num(a.target_temp_low);
      const hi = num(a.target_temp_high);
      const range = target === undefined && lo !== undefined && hi !== undefined;
      const cur = num(a.current_temperature);
      const hum = num(a.current_humidity) ?? num(st(this._hass, this._config.humidity)?.state);
      const step = Math.max(num(a.target_temp_step) || 0.5, 0.5);
      const min = num(a.min_temp) ?? 5;
      const max = num(a.max_temp) ?? 35;
      const action = a.hvac_action;
      const known = target !== undefined || cur !== undefined;
      const mood = !known ? "" : action === "cooling" || s.state === "cool" ? "cool" : action === "heating" || s.state === "heat" ? "heat" : "";
      // «Нагревает» when it's warmer than the goal isn't believable (persona review 25.10): said only when it fits.
      const fits = (act) => cur === undefined || target === undefined || (act === "heating" ? cur < target + 0.5 : act === "cooling" ? cur > target - 0.5 : true);
      const doing = !known ? "" : action === "heating" && fits("heating") ? "Нагревает" : action === "cooling" && fits("cooling") ? "Охлаждает" : action === "drying" ? "Осушает" : action === "fan" ? "Вентилирует" : action === "idle" ? "Держит" : s.state === "off" ? "Выключен" : "";
      const n = 31;
      const at = (v) => Math.round(((v - min) / (max - min || 1)) * (n - 1));
      const pos = target === undefined ? -1 : at(target);
      const cpos = cur === undefined ? -1 : at(Math.min(max, Math.max(min, cur)));
      const ticks = Array.from({ length: n }, (_, i) => `<i class="${i === pos ? "a" : i % 5 === 0 ? "m" : ""}${i === cpos ? " cur" : ""}"></i>`).join("");
      const modes = (a.hvac_modes || []).filter((m) => HVAC[m]);
      const presets = (a.preset_modes || []).filter((p) => p !== "none");
      const useModes = modes.length > 1;
      // An AC's fan speed and swing (persona round 3, Dubai: «no fan speed or swing»): shown when the device has them.
      const fans = (a.fan_modes || []).filter((f) => f !== "off");
      const swing = (a.swing_modes || []).includes("off") ? (a.swing_modes.find((m) => m !== "off") ?? null) : null;
      // A mode lit only when the thermostat has said something: HA's default «heat» isn't a fact (integrator run 25.10).
      const opts = useModes ? modes.map((m) => [m, HVAC[m], known && s.state === m]) : presets.map((p) => [p, PRESET[p] || p, known && a.preset_mode === p]);
      const digits = step < 1 && target !== undefined && target % 1 ? 1 : 0;
      const sp = this._hist ? spark(this._hist, 600, 44, 6, 3, 6, 6) : null;
      const chart = sp ? `<svg class="spark" viewBox="0 0 600 44" preserveAspectRatio="none"><defs><linearGradient id="cf" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="currentColor" stop-opacity=".28"/><stop offset="1" stop-color="currentColor" stop-opacity="0"/></linearGradient></defs><path d="${sp.area}" fill="url(#cf)"/>${target !== undefined ? `<line x1="0" x2="600" y1="${sp.yOf(target).toFixed(1)}" y2="${sp.yOf(target).toFixed(1)}" stroke="currentColor" stroke-width="1" stroke-dasharray="4 5" vector-effect="non-scaling-stroke" opacity=".7"/>` : ""}<path d="${sp.line}" fill="none" stroke="currentColor" stroke-width="2" vector-effect="non-scaling-stroke" stroke-linejoin="round"/></svg>` : "";
      return `<div class="glass c ${mood}"><div class="glow"></div><div style="position:relative">
<div class="top"><span class="caps">${esc(this._config.label || "Климат")}</span>${doing ? `<span class="act"><i></i>${doing}</span>` : ""}</div>
<div class="row"><div>${range ? `<div class="t thin"><span class="v">${fmt(lo)}–${fmt(hi)}</span><sup>°C</sup></div>` : target === undefined ? `<div class="t thin"><span class="v">—</span></div><div class="nos">нет связи с термостатом</div>` : `<div class="t thin"><span class="v">${fmt(target, digits)}</span><sup>°C</sup><span class="goal">цель</span></div>`}
<div class="sub">${cur === undefined ? "нет данных из комнаты" : `В комнате ${fmt(cur, 1)}°`}${hum !== undefined ? ` · влажность ${fmt(hum)} %` : ""}${sp ? ` · ${sp.hours >= 22 ? "за сутки" : `за ${Math.round(sp.hours)} ч`} ${fmt(sp.lo, 1)}–${fmt(sp.hi, 1)}°` : ""}</div></div>
<div class="st"><button data-d="${step}" aria-label="Теплее"${target === undefined ? " disabled" : ""}>+</button><button data-d="${-step}" aria-label="Холоднее"${target === undefined ? " disabled" : ""}>−</button></div></div>
${chart ? `<div class="chart">${chart}</div>` : ""}
<div class="scale" data-min="${min}" data-max="${max}" data-step="${step}"><div class="ticks">${ticks}</div><div class="ends"><span>${fmt(min)}°</span><span>проведите, чтобы изменить</span><span>${fmt(max)}°</span></div></div>
${opts.length ? `<div class="modes">${opts.slice(0, 5).map(([k, l, on]) => `<button class="${on ? "on" : ""}" data-${useModes ? "m" : "p"}="${esc(k)}">${esc(l)}</button>`).join("")}</div>` : ""}
${fans.length > 1 || swing ? `<div class="modes fan"><span class="fl"><ha-icon icon="hauflo:fan"></ha-icon></span>${fans.slice(0, 5).map((f) => `<button class="${known && a.fan_mode === f ? "on" : ""}" data-f="${esc(f)}">${esc(FAN[f] || f)}</button>`).join("")}${swing ? `<button class="${known && a.swing_mode && a.swing_mode !== "off" ? "on" : ""}" data-sw="${esc(a.swing_mode && a.swing_mode !== "off" ? "off" : swing)}">Обдув</button>` : ""}</div>` : ""}</div></div>`;
    }
    /** Floor-heating zones of one room in one card: a line each — name, now, setpoint, ± (persona review 25.10). */
    zones() {
      const rows = this._config.zones.map((z) => {
        const s = st(this._hass, z.entity);
        const a = s?.attributes || {};
        const target = num(a.temperature);
        const cur = num(a.current_temperature);
        const step = Math.max(num(a.target_temp_step) || 0.5, 0.5);
        const heating = a.hvac_action === "heating" && (cur === undefined || target === undefined || cur < target + 0.5);
        return `<div class="zr ${heating ? "heat" : ""}"><div class="ccn"><span class="zn">${esc(z.name || nameOf(s, z.entity))}</span><span class="sub">${!s || ["unavailable", "unknown"].includes(s.state) ? "нет связи" : cur === undefined ? "нет данных" : `Сейчас ${fmt(cur, 1)}°`}${heating ? " · нагрев" : ""}</span></div>
<div class="cct thin">${target === undefined ? "—" : `${fmt(target, step < 1 && target % 1 ? 1 : 0)}<small>°</small>`}</div>
<div class="st row2"><button data-e="${esc(z.entity)}" data-d="${-step}" aria-label="Холоднее"${target === undefined ? " disabled" : ""}>−</button><button data-e="${esc(z.entity)}" data-d="${step}" aria-label="Теплее"${target === undefined ? " disabled" : ""}>+</button></div></div>`;
      });
      const any = this._config.zones.some((z) => st(this._hass, z.entity)?.attributes?.hvac_action === "heating");
      return `<div class="glass cc zs ${any ? "heat" : ""}"><div class="glow"></div><div style="position:relative"><div class="caps">${esc(this._config.label || "Тёплый пол")}</div>${rows.join("")}</div></div>`;
    }
    /** One line for a second thermostat (zones of floor heating): name, setpoint, ±. */
    compact(s) {
      const a = s.attributes;
      const target = num(a.temperature);
      const cur = num(a.current_temperature);
      const step = Math.max(num(a.target_temp_step) || 0.5, 0.5);
      const heating = a.hvac_action === "heating" && (num(a.current_temperature) === undefined || num(a.temperature) === undefined || num(a.current_temperature) < num(a.temperature) + 0.5);
      // A floor with several sensors: each one's reading (owner 2026-10-25).
      const sensors = (this._config.sensors || []).map((id) => tempOf(st(this._hass, id))).filter((v) => v !== undefined);
      return `<div class="glass cc ${heating ? "heat" : ""}"><div class="glow"></div><div class="ccrow">
<div class="ccn"><span class="caps">${esc(this._config.label || nameOf(s, this._config.entity))}</span><span class="sub">${cur === undefined ? "" : `Сейчас ${fmt(cur, 1)}°`}${heating ? " · нагрев" : ""}</span>${sensors.length > 1 ? `<span class="sens">${sensors.map((v) => `<b>${fmt(v, 1)}°</b>`).join("")}</span>` : ""}</div>
<div class="cct thin">${target === undefined ? "—" : `${fmt(target, step < 1 && target % 1 ? 1 : 0)}<small>°</small>`}</div>
<div class="st row2"><button data-d="${-step}" aria-label="Холоднее"${target === undefined ? " disabled" : ""}>−</button><button data-d="${step}" aria-label="Теплее"${target === undefined ? " disabled" : ""}>+</button></div></div></div>`;
    }
    bind() {
      const id = this._config.entity;
      this.shadowRoot.querySelectorAll("button[data-d]").forEach((b) =>
        b.addEventListener("click", () => {
          const eid = b.dataset.e || id;
          const s = st(this._hass, eid);
          if (!s) return;
          const a = s.attributes;
          const base = num(a.temperature) ?? Math.round(num(a.current_temperature) ?? 21);
          const v = Math.min(num(a.max_temp) ?? 35, Math.max(num(a.min_temp) ?? 5, base + Number(b.dataset.d)));
          haptic(this);
          call(this._hass, "climate", "set_temperature", { entity_id: eid, temperature: v });
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
        sc.addEventListener("pointerdown", (ev) => {
          x0 = ev.clientX;
          y0 = ev.clientY;
          val = undefined;
          sliding = false;
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
            ticks.forEach((t, i) => t.classList.toggle("a", i === k));
          }
        });
        const end = (ev) => {
          if (x0 === null) return;
          x0 = null;
          box.classList.remove("dragging");
          if (sliding && val !== undefined && ev.type === "pointerup") call(this._hass, "climate", "set_temperature", { entity_id: id, temperature: val });
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
      this.shadowRoot.querySelector(".caps")?.addEventListener("click", () => moreInfo(this, id));
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
.ud3{display:flex;gap:6px;margin-top:10px}.ud3 button{flex:1;height:44px;border-radius:13px;display:grid;place-items:center;color:var(--ink);box-shadow:inset 0 0 0 1px var(--hair);background:var(--eh-glass,transparent)}.ud3 button ha-icon{--mdc-icon-size:20px}`;
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
        const pos = num(s.attributes.current_position);
        const settable = ((num(s.attributes.supported_features) || 0) & 4) !== 0;
        const known = !["unknown", "unavailable"].includes(s.state);
        return { dom: "cover", name, pct: pos ?? (s.state === "open" ? 100 : 0), on: known && s.state !== "closed", known, dimmable: settable && pos !== undefined, icon: e.icon || "hauflo:blind", s };
      }
      return { dom: "switch", name, pct: s.state === "on" ? 100 : 0, on: s.state === "on", dimmable: false, icon: e.icon || "hauflo:power", s };
    }
    render() {
      const items = this.items().map((e) => ({ e, m: this.model(e) }));
      const cols = Math.min(4, Math.max(1, items.length));
      return `<div class="wrap" style="--cols:${cols}">${items
        .map(({ e, m }) => {
          const unavailable = m.s.state === "unavailable";
          const label = m.dom === "cover" ? (!m.known ? "—" : m.dimmable ? `${m.pct}` : m.on ? "Откр." : "Закр.") : m.on ? (m.dimmable ? `${m.pct}` : "Вкл") : "Выкл";
          return `<div class="glass v ${m.dom} ${m.on ? "" : "off"} ${unavailable ? "unavail" : ""}" data-e="${esc(e.entity)}" role="slider" aria-label="${esc(m.name)}" aria-valuenow="${m.pct}" tabindex="0">
<div class="lvl" style="height:${m.on || m.dom === "cover" ? m.pct : 0}%"></div><ha-icon class="ic" icon="${esc(m.icon)}"></ha-icon>
<div><div class="p thin">${esc(label)}</div><div class="n">${esc(m.name)}</div>
${m.dom === "cover" && !m.dimmable ? `<div class="ud3">${coverButtons(coverDir({ ...e, s: m.s }, this._config), "data-c")}</div>` : ""}</div></div>`;
        })
        .join("")}</div>`;
    }
    bind() {
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
          const m = this.model({ entity: id });
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
          const m = this.model({ entity: id });
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
      const normal = AGENT.data && top.critical === false;
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
        try {
          await this._hass.callApi("POST", "hauflo/alarms", { ack: b.dataset.ack });
        } catch (e) {
          b.disabled = false;
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
      return [...(this._config.entities || []), ...(this._config.groups || [])].map((e) => (typeof e === "string" ? e : e.entity));
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
.cover .hd{flex-wrap:nowrap}.cover .hd .sum{flex:1 1 auto;min-width:0}@container (max-width:360px){.cover .mst button.ico{width:38px;height:38px}}
.hd b{font:300 24px Onest,sans-serif;letter-spacing:-.03em;display:block;line-height:1.05;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.hd b.nd{font:400 15px Onest,sans-serif;letter-spacing:0;color:var(--ink3);margin-top:4px}
.grp{margin-top:14px}.grp .caps{display:block;margin-bottom:8px}.grow{display:flex;gap:6px;flex-wrap:wrap}
.g{display:flex;align-items:center;gap:6px;padding:5px 5px 5px 14px;border-radius:18px;box-shadow:inset 0 0 0 1px var(--hair);font:500 12px Onest,sans-serif;max-width:100%}
.g span{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;hyphens:auto;overflow-wrap:anywhere;line-height:1.25}.g.on{background:var(--track);box-shadow:none}.g.on.lt{background:color-mix(in srgb,var(--amber) 22%,transparent)}
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
.chips{display:flex;gap:6px;margin-top:14px;overflow-x:auto;scrollbar-width:none;-webkit-mask-image:linear-gradient(90deg,#000 88%,transparent);mask-image:linear-gradient(90deg,#000 88%,transparent);padding-right:24px}.chips::-webkit-scrollbar{display:none}
.chips button{flex:0 0 auto;min-height:44px;padding:0 15px;display:inline-flex;align-items:center;border-radius:14px;font:500 12px Onest,sans-serif;color:var(--ink2);box-shadow:inset 0 0 0 1px var(--hair)}
.chips button.on{color:var(--ink);background:var(--track);box-shadow:none}.chips em{font-style:normal;color:var(--amber);margin-left:5px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:12px}
.pill{position:relative;min-height:56px;padding-top:7px;padding-bottom:7px;border-radius:18px;overflow:hidden;display:flex;align-items:center;gap:10px;padding:0 12px;box-shadow:inset 0 0 0 1px var(--hair);touch-action:pan-y;user-select:none;-webkit-user-select:none;cursor:pointer}
.pill .f{position:absolute;left:0;top:0;bottom:0;transition:width .25s}
.pill.dragging .f{transition:none}.pill>*:not(.f){position:relative}
.pill ha-icon{color:var(--ink3);--mdc-icon-size:18px}.pill .tx{min-width:0;flex:1}
.pill .nm{font:500 13px/1.25 Onest,sans-serif;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;overflow-wrap:anywhere;-webkit-hyphens:auto;hyphens:auto}
.pill .lv{font:400 13px Onest,sans-serif;color:var(--ink2)}
.pill.on{box-shadow:none;background:var(--track)}.pill.on ha-icon{color:var(--accentC)}
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
      const levels = shown.map((e) => (isCover ? num(e.s.attributes.current_position) : e.s.state === "on" && num(e.s.attributes.brightness) !== undefined ? brightnessPct(e.s) : undefined)).filter((x) => x !== undefined);
      const lvl = levels.length ? Math.round(avg(levels)) : undefined;
      // Only group commands (a floor's, the building's): the summary and the master act on them.
      const groupsOnly = !all.length;
      const gItems = (this._config.groups || []).map((id) => ({ entity: id, s: st(this._hass, id) })).filter((g) => g.s);
      // A floor's/building's console: its group commands and a few devices («Мультимедиа») — the summary and
      // «Вкл./Выкл. все» count both (integrator run 25.10: one switch hid the floor's lit groups).
      const both = !!this._config.master_groups && !this._zone;
      const counted = groupsOnly ? gItems : both ? [...zoneItems, ...gItems] : zoneItems;
      const n = onCount(counted);
      // A lit group counts in the headline too: «Выключен» above a lit «Закарнизная подсветка» (persona round 4).
      const shownN = groupsOnly || both ? n : onCount([...zoneItems, ...gItems]);
      const shownTotal = groupsOnly || both ? counted.length : zoneItems.length + gItems.length;
      const title = this._config.title || (isCover ? "Шторы" : "Свет");
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
        const name = e.z && e.z !== "Прочее" ? shortName(full) : full;
        const zoneTag = !this._zone && e.z && e.z !== "Прочее" ? `${e.z} · ` : "";
        const settable = this.settable(e);
        const unavailable = e.s.state === "unavailable";
        if (isCover) {
          const pos = num(e.s.attributes.current_position);
          const open = isOpenOn(e);
          // Without a position the pill has ▲ ■ ▼ — full width, the name stays readable.
          const state = !known(e) ? "нет данных" : e.s.state === "opening" ? "открывается…" : e.s.state === "closing" ? "закрывается…" : !open ? "закрыта" : settable ? `${pos ?? 100} %` : "открыта";
          const moving = ["opening", "closing"].includes(e.s.state);
          return `<div class="pill ${open ? "on" : ""} ${moving ? "mv" : ""} ${settable ? "" : "wide"} ${unavailable ? "unavail" : ""}" data-e="${esc(e.entity)}" data-set="${settable ? 1 : 0}"><div class="f" style="width:${moving ? 100 : open ? pos ?? 100 : 0}%"></div>
<ha-icon icon="${esc(e.icon || (coverDir(e, this._config) === "horizontal" ? "hauflo:curtain" : "hauflo:blind"))}"></ha-icon><div class="tx"><div class="nm">${esc(name)}</div><div class="lv">${esc(zoneTag)}${state}</div></div>
${settable ? "" : `<div class="ud">${coverButtons(coverDir(e, this._config), "data-c")}</div>`}</div>`;
        }
        const on = isOpenOn(e);
        const pct = brightnessPct(e.s);
        return `<div class="pill ${on ? "on" : ""} ${settable ? "" : "solo"} ${unavailable ? "unavail" : ""}" data-e="${esc(e.entity)}" data-set="${settable ? 1 : 0}"><div class="f" style="width:${on && settable ? pct : 0}%"></div>
<ha-icon icon="${esc(e.icon || (!String(e.entity).startsWith("light.") ? e.s.attributes.icon || "hauflo:power" : on ? "hauflo:bulb-on" : "hauflo:bulb"))}"></ha-icon><div class="tx"><div class="nm">${esc(name)}</div><div class="lv">${esc(zoneTag)}${nd(e.s) ? "нет связи" : on ? (settable ? `${pct} %` : "вкл") : "выкл"}</div></div></div>`;
      };
      return `<div class="glass k ${isCover ? "cover" : "light"}"><div class="hd"><div class="sum"><span class="caps">${esc(title)}${this._zone ? ` · ${esc(this._zone)}` : ""}</span><b class="${isCover && unknownAll ? "nd" : ""}">${esc(summary)}</b></div><div class="mst">${master}</div></div>
${dimmables.length > 1 && (isCover || n) ? `<div class="bar" data-bar="1"><i style="width:${lvl ?? 0}%"></i><span>${isCover ? "Все" : "Яркость"}${lvl === undefined ? "" : ` · ${lvl} %`}</span></div>` : ""}
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
      if (this.kind === "cover") return ((num(e.s.attributes.supported_features) || 0) & 4) !== 0 && num(e.s.attributes.current_position) !== undefined;
      return (e.s.attributes.supported_color_modes || []).some((m) => m !== "onoff");
    }
    bind() {
      const root = this.shadowRoot;
      const isCover = this.kind === "cover";
      // Lights and switches (sockets) together: HA's generic service for the master, own domain per pill.
      const domain = isCover ? "cover" : "homeassistant";
      const shownIds = () => {
        const ids = [...root.querySelectorAll(".pill")].map((p) => p.dataset.e);
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
.nm{font:500 13px/1.25 Onest,sans-serif;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;overflow-wrap:anywhere;-webkit-hyphens:auto;hyphens:auto}
.fault,.warn{box-shadow:none}.w{font:400 13px Onest,sans-serif;color:var(--ink2)}
.fault{background:color-mix(in srgb,var(--alert) 14%,transparent)}.fault .w{color:var(--alert);font-weight:600}
.warn i,i.warn{background:var(--amber);box-shadow:0 0 8px var(--amberGlow)}.warn{background:color-mix(in srgb,var(--amber) 14%,transparent)}.warn .w{color:var(--amber);font-weight:600}`;
    }
    render() {
      const rows = (this._config.entities || []).map((e) => (typeof e === "string" ? { entity: e } : e)).map((e) => ({ ...e, s: st(this._hass, e.entity) })).filter((e) => e.s);
      const faults = rows.filter((e) => this.word(e.s)[1] === "fault").length;
      const nolink = rows.filter((e) => this.word(e.s)[1] === "nolink").length;
      const warns = rows.filter((e) => this.word(e.s)[1] === "warn").length;
      return `<div class="glass d"><div class="hd"><span class="caps">${esc(this._config.title || "Инженерные системы")}</span><span class="sum"><i class="${faults ? "fault" : warns || nolink ? "warn" : "ok"}"></i>${faults ? `аварий: ${faults}` : nolink ? `без связи: ${nolink}` : warns ? `внимание: ${warns}` : "всё в норме"}</span></div>
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
.it .nm{font:500 13px Onest,sans-serif;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;hyphens:auto;overflow-wrap:anywhere;line-height:1.25}.it .w{font:400 13px Onest,sans-serif;color:var(--ink2)}.it.bad{background:color-mix(in srgb,var(--amber) 18%,transparent)}.it.bad ha-icon{color:var(--amber)}
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
    button: { icon: "hauflo:check", word: (s, name = "") => (/звон|bell/i.test(name) ? "позвонить" : /калит|ворот|двер|замок|gate|door/i.test(name) ? "открыть" : "нажать"), tap: ["button", "press"], on: () => false, action: true },
    binary_sensor: { icon: "hauflo:info", word: (s) => (OPENING.includes(s.attributes.device_class) ? (s.state === "on" ? "открыто" : "закрыто") : s.state === "on" ? "сработал" : "в норме"), tap: null, tone: (s) => (s.state === "on" && ["moisture", "smoke", "gas", "problem", "safety", "carbon_monoxide"].includes(s.attributes.device_class) ? "alarm" : ""), on: (s) => s.state === "on" && !["moisture", "smoke", "gas", "problem", "safety", "carbon_monoxide"].includes(s.attributes.device_class) },
    sensor: { icon: "hauflo:info", word: (s) => `${fmt(num(s.state), num(s.state) % 1 ? 1 : 0)} ${unitOf(s)}`.trim(), tap: null, on: () => false, tone: (s) => (s.attributes.device_class === "battery" && num(s.state) !== undefined && num(s.state) < 20 ? "warn" : "") },
  };
  // --- hauflo-acs: every AC of the house in one card (persona round 3, Dubai: 14 rooms, «turn off every AC») ---
  class Acs extends EHCard {
    watched() {
      return (this._config.entities || []).map((e) => (typeof e === "string" ? e : e.entity));
    }
    css() {
      return `.k{padding:16px}.hd{display:flex;align-items:center;gap:10px}.hd .caps{flex:1}.sum{font:400 13px Onest,sans-serif;color:var(--ink2);margin-top:2px}
.off{min-height:40px;padding:0 14px;border-radius:20px;font:500 13px Onest,sans-serif;color:var(--ink2);box-shadow:inset 0 0 0 1px var(--hair)}
.rows{margin-top:10px;display:flex;flex-direction:column}.r{display:flex;align-items:center;gap:12px;min-height:52px;border-top:1px solid var(--hair);cursor:pointer}.r:first-child{border-top:0}
.r ha-icon{--mdc-icon-size:20px;color:var(--ink3)}.r.on ha-icon{color:var(--ice)}.r .nm{flex:1;min-width:0;font:500 14px Onest,sans-serif;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.r .w{font:400 13px Onest,sans-serif;color:var(--ink2);white-space:nowrap}.r .t{font:300 22px Onest,sans-serif;min-width:52px;text-align:right}.r.un{opacity:.5}`;
    }
    render() {
      const items = (this._config.entities || []).map((e) => (typeof e === "string" ? { entity: e } : e)).map((e) => ({ ...e, s: st(this._hass, e.entity) })).filter((e) => e.s);
      // «На связи» as the room card says it: a setpoint heard from the bus (integrator run 18: «4 из 4 работают» here,
      // «нет связи с термостатом» in the rooms).
      const known = (s) => !["unavailable", "unknown"].includes(s.state) && num(s.attributes.temperature) !== undefined;
      const on = items.filter((e) => known(e.s) && e.s.state !== "off");
      const offable = on.filter((e) => canTurnOff(e.s));
      const rows = items.map((e) => {
        const a = e.s.attributes;
        const cur = num(a.current_temperature);
        const target = num(a.temperature);
        // The mode on every row — «Нагрев» next to «Нагревает» in one list read as two different things (integrator run 17).
        const word = !known(e.s) ? "нет связи" : e.s.state === "off" ? "Выкл" : HVAC[e.s.state] || e.s.state;
        return `<div class="r ${known(e.s) && e.s.state !== "off" ? "on" : ""} ${known(e.s) ? "" : "un"}" data-i="${esc(e.entity)}"><ha-icon icon="hauflo:ac"></ha-icon><div class="nm">${esc(e.name || nameOf(e.s, e.entity))}</div><div class="w">${esc(word)}${cur !== undefined ? ` · ${fmt(cur, 1)}°` : ""}</div><div class="t">${target === undefined || e.s.state === "off" ? "—" : `${fmt(target, Number.isInteger(target) ? 0 : 1)}°`}</div></div>`;
      });
      return `<div class="glass k"><div class="hd"><div><span class="caps">${esc(this._config.title || "Кондиционеры")}</span><div class="sum">${on.length ? `${on.length} из ${items.length} работают` : items.some((e) => known(e.s)) ? "Все выключены" : "Нет связи"}</div></div>${offable.length ? `<button class="off" data-off="1">Выкл. все</button>` : ""}</div><div class="rows">${rows.join("")}</div></div>`;
    }
    bind() {
      this.shadowRoot.querySelectorAll("[data-i]").forEach((r) => r.addEventListener("click", () => moreInfo(this, r.dataset.i)));
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
.p ha-icon{color:var(--ink3);--mdc-icon-size:20px;flex:0 0 auto}.p .tx{min-width:0}.nm{font:500 13px/1.25 Onest,sans-serif;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;overflow-wrap:anywhere;-webkit-hyphens:auto;hyphens:auto}.w{font:400 13px Onest,sans-serif;color:var(--ink2)}
.p.on{background:color-mix(in srgb,var(--mint) 16%,transparent);box-shadow:none}.p.on ha-icon{color:var(--mint)}
.p.act{box-shadow:inset 0 0 0 1.5px color-mix(in srgb,var(--amber) 55%,transparent)}.p.act ha-icon{color:var(--amber)}.p.act .w{color:var(--ink2)}
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
      return `<div class="glass k">${this._config.title ? `<span class="caps">${esc(this._config.title)}</span>` : ""}<div class="grid">${items
        .map((e) => {
          const d = DEV[e.entity.split(".")[0]] || { icon: "hauflo:info", word: (s) => s.state, tap: null };
          const tone = d.tone ? d.tone(e.s) : "";
          const on = !tone && (d.on ? d.on(e.s) : e.s.state === "on");
          return `<div class="p ${on ? "on" : ""} ${tone} ${d.action ? "act" : ""} ${nd(e.s) ? "un" : ""}" data-e="${esc(e.entity)}"><ha-icon icon="${esc(e.icon || (/звон|bell/i.test(e.name || nameOf(e.s, e.entity)) ? "hauflo:doorbell" : d.icon))}"></ha-icon><div class="tx"><div class="nm">${esc(e.name || nameOf(e.s, e.entity))}</div><div class="w">${esc(nd(e.s) ? "нет связи" : d.word(e.s, e.name || nameOf(e.s, e.entity)))}</div></div></div>`;
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
  const define = () => {
    const reg = window.customElements;
    if (!reg.get("home-assistant") && !reg.get("hui-view")) return setTimeout(define, 120);
    window.customCards = window.customCards || [];
    for (const [tag, cls, name, description] of cards) {
      if (reg.get(tag)) continue;
      if (STUB[tag] && !cls.getStubConfig) cls.getStubConfig = (hass) => JSON.parse(JSON.stringify(STUB[tag](hass)));
      reg.define(tag, cls);
      if (!window.customCards.some((c) => c.type === tag)) window.customCards.push({ type: tag, name, description, preview: false });
    }
  };
  define();
})();
