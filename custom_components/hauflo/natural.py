"""Natural order for every list a person reads: «Штора 2» before «Штора 10», «Room 9» before «Room 10»."""
from __future__ import annotations
import re
_PARTS = re.compile('(\\d+)')

def natural_key(text: object) -> tuple[tuple[int, int, str], ...]:
    """Sort key: text and numbers alternate, so «2» < «10» and «Кухня» ~ «кухня»."""
    out: list[tuple[int, int, str]] = []
    for part in _PARTS.split(str(text or '').casefold()):
        if part.isdigit():
            out.append((0, int(part), ''))
        elif part:
            out.append((1, 0, part))
    return tuple(out)
