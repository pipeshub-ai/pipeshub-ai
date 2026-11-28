"""The sim tiers collection hook (helper/sim_tiers.py) inside a real pytest run.

scripts/test_sim_tiers.py covers the selection rules as a pure function. This drives the hook itself on a throwaway
project, as the workflow runs it: deselection next to ``-m``, the same cases on every xdist worker, and a loud stop
on a broken tiers file instead of a run that quietly keeps the wrong cases.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

HOOK = Path(__file__).resolve().parent.parent / "helper" / "sim_tiers.py"

TESTS = '''
import pytest

pytestmark = pytest.mark.rot_sim

@pytest.mark.sim_core
def test_core():
    pass

@pytest.mark.parametrize("n", range(40))
def test_other(n):
    pass
'''

LIVE = '''
import pytest

@pytest.mark.live
def test_live():
    pass
'''


def _project(tmp_path: Path, tiers: object) -> Path:
    helper = tmp_path / "helper"
    helper.mkdir()
    (helper / "__init__.py").write_text("")
    shutil.copy(HOOK, helper / "sim_tiers.py")
    (tmp_path / "sim_tiers.json").write_text(json.dumps(tiers) if not isinstance(tiers, str) else tiers)
    (tmp_path / "conftest.py").write_text('pytest_plugins = ("helper.sim_tiers",)\n')
    (tmp_path / "pytest.ini").write_text(
        "[pytest]\nmarkers =\n    rot_sim: a rotating suite\n    sim_core: core\n    live: a live suite\n"
    )
    (tmp_path / "test_rot.py").write_text(TESTS)
    (tmp_path / "test_live.py").write_text(LIVE)
    return tmp_path


def _run(project: Path, *args: str, leg: str = "neo4j") -> subprocess.CompletedProcess[str]:
    """pytest on the throwaway project with no installed plugin but the ones a case asks for (``-p``), so the run
    is fast and the same under the integration helper job and the backend's suite guards."""
    env = {**os.environ, "TEST_GRAPH_DB_TYPE": leg, "PYTHONDONTWRITEBYTECODE": "1", "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}
    env.pop("PYTEST_ADDOPTS", None)
    plugins = ("-p", "xdist.plugin") if "-n" in args else ()
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", *plugins, "-q", *args],
        cwd=project, env=env, capture_output=True, text=True, check=False,
    )


def _passed(result: subprocess.CompletedProcess[str]) -> int:
    match = re.search(r"(\d+) passed", result.stdout)
    return int(match.group(1)) if match else 0


ROTATE = {"schema": 1, "sim_shards": ["connectors-6"], "suites": {"rot_sim": {"pr": "core", "nightly": "rotate"}}}


def test_pull_request_keeps_the_core_and_leaves_other_suites_alone(tmp_path: Path) -> None:
    project = _project(tmp_path, ROTATE)
    result = _run(project, "--sim-tier", "pr")
    assert result.returncode == 0, result.stdout + result.stderr
    assert _passed(result) == 2  # test_core and the live case
    assert "rot_sim runs 1, its tier leaves out 40" in result.stdout


def _selected(result: subprocess.CompletedProcess[str]) -> list[str]:
    return sorted(line.split("@", 1)[0] for line in result.stdout.splitlines() if "::" in line)


def test_a_week_of_nightlies_runs_every_case_once_on_each_leg(tmp_path: Path) -> None:
    project = _project(tmp_path, ROTATE)
    for leg in ("neo4j", "arango"):
        others = 0
        for slot in range(7):
            result = _run(project, "-m", "rot_sim", "--sim-tier", "nightly", "--sim-slot", str(slot), leg=leg)
            assert result.returncode == 0, result.stdout + result.stderr
            others += _passed(result) - 1  # the core case runs every night
        assert others == 40, leg


def test_a_suite_can_keep_its_rotation_to_one_leg(tmp_path: Path) -> None:
    tiers = {**ROTATE, "suites": {"rot_sim": {"pr": "core", "nightly": "rotate", "rotate_legs": ["neo4j"]}}}
    project = _project(tmp_path, tiers)
    for slot in range(7):
        result = _run(project, "-m", "rot_sim", "--sim-tier", "nightly", "--sim-slot", str(slot), leg="arango")
        assert _passed(result) == 1  # the core case only


def test_serial_parallel_and_collect_only_keep_the_same_cases(tmp_path: Path) -> None:
    """The three ways a shard collects keep the same cases, with whatever pytest and xdist are installed.

    Whether xdist appends ``@serial`` to node ids depends on their versions (pytest 9.1 does, 9.2 does not);
    ``scripts/test_sim_tiers.py`` pins that the case id ignores the suffix on a hand-built item, whatever they do.
    """
    pytest.importorskip("xdist")
    project = _project(tmp_path, ROTATE)
    # The repository's own conftest pins cases to one xdist group, which makes xdist add "@serial" to node ids.
    (project / "conftest.py").write_text(
        'import pytest\n\npytest_plugins = ("helper.sim_tiers",)\n\n\n'
        "@pytest.hookimpl(tryfirst=True)\n"
        "def pytest_collection_modifyitems(config, items):\n"
        "    for item in items:\n"
        '        item.add_marker(pytest.mark.xdist_group("serial"))\n'
    )
    args = ("-m", "rot_sim", "--sim-tier", "nightly", "--sim-slot", "4", "-rA")
    collected = _run(project, "--collect-only", *args[:-1])
    serial = _run(project, *args)
    parallel = _run(project, *args, "-n", "2", "--dist", "loadgroup")
    assert serial.returncode == parallel.returncode == collected.returncode == 0, parallel.stdout + parallel.stderr
    planned = _selected(collected)
    passed = lambda result: sorted(
        line.split()[1].split("@", 1)[0] for line in result.stdout.splitlines() if line.startswith("PASSED ")
    )
    assert planned and passed(serial) == planned
    assert passed(parallel) == planned


def test_a_full_run_keeps_everything(tmp_path: Path) -> None:
    assert _passed(_run(_project(tmp_path, ROTATE), "--sim-tier", "all")) == 42


@pytest.mark.parametrize(
    "tiers, args, message",
    [
        ("{not json", ("--sim-tier", "pr"), "cannot read"),
        ({**ROTATE, "schema": 2}, ("--sim-tier", "pr"), "schema"),
        (ROTATE, ("--sim-tier", "nightly"), "needs --sim-slot"),
        (ROTATE, ("--sim-tier", "nightly", "--sim-slot", "9"), "--sim-slot must be"),
        (ROTATE, ("--sim-tier", "nightly", "--sim-slot", "Tuesday"), "--sim-slot must be"),
    ],
)
def test_a_broken_setup_stops_the_session(tmp_path: Path, tiers: object, args: tuple[str, ...], message: str) -> None:
    result = _run(_project(tmp_path, tiers), *args)
    assert result.returncode == 4, result.stdout + result.stderr  # pytest's usage error
    assert message in result.stderr + result.stdout


def test_a_broken_setup_is_a_usage_error_under_xdist_too(tmp_path: Path) -> None:
    pytest.importorskip("xdist")
    result = _run(_project(tmp_path, ROTATE), "--sim-tier", "nightly", "-n", "2")
    assert result.returncode == 4, result.stdout + result.stderr
    assert "needs --sim-slot" in result.stderr + result.stdout
    assert "INTERNALERROR" not in result.stdout + result.stderr


def test_a_case_of_two_suites_stops_the_session(tmp_path: Path) -> None:
    tiers = {**ROTATE, "suites": {**ROTATE["suites"], "live": {"pr": "all", "nightly": "all"}}}
    project = _project(tmp_path, tiers)
    (project / "test_both.py").write_text("import pytest\n\n@pytest.mark.rot_sim\n@pytest.mark.live\ndef test_both():\n    pass\n")
    result = _run(project, "--sim-tier", "pr")
    assert result.returncode == 4
    assert "two simulator suites" in result.stderr + result.stdout
