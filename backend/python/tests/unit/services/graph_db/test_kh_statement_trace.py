"""The slow-plan guard's plan key and reset (tests/integration/kh_browse_cost/statement_trace.py), without a Neo4j."""

from __future__ import annotations

from typing import Any

import pytest

from tests.integration.kh_browse_cost import statement_trace as trace


def test_the_size_buckets_are_neo4js() -> None:
    # SizeBucket.computeBucket: exact for 0 and 1, then the next power of ten; its own examples are 7 -> 10, 17 -> 100, 42 -> 100.
    assert [trace._bucket(n) for n in (0, 1, 2, 7, 10, 11, 17, 42, 100, 101, 1000, 1001)] == [
        0, 1, 10, 10, 10, 100, 100, 100, 100, 1000, 1000, 10000]


@pytest.mark.parametrize(("one", "other", "same"), [
    ("a", "b", True),
    ("a", "ab", False),
    ("ab", "abcdefghij", True),
    ("abcdefghij", "abcdefghijk", False),
    (["x"] * 11, ["y"] * 100, True),
    (["x"] * 10, ["x"] * 11, False),
    (["x"], [1], False),
    ({"ids": ["x"]}, {"ids": ["x"] * 500}, True),
    (3, 4, True),
])
def test_a_plan_key_changes_only_where_neo4js_does(one: Any, other: Any, same: bool) -> None:  # noqa: ANN401
    assert (trace._key("RETURN $p", {"p": one}) == trace._key("RETURN $p", {"p": other})) is same


async def test_a_statement_is_not_timed_on_its_first_run_after_the_plans_are_forgotten(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run(*_a: Any, **_k: Any) -> list:  # noqa: ANN401
        return []

    monkeypatch.setattr(trace, "_execute", run)
    monkeypatch.setattr(trace, "_planned", set())
    monkeypatch.setattr(trace, "_slow", [])
    monkeypatch.setattr(trace, "_slowest", [])
    monkeypatch.setattr(trace, "BOUND_S", -1.0)     # every timed run counts as slow
    await trace._timed(None, "RETURN 1")
    assert trace._slow == []                         # the first run plans
    await trace._timed(None, "RETURN 1")
    assert len(trace._slow) == 1                     # the second runs on that plan
    trace._slow.clear()
    trace.forget_plans()
    await trace._timed(None, "RETURN 1")
    assert trace._slow == []                         # the cache was cleared: it plans again
    await trace._timed(None, "RETURN 1")
    assert len(trace._slow) == 1
