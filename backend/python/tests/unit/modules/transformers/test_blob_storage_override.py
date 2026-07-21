"""Tests for Phase 1 (non-versioned override) and Phase 2 (hierarchical paths)
in BlobStorage:
  - update_record_buffer
  - apply() now uses update_record_buffer for existing docs
  - _build_hierarchical_storage_path
  - _sanitize_path_segment
  - save_record_to_storage uses isVersionedFile=false and hierarchical path
"""

from unittest.mock import AsyncMock, MagicMock, patch, call
import json

import pytest

from app.modules.transformers.blob_storage import BlobStorage
from app.utils.storage_path import sanitize_path_segment


# ---------------------------------------------------------------------------
# Helpers (same pattern as test_blob_storage_sql_reconciliation.py)
# ---------------------------------------------------------------------------


def _make_bs(*, config_service=None, graph_provider=None):
    return BlobStorage(
        logger=MagicMock(),
        config_service=config_service or AsyncMock(),
        graph_provider=graph_provider or AsyncMock(),
    )


def _configure_auth(bs, storage_type="local"):
    bs.config_service.get_config = AsyncMock(
        side_effect=[
            {"scopedJwtSecret": "secret"},
            {"cm": {"endpoint": "http://localhost:3001"}},
            {"storageType": storage_type},
        ]
    )


def _resp(status=200, json_value=None, text_value=""):
    r = AsyncMock()
    r.status = status
    r.json = AsyncMock(return_value=json_value or {})
    r.text = AsyncMock(return_value=text_value)
    r.__aenter__ = AsyncMock(return_value=r)
    r.__aexit__ = AsyncMock(return_value=False)
    return r


def _mock_session(put_resp=None, post_resp=None):
    """Build a mock aiohttp session with configurable responses."""
    session = AsyncMock()
    if put_resp is not None:
        session.put = MagicMock(return_value=put_resp)
    if post_resp is not None:
        session.post = MagicMock(return_value=post_resp)
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    return session


# ---------------------------------------------------------------------------
# TestUpdateRecordBuffer (Phase 1)
# ---------------------------------------------------------------------------


class TestUpdateRecordBuffer:
    @pytest.mark.asyncio
    async def test_success_returns_doc_id_and_size(self):
        """Happy path: PUT /buffer returns 200, method returns (document_id, size)."""
        bs = _make_bs()
        _configure_auth(bs, "local")

        put_resp = _resp(200)
        session = _mock_session(put_resp=put_resp)

        with patch("app.modules.transformers.blob_storage.aiohttp.ClientSession", return_value=session):
            doc_id, size = await bs.update_record_buffer(
                "org-1", "doc-123", {"key": "val"}, "vr-abc"
            )

        assert doc_id == "doc-123"
        assert isinstance(size, int)
        assert size > 0

    @pytest.mark.asyncio
    async def test_uses_put_method_to_buffer_url(self):
        """Verify the request goes to the correct PUT /buffer endpoint."""
        bs = _make_bs()
        _configure_auth(bs, "local")

        put_resp = _resp(200)
        session = _mock_session(put_resp=put_resp)

        with patch("app.modules.transformers.blob_storage.aiohttp.ClientSession", return_value=session):
            await bs.update_record_buffer("org-1", "doc-456", {}, "vr-1")

        # session.put should have been called
        assert session.put.called
        call_args = session.put.call_args
        url = call_args[0][0]
        assert "doc-456" in url
        assert "buffer" in url

    @pytest.mark.asyncio
    async def test_non_200_response_raises(self):
        """Non-200 HTTP response raises an Exception."""
        bs = _make_bs()
        _configure_auth(bs, "local")

        put_resp = _resp(500, text_value="internal error")
        session = _mock_session(put_resp=put_resp)

        with patch("app.modules.transformers.blob_storage.aiohttp.ClientSession", return_value=session):
            with pytest.raises(Exception, match="Failed to update buffer"):
                await bs.update_record_buffer("org-1", "doc-789", {}, "vr-2")

    @pytest.mark.asyncio
    async def test_compression_failure_falls_back_to_uncompressed(self):
        """If compression raises, the method still sends uncompressed data."""
        bs = _make_bs()
        _configure_auth(bs, "local")
        bs._compress_record = MagicMock(side_effect=RuntimeError("zstd boom"))

        put_resp = _resp(200)
        session = _mock_session(put_resp=put_resp)

        with patch("app.modules.transformers.blob_storage.aiohttp.ClientSession", return_value=session):
            doc_id, size = await bs.update_record_buffer(
                "org-1", "doc-fallback", {"data": "hello"}, "vr-3"
            )

        assert doc_id == "doc-fallback"
        assert size > 0


# ---------------------------------------------------------------------------
# TestApplyUsesOverride (Phase 1)
# ---------------------------------------------------------------------------


class TestApplyUsesOverride:
    def _make_transform_ctx(self, virtual_record_id="vr-1"):
        """Build a minimal TransformContext mock."""
        from unittest.mock import MagicMock
        record = MagicMock()
        record.org_id = "org-1"
        record.id = "rec-1"
        record.virtual_record_id = virtual_record_id
        record.connector_id = "conn-1"
        record.record_group_id = None
        record.model_dump = MagicMock(return_value={"id": "rec-1"})
        ctx = MagicMock()
        ctx.record = record
        return ctx

    @pytest.mark.asyncio
    async def test_existing_doc_always_buffer_overrides(self):
        """Existing docs are always updated in place via buffer override,
        regardless of where the blob currently lives."""
        bs = _make_bs()
        bs.get_document_id_by_virtual_record_id = AsyncMock(
            return_value={"record_doc_id": "doc-existing"}
        )
        bs._build_hierarchical_storage_path = AsyncMock(return_value="records/conn-1/Finance/doc.pdf")
        bs._get_current_document_path = AsyncMock(return_value="org-1/PipesHub/records/vr-1")
        bs.update_record_buffer = AsyncMock(return_value=("doc-existing", 100))
        bs.save_record_to_storage = AsyncMock(return_value=("doc-new", 200))
        bs.store_virtual_record_mapping = AsyncMock()
        bs._clean_empty_values = MagicMock(side_effect=lambda x: x)

        ctx = self._make_transform_ctx()
        await bs.apply(ctx)

        bs.update_record_buffer.assert_awaited_once()
        bs.save_record_to_storage.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_existing_doc_buffer_failure_falls_back_to_new_upload(self):
        """When buffer override fails, apply() falls back to creating a new
        document at the hierarchical storage path."""
        bs = _make_bs()
        bs.get_document_id_by_virtual_record_id = AsyncMock(
            return_value={"record_doc_id": "doc-existing"}
        )
        bs._build_hierarchical_storage_path = AsyncMock(return_value="records/conn-1/Finance/doc.pdf")
        bs.update_record_buffer = AsyncMock(side_effect=Exception("buffer 404"))
        bs.save_record_to_storage = AsyncMock(return_value=("doc-new", 200))
        bs.store_virtual_record_mapping = AsyncMock()
        bs._clean_empty_values = MagicMock(side_effect=lambda x: x)

        ctx = self._make_transform_ctx()
        await bs.apply(ctx)

        bs.save_record_to_storage.assert_awaited_once()
        call_kwargs = bs.save_record_to_storage.call_args
        assert call_kwargs.kwargs.get("document_path") == "records/conn-1/Finance/doc.pdf"

    @pytest.mark.asyncio
    async def test_storage_path_reflects_actual_content_location(self):
        """After buffer override, ctx.settings['storage_path'] must reflect
        the actual content location (queried from Node.js), not the freshly
        computed hierarchical candidate. This ensures metadata is colocated
        with content for both flat-path and hierarchical records."""
        bs = _make_bs()
        bs.get_document_id_by_virtual_record_id = AsyncMock(
            return_value={"record_doc_id": "doc-existing"}
        )
        bs._build_hierarchical_storage_path = AsyncMock(return_value="records/conn-1/Finance/doc.pdf")
        bs._get_current_document_path = AsyncMock(return_value="org-1/PipesHub/records/vr-1")
        bs.update_record_buffer = AsyncMock(return_value=("doc-existing", 100))
        bs.save_record_to_storage = AsyncMock()
        bs.store_virtual_record_mapping = AsyncMock()
        bs._clean_empty_values = MagicMock(side_effect=lambda x: x)

        ctx = self._make_transform_ctx()
        ctx.settings = {}
        result = await bs.apply(ctx)

        assert result.settings["storage_path"] == "records/vr-1"
        bs._get_current_document_path.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_new_record_calls_save_with_hierarchical_path(self):
        """When no existing doc is found, apply() calls save_record_to_storage with document_path."""
        bs = _make_bs()
        bs.get_document_id_by_virtual_record_id = AsyncMock(return_value=None)
        expected_path = "records/conn-1/My_Space/Parent_Page"
        bs._build_hierarchical_storage_path = AsyncMock(return_value=expected_path)
        bs.save_record_to_storage = AsyncMock(return_value=("doc-new", 200))
        bs.update_record_buffer = AsyncMock()
        bs.store_virtual_record_mapping = AsyncMock()
        bs._clean_empty_values = MagicMock(side_effect=lambda x: x)

        ctx = self._make_transform_ctx()
        await bs.apply(ctx)

        bs.update_record_buffer.assert_not_awaited()
        call_kwargs = bs.save_record_to_storage.call_args
        assert call_kwargs.kwargs.get("document_path") == expected_path

    @pytest.mark.asyncio
    async def test_no_upload_next_version_called(self):
        """upload_next_version must never be called from apply()."""
        bs = _make_bs()
        bs.get_document_id_by_virtual_record_id = AsyncMock(
            return_value={"record_doc_id": "doc-123"}
        )
        bs._build_hierarchical_storage_path = AsyncMock(return_value="records/conn-1")
        bs._get_current_document_path = AsyncMock(return_value="org-1/PipesHub/records/conn-1")
        bs.save_record_to_storage = AsyncMock(return_value=("doc-new", 200))
        bs.update_record_buffer = AsyncMock(return_value=("doc-123", 50))
        bs.upload_next_version = AsyncMock()
        bs.store_virtual_record_mapping = AsyncMock()
        bs._clean_empty_values = MagicMock(side_effect=lambda x: x)

        ctx = self._make_transform_ctx()
        await bs.apply(ctx)

        bs.upload_next_version.assert_not_awaited()


# ---------------------------------------------------------------------------
# TestBuildHierarchicalStoragePath (Phase 2)
# ---------------------------------------------------------------------------


class TestBuildHierarchicalStoragePath:
    def _make_record(self, connector_id="conn-1", record_group_id=None, record_id="rec-1", record_name="Test Record"):
        r = MagicMock()
        r.connector_id = connector_id
        r.record_group_id = record_group_id
        r.id = record_id
        r.record_name = record_name
        return r

    @pytest.mark.asyncio
    async def test_path_with_group_and_parents(self):
        """Builds records/<cid>/<group>/<full record path> when all data is present."""
        bs = _make_bs()
        bs.graph_provider.get_record_group_by_id = AsyncMock(
            return_value={"groupName": "Engineering Space"}
        )
        bs.graph_provider.get_record_path = AsyncMock(
            return_value="Architecture/Runbooks/Design Doc"
        )
        record = self._make_record(connector_id="conn-1", record_group_id="grp-1")

        path = await bs._build_hierarchical_storage_path(record, "vr-1")

        assert path == "records/conn-1/Engineering Space/Architecture/Runbooks/Design Doc"

    @pytest.mark.asyncio
    async def test_path_no_group(self):
        """Without record_group_id, path includes full record path from graph."""
        bs = _make_bs()
        bs.graph_provider.get_record_path = AsyncMock(return_value="Parent/Child File")
        record = self._make_record(connector_id="conn-2", record_group_id=None)

        path = await bs._build_hierarchical_storage_path(record, "vr-2")

        assert path == "records/conn-2/Parent/Child File"

    @pytest.mark.asyncio
    async def test_path_no_parents_only_group(self):
        """With group and single-segment record path, includes the record name."""
        bs = _make_bs()
        bs.graph_provider.get_record_group_by_id = AsyncMock(
            return_value={"groupName": "General"}
        )
        bs.graph_provider.get_record_path = AsyncMock(return_value="My File")
        record = self._make_record(connector_id="conn-3", record_group_id="grp-2")

        path = await bs._build_hierarchical_storage_path(record, "vr-3")

        assert path == "records/conn-3/General/My File"

    @pytest.mark.asyncio
    async def test_graph_provider_error_falls_back_to_flat_vrid_path(self):
        """If get_record_path raises, the safe flat vrid path is used -- not a
        fabricated <group>/<record_name> path built from partial data."""
        bs = _make_bs()
        bs.graph_provider.get_record_group_by_id = AsyncMock(
            return_value={"groupName": "Space"}
        )
        bs.graph_provider.get_record_path = AsyncMock(side_effect=Exception("AQL error"))
        record = self._make_record(connector_id="conn-4", record_group_id="grp-3", record_name="My Doc")

        path = await bs._build_hierarchical_storage_path(record, "vr-fallback")

        # Should not raise; short-circuits to the flat vrid path on traversal failure
        assert path == "records/vr-fallback"

    @pytest.mark.asyncio
    async def test_no_graph_provider_returns_flat_path(self):
        """Without graph_provider, returns records/<virtual_record_id>."""
        # Instantiate BlobStorage directly with graph_provider=None;
        # the _make_bs helper uses `or AsyncMock()` so it cannot produce None.
        bs = BlobStorage(logger=MagicMock(), config_service=AsyncMock(), graph_provider=None)
        record = self._make_record(connector_id="conn-5")

        path = await bs._build_hierarchical_storage_path(record, "vr-nogp")

        assert path == "records/vr-nogp"

    @pytest.mark.asyncio
    async def test_no_connector_id_returns_flat_path(self):
        """If connector_id is None/empty, falls back to flat path."""
        bs = _make_bs()
        record = self._make_record(connector_id=None)

        path = await bs._build_hierarchical_storage_path(record, "vr-nocid")

        assert path == "records/vr-nocid"


# ---------------------------------------------------------------------------
# TestSanitizePathSegment (Phase 2)
# ---------------------------------------------------------------------------


class TestSanitizePathSegment:
    def test_removes_forward_slash(self):
        assert sanitize_path_segment("foo/bar") == "foo_bar"

    def test_removes_backslash(self):
        assert sanitize_path_segment("foo\\bar") == "foo_bar"

    def test_removes_colon(self):
        assert sanitize_path_segment("C:drive") == "C_drive"

    def test_removes_asterisk_and_question_mark(self):
        result = sanitize_path_segment("file*?.txt")
        assert "*" not in result
        assert "?" not in result

    def test_removes_angle_brackets_and_pipe(self):
        result = sanitize_path_segment("a<b>c|d")
        assert "<" not in result
        assert ">" not in result
        assert "|" not in result

    def test_truncates_at_100_chars(self):
        long_name = "a" * 150
        result = sanitize_path_segment(long_name)
        assert len(result) == 100

    def test_preserves_normal_name(self):
        assert sanitize_path_segment("Engineering Space") == "Engineering Space"

    def test_preserves_alphanumeric_and_dash(self):
        assert sanitize_path_segment("my-doc_2024") == "my-doc_2024"


# ---------------------------------------------------------------------------
# TestSaveRecordNonVersioned (Phase 1 + 2)
# ---------------------------------------------------------------------------


class TestSaveRecordNonVersioned:
    """Verify that save_record_to_storage uses isVersionedFile=false."""

    @pytest.mark.asyncio
    async def test_local_upload_uses_isVersionedFile_false(self):
        """Form-data sent to POST /upload must have isVersionedFile='false'."""
        bs = _make_bs()
        _configure_auth(bs, "local")

        post_resp = _resp(200, {"_id": "doc-new"})
        session = _mock_session(post_resp=post_resp)

        # Capture what FormData fields are added
        captured_fields: dict = {}

        class CapturingFormData:
            def add_field(self, name, value, **kwargs):
                captured_fields[name] = value

        with patch("app.modules.transformers.blob_storage.aiohttp.ClientSession", return_value=session), \
             patch("app.modules.transformers.blob_storage.aiohttp.FormData", CapturingFormData):
            await bs.save_record_to_storage("org-1", "rec-1", "vr-1", {"data": "x"})

        assert captured_fields.get("isVersionedFile") == "false"

    @pytest.mark.asyncio
    async def test_local_upload_uses_provided_document_path(self):
        """Form-data documentPath field must match the document_path kwarg."""
        bs = _make_bs()
        _configure_auth(bs, "local")

        post_resp = _resp(200, {"_id": "doc-new"})
        session = _mock_session(post_resp=post_resp)

        captured_fields: dict = {}

        class CapturingFormData:
            def add_field(self, name, value, **kwargs):
                captured_fields[name] = value

        custom_path = "records/conn-1/My_Space/Parent"
        with patch("app.modules.transformers.blob_storage.aiohttp.ClientSession", return_value=session), \
             patch("app.modules.transformers.blob_storage.aiohttp.FormData", CapturingFormData):
            await bs.save_record_to_storage(
                "org-1", "rec-1", "vr-1", {"data": "x"}, document_path=custom_path
            )

        assert captured_fields.get("documentPath") == custom_path

    @pytest.mark.asyncio
    async def test_local_upload_falls_back_to_flat_path_when_no_document_path(self):
        """When document_path is None, falls back to records/<vrid>."""
        bs = _make_bs()
        _configure_auth(bs, "local")

        post_resp = _resp(200, {"_id": "doc-new"})
        session = _mock_session(post_resp=post_resp)

        captured_fields: dict = {}

        class CapturingFormData:
            def add_field(self, name, value, **kwargs):
                captured_fields[name] = value

        with patch("app.modules.transformers.blob_storage.aiohttp.ClientSession", return_value=session), \
             patch("app.modules.transformers.blob_storage.aiohttp.FormData", CapturingFormData):
            await bs.save_record_to_storage("org-1", "rec-1", "vr-flat", {"data": "x"})

        assert captured_fields.get("documentPath") == "records/vr-flat"

    @pytest.mark.asyncio
    async def test_s3_placeholder_uses_isVersionedFile_false(self):
        """Placeholder JSON posted for S3/cloud must have isVersionedFile=False."""
        bs = _make_bs()
        _configure_auth(bs, "s3")

        # Capture the placeholder JSON body
        captured_placeholder: dict = {}

        async def fake_create_placeholder(session, url, data, headers):
            captured_placeholder.update(data)
            return {"_id": "doc-s3"}

        bs._create_placeholder = fake_create_placeholder
        bs._get_signed_url = AsyncMock(return_value={"signedUrl": "https://s3.example.com/signed"})
        bs._upload_to_signed_url = AsyncMock(return_value=200)

        session = AsyncMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)

        with patch("app.modules.transformers.blob_storage.aiohttp.ClientSession", return_value=session):
            await bs.save_record_to_storage("org-1", "rec-1", "vr-s3", {"data": "x"})

        assert captured_placeholder.get("isVersionedFile") is False
