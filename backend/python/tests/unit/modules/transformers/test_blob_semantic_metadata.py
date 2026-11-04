"""BlobStorage.rewrite_semantic_metadata: a metadata writer replaces only ``semantic_metadata`` of the
mapped stored record, by copy-on-write (a flag on) or in place (both off), and switches the mapping by
compare-and-set."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.modules.transformers.blob_storage import (
    BlobStorage,
    StoredRecordNotFound,
)
from app.modules.transformers.stored_write import (
    COW,
    IN_PLACE,
    RunSuperseded,
    SidecarWrite,
    StoredWrite,
    StoredWriteBusy,
)

COW_WRITE = StoredWrite(COW, "gen-1", "gen-1:meta-writer")
IN_PLACE_WRITE = StoredWrite(IN_PLACE, None, "none:meta-writer")
STORED = {
    "id": "rec-1", "record_name": "plan.pdf", "block_containers": {"blocks": [{"data": "b"}]},
    "semantic_metadata": {"summary": "old"}, "connector_id": "conn-owner",
}


def _node(**fields: Any) -> dict[str, Any]:  # noqa: ANN401
    document = fields.get("documentId", "d1")
    return {
        "id": "vr-1", "documentId": document, "record_doc_id": document, "storedRun": "gen-1:sink-run",
        "storedMeta": "enrich/classification.aa@4.json", "storedRev": "rev-1", "switchSeq": 3, **fields,
    }


def _storage(node: dict[str, Any] | None = None, stored: dict[str, Any] | None = None) -> BlobStorage:
    bs = BlobStorage(MagicMock(), MagicMock(), AsyncMock())
    bs._mapping_node = AsyncMock(return_value=node)  # type: ignore[method-assign]
    bs.get_record_from_storage = AsyncMock(return_value=dict(stored if stored is not None else STORED))  # type: ignore[method-assign]
    bs._get_document_row = AsyncMock(return_value={  # type: ignore[method-assign]
        "documentPath": "org-1/PipesHub/records/conn-owner/Team/plan",
        "customMetadata": [{"key": "connectorId", "value": "conn-owner"}, {"key": "recordGroupId", "value": "rg-1"}],
    })
    bs.save_record_to_storage = AsyncMock(return_value=("d2", 99))  # type: ignore[method-assign]
    bs._retire_document = AsyncMock()  # type: ignore[method-assign]
    bs._record_still_exists = AsyncMock(return_value=True)  # type: ignore[method-assign]
    bs._switch_mapping = AsyncMock(return_value=True)  # type: ignore[method-assign]
    bs._keep_owner_identity = AsyncMock()  # type: ignore[method-assign]
    return bs


async def _classify(stored: dict[str, Any]) -> dict[str, Any]:
    return {"summary": "new", "categories": ["x"]}


class TestCopyOnWrite:
    @pytest.mark.asyncio
    async def test_it_copies_the_document_with_only_the_metadata_changed_and_switches_by_compare_and_set(self) -> None:
        bs = _storage(_node())
        order: list[str] = []
        bs.save_record_to_storage.side_effect = lambda *a, **k: order.append("create") or ("d2", 99)
        bs._retire_document.side_effect = lambda *a: order.append("retire")
        bs._record_still_exists.side_effect = lambda *a: order.append("recheck") or True
        bs._switch_mapping.side_effect = lambda *a, **k: order.append("switch") or True
        filed: list[tuple[str, str | None, dict]] = []

        async def write(path: str, run: str | None, semantic: dict[str, Any]) -> bool:
            order.append("sidecar")
            filed.append((path, run, semantic))
            return True

        written = await bs.rewrite_semantic_metadata(
            "org-1", "rec-1", "vr-1", _classify, stored_write=COW_WRITE, sidecar=SidecarWrite(4, write),
        )

        # One download of the mapped document; every other field exactly as it was; no identity merge.
        bs.get_record_from_storage.assert_awaited_once()
        bs._keep_owner_identity.assert_not_awaited()
        assert written == {**STORED, "semantic_metadata": {"summary": "new", "categories": ["x"]}}
        created = bs.save_record_to_storage.await_args
        assert created.args[3] == written
        assert created.kwargs == {
            "document_path": "records/conn-owner/Team/plan", "connector_id": "conn-owner", "record_group_id": "rg-1",
        }
        # The sidecar, then the mark, then the re-check, then the switch.
        assert order == ["create", "sidecar", "retire", "recheck", "switch"]
        path, run, semantic = filed[0]
        assert path.startswith("enrich/classification.") and path.endswith("@4.json")
        assert run == "gen-1:sink-run" and semantic == {"summary": "new", "categories": ["x"]}
        bs._retire_document.assert_awaited_once_with("org-1", "d1")

        node, fields = bs._switch_mapping.await_args.args[2:4]
        assert node == _node()
        assert fields["documentId"] == fields["record_doc_id"] == fields["switchDoc"] == "d2"
        assert fields["storedMeta"] == path and fields["storedRun"] == "gen-1:sink-run"
        assert fields["switchSeq"] == 4 and fields["fileSizeBytes"] == 99
        assert fields["updatedAt"] == fields["switchUpdatedAt"] == fields["lastSwitchAt"]
        assert "storedRev" not in fields
        assert [(e["doc"], e["rev"], e["sidecar"]) for e in fields["retired"]] == [
            ("d1", "rev-1", "enrich/classification.aa@4.json"),
        ]
        assert bs._switch_mapping.await_args.kwargs["expect_also"] == {
            "storedRun": "gen-1:sink-run", "storedMeta": "enrich/classification.aa@4.json",
        }

    @pytest.mark.asyncio
    async def test_a_lost_compare_and_set_is_busy_and_leaves_the_copy_and_the_sidecar_for_the_stray_pass(self) -> None:
        bs = _storage(_node())
        bs._switch_mapping.return_value = False
        bs._delete_replaced_document = AsyncMock()  # type: ignore[method-assign]
        bs.purge_document = AsyncMock()  # type: ignore[method-assign]

        with pytest.raises(StoredWriteBusy):
            await bs.rewrite_semantic_metadata(
                "org-1", "rec-1", "vr-1", _classify, stored_write=COW_WRITE,
                sidecar=SidecarWrite(4, AsyncMock(return_value=True)),
            )

        bs._delete_replaced_document.assert_not_awaited()
        bs.purge_document.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_record_deleted_meanwhile_is_not_switched(self) -> None:
        bs = _storage(_node())
        bs._record_still_exists.return_value = False

        with pytest.raises(StoredWriteBusy):
            await bs.rewrite_semantic_metadata("org-1", "rec-1", "vr-1", _classify, stored_write=COW_WRITE)

        bs._switch_mapping.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_sidecar_that_cannot_be_filed_still_lets_the_stored_record_switch_and_names_a_path(self) -> None:
        """The stored record must get the classification; the read is stale_sidecar until the next write."""
        for sidecar in (SidecarWrite(4, AsyncMock(return_value=False)), None):
            bs = _storage(_node())
            await bs.rewrite_semantic_metadata("org-1", "rec-1", "vr-1", _classify, stored_write=COW_WRITE, sidecar=sidecar)
            fields = bs._switch_mapping.await_args.args[3]
            assert fields["storedMeta"].startswith("enrich/classification.")

    @pytest.mark.asyncio
    async def test_nothing_to_write_writes_nothing(self) -> None:
        bs = _storage(_node())

        async def unchanged(stored: dict[str, Any]) -> None:
            return None

        assert await bs.rewrite_semantic_metadata("org-1", "rec-1", "vr-1", unchanged, stored_write=COW_WRITE) is None

        bs.save_record_to_storage.assert_not_awaited()
        bs._switch_mapping.assert_not_awaited()
        bs._retire_document.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_stored_record_is_an_error(self) -> None:
        with pytest.raises(StoredRecordNotFound):
            await _storage(None).rewrite_semantic_metadata("org-1", "rec-1", "vr-1", _classify, stored_write=COW_WRITE)
        with pytest.raises(StoredRecordNotFound):
            await _missing_copy().rewrite_semantic_metadata("org-1", "rec-1", "vr-1", _classify, stored_write=COW_WRITE)

    @pytest.mark.asyncio
    async def test_a_purged_row_after_a_switch_reads_the_new_document_once_then_gives_up(self) -> None:
        """The row of the mapped document is gone because a switch landed and its sweep ran: re-read the
        mapping; the tags are never taken from the writer's own."""
        bs = _storage()
        bs._mapping_node.side_effect = [_node(), _node(documentId="d9", record_doc_id="d9"), _node(documentId="d9")]
        bs._get_document_row.side_effect = [None, {"documentPath": "org-1/PipesHub/records/x", "customMetadata": []}]

        await bs.rewrite_semantic_metadata("org-1", "rec-1", "vr-1", _classify, stored_write=COW_WRITE)

        assert bs._retire_document.await_args.args == ("org-1", "d9")

        bs = _storage()
        bs._mapping_node.side_effect = [_node(), _node(documentId="d9"), _node(documentId="d9"), _node(documentId="dA")]
        bs._get_document_row.return_value = None
        with pytest.raises(StoredWriteBusy):
            await bs.rewrite_semantic_metadata("org-1", "rec-1", "vr-1", _classify, stored_write=COW_WRITE)
        bs.save_record_to_storage.assert_not_awaited()


def _missing_copy() -> BlobStorage:
    bs = _storage(_node())
    bs.get_record_from_storage = AsyncMock(return_value=None)  # type: ignore[method-assign]
    return bs


class TestInPlace:
    @pytest.mark.asyncio
    async def test_a_servable_stored_run_is_invalidated_then_overwritten_then_switched_with_no_sidecar(self) -> None:
        bs = _storage(_node())
        bs.update_record_buffer = AsyncMock(return_value=("d1", 77))  # type: ignore[method-assign]
        bs._get_current_document_path = AsyncMock(return_value=None)  # type: ignore[method-assign]
        bs._live_holders = AsyncMock(return_value=["rec-1"])  # type: ignore[method-assign]
        sidecar = AsyncMock()

        written = await bs.rewrite_semantic_metadata(
            "org-1", "rec-1", "vr-1", _classify, stored_write=IN_PLACE_WRITE,
            sidecar=SidecarWrite(4, sidecar),
        )

        assert written["semantic_metadata"] == {"summary": "new", "categories": ["x"]}
        bs._keep_owner_identity.assert_not_awaited()
        sidecar.assert_not_awaited()
        calls = bs._switch_mapping.await_args_list
        assert len(calls) == 2
        invalidation, final = calls[0].args[3], calls[1].args[3]
        assert invalidation["storedRun"] == "none:meta-writer" and invalidation["storedMeta"] is None
        assert invalidation["switchSeq"] == 4 and "documentId" not in invalidation
        assert final["documentId"] == "d1" and final["switchSeq"] == 5 and final["switchDoc"] == "d1"
        assert calls[1].kwargs["expect_seq"] == 4
        bs.update_record_buffer.assert_awaited_once()
        assert bs.update_record_buffer.await_args.args[2] == written

    @pytest.mark.asyncio
    async def test_an_unservable_stored_run_is_not_invalidated_again(self) -> None:
        """R8-4: one compare-and-set in the steady state, the same graph write as before."""
        bs = _storage(_node(storedRun="none:old", storedMeta=None))
        bs.update_record_buffer = AsyncMock(return_value=("d1", 77))  # type: ignore[method-assign]
        bs._get_current_document_path = AsyncMock(return_value=None)  # type: ignore[method-assign]

        await bs.rewrite_semantic_metadata("org-1", "rec-1", "vr-1", _classify, stored_write=IN_PLACE_WRITE)

        assert bs._switch_mapping.await_count == 1
        assert bs._switch_mapping.await_args.args[3]["documentId"] == "d1"

    @pytest.mark.asyncio
    async def test_a_none_run_with_a_stored_meta_is_invalidated(self) -> None:
        bs = _storage(_node(storedRun="none:old"))
        bs.update_record_buffer = AsyncMock(return_value=("d1", 77))  # type: ignore[method-assign]
        bs._get_current_document_path = AsyncMock(return_value=None)  # type: ignore[method-assign]

        await bs.rewrite_semantic_metadata("org-1", "rec-1", "vr-1", _classify, stored_write=IN_PLACE_WRITE)

        assert bs._switch_mapping.await_count == 2

    @pytest.mark.asyncio
    async def test_a_record_deleted_meanwhile_is_not_switched(self) -> None:
        bs = _storage(_node(storedRun="none:old", storedMeta=None))
        bs.update_record_buffer = AsyncMock(return_value=("d1", 77))  # type: ignore[method-assign]
        bs._get_current_document_path = AsyncMock(return_value=None)  # type: ignore[method-assign]
        bs._record_still_exists.return_value = False

        with pytest.raises(RunSuperseded):
            await bs.rewrite_semantic_metadata("org-1", "rec-1", "vr-1", _classify, stored_write=IN_PLACE_WRITE)

        bs._switch_mapping.assert_not_awaited()
