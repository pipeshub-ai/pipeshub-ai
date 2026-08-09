"""Parameterized contract suite for `ITaskStore` -- any future adapter
(a Neo4j-backed one, say) proves itself by passing these same tests against
its own fixture, per the plan's Part J testing strategy.

Currently one backend under test: `GraphTaskStore` over `FakeGraphProvider`.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from app.services.tasks.adapters.graph.task_store import GraphTaskStore
from app.services.tasks.domain.errors import (
    OptimisticConcurrencyError,
    TaskNotFoundError,
)
from app.services.tasks.domain.models import (
    TaskDefinition,
    TaskPrincipal,
    TaskQuery,
    TaskStatus,
    TaskStep,
)
from tests.unit.services.tasks.adapters.fakes import FakeGraphProvider

if TYPE_CHECKING:
    from app.services.tasks.interface.task_store import ITaskStore


def _make_task(**overrides) -> TaskDefinition:
    defaults = {
        "org_id": "org-1",
        "created_by_user_id": "user-1",
        "principal": TaskPrincipal(org_id="org-1", user_id="user-1", user_email="a@b.com"),
        "title": "Daily digest",
        "description": "every morning summarize tickets",
        "instructions": "Summarize yesterday's tickets and post to #support",
        "created_at": "2024-01-01T00:00:00+00:00",
        "updated_at": "2024-01-01T00:00:00+00:00",
    }
    defaults.update(overrides)
    return TaskDefinition(**defaults)


@pytest.fixture(params=["graph"])
def task_store(request: pytest.FixtureRequest) -> ITaskStore:
    if request.param == "graph":
        return GraphTaskStore(FakeGraphProvider())
    raise ValueError(request.param)


class TestCreateAndGet:
    async def test_create_then_get_roundtrip(self, task_store: ITaskStore) -> None:
        task = _make_task()
        created = await task_store.create(task)
        assert created.task_id == task.task_id

        fetched = await task_store.get(task.task_id, "org-1")
        assert fetched is not None
        assert fetched.title == "Daily digest"
        assert fetched.revision == 0

    async def test_get_missing_returns_none(self, task_store: ITaskStore) -> None:
        assert await task_store.get("no-such-id", "org-1") is None

    async def test_get_wrong_org_returns_none(self, task_store: ITaskStore) -> None:
        task = _make_task()
        await task_store.create(task)
        # Cross-tenant read must be indistinguishable from "doesn't exist".
        assert await task_store.get(task.task_id, "org-2") is None

    async def test_roundtrip_preserves_nested_structures(self, task_store: ITaskStore) -> None:
        task = _make_task(
            clarifications=[{"question": "which channel?", "answer": "#support"}],
            steps=[
                TaskStep(id="s1", description="fetch tickets", domain="jira"),
                TaskStep(id="s2", description="summarize", domain="llm", depends_on=["s1"]),
            ],
        )
        await task_store.create(task)
        fetched = await task_store.get(task.task_id, "org-1")
        assert fetched.clarifications == [{"question": "which channel?", "answer": "#support"}]
        assert fetched.steps is not None
        assert fetched.steps[1].depends_on == ["s1"]
        assert fetched.retry_policy.max_attempts == task.retry_policy.max_attempts
        assert fetched.budget.max_turns_per_run == task.budget.max_turns_per_run


class TestUpdate:
    async def test_update_with_correct_revision_succeeds(self, task_store: ITaskStore) -> None:
        task = await task_store.create(_make_task())
        updated = task.model_copy(update={"title": "New title"})
        result = await task_store.update(updated, expected_revision=0)
        assert result.revision == 1
        assert result.title == "New title"

        fetched = await task_store.get(task.task_id, "org-1")
        assert fetched.revision == 1
        assert fetched.title == "New title"

    async def test_update_with_stale_revision_raises(self, task_store: ITaskStore) -> None:
        task = await task_store.create(_make_task())
        await task_store.update(task.model_copy(update={"title": "v2"}), expected_revision=0)

        with pytest.raises(OptimisticConcurrencyError):
            await task_store.update(task.model_copy(update={"title": "v3-stale"}), expected_revision=0)

    async def test_update_missing_task_raises(self, task_store: ITaskStore) -> None:
        task = _make_task()
        with pytest.raises(TaskNotFoundError):
            await task_store.update(task, expected_revision=0)


class TestDelete:
    async def test_delete_existing(self, task_store: ITaskStore) -> None:
        task = await task_store.create(_make_task())
        assert await task_store.delete(task.task_id, "org-1") is True
        assert await task_store.get(task.task_id, "org-1") is None

    async def test_delete_missing_returns_false(self, task_store: ITaskStore) -> None:
        assert await task_store.delete("no-such-id", "org-1") is False

    async def test_delete_wrong_org_returns_false(self, task_store: ITaskStore) -> None:
        task = await task_store.create(_make_task())
        assert await task_store.delete(task.task_id, "org-2") is False
        # Must still exist for the real owner.
        assert await task_store.get(task.task_id, "org-1") is not None


class TestList:
    async def test_list_filters_by_org(self, task_store: ITaskStore) -> None:
        await task_store.create(_make_task(org_id="org-1"))
        await task_store.create(_make_task(org_id="org-2"))
        page = await task_store.list(TaskQuery(org_id="org-1"))
        assert page.total == 1
        assert page.items[0].org_id == "org-1"

    async def test_list_filters_by_status(self, task_store: ITaskStore) -> None:
        await task_store.create(_make_task(status=TaskStatus.ACTIVE))
        await task_store.create(_make_task(status=TaskStatus.DRAFT))
        page = await task_store.list(TaskQuery(org_id="org-1", status=TaskStatus.ACTIVE))
        assert page.total == 1
        assert page.items[0].status == TaskStatus.ACTIVE

    async def test_list_pagination(self, task_store: ITaskStore) -> None:
        for i in range(5):
            await task_store.create(_make_task(title=f"task-{i}", created_at=f"2024-01-0{i+1}T00:00:00+00:00"))
        page1 = await task_store.list(TaskQuery(org_id="org-1", limit=2, offset=0))
        assert len(page1.items) == 2
        assert page1.total == 5
        assert page1.has_more is True

        page3 = await task_store.list(TaskQuery(org_id="org-1", limit=2, offset=4))
        assert len(page3.items) == 1
        assert page3.has_more is False

    async def test_list_text_search(self, task_store: ITaskStore) -> None:
        await task_store.create(_make_task(title="Support digest"))
        await task_store.create(_make_task(title="Sales report"))
        page = await task_store.list(TaskQuery(org_id="org-1", text_search="support"))
        assert page.total == 1
        assert "Support" in page.items[0].title
