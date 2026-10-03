"""Restoring KB records from the trash (KnowledgeBaseService.restore_record / restore_records).

Who may restore, which batch comes back, the "restore the folder first" rule,
"name (restored)" on a clash, a refusal from the write path passed on word for
word, and the re-index that follows.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import (
    Connectors,
    DeleteSource,
    OriginTypes,
    ProgressStatus,
)
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    RestoreRefused,
)
from app.connectors.sources.localKB.handlers.kb_service import (
    MAX_RESTORE_RECORD_IDS,
    RESTORE_TURNED_OFF_REASON,
    KnowledgeBaseService,
    restored_name,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

MODULE = "app.connectors.sources.localKB.handlers.kb_service"
KB = "kb-1"
ORG = "org-1"


def _doc(key: str, *, name: str = "a.pdf", deleted: bool = True, batch: str = "b-1", **extra) -> dict:
    doc = {
        "_key": key,
        "orgId": ORG,
        "connectorId": KB,
        "origin": OriginTypes.UPLOAD.value,
        "connectorName": Connectors.KNOWLEDGE_BASE.value,
        "recordName": name,
        "isDeleted": deleted,
        "deleteBatchId": batch if deleted else None,
        "indexingStatus": ProgressStatus.COMPLETED.value,
    }
    doc.update(extra)
    return doc


def _member(doc: dict, *, is_file: bool = True, parent: str | None = None, parent_deleted: bool | None = None,
            parent_name: str | None = None, relation: str | None = None, mime: str = "application/pdf") -> dict:
    return {
        "record": doc,
        "parentId": parent,
        "parentRelation": relation or ("PARENT_CHILD" if parent else None),
        "parentIsDeleted": parent_deleted,
        "parentBatchId": None,
        "parentName": parent_name,
        "isFile": is_file,
        "fileMimeType": mime if is_file else None,
    }


@pytest.fixture(autouse=True)
def flag_on() -> Iterator[AsyncMock]:
    with patch(f"{MODULE}.is_soft_delete_enabled", AsyncMock(return_value=True)) as flag:
        yield flag


@pytest.fixture
def svc(service: KnowledgeBaseService, mock_processor: AsyncMock) -> KnowledgeBaseService:
    gp = service.graph_provider
    gp.get_user_by_user_id = AsyncMock(return_value={"id": "uk1", "_key": "uk1"})
    gp.get_user_kb_permission = AsyncMock(return_value="OWNER")
    gp._fetch_existing_file_names_in_parent = AsyncMock(return_value=set())
    gp._normalized_name_variants_lower = MagicMock(side_effect=lambda name: [name.lower()])
    gp.find_folder_by_name_in_parent = AsyncMock(return_value=None)
    gp.get_typed_records_batch = AsyncMock(side_effect=lambda ids: {i: MagicMock(id=i) for i in ids})
    gp.get_file_record_by_id = AsyncMock(side_effect=lambda rid: MagicMock(id=rid, record_name="old"))
    mock_processor.restore_trashed_records = AsyncMock(side_effect=lambda kb, batch, items, **kw: [i["id"] for i in items])
    mock_processor.reindex_existing_records = AsyncMock(side_effect=lambda records: [r.id for r in records])
    return service


def _batch(svc, record: dict, members: list[dict]) -> None:
    svc.graph_provider.get_document = AsyncMock(return_value=record)
    svc.graph_provider.get_records_in_delete_batch = AsyncMock(return_value=members)


class TestRestoredName:
    @pytest.mark.parametrize(
        ("name", "attempt", "is_file", "expected"),
        [
            ("report.pdf", 1, True, "report (restored).pdf"),
            ("report.pdf", 2, True, "report (restored 2).pdf"),
            ("archive.tar.gz", 1, True, "archive.tar (restored).gz"),
            (".bashrc", 1, True, ".bashrc (restored)"),
            ("notes", 1, True, "notes (restored)"),
            ("Q3.final", 1, False, "Q3.final (restored)"),
        ],
    )
    def test_names(self, name, attempt, is_file, expected) -> None:
        assert restored_name(name, attempt, is_file=is_file) == expected


class TestWhoMayRestore:
    async def test_flag_off_refuses_with_the_way_to_turn_it_on(self, svc, flag_on) -> None:
        flag_on.return_value = False
        result = await svc.restore_record("r1", "u1", ORG)
        assert (result["code"], result["reason"]) == (403, RESTORE_TURNED_OFF_REASON)
        svc.graph_provider.get_document.assert_not_called()

    @pytest.mark.parametrize("stored", [None, _doc("r1", orgId="org-other")])
    async def test_a_missing_or_foreign_record_reads_as_not_found(self, svc, stored) -> None:
        svc.graph_provider.get_document = AsyncMock(return_value=stored)
        result = await svc.restore_record("r1", "u1", ORG)
        assert result["code"] == 404 and result["success"] is False

    async def test_a_connector_record_is_refused_with_where_to_restore_it(self, svc) -> None:
        _batch(svc, _doc("r1", origin=OriginTypes.CONNECTOR.value, connectorName="DRIVE"), [])
        result = await svc.restore_record("r1", "u1", ORG)
        assert result["code"] == 403
        assert "restore it there" in result["reason"]

    async def test_no_role_on_the_kb_reads_as_not_found(self, svc) -> None:
        svc.graph_provider.get_user_kb_permission = AsyncMock(return_value=None)
        _batch(svc, _doc("r1"), [_member(_doc("r1"))])
        result = await svc.restore_record("r1", "u1", ORG)
        assert result["code"] == 404
        svc.graph_provider.get_records_in_delete_batch.assert_not_called()

    @pytest.mark.parametrize(("role", "allowed"), [("FILEORGANIZER", True), ("WRITER", True), ("READER", False)])
    async def test_a_single_file_needs_the_single_file_delete_role(self, svc, mock_processor, role, allowed) -> None:
        svc.graph_provider.get_user_kb_permission = AsyncMock(return_value=role)
        _batch(svc, _doc("r1"), [_member(_doc("r1"))])
        result = await svc.restore_record("r1", "u1", ORG)
        assert result["success"] is allowed
        if not allowed:
            assert result["code"] == 403
            mock_processor.restore_trashed_records.assert_not_called()

    async def test_a_folder_needs_owner_or_writer(self, svc, mock_processor) -> None:
        svc.graph_provider.get_user_kb_permission = AsyncMock(return_value="FILEORGANIZER")
        folder, child = _doc("f1", name="Docs"), _doc("c1")
        _batch(svc, folder, [_member(folder, is_file=False), _member(child, parent="f1", parent_deleted=True)])
        result = await svc.restore_record("f1", "u1", ORG)
        assert result["code"] == 403
        mock_processor.restore_trashed_records.assert_not_called()


class TestWhatComesBack:
    async def test_a_live_record_restores_nothing(self, svc, mock_processor) -> None:
        _batch(svc, _doc("r1", deleted=False), [])
        result = await svc.restore_record("r1", "u1", ORG)
        assert result["success"] is True and result["restoredRecords"] == []
        mock_processor.restore_trashed_records.assert_not_called()

    async def test_the_whole_batch_comes_back_and_its_files_are_indexed_again(self, svc, mock_processor) -> None:
        folder = _doc("f1", name="Docs")
        child = _doc("c1", name="a.pdf")
        manual = _doc("m1", name="m.pdf", indexingStatus=ProgressStatus.AUTO_INDEX_OFF.value)
        _batch(svc, child, [
            _member(folder, is_file=False),
            _member(child, parent="f1", parent_deleted=True),
            _member(manual, parent="f1", parent_deleted=True),
        ])

        result = await svc.restore_record("c1", "u1", ORG)

        assert result["success"] is True, result
        assert {r["recordId"] for r in result["restoredRecords"]} == {"f1", "c1", "m1"}
        kb, batch, items = mock_processor.restore_trashed_records.await_args.args
        assert (kb, batch) == (KB, "b-1")
        assert mock_processor.restore_trashed_records.await_args.kwargs == {"restore_source": DeleteSource.USER}
        sets = {i["id"]: i["set"] for i in items}
        assert sets == {"f1": {}, "c1": {"indexingStatus": ProgressStatus.NOT_STARTED.value}, "m1": {}}
        (reindexed,) = mock_processor.reindex_existing_records.await_args.args
        assert [r.id for r in reindexed] == ["c1"]
        assert "reindexPending" not in result

    async def test_the_folder_it_was_in_must_be_restored_first(self, svc, mock_processor) -> None:
        child = _doc("c1", name="a.pdf")
        _batch(svc, child, [_member(child, parent="f1", parent_deleted=True, parent_name="Docs")])
        result = await svc.restore_record("c1", "u1", ORG)
        assert result["code"] == 409
        assert result["reason"] == "'a.pdf' was in 'Docs', which is also in the trash. Restore 'Docs' first, then restore 'a.pdf'."
        assert result["parentId"] == "f1"
        mock_processor.restore_trashed_records.assert_not_called()

    async def test_a_record_gone_from_its_batch_is_refused(self, svc, mock_processor) -> None:
        _batch(svc, _doc("r1"), [])
        result = await svc.restore_record("r1", "u1", ORG)
        assert result["code"] == 409 and "Refresh the page" in result["reason"]
        mock_processor.restore_trashed_records.assert_not_called()

    async def test_a_refusal_from_the_write_path_is_passed_on(self, svc, mock_processor) -> None:
        _batch(svc, _doc("r1"), [_member(_doc("r1"))])
        mock_processor.restore_trashed_records = AsyncMock(
            side_effect=RestoreRefused(409, "delete 'b.pdf' first", conflicting_record_id="r9")
        )
        result = await svc.restore_record("r1", "u1", ORG)
        assert (result["code"], result["reason"], result["conflicting_record_id"]) == (409, "delete 'b.pdf' first", "r9")
        mock_processor.reindex_existing_records.assert_not_called()

    async def test_an_unqueued_reindex_is_reported_with_what_to_do(self, svc, mock_processor) -> None:
        _batch(svc, _doc("r1"), [_member(_doc("r1"))])
        mock_processor.reindex_existing_records = AsyncMock(return_value=[])
        result = await svc.restore_record("r1", "u1", ORG)
        assert result["success"] is True
        assert result["reindexPendingRecordIds"] == ["r1"]
        assert "Reindex" in result["reindexPendingReason"]

    async def test_an_unexpected_failure_says_try_again(self, svc) -> None:
        svc.graph_provider.get_document = AsyncMock(side_effect=RuntimeError("boom"))
        result = await svc.restore_record("r1", "u1", ORG)
        assert result["code"] == 500 and "boom" not in result["reason"]


class TestNameClash:
    async def test_a_file_whose_name_is_taken_comes_back_renamed(self, svc, mock_processor) -> None:
        doc = _doc("r1", name="Report.pdf")
        _batch(svc, doc, [_member(doc)])
        # Any type: a name alone clashes, as the design says.
        svc.graph_provider._fetch_existing_file_names_in_parent = AsyncMock(
            return_value={("report.pdf", ""), ("report (restored).pdf", "text/plain")}
        )

        result = await svc.restore_record("r1", "u1", ORG)

        assert result["restoredRecords"] == [
            {"recordId": "r1", "name": "Report (restored 2).pdf", "renamedFrom": "Report.pdf"}
        ]
        renamed = mock_processor.on_record_metadata_update.await_args.args[0]
        assert renamed.record_name == "Report (restored 2).pdf"
        svc.graph_provider._fetch_existing_file_names_in_parent.assert_awaited_once_with(
            kb_id=KB, parent_folder_id=None
        )
        # Renamed before the reindex, so indexing sees the new name.
        assert mock_processor.method_calls.index(
            next(c for c in mock_processor.method_calls if c[0] == "on_record_metadata_update")
        ) < mock_processor.method_calls.index(
            next(c for c in mock_processor.method_calls if c[0] == "reindex_existing_records")
        )

    async def test_a_folder_clash_is_checked_in_its_live_parent(self, svc, mock_processor) -> None:
        folder = _doc("f2", name="Docs")
        _batch(svc, folder, [_member(folder, is_file=False, parent="f0", parent_deleted=False)])
        svc.graph_provider.find_folder_by_name_in_parent = AsyncMock(
            side_effect=lambda **kw: {"_key": "x"} if kw["folder_name"] == "Docs" else None
        )
        result = await svc.restore_record("f2", "u1", ORG)
        assert result["restoredRecords"][0]["name"] == "Docs (restored)"
        assert svc.graph_provider.find_folder_by_name_in_parent.await_args_list[0].kwargs["parent_folder_id"] == "f0"
        mock_processor.reindex_existing_records.assert_not_called()

    async def test_children_of_a_restored_folder_are_not_checked(self, svc) -> None:
        folder, child = _doc("f1", name="Docs"), _doc("c1", name="a.pdf")
        _batch(svc, folder, [_member(folder, is_file=False), _member(child, parent="f1", parent_deleted=True)])
        await svc.restore_record("f1", "u1", ORG)
        svc.graph_provider._fetch_existing_file_names_in_parent.assert_not_called()

    async def test_two_roots_of_one_batch_never_take_the_same_name(self, svc) -> None:
        a, b = _doc("r1", name="a.pdf"), _doc("r2", name="a (restored).pdf")
        _batch(svc, a, [_member(a), _member(b)])
        svc.graph_provider._fetch_existing_file_names_in_parent = AsyncMock(return_value={("a.pdf", "")})
        result = await svc.restore_record("r1", "u1", ORG)
        names = {r["recordId"]: r["name"] for r in result["restoredRecords"]}
        assert names == {"r1": "a (restored).pdf", "r2": "a (restored) (restored).pdf"}

    async def test_no_free_name_refuses_before_restoring(self, svc, mock_processor) -> None:
        _batch(svc, _doc("r1"), [_member(_doc("r1"))])
        svc.graph_provider._normalized_name_variants_lower = MagicMock(return_value=["taken"])
        svc.graph_provider._fetch_existing_file_names_in_parent = AsyncMock(return_value={("taken", "")})
        result = await svc.restore_record("r1", "u1", ORG)
        assert result["code"] == 409 and "Rename or move" in result["reason"]
        mock_processor.restore_trashed_records.assert_not_called()

    async def test_a_failed_rename_is_reported_and_the_restore_stands(self, svc, mock_processor) -> None:
        doc = _doc("r1", name="report.pdf")
        _batch(svc, doc, [_member(doc)])
        svc.graph_provider._fetch_existing_file_names_in_parent = AsyncMock(return_value={("report.pdf", "")})
        mock_processor.on_record_metadata_update = AsyncMock(side_effect=RuntimeError("down"))
        result = await svc.restore_record("r1", "u1", ORG)
        assert result["success"] is True
        assert result["restoredRecords"] == [{"recordId": "r1", "name": "report.pdf"}]
        assert result["renamePendingRecordIds"] == ["r1"]


class TestBulk:
    @pytest.mark.parametrize("ids", [[], [f"r{i}" for i in range(MAX_RESTORE_RECORD_IDS + 1)]])
    async def test_an_empty_or_oversized_request_is_refused(self, svc, ids) -> None:
        result = await svc.restore_records(ids, "u1", ORG)
        assert result["code"] == 400 and str(MAX_RESTORE_RECORD_IDS) in result["reason"]

    async def test_ids_of_one_batch_restore_it_once_and_each_id_gets_an_outcome(self, svc, mock_processor) -> None:
        folder, child = _doc("f1", name="Docs"), _doc("c1")
        members = [_member(folder, is_file=False), _member(child, parent="f1", parent_deleted=True)]
        docs = {"f1": folder, "c1": child, "x1": None}
        svc.graph_provider.get_document = AsyncMock(side_effect=lambda key, collection: docs[key])
        svc.graph_provider.get_records_in_delete_batch = AsyncMock(return_value=members)

        result = await svc.restore_records(["f1", "c1", "x1"], "u1", ORG)

        assert mock_processor.restore_trashed_records.await_count == 1
        assert [(r["recordId"], r["success"]) for r in result["results"]] == [
            ("f1", True), ("c1", True), ("x1", False),
        ]
        assert (result["restoredCount"], result["failedCount"], result["success"]) == (2, 1, False)
