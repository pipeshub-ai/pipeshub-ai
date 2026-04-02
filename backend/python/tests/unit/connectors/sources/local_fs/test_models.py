"""Tests for Local FS Pydantic models."""

import pytest
from pydantic import ValidationError

from app.connectors.sources.local_fs.models import (
    LocalFsFileEvent,
    LocalFsFileEventBatchRequest,
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

    def test_requires_fields(self):
        with pytest.raises(ValidationError):
            LocalFsFileEvent()  # type: ignore[call-arg]


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
