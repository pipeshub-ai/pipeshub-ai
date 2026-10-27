"""A 404 from the storage service is a typed, non-retried "document gone".

Writes replace the missing document at once instead of retrying a PUT that
cannot succeed; reads raise the typed error without a traceback. Any other
failure keeps the existing retry-then-replace behaviour.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from app.modules.transformers.blob_storage import (
    BlobStorage,
    StorageDocumentNotFoundError,
    TransientStorageError,
    _storage_status_error,
)


def _make_bs(graph_provider=None) -> BlobStorage:
    return BlobStorage(
        logger=MagicMock(),
        config_service=AsyncMock(),
        graph_provider=graph_provider or AsyncMock(),
    )


def _put_session(status, text="Document not found") -> MagicMock:
    resp = AsyncMock()
    resp.status = status
    resp.text = AsyncMock(return_value=text)
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)
    session = MagicMock()
    session.put = MagicMock(return_value=resp)
    return session


def _record_ctx() -> MagicMock:
    record = MagicMock()
    record.org_id = "org-1"
    record.id = "rec-1"
    record.virtual_record_id = "vr-1"
    record.connector_id = "conn-1"
    record.record_group_id = None
    record.model_dump = MagicMock(return_value={"id": "rec-1"})
    ctx = MagicMock()
    ctx.record = record
    ctx.settings = {}
    return ctx


def _apply_ready(bs) -> None:
    bs.get_document_id_by_virtual_record_id = AsyncMock(return_value={"record_doc_id": "doc-gone"})
    bs._build_hierarchical_storage_path = AsyncMock(return_value="records/conn-1/KB/doc")
    bs._get_current_document_path = AsyncMock(return_value=None)
    bs.save_record_to_storage = AsyncMock(return_value=("doc-new", 10))
    bs.store_virtual_record_mapping = AsyncMock()
    bs._clean_empty_values = MagicMock(side_effect=lambda x: x)


class TestStorageStatusError:
    def test_404_is_not_found(self) -> None:
        err = _storage_status_error(404, "gone")
        assert isinstance(err, StorageDocumentNotFoundError)
        assert isinstance(err, aiohttp.ClientError)

    def test_transient_and_other_statuses_unchanged(self) -> None:
        assert isinstance(_storage_status_error(503, "x"), TransientStorageError)
        other = _storage_status_error(500, "x")
        assert type(other) is aiohttp.ClientError


class TestBufferUpdates404:
    @pytest.mark.asyncio
    async def test_record_buffer_404_raises_typed_without_error_log(self) -> None:
        bs = _make_bs()
        bs._get_auth_and_config = AsyncMock(return_value=({}, "http://node", "local"))
        with patch(
            "app.modules.transformers.blob_storage.get_shared_session",
            return_value=_put_session(404),
        ):
            with pytest.raises(StorageDocumentNotFoundError, match="Failed to update buffer: 404"):
                await bs.update_record_buffer("org-1", "doc-gone", {}, "vr-1")
        bs.logger.error.assert_not_called()

    @pytest.mark.asyncio
    async def test_record_buffer_500_still_logged_once(self) -> None:
        bs = _make_bs()
        bs._get_auth_and_config = AsyncMock(return_value=({}, "http://node", "local"))
        with patch(
            "app.modules.transformers.blob_storage.get_shared_session",
            return_value=_put_session(500, "boom"),
        ):
            with pytest.raises(aiohttp.ClientError) as info:
                await bs.update_record_buffer("org-1", "doc-1", {}, "vr-1")
        assert not isinstance(info.value, StorageDocumentNotFoundError)
        bs.logger.error.assert_called_once()

    @pytest.mark.asyncio
    async def test_metadata_buffer_404_raises_typed_without_error_log(self) -> None:
        bs = _make_bs()
        bs._get_auth_and_config = AsyncMock(return_value=({}, "http://node", "local"))
        with patch(
            "app.modules.transformers.blob_storage.get_shared_session",
            return_value=_put_session(404),
        ):
            with pytest.raises(StorageDocumentNotFoundError):
                await bs._update_metadata_buffer("org-1", "meta-gone", {}, "vr-1")
        bs.logger.error.assert_not_called()


class TestApplyReplacesMissingDocument:
    @pytest.mark.asyncio
    async def test_missing_document_uploads_replacement_without_retry(self) -> None:
        bs = _make_bs()
        _apply_ready(bs)
        bs.update_record_buffer = AsyncMock(side_effect=StorageDocumentNotFoundError("gone"))

        with patch("app.modules.transformers.blob_storage.asyncio.sleep", new=AsyncMock()) as sleep:
            await bs.apply(_record_ctx())

        bs.update_record_buffer.assert_awaited_once()
        sleep.assert_not_awaited()
        bs.save_record_to_storage.assert_awaited_once()
        assert bs.save_record_to_storage.call_args.kwargs["document_path"] == "records/conn-1/KB/doc"
        bs.store_virtual_record_mapping.assert_awaited_once()
        assert bs.store_virtual_record_mapping.call_args.args[2] == "doc-new"

    @pytest.mark.asyncio
    async def test_other_failure_still_retries_before_replacing(self) -> None:
        bs = _make_bs()
        _apply_ready(bs)
        bs.update_record_buffer = AsyncMock(side_effect=aiohttp.ClientError("503"))

        with patch("app.modules.transformers.blob_storage.asyncio.sleep", new=AsyncMock()) as sleep:
            await bs.apply(_record_ctx())

        assert bs.update_record_buffer.await_count == 2
        sleep.assert_awaited_once()
        bs.save_record_to_storage.assert_awaited_once()


class TestMetadataReplacesMissingDocument:
    @pytest.mark.asyncio
    async def test_missing_metadata_document_created_without_retry(self) -> None:
        gp = AsyncMock()
        gp.get_document = AsyncMock(return_value={"record_metadata_doc_id": "meta-gone"})
        gp.batch_upsert_nodes = AsyncMock(return_value=True)
        bs = _make_bs(graph_provider=gp)
        bs._update_metadata_buffer = AsyncMock(side_effect=StorageDocumentNotFoundError("gone"))
        bs._create_metadata_document = AsyncMock(return_value="meta-new")

        with patch("app.modules.transformers.blob_storage.asyncio.sleep", new=AsyncMock()) as sleep:
            result = await bs.save_reconciliation_metadata(
                "org-1", "rec-1", "vr-1", {"k": "v"}, document_path="records/conn-1/KB/doc",
            )

        assert result == "meta-new"
        bs._update_metadata_buffer.assert_awaited_once()
        sleep.assert_not_awaited()
        bs._create_metadata_document.assert_awaited_once()
        gp.batch_upsert_nodes.assert_awaited_once()


class TestReadOfMissingDocument:
    @pytest.mark.asyncio
    async def test_get_record_raises_typed_error_without_traceback(self) -> None:
        bs = _make_bs()
        bs._get_auth_and_config = AsyncMock(return_value=({}, "http://node", "local"))
        bs.get_document_id_by_virtual_record_id = AsyncMock(return_value={"record_doc_id": "doc-gone"})
        bs._cached_signed_url = AsyncMock(return_value=None)
        bs._fetch_record_envelope = AsyncMock(side_effect=StorageDocumentNotFoundError("gone"))

        with patch("app.modules.transformers.blob_storage.get_shared_session", return_value=MagicMock()):
            with pytest.raises(StorageDocumentNotFoundError):
                await bs.get_record_from_storage("vr-1", "org-1")

        bs.logger.exception.assert_not_called()
        bs.logger.warning.assert_called_once()

    @pytest.mark.asyncio
    async def test_fetch_envelope_404_not_retried(self) -> None:
        bs = _make_bs()
        resp = AsyncMock()
        resp.status = 404
        resp.__aenter__ = AsyncMock(return_value=resp)
        resp.__aexit__ = AsyncMock(return_value=False)
        session = MagicMock()
        session.get = MagicMock(return_value=resp)

        with pytest.raises(StorageDocumentNotFoundError):
            await bs._fetch_record_envelope(session, "http://node/doc", {}, "vr-1")

        session.get.assert_called_once()
        bs.logger.error.assert_not_called()


def _delete_session(status=None, exc=None) -> MagicMock:
    resp = AsyncMock()
    resp.status = status
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)
    session = MagicMock()
    session.delete = MagicMock(side_effect=exc) if exc else MagicMock(return_value=resp)
    return session


class TestSupersededDocumentCleanup:
    """A replacement upload must not orphan the document it replaced."""

    @pytest.mark.asyncio
    async def test_record_replaced_after_retry_is_deleted_once_mapping_moves(self) -> None:
        bs = _make_bs()
        _apply_ready(bs)
        bs.store_virtual_record_mapping = AsyncMock(return_value=True)
        bs.update_record_buffer = AsyncMock(side_effect=aiohttp.ClientError("503"))
        order: list[str] = []
        bs.store_virtual_record_mapping.side_effect = lambda *a, **k: order.append("map") or True
        bs._delete_replaced_document = AsyncMock(side_effect=lambda *a: order.append("delete"))

        with patch("app.modules.transformers.blob_storage.asyncio.sleep", new=AsyncMock()):
            await bs.apply(_record_ctx())

        bs._delete_replaced_document.assert_awaited_once_with("org-1", "doc-gone", "vr-1")
        assert order == ["map", "delete"]

    @pytest.mark.asyncio
    async def test_replaced_record_kept_when_mapping_not_stored(self) -> None:
        bs = _make_bs()
        _apply_ready(bs)
        bs.store_virtual_record_mapping = AsyncMock(return_value=False)
        bs.update_record_buffer = AsyncMock(side_effect=aiohttp.ClientError("503"))
        bs._delete_replaced_document = AsyncMock()

        with patch("app.modules.transformers.blob_storage.asyncio.sleep", new=AsyncMock()):
            await bs.apply(_record_ctx())

        bs._delete_replaced_document.assert_not_awaited()

    @pytest.mark.parametrize(
        "update_effect",
        [None, StorageDocumentNotFoundError("gone")],
        ids=["in-place-update", "document-already-gone"],
    )
    @pytest.mark.asyncio
    async def test_nothing_deleted_without_a_superseded_document(self, update_effect) -> None:
        bs = _make_bs()
        _apply_ready(bs)
        bs.update_record_buffer = AsyncMock(
            side_effect=update_effect, return_value=("doc-gone", 10),
        )
        bs._delete_replaced_document = AsyncMock()

        await bs.apply(_record_ctx())

        bs._delete_replaced_document.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_metadata_replaced_after_retry_is_deleted_after_upsert(self) -> None:
        gp = AsyncMock()
        gp.get_document = AsyncMock(return_value={"record_metadata_doc_id": "meta-old"})
        order: list[str] = []
        gp.batch_upsert_nodes = AsyncMock(side_effect=lambda *a: order.append("map") or True)
        bs = _make_bs(graph_provider=gp)
        bs._update_metadata_buffer = AsyncMock(side_effect=aiohttp.ClientError("503"))
        bs._create_metadata_document = AsyncMock(return_value="meta-new")
        bs._delete_replaced_document = AsyncMock(side_effect=lambda *a: order.append("delete"))

        with patch("app.modules.transformers.blob_storage.asyncio.sleep", new=AsyncMock()):
            result = await bs.save_reconciliation_metadata(
                "org-1", "rec-1", "vr-1", {"k": "v"}, document_path="records/conn-1/KB/doc",
            )

        assert result == "meta-new"
        bs._delete_replaced_document.assert_awaited_once_with("org-1", "meta-old", "vr-1")
        assert order == ["map", "delete"]

    @pytest.mark.asyncio
    async def test_missing_metadata_document_is_not_deleted_again(self) -> None:
        gp = AsyncMock()
        gp.get_document = AsyncMock(return_value={"record_metadata_doc_id": "meta-gone"})
        bs = _make_bs(graph_provider=gp)
        bs._update_metadata_buffer = AsyncMock(side_effect=StorageDocumentNotFoundError("gone"))
        bs._create_metadata_document = AsyncMock(return_value="meta-new")
        bs._delete_replaced_document = AsyncMock()

        await bs.save_reconciliation_metadata(
            "org-1", "rec-1", "vr-1", {"k": "v"}, document_path="records/conn-1/KB/doc",
        )

        bs._delete_replaced_document.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_delete_sends_hard_delete_for_the_superseded_document(self) -> None:
        bs = _make_bs()
        bs._get_auth_and_config = AsyncMock(return_value=({"a": "b"}, "http://node", "local"))
        session = _delete_session(status=200)
        with patch("app.modules.transformers.blob_storage.get_shared_session", return_value=session):
            await bs._delete_replaced_document("org-1", "doc-old", "vr-1")

        url = session.delete.call_args.args[0]
        assert url.startswith("http://node") and "doc-old" in url and url.endswith("?hard=true")
        bs.logger.warning.assert_not_called()

    @pytest.mark.parametrize(
        "session",
        [_delete_session(status=500), _delete_session(exc=aiohttp.ClientError("down"))],
        ids=["error-status", "network-error"],
    )
    @pytest.mark.asyncio
    async def test_delete_failure_is_logged_not_raised(self, session) -> None:
        bs = _make_bs()
        bs._get_auth_and_config = AsyncMock(return_value=({}, "http://node", "local"))
        with patch("app.modules.transformers.blob_storage.get_shared_session", return_value=session):
            await bs._delete_replaced_document("org-1", "doc-old", "vr-1")

        bs.logger.warning.assert_called_once()
