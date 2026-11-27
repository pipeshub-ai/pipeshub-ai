"""Under `--dist loadfile`, a file in PER_TEST_FILES spreads over the workers and every other file stays on one."""

from pathlib import Path

import pytest

from tests.xdist_scopes import PER_TEST_FILES

pytest_plugins = ("pytester",)

_ROOT = Path(__file__).resolve().parents[2]
_PROBE = """
import os
import time

import pytest


@pytest.mark.parametrize("case", range(6))
def test_case(case, tmp_path_factory):
    (tmp_path_factory.getbasetemp().parent / f"{__name__}-{case}").write_text(os.environ["PYTEST_XDIST_WORKER"])
    time.sleep(SECONDS)
"""


def test_a_per_test_file_runs_on_several_workers_and_the_others_on_one(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PYTHONPATH", str(_ROOT))
    pytester.makeconftest("from tests.xdist_scopes import pytest_xdist_make_scheduler  # noqa: F401\n")
    split = pytester.path / next(iter(PER_TEST_FILES))
    split.parent.mkdir(parents=True)
    # Slow, so the other worker finishes the fast file and takes some of these.
    split.write_text(_PROBE + "SECONDS = 0.5\n")
    (pytester.path / "test_kept_together.py").write_text(_PROBE + "SECONDS = 0\n")
    basetemp = pytester.path / "basetemp"  # each worker's own basetemp sits under it
    result = pytester.runpytest_subprocess("-p", "no:cacheprovider", "-n", "2", "--dist", "loadfile", f"--basetemp={basetemp}")
    result.assert_outcomes(passed=12)
    workers: dict[str, set[str]] = {}
    for mark in basetemp.glob("test_*-*"):
        workers.setdefault(mark.name.rsplit("-", 1)[0], set()).add(mark.read_text())
    assert workers[split.stem] == {"gw0", "gw1"}
    assert len(workers["test_kept_together"]) == 1
