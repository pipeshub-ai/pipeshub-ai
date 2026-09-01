"""Tests for Local FS Pydantic models."""

import pytest
from pydantic import ValidationError

from app.connectors.sources.local_fs.models import (
    LocalFsFileEvent,
    LocalFsFileEventBatchStats,
    LocalFsPullBatch,
    LocalFsPullRequest,
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
            sha256="0" * 64,
            mimeType="text/plain",
        )
        assert ev.oldPath == "a/old.txt"
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


class TestLocalFsPullRequest:
    def _kwargs(self, **overrides) -> dict:
        kwargs = {
            "connectorId": "conn-1",
            "runId": "run-1",
            "batchIndex": 0,
            "mode": "FULL",
            "cursor": None,
            "maxEvents": 50,
            "timeoutMs": 60_000,
        }
        kwargs.update(overrides)
        return kwargs

    def test_valid(self):
        req = LocalFsPullRequest(**self._kwargs())
        assert req.mode == "FULL"
        assert req.cursor is None

    def test_mode_is_constrained(self):
        LocalFsPullRequest(**self._kwargs(mode="INCREMENTAL"))
        with pytest.raises(ValidationError):
            LocalFsPullRequest(**self._kwargs(mode="PARTIAL"))

    def test_cursor_is_optional(self):
        req = LocalFsPullRequest(
            connectorId="c",
            runId="r",
            batchIndex=3,
            mode="INCREMENTAL",
            maxEvents=10,
            timeoutMs=1000,
        )
        assert req.cursor is None


class TestLocalFsPullBatch:
    def test_valid_with_events(self):
        batch = LocalFsPullBatch(
            connectorId="conn-1",
            runId="run-1",
            batchIndex=0,
            cursor="j:42",
            hasMore=True,
            events=[
                {
                    "type": "CREATED",
                    "path": "a.txt",
                    "timestamp": 1,
                    "isDirectory": False,
                }
            ],
        )
        assert batch.hasMore is True
        assert batch.events[0].path == "a.txt"
        assert batch.rootPath is None

    def test_events_default_empty(self):
        # A page with no events but hasMore set is the desktop's keepalive.
        batch = LocalFsPullBatch(
            connectorId="c", runId="r", batchIndex=1, hasMore=True
        )
        assert batch.events == []

    def test_requires_identity_fields(self):
        # connectorId/runId are what let a run reject a reply from the wrong
        # machine, so they must not be optional.
        with pytest.raises(ValidationError):
            LocalFsPullBatch(runId="r", batchIndex=0, hasMore=False)  # type: ignore[call-arg]
        with pytest.raises(ValidationError):
            LocalFsPullBatch(connectorId="c", batchIndex=0, hasMore=False)  # type: ignore[call-arg]


class TestLocalFsFileEventBatchStats:
    def test_valid(self):
        stats = LocalFsFileEventBatchStats(processed=3, deleted=1, skipped=2)
        assert stats.processed == 3
        assert stats.deleted == 1
        assert stats.skipped == 2

    def test_skipped_defaults_to_zero(self):
        stats = LocalFsFileEventBatchStats(processed=0, deleted=0)
        assert stats.skipped == 0

    def test_required_fields(self):
        with pytest.raises(ValidationError):
            LocalFsFileEventBatchStats()  # type: ignore[call-arg]
        with pytest.raises(ValidationError):
            LocalFsFileEventBatchStats(processed=1)  # type: ignore[call-arg]
