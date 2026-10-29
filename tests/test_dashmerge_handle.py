"""dashmerge.handle / local_ws: every way a dashboard command can end, said to Hauflo as a result frame, never relayed."""

import json
from types import SimpleNamespace as NS

import pytest

from custom_components.hauflo import dashmerge
from custom_components.hauflo.dashmerge import MergeError


class FakeWS:
    def __init__(self, replies):
        self.replies, self.sent = list(replies), []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def receive_json(self):
        return self.replies.pop(0)

    async def send_json(self, data):
        self.sent.append(data)


class FakeSession:
    def __init__(self, ws):
        self.ws = ws

    def ws_connect(self, url, max_msg_size=0):
        return self.ws


async def test_local_ws_runs_commands_in_order_and_stops_at_the_first_failure():
    ws = FakeWS([{"type": "auth_required"}, {"type": "auth_ok"}, {"type": "event", "id": 1}, {"id": 1, "type": "result", "success": True, "result": "a"}, {"id": 2, "type": "result", "success": True, "result": "b"}])
    out = await dashmerge.local_ws(FakeSession(ws), "ws://x", "tok", [{"type": "one"}, {"type": "two"}])
    assert out == ["a", "b"] and ws.sent[0] == {"type": "auth", "access_token": "tok"} and [m["id"] for m in ws.sent[1:]] == [1, 2]
    # the sign-in refused
    with pytest.raises(MergeError) as err:
        await dashmerge.local_ws(FakeSession(FakeWS([{"type": "auth_required"}, {"type": "auth_invalid"}])), "ws://x", "bad", [{"type": "one"}])
    assert err.value.code == "local_auth"
    # a failing command: the message comes along, the next one never runs
    ws = FakeWS([{"type": "auth_required"}, {"type": "auth_ok"}, {"id": 1, "type": "result", "success": False, "error": {"message": "nope"}}])
    with pytest.raises(MergeError, match="one: nope") as err:
        await dashmerge.local_ws(FakeSession(ws), "ws://x", "tok", [{"type": "one"}, {"type": "two"}])
    assert err.value.code == "local_failed" and len(ws.sent) == 2
    ws = FakeWS([{"type": "auth_required"}, {"type": "auth_ok"}, {"id": 1, "type": "result", "success": False}])
    with pytest.raises(MergeError, match="None"):
        await dashmerge.local_ws(FakeSession(ws), "ws://x", "tok", [{"type": "one"}])


class Merger:
    def __init__(self, managed=True):
        self._managed, self.calls = managed, []
        self.read_err = self.plan_err = self.save_err = None

    def manages(self, url):
        return self._managed and url.startswith("hauflo-")

    async def read(self, url):
        if self.read_err:
            raise self.read_err
        return {"views": []}

    async def plan(self, url, config):
        if self.plan_err:
            raise self.plan_err
        return {"plan": 1}

    async def save(self, *args):
        self.calls.append(args)
        if self.save_err:
            raise self.save_err
        return {"saved": True}


def rig(locked=False, merger=None):
    sent = []

    async def send(frame):
        sent.append(json.loads(frame["data"]))

    agent = NS(send=send, dashboards=merger or Merger(), locked=locked, local_session=None, local_base="http://x", token="t")
    return agent, sent


async def run(agent, msg, valid=True, fresh=True):
    await dashmerge.handle(agent, 1, {"id": 7, **msg}, lambda c: valid, lambda: fresh, "locked_by_owner")


async def test_reads_are_answered_by_the_agent():
    agent, sent = rig()
    await run(agent, {"type": "lovelace/config", "url_path": "hauflo-home"})
    assert sent[-1]["result"] == {"views": []}
    agent.dashboards.read_err = MergeError("no_base", "nothing saved yet")
    await run(agent, {"type": "lovelace/config", "url_path": "hauflo-home"})
    assert sent[-1]["error"]["code"] == "no_base"
    # a copy, or a dashboard of the family's: never read through here
    await run(agent, {"type": "lovelace/config", "url_path": "hauflo-home-copy-20261029"})
    await run(agent, {"type": "lovelace/config", "url_path": "family"})
    assert [s["error"]["code"] for s in sent[-2:]] == ["not_allowed", "not_allowed"]


async def test_writes_refused_by_url_config_and_the_dashboard_check():
    agent, sent = rig()
    for msg, valid in (({"type": "lovelace/config/save", "url_path": "family", "config": {}}, True), ({"type": "lovelace/config/save", "url_path": "hauflo-x-copy-1", "config": {}}, True),
                       ({"type": "lovelace/config/save", "url_path": "hauflo-x", "config": []}, True), ({"type": "lovelace/config/save", "url_path": "hauflo-x", "config": {}}, False)):
        await run(agent, msg, valid=valid)
        assert sent[-1]["error"]["code"] == "not_allowed"
    assert agent.dashboards.calls == []


async def test_plan_save_lock_backup_and_failures():
    agent, sent = rig()
    await run(agent, {"type": dashmerge.PLAN, "url_path": "hauflo-x", "config": {}})
    assert sent[-1]["result"] == {"plan": 1}
    agent.dashboards.plan_err = MergeError("lost", "a card is lost")
    await run(agent, {"type": dashmerge.PLAN, "url_path": "hauflo-x", "config": {}})
    assert sent[-1]["error"]["code"] == "lost"
    # a locked house: no save, no copy made
    agent.locked = True
    await run(agent, {"type": dashmerge.SAVE, "url_path": "hauflo-x", "config": {}})
    assert sent[-1]["error"]["code"] == "locked_by_owner" and agent.dashboards.calls == []
    agent.locked = False
    await run(agent, {"type": dashmerge.SAVE, "url_path": "hauflo-x", "config": {}}, fresh=False)
    assert sent[-1]["error"]["code"] == "backup_required" and agent.dashboards.calls == []
    # a merged save passes what the integrator chose (bounded), a plain one replaces only where the agent has no version
    await run(agent, {"type": dashmerge.SAVE, "url_path": "hauflo-x", "config": {}, "take": ["a", 5, "x" * 300], "restore": True, "replace": True, "title": "T" * 100, "lang": "ru-RU-extra"})
    take, restore, replace, title, lang = (agent.dashboards.calls[-1][i] for i in (2, 3, 4, 5, 7))
    assert sent[-1]["result"] == {"saved": True} and take == ["a"] and restore is True and replace is True and len(title) == 60 and lang == "ru-RU"
    await run(agent, {"type": "lovelace/config/save", "url_path": "hauflo-x", "config": {}, "take": ["a"]})
    args = agent.dashboards.calls[-1]
    assert args[2] == [] and args[3] == [] and args[4] is False and sent[-1]["result"] is None
    agent.dashboards._managed = False
    await run(agent, {"type": "lovelace/config/save", "url_path": "hauflo-x", "config": {}})
    assert agent.dashboards.calls[-1][4] is True  # none yet: Hauflo's config replaces it (the old one kept as a copy)
    # a merge failure is said with its code; anything else as "failed" (shortened), and the link stays
    agent.dashboards.save_err = MergeError("conflict", "edited")
    await run(agent, {"type": dashmerge.SAVE, "url_path": "hauflo-x", "config": {}})
    assert sent[-1]["error"]["code"] == "conflict"
    agent.dashboards.save_err = RuntimeError("x" * 500)
    await run(agent, {"type": dashmerge.SAVE, "url_path": "hauflo-x", "config": {}})
    assert sent[-1]["error"]["code"] == "failed" and len(sent[-1]["error"]["message"]) < 260
    assert dashmerge._choice("junk") == [] and dashmerge._choice(True) is True
