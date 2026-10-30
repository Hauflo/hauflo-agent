"""tuning.json: every timing / threshold / word list of the modules comes from one checked file."""

import json
import re

import pytest

from custom_components.hauflo import tuning


def test_tuning_loads_and_is_checked(tmp_path):
    assert tuning.TUNING["notify"]["nodata_after_s"] == 900
    bad = tmp_path / "t.json"
    bad.write_text(json.dumps({"version": 1, "x": {"a": -1}}))
    with pytest.raises(ValueError):
        tuning.load(bad)
    bad.write_text(json.dumps({"version": 1, "x": {"w": "("}}))
    with pytest.raises(re.error):
        tuning.load(bad)


def test_modules_read_tuning():
    from custom_components.hauflo import notify, presence

    assert notify.NODATA_AFTER == tuning.TUNING["notify"]["nodata_after_s"]
    assert presence.EVENING == tuple(tuning.TUNING["presence"]["evening"])
