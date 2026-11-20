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


@pytest.mark.asyncio
async def test_a_block_that_goes_between_the_read_and_the_write_undoes_the_commit_too() -> None:
    """The read after the write checks every block that was given a point, not only that something is there."""
    g = FakeGraph({"A": rec("X", pendingVirtualRecordId="Y")})
    reads = iter([["b1", "b2"], ["b1"]])
    swap, _ = committer(g, view=lambda vrid: next(reads))
    with pytest.raises(IndexingError):
        await swap.commit(ctx_for("A", "Y", pointed=["b1", "b2"]), run(), {"indexingStatus": "COMPLETED"})
    assert g.records["A"]["virtualRecordId"] == "X"


@pytest.mark.asyncio
async def test_a_record_that_attaches_while_the_membership_is_taken_off_gets_it_back() -> None:
    from unittest.mock import AsyncMock, patch

    g = FakeGraph({"A": rec("Y", "COMPLETED")})
    swap, vs = committer(g)
    vs.vector_db_service.set_payload = AsyncMock()
    vs.collection_locator.all_collections = AsyncMock(return_value=["c1"])

    async def attach(*_a: object, **_k: object) -> None:
        g.records["B"] = rec("X", "COMPLETED")  # a duplicate attaches to the old copy during the write

    vs.vector_db_service.set_payload = AsyncMock(side_effect=attach)
    with patch("app.modules.transformers.swap_commit.org_for_filter", return_value="o"), \
         patch("app.modules.transformers.swap_commit.mark_scope_stale", AsyncMock()), \
         patch("app.modules.transformers.swap_commit.sync_vector_membership", AsyncMock()) as sync:
        await swap._retire_membership(run())
    sync.assert_awaited_once()  # the arrays are recomputed from the holders that are there now


@pytest.mark.asyncio
async def test_twins_are_not_moved_onto_a_copy_the_record_has_already_left() -> None:
    from unittest.mock import AsyncMock, patch

    g = FakeGraph({
        "A": {"orgId": "o", "virtualRecordId": "Z", "indexingStatus": "COMPLETED", "md5Checksum": "m", "connectorId": "k1"},  # a later run moved it on
        "B": rec("X", "COMPLETED", connectorId="k1"),
    })
    swap, _ = committer(g)
    with patch("app.modules.transformers.swap_commit.sync_vector_membership", AsyncMock()):
        await swap._repoint_twins(ctx_for("A", "Y"), run(forced=True))
    assert g.records["B"]["virtualRecordId"] == "X"


@pytest.mark.asyncio
async def test_a_twin_goes_back_when_the_record_moves_on_while_it_is_moved() -> None:
    from unittest.mock import AsyncMock, patch

    g = FakeGraph({
        "A": {"orgId": "o", "virtualRecordId": "Y", "indexingStatus": "COMPLETED", "md5Checksum": "m", "connectorId": "k1"},
        "B": rec("X", "COMPLETED", connectorId="k1"),
    })
    swap, _ = committer(g)
    real = g.update_node_fields_if

    async def move_then_a_later_run_commits(key: str, collection: str, updates: dict, conditions: list, *a: object, **k: object) -> bool:
        moved = await real(key, collection, updates, conditions, *a, **k)
        if key == "B" and moved:
            g.records["A"]["virtualRecordId"] = "Z"  # the record's later run committed while the twin was being moved
        return moved

    g.update_node_fields_if = move_then_a_later_run_commits  # type: ignore[method-assign]
    with patch("app.modules.transformers.swap_commit.sync_vector_membership", AsyncMock()):
        await swap._repoint_twins(ctx_for("A", "Y"), run(forced=True))
    assert g.records["B"]["virtualRecordId"] == "X"


@pytest.mark.asyncio
async def test_a_holder_that_attaches_before_the_purge_keeps_the_old_copys_points() -> None:
    g = FakeGraph({"A": rec("Y", "COMPLETED"), "C": rec("X", "COMPLETED", connectorId="k2")})
    swap, vs = committer(g, collections={"k1": "c1", "k2": "c2"})
    calls = {"n": 0}
    real = g.get_records_by_virtual_record_id

    async def lookup(vrid: str, *a: object, **k: object) -> list[str]:
        calls["n"] += 1
        if calls["n"] == 2:
            g.records["D"] = rec("X", "COMPLETED", connectorId="k1")  # attaches in this record's collection after the first read
        return await real(vrid, *a, **k)

    g.get_records_by_virtual_record_id = lookup  # type: ignore[method-assign]
    from unittest.mock import AsyncMock, patch

    with patch("app.modules.transformers.swap_commit.sync_vector_membership", AsyncMock()):
        await swap.release_old_copy(run(), ctx_for("A", "Y").record)
    vs.purge_record_vectors.assert_not_called()


@pytest.mark.asyncio
async def test_a_failure_releases_only_the_copy_its_own_attempt_wrote() -> None:
    g = FakeGraph({"A": rec("X", "FAILED", pendingVirtualRecordId="Y2")})  # another consumer's attempt has named its own copy since
    swap, _ = committer(g)
    await swap.release_failed("A", "o", attempt_pending="Y1")
    assert g.records["A"]["pendingVirtualRecordId"] == "Y2"
    swap._release_unheld.assert_not_called()


@pytest.mark.asyncio
async def test_a_truncated_listing_still_checks_the_blocks_it_did_not_reach() -> None:
    """A listing that stops at its bound cannot prove the set whole: the missing block ids are asked for directly."""
    from unittest.mock import patch

    g = FakeGraph({"A": rec("X", pendingVirtualRecordId="Y")})
    swap, _ = committer(g, view=lambda vrid: ["b1"])  # the store has b1 only
    async def truncated(ctx: object, s: object) -> tuple[set[str], bool]:
        return {"b1"}, False  # b2 was past the bound of the listing

    swap._blocks_in_store = truncated  # type: ignore[method-assign]
    with pytest.raises(IndexingError):
        await swap.commit(ctx_for("A", "Y", pointed=["b1", "b2"]), run(), {"indexingStatus": "COMPLETED"})
    assert g.records["A"]["virtualRecordId"] == "X"
    present = {"b1", "b2"}

    async def ask(ctx: object, s: object, blocks: list[str]) -> set[str]:
        return set(blocks) & present  # b2 is there when asked for by id

    swap._present_among = ask  # type: ignore[method-assign]
    assert await swap.commit(ctx_for("A", "Y", pointed=["b1", "b2"]), run(), {"indexingStatus": "COMPLETED"})
    del patch


@pytest.mark.asyncio
async def test_a_failed_membership_restore_after_the_retirement_asks_for_a_repair() -> None:
    from unittest.mock import AsyncMock, patch

    g = FakeGraph({"A": rec("Y", "COMPLETED")})
    swap, vs = committer(g)
    vs.vector_db_service.set_payload = AsyncMock()
    vs.collection_locator.all_collections = AsyncMock(return_value=["c1"])
    g.update_node = AsyncMock(return_value=True)

    async def attach(*_a: object, **_k: object) -> None:
        g.records["B"] = rec("X", "COMPLETED", connectorId="k1")

    vs.vector_db_service.set_payload = AsyncMock(side_effect=attach)
    with patch("app.modules.transformers.swap_commit.org_for_filter", return_value="o"), \
         patch("app.modules.transformers.swap_commit.mark_scope_stale", AsyncMock()), \
         patch("app.modules.transformers.swap_commit.sync_vector_membership", AsyncMock(side_effect=RuntimeError("down"))):
        await swap._retire_membership(run())
    g.update_node.assert_any_await("k1", "apps", {"vectorMembershipBackfilled": False})


@pytest.mark.asyncio
async def test_a_read_that_fails_after_the_commit_write_undoes_the_move_and_the_run_is_retried() -> None:
    """A store timeout on the read after the write must not leave the record on a copy nobody has shown to be whole."""
    g = FakeGraph({"A": rec("X", pendingVirtualRecordId="Y")})
    calls = {"n": 0}

    def view(vrid: str) -> list[str]:
        calls["n"] += 1
        if calls["n"] == 2:  # the read after the write
            raise TimeoutError("down")
        return ["b1"]

    swap, _ = committer(g, view=view)
    with pytest.raises(TimeoutError):
        await swap.commit(ctx_for("A", "Y", pointed=["b1"]), run(), {"indexingStatus": "COMPLETED"})
    assert g.records["A"]["virtualRecordId"] == "X" and g.records["A"]["pendingVirtualRecordId"] == "Y"


@pytest.mark.asyncio
async def test_when_the_read_and_the_undo_both_fail_the_record_is_an_attempt_in_flight_and_the_rerun_repairs_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """The double failure: the record is never COMPLETED on a copy nobody has verified (its status is written only after the read), and the
    rerun that the requeue (or the stale sweep) starts replaces the copy."""
    monkeypatch.setattr("app.modules.transformers.swap_commit.UNDO_BACKOFF_SECONDS", 0)
    g = FakeGraph({"A": rec("X", "IN_PROGRESS", pendingVirtualRecordId="Y", hasIndexedCopy=True)})
    state = {"fail": True}

    def view(vrid: str) -> list[str]:
        if state["fail"] and vrid == "Y" and state.setdefault("reads", 0) is not None:
            state["reads"] += 1
            if state["reads"] == 2:  # the read after the write
                raise TimeoutError("down")
        return ["b1"]

    real = g.update_node_fields_if

    async def graph_down_for_the_undo(key: str, collection: str, updates: dict, conditions: list, *a: object, **k: object) -> bool:
        if state["fail"] and updates.get("pendingVirtualRecordId") and updates.get("virtualRecordId") == "X":
            raise TimeoutError("down")
        return await real(key, collection, updates, conditions, *a, **k)

    g.update_node_fields_if = graph_down_for_the_undo  # type: ignore[method-assign]
    swap, _ = committer(g, view=view)
    with pytest.raises(TimeoutError):
        await swap.commit(ctx_for("A", "Y", pointed=["b1"]), run(), {"indexingStatus": "COMPLETED"})
    doc = g.records["A"]
    assert doc["virtualRecordId"] == "Y" and doc["indexingStatus"] == "IN_PROGRESS"  # an attempt in flight, not COMPLETED
    # the rerun (the record is requeued, or found by the stale sweep): it replaces the unverified copy and finishes
    state["fail"] = False
    doc["pendingVirtualRecordId"] = "Y2"
    assert await swap.commit(ctx_for("A", "Y2"), keep_old_copy.SwapRun("A", "o", "Y", "Y2"), {"indexingStatus": "COMPLETED"})
    assert g.records["A"]["virtualRecordId"] == "Y2" and g.records["A"]["indexingStatus"] == "COMPLETED"


@pytest.mark.asyncio
async def test_a_mark_that_cannot_be_stored_leaves_the_record_on_the_old_copy() -> None:
    from unittest.mock import AsyncMock

    g = FakeGraph({"A": rec("X", pendingVirtualRecordId="Y")})
    swap, _ = committer(g)
    g.mapping["X"] = {"orgId": "o"}  # the row exists but the mark could not be written
    g.update_node = AsyncMock(return_value=False)  # type: ignore[method-assign]
    with pytest.raises(IndexingError):
        await swap.commit(ctx_for("A", "Y"), run(), {"indexingStatus": "COMPLETED"})
    assert g.records["A"]["virtualRecordId"] == "X"
    g.update_node = AsyncMock(side_effect=RuntimeError("down"))  # type: ignore[method-assign]
    with pytest.raises(IndexingError):
        await swap.commit(ctx_for("A", "Y"), run(), {"indexingStatus": "COMPLETED"})
    assert g.records["A"]["virtualRecordId"] == "X"


@pytest.mark.asyncio
async def test_twins_stay_on_their_copy_when_the_committed_copy_is_empty() -> None:
    from unittest.mock import AsyncMock

    g = FakeGraph({
        "A": rec("X", "IN_PROGRESS", pendingVirtualRecordId="Y", connectorId="k1"),
        "B": rec("X", "COMPLETED", connectorId="k1"),
    })
    swap, _ = committer(g)
    swap.finish = AsyncMock()  # type: ignore[method-assign]
    assert await swap.commit_document(g.records["A"], keep_old_copy.SwapRun("A", "o", "X", "Y", True), {"indexingStatus": "EMPTY"})
    swap.finish.assert_awaited_once()
    assert swap.finish.await_args.kwargs == {"repoint_twins": False}
    assert g.records["B"]["virtualRecordId"] == "X"


@pytest.mark.asyncio
async def test_a_failure_partway_through_the_membership_clear_still_restores_the_holders() -> None:
    from unittest.mock import AsyncMock, patch

    g = FakeGraph({"A": rec("Y", "COMPLETED")})
    swap, vs = committer(g)
    vs.collection_locator.all_collections = AsyncMock(return_value=["c1", "c2"])
    calls = {"n": 0}

    async def clear(*_a: object, **_k: object) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            g.records["B"] = rec("X", "COMPLETED")  # attaches after the first collection was cleared
        else:
            raise RuntimeError("down")  # the second clear fails

    vs.vector_db_service.set_payload = AsyncMock(side_effect=clear)
    with patch("app.modules.transformers.swap_commit.org_for_filter", return_value="o"), \
         patch("app.modules.transformers.swap_commit.mark_scope_stale", AsyncMock()), \
         patch("app.modules.transformers.swap_commit.sync_vector_membership", AsyncMock()) as sync:
        with pytest.raises(RuntimeError):
            await swap._retire_membership(run())
    sync.assert_awaited_once()  # the holders are read again and their membership restored before the failure surfaces


@pytest.mark.asyncio
async def test_a_twin_that_moves_back_gets_its_own_summary_and_redaction_back() -> None:
    from unittest.mock import AsyncMock, patch

    g = FakeGraph({
        "A": {"orgId": "o", "virtualRecordId": "Y", "indexingStatus": "COMPLETED", "md5Checksum": "m", "connectorId": "k1",
              "summaryDocumentId": "sumY", "secretsRedacted": 3},
        "B": rec("X", "COMPLETED", connectorId="k1", summaryDocumentId="sumX"),  # no secretsRedacted of its own
    })
    swap, _ = committer(g)
    real = g.update_node_fields_if

    async def move_then_a_later_run_commits(key: str, collection: str, updates: dict, conditions: list, *a: object, **k: object) -> bool:
        moved = await real(key, collection, updates, conditions, *a, **k)
        if key == "B" and moved and updates.get("virtualRecordId") == "Y":
            g.records["A"]["virtualRecordId"] = "Z"
        return moved

    g.update_node_fields_if = move_then_a_later_run_commits  # type: ignore[method-assign]
    with patch("app.modules.transformers.swap_commit.sync_vector_membership", AsyncMock()):
        await swap._repoint_twins(ctx_for("A", "Y"), run(forced=True))
    assert g.records["B"] == {**rec("X", "COMPLETED", connectorId="k1", summaryDocumentId="sumX")}  # nothing of Y's identity is left on it


@pytest.mark.asyncio
async def test_one_failing_twin_does_not_stop_the_others_and_all_are_waited_for() -> None:
    from unittest.mock import AsyncMock, patch

    g = FakeGraph({
        "A": {"orgId": "o", "virtualRecordId": "Y", "indexingStatus": "COMPLETED", "md5Checksum": "m", "connectorId": "k1"},
        "B": rec("X", "COMPLETED", connectorId="k1"), "C": rec("X", "COMPLETED", connectorId="k1"),
    })
    swap, _ = committer(g)
    real = g.get_document

    async def get(key: str, collection: str, **k: object) -> dict | None:
        if key == "B":
            raise RuntimeError("down")
        return await real(key, collection, **k)

    g.get_document = get  # type: ignore[method-assign]
    with patch("app.modules.transformers.swap_commit.sync_vector_membership", AsyncMock()):
        await swap._repoint_twins(ctx_for("A", "Y"), run(forced=True))
    assert g.records["C"]["virtualRecordId"] == "Y" and g.records["B"]["virtualRecordId"] == "X"


@pytest.mark.asyncio
async def test_the_release_waits_for_the_latest_retirement_mark_of_the_copy(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two records left the same copy: the first one's timer must not release it while the second one's grace is running."""

    clock = {"now": 1_000_000.0}
    monkeypatch.setattr(keep_old_copy, "grace_seconds", lambda: 100.0)
    monkeypatch.setattr("app.modules.transformers.swap_commit.time.time", lambda: clock["now"])
    mark = clock["now"] * 1000 + 40_000  # the second record left 40 s after the timer was set
    g = FakeGraph({"A": rec("Y", "COMPLETED")})
    g.mapping["X"] = {keep_old_copy.RETIRED_AT_FIELD: mark}
    swap, _ = committer(g)
    released_at: list[float] = []

    async def release(s: object, r: object) -> None:
        released_at.append(clock["now"])

    swap.release_old_copy = release  # type: ignore[method-assign]

    async def fake_sleep(seconds: float) -> None:
        clock["now"] += seconds  # the mark stays: only the clock moves

    monkeypatch.setattr("app.modules.transformers.swap_commit.asyncio.sleep", fake_sleep)
    await swap._release_after_grace(run(), ctx_for("A", "Y").record)
    assert released_at and released_at[0] >= mark / 1000 + 100.0  # not before the latest mark's grace is over


@pytest.mark.asyncio
async def test_the_release_does_nothing_when_the_mark_cannot_be_read_or_keeps_being_renewed(monkeypatch: pytest.MonkeyPatch) -> None:
    from unittest.mock import AsyncMock

    clock = {"now": 1_000_000.0}
    monkeypatch.setattr(keep_old_copy, "grace_seconds", lambda: 100.0)
    monkeypatch.setattr("app.modules.transformers.swap_commit.time.time", lambda: clock["now"])
    g = FakeGraph({"A": rec("Y", "COMPLETED")})
    swap, _ = committer(g)
    swap.release_old_copy = AsyncMock()  # type: ignore[method-assign]

    async def fake_sleep(seconds: float) -> None:
        clock["now"] += seconds
        g.mapping["X"] = {keep_old_copy.RETIRED_AT_FIELD: clock["now"] * 1000}  # a holder renews the mark each time

    monkeypatch.setattr("app.modules.transformers.swap_commit.asyncio.sleep", fake_sleep)
    g.mapping["X"] = {keep_old_copy.RETIRED_AT_FIELD: clock["now"] * 1000}
    await swap._release_after_grace(run(), ctx_for("A", "Y").record)
    swap.release_old_copy.assert_not_called()  # renewed past the limit: the sweep reclaims it later

    async def unreadable(key: str, collection: str, **k: object) -> dict | None:
        if k.get("raise_on_error"):
            raise RuntimeError("down")
        return None  # what a swallowed read failure looks like

    g.get_document = unreadable  # type: ignore[method-assign]
    swap.release_old_copy.reset_mock()
    await swap._release_after_grace(run(), ctx_for("A", "Y").record)
    swap.release_old_copy.assert_not_called()


@pytest.mark.asyncio
async def test_after_a_failed_read_and_a_failed_undo_the_copy_search_serves_is_the_complete_one_that_was_confirmed_before_the_move(monkeypatch: pytest.MonkeyPatch) -> None:
    """The exact guarantee of the double failure: the pre-commit read found every block, and once the record names the copy nothing can
    sweep it (a record holds the virtual record it names, whatever its status), so the copy it keeps serving until the rerun is whole."""
    from app.services.graph_db.common.record_visibility import holds_vrid

    monkey_points = {"Y": ["b1", "b2"]}  # the store holds the whole set throughout
    g = FakeGraph({"A": rec("X", "IN_PROGRESS", pendingVirtualRecordId="Y", hasIndexedCopy=True)})
    reads = {"n": 0}

    def view(vrid: str) -> list[str]:
        reads["n"] += 1
        if reads["n"] == 2:
            raise TimeoutError("the read after the write fails")
        return monkey_points.get(vrid, [])

    real = g.update_node_fields_if

    async def undo_fails(key: str, collection: str, updates: dict, conditions: list, *a: object, **k: object) -> bool:
        if updates.get("virtualRecordId") == "X" and updates.get("pendingVirtualRecordId"):
            raise TimeoutError("the undo write fails")
        return await real(key, collection, updates, conditions, *a, **k)

    g.update_node_fields_if = undo_fails  # type: ignore[method-assign]
    swap, _ = committer(g, view=view)
    monkeypatch.setattr("app.modules.transformers.swap_commit.UNDO_BACKOFF_SECONDS", 0)
    with pytest.raises(TimeoutError):
        await swap.commit(ctx_for("A", "Y", pointed=["b1", "b2"]), run(), {"indexingStatus": "COMPLETED"})
    doc = g.records["A"]
    assert doc["virtualRecordId"] == "Y" and doc["indexingStatus"] == "IN_PROGRESS" and doc["hasIndexedCopy"] is True
    assert holds_vrid(doc, "Y")  # a sweep cannot take it: the record names it
    assert monkey_points["Y"] == ["b1", "b2"]  # and what search serves is the set the pre-commit read confirmed


@pytest.mark.asyncio
async def test_a_copy_known_to_be_incomplete_is_kept_out_of_search_when_the_undo_cannot_be_written(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.modules.transformers.swap_commit.UNDO_BACKOFF_SECONDS", 0)
    g = FakeGraph({"A": rec("X", "IN_PROGRESS", pendingVirtualRecordId="Y", hasIndexedCopy=True)})
    reads = iter([["b1", "b2"], ["b1"]])  # whole before the write, a block gone after it (the read answers: incomplete)
    real = g.update_node_fields_if

    async def undo_fails(key: str, collection: str, updates: dict, conditions: list, *a: object, **k: object) -> bool:
        if updates.get("virtualRecordId") == "X" and updates.get("pendingVirtualRecordId"):
            raise TimeoutError("down")
        return await real(key, collection, updates, conditions, *a, **k)

    g.update_node_fields_if = undo_fails  # type: ignore[method-assign]
    swap, _ = committer(g, view=lambda vrid: next(reads))
    with pytest.raises(IndexingError):
        await swap.commit(ctx_for("A", "Y", pointed=["b1", "b2"]), run(), {"indexingStatus": "COMPLETED"})
    assert g.records["A"]["virtualRecordId"] == "Y" and g.records["A"]["hasIndexedCopy"] is False  # not served until the rerun


@pytest.mark.asyncio
async def test_neither_the_finish_nor_the_undo_runs_over_the_pending_copy_of_a_newer_attempt() -> None:
    """After the move the record has no pending field, and a newer attempt may install its own while this one verifies."""
    g = FakeGraph({"A": rec("X", pendingVirtualRecordId="Y")})
    swap, _ = committer(g)

    async def newer_attempt_installs_its_copy(ctx: object, s: object) -> None:
        g.records["A"]["pendingVirtualRecordId"] = "Y2"  # during the read after the write

    swap._confirm_after_commit = newer_attempt_installs_its_copy  # type: ignore[method-assign]
    assert await swap.commit(ctx_for("A", "Y"), run(), {"indexingStatus": "COMPLETED"})  # the move stands
    assert g.records["A"]["indexingStatus"] == "IN_PROGRESS"  # not completed over the newer attempt, which finishes the record
    assert g.records["A"]["pendingVirtualRecordId"] == "Y2"

    g2 = FakeGraph({"A": rec("Y", "IN_PROGRESS", pendingVirtualRecordId="Y2")})
    swap2, _ = committer(g2, view=lambda vrid: [])
    with pytest.raises(IndexingError):
        await swap2._confirm_after_commit(ctx_for("A", "Y", pointed=["b1"]), run())
    assert g2.records["A"]["virtualRecordId"] == "Y" and g2.records["A"]["pendingVirtualRecordId"] == "Y2"  # the undo did not overwrite it


@pytest.mark.asyncio
async def test_the_fallback_that_hides_an_incomplete_copy_leaves_a_newer_complete_copy_served() -> None:
    from unittest.mock import AsyncMock

    g = FakeGraph({"A": rec("Z", "COMPLETED", hasIndexedCopy=True)})  # a newer run already committed its complete copy Z
    swap, _ = committer(g, view=lambda vrid: [])
    real = g.update_node_fields_if

    async def undo_refused(key: str, collection: str, updates: dict, conditions: list, *a: object, **k: object) -> bool:
        if updates.get("pendingVirtualRecordId"):
            return False
        return await real(key, collection, updates, conditions, *a, **k)

    g.update_node_fields_if = undo_refused  # type: ignore[method-assign]
    g.update_node = AsyncMock(return_value=True)  # type: ignore[method-assign]
    with pytest.raises(IndexingError):
        await swap._confirm_after_commit(ctx_for("A", "Y", pointed=["b1"]), run())
    assert g.records["A"]["hasIndexedCopy"] is True  # the record names Z, not the incomplete Y
