"""The commit of a replacement copy, the take-over, the twins and the release of the old copy (real code, in-memory stores)."""
import pytest

from app.exceptions.indexing_exceptions import IndexingError, SupersededRun
from app.modules.indexing import keep_old_copy
from tests.support.swap_world import FakeGraph, committer, ctx_for


@pytest.fixture(autouse=True)
def _no_vector_membership_io(monkeypatch):
    from unittest.mock import AsyncMock

    monkeypatch.setattr("app.modules.transformers.swap_commit.sync_vector_membership", AsyncMock())


def rec(vrid, status="IN_PROGRESS", **more):
    return {"orgId": "o", "virtualRecordId": vrid, "indexingStatus": status, "md5Checksum": "m", "connectorId": "k1", **more}


def run(forced=False):
    return keep_old_copy.SwapRun("A", "o", "X", "Y", forced)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["IN_PROGRESS", "QUEUED", "NOT_STARTED"])
async def test_the_commit_moves_the_record_and_removes_the_pending_field(status):
    g = FakeGraph({"A": rec("X", status, pendingVirtualRecordId="Y")})
    swap, _ = committer(g)
    assert await swap.commit(ctx_for("A", "Y"), run(), {"indexingStatus": "COMPLETED"})
    assert g.records["A"]["virtualRecordId"] == "Y"
    assert "pendingVirtualRecordId" not in g.records["A"]  # removed, never null
    assert g.records["A"]["indexingStatus"] == "COMPLETED"


@pytest.mark.asyncio
async def test_a_record_that_changed_state_refuses_the_commit_and_the_run_ends_superseded():
    g = FakeGraph({"A": rec("X", "FAILED", pendingVirtualRecordId="Y")})
    swap, _ = committer(g)
    with pytest.raises(SupersededRun):
        await swap.commit(ctx_for("A", "Y"), run(), {"indexingStatus": "COMPLETED"})
    assert g.records["A"]["virtualRecordId"] == "X"  # the old copy is still the record's
    assert "pendingVirtualRecordId" not in g.records["A"]
    swap._release_unheld.assert_awaited_once_with("o", "Y")


@pytest.mark.asyncio
async def test_a_commit_does_not_land_on_a_record_another_run_took_over():
    g = FakeGraph({"A": rec("X", pendingVirtualRecordId="Y2")})
    swap, _ = committer(g)
    with pytest.raises(SupersededRun):
        await swap.commit(ctx_for("A", "Y"), run(), {"indexingStatus": "COMPLETED"})
    assert g.records["A"]["virtualRecordId"] == "X" and g.records["A"]["pendingVirtualRecordId"] == "Y2"  # the other run's copy is untouched


@pytest.mark.asyncio
async def test_a_deleted_record_returns_false_and_releases_the_run_copy():
    g = FakeGraph({})
    swap, _ = committer(g)
    assert not await swap.commit(ctx_for("A", "Y"), run(), {"indexingStatus": "COMPLETED"})
    swap._release_unheld.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_copy_whose_points_are_gone_is_not_committed():
    g = FakeGraph({"A": rec("X", pendingVirtualRecordId="Y")})
    swap, _ = committer(g, points=0)
    with pytest.raises(IndexingError):
        await swap.commit(ctx_for("A", "Y"), run(), {"indexingStatus": "COMPLETED"})
    assert g.records["A"]["virtualRecordId"] == "X"


@pytest.mark.asyncio
async def test_a_forced_commit_moves_the_matching_twin_in_the_same_collection_only():
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
async def test_the_old_copy_is_deleted_in_this_records_collection_only_while_another_collection_holds_it():
    g = FakeGraph({"A": rec("Y", "COMPLETED"), "C": rec("X", "COMPLETED", connectorId="k2")})
    swap, vs = committer(g, collections={"k1": "c1", "k2": "c2"})
    await swap.release_old_copy(run(), ctx_for("A", "Y").record)
    vs.purge_record_vectors.assert_awaited_once()  # A's collection loses X's points; c2 keeps them
    swap._release_unheld.assert_not_called()


@pytest.mark.asyncio
async def test_the_old_copy_is_kept_while_a_holder_uses_the_same_collection():
    g = FakeGraph({"A": rec("Y", "COMPLETED"), "B": rec("X", "COMPLETED")})
    swap, vs = committer(g)
    await swap.release_old_copy(run(), ctx_for("A", "Y").record)
    vs.purge_record_vectors.assert_not_called()
    swap._release_unheld.assert_not_called()


@pytest.mark.asyncio
async def test_an_unheld_old_copy_is_released_whole():
    g = FakeGraph({"A": rec("Y", "COMPLETED")})
    swap, _ = committer(g)
    await swap.release_old_copy(run(), ctx_for("A", "Y").record)
    swap._release_unheld.assert_awaited_once_with("o", "X")


@pytest.mark.asyncio
async def test_a_record_that_failed_for_good_releases_its_pending_copy():
    g = FakeGraph({"A": rec("X", "FAILED", pendingVirtualRecordId="Y")})
    swap, _ = committer(g)
    await swap.release_failed("A", "o")
    assert "pendingVirtualRecordId" not in g.records["A"] and g.records["A"]["virtualRecordId"] == "X"
    swap._release_unheld.assert_awaited_once_with("o", "Y")


def test_the_running_copy_is_held_until_the_record_reaches_another_status():
    from app.services.graph_db.common.record_visibility import holds_vrid

    assert holds_vrid(rec("X", "NOT_STARTED", pendingVirtualRecordId="Y"), "Y")  # edited faster than it indexes still commits
    assert not holds_vrid(rec("X", "FAILED", pendingVirtualRecordId="Y"), "Y")
