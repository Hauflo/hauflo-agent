from __future__ import annotations
import re
_PARTS = re.compile('(\\d+)')

def natural_key(text: object) -> tuple[tuple[int, int, str], ...]:
    out: list[tuple[int, int, str]] = []
    for part in _PARTS.split(str(text or '').casefold()):
        if part.isdigit():
            out.append((0, int(part), ''))
        elif part:
            out.append((1, 0, part))
    return tuple(out)
