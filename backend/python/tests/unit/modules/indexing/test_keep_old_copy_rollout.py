"""The parts of keeping the old copy that guard the rollout and the readers: the retired-copy grace, the cache drop, the scope fence,
the sweeps that need the protocol tag, and the order that is kept when the flag is off."""
import ast
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import Connectors
from app.exceptions.indexing_exceptions import SupersededRun
from app.modules.indexing import keep_old_copy
from app.services.graph_db.common.record_visibility import RecordVisibility
from tests.support.swap_world import FakeGraph, committer, ctx_for

APP = Path(__file__).resolve().parents[4] / "app"


def _paged_graph(rows: list[dict]) -> AsyncMock:
    graph = AsyncMock()

    async def paged(collection: str, skip: int = 0, limit: int = 50, **_: object) -> list[dict]:
        return rows[skip: skip + limit]

    async def in_use(vrids: list[str], *, visibility: RecordVisibility = RecordVisibility.LIVE, transaction: object = None) -> set[str]:
        return set()  # nothing holds any of them

    graph.get_documents_paginated = AsyncMock(side_effect=paged)
    graph.get_virtual_record_ids_in_use = AsyncMock(side_effect=in_use)
    return graph


@pytest.mark.asyncio
async def test_the_orphan_sweep_keeps_a_copy_a_record_has_just_left_until_the_grace_is_over() -> None:
    """Scenarios 19 and 26: after a commit the old copy has no holder, but a reader's cached map may still name it."""
    from app.indexing_main import _sweep_orphaned_virtual_record_mappings

    now_ms = time.time() * 1000
    rows = [
        {"_key": "just-retired", "orgId": "o1", keep_old_copy.RETIRED_AT_FIELD: now_ms - 1000},
        {"_key": "retired-long-ago", "orgId": "o1", keep_old_copy.RETIRED_AT_FIELD: now_ms - (keep_old_copy.grace_seconds() + 60) * 1000},
        {"_key": "never-retired", "orgId": "o1"},
    ]
    pipeline = AsyncMock()
    pipeline.rewrite_or_delete_vector_membership = AsyncMock(return_value="deleted")
    swept = await _sweep_orphaned_virtual_record_mappings(
        graph_provider=_paged_graph(rows), pipeline=pipeline, logger=MagicMock(), page_size=100, org_id="o1",
    )
    assert swept == 2
    assert [c.args[0] for c in pipeline.rewrite_or_delete_vector_membership.await_args_list] == ["retired-long-ago", "never-retired"]


@pytest.mark.parametrize(("ttl", "pg_state", "expected"), [("300", False, 330.0), ("100", False, 130.0), (None, True, 90.0), ("500", True, 90.0)])
def test_the_grace_is_the_cache_ttl_plus_a_margin_in_every_mode(monkeypatch: pytest.MonkeyPatch, ttl: str | None, pg_state: bool, expected: float) -> None:
    """Scenario 38."""
    if ttl is None:
        monkeypatch.delenv("PIPESHUB_ACCESSIBLE_RECORDS_CACHE_TTL", raising=False)
    else:
        monkeypatch.setenv("PIPESHUB_ACCESSIBLE_RECORDS_CACHE_TTL", ttl)
    monkeypatch.setattr("app.services.redis.connection_provider_factory.uses_pg_state", lambda: pg_state)
    assert keep_old_copy.grace_seconds() == expected


@pytest.mark.asyncio
async def test_a_commit_drops_the_records_cache_entry_at_once_and_not_through_the_coalesced_path() -> None:
    """Scenario 19: the invalidation of a record that moved to another virtual record is not coalesced like the one of a new record."""
    from app.services.cache.accessible_records_cache import AccessibleRecordsInvalidator

    invalidator = AccessibleRecordsInvalidator.__new__(AccessibleRecordsInvalidator)
    invalidator.cache = MagicMock()
    invalidator.cache.invalidate_kb = AsyncMock()
    invalidator.cache.invalidate_connector = AsyncMock()
    invalidator.logger = MagicMock()
    invalidator._on_connector_record_indexed = AsyncMock()
    await invalidator.on_record_vrid_moved(connector_name=Connectors.KNOWLEDGE_BASE.value, connector_id="kb1", org_id="o1")
    invalidator.cache.invalidate_kb.assert_awaited_once_with("o1", "kb1")
    await invalidator.on_record_vrid_moved(connector_name=Connectors.GOOGLE_DRIVE.value, connector_id="c1", org_id="o1")
    invalidator.cache.invalidate_connector.assert_awaited_once_with("o1", "c1")
    invalidator._on_connector_record_indexed.assert_not_called()  # the coalesced path


@pytest.mark.asyncio
async def test_with_scope_fields_on_the_commit_and_each_twin_move_run_inside_the_fence_of_the_copy_they_leave() -> None:
    """Scenario 20: the records that keep sharing the old copy are registered before the record or a twin leaves it."""
    entered: list[tuple[str, str]] = []

    class Fence:
        def __init__(self, graph: object, record_id: str, vrid: str, logger: object) -> None:
            self.args = (record_id, vrid)

        async def __aenter__(self) -> None:
            entered.append(self.args)

        async def __aexit__(self, *_: object) -> None:
            return None

    g = FakeGraph({
        "A": {"orgId": "o", "virtualRecordId": "X", "pendingVirtualRecordId": "Y", "indexingStatus": "IN_PROGRESS", "md5Checksum": "m", "connectorId": "k1"},
        "B": {"orgId": "o", "virtualRecordId": "X", "indexingStatus": "COMPLETED", "md5Checksum": "m", "connectorId": "k1"},
    })
    swap, _ = committer(g)
    with patch("app.modules.transformers.swap_commit.scope_fields_enabled", return_value=True), \
         patch("app.modules.transformers.swap_commit.leaving_vrid", Fence), \
         patch("app.modules.transformers.swap_commit.sync_vector_membership", AsyncMock()):
        run = keep_old_copy.SwapRun("A", "o", "X", "Y", True)
        assert await swap.commit(ctx_for("A", "Y"), run, {"indexingStatus": "COMPLETED"})
        g.records["A"].pop("pendingVirtualRecordId", None)
        await swap._repoint_twins(ctx_for("A", "Y"), run)
    assert entered == [("A", "X"), ("B", "X")]


@pytest.mark.asyncio
async def test_a_refused_commit_with_scope_fields_on_leaves_the_fence_and_the_record_where_they_were() -> None:
    g = FakeGraph({"A": {"orgId": "o", "virtualRecordId": "X", "pendingVirtualRecordId": "Y2", "indexingStatus": "IN_PROGRESS", "md5Checksum": "m", "connectorId": "k1"}})
    swap, _ = committer(g)
    with pytest.raises(SupersededRun):
        await swap.commit(ctx_for("A", "Y"), keep_old_copy.SwapRun("A", "o", "X", "Y"), {"indexingStatus": "COMPLETED"})
    assert g.records["A"]["virtualRecordId"] == "X"


@pytest.mark.asyncio
async def test_with_the_flag_unset_no_run_asks_the_fleet_and_the_record_never_gets_a_pending_field(monkeypatch: pytest.MonkeyPatch) -> None:
    """Scenario 28: the default is today's order, whatever the broker or the membership says."""
    monkeypatch.delenv(keep_old_copy.ENV_ENABLED, raising=False)
    reader = MagicMock()
    with patch("app.services.messaging.membership.reader_for", return_value=reader):
        assert not await keep_old_copy.swap_allowed(MagicMock())
    reader.strict_verdict.assert_not_called()


def _referenced_in(names: tuple[str, ...], roots: tuple[str, ...]) -> list[str]:
    found = []
    for root in roots:
        path = APP / root
        files = [path] if path.is_file() else sorted(path.rglob("*.py"))
        for file in files:
            tree = ast.parse(file.read_text(encoding="utf-8-sig"))
            for node in ast.walk(tree):
                name = node.attr if isinstance(node, ast.Attribute) else node.id if isinstance(node, ast.Name) else None
                if name in names:
                    found.append(f"{file.relative_to(APP)}:{node.lineno}")
    return found


def test_only_the_indexing_service_sweeps_virtual_records_from_their_mapping_rows_or_stage_states() -> None:
    """Scenario 36: the connector and query services reach a virtual record only through a record's own ``virtualRecordId``, so they never
    see a pending one and need not carry the protocol tag. A sweep of that kind added there must be added to the tag check first."""
    sweeps = ("get_virtual_record_ids_in_use", "stage_states_unheld_revisions", "stage_states_delete_unheld_revisions",
              "_sweep_orphaned_virtual_record_mappings")
    assert _referenced_in(sweeps, ("connectors", "connectors_main.py", "query_main.py", "agents", "api", "agent_loop_lib", "modules/retrieval")) == []
