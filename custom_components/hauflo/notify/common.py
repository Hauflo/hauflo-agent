"""Alarm settings, hold times and the small helpers shared by the alarm engine."""
from __future__ import annotations
import contextvars
import functools
import logging
import re
from typing import Any
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import device_registry as dr, entity_registry as er
from ..const import DOMAIN
from ..signals import canon as signals_canon, store as signals_store
from ..tuning import tuning
STORE_KEY = 'hauflo.notify'
ACTIVE_KEY = 'hauflo.notify_active'
ACTION_EVENT = 'mobile_app_notification_action'
ACK = 'HAUFLO_ACK'
RUN_SERVICES = {'valve.close_valve', 'switch.turn_off'}
t = functools.cache(lambda: tuning()['notify'])
critical_classes = functools.cache(lambda: set(t()['critical_classes']))
repeat_min = functools.cache(lambda: t()['repeat_min'])
RUN = 'HAUFLO_RUN'
alarm_classes = functools.cache(lambda: set(t()['alarm_classes']))
NOT_A_STATE = {STATE_UNAVAILABLE, STATE_UNKNOWN, None}
group_window = functools.cache(lambda: t()['group_window_s'])
nodata_after = functools.cache(lambda: t()['nodata_after_s'])
chatter_count = functools.cache(lambda: t()['chatter_count'])
chatter_window_cfg = functools.cache(lambda: t()['chatter_window_s'])
chatter_window_life = functools.cache(lambda: t()['chatter_window_life_s'])
life_safety_kinds = functools.cache(lambda: frozenset(t()['life_safety_kinds']))

def chatter_window(rule: dict[str, Any] | None) -> int:
    """The rule's chatter window (seconds): short for life safety, the common one for the rest."""
    return chatter_window_life() if rule and rule.get('kind') in life_safety_kinds() else chatter_window_cfg()
hold_canon = functools.cache(lambda: t()['hold'])
hold_max = functools.cache(lambda: hold_canon()['max_s'])
native_hold = functools.cache(lambda: t()['native_hold_s'])

def native_back(rule: dict[str, Any]) -> int:
    """Seconds a source of a rule HA runs stays in normal before its «back» (its chatter window, at least NATIVE_HOLD —
    ISA-18.2 / Nagios flap hysteresis on the return; HA's own `for:`); in alarm again sooner — the same open alarm."""
    return max(native_hold(), chatter_window(rule))

def hold_value(v: Any) -> int | None:
    """A rule's own hold as sent: a whole number of seconds 0..HOLD_MAX; anything else (absent, an older Hauflo) —
    None."""
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != int(v) or (not 0 <= v <= hold_max()):
        return None
    return int(v)

def default_hold(hass: HomeAssistant | None, kind: str, sources: list[str], thresholds: dict[str, Any]) -> int:
    """The canon hold of a rule with no own: its kind's, each source's (a level — level_s, a signal — by its class,
    classless — default_s); the shortest."""
    found = [hold_canon()['kinds'][kind]] if kind in hold_canon()['kinds'] else []
    for eid in sources:
        found.append(hold_canon()['level_s'] if eid in thresholds else hold_canon()['classes'].get(str(device_class_of(hass, eid) or ''), hold_canon()['default_s']))
    return int(min(found)) if found else int(hold_canon()['default_s'])

def source_hold(rule: dict[str, Any], eid: str) -> int:
    """How long this source's alarm state must hold in this rule: its own (a combination's — `holds`), else the
    rule's."""
    return int((rule.get('holds') or {}).get(eid, rule.get('hold') or 0))

def own_kind(hass: HomeAssistant | None, eid: str) -> str | None:
    """A Hauflo signal that holds its time itself — its key in notify.hold.own, else None: a combination of the site's
    policy (on only once every condition held its «дольше, чем» — that T is its hold, a second one after it would make
    it T + a minute), or an own sensor of that kind in HA's registry (platform hauflo, its translation key: a rule's
    «Сработало» is on only after its own «дольше» / periods in a row). By what it is, never by its name."""
    if hass is None:
        return None
    if eid in signals_store.combo_entities(hass):
        return 'combo'
    entry = er.async_get(hass).async_get(eid)
    key = entry.translation_key if entry is not None and entry.platform == DOMAIN and (entry.domain == 'binary_sensor') else None
    return key if key in hold_canon()['own'] else None

def own_holds(hass: HomeAssistant | None, sources: list[str], holds: dict[str, int]) -> dict[str, int]:
    """{source: its hold} for the rule's sources that hold their time themselves (own_kind): the site's own hold of
    that very signal if it set one, else the canon's for its kind (notify.hold.own) — never another source's class,
    nor a scope's (a class's choice, the canon's level): the rule's other sources keep theirs."""
    pol = signals_store.policy(hass) or {}
    out: dict[str, int] = {}
    for eid in sources:
        if (kind := own_kind(hass, eid)) is not None:
            mine = ((pol.get('entities') or {}).get(eid) or {}).get('holdS')
            out[eid] = int(mine) if isinstance(mine, int) and eid in holds else int(hold_canon()['own'][kind])
    return out
LINK_KINDS = ('connection', 'link', 'connectivity')
PHONE: contextvars.ContextVar[str | None] = contextvars.ContextVar('hauflo_phone', default=None)

class PushErrors(logging.Handler):

    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.failed: set[str] = set()
        self.limited: set[str] = set()

    def emit(self, record: logging.LogRecord) -> None:
        if (phone := PHONE.get()):
            self.failed.add(phone)
            if record.levelno == logging.WARNING or 'rate limit' in record.getMessage().lower():
                self.limited.add(phone)
push_hour_max = functools.cache(lambda: t()['push_hour_max'])
push_day_max = functools.cache(lambda: t()['push_day_max'])
push_day_critical = functools.cache(lambda: t()['push_day_critical'])
push_event_hour_max = functools.cache(lambda: t()['push_event_hour_max'])
push_event_day_max = functools.cache(lambda: t()['push_event_day_max'])
LANES = ('alarm', 'event')
repeat_fast = functools.cache(lambda: t()['repeat_fast'])
repeat_slow = functools.cache(lambda: t()['repeat_slow_min'])
SCOPES = ('all', 'critical', 'off')
PHONE_ID = re.compile('^mobile_app_[a-z0-9_]+$')
WORD = re.compile('^[a-z0-9_]{1,32}$')
states_max = functools.cache(lambda: t()['rule_states_max'])
test_gap = functools.cache(lambda: t()['test_gap_s'])
test_per_min = functools.cache(lambda: t()['test_per_min'])
group_min = functools.cache(lambda: t()['group_min'])
list_max = functools.cache(lambda: t()['list_max'])
heartbeat = functools.cache(lambda: re.compile(t()['heartbeat_words'], re.I))
fault_name = functools.cache(lambda: re.compile(t()['fault_name_words'], re.I))
outputs_cfg = functools.cache(lambda: t()['outputs'])
outputs_max = functools.cache(lambda: t()['outputs_max'])
ENTITY_ID = re.compile('^[a-z_]+\\.[a-z0-9_]+$')
levels = functools.cache(lambda: t()['levels'])
level_step_share = functools.cache(lambda: t()['level_step_share'])
level_abs_max = functools.cache(lambda: t()['level_abs_max'])
ring_window = functools.cache(lambda: t()['ring_window_s'])
ring_fresh = functools.cache(lambda: tuning()['link']['event_recent_s'])
quiet_channel = functools.cache(lambda: t()['quiet_channel'])
_kinds = functools.cache(lambda: {k: v for k, v in t()['event_kinds'].items() if not k.startswith('_')})
event_kinds = functools.cache(lambda: {k: (frozenset(v.get('classes', ())), re.compile(v['words'], re.I)) for k, v in _kinds().items()})
c_sos = functools.cache(lambda: frozenset((k for k, v in _kinds().items() if 'critical' in v.get('does', ()))))
watch_kinds = functools.cache(lambda: frozenset((k for k, v in _kinds().items() if 'watch' in v.get('does', ()))))
ring_kinds = functools.cache(lambda: frozenset((k for k, v in _kinds().items() if 'ring' in v.get('does', ()))))
ring = functools.cache(lambda: t()['ring_push'])
INTERCOM_EVENT = f'{DOMAIN}_intercom'

def event_kind(state: State) -> str:
    """A kind of `event_kinds` — «sos» (a panic button), «doorbell» (a doorbell, an intercom's SIP call), «button» (a
    Hue /
    Aqara / Shelly button), «motion» — or «event» (any other press): the first kind whose class or words match, so an
    SOS button of the «button» class is an SOS and a button named «Звонок» a doorbell. One rule for the push here and
    the
    bell / Telegram line in the cloud (status `eventClass`)."""
    dc = str(state.attributes.get('device_class') or '')
    said = f"{state.attributes.get('event_type') or ''} {state.name or ''} {state.object_id}"
    return next((kind for kind, (classes, words) in event_kinds().items() if dc and dc in classes or words.search(said)), 'event')

def watched_press(state: State | None, platform: str | None=None) -> bool:
    """A press the canon watches by default though HA gives it no doorbell class (acceptance 0.33 M4): an event entity
    whose kind `event_kinds` watches — an SOS / panic button, an intercom's SIP call. Any other press (a button, motion)
    stays the integrator's choice (the label / «Уведомлять»)."""
    return state is not None and state.domain == 'event' and (platform not in signals_canon.watch_skip()) and (event_kind(state) in watch_kinds())
SOURCE_DOMAINS = ('binary_sensor.', 'sensor.')

def device_class_of(hass: HomeAssistant | None, eid: str) -> str | None:
    if hass is None:
        return None
    entry = er.async_get(hass).async_get(eid)
    dc = entry.device_class or entry.original_device_class if entry is not None else None
    if dc is None and (st := hass.states.get(eid)) is not None:
        dc = st.attributes.get('device_class')
    return str(dc) if dc is not None else None
quiet_name = functools.cache(lambda: re.compile(t()['quiet_name_words'], re.I))
loud_name = functools.cache(lambda: re.compile(t()['loud_name_words'], re.I))
DOWNGRADE = re.compile('^(no_sources|quiet_class:[a-z_]{1,32}|quiet_name)$')
SCOPE_RANK = {'off': 0, 'critical': 1, 'all': 2}
_EN_FRAGMENTS = [('Hauflo: проверка уведомлений', 'Hauflo: notification test'), ('Так будут приходить аварии.', 'This is how alarms will arrive.'), (' Критичные — со звуком даже в беззвучном режиме.', ' Critical ones ring even on silent.'), (', никто не подтвердил. Повтор ', ', nobody accepted it. Repeat '), ('Никто не подтвердил. Повтор ', 'Nobody accepted it. Repeat '), (': снова в норме.', ': back to normal.'), ('Снова в норме: ', 'Back to normal: '), (': авария в ', ': alarm at '), (': авария с ', ': alarm since '), (': авария.', ': alarm.'), (' (последняя в ', ' (latest at '), (': выполнено (', ': done ('), (': не получилось — ', ': failed — '), ('Открыто: ', 'Open: '), ('Принято: ', 'Accepted: '), ('Выключить: ', 'Turn off: '), ('Закрыть: ', 'Close: '), ('кто-то', 'someone')]
_EN_WORDS = {'Принято': 'Accept', 'Авария': 'Alarm'}
EN_TITLES = {'Протечка': 'Water leak', 'Дым': 'Smoke', 'Газ': 'Gas', 'Авария котла': 'Boiler alarm', 'Авария оборудования': 'Equipment alarm', 'Температура': 'Temperature', 'Батарейка': 'Battery', 'Двери и окна': 'Doors and windows', 'Своё': 'Custom', 'Связь со щитом': 'Panel link lost', 'Авария': 'Alarm'}
RU_TITLES = {en: ru for ru, en in EN_TITLES.items()}

def label(hass: HomeAssistant, entity_id: str, state: State | None=None) -> str:
    """What a person calls the device: HA's friendly name, else the registry's name, else its device's name — the raw
    object id («leak_bathroom») only when nothing else exists (stage 10 round 3)."""
    state = state or hass.states.get(entity_id)
    if (name := (str(state.attributes.get('friendly_name') or '').strip() if state else '')):
        return plain(once(name))
    entry = er.async_get(hass).async_get(entity_id)
    if entry:
        if (name := (entry.name or entry.original_name or '')):
            return plain(once(str(name)))
        if entry.device_id and (device := dr.async_get(hass).async_get(entry.device_id)) and (name := (device.name_by_user or device.name or '')):
            return plain(once(str(name)))
    return plain(entity_id.partition('.')[2] or entity_id)
_SNAKE = re.compile('\\b[a-z0-9]+(?:_[a-z0-9]+)+\\b')

def plain(name: str) -> str:
    """A machine id said as words (acceptance 0.33: «climate_living Temperature» — an MQTT device named by its id): the
    snake_case tokens spaced, as HA itself says an entity without a name («leak_bathroom» → «Leak bathroom»)."""
    out = _SNAKE.sub(lambda m: m.group(0).replace('_', ' '), name.strip())
    return out[:1].upper() + out[1:] if out != name.strip() else out

def once(name: str) -> str:
    """«Звонок в дверь Звонок в дверь» → «Звонок в дверь»: HA's friendly name is «device + entity», and a device whose
    one entity is named as the device itself (an MQTT doorbell / SOS button) says it twice (acceptance 0.33)."""
    half, rest = (len(name) // 2, len(name) % 2)
    if rest and name[half] == ' ' and (name[:half] == name[half + 1:]):
        return name[:half]
    return name

def in_en(text: str) -> str:
    if text in _EN_WORDS:
        return _EN_WORDS[text]
    for ru, en in _EN_FRAGMENTS:
        text = text.replace(ru, en)
    text = re.sub('^С (\\d)', 'Since \\1', text)
    text = re.sub(' и ещё (\\d+)', ' and \\1 more', text)
    return re.sub('(\\d+) (устройство|устройства|устройств)\\b', lambda m: f"{m.group(1)} device{('' if m.group(2) == 'устройство' else 's')}", text)

def safe_int(v: Any) -> int:
    """A number from Hauflo's rules, never a 500 on «abc» (HA review round 4)."""
    try:
        return int(float(v or 0))
    except (TypeError, ValueError):
        return 0

class LockedChange(PermissionError):
    pass
