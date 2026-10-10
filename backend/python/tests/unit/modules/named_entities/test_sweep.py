import logging
from unittest.mock import AsyncMock

import pytest

from app.modules.named_entities.sweep import MAX_BATCHES_PER_PASS, NamedEntitySweeper

HOUR = 3_600_000
NOW = 10_000 * HOUR
GRACE = 24 * HOUR


class _Deadlock(Exception):
    pass


class FakeGraph:
    """Nodes: key -> {"org", "linked", "orphanedAt", "kind"}. Mirrors the provider contract."""

    def __init__(self):
        self.nodes: dict[str, dict] = {}
        self.dangling: dict[str, int] = {}
        self.dangling_values = 0
        self.orgs = [{"_key": "org-1"}]
        self.deleted_calls: list[int | None] = []

    def is_named_entity_write_retryable(self, error):
        return isinstance(error, _Deadlock)

    def add(self, key, *, org="org-1", linked=False, orphaned_at=None, kind="date"):
        self.nodes[key] = {"org": org, "linked": linked, "orphanedAt": orphaned_at, "kind": kind}

    async def get_all_orgs(self, *, active=True):
        return self.orgs

    async def clear_orphan_named_entity_marks(self, org_id, batch_size=200):
        hit = [k for k, n in self.nodes.items() if n["org"] == org_id and n["orphanedAt"] is not None and n["linked"]]
        for key in hit[:batch_size]:
            self.nodes[key]["orphanedAt"] = None
        return len(hit[:batch_size])

    async def mark_orphan_named_entities(self, org_id, now_ms, batch_size=200):
        hit = [k for k, n in self.nodes.items() if n["org"] == org_id and n["orphanedAt"] is None and not n["linked"]]
        for key in hit[:batch_size]:
            self.nodes[key]["orphanedAt"] = now_ms
        return len(hit[:batch_size])

    def _orphans(self, org_id, marked_before_ms, entity_ids=None, skip_kinds=None):
        return [
            k for k, n in self.nodes.items()
            if n["org"] == org_id and not n["linked"]
            and (entity_ids is None or k in entity_ids)
            and (skip_kinds is None or n["kind"] not in skip_kinds)
            and (marked_before_ms is None or (n["orphanedAt"] is not None and n["orphanedAt"] <= marked_before_ms))
        ]

    async def find_orphan_named_entities(self, org_id, batch_size=200, marked_before_ms=None, skip_kinds=None):
        return self._orphans(org_id, marked_before_ms, skip_kinds=skip_kinds)[:batch_size]

    async def delete_orphan_named_entities(self, org_id, batch_size=200, marked_before_ms=None, entity_ids=None):
        self.deleted_calls.append(marked_before_ms)
        hit = self._orphans(org_id, marked_before_ms, entity_ids)[:batch_size]
        for key in hit:
            del self.nodes[key]
        return hit

    async def delete_dangling_named_entity_mentions(self, entity_ids):
        return sum(self.dangling.get(key, 0) for key in entity_ids if key not in self.nodes)

    async def delete_dangling_named_entity_values(self, org_id, batch_size=200):
        removed = min(self.dangling_values, batch_size)
        self.dangling_values -= removed
        return removed


def _sweeper(graph, vectors=None, batch=200):
    return NamedEntitySweeper(
        graph, vectors, grace_ms=GRACE, batch_size=batch, now_ms=lambda: NOW, logger_=logging.getLogger("test")
    )


async def test_a_fresh_orphan_is_marked_not_deleted():
    graph = FakeGraph()
    graph.add("a")
    result = await _sweeper(graph).sweep_org("org-1")
    assert (result.marked, result.deleted) == (1, 0)
    assert graph.nodes["a"]["orphanedAt"] == NOW


async def test_an_orphan_inside_the_grace_period_survives():
    graph = FakeGraph()
    graph.add("a", orphaned_at=NOW - GRACE + 1)
    result = await _sweeper(graph).sweep_org("org-1")
    assert result.deleted == 0
    assert "a" in graph.nodes


async def test_an_orphan_past_the_grace_period_is_deleted_with_its_vectors():
    graph, vectors = FakeGraph(), AsyncMock()
    graph.add("a", orphaned_at=NOW - GRACE)
    graph.add("b", orphaned_at=NOW - GRACE - HOUR)
    result = await _sweeper(graph, vectors).sweep_org("org-1")
    assert result.deleted == 2
    assert graph.nodes == {}
    vectors.delete_entities.assert_awaited_once_with("org-1", "named_entity", ["a", "b"])


async def test_a_node_a_record_linked_again_is_unmarked_and_kept():
    graph, vectors = FakeGraph(), AsyncMock()
    graph.add("a", linked=True, orphaned_at=NOW - 5 * GRACE)
    result = await _sweeper(graph, vectors).sweep_org("org-1")
    assert (result.cleared, result.deleted) == (1, 0)
    assert graph.nodes["a"]["orphanedAt"] is None
    vectors.delete_entities.assert_not_awaited()


async def test_a_linked_node_is_never_marked():
    graph = FakeGraph()
    graph.add("a", linked=True)
    result = await _sweeper(graph).sweep_org("org-1")
    assert result.marked == 0
    assert graph.nodes["a"]["orphanedAt"] is None


async def test_the_delete_pass_always_carries_a_cutoff():
    graph = FakeGraph()
    graph.add("a", orphaned_at=NOW - GRACE)
    await _sweeper(graph).sweep_org("org-1")
    assert graph.deleted_calls and all(cutoff == NOW - GRACE for cutoff in graph.deleted_calls)


async def test_a_backlog_is_drained_in_batches():
    graph, vectors = FakeGraph(), AsyncMock()
    for index in range(5):
        graph.add(f"n{index}", orphaned_at=NOW - GRACE)
    result = await _sweeper(graph, vectors, batch=2).sweep_org("org-1")
    assert result.deleted == 5
    assert [call.args[2] for call in vectors.delete_entities.await_args_list] == [
        ["n0", "n1"], ["n2", "n3"], ["n4"],
    ]


async def test_one_pass_is_bounded_per_org():
    graph = FakeGraph()
    for index in range((MAX_BATCHES_PER_PASS + 3) * 2):
        graph.add(f"n{index}", orphaned_at=NOW - GRACE)
    result = await _sweeper(graph, None, batch=2).sweep_org("org-1")
    assert result.deleted == MAX_BATCHES_PER_PASS * 2


async def test_another_orgs_nodes_are_untouched():
    graph = FakeGraph()
    graph.add("mine", orphaned_at=NOW - GRACE)
    graph.add("theirs", org="org-2", orphaned_at=NOW - GRACE)
    await _sweeper(graph).sweep_org("org-1")
    assert list(graph.nodes) == ["theirs"]


async def test_a_vector_failure_keeps_the_nodes_for_the_next_pass():
    graph, vectors = FakeGraph(), AsyncMock()
    vectors.delete_entities.side_effect = RuntimeError("qdrant down")
    graph.add("a", orphaned_at=NOW - GRACE)
    graph.add("b", orphaned_at=NOW - GRACE)
    result = await _sweeper(graph, vectors, batch=1).sweep_org("org-1")
    assert (result.deleted, result.vector_failed) == (0, True)
    assert set(graph.nodes) == {"a", "b"}
    assert vectors.delete_entities.await_count == 1

    vectors.delete_entities.side_effect = None
    result = await _sweeper(graph, vectors).sweep_org("org-1")
    assert (result.deleted, result.vector_failed) == (2, False)
    assert graph.nodes == {}
    vectors.delete_entities.assert_awaited_with("org-1", "named_entity", ["a", "b"])


async def test_vectors_are_deleted_before_their_nodes():
    graph, vectors = FakeGraph(), AsyncMock()
    graph.add("a", orphaned_at=NOW - GRACE)
    present_at_vector_delete = []
    vectors.delete_entities.side_effect = lambda *_: present_at_vector_delete.append("a" in graph.nodes)
    await _sweeper(graph, vectors).sweep_org("org-1")
    assert present_at_vector_delete == [True]
    assert graph.nodes == {}


async def test_only_nodes_whose_vectors_were_deleted_are_deleted():
    graph, vectors = FakeGraph(), AsyncMock()
    graph.add("a", orphaned_at=NOW - GRACE)

    def a_is_linked_and_late_falls_idle(*_):
        graph.nodes["a"].update(linked=True, orphanedAt=None)
        graph.add("late", orphaned_at=NOW - GRACE)

    vectors.delete_entities.side_effect = a_is_linked_and_late_falls_idle
    result = await _sweeper(graph, vectors).sweep_org("org-1")
    assert (result.deleted, result.relinked) == (0, 1)
    assert set(graph.nodes) == {"a", "late"}


async def test_without_a_vector_store_only_kinds_without_vectors_are_deleted():
    graph, vectors = FakeGraph(), AsyncMock()
    graph.add("date", orphaned_at=NOW - GRACE, kind="date")
    graph.add("person", orphaned_at=NOW - GRACE, kind="person")
    graph.add("acme", orphaned_at=NOW - GRACE, kind="organization")
    result = await _sweeper(graph, None).sweep_org("org-1")
    assert result.deleted == 2
    assert list(graph.nodes) == ["acme"]

    result = await _sweeper(graph, vectors).sweep_org("org-1")
    assert result.deleted == 1
    vectors.delete_entities.assert_awaited_once_with("org-1", "named_entity", ["acme"])


async def test_value_rows_of_deleted_records_are_removed_in_batches():
    graph = FakeGraph()
    graph.dangling_values = 450
    result = await _sweeper(graph, batch=200).sweep_org("org-1")
    assert (result.values_removed, graph.dangling_values) == (450, 0)


async def test_dangling_edges_to_deleted_nodes_are_counted():
    graph = FakeGraph()
    graph.add("a", orphaned_at=NOW - GRACE)
    graph.dangling["a"] = 2
    result = await _sweeper(graph).sweep_org("org-1")
    assert result.dangling_removed == 2


async def test_a_dangling_edge_failure_does_not_stop_the_pass():
    graph, vectors = FakeGraph(), AsyncMock()
    graph.add("a", orphaned_at=NOW - GRACE)
    graph.add("b", orphaned_at=NOW - GRACE)
    graph.delete_dangling_named_entity_mentions = AsyncMock(side_effect=RuntimeError("arango down"))
    result = await _sweeper(graph, vectors, batch=1).sweep_org("org-1")
    assert (result.deleted, result.dangling_removed) == (2, 0)
    assert graph.nodes == {}


async def test_a_node_linked_after_it_was_found_is_kept():
    graph, vectors = FakeGraph(), AsyncMock()
    graph.add("a", orphaned_at=NOW - GRACE)
    graph.add("b", orphaned_at=NOW - GRACE)
    original = graph.find_orphan_named_entities

    async def a_writer_links_a(*args, **kwargs):
        ids = await original(*args, **kwargs)
        if "a" in ids:
            graph.nodes["a"].update(linked=True, orphanedAt=None)
        return ids

    graph.find_orphan_named_entities = a_writer_links_a
    result = await _sweeper(graph, vectors).sweep_org("org-1")
    assert (result.deleted, result.relinked) == (1, 1)
    assert list(graph.nodes) == ["a"]


async def test_a_node_created_again_after_the_delete_keeps_its_edges():
    graph, vectors = FakeGraph(), AsyncMock()
    graph.add("a", orphaned_at=NOW - GRACE)
    graph.add("b", orphaned_at=NOW - GRACE)
    graph.dangling = {"a": 1, "b": 1}
    original = graph.delete_orphan_named_entities

    async def a_writer_creates_a_again(*args, **kwargs):
        ids = await original(*args, **kwargs)
        if ids:
            graph.add("a", linked=True)
        return ids

    graph.delete_orphan_named_entities = a_writer_creates_a_again
    result = await _sweeper(graph, vectors).sweep_org("org-1")
    assert (result.deleted, result.dangling_removed) == (2, 1)
    assert graph.nodes["a"]["linked"] is True


async def test_sweep_all_continues_after_one_org_fails():
    graph = FakeGraph()
    graph.orgs = [{"_key": "bad"}, {"id": "org-1"}, {}]
    graph.add("a", orphaned_at=NOW - GRACE)
    original = graph.clear_orphan_named_entity_marks

    async def failing(org_id, batch_size=200):
        if org_id == "bad":
            raise RuntimeError("boom")
        return await original(org_id, batch_size)

    graph.clear_orphan_named_entity_marks = failing
    result = await _sweeper(graph).sweep_all()
    assert result.deleted == 1


@pytest.mark.parametrize("value", ["abc", "", None, "inf", "-inf", "nan"])
def test_loop_env_parsing_falls_back(monkeypatch, value):
    from app.modules.indexing.named_entity_sweep import _env_float

    if value is None:
        monkeypatch.delenv("X_SWEEP", raising=False)
    else:
        monkeypatch.setenv("X_SWEEP", value)
    assert _env_float("X_SWEEP", 7.0) == 7.0


async def test_a_deadlock_with_a_writer_is_retried_not_fatal(monkeypatch):
    from app.modules.named_entities import write_retry

    monkeypatch.setattr(write_retry, "_backoff", AsyncMock())
    graph = FakeGraph()
    graph.add("a", orphaned_at=NOW - GRACE - 1)
    graph.add("b")
    real_delete, real_mark = graph.delete_orphan_named_entities, graph.mark_orphan_named_entities
    failures = {"delete": 1, "mark": 1}

    async def delete(*args, **kwargs):
        if failures["delete"]:
            failures["delete"] -= 1
            raise _Deadlock("Neo.TransientError.Transaction.DeadlockDetected")
        return await real_delete(*args, **kwargs)

    async def mark(*args, **kwargs):
        if failures["mark"]:
            failures["mark"] -= 1
            raise _Deadlock("deadlock")
        return await real_mark(*args, **kwargs)

    graph.delete_orphan_named_entities, graph.mark_orphan_named_entities = delete, mark
    result = await _sweeper(graph).sweep_org("org-1")
    assert (result.deleted, result.marked) == (1, 1)

