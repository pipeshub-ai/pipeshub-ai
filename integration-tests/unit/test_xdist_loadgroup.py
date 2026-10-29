"""The installed pytest and pytest-xdist still honour the root conftest's serial pin.

The root conftest pins every connector test to one worker by adding an
``xdist_group`` mark from a ``tryfirst`` collection hook and running with
``--dist loadgroup``. xdist turns that mark into a ``nodeid@group`` suffix by
writing a private pytest attribute, so a pytest release can break the pin with
no error: the scheduler sees no groups, splits each ordered class across
workers, and the later tests fail on state an earlier test set on the other
worker. pytest 9.2 did exactly that. This runs the same mechanism in a child
pytest so the helper unit step fails before the live suites start.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("xdist")

_CONFTEST = '''
import pytest


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config, items):
    for item in items:
        item.add_marker(pytest.mark.xdist_group("serial"))
'''

_TESTS = "".join(f"def test_{i}():\n    pass\n\n\n" for i in range(6))

_RESULT_LINE = re.compile(r"^\[(gw\d+)\] \[\s*\d+%\] PASSED (\S+)", re.MULTILINE)


def test_xdist_group_added_at_collection_keeps_tests_on_one_worker(tmp_path: Path) -> None:
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    (tmp_path / "conftest.py").write_text(_CONFTEST)
    (tmp_path / "test_pinned.py").write_text(_TESTS)
    env = {k: v for k, v in os.environ.items() if k != "PYTEST_ADDOPTS"}
    # Third-party plugins are irrelevant here and slow every worker's start-up.
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"

    run = subprocess.run(
        [
            sys.executable, "-m", "pytest",
            "-p", "xdist", "-p", "no:cacheprovider", "--color=no",
            "-n", "2", "--dist", "loadgroup", "-v",
            "-c", str(tmp_path / "pytest.ini"), "--rootdir", str(tmp_path),
            str(tmp_path),
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert run.returncode == 0, run.stdout + run.stderr
    results = _RESULT_LINE.findall(run.stdout)
    assert len(results) == 6, run.stdout
    unpinned = [nodeid for _, nodeid in results if not nodeid.endswith("@serial")]
    assert not unpinned, (
        "xdist no longer applies xdist_group marks added at collection, so "
        "--dist loadgroup spreads the connector suites across workers. "
        f"Unpinned: {unpinned}"
    )
    assert len({worker for worker, _ in results}) == 1, run.stdout
