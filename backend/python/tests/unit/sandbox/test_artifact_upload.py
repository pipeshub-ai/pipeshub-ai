"""Tests for app.sandbox.artifact_upload."""

import asyncio
import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.sandbox.artifact_upload import (
    MAX_ARTIFACT_BYTES,
    _read_file_bytes,
    create_artifact_record,
    schedule_artifact_upload_task,
    upload_artifacts_to_blob,
    upload_bytes_artifact,
)
from app.sandbox.models import ArtifactOutput, ExecutionResult


class TestReadFileBytes:
    def test_reads_file_under_sandbox_root(self, monkeypatch):
        sandbox_root = os.path.join(tempfile.gettempdir(), "pipeshub_sandbox")
        os.makedirs(sandbox_root, exist_ok=True)
        test_file = os.path.join(sandbox_root, "test_read.txt")
        try:
            with open(test_file, "wb") as f:
                f.write(b"hello world")
            assert _read_file_bytes(test_file) == b"hello world"
        finally:
            if os.path.exists(test_file):
                os.unlink(test_file)

    def test_nonexistent_file(self):
        assert _read_file_bytes("/nonexistent/file.txt") is None

    def test_rejects_file_outside_sandbox(self, tmp_path):
        f = tmp_path / "secret.txt"
        f.write_bytes(b"sensitive data")
        assert _read_file_bytes(str(f)) is None

    def test_rejects_file_exceeding_size_cap(self):
        """A file larger than max_bytes must be refused without being loaded into memory."""
        sandbox_root = os.path.join(tempfile.gettempdir(), "pipeshub_sandbox")
        os.makedirs(sandbox_root, exist_ok=True)
        test_file = os.path.join(sandbox_root, "test_oversize.bin")
        try:
            # 2 MiB file with a 1 MiB cap
            with open(test_file, "wb") as f:
                f.write(b"\x00" * (2 * 1024 * 1024))
            assert _read_file_bytes(test_file, max_bytes=1024 * 1024) is None
        finally:
            if os.path.exists(test_file):
                os.unlink(test_file)

    def test_allows_file_at_size_cap(self):
        sandbox_root = os.path.join(tempfile.gettempdir(), "pipeshub_sandbox")
        os.makedirs(sandbox_root, exist_ok=True)
        test_file = os.path.join(sandbox_root, "test_atcap.bin")
        try:
            payload = b"\x00" * 1024
            with open(test_file, "wb") as f:
                f.write(payload)
            assert _read_file_bytes(test_file, max_bytes=1024) == payload
        finally:
            if os.path.exists(test_file):
                os.unlink(test_file)

    def test_default_cap_is_reasonable(self):
        """Sanity-check the module-level default cap is within an expected range."""
        assert 1024 * 1024 <= MAX_ARTIFACT_BYTES <= 1024 * 1024 * 1024

    def test_reads_file_under_docker_sandbox_root(self):
        sandbox_root = os.path.join(tempfile.gettempdir(), "pipeshub_sandbox_docker")
        os.makedirs(sandbox_root, exist_ok=True)
        test_file = os.path.join(sandbox_root, "test_read_docker.txt")
        try:
            with open(test_file, "wb") as f:
                f.write(b"docker sandbox content")
            assert _read_file_bytes(test_file) == b"docker sandbox content"
        finally:
            if os.path.exists(test_file):
                os.unlink(test_file)


class TestUploadArtifactsToBlob:
    @pytest.mark.asyncio
    async def test_empty_list(self):
        blob_store = AsyncMock()
        result = await upload_artifacts_to_blob(
            [],
            blob_store=blob_store,
            org_id="org1",
            conversation_id="conv1",
        )
        assert result == []
        blob_store.save_conversation_file_to_storage.assert_not_called()

    @pytest.mark.asyncio
    async def test_uploads_artifacts(self):
        sandbox_root = os.path.join(tempfile.gettempdir(), "pipeshub_sandbox")
        os.makedirs(sandbox_root, exist_ok=True)
        file_path = os.path.join(sandbox_root, "test_upload_chart.png")
        file_data = b"\x89PNG" + b"\x00" * 100
        try:
            with open(file_path, "wb") as f:
                f.write(file_data)

            artifact = ArtifactOutput(
                file_name="chart.png",
                file_path=file_path,
                mime_type="image/png",
                size_bytes=104,
            )

            blob_store = AsyncMock()
            blob_store.save_conversation_file_to_storage = AsyncMock(return_value={
                "documentId": "doc123",
                "fileName": "chart.png",
                "signedUrl": "https://storage.example.com/chart.png",
            })

            result = await upload_artifacts_to_blob(
                [artifact],
                blob_store=blob_store,
                org_id="org1",
                conversation_id="conv1",
            )

            assert len(result) == 1
            assert result[0]["documentId"] == "doc123"
            assert result[0]["mimeType"] == "image/png"
            assert result[0]["sizeBytes"] == 104

            blob_store.save_conversation_file_to_storage.assert_called_once_with(
                org_id="org1",
                conversation_id="conv1",
                file_name="chart.png",
                file_bytes=file_data,
                content_type="image/png",
            )
        finally:
            if os.path.exists(file_path):
                os.unlink(file_path)

    @pytest.mark.asyncio
    async def test_skips_unreadable_files(self):
        artifact = ArtifactOutput(
            file_name="missing.csv",
            file_path="/nonexistent/missing.csv",
            mime_type="text/csv",
            size_bytes=0,
        )
        blob_store = AsyncMock()

        result = await upload_artifacts_to_blob(
            [artifact],
            blob_store=blob_store,
            org_id="org1",
            conversation_id="conv1",
        )
        assert result == []
        blob_store.save_conversation_file_to_storage.assert_not_called()

    @pytest.mark.asyncio
    async def test_continues_on_upload_error(self):
        sandbox_root = os.path.join(tempfile.gettempdir(), "pipeshub_sandbox")
        os.makedirs(sandbox_root, exist_ok=True)
        f1_path = os.path.join(sandbox_root, "test_bad.png")
        f2_path = os.path.join(sandbox_root, "test_good.csv")
        try:
            with open(f1_path, "wb") as f:
                f.write(b"data1")
            with open(f2_path, "wb") as f:
                f.write(b"a,b\n1,2\n")

            art1 = ArtifactOutput(file_name="bad.png", file_path=f1_path, mime_type="image/png", size_bytes=5)
            art2 = ArtifactOutput(file_name="good.csv", file_path=f2_path, mime_type="text/csv", size_bytes=8)

            blob_store = AsyncMock()

            call_count = 0
            async def _mock_save(**kwargs):
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    raise RuntimeError("upload failed")
                return {"documentId": "doc2", "fileName": "good.csv", "signedUrl": "https://url"}

            blob_store.save_conversation_file_to_storage = _mock_save

            result = await upload_artifacts_to_blob(
                [art1, art2],
                blob_store=blob_store,
                org_id="org1",
                conversation_id="conv1",
            )
            assert len(result) == 1
            assert result[0]["fileName"] == "good.csv"
        finally:
            for p in (f1_path, f2_path):
                if os.path.exists(p):
                    os.unlink(p)


class TestUploadBytesArtifact:
    @pytest.mark.asyncio
    async def test_rejects_oversize_in_memory_bytes(self):
        """upload_bytes_artifact must refuse payloads over the cap without calling the blob store."""
        blob_store = AsyncMock()
        blob_store.save_conversation_file_to_storage = AsyncMock()

        huge_bytes = b"\x00" * (MAX_ARTIFACT_BYTES + 1)
        result = await upload_bytes_artifact(
            file_name="huge.png",
            file_bytes=huge_bytes,
            mime_type="image/png",
            blob_store=blob_store,
            org_id="org-1",
            conversation_id="conv-1",
        )
        assert result is None
        blob_store.save_conversation_file_to_storage.assert_not_called()

    @pytest.mark.asyncio
    async def test_accepts_small_bytes(self):
        blob_store = AsyncMock()
        blob_store.save_conversation_file_to_storage = AsyncMock(return_value={
            "documentId": "doc-xyz",
            "fileName": "ok.png",
            "signedUrl": "https://ok.example/x",
        })
        result = await upload_bytes_artifact(
            file_name="ok.png",
            file_bytes=b"\x89PNG" + b"\x00" * 100,
            mime_type="image/png",
            blob_store=blob_store,
            org_id="org-1",
            conversation_id="conv-1",
        )
        assert result is not None
        assert result["documentId"] == "doc-xyz"
        blob_store.save_conversation_file_to_storage.assert_called_once()


class TestCreateArtifactRecord:
    @pytest.mark.asyncio
    async def test_creates_record_with_edges(self):
        mock_graph = AsyncMock()
        mock_graph.get_user_by_user_id = AsyncMock(return_value={"_key": "user-key-1", "id": "user-key-1"})
        mock_graph.batch_upsert_nodes = AsyncMock()
        mock_graph.batch_create_edges = AsyncMock()

        record_id = await create_artifact_record(
            graph_provider=mock_graph,
            document_id="doc-ext-123",
            file_name="chart.png",
            mime_type="image/png",
            size_bytes=4096,
            org_id="org-1",
            user_id="user-1",
            conversation_id="conv-1",
            source_tool="coding_sandbox.execute_python",
        )

        assert record_id is not None
        assert len(record_id) > 0

        # Should have created records and artifacts nodes
        assert mock_graph.batch_upsert_nodes.call_count == 2
        # Should have created permission and is_of_type edges
        assert mock_graph.batch_create_edges.call_count == 2

    @pytest.mark.asyncio
    async def test_raises_on_missing_user(self):
        mock_graph = AsyncMock()
        mock_graph.get_user_by_user_id = AsyncMock(return_value=None)

        with pytest.raises(ValueError, match="User not found"):
            await create_artifact_record(
                graph_provider=mock_graph,
                document_id="doc-ext-123",
                file_name="chart.png",
                mime_type="image/png",
                size_bytes=4096,
                org_id="org-1",
                user_id="nonexistent-user",
                conversation_id="conv-1",
            )


class TestScheduleArtifactUploadTask:
    @pytest.mark.asyncio
    async def test_registers_task_for_artifacts(self):
        artifact = ArtifactOutput(
            file_name="chart.png",
            file_path="/tmp/pipeshub_sandbox/test/chart.png",
            mime_type="image/png",
            size_bytes=100,
        )
        exec_result = ExecutionResult(
            success=True, stdout="", exit_code=0, artifacts=[artifact],
        )
        mock_blob = AsyncMock()
        mock_blob.save_conversation_file_to_storage = AsyncMock(return_value={
            "documentId": "doc1", "fileName": "chart.png", "signedUrl": "https://url",
        })

        with patch("app.sandbox.artifact_upload.register_task") as mock_register:
            schedule_artifact_upload_task(
                exec_result,
                blob_store=mock_blob,
                org_id="org-1",
                conversation_id="conv-1",
            )
            mock_register.assert_called_once()
            assert mock_register.call_args[0][0] == "conv-1"

    def test_no_op_without_artifacts(self):
        exec_result = ExecutionResult(success=True, stdout="", exit_code=0, artifacts=[])

        with patch("app.sandbox.artifact_upload.register_task") as mock_register:
            schedule_artifact_upload_task(
                exec_result,
                blob_store=AsyncMock(),
                org_id="org-1",
                conversation_id="conv-1",
            )
            mock_register.assert_not_called()

    def test_no_op_without_conversation_id(self):
        artifact = ArtifactOutput(
            file_name="chart.png", file_path="/tmp/chart.png",
            mime_type="image/png", size_bytes=100,
        )
        exec_result = ExecutionResult(
            success=True, stdout="", exit_code=0, artifacts=[artifact],
        )

        with patch("app.sandbox.artifact_upload.register_task") as mock_register:
            schedule_artifact_upload_task(
                exec_result,
                blob_store=AsyncMock(),
                org_id="org-1",
                conversation_id="",
            )
            mock_register.assert_not_called()
