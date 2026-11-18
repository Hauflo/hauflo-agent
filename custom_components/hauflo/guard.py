"""The agent's one place for «a failure here must not stop the caller»: a boundary that logs and goes on.

A callback HA runs, a background loop, a handler of the tunnel's message, a phone that fails among several, an HA
internal that changed between releases — in every such place any error must be told (never swallowed silently) and
must not end the loop or the setup. The code that wants it writes `with contained(...)` instead of a bare
`except Exception`: the broad catch lives here once, with its reason.

Two shapes, by what the caller knows:
- `expect=(TimeoutError, ClientError)` — the caller names what may fail in the block and that it is normal noise
  (a poll that timed out, a registry entry that is not there): only those are caught, told at DEBUG, anything else
  flies on to the caller;
- no `expect` — nobody can say what fails (HA's internals, a user's callback): any error is caught and told at
  WARNING with its stack, so at HA's normal log level it is never silent."""
from __future__ import annotations
from collections.abc import Iterator
from contextlib import contextmanager
import logging
_ExpectedErrors = type[BaseException] | tuple[type[BaseException], ...]

@contextmanager
def contained(log: logging.Logger, msg: str, *args: object, expect: _ExpectedErrors | None=None, level: int | None=None, err: bool=False) -> Iterator[None]:
    """Run the block; a failure in it is logged as `msg % args` and the code after the block goes on.

    `expect` — the errors the block is known to raise in a normal case: only they are caught, at DEBUG; others
    propagate. Without it every `Exception` is caught and logged at WARNING. `level` overrides the level of either
    shape (ERROR for «this must not happen»). The stack is always attached. `err` — the error is the last `%s`
    argument of the message."""
    caught: _ExpectedErrors = Exception if expect is None else expect
    try:
        yield
    except caught as exc:
        if level is None:
            level = logging.WARNING if expect is None else logging.DEBUG
        log.log(level, msg, *((*args, exc) if err else args), exc_info=True)
