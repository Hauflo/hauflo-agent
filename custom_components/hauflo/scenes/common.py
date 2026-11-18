"""Scene limits, errors and names."""
from __future__ import annotations
import asyncio
from collections.abc import Callable
from dataclasses import dataclass
import functools
from http import HTTPStatus
import re
import time
from typing import Any
import unicodedata
from .. import derived, yamlfile
from ..tuning import tuning
STORE_KEY = 'hauflo.scenes'
SCENES = 'scenes.yaml'
BACKUP = SCENES + '.hauflo-bak-'
KEEP_BACKUPS = 3
MAX_NAME = 40
MAX_FAMILY = 60
MAX_ENTITIES = 60
ROOM = re.compile('[a-z0-9]{1,12}')
FAMILY = re.compile('hauflo_fam_([a-z0-9]{1,12})_([lc])_[a-z0-9]{6,16}')
HAUFLO = re.compile('(?:hauflo|easyha)_(?!fam_)[a-z0-9]{1,12}_(?:cv_)?[a-z0-9]{1,16}')
_CONTROL = re.compile('[\x00-\x1f\x7f-\x9f\u2028\u2029]+')
FILE_LOCK = asyncio.Lock()
_t = functools.cache(lambda: tuning()['scenes'])
learn_every = functools.cache(lambda: _t()['learn_every_s'])
learn_per_minute = functools.cache(lambda: _t()['learn_per_minute'])
MAX_NAMES = 200
clock = time.monotonic

class Invalid(ValueError):
    pass

class Refused(Invalid, derived.Refused):
    """A refusal a person can meet (not a request sent wrong): said in the page's language — translations
    scenes_<code>."""

    def __init__(self, code: str, status: int=HTTPStatus.BAD_REQUEST, **ph: Any) -> None:
        derived.Refused.__init__(self, code, f'scenes_{code}', status, **ph)

class Locked(RuntimeError):
    RU = 'Владелец выключил «Разрешить Hauflo менять настройки этого объекта» (Hauflo → Настроить) — сцену KNX сейчас не запомнить.'
    EN = "The owner switched off “Allow Hauflo to change this site's setup” (Hauflo → Configure) — the KNX scene can't be saved now."

class TooSoon(RuntimeError):
    """    «только что отправлено» though nothing of this scene had been)."""
    RU = 'Только что отправлено. Подождите несколько секунд.'
    EN = 'Just sent. Wait a few seconds.'

    def __init__(self, house: bool=False) -> None:
        super().__init__('house' if house else 'scene')
        self.house = house

    def text(self, ru: bool) -> str:
        if self.house:
            n = learn_per_minute()
            return f'Не больше {n} сцен в минуту — подождите минуту.' if ru else f'No more than {n} scenes a minute — wait a minute.'
        return self.RU if ru else self.EN

@dataclass
class KnxScenes:
    """    lock)."""
    numbers: Callable[[], dict[str, int]]
    target: Callable[[str], tuple[str, int]]
    locked: Callable[[], bool]

class NotPlain(RuntimeError):
    RU = 'Hauflo не меняет scenes.yaml: в нём комментарии, !include или ручное оформление. Сцену можно сохранить в редакторе сцен Home Assistant.'
    EN = "Hauflo doesn't change scenes.yaml: it has comments, !include or hand formatting. Save the scene in Home Assistant's scene editor."

def clean_name(value: Any) -> str:
    if not isinstance(value, str):
        raise Refused('bad_name')
    raw = ''.join((ch for ch in value if unicodedata.category(ch) != 'Cf'))
    text = ' '.join(_CONTROL.sub(' ', raw).split()).strip()
    if not any((unicodedata.category(ch)[0] in 'LN' for ch in text)):
        raise Refused('bad_name')
    if len(text) > MAX_NAME:
        raise Refused('name_long', n=MAX_NAME)
    return text

def _plain(text: str) -> list[Any]:
    return yamlfile.parse(text, NotPlain)
