"""A pytest plugin that fails a test during which a Neo4j statement ran on an existing plan for longer than BOUND_S.

Load it with ``-p tests.integration.kh_browse_cost.statement_trace``. Neo4j caches one plan per statement text and parameter types,
and plans again only after ``dbms.cypher.min_replan_interval`` (Compose ships 30 min) and a large shift in statistics. A plan chosen
while an App was small is then kept for every App: a batch check of a few hundred ids took a minute on it, where a fresh plan takes
milliseconds. Run the Knowledge Hub tests in one process against a Neo4j configured as Compose ships it, and such a plan shows up
as a slow statement here.

The first run of each statement text is not timed: it includes planning, which takes seconds for the legacy browse's large
statements on any plan. Every later run reuses that plan, and a healthy one takes about a second at most on these worlds.
"""

from __future__ import annotations

import hashlib
import time
from typing import TYPE_CHECKING, Any

import pytest

from app.services.graph_db.neo4j.neo4j_client import Neo4jClient

if TYPE_CHECKING:
    from collections.abc import Generator

BOUND_S = 30.0

_execute = Neo4jClient.execute_query
_planned: set[str] = set()
_slow: list[str] = []


def _key(query: str, parameters: dict[str, Any] | None) -> str:
    # The plan cache key: the text and each parameter's type (as `Neo4jProvider._kh_run` keys its planner).
    types = sorted((k, type(v).__name__) for k, v in (parameters or {}).items())
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
        if timed and took > BOUND_S:
            _slow.append(f"{took:.1f} s, {len(query)} bytes, {key[:12]}: {' '.join(query.split())[:200]}")


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
    # Statements run by the last module's teardown, after every test's own check.
    if _slow:
        session.config.get_terminal_writer().line(_report(), red=True)
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
