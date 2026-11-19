"""The agent's one place for «a failure here must not stop the caller»: a boundary that logs and goes on."""
from __future__ import annotations
from collections.abc import Iterator
from contextlib import contextmanager
import logging
_ExpectedErrors = type[BaseException] | tuple[type[BaseException], ...]

@contextmanager
def contained(log: logging.Logger, msg: str, *args: object, expect: _ExpectedErrors | None=None, level: int | None=None, err: bool=False) -> Iterator[None]:
    """Run the block; a failure in it is logged as `msg % args` and the code after the block goes on."""
    caught: _ExpectedErrors = Exception if expect is None else expect
    try:
        yield
    except caught as exc:
        if level is None:
            level = logging.WARNING if expect is None else logging.DEBUG
        log.log(level, msg, *((*args, exc) if err else args), exc_info=True)
