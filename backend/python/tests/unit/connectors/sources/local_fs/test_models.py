"""Tests for Local FS Pydantic models."""

import pytest
from pydantic import ValidationError

from app.connectors.sources.local_fs.models import (
    LocalFsFileEvent,
    LocalFsFileEventBatchRequest,
    LocalFsFileEventBatchStats,
    LocalFsFileEventSubmissionResponse,
)


class TestLocalFsFileEvent:
    def test_valid_minimal(self):
        ev = LocalFsFileEvent(
            type="CREATED",
            path="a/b.txt",
            oldPath=None,
            timestamp=1,
            size=10,
            isDirectory=False,
        )
        assert ev.type == "CREATED"
        assert ev.path == "a/b.txt"
        assert ev.oldPath is None
        assert ev.timestamp == 1
        assert ev.size == 10
        assert ev.isDirectory is False

    def test_valid_full(self):
        ev = LocalFsFileEvent(
            type="RENAMED",
            path="a/new.txt",
            oldPath="a/old.txt",
            timestamp=42,
            size=1024,
            isDirectory=False,
            contentField="file_0",
            sha256="0" * 64,
            mimeType="text/plain",
        )
        assert ev.oldPath == "a/old.txt"
        assert ev.contentField == "file_0"
        assert ev.sha256 == "0" * 64
        assert ev.mimeType == "text/plain"

    def test_optional_fields_default_to_none(self):
        ev = LocalFsFileEvent(
            type="DELETED",
            path="x",
            timestamp=1,
            isDirectory=False,
        )
        assert ev.oldPath is None
        assert ev.size is None
        assert ev.contentField is None
        assert ev.sha256 is None
        assert ev.mimeType is None

    def test_directory_event(self):
        ev = LocalFsFileEvent(
            type="CREATED",
            path="folder/",
            timestamp=1,
            isDirectory=True,
        )
        assert ev.isDirectory is True

    def test_requires_fields(self):
        with pytest.raises(ValidationError):
            LocalFsFileEvent()  # type: ignore[call-arg]

    @pytest.mark.parametrize(
        "missing",
        ["type", "path", "timestamp", "isDirectory"],
    )
    def test_each_required_field_individually(self, missing: str):
        kwargs = {
            "type": "CREATED",
            "path": "a",
            "timestamp": 1,
            "isDirectory": False,
        }
        kwargs.pop(missing)
        with pytest.raises(ValidationError) as ei:
            LocalFsFileEvent(**kwargs)  # type: ignore[arg-type]
        assert missing in str(ei.value)

    def test_rejects_wrong_types(self):
        with pytest.raises(ValidationError):
            LocalFsFileEvent(
                type="CREATED",
                path="a",
                timestamp="not-an-int",  # type: ignore[arg-type]
                isDirectory=False,
            )
        with pytest.raises(ValidationError):
            LocalFsFileEvent(
                type="CREATED",
                path="a",
                timestamp=1,
                isDirectory="not-a-bool",  # type: ignore[arg-type]
            )

    def test_size_zero_is_valid(self):
        # Empty files are legal (e.g. `touch foo`); size=0 must round-trip.
        ev = LocalFsFileEvent(
            type="CREATED",
            path="empty",
            timestamp=1,
            size=0,
            isDirectory=False,
        )
        assert ev.size == 0


class TestLocalFsFileEventBatchRequest:
    def test_valid_batch(self):
        req = LocalFsFileEventBatchRequest(
            batchId="b1",
            events=[
                LocalFsFileEvent(
                    type="MODIFIED",
                    path="x",
                    oldPath=None,
                    timestamp=2,
                    size=None,
                    isDirectory=False,
                )
            ],
            timestamp=99,
        )
        assert req.batchId == "b1"
        assert len(req.events) == 1
        assert req.timestamp == 99
        assert req.resetBeforeApply is False

    def test_reset_before_apply_default_false(self):
        req = LocalFsFileEventBatchRequest(
            batchId="b", events=[], timestamp=1
        )
        assert req.resetBeforeApply is False

    def test_reset_before_apply_can_be_true(self):
        req = LocalFsFileEventBatchRequest(
            batchId="b", events=[], timestamp=1, resetBeforeApply=True
        )
        assert req.resetBeforeApply is True

    def test_empty_events_allowed(self):
        # Batches with zero events are legal — the watcher may still want to
        # emit a heartbeat or reset signal.
        req = LocalFsFileEventBatchRequest(
            batchId="b-empty", events=[], timestamp=1
        )
        assert req.events == []

    def test_requires_batchId_and_timestamp(self):
        with pytest.raises(ValidationError):
            LocalFsFileEventBatchRequest(events=[], timestamp=1)  # type: ignore[call-arg]
        with pytest.raises(ValidationError):
            LocalFsFileEventBatchRequest(batchId="b", events=[])  # type: ignore[call-arg]

    def test_events_must_be_event_models(self):
        with pytest.raises(ValidationError):
            LocalFsFileEventBatchRequest(
                batchId="b",
                events=[{"not": "valid"}],  # type: ignore[list-item]
                timestamp=1,
            )


class TestLocalFsFileEventBatchStats:
    def test_valid(self):
        stats = LocalFsFileEventBatchStats(processed=3, deleted=1)
        assert stats.processed == 3
        assert stats.deleted == 1

    def test_zero_counts(self):
        stats = LocalFsFileEventBatchStats(processed=0, deleted=0)
        assert stats.processed == 0
        assert stats.deleted == 0

    def test_required_fields(self):
        with pytest.raises(ValidationError):
            LocalFsFileEventBatchStats()  # type: ignore[call-arg]
        with pytest.raises(ValidationError):
            LocalFsFileEventBatchStats(processed=1)  # type: ignore[call-arg]


class TestLocalFsFileEventSubmissionResponse:
    def _stats(self, processed: int = 0, deleted: int = 0) -> LocalFsFileEventBatchStats:
        return LocalFsFileEventBatchStats(processed=processed, deleted=deleted)

    def test_valid(self):
        resp = LocalFsFileEventSubmissionResponse(
            success=True,
            connectorId="conn-1",
            batchId="batch-1",
            stats=self._stats(processed=2, deleted=1),
        )
        assert resp.success is True
        assert resp.connectorId == "conn-1"
        assert resp.batchId == "batch-1"
        assert resp.stats.processed == 2
        assert resp.stats.deleted == 1

    def test_required_fields(self):
        # Missing every required field at once.
        with pytest.raises(ValidationError):
            LocalFsFileEventSubmissionResponse()  # type: ignore[call-arg]

    @pytest.mark.parametrize(
        "missing",
        ["success", "connectorId", "batchId", "stats"],
    )
    def test_each_required_field_individually(self, missing: str):
        kwargs: dict[str, object] = {
            "success": True,
            "connectorId": "c",
            "batchId": "b",
            "stats": self._stats(),
        }
        kwargs.pop(missing)
        with pytest.raises(ValidationError) as ei:
            LocalFsFileEventSubmissionResponse(**kwargs)  # type: ignore[arg-type]
        assert missing in str(ei.value)

    def test_stats_must_be_batch_stats_model(self):
        # A bare dict that doesn't match LocalFsFileEventBatchStats must fail
        # validation rather than be silently coerced.
        with pytest.raises(ValidationError):
            LocalFsFileEventSubmissionResponse(
                success=True,
                connectorId="c",
                batchId="b",
                stats={"unexpected": 1},  # type: ignore[arg-type]
            )

    def test_serializes_to_dict_with_camelcase_keys(self):
        resp = LocalFsFileEventSubmissionResponse(
            success=False,
            connectorId="conn-2",
            batchId="batch-2",
            stats=self._stats(processed=5, deleted=3),
        )
        # The router relies on these exact field names in the JSON response.
        dumped = resp.model_dump()
        assert dumped["success"] is False
        assert dumped["connectorId"] == "conn-2"
        assert dumped["batchId"] == "batch-2"
        assert dumped["stats"] == {"processed": 5, "deleted": 3}
