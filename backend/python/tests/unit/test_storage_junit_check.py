"""The skip check of the storage integration job (tests/integration/compose/check_storage_junit.py)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "integration" / "compose" / "check_storage_junit.py"
MODULE = "tests.integration.test_x"


def _run(tmp_path: Path, cases: str, store: str, discovered: str = "tests/integration/test_x.py") -> subprocess.CompletedProcess[str]:
    report = tmp_path / "r.xml"
    report.write_text(f'<testsuites><testsuite name="pytest">{cases}</testsuite></testsuites>')
    listing = tmp_path / "d.txt"
    listing.write_text(discovered)
    env = {"REPORT": str(report), "DISCOVERED": str(listing), "STORAGE_TYPE": store}
    return subprocess.run([sys.executable, "-I", str(SCRIPT)], env=env, capture_output=True, text=True, check=False)


def _ok(name: str = "a") -> str:
    return f'<testcase classname="{MODULE}" name="{name}"/>'


def _skip(message: str, name: str = "s") -> str:
    return f'<testcase classname="{MODULE}" name="{name}"><skipped type="pytest.skip" message="{message}"/></testcase>'


def test_all_tests_ran(tmp_path: Path) -> None:
    assert _run(tmp_path, _ok(), "local").returncode == 0


def test_a_skip_fails(tmp_path: Path) -> None:
    result = _run(tmp_path, _ok() + _skip("service unreachable"), "local")
    assert result.returncode == 1 and "1 skipped" in result.stdout


def test_not_applicable_on_this_store_is_allowed(tmp_path: Path) -> None:
    cases = _ok() + _skip("not applicable on local: local storage serves the record inline")
    assert _run(tmp_path, cases, "local").returncode == 0


@pytest.mark.parametrize("leg", ["s3", "azureBlob"])
def test_not_applicable_naming_another_store_fails(tmp_path: Path, leg: str) -> None:
    cases = _ok() + _skip("not applicable on local: local storage serves the record inline")
    result = _run(tmp_path, cases, leg)
    assert result.returncode == 1 and "1 skipped" in result.stdout


@pytest.mark.parametrize("message", ["not applicable on local", "Not applicable on local: x", "x not applicable on local: y", ""])
def test_only_the_exact_prefix_counts(tmp_path: Path, message: str) -> None:
    assert _run(tmp_path, _ok() + _skip(message), "local").returncode == 1


def test_a_discovered_file_with_no_test_case_fails(tmp_path: Path) -> None:
    result = _run(tmp_path, _ok(), "local", discovered="tests/integration/test_x.py tests/integration/test_unmarked.py")
    assert result.returncode == 1 and "tests.integration.test_unmarked" in result.stdout


def test_a_file_whose_name_only_starts_like_a_discovered_one_does_not_count(tmp_path: Path) -> None:
    other = '<testcase classname="tests.integration.test_x_more" name="a"/>'
    assert _run(tmp_path, other, "local").returncode == 1


def test_nothing_ran_fails(tmp_path: Path) -> None:
    assert _run(tmp_path, "", "local", discovered="").returncode == 1
