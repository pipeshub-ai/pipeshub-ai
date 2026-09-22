"""Tests for ENOENT retry logic in BlobStorage.apply() and metadata save.

Covers the cross-batch TOCTOU race: moveTreeLocal renames the directory
before updating MongoDB. The indexing service reads the stale MongoDB
path, builds a full path pointing at the renamed directory, and
fs.writeFile fails with ENOENT. The retry after 500ms gives the
MongoDB update time to land.
"""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.modules.transformers import blob_storage as bs_mod
from app.modules.transformers.blob_storage import BlobStorage


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _blob(**overrides) -> BlobStorage:
    """Build a BlobStorage with mocked dependencies."""
    b = BlobStorage(
        logger=MagicMock(),
        config_service=MagicMock(),
        graph_provider=MagicMock(),
    )
    for k, v in overrides.items():
        setattr(b, k, v)
    return b


@pytest.fixture(autouse=True)
def _no_sleep() -> Iterator[AsyncMock]:
    """Prevent real sleeps; verify sleep was called where expected."""
    with patch.object(bs_mod.asyncio, "sleep", AsyncMock()) as sleep:
        yield sleep


@pytest.fixture
def _no_auth():
    """Mock out _get_auth_and_config so HTTP calls don't need real config."""
    with patch.object(
        BlobStorage,
        "_get_auth_and_config",
        AsyncMock(return_value=({"Authorization": "Bearer tok"}, "http://node:3000", {})),
    ):
        yield


# ---------------------------------------------------------------------------
# Record buffer retry (apply method, lines 1053-1085)
# ---------------------------------------------------------------------------


class TestRecordBufferRetry:
    @pytest.mark.asyncio
    async def test_first_fail_retry_succeeds(self, _no_sleep, _no_auth):
        """update_record_buffer fails once, retry succeeds — no fallback."""
        b = _blob()
        b._build_hierarchical_storage_path = AsyncMock(return_value="records/conn/space/f.txt")
        b.get_document_id_by_virtual_record_id = AsyncMock(
            return_value={"record_doc_id": "doc-1"}
        )
        b._get_current_document_path = AsyncMock(return_value="org-1/PipesHub/records/conn/space/f.txt")

        call_count = 0

        async def failing_then_ok(org_id, doc_id, record_dict, vrid):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise FileNotFoundError("ENOENT: no such file or directory")
            return ("doc-1", 1024)

        b.update_record_buffer = failing_then_ok
        b.save_record_to_storage = AsyncMock(return_value=("doc-new", 2048))
        b.store_virtual_record_mapping = AsyncMock()
        b._save_metadata = AsyncMock()

        record = MagicMock()
        record.org_id = "org-1"
        record.id = "rec-1"
        record.virtual_record_id = "vrid-1"
        record.connector_id = "conn-1"
        record.record_group_id = "rg-1"
        record.model_dump.return_value = {"record_name": "f.txt"}

        ctx = MagicMock()
        ctx.record = record
        ctx.settings = {}

        result = await b.apply(ctx)

        assert call_count == 2
        b.save_record_to_storage.assert_not_called()
        _no_sleep.assert_awaited_once_with(0.5)

    @pytest.mark.asyncio
    async def test_both_fail_falls_back_to_new_upload(self, _no_sleep, _no_auth):
        """Both update_record_buffer attempts fail → falls back to save_record_to_storage."""
        b = _blob()
        b._build_hierarchical_storage_path = AsyncMock(return_value="records/conn/space/f.txt")
        b.get_document_id_by_virtual_record_id = AsyncMock(
            return_value={"record_doc_id": "doc-1"}
        )
        b._get_current_document_path = AsyncMock(return_value=None)

        async def always_fail(org_id, doc_id, record_dict, vrid):
            raise FileNotFoundError("ENOENT: directory renamed by move")

        b.update_record_buffer = always_fail
        b.save_record_to_storage = AsyncMock(return_value=("doc-new", 2048))
        b.store_virtual_record_mapping = AsyncMock()
        b._save_metadata = AsyncMock()

        record = MagicMock()
        record.org_id = "org-1"
        record.id = "rec-1"
        record.virtual_record_id = "vrid-1"
        record.connector_id = "conn-1"
        record.record_group_id = "rg-1"
        record.model_dump.return_value = {"record_name": "f.txt"}

        ctx = MagicMock()
        ctx.record = record
        ctx.settings = {}

        result = await b.apply(ctx)

        b.save_record_to_storage.assert_awaited_once()
        _no_sleep.assert_awaited_once_with(0.5)

    @pytest.mark.asyncio
    async def test_no_retry_when_first_call_succeeds(self, _no_sleep, _no_auth):
        """When update_record_buffer succeeds on first try, no sleep/retry."""
        b = _blob()
        b._build_hierarchical_storage_path = AsyncMock(return_value="records/conn/space/f.txt")
        b.get_document_id_by_virtual_record_id = AsyncMock(
            return_value={"record_doc_id": "doc-1"}
        )
        b._get_current_document_path = AsyncMock(return_value="org-1/PipesHub/records/conn/space/f.txt")
        b.update_record_buffer = AsyncMock(return_value=("doc-1", 1024))
        b.store_virtual_record_mapping = AsyncMock()
        b._save_metadata = AsyncMock()

        record = MagicMock()
        record.org_id = "org-1"
        record.id = "rec-1"
        record.virtual_record_id = "vrid-1"
        record.connector_id = "conn-1"
        record.record_group_id = "rg-1"
        record.model_dump.return_value = {"record_name": "f.txt"}

        ctx = MagicMock()
        ctx.record = record
        ctx.settings = {}

        await b.apply(ctx)

        _no_sleep.assert_not_awaited()


# ---------------------------------------------------------------------------
# Metadata buffer retry (lines 1986-2011)
# ---------------------------------------------------------------------------


class TestMetadataBufferRetry:
    @pytest.mark.asyncio
    async def test_metadata_first_fail_retry_succeeds(self, _no_sleep, _no_auth):
        """_update_metadata_buffer fails once, retry succeeds."""
        b = _blob()

        call_count = 0

        async def failing_then_ok(org_id, doc_id, metadata_dict, vrid):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise FileNotFoundError("ENOENT")
            return ("meta-1", 512)

        b._update_metadata_buffer = failing_then_ok
        b._create_metadata_document = AsyncMock(return_value="meta-new")

        async def get_doc(vrid, collection):
            return {"record_metadata_doc_id": "meta-existing"}

        b.graph_provider.get_document = get_doc
        b.graph_provider.batch_upsert_nodes = AsyncMock()

        await b.save_reconciliation_metadata(
            "org-1", "rec-1", "vrid-1", {"key": "val"},
            "records/conn/space/f.txt",
            connector_id="conn-1", record_group_id="rg-1",
        )

        assert call_count == 2
        b._create_metadata_document.assert_not_called()

    @pytest.mark.asyncio
    async def test_metadata_both_fail_falls_back_to_create(self, _no_sleep, _no_auth):
        """Both _update_metadata_buffer attempts fail → creates new metadata document."""
        b = _blob()

        async def always_fail(org_id, doc_id, metadata_dict, vrid):
            raise FileNotFoundError("ENOENT: moved")

        b._update_metadata_buffer = always_fail
        b._create_metadata_document = AsyncMock(return_value="meta-new")

        async def get_doc(vrid, collection):
            return {"record_metadata_doc_id": "meta-existing"}

        b.graph_provider.get_document = get_doc
        b.graph_provider.batch_upsert_nodes = AsyncMock()

        await b.save_reconciliation_metadata(
            "org-1", "rec-1", "vrid-1", {"key": "val"},
            "records/conn/space/f.txt",
            connector_id="conn-1", record_group_id="rg-1",
        )

        b._create_metadata_document.assert_awaited_once()
