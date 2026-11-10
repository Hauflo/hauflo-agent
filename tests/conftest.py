"""pytest-homeassistant-custom-component: the integration under custom_components/ is loaded for each test."""

import pytest
from pytest_homeassistant_custom_component.common import mock_component

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def _custom_integrations(enable_custom_integrations, hass):
    # The frontend package (hass_frontend) is not in the test requirements; the flow never serves panels.
    mock_component(hass, "frontend")
    yield


@pytest.fixture(autouse=True)
def _no_quiet_hours(monkeypatch):
    """Quiet hours are on by default in the canon; the other tests run at any time of day — they switch it off here,
    test_quiet.py turns it on through the API."""
    from custom_components.hauflo import quiet

    real = quiet.default
    monkeypatch.setattr(quiet, "default", lambda: {**real(), "on": False})


# The alarm automations (0.31) repeat inside their run: after the push it waits for «Принято», the sensor back or the
# repeat timer (wait_for_trigger) — for minutes, as in HA itself. HA's own block_till_done waits for every automation
# run to finish, so it would wait for ever; here a run parked in its wait is settled — and a task that only awaits such
# a run (its trigger's task, a step running a sub-sequence), its own sub-tasks are looked at themselves.
_PARKED = "_ScriptRun._async_wait_with_optional_timeout"


def _chain(task) -> list[str]:
    out, c = [], task.get_coro() if hasattr(task, "get_coro") else None
    while c is not None:
        out.append(getattr(c, "__qualname__", None) or type(c).__name__)
        c = getattr(c, "cr_await", None) or getattr(c, "gi_yieldfrom", None)
    return out


def _settled(task, depth: int = 0) -> bool:
    """Waits only for a parked script run — itself, or through the tasks / gather it awaits — not for work of its own."""
    import asyncio

    waiter = getattr(task, "_fut_waiter", None)
    if waiter is None or waiter.done() or depth > 20:  # running, or woken and not run yet
        return False
    chain = _chain(task)
    if _PARKED in chain or chain[-2:] == ["Script.async_run", "FutureIter"]:  # the run itself is looked at on its own
        return True
    if isinstance(waiter, asyncio.Task):
        return _settled(waiter, depth + 1)
    if type(waiter).__name__ == "_GatheringFuture":
        # All its children done and the gather not yet woken is work to come, not a parked run.
        pending = [c for c in getattr(waiter, "_children", []) if not c.done()]
        return bool(pending) and all(_settled(c, depth + 1) for c in pending)
    return False


@pytest.fixture(autouse=True)
def _parked_runs_are_settled(monkeypatch):
    import asyncio
    import threading

    from homeassistant.core import HomeAssistant
    from homeassistant.util.async_ import cancelling

    original = HomeAssistant.async_block_till_done

    async def block(self, wait_background_tasks: bool = False) -> None:
        # No script run at all: HA's own wait, exactly (its stop, a config entry's reload).
        if not any((ch := _chain(t))[:1] and ch[0].startswith(("_ScriptRun.", "Script.")) for t in asyncio.all_tasks()):
            await original(self, wait_background_tasks)
            return
        loop = asyncio.get_running_loop()
        # A task may park while it is awaited, or wait for a thread (a sync service): waited a few real milliseconds at
        # a time — the loop's own clock is frozen in the tests.
        quiet = 0
        for _ in range(20000):
            await asyncio.sleep(0)
            cur = asyncio.current_task()
            busy = [t for t in asyncio.all_tasks() if t is not cur and (ch := _chain(t))[:1] and ch[0].startswith("_ScriptRun.") and not _settled(t)]
            tasks = self._tasks | self._background_tasks if wait_background_tasks else self._tasks
            others = [t for t in tasks if t is not cur and not t.done() and not cancelling(t) and not _settled(t)]
            if not busy and not others:
                # A step's own callbacks (a gather's, a woken task's) take a few turns of the loop.
                quiet += 1
                if quiet > 5:
                    return
                continue
            quiet = 0
            waker = loop.create_future()
            timer = threading.Timer(0.005, lambda w=waker: loop.call_soon_threadsafe(lambda: w.done() or w.set_result(None)))
            timer.start()
            await asyncio.wait([*busy, *others, waker], return_when=asyncio.FIRST_COMPLETED)
            timer.cancel()

    monkeypatch.setattr(HomeAssistant, "async_block_till_done", block)
