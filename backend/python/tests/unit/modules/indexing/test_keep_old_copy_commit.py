"""The commit of a replacement copy, the take-over, the twins and the release of the old copy (real code, in-memory stores)."""
import pytest

from app.exceptions.indexing_exceptions import IndexingError, SupersededRun
from app.modules.indexing import keep_old_copy
from tests.support.swap_world import FakeGraph, committer, ctx_for


@pytest.fixture(autouse=True)
def _no_vector_membership_io(monkeypatch) -> None:
    from unittest.mock import AsyncMock

    monkeypatch.setattr("app.modules.transformers.swap_commit.sync_vector_membership", AsyncMock())


def rec(vrid: str, status: str = "IN_PROGRESS", **more: object) -> dict:
    return {"orgId": "o", "virtualRecordId": vrid, "indexingStatus": status, "md5Checksum": "m", "connectorId": "k1", **more}


def run(forced: bool = False) -> keep_old_copy.SwapRun:
    return keep_old_copy.SwapRun("A", "o", "X", "Y", forced)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["IN_PROGRESS", "QUEUED", "NOT_STARTED"])
async def test_the_commit_moves_the_record_and_removes_the_pending_field(status) -> None:
    g = FakeGraph({"A": rec("X", status, pendingVirtualRecordId="Y")})
    swap, _ = committer(g)
    assert await swap.commit(ctx_for("A", "Y"), run(), {"indexingStatus": "COMPLETED"})
    assert g.records["A"]["virtualRecordId"] == "Y"
    assert "pendingVirtualRecordId" not in g.records["A"]  # removed, never null
    assert g.records["A"]["indexingStatus"] == "COMPLETED"


@pytest.mark.asyncio
async def test_a_record_that_changed_state_refuses_the_commit_and_the_run_ends_superseded() -> None:
    g = FakeGraph({"A": rec("X", "FAILED", pendingVirtualRecordId="Y")})
    swap, _ = committer(g)
    with pytest.raises(SupersededRun):
        await swap.commit(ctx_for("A", "Y"), run(), {"indexingStatus": "COMPLETED"})
    assert g.records["A"]["virtualRecordId"] == "X"  # the old copy is still the record's
    assert "pendingVirtualRecordId" not in g.records["A"]
    swap._release_unheld.assert_awaited_once_with("o", "Y")


@pytest.mark.asyncio
async def test_a_commit_does_not_land_on_a_record_another_run_took_over() -> None:
    g = FakeGraph({"A": rec("X", pendingVirtualRecordId="Y2")})
    swap, _ = committer(g)
    with pytest.raises(SupersededRun):
        await swap.commit(ctx_for("A", "Y"), run(), {"indexingStatus": "COMPLETED"})
    assert g.records["A"]["virtualRecordId"] == "X" and g.records["A"]["pendingVirtualRecordId"] == "Y2"  # the other run's copy is untouched


@pytest.mark.asyncio
async def test_a_deleted_record_returns_false_and_releases_the_run_copy() -> None:
    g = FakeGraph({})
    swap, _ = committer(g)
    assert not await swap.commit(ctx_for("A", "Y"), run(), {"indexingStatus": "COMPLETED"})
    swap._release_unheld.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_copy_whose_points_are_gone_is_not_committed() -> None:
    g = FakeGraph({"A": rec("X", pendingVirtualRecordId="Y")})
    swap, _ = committer(g, points=0)
    with pytest.raises(IndexingError):
        await swap.commit(ctx_for("A", "Y"), run(), {"indexingStatus": "COMPLETED"})
    assert g.records["A"]["virtualRecordId"] == "X"


@pytest.mark.asyncio
async def test_a_forced_commit_moves_the_matching_twin_in_the_same_collection_only() -> None:
    g = FakeGraph({
        "A": rec("Y", pendingVirtualRecordId=None),
        "B": rec("X", "COMPLETED"),                                  # same bytes, same collection: moved
        "C": rec("X", "COMPLETED", connectorId="k2"),                # another collection: keeps X
        "D": rec("X", "COMPLETED", md5Checksum="other"),             # other bytes: keeps X
        "E": rec("X", "IN_PROGRESS"),                                # running: keeps X
    })
    g.records["A"].pop("pendingVirtualRecordId")
    swap, vs = committer(g, collections={"k1": "c1", "k2": "c2"})
    await swap._repoint_twins(ctx_for("A", "Y"), run(forced=True))
    assert [k for k, d in g.records.items() if d["virtualRecordId"] == "Y"] == ["A", "B"]
    assert await g.get_records_by_virtual_record_id("X") == ["C", "D", "E"]  # X survives with its holders


@pytest.mark.asyncio
async def test_the_old_copy_is_deleted_in_this_records_collection_only_while_another_collection_holds_it() -> None:
    g = FakeGraph({"A": rec("Y", "COMPLETED"), "C": rec("X", "COMPLETED", connectorId="k2")})
    swap, vs = committer(g, collections={"k1": "c1", "k2": "c2"})
    await swap.release_old_copy(run(), ctx_for("A", "Y").record)
    vs.purge_record_vectors.assert_awaited_once()  # A's collection loses X's points; c2 keeps them
    swap._release_unheld.assert_not_called()


@pytest.mark.asyncio
async def test_the_old_copy_is_kept_while_a_holder_uses_the_same_collection() -> None:
    g = FakeGraph({"A": rec("Y", "COMPLETED"), "B": rec("X", "COMPLETED")})
    swap, vs = committer(g)
    await swap.release_old_copy(run(), ctx_for("A", "Y").record)
    vs.purge_record_vectors.assert_not_called()
    swap._release_unheld.assert_not_called()


@pytest.mark.asyncio
async def test_an_unheld_old_copy_is_released_whole() -> None:
    g = FakeGraph({"A": rec("Y", "COMPLETED")})
    swap, _ = committer(g)
    await swap.release_old_copy(run(), ctx_for("A", "Y").record)
    swap._release_unheld.assert_awaited_once_with("o", "X")


@pytest.mark.asyncio
async def test_a_record_that_failed_for_good_releases_its_pending_copy() -> None:
    g = FakeGraph({"A": rec("X", "FAILED", pendingVirtualRecordId="Y")})
    swap, _ = committer(g)
    await swap.release_failed("A", "o")
    assert "pendingVirtualRecordId" not in g.records["A"] and g.records["A"]["virtualRecordId"] == "X"
    swap._release_unheld.assert_awaited_once_with("o", "Y")


def test_the_running_copy_is_held_until_the_record_reaches_another_status() -> None:
    from app.services.graph_db.common.record_visibility import holds_vrid

    assert holds_vrid(rec("X", "NOT_STARTED", pendingVirtualRecordId="Y"), "Y")  # edited faster than it indexes still commits
    assert not holds_vrid(rec("X", "FAILED", pendingVirtualRecordId="Y"), "Y")


@pytest.mark.asyncio
async def test_a_partial_copy_is_not_committed() -> None:
    """Every block that was given a point must be there: a copy that lost some blocks (a partial delete) is retried, not served."""
    g = FakeGraph({"A": rec("X", pendingVirtualRecordId="Y")})
    swap, _ = committer(g, view=lambda vrid: ["b1"])
    with pytest.raises(IndexingError):
        await swap.commit(ctx_for("A", "Y", pointed=["b1", "b2"]), run(), {"indexingStatus": "COMPLETED"})
    assert g.records["A"]["virtualRecordId"] == "X" and g.records["A"]["pendingVirtualRecordId"] == "Y"


@pytest.mark.asyncio
async def test_a_copy_taken_between_the_read_and_the_write_is_undone_while_the_old_copy_is_there() -> None:
    """The record names the copy once the write lands, so a read after it is final: nothing there means the move goes back to the old copy."""
    g = FakeGraph({"A": rec("X", pendingVirtualRecordId="Y")})
    reads = iter([["b1"], []])  # the read before the commit sees the set; the read after it does not
    swap, _ = committer(g, view=lambda vrid: next(reads))
    with pytest.raises(IndexingError):
        await swap.commit(ctx_for("A", "Y", pointed=["b1"]), run(), {"indexingStatus": "COMPLETED"})
    assert g.records["A"]["virtualRecordId"] == "X"
    assert g.records["A"]["pendingVirtualRecordId"] == "Y" and g.records["A"]["indexingStatus"] == "IN_PROGRESS"


@pytest.mark.asyncio
async def test_a_commit_that_loses_to_its_own_redelivery_is_a_commit_not_a_deletion() -> None:
    g = FakeGraph({"A": rec("Y", "COMPLETED")})  # the redelivery landed first: no pending field, the copy is the record's
    swap, _ = committer(g)
    assert await swap.commit(ctx_for("A", "Y"), run(), {"indexingStatus": "COMPLETED"}) is True
    swap._release_unheld.assert_not_called()


@pytest.mark.asyncio
async def test_a_record_that_ends_a_run_as_a_copy_of_another_loses_its_pending_field() -> None:
    """A later commit of an earlier run of the record expects the pending field, so removing it fences that commit."""
    from unittest.mock import AsyncMock, MagicMock

    from app.events.events import EventProcessor

    g = FakeGraph({"A": rec("X", "IN_PROGRESS", pendingVirtualRecordId="Y")})
    ep = EventProcessor(MagicMock(), MagicMock(), g, MagicMock())
    doc = {"_key": "A", **g.records["A"]}
    await ep._clear_pending(doc)
    assert "pendingVirtualRecordId" not in g.records["A"] and "pendingVirtualRecordId" not in doc
    ep.update_record_fields = AsyncMock(return_value=True)
    g.records["A"]["pendingVirtualRecordId"] = "Y2"
    await ep._discard_embed_marker({"_key": "A", **g.records["A"]}, {"embedVrid": None}, "o")
    assert "pendingVirtualRecordId" not in g.records["A"]


@pytest.mark.asyncio
async def test_an_empty_result_moves_the_record_to_the_empty_copy_and_releases_the_old_one() -> None:
    """The processor's EMPTY exits come after the pending write: without the commit the record stays on the old copy, which is kept for good."""
    from unittest.mock import AsyncMock, MagicMock

    from app.config.constants.arangodb import ProgressStatus
    from app.events.processor import Processor

    g = FakeGraph({"A": rec("X", "IN_PROGRESS", pendingVirtualRecordId="Y", hasIndexedCopy=True)})
    swap_committer, _ = committer(g)
    processor = Processor.__new__(Processor)
    processor.graph_provider = g
    processor.logger = MagicMock()
    processor.sink_orchestrator = MagicMock()
    processor.sink_orchestrator.swap = swap_committer
    swap_committer.finish = AsyncMock()
    keep_old_copy.begin_swap(keep_old_copy.SwapRun("A", "o", "X", "Y"))
    try:
        await processor._mark_record("A", ProgressStatus.EMPTY)
    finally:
        keep_old_copy.end_swap()
    assert g.records["A"]["virtualRecordId"] == "Y" and g.records["A"]["indexingStatus"] == "EMPTY"
    assert "pendingVirtualRecordId" not in g.records["A"]
    swap_committer.finish.assert_awaited_once()  # the old copy is released from there, after the grace


@pytest.mark.asyncio
async def test_the_old_copys_points_lose_their_membership_at_the_commit_not_after_the_grace() -> None:
    """retired points that still carry the connector's membership sit beside the served copy in every container search of
    that connector until the grace is over."""
    from unittest.mock import AsyncMock, patch

    g = FakeGraph({"A": rec("Y", "COMPLETED")})
    swap, vs = committer(g)
    vs.vector_db_service.set_payload = AsyncMock()
    vs.collection_locator.all_collections = AsyncMock(return_value=["c1", "c2"])
    with patch("app.modules.transformers.swap_commit.org_for_filter", return_value="o"), \
         patch("app.modules.transformers.swap_commit.mark_scope_stale", AsyncMock()):
        await swap._retire_membership(run())
    assert [c.args[0] for c in vs.vector_db_service.set_payload.await_args_list] == ["c1", "c2"]
    payload = vs.vector_db_service.set_payload.await_args_list[0].args[1]
    assert payload == {"connectorIds": [], "recordGroupIds": [], "rootRecordGroupIds": []}


@pytest.mark.asyncio
async def test_a_holder_that_remains_keeps_the_membership_of_the_old_copy() -> None:
    from unittest.mock import AsyncMock, patch

    g = FakeGraph({"A": rec("Y", "COMPLETED"), "B": rec("X", "COMPLETED")})  # B was not moved
    swap, vs = committer(g)
    vs.vector_db_service.set_payload = AsyncMock()
    with patch("app.modules.transformers.swap_commit.sync_vector_membership", AsyncMock()) as sync:
        await swap._retire_membership(run())
    sync.assert_awaited_once()
    vs.vector_db_service.set_payload.assert_not_called()


@pytest.mark.asyncio
async def test_a_twin_whose_membership_resync_fails_gets_a_durable_repair_and_the_other_twins_still_move() -> None:
    """a crash or an error between a twin's move and the write of its membership leaves the twin out of a container search of its
    connector; the connector's membership is walked again (the backfill scanner), not left to the next write."""
    from unittest.mock import AsyncMock, patch

    g = FakeGraph({
        "A": {"orgId": "o", "virtualRecordId": "Y", "indexingStatus": "COMPLETED", "md5Checksum": "m", "connectorId": "k1"},
        "B": rec("X", "COMPLETED", connectorId="k1"),
        "C": rec("X", "COMPLETED", connectorId="k1"),
    })
    swap, _ = committer(g)
    g.update_node = AsyncMock(return_value=True)
    with patch("app.modules.transformers.swap_commit.sync_vector_membership", AsyncMock(side_effect=RuntimeError("down"))):
        await swap._repoint_twins(ctx_for("A", "Y"), run(forced=True))
    assert g.records["B"]["virtualRecordId"] == "Y" and g.records["C"]["virtualRecordId"] == "Y"  # both moved: one failure does not stop the rest
    g.update_node.assert_any_await("k1", "apps", {"vectorMembershipBackfilled": False})


@pytest.mark.asyncio
async def test_the_old_copy_is_marked_for_its_grace_before_the_move_so_a_crash_before_finish_leaves_it_protected() -> None:
    from app.config.constants.arangodb import CollectionNames

    g = FakeGraph({"A": rec("X", pendingVirtualRecordId="Y")})
    swap, _ = committer(g)
    order: list[str] = []
    real_update = g.update_node_fields_if

    async def recording(*a: object, **k: object) -> bool:
        order.append("move")
        return await real_update(*a, **k)

    g.update_node_fields_if = recording  # type: ignore[method-assign]
    real_node = g.update_node

    async def mark(key: str, collection: str, fields: dict, *rest: object) -> bool:
        order.append(f"mark {key} {collection}")
        return await real_node(key, collection, fields, *rest)

    g.update_node = mark  # type: ignore[method-assign]
    assert await swap.commit(ctx_for("A", "Y"), run(), {"indexingStatus": "COMPLETED"})  # finish never runs: the process died
    assert order[:2] == [f"mark X {CollectionNames.VIRTUAL_RECORD_TO_DOC_ID_MAPPING.value}", "move"]
    assert keep_old_copy.RETIRED_AT_FIELD in g.mapping["X"]


@pytest.mark.asyncio
async def test_a_commit_does_not_land_on_a_record_that_names_another_copy_than_the_one_it_replaces() -> None:
    """The commit also matches the vrid it replaces: a record that moved on (a twin's attach, a rebuild) is not moved again."""
    g = FakeGraph({"A": rec("Z", pendingVirtualRecordId="Y")})
    swap, _ = committer(g)
    with pytest.raises(SupersededRun):
        await swap.commit(ctx_for("A", "Y"), run(), {"indexingStatus": "COMPLETED"})
    assert g.records["A"]["virtualRecordId"] == "Z"


@pytest.mark.asyncio
async def test_finish_drops_the_records_cache_entry_and_marks_the_old_copy() -> None:
    """The invalidation is not left to the coalesced path or to the cache's TTL: the finish of a commit calls it."""
    from unittest.mock import AsyncMock, patch

    g = FakeGraph({"A": rec("Y", "COMPLETED")})
    swap, vs = committer(g)
    vs.vector_db_service.set_payload = AsyncMock()
    vs.collection_locator.all_collections = AsyncMock(return_value=[])
    with patch("app.modules.transformers.swap_commit.notify_record_vrid_moved", AsyncMock()) as moved, \
         patch("app.modules.transformers.swap_commit.spawn_detached", lambda coro, name=None: coro.close()), \
         patch("app.modules.transformers.swap_commit.org_for_filter", return_value="o"), \
         patch("app.modules.transformers.swap_commit.mark_scope_stale", AsyncMock()):
        await swap.finish(ctx_for("A", "Y", connector_id="k1"), run())
    moved.assert_awaited_once()
    assert moved.await_args.kwargs["connector_id"] == "k1" and moved.await_args.kwargs["org_id"] == "o"
    assert keep_old_copy.RETIRED_AT_FIELD in g.mapping["X"]


@pytest.mark.asyncio
async def test_finish_retires_the_membership_of_the_old_copy() -> None:
    """The call that keeps retired copies out of container search: without it search falls back to the record-id path for most
    queries on a connector being re-indexed."""
    from unittest.mock import AsyncMock, patch

    g = FakeGraph({"A": rec("Y", "COMPLETED")})
    swap, _vs = committer(g)
    swap._retire_membership = AsyncMock()  # type: ignore[method-assign]
    with patch("app.modules.transformers.swap_commit.notify_record_vrid_moved", AsyncMock()), \
         patch("app.modules.transformers.swap_commit.spawn_detached", lambda coro, name=None: coro.close()):
        await swap.finish(ctx_for("A", "Y"), run())
    swap._retire_membership.assert_awaited_once_with(run())
