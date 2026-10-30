"""Natural order of every list a person reads (natural.py): blinds 1, 2, 10 — not 1, 10, 2."""

from custom_components.hauflo.natural import natural_key


def test_numbers_compare_as_numbers():
    names = ["Штора 10", "Штора 2", "Штора 1", "Штора 12", "Штора 11"]
    assert sorted(names, key=natural_key) == ["Штора 1", "Штора 2", "Штора 10", "Штора 11", "Штора 12"]


def test_case_and_mixed_parts():
    assert sorted(["room 10", "Room 9", "room 1b", "Room 1a"], key=natural_key) == ["Room 1a", "room 1b", "Room 9", "room 10"]
    assert sorted(["b", "A", "c"], key=natural_key) == ["A", "b", "c"]


def test_none_and_empty_do_not_crash():
    assert sorted([None, "", "x 1"], key=natural_key)[-1] == "x 1"
