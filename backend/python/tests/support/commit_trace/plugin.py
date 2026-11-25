"""pytest plugin: record every ``InMemoryJournal`` commit request, per test.

For refactors that must not change what a run journals (F1): run the same
suites on the base commit and on the branch, then compare with ``diff.py``::

    PYTHONHASHSEED=0 pytest <suites> -n auto --hypothesis-seed=0 \\
        -p tests.support.commit_trace.plugin --commit-trace-out=<dir>
    python -m tests.support.commit_trace.diff <base-dir> <branch-dir>

Each xdist worker writes ``<dir>/<worker>.jsonl``: one line per test with its
commit requests (command id, events, transition; the attempt token is left out).
"""

from __future__ import annotations

import dataclasses
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Generator

    from harness_core.journal.types import CommitReceipt, CommitRequest

_current = [""]
_records: dict[str, list[object]] = {}


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--commit-trace-out", default=None)


def _plain(value: object) -> object:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        out: dict[str, object] = {"__kind": type(value).__name__}
        for f in dataclasses.fields(value):
            if f.name != "token":
                out[f.name] = _plain(getattr(value, f.name))
        return out
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}  # type: ignore[misc]
    if isinstance(value, list | tuple):
        return [_plain(v) for v in value]  # type: ignore[misc]
    return value


def pytest_configure(config: pytest.Config) -> None:
    if not config.getoption("--commit-trace-out"):
        return
    from harness_core.journal.memory import InMemoryJournal

    original = InMemoryJournal.commit

    async def commit(self: InMemoryJournal, request: CommitRequest) -> CommitReceipt:
        _records.setdefault(_current[0], []).append(_plain(request))
        return await original(self, request)

    InMemoryJournal.commit = commit  # type: ignore[method-assign]


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item: pytest.Item) -> Generator[None]:
    _current[0] = item.nodeid
    yield
    _current[0] = ""


@pytest.hookimpl(tryfirst=True)
def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    # Not pytest_unconfigure: tests/conftest.py ends xdist workers with os._exit there.
    out = session.config.getoption("--commit-trace-out")
    if not out:
        return
    path = Path(out)
    path.mkdir(parents=True, exist_ok=True)
    worker = os.environ.get("PYTEST_XDIST_WORKER", "main")
    with (path / f"{worker}.jsonl").open("w") as fh:
        for nodeid, commits in sorted(_records.items()):
            fh.write(
                json.dumps(
                    {"test": nodeid, "commits": commits}, sort_keys=True, default=str
                )
                + "\n"
            )
