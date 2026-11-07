"""Review r3 R3-9: an alarm rule whose alarm_states are not state names is refused with its reason — never quietly
read as «on» (the rule would never fire)."""
from custom_components.hauflo import notify


def _rule(**kw):
    return {"id": "r1", "kind": "leak", "title": "Протечка", "sources": ["sensor.a"], "picked": ["sensor.a"], "recipients": {"all": True}, **kw}


def test_bad_state_names_drop_the_rule_with_a_reason():
    for bad in (["Alarm"], ["water leak"], ["on", "Wet"], "on", ["x" * 33], [1], ["a"] * 9):
        out = notify._clean_rules({"rules": [_rule(alarm_states=bad)]}, None, None)
        assert out["rules"] == [], bad
        assert out["rejected"][0]["id"] == "r1" and out["rejected"][0]["reason"] == "bad_alarm_states", bad


def test_good_or_absent_states_keep_the_rule():
    assert notify._clean_rules({"rules": [_rule(alarm_states=["on", "wet"])]}, None, None)["rules"][0]["alarm_states"] == ["on", "wet"]
    for absent in ({}, {"alarm_states": []}, {"alarm_states": None}):
        out = notify._clean_rules({"rules": [_rule(**absent)]}, None, None)
        assert out["rules"][0]["alarm_states"] == ["on"] and "rejected" not in out


def test_one_bad_rule_does_not_take_the_others():
    out = notify._clean_rules({"rules": [_rule(alarm_states=["Alarm"]), _rule(id="r2", alarm_states=["off"])]}, None, None)
    assert [r["id"] for r in out["rules"]] == ["r2"] and [x["id"] for x in out["rejected"]] == ["r1"]
