"""Unit tests for `GraphTimelineStore` -- same `TimelineStore` contract
`InMemoryTimelineStore` satisfies, run against a fake `IGraphDBProvider`.
"""
from __future__ import annotations

import pytest

from app.agent_loop_lib.modules.stores.state.base import AgentStatus
from app.agent_loop_lib.modules.stores.timeline.base import TimelineEntry
from app.agent_loop_lib.modules.stores.timeline.graph_store import GraphTimelineStore
from tests.unit.agent_loop_lib.modules.stores.fakes import FakeGraphProvider


def _make_entry(**overrides) -> TimelineEntry:
    defaults = {
        "sequence_id": 1,
        "trace_id": "trace-1",
        "run_id": "run-1",
        "agent_id": "agent-1",
        "timestamp": "2024-01-01T00:00:00+00:00",
        "status": AgentStatus.RUNNING_TOOL,
        "event_type": "tool_call",
        "summary": "called a tool",
        "detail": {"tool": "echo"},
    }
    defaults.update(overrides)
    return TimelineEntry(**defaults)


@pytest.fixture
def store() -> GraphTimelineStore:
    return GraphTimelineStore(FakeGraphProvider(), org_id="org-1")


class TestAppendAndRead:
    async def test_append_then_get_by_run(self, store: GraphTimelineStore) -> None:
        entry = _make_entry()
        await store.append(entry)

        entries = await store.get_by_run("run-1")
        assert len(entries) == 1
        assert entries[0].entry_id == entry.entry_id
        assert entries[0].summary == "called a tool"
        assert entries[0].detail == {"tool": "echo"}
        assert entries[0].status == AgentStatus.RUNNING_TOOL

    async def test_get_by_run_returns_ordered_by_sequence(self, store: GraphTimelineStore) -> None:
        await store.append(_make_entry(sequence_id=2, summary="second"))
        await store.append(_make_entry(sequence_id=1, summary="first"))

        entries = await store.get_by_run("run-1")
        assert [e.summary for e in entries] == ["first", "second"]

    async def test_get_by_trace_scoped_correctly(self, store: GraphTimelineStore) -> None:
        await store.append(_make_entry(trace_id="trace-1", sequence_id=1))
        await store.append(_make_entry(trace_id="trace-2", sequence_id=1))

        assert len(await store.get_by_trace("trace-1")) == 1
        assert len(await store.get_by_trace("trace-2")) == 1

    async def test_get_by_run_empty_when_no_entries(self, store: GraphTimelineStore) -> None:
        assert await store.get_by_run("no-such-run") == []


class TestClear:
    async def test_clear_removes_entries_for_trace(self, store: GraphTimelineStore) -> None:
        await store.append(_make_entry(trace_id="trace-1", sequence_id=1))
        await store.append(_make_entry(trace_id="trace-1", sequence_id=2))
        await store.append(_make_entry(trace_id="trace-2", sequence_id=1))

        await store.clear("trace-1")

        assert await store.get_by_trace("trace-1") == []
        assert len(await store.get_by_trace("trace-2")) == 1
