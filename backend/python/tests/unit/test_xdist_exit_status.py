"""A configure failure under xdist must not exit 0.

conftest force-exits when a status was never stored. This plugin runs
first, flushes, and records an internal error for that path.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import tests.xdist_exit as xdist_exit

_ROOT_CONFTEST = Path(__file__).resolve().parents[1] / "conftest.py"


class _Stream:
    def __init__(self) -> None:
        self.flushed = False

    def flush(self) -> None:
        self.flushed = True


def test_a_configure_failure_exits_as_an_internal_error(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    conftest = next(
        plugin
        for plugin in request.config.pluginmanager.get_plugins()
        if Path(getattr(plugin, "__file__", "") or "").resolve() == _ROOT_CONFTEST
    )
    codes: list[int] = []

    def record(code: int) -> None:
        codes.append(int(code))

    monkeypatch.setattr(xdist_exit.os, "_exit", record)
    out, err = _Stream(), _Stream()
    monkeypatch.setattr(xdist_exit.sys, "stdout", out)
    monkeypatch.setattr(xdist_exit.sys, "stderr", err)
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)

    config = SimpleNamespace(option=SimpleNamespace(numprocesses=2))
    xdist_exit.pytest_unconfigure(config)
    conftest.pytest_unconfigure(config)

    assert codes == [pytest.ExitCode.INTERNAL_ERROR, pytest.ExitCode.INTERNAL_ERROR]
    assert out.flushed and err.flushed
