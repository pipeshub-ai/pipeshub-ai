"""E2E3-F2: a durable engine run reads whole records, only those its own knowledge calls surfaced (S1c on E1's path).

Found by the tool matrix: ``PipesHubEngineAssembler`` granted engines the knowledge tools but never
``knowledgegraph__fetch_record``. The loop's hooks register it mid-run (``LATE_TOOLS``); an engine runs no loop
hooks and lists its tools once, at connect, so a durable engine could only ever read search snippets.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.agents.actions.knowledge_graph.ops import fetch as fetch_ops
from app.harness.citations import EVENT, CitationJournal
from app.harness.engines.durable import with_record_fetch
from app.harness.engines.record_fetch import NOT_SHOWN
from harness_core.journal.types import JournalEvent
from harness_core.logs import HarnessLog
from harness_core.tools.base import ToolOutput
from harness_core.tools.registry import ToolRegistry
from tests.unit.agents.adapter.conftest import make_context
from tests.unit.harness.engines.knowledge_fixtures import (
    LOCKED,
    Q3,
    KnowledgeGraphSearch,
    Navigate,
)

FETCH = "knowledgegraph__fetch_record"
if TYPE_CHECKING:
    import pytest

LOG = HarnessLog.named("tests.durable_record_fetch")


def _run() -> tuple[ToolRegistry, Any]:
    context = make_context()
    registry = ToolRegistry()
    registry.register_tool(KnowledgeGraphSearch(context.tool_state, {"q3": Q3}))
    return registry, context


def test_f2_the_fetch_is_in_the_grant_from_the_start_when_the_run_searches_knowledge() -> None:
    registry, context = _run()
    granted = with_record_fetch(registry, {}, context, lambda effect: True, LOG)
    assert list(granted) == [FETCH]
    assert granted[FETCH].effect == "read"
    assert granted[FETCH].tool_id == f"pipeshub@dynamic/{FETCH}"
    assert registry.has(FETCH)


def test_f2_no_internal_search_no_fetch() -> None:
    context = make_context()
    assert with_record_fetch(ToolRegistry(), {}, context, lambda effect: True, LOG) == {}


def test_f2_a_profile_without_reads_gets_no_fetch() -> None:
    registry, context = _run()
    assert with_record_fetch(registry, {}, context, lambda effect: effect.effect != "read", LOG) == {}


async def test_f2_an_id_the_run_never_surfaced_is_refused_before_anything_is_read(monkeypatch: pytest.MonkeyPatch) -> None:
    registry, context = _run()
    reads: list[list[str]] = []
    real = fetch_ops.execute_fetch_record

    async def counted(**kwargs: Any) -> Any:  # noqa: ANN401
        reads.append(list(kwargs.get("record_ids") or []))
        return await real(**kwargs)

    monkeypatch.setattr(fetch_ops, "execute_fetch_record", counted)
    _ = with_record_fetch(registry, {}, context, lambda effect: True, LOG)
    fetch = registry.resolve_by_name(FETCH)
    refused = await fetch.execute(record_ids=["rec-q3"])
    assert not refused.success and NOT_SHOWN in str(refused.error)
    assert reads == []
    _ = await registry.resolve_by_name("knowledgegraph__search").execute(q="q3")
    _ = await fetch.execute(record_ids=["rec-q3"])
    assert reads == [["rec-q3"]]


# E2E4 RD: the records a run surfaced survive a re-drive. Each call's citation delta (PA-8) is journaled with its
# outcome and restored by the next attempt's assembler before the run's endpoint serves; the scoped fetch reads it.


def _journaled(delta: Any, seq: int) -> JournalEvent:  # noqa: ANN401
    return JournalEvent(
        org_id="org-1", session_id="ses-1", stream_id="ses-1", seq=seq, run_id="run-1", attempt_id="att-1",
        type=EVENT, critical=False, ts="2026-10-09T00:00:00Z", writer="worker", data=delta,
    )


async def _first_attempt(call: str, **arguments: Any) -> list[JournalEvent]:  # noqa: ANN401
    """One knowledge call in a first attempt, and what its outcome journaled."""
    registry, context = _run()
    registry.register_tool(Navigate(context.tool_state))
    journal = CitationJournal(context, registry=registry, log=LOG)
    _ = await registry.resolve_by_name(call).execute(**arguments)
    delta = journal.delta("call-1")
    assert delta is not None
    return [_journaled(delta, 1)]


async def _redriven(events: list[JournalEvent], monkeypatch: pytest.MonkeyPatch) -> tuple[Any, list[list[str]]]:
    """A fresh attempt's fetch, assembled as ``PipesHubEngineAssembler.build`` does: grant, then restore."""
    registry, context = _run()
    _ = with_record_fetch(registry, {}, context, lambda effect: True, LOG)
    await CitationJournal(context, registry=registry, log=LOG).restore(events)
    reads: list[list[str]] = []

    async def counted(**kwargs: Any) -> Any:  # noqa: ANN401
        reads.append(list(kwargs.get("record_ids") or []))
        return ToolOutput(success=True, data="read"), kwargs.get("citation_ref_mapper")

    monkeypatch.setattr(fetch_ops, "execute_fetch_record", counted)
    return registry.resolve_by_name(FETCH), reads


async def test_rd1_a_record_an_earlier_attempts_search_surfaced_is_fetched_without_searching_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events = await _first_attempt("knowledgegraph__search", q="q3")
    fetch, reads = await _redriven(events, monkeypatch)
    out = await fetch.execute(record_ids=["rec-q3"])
    assert NOT_SHOWN not in str(out.error), out.error
    assert reads == [["rec-q3"]]


async def test_rd2_a_record_an_earlier_attempts_navigate_surfaced_is_fetched(monkeypatch: pytest.MonkeyPatch) -> None:
    events = await _first_attempt("knowledgegraph__navigate", node_id="root")
    fetch, reads = await _redriven(events, monkeypatch)
    _ = await fetch.execute(record_ids=[LOCKED])
    assert reads == [[LOCKED]]


async def test_rd3_a_record_no_attempt_surfaced_is_still_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    events = await _first_attempt("knowledgegraph__search", q="q3")
    fetch, reads = await _redriven(events, monkeypatch)
    refused = await fetch.execute(record_ids=["rec-never-shown"])
    assert not refused.success and NOT_SHOWN in str(refused.error)
    assert reads == []
