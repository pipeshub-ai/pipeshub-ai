"""G-49: an opt-in lane is run by somebody. A test marked with an opt-in lane marker is deselected from the default
run (tests/support/opt_in_lanes.py ``OPT_IN_LANES``), so each such marker must be registered in pytest.ini and selected with
``-m <marker>`` by the workflow it names; otherwise its tests run nowhere (the C-25 trap, for markers)."""

from __future__ import annotations

import re

from tests.cohesion.support import PYTHON_ROOT, REPO_ROOT
from tests.support.opt_in_lanes import OPT_IN_LANES


def test_g49_every_opt_in_lane_is_registered_and_run_by_its_workflow() -> None:
    ini = (PYTHON_ROOT / "pytest.ini").read_text(encoding="utf-8")
    for marker, workflow in OPT_IN_LANES.items():
        assert re.search(rf"^\s+{marker}:", ini, re.MULTILINE), f"{marker} is not registered in pytest.ini"
        text = (REPO_ROOT / workflow).read_text(encoding="utf-8")
        assert re.search(rf"-m {marker}\b", text), f"{workflow} does not select -m {marker}"


def test_g49_the_ga26_lane_has_tests() -> None:
    marked = [p for p in (PYTHON_ROOT / "tests").rglob("test_*.py") if "pytest.mark.ga26" in p.read_text(encoding="utf-8")]
    assert marked, "no test is marked ga26"
    assert "ga26" not in OPT_IN_LANES, "the GA-26 journeys run in the default gate (R23-FU-GA26)"
