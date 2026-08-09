"""Unit tests for `GraphCheckpointStore` -- same `CheckpointStore` contract
`InMemoryCheckpointStore` satisfies, run against a fake `IGraphDBProvider`.
"""
from __future__ import annotations

import pytest

from app.agent_loop_lib.core.types import Goal
from app.agent_loop_lib.modules.providers.budget.base import BudgetSnapshot
from app.agent_loop_lib.modules.stores.checkpoint.base import (
    AgentCheckpoint,
    CheckpointKind,
)
from app.agent_loop_lib.modules.stores.checkpoint.graph_store import (
    GraphCheckpointStore,
)
from tests.unit.agent_loop_lib.modules.stores.fakes import FakeGraphProvider


def _make_checkpoint(**overrides) -> AgentCheckpoint:
    defaults = {
        "run_id": "run-1",
        "agent_id": "agent-1",
        "trace_id": "trace-1",
        "role_name": "worker",
        "model": "scripted-model",
        "goal": Goal(description="do the thing"),
        "messages": [],
        "turn_index": 0,
        "budget_snapshot": BudgetSnapshot(),
    }
    defaults.update(overrides)
    return AgentCheckpoint(**defaults)


@pytest.fixture
def store() -> GraphCheckpointStore:
    return GraphCheckpointStore(FakeGraphProvider(), org_id="org-1")


class TestSaveLoad:
    async def test_save_then_load_roundtrip(self, store: GraphCheckpointStore) -> None:
        cp = _make_checkpoint()
        checkpoint_id = await store.save(cp)
        assert checkpoint_id == cp.checkpoint_id

        loaded = await store.load(checkpoint_id)
        assert loaded.checkpoint_id == cp.checkpoint_id
        assert loaded.run_id == "run-1"
        assert loaded.goal.description == "do the thing"

    async def test_load_missing_raises_key_error(self, store: GraphCheckpointStore) -> None:
        with pytest.raises(KeyError):
            await store.load("no-such-id")

    async def test_load_wrong_org_raises_key_error(self) -> None:
        graph = FakeGraphProvider()
        owner_store = GraphCheckpointStore(graph, org_id="org-1")
        other_store = GraphCheckpointStore(graph, org_id="org-2")
        cp = _make_checkpoint()
        await owner_store.save(cp)

        with pytest.raises(KeyError):
            await other_store.load(cp.checkpoint_id)


class TestLatestAndHistory:
    async def test_latest_returns_none_when_no_checkpoints(self, store: GraphCheckpointStore) -> None:
        assert await store.latest("no-such-run") is None

    async def test_latest_returns_most_recently_saved(self, store: GraphCheckpointStore) -> None:
        first = _make_checkpoint(turn_index=0, kind=CheckpointKind.TURN_START)
        second = _make_checkpoint(turn_index=0, kind=CheckpointKind.POST_TOOL)
        third = _make_checkpoint(turn_index=1, kind=CheckpointKind.TURN_COMPLETE)
        await store.save(first)
        await store.save(second)
        await store.save(third)

        latest = await store.latest("run-1")
        assert latest is not None
        assert latest.checkpoint_id == third.checkpoint_id

    async def test_history_returns_oldest_first(self, store: GraphCheckpointStore) -> None:
        first = _make_checkpoint(turn_index=0)
        second = _make_checkpoint(turn_index=1)
        await store.save(first)
        await store.save(second)

        history = await store.history("run-1")
        assert [cp.checkpoint_id for cp in history] == [first.checkpoint_id, second.checkpoint_id]

    async def test_history_scoped_to_run_id(self, store: GraphCheckpointStore) -> None:
        await store.save(_make_checkpoint(run_id="run-1"))
        await store.save(_make_checkpoint(run_id="run-2"))

        assert len(await store.history("run-1")) == 1
        assert len(await store.history("run-2")) == 1

    async def test_history_and_latest_are_scoped_to_the_org(self) -> None:
        """`load()` has always checked the org, but the run-keyed reads did
        not, so a run id known to one tenant read another's checkpoints."""
        graph = FakeGraphProvider()
        owner_store = GraphCheckpointStore(graph, org_id="org-1")
        other_store = GraphCheckpointStore(graph, org_id="org-2")
        await owner_store.save(_make_checkpoint(run_id="run-1"))

        assert await other_store.history("run-1") == []
        assert await other_store.latest("run-1") is None


class TestDeleteRun:
    async def test_delete_run_removes_all_checkpoints(self, store: GraphCheckpointStore) -> None:
        await store.save(_make_checkpoint(run_id="run-1", turn_index=0))
        await store.save(_make_checkpoint(run_id="run-1", turn_index=1))

        await store.delete_run("run-1")

        assert await store.history("run-1") == []
        assert await store.latest("run-1") is None

    async def test_delete_run_leaves_other_runs_untouched(self, store: GraphCheckpointStore) -> None:
        await store.save(_make_checkpoint(run_id="run-1"))
        await store.save(_make_checkpoint(run_id="run-2"))

        await store.delete_run("run-1")

        assert await store.history("run-2") != []

    async def test_delete_run_leaves_another_orgs_checkpoints_untouched(self) -> None:
        graph = FakeGraphProvider()
        owner_store = GraphCheckpointStore(graph, org_id="org-1")
        other_store = GraphCheckpointStore(graph, org_id="org-2")
        await owner_store.save(_make_checkpoint(run_id="run-1"))

        await other_store.delete_run("run-1")

        assert await owner_store.history("run-1") != []
