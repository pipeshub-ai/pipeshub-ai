"""A pytest plugin that fails a test during which a Neo4j statement ran on an existing plan for longer than BOUND_S.

Load it with ``-p tests.integration.kh_browse_cost.statement_trace``. Neo4j caches one plan per statement text and parameter types,
and plans again only after ``dbms.cypher.min_replan_interval`` (Compose ships 30 min) and a large shift in statistics. A plan chosen
while an App was small is then kept for every App: a batch check of a few hundred ids took a minute on it, where a fresh plan takes
milliseconds. Run the Knowledge Hub tests in one process against a Neo4j configured as Compose ships it, and such a plan shows up
as a slow statement here.

The first run of each statement text is not timed: it includes planning, which takes seconds for the legacy browse's large
statements on any plan. Every later run reuses that plan.
"""

from __future__ import annotations

import hashlib
import time
from typing import TYPE_CHECKING, Any

import pytest

from app.services.graph_db.neo4j.neo4j_client import Neo4jClient

if TYPE_CHECKING:
    from collections.abc import Generator

# A healthy run takes about a second; the slowest on main was a 105 KB legacy statement at 12.7 s. The plan kept from a small App took
# 60-100 s per run.
BOUND_S = 45.0

_execute = Neo4jClient.execute_query
_planned: set[str] = set()
_slow: list[str] = []
_slowest: list[tuple[float, str]] = []


def _type(value: Any) -> str:  # noqa: ANN401
    if isinstance(value, list):
        # Neo4j plans a list parameter again when its size class changes: 0, 1, 2-10, 11-100, ...
        return f"list/{len(value) if len(value) < 2 else len(str(len(value) - 1)) + 1}"
    return type(value).__name__


def _key(query: str, parameters: dict[str, Any] | None) -> str:
    # The plan cache key: the text, and each parameter's type and a list's size class.
    types = sorted((k, _type(v)) for k, v in (parameters or {}).items())
    return hashlib.sha1(f"{query}\n{types}".encode()).hexdigest()


async def _timed(self: Neo4jClient, query: str, parameters: dict[str, Any] | None = None, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
    key = _key(query, parameters)
    # A run that starts before the first one ends may be waiting on that plan too, so only runs after it are timed.
    timed = key in _planned
    start = time.monotonic()
    try:
        return await _execute(self, query, parameters, *args, **kwargs)
    finally:
        took = time.monotonic() - start
        _planned.add(key)
        line = f"{took:.2f} s, {len(query)} bytes, {key[:12]}: {' '.join(query.split())[:200]}"
        if timed:
            _slowest[:] = sorted([*_slowest, (took, line)], reverse=True)[:5]
        if timed and took > BOUND_S:
            _slow.append(line)


def pytest_configure(config: pytest.Config) -> None:
    Neo4jClient.execute_query = _timed


def pytest_unconfigure(config: pytest.Config) -> None:
    Neo4jClient.execute_query = _execute


def _report() -> str:
    found, _slow[:] = list(_slow), []
    return f"{len(found)} statement run(s) on a cached plan took over {BOUND_S:.0f} s:\n" + "\n".join(found)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item: pytest.Item) -> Generator[None, None, None]:
    # Statements run while setting the test up are reported here too.
    yield
    if _slow:
        raise AssertionError(_report())


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    # The headroom under the bound, in every run's log.
    session.config.get_terminal_writer().line(f"slowest statement runs on a cached plan (bound {BOUND_S:.0f} s):\n" +
                                              "\n".join(line for _, line in _slowest))
    # Statements run by the last module's teardown, after every test's own check.
    if _slow:
        session.config.get_terminal_writer().line(_report(), red=True)
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
