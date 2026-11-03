"""An upload records what set its source timestamps: the upload time or the file's own."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

UPLOADED = 1792090099000  # 2026-10-15 18:48:19 UTC
FILE_MTIME = 1741000000000  # 2025-03-03 11:06:40 UTC
# Node received the new version ten minutes before its storage upload ended.
RECEIVED = UPLOADED - 600_000


def _upload_dicts(source_ms: int) -> tuple[dict, dict]:
    record = {
        "_key": "rec-1",
        "orgId": "org-1",
        "recordName": "notes.txt",
        "externalRecordId": "doc-1",
        "recordType": "FILE",
        "origin": "UPLOAD",
        "connectorId": "kb-1",
        "createdAtTimestamp": UPLOADED,
        "updatedAtTimestamp": UPLOADED,
        "sourceCreatedAtTimestamp": source_ms,
        "sourceLastModifiedTimestamp": source_ms,
        "version": 1,
    }
    return record, {"_key": "rec-1", "orgId": "org-1", "name": "notes.txt", "isFile": True}


class TestUploadStamp:
    def test_a_file_sent_without_its_own_time_is_dated_by_the_upload(self, service) -> None:
        record, file_record = _upload_dicts(UPLOADED - 25)
        built = service._build_kb_file_record("kb-1", record, file_record, None, "")
        assert built.source_timestamp_origin == "upload"
        assert built.to_arango_base_record()["sourceTimestampOrigin"] == "upload"

    def test_a_file_sent_with_its_last_modified_time_is_dated_by_the_file(self, service) -> None:
        record, file_record = _upload_dicts(FILE_MTIME)
        built = service._build_kb_file_record("kb-1", record, file_record, None, "")
        assert built.source_timestamp_origin == "file"


class TestNewVersionStamp:
    @staticmethod
    def _editable(service) -> MagicMock:
        service.graph_provider._get_kb_context_for_record = AsyncMock(return_value={"kb_id": "kb-1"})
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="WRITER")
        service.graph_provider.get_document = AsyncMock(return_value={"recordName": "notes"})
        stored = MagicMock()
        stored.source_timestamp_origin = "file"
        service.graph_provider.get_file_record_by_id = AsyncMock(return_value=stored)
        return stored

    @pytest.mark.asyncio
    async def test_a_new_version_without_a_file_time_is_dated_by_the_upload(self, service) -> None:
        stored = self._editable(service)
        with patch(
            "app.connectors.sources.localKB.handlers.kb_service.get_epoch_timestamp_in_ms",
            return_value=UPLOADED,
        ):
            result = await service.update_record("user1", "rec-1", {}, file_metadata={"lastModified": UPLOADED - 900})
        assert result["success"] is True
        assert stored.source_updated_at == UPLOADED - 900
        assert stored.source_timestamp_origin == "upload"

    @pytest.mark.asyncio
    async def test_a_new_version_with_its_file_time_is_dated_by_the_file(self, service) -> None:
        stored = self._editable(service)
        with patch(
            "app.connectors.sources.localKB.handlers.kb_service.get_epoch_timestamp_in_ms",
            return_value=UPLOADED,
        ):
            await service.update_record("user1", "rec-1", {}, file_metadata={"lastModified": FILE_MTIME})
        assert stored.source_timestamp_origin == "file"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("last_modified", "origin"),
        [(RECEIVED - 5, "upload"), (FILE_MTIME, "file")],
        ids=["node-fallback", "file-time"],
    )
    async def test_a_slow_storage_upload_is_judged_by_nodes_clock(
        self, service, last_modified: int, origin: str,
    ) -> None:
        stored = self._editable(service)
        with patch(
            "app.connectors.sources.localKB.handlers.kb_service.get_epoch_timestamp_in_ms",
            return_value=UPLOADED,
        ):
            await service.update_record(
                "user1", "rec-1", {}, file_metadata={"lastModified": last_modified, "receivedAt": RECEIVED},
            )
        assert stored.source_timestamp_origin == origin

    @pytest.mark.asyncio
    async def test_a_rename_leaves_it_alone(self, service) -> None:
        stored = self._editable(service)
        await service.update_record("user1", "rec-1", {"recordName": "renamed"})
        assert stored.source_timestamp_origin == "file"


class TestNewVersionOfAContentDatedUpload:
    @pytest.mark.asyncio
    async def test_its_dates_go_back_to_what_uploads_gave_it(self, service) -> None:
        stored = TestNewVersionStamp._editable(service)
        stored.source_timestamp_origin = "content"
        stored.source_created_at = 1739232000000
        stored.created_at = UPLOADED - 86_400_000
        service.graph_provider.get_document = AsyncMock(return_value={
            "sourceTimestampOrigin": "content", "uploadSourceCreatedAtTimestamp": UPLOADED - 86_400_100,
        })
        with patch(
            "app.connectors.sources.localKB.handlers.kb_service.get_epoch_timestamp_in_ms",
            return_value=UPLOADED,
        ):
            await service.update_record("user1", "rec-1", {}, file_metadata={"lastModified": UPLOADED - 900})
        # The new version's content is dated again once it is indexed.
        assert stored.source_created_at == UPLOADED - 86_400_100
        assert stored.source_updated_at == UPLOADED - 900
        assert stored.source_timestamp_origin == "upload"
