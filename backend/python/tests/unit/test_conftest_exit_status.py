"""A parallel run's exit status and summary are the run's own.

``tests/conftest.py`` force-exits xdist runs. It exited 0 whatever had
failed, and before the summary had been written out.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from types import ModuleType

_ROOT_CONFTEST = Path(__file__).resolve().parents[1] / "conftest.py"


class _Exited(Exception):
    def __init__(self, code: int) -> None:
        super().__init__(code)
        self.code = code


class _Stream:
    def __init__(self) -> None:
        self.flushed = False

    def flush(self) -> None:
        self.flushed = True


@pytest.fixture
def root_conftest(request: pytest.FixtureRequest) -> ModuleType:
    for plugin in request.config.pluginmanager.get_plugins():
        if Path(getattr(plugin, "__file__", "") or "").resolve() == _ROOT_CONFTEST:
            return plugin
    raise AssertionError("tests/conftest.py is not loaded")


@pytest.fixture
def exits(monkeypatch: pytest.MonkeyPatch, root_conftest: ModuleType) -> None:
    def exit_with(code: int) -> None:
        raise _Exited(code)

    monkeypatch.setattr(root_conftest.os, "_exit", exit_with)


def _config(**fields: object) -> SimpleNamespace:
    return SimpleNamespace(option=SimpleNamespace(numprocesses=2), **fields)


@pytest.mark.parametrize("status", [pytest.ExitCode.OK, pytest.ExitCode.TESTS_FAILED, pytest.ExitCode.INTERRUPTED])
def test_a_parallel_run_exits_with_its_own_status(
    root_conftest: ModuleType, exits: None, status: pytest.ExitCode
) -> None:
    config = _config()
    root_conftest.pytest_sessionfinish(SimpleNamespace(config=config), status)

    with pytest.raises(_Exited) as exited:
        root_conftest.pytest_unconfigure(config)

    assert exited.value.code == status


def test_the_summary_is_flushed_before_the_exit(
    root_conftest: ModuleType, exits: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Set here, not in a fixture: output capture swaps the streams again when the test starts.
    out, err = _Stream(), _Stream()
    monkeypatch.setattr(root_conftest.sys, "stdout", out)
    monkeypatch.setattr(root_conftest.sys, "stderr", err)

    with pytest.raises(_Exited):
        root_conftest.pytest_unconfigure(_config(_exitcode=0))

    assert out.flushed and err.flushed


def test_a_run_that_never_finished_does_not_pass(root_conftest: ModuleType, exits: None) -> None:
    with pytest.raises(_Exited) as exited:
        root_conftest.pytest_unconfigure(_config())

    assert exited.value.code == pytest.ExitCode.INTERNAL_ERROR


def test_a_serial_run_is_left_to_exit_normally(
    root_conftest: ModuleType, exits: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)

    root_conftest.pytest_unconfigure(SimpleNamespace(option=SimpleNamespace(numprocesses=None)))
