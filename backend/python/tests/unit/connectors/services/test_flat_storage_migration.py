"""L06: copies stored under the flat records/<vrid> layout (before hierarchical
storage) are moved under their owner's path; one replica leads, progress is
resumable per org, and a pass that hit failures is run again."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.connectors.services.flat_storage_migration import (
    MAX_PASSES,
    FlatStorageMigration,
)
from app.core import org_context

KEY = "/services/storageMigration/flatToHierarchical/org-1"
MOVE = "app.connectors.services.flat_storage_migration.post_move_tree"


class _KV:
    def __init__(self) -> None:
        self.data = {}

    async def get_config(self, key: str, use_cache: bool = True, default: object = None) -> object:
        return self.data.get(key, default)

    async def set_config(self, key, value) -> bool:
        self.data[key] = dict(value)
        return True


def _holder(record_id: str, *, deleted: bool = False) -> dict:
    return {"id": record_id, "connectorId": "conn", "recordName": record_id, "isDeleted": deleted}


def _migration(
    pages: list, holders: dict, *, stored_owner: str | None = None, leader: bool = True, keeps_leading: bool = True,
) -> tuple[FlatStorageMigration, _KV]:
    kv = _KV()
    gp = AsyncMock()
    gp.get_all_orgs = AsyncMock(return_value=[{"id": "org-1"}])
    gp.get_virtual_record_holders = AsyncMock(
        side_effect=lambda vrids, org_id: {v: holders[v] for v in vrids if v in holders}
    )
    blob = MagicMock()
    blob._get_auth_and_config = AsyncMock(return_value=({}, "http://node", "local"))
    blob.get_record_from_storage = AsyncMock(return_value={"id": stored_owner} if stored_owner else None)
    lock = MagicMock()
    lock.try_acquire = AsyncMock(return_value=leader)
    lock.refresh = AsyncMock(return_value=keeps_leading)
    m = FlatStorageMigration(MagicMock(), kv, gp, blob, lock)
    m.storage._owner_storage_path = AsyncMock(side_effect=lambda owner: f"records/conn/{owner['id']}")
    m._flat_documents = AsyncMock(side_effect=list(pages) + [[]] * 5)
    return m, kv


def _docs(*vrids: str, start: int = 0) -> list[dict]:
    out = []
    for i, v in enumerate(vrids):
        out.append({"_id": f"{start + 2 * i:024x}", "documentName": f"record_{v}"})
        out.append({"_id": f"{start + 2 * i + 1:024x}", "documentName": f"metadata_{v}"})
    return out


@pytest.mark.asyncio
async def test_each_vrid_is_moved_once_under_its_owners_path() -> None:
    m, kv = _migration([_docs("v1", "v2")], {"v1": [_holder("r1")], "v2": [_holder("r2")]})
    with patch(MOVE, new_callable=AsyncMock, return_value={"moved": 2}) as move:
        assert await m.run_all_orgs() is True
    assert [(c.args[3], c.args[4], c.kwargs) for c in move.await_args_list] == [
        ("records/v1", "records/conn/r1", {"virtual_record_ids": ["v1"]}),
        ("records/v2", "records/conn/r2", {"virtual_record_ids": ["v2"]}),
    ]
    # One holders read for the whole page.
    m.graph_provider.get_virtual_record_holders.assert_awaited_once_with(["v1", "v2"], "org-1")
    assert kv.data[KEY]["done"] is True and kv.data[KEY]["moved"] == 2


@pytest.mark.asyncio
async def test_only_the_leader_runs_it() -> None:
    m, kv = _migration([_docs("v1")], {"v1": [_holder("r1")]}, leader=False)
    with patch(MOVE, new_callable=AsyncMock) as move:
        assert await m.run_all_orgs() is False
    move.assert_not_awaited()
    m._flat_documents.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_leader_that_loses_the_lock_stops_and_the_next_one_resumes() -> None:
    m, kv = _migration([_docs("v1")], {"v1": [_holder("r1")]}, keeps_leading=False)
    with patch(MOVE, new_callable=AsyncMock, return_value={"moved": 2}):
        assert await m.run_all_orgs() is False
    assert kv.data[KEY]["after"] == f"{1:024x}" and not kv.data[KEY].get("done")


@pytest.mark.asyncio
async def test_a_finished_org_is_not_scanned_again() -> None:
    m, kv = _migration([], {})
    kv.data[KEY] = {"done": True}
    assert await m.run_org("org-1") is True
    m._flat_documents.assert_not_awaited()


@pytest.mark.asyncio
async def test_progress_is_saved_after_each_page_and_resumed() -> None:
    m, kv = _migration([_docs(*[f"v{i}" for i in range(250)])], {})
    with patch("app.connectors.services.flat_storage_migration._PAGE", 500), \
         patch.object(m, "_finish_pass", new_callable=AsyncMock, return_value=True):
        await m.run_org("org-1")
    assert kv.data[KEY]["after"] == f"{499:024x}"
    m2, _ = _migration([], {})
    m2.config_service = kv
    await m2.run_org("org-1")
    assert m2._flat_documents.await_args_list[0].args[2] == f"{499:024x}"


@pytest.mark.asyncio
async def test_a_copy_no_record_holds_is_left_in_place_without_retrying() -> None:
    m, kv = _migration([_docs("orphan")], {})
    with patch(MOVE, new_callable=AsyncMock) as move:
        await m.run_org("org-1")
    move.assert_not_awaited()
    assert kv.data[KEY]["unowned"] == 1 and kv.data[KEY]["passes"] == 1 and kv.data[KEY]["done"] is True


@pytest.mark.asyncio
async def test_a_transient_failure_is_retried_in_another_pass() -> None:
    # The failed copy is still flat, so the second pass lists it again.
    m, kv = _migration([_docs("v1"), _docs("v1")], {"v1": [_holder("r1")]})
    with patch(MOVE, new_callable=AsyncMock, side_effect=[RuntimeError("503"), {"moved": 2}]) as move:
        await m.run_org("org-1")
    assert move.await_count == 2
    assert kv.data[KEY]["done"] is True and kv.data[KEY]["moved"] == 1
    assert kv.data[KEY].get("failed", 0) == 0 and kv.data[KEY]["passes"] == 2


@pytest.mark.asyncio
async def test_failures_left_after_others_moved_are_given_up_on_after_the_last_pass() -> None:
    m, kv = _migration(
        [_docs("v1", "v2"), *[_docs("v2")] * (MAX_PASSES - 1)], {"v1": [_holder("r1")], "v2": [_holder("r2")]},
    )
    with patch(MOVE, new_callable=AsyncMock, side_effect=[{"moved": 2}, *[RuntimeError("503")] * MAX_PASSES]) as move:
        await m.run_org("org-1")
    assert move.await_count == MAX_PASSES + 1
    assert kv.data[KEY]["done"] is True and kv.data[KEY]["failed"] == 1
    [call] = m.logger.error.call_args_list
    assert "finished: 1 moved, 0 with no record to own them, 1 failed" in call.args[0] % call.args[1:]


@pytest.mark.asyncio
async def test_a_run_that_moved_nothing_is_tried_again_at_the_next_start() -> None:
    """Every copy failing is one shared cause, such as a missing bucket permission: once it is fixed the
    copies must move without anyone clearing a key by hand."""
    m, kv = _migration([_docs("v1")] * MAX_PASSES, {"v1": [_holder("r1")]})
    with patch(MOVE, new_callable=AsyncMock, side_effect=RuntimeError("AccessDenied")) as move:
        assert await m.run_org("org-1") is True, "the run ends; it does not spin"
    assert move.await_count == MAX_PASSES
    assert not kv.data[KEY].get("done") and not kv.data[KEY].get("after")
    m.logger.error.assert_called_once()

    m._flat_documents = AsyncMock(side_effect=[_docs("v1"), []])
    with patch(MOVE, new_callable=AsyncMock, return_value={"moved": 2}):
        await m.run_org("org-1")
    assert kv.data[KEY]["done"] is True and kv.data[KEY]["moved"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(("path", "result", "reason"), [
    (None, None, "no storage path for the owning record"),
    ("records/conn/r1", {"moved": 0}, "the storage service moved nothing"),
    ("records/conn/r1", {"moved": 1, "failed": ["d1"]}, "the storage service could not move every document"),
    ("records/conn/r1", RuntimeError("move-tree failed: 500"), "RuntimeError: move-tree failed: 500"),
])
async def test_the_reason_copies_failed_is_logged(path: str | None, result: object, reason: str) -> None:
    m, _ = _migration([_docs("v1")] * MAX_PASSES, {"v1": [_holder("r1")]})
    m.storage._owner_storage_path = AsyncMock(return_value=path)
    with patch(MOVE, new_callable=AsyncMock, side_effect=result if isinstance(result, Exception) else [result] * MAX_PASSES):
        await m.run_org("org-1")
    [call] = m.logger.error.call_args_list
    assert f"{reason} (1)" in call.args[0] % call.args[1:]


@pytest.mark.asyncio
async def test_a_failure_names_only_a_few_copies() -> None:
    vrids = [f"v{i}" for i in range(50)]
    m, _ = _migration([_docs(*vrids)] * MAX_PASSES, {v: [_holder(v)] for v in vrids})
    with patch(MOVE, new_callable=AsyncMock, side_effect=RuntimeError("AccessDenied")):
        await m.run_org("org-1")
    named = [c for c in m.logger.warning.call_args_list if "left in place" in c.args[0]]
    assert len(named) == 5 * MAX_PASSES


@pytest.mark.asyncio
async def test_an_unreadable_page_counts_as_failed() -> None:
    m, kv = _migration([_docs("v1")] * MAX_PASSES, {})
    m.graph_provider.get_virtual_record_holders = AsyncMock(side_effect=RuntimeError("graph down"))
    with patch(MOVE, new_callable=AsyncMock) as move:
        await m.run_org("org-1")
    move.assert_not_awaited()
    [call] = m.logger.error.call_args_list
    assert "1 failed: could not read the records holding them: RuntimeError (1)" in call.args[0] % call.args[1:]


@pytest.mark.asyncio
async def test_shared_content_goes_under_the_live_record_its_copy_describes() -> None:
    m, kv = _migration([_docs("v1")], {"v1": [_holder("r-a"), _holder("r-b")]}, stored_owner="r-b")
    with patch(MOVE, new_callable=AsyncMock, return_value={"moved": 2}) as move:
        await m.run_org("org-1")
    assert move.await_args.args[4] == "records/conn/r-b"


@pytest.mark.asyncio
async def test_a_copy_describing_a_record_that_is_gone_goes_where_handover_would_put_it() -> None:
    # Same rule as storage's handover: a live holder first, then the smallest id.
    m, kv = _migration(
        [_docs("v1")], {"v1": [_holder("r-c"), _holder("r-a", deleted=True), _holder("r-b")]},
        stored_owner="r-x",
    )
    with patch(MOVE, new_callable=AsyncMock, return_value={"moved": 2}) as move:
        await m.run_org("org-1")
    assert move.await_args.args[4] == "records/conn/r-b"


@pytest.mark.asyncio
async def test_orgs_are_listed_cross_org_and_each_runs_inside_its_own_org() -> None:
    m, _ = _migration([_docs("v1")], {"v1": [_holder("r1")]})
    seen: list[tuple[str, str | None]] = []

    async def orgs() -> list[dict]:
        seen.append(("orgs", org_context.current_or_system().kind))
        return [{"id": "org-1"}]

    async def storage_config(org_id: str) -> tuple:
        seen.append(("storage", org_context.current_org_id()))
        return {}, "http://node", "local"

    m.graph_provider.get_all_orgs = orgs
    m.blob_storage._get_auth_and_config = storage_config
    with patch(MOVE, new_callable=AsyncMock, return_value={"moved": 2}):
        assert await m.run_all_orgs() is True
    assert seen == [("orgs", "system"), ("storage", "org-1")]


@pytest.mark.asyncio
async def test_error_texts_that_differ_for_every_copy_do_not_pile_up() -> None:
    vrids = [f"v{i}" for i in range(200)]
    m, _ = _migration([_docs(*vrids)] * MAX_PASSES, {v: [_holder(v)] for v in vrids})
    errors = [RuntimeError(f"no such key {i}") for i in range(200 * MAX_PASSES)]
    with patch(MOVE, new_callable=AsyncMock, side_effect=errors):
        await m.run_org("org-1")
    assert len(m._reasons) <= 51 and sum(m._reasons.values()) == 200
    [call] = m.logger.error.call_args_list
    assert "other reasons (150)" in call.args[0] % call.args[1:]
