"""A fixture teardown that hangs after its test failed still prints every thread's stack (faulthandler_timeout)."""

from pathlib import Path

import pytest

pytest_plugins = ("pytester",)

_ROOT = Path(__file__).resolve().parents[2]


def test_a_teardown_that_hangs_after_a_failure_still_dumps_its_stack(pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYTHONPATH", str(_ROOT))
    pytester.makeconftest("from tests.hang_dump import pytest_exception_interact  # noqa: F401\n")
    pytester.makepyfile(test_probe="""
import time

import pytest


@pytest.fixture
def stuck_teardown():
    yield
    time.sleep(4)


def test_fails(stuck_teardown):
    assert False
""")
    result = pytester.runpytest_subprocess("-p", "no:cacheprovider", "-p", "no:timeout", "-o", "faulthandler_timeout=1")
    result.assert_outcomes(failed=1)
    assert "Timeout (0:00:01)!" in result.stderr.str()


def test_a_failure_with_the_faulthandler_plugin_off_is_reported_normally(pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYTHONPATH", str(_ROOT))
    pytester.makeconftest("from tests.hang_dump import pytest_exception_interact  # noqa: F401\n")
    pytester.makepyfile(test_probe="def test_fails():\n    assert False\n")
    result = pytester.runpytest_subprocess("-p", "no:cacheprovider", "-p", "no:timeout", "-p", "no:faulthandler")
    result.assert_outcomes(failed=1)
    assert "INTERNALERROR" not in result.stdout.str() + result.stderr.str()
