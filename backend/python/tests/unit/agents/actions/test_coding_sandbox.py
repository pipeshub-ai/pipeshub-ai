"""Tests for app.agents.actions.coding_sandbox.coding_sandbox."""

import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.sandbox.models import ArtifactOutput, ExecutionResult


def _make_state(**overrides):
    """Build a minimal ChatState-like dict for toolset tests."""
    state = {
        "conversation_id": "conv-123",
        "org_id": "org-456",
        "blob_store": None,
        "config_service": MagicMock(),
        "graph_provider": MagicMock(),
    }
    state.update(overrides)
    return state


class TestCodingSandboxInit:
    def test_imports(self):
        from app.agents.actions.coding_sandbox.coding_sandbox import CodingSandbox
        assert CodingSandbox is not None


class TestExecutePython:
    @pytest.mark.asyncio
    async def test_success(self):
        from app.agents.actions.coding_sandbox.coding_sandbox import CodingSandbox

        state = _make_state()
        sandbox = CodingSandbox(state)

        mock_result = ExecutionResult(
            success=True,
            stdout="Hello World\n",
            exit_code=0,
            execution_time_ms=150,
            artifacts=[],
        )
        with patch("app.agents.actions.coding_sandbox.coding_sandbox.get_executor") as mock_get:
            mock_executor = AsyncMock()
            mock_executor.execute = AsyncMock(return_value=mock_result)
            mock_get.return_value = mock_executor

            success, result_json = await sandbox.execute_python("print('Hello World')")
            assert success is True
            data = json.loads(result_json)
            assert data["message"] == "Code executed successfully"
            assert data["stdout"] == "Hello World\n"
            assert data["exit_code"] == 0

    @pytest.mark.asyncio
    async def test_failure(self):
        from app.agents.actions.coding_sandbox.coding_sandbox import CodingSandbox

        state = _make_state()
        sandbox = CodingSandbox(state)

        mock_result = ExecutionResult(
            success=False,
            stderr="NameError: name 'x' is not defined",
            exit_code=1,
            execution_time_ms=50,
            error="Script failed",
        )
        with patch("app.agents.actions.coding_sandbox.coding_sandbox.get_executor") as mock_get:
            mock_executor = AsyncMock()
            mock_executor.execute = AsyncMock(return_value=mock_result)
            mock_get.return_value = mock_executor

            success, result_json = await sandbox.execute_python("print(x)")
            assert success is False
            data = json.loads(result_json)
            assert data["message"] == "Code execution failed"
            assert "NameError" in data["stderr"]

    @pytest.mark.asyncio
    async def test_with_artifacts(self):
        from app.agents.actions.coding_sandbox.coding_sandbox import CodingSandbox

        state = _make_state()
        sandbox = CodingSandbox(state)

        artifact = ArtifactOutput(
            file_name="chart.png",
            file_path="/tmp/chart.png",
            mime_type="image/png",
            size_bytes=4096,
        )
        mock_result = ExecutionResult(
            success=True,
            stdout="",
            exit_code=0,
            execution_time_ms=500,
            artifacts=[artifact],
        )

        with patch("app.agents.actions.coding_sandbox.coding_sandbox.get_executor") as mock_get:
            mock_executor = AsyncMock()
            mock_executor.execute = AsyncMock(return_value=mock_result)
            mock_get.return_value = mock_executor

            with patch("app.agents.actions.coding_sandbox.coding_sandbox.register_task"):
                success, result_json = await sandbox.execute_python(
                    "import matplotlib; ...",
                    requirements=["matplotlib"],
                )
                assert success is True
                data = json.loads(result_json)
                assert len(data["artifacts"]) == 1
                assert data["artifacts"][0]["fileName"] == "chart.png"
                assert data["artifacts"][0]["mimeType"] == "image/png"

    @pytest.mark.asyncio
    async def test_exception_handling(self):
        from app.agents.actions.coding_sandbox.coding_sandbox import CodingSandbox

        state = _make_state()
        sandbox = CodingSandbox(state)

        with patch("app.agents.actions.coding_sandbox.coding_sandbox.get_executor") as mock_get:
            mock_get.side_effect = RuntimeError("executor unavailable")

            success, result_json = await sandbox.execute_python("print(1)")
            assert success is False
            data = json.loads(result_json)
            assert "executor unavailable" in data["error"]


class TestExecuteTypescript:
    @pytest.mark.asyncio
    async def test_success(self):
        from app.agents.actions.coding_sandbox.coding_sandbox import CodingSandbox

        state = _make_state()
        sandbox = CodingSandbox(state)

        mock_result = ExecutionResult(
            success=True,
            stdout="hello ts\n",
            exit_code=0,
            execution_time_ms=200,
            artifacts=[],
        )
        with patch("app.agents.actions.coding_sandbox.coding_sandbox.get_executor") as mock_get:
            mock_executor = AsyncMock()
            mock_executor.execute = AsyncMock(return_value=mock_result)
            mock_get.return_value = mock_executor

            success, result_json = await sandbox.execute_typescript("console.log('hello ts')")
            assert success is True
            data = json.loads(result_json)
            assert data["stdout"] == "hello ts\n"

    @pytest.mark.asyncio
    async def test_failure(self):
        from app.agents.actions.coding_sandbox.coding_sandbox import CodingSandbox

        state = _make_state()
        sandbox = CodingSandbox(state)

        mock_result = ExecutionResult(
            success=False,
            stderr="TypeError: x is not defined",
            exit_code=1,
            execution_time_ms=50,
            error="Script failed",
        )
        with patch("app.agents.actions.coding_sandbox.coding_sandbox.get_executor") as mock_get:
            mock_executor = AsyncMock()
            mock_executor.execute = AsyncMock(return_value=mock_result)
            mock_get.return_value = mock_executor

            success, result_json = await sandbox.execute_typescript("console.log(x)")
            assert success is False
            data = json.loads(result_json)
            assert data["message"] == "Code execution failed"
            assert "TypeError" in data["stderr"]

    @pytest.mark.asyncio
    async def test_exception_handling(self):
        from app.agents.actions.coding_sandbox.coding_sandbox import CodingSandbox

        state = _make_state()
        sandbox = CodingSandbox(state)

        with patch("app.agents.actions.coding_sandbox.coding_sandbox.get_executor") as mock_get:
            mock_get.side_effect = RuntimeError("executor unavailable")

            success, result_json = await sandbox.execute_typescript("console.log(1)")
            assert success is False
            data = json.loads(result_json)
            assert "executor unavailable" in data["error"]

    @pytest.mark.asyncio
    async def test_with_artifacts(self):
        from app.agents.actions.coding_sandbox.coding_sandbox import CodingSandbox

        state = _make_state()
        sandbox = CodingSandbox(state)

        artifact = ArtifactOutput(
            file_name="report.html",
            file_path="/tmp/report.html",
            mime_type="text/html",
            size_bytes=2048,
        )
        mock_result = ExecutionResult(
            success=True,
            stdout="",
            exit_code=0,
            execution_time_ms=300,
            artifacts=[artifact],
        )
        with patch("app.agents.actions.coding_sandbox.coding_sandbox.get_executor") as mock_get:
            mock_executor = AsyncMock()
            mock_executor.execute = AsyncMock(return_value=mock_result)
            mock_get.return_value = mock_executor

            with patch("app.agents.actions.coding_sandbox.coding_sandbox.register_task"):
                success, result_json = await sandbox.execute_typescript(
                    "import fs from 'fs'; ...",
                    packages=["fs-extra"],
                )
                assert success is True
                data = json.loads(result_json)
                assert len(data["artifacts"]) == 1
                assert data["artifacts"][0]["fileName"] == "report.html"


class TestSourceToolTracking:
    """Verify that _schedule_artifact_upload passes the correct source_tool."""

    @pytest.mark.asyncio
    async def test_python_uses_python_source_tool(self):
        from app.agents.actions.coding_sandbox.coding_sandbox import CodingSandbox

        state = _make_state()
        sandbox = CodingSandbox(state)

        artifact = ArtifactOutput(
            file_name="out.png", file_path="/tmp/out.png",
            mime_type="image/png", size_bytes=100,
        )
        mock_result = ExecutionResult(
            success=True, stdout="", exit_code=0,
            execution_time_ms=100, artifacts=[artifact],
        )
        with patch("app.agents.actions.coding_sandbox.coding_sandbox.get_executor") as mock_get:
            mock_executor = AsyncMock()
            mock_executor.execute = AsyncMock(return_value=mock_result)
            mock_get.return_value = mock_executor

            with patch.object(sandbox, "_schedule_artifact_upload") as mock_schedule:
                await sandbox.execute_python("print(1)")
                mock_schedule.assert_called_once()
                call_kwargs = mock_schedule.call_args
                # Default source_tool for python should not pass explicit kwarg
                # (defaults to "coding_sandbox.execute_python")
                assert call_kwargs.kwargs.get("source_tool", "coding_sandbox.execute_python") == "coding_sandbox.execute_python"

    @pytest.mark.asyncio
    async def test_typescript_uses_typescript_source_tool(self):
        from app.agents.actions.coding_sandbox.coding_sandbox import CodingSandbox

        state = _make_state()
        sandbox = CodingSandbox(state)

        artifact = ArtifactOutput(
            file_name="out.html", file_path="/tmp/out.html",
            mime_type="text/html", size_bytes=100,
        )
        mock_result = ExecutionResult(
            success=True, stdout="", exit_code=0,
            execution_time_ms=100, artifacts=[artifact],
        )
        with patch("app.agents.actions.coding_sandbox.coding_sandbox.get_executor") as mock_get:
            mock_executor = AsyncMock()
            mock_executor.execute = AsyncMock(return_value=mock_result)
            mock_get.return_value = mock_executor

            with patch.object(sandbox, "_schedule_artifact_upload") as mock_schedule:
                await sandbox.execute_typescript("console.log(1)")
                mock_schedule.assert_called_once()
                call_kwargs = mock_schedule.call_args
                assert call_kwargs.kwargs.get("source_tool") == "coding_sandbox.execute_typescript"


class TestUploadArtifactsMethod:
    @pytest.mark.asyncio
    async def test_no_artifacts(self):
        from app.agents.actions.coding_sandbox.coding_sandbox import CodingSandbox

        state = _make_state()
        sandbox = CodingSandbox(state)

        result = ExecutionResult(success=True, artifacts=[])
        uploaded = await sandbox._upload_artifacts(result)
        assert uploaded == []

    @pytest.mark.asyncio
    async def test_no_conversation_id(self):
        from app.agents.actions.coding_sandbox.coding_sandbox import CodingSandbox

        state = _make_state(conversation_id=None)
        sandbox = CodingSandbox(state)

        artifact = ArtifactOutput(
            file_name="test.csv",
            file_path="/tmp/test.csv",
            mime_type="text/csv",
            size_bytes=10,
        )
        result = ExecutionResult(success=True, artifacts=[artifact])
        uploaded = await sandbox._upload_artifacts(result)
        assert uploaded == []

    @pytest.mark.asyncio
    async def test_blob_fallback_construction_fails(self):
        """When no blob_store is injected and BlobStorage construction fails,
        _upload_artifacts must return []."""
        from app.agents.actions.coding_sandbox.coding_sandbox import CodingSandbox

        state = _make_state(blob_store=None)
        sandbox = CodingSandbox(state)

        artifact = ArtifactOutput(
            file_name="test.csv",
            file_path="/tmp/test.csv",
            mime_type="text/csv",
            size_bytes=10,
        )
        result = ExecutionResult(success=True, artifacts=[artifact])

        with patch(
            "app.modules.transformers.blob_storage.BlobStorage",
            side_effect=RuntimeError("missing storage config"),
        ):
            uploaded = await sandbox._upload_artifacts(result)

        assert uploaded == []


class TestPackageRejection:
    """Cover the validate_packages failure branches in execute_python/typescript."""

    @pytest.mark.asyncio
    async def test_execute_python_rejects_disallowed_package(self):
        from app.agents.actions.coding_sandbox import coding_sandbox as mod
        from app.sandbox.models import SandboxLanguage
        from app.sandbox.package_policy import PackageNotAllowedError

        sandbox = mod.CodingSandbox(_make_state())

        exc = PackageNotAllowedError(
            "evil-pkg", SandboxLanguage.PYTHON, ["pandas", "numpy"],
        )
        with patch(
            "app.sandbox.models.validate_packages", side_effect=exc,
        ):
            success, result_json = await sandbox.execute_python(
                "print('x')", requirements=["evil-pkg"],
            )

        assert success is False
        data = json.loads(result_json)
        assert data["error_code"] == "package_not_allowed"
        assert data["rejected_package"] == "evil-pkg"
        assert data["language"] == "python"
        assert "evil-pkg" in data["message"]
        assert set(data["allowed_packages"]) >= {"pandas", "numpy"} or isinstance(
            data["allowed_packages"], list,
        )

    @pytest.mark.asyncio
    async def test_execute_python_invalid_package_name(self):
        from app.agents.actions.coding_sandbox import coding_sandbox as mod

        sandbox = mod.CodingSandbox(_make_state())

        with patch(
            "app.sandbox.models.validate_packages",
            side_effect=ValueError("not a valid PyPI name"),
        ):
            success, result_json = await sandbox.execute_python(
                "print('x')", requirements=["$$$invalid"],
            )

        assert success is False
        data = json.loads(result_json)
        assert data["error_code"] == "invalid_package_name"
        assert "not a valid PyPI name" in data["message"]

    @pytest.mark.asyncio
    async def test_execute_typescript_rejects_disallowed_package(self):
        from app.agents.actions.coding_sandbox import coding_sandbox as mod
        from app.sandbox.models import SandboxLanguage
        from app.sandbox.package_policy import PackageNotAllowedError

        sandbox = mod.CodingSandbox(_make_state())

        exc = PackageNotAllowedError(
            "evil-npm", SandboxLanguage.TYPESCRIPT, ["chart.js", "sharp"],
        )
        with patch(
            "app.sandbox.models.validate_packages", side_effect=exc,
        ):
            success, result_json = await sandbox.execute_typescript(
                "console.log(1)", packages=["evil-npm"],
            )

        assert success is False
        data = json.loads(result_json)
        assert data["error_code"] == "package_not_allowed"
        assert data["rejected_package"] == "evil-npm"
        assert data["language"] == "typescript"

    @pytest.mark.asyncio
    async def test_execute_typescript_invalid_package_name(self):
        from app.agents.actions.coding_sandbox import coding_sandbox as mod

        sandbox = mod.CodingSandbox(_make_state())

        with patch(
            "app.sandbox.models.validate_packages",
            side_effect=ValueError("bad npm name"),
        ):
            success, result_json = await sandbox.execute_typescript(
                "console.log(1)", packages=["!!bad"],
            )

        assert success is False
        data = json.loads(result_json)
        assert data["error_code"] == "invalid_package_name"
        assert "bad npm name" in data["message"]


class TestScheduleArtifactUploadException:
    """Exercise the background-task exception branch in _schedule_artifact_upload."""

    @pytest.mark.asyncio
    async def test_upload_exception_resolves_to_none(self):
        import asyncio
        from app.agents.actions.coding_sandbox import coding_sandbox as mod

        sandbox = mod.CodingSandbox(_make_state())

        artifact = ArtifactOutput(
            file_name="out.csv", file_path="/tmp/out.csv",
            mime_type="text/csv", size_bytes=10,
        )
        exec_result = ExecutionResult(
            success=True, stdout="", exit_code=0,
            execution_time_ms=1, artifacts=[artifact],
        )

        captured_tasks: list[asyncio.Task] = []

        async def _raise(*args, **kwargs):
            raise RuntimeError("upload boom")

        with patch.object(sandbox, "_upload_artifacts", _raise), patch.object(
            mod, "register_task",
            lambda conv_id, task: captured_tasks.append(task),
        ):
            sandbox._schedule_artifact_upload(exec_result)
            assert len(captured_tasks) == 1
            result = await captured_tasks[0]

        assert result is None

    @pytest.mark.asyncio
    async def test_upload_success_yields_artifacts(self):
        import asyncio
        from app.agents.actions.coding_sandbox import coding_sandbox as mod

        sandbox = mod.CodingSandbox(_make_state())

        artifact = ArtifactOutput(
            file_name="out.csv", file_path="/tmp/out.csv",
            mime_type="text/csv", size_bytes=10,
        )
        exec_result = ExecutionResult(
            success=True, stdout="", exit_code=0,
            execution_time_ms=1, artifacts=[artifact],
        )

        captured_tasks: list[asyncio.Task] = []

        async def _ok(*args, **kwargs):
            return [{"fileName": "out.csv", "documentId": "d"}]

        with patch.object(sandbox, "_upload_artifacts", _ok), patch.object(
            mod, "register_task",
            lambda conv_id, task: captured_tasks.append(task),
        ):
            sandbox._schedule_artifact_upload(exec_result)
            assert len(captured_tasks) == 1
            result = await captured_tasks[0]

        assert result == {
            "type": "artifacts",
            "artifacts": [{"fileName": "out.csv", "documentId": "d"}],
        }


# ---------------------------------------------------------------------------
# get_document_skill
# ---------------------------------------------------------------------------


class TestGetDocumentSkill:
    """Exercise the get_document_skill tool (pptx / docx markdown loader)."""

    @pytest.mark.asyncio
    async def test_pptx_returns_skill_markdown(self):
        from app.agents.actions.coding_sandbox.coding_sandbox import CodingSandbox

        sandbox = CodingSandbox(_make_state())
        success, result_json = await sandbox.get_document_skill(kind="pptx")

        assert success is True
        data = json.loads(result_json)
        assert data["success"] is True
        assert data["kind"] == "pptx"
        # Skill file is authored as markdown and must contain the top-level
        # heading so the agent knows it loaded the right thing.
        assert "PPTX Design Skill" in data["skill"]
        # Spot-check for at least one curated palette so a silent truncation
        # of the skill file would break this test.
        assert "Midnight Executive" in data["skill"]

    @pytest.mark.asyncio
    async def test_docx_returns_skill_markdown(self):
        from app.agents.actions.coding_sandbox.coding_sandbox import CodingSandbox

        sandbox = CodingSandbox(_make_state())
        success, result_json = await sandbox.get_document_skill(kind="docx")

        assert success is True
        data = json.loads(result_json)
        assert data["kind"] == "docx"
        assert "DOCX Design Skill" in data["skill"]
        # Spot-check that the dual-width table guidance survived.
        assert "WidthType.DXA" in data["skill"]

    @pytest.mark.asyncio
    async def test_invalid_kind_is_rejected(self):
        from app.agents.actions.coding_sandbox.coding_sandbox import CodingSandbox

        sandbox = CodingSandbox(_make_state())
        success, result_json = await sandbox.get_document_skill(kind="xlsx")

        assert success is False
        data = json.loads(result_json)
        assert data["error_code"] == "invalid_kind"

    @pytest.mark.asyncio
    async def test_missing_skill_file_is_surfaced(self):
        """If every known skill location is missing, the tool returns a structured error."""
        from app.agents.actions.coding_sandbox.coding_sandbox import CodingSandbox
        from app.agents.actions.coding_sandbox import coding_sandbox as mod

        sandbox = CodingSandbox(_make_state())
        with patch.object(
            mod, "_load_skill_markdown",
            side_effect=FileNotFoundError("probed: /a; /b"),
        ):
            success, result_json = await sandbox.get_document_skill(kind="pptx")

        assert success is False
        data = json.loads(result_json)
        assert data["error_code"] == "skill_missing"
        # The probe list should surface to help operators diagnose which
        # packaging path is broken.
        assert "probed" in data["message"]


# ---------------------------------------------------------------------------
# ingest_template
# ---------------------------------------------------------------------------


class TestIngestTemplate:
    """Exercise the ingest_template tool (pptx / docx structural summary)."""

    @pytest.mark.asyncio
    async def test_file_not_found_returns_structured_error(self):
        from app.agents.actions.coding_sandbox.coding_sandbox import CodingSandbox
        from app.agents.actions.coding_sandbox import coding_sandbox as mod

        sandbox = CodingSandbox(_make_state())
        with patch.object(mod, "_find_latest_sandbox_file", return_value=None):
            success, result_json = await sandbox.ingest_template(
                file_name="missing.pptx",
            )

        assert success is False
        data = json.loads(result_json)
        assert data["error_code"] == "file_not_found"

    @pytest.mark.asyncio
    async def test_pptx_success(self, tmp_path):
        from app.agents.actions.coding_sandbox.coding_sandbox import CodingSandbox
        from app.agents.actions.coding_sandbox import coding_sandbox as mod

        fake_pptx = tmp_path / "template.pptx"
        fake_pptx.write_bytes(b"PK\x03\x04stub")

        sandbox = CodingSandbox(_make_state())
        with patch.object(
            mod, "_find_latest_sandbox_file", return_value=fake_pptx,
        ), patch.object(
            mod, "_summarise_pptx",
            return_value={"slide_count": 3, "layouts": []},
        ):
            success, result_json = await sandbox.ingest_template(
                file_name="template.pptx",
            )

        assert success is True
        data = json.loads(result_json)
        assert data["summary"]["slide_count"] == 3
        assert data["file_name"] == "template.pptx"
        assert data["template_path"] == str(fake_pptx)

    @pytest.mark.asyncio
    async def test_docx_success(self, tmp_path):
        from app.agents.actions.coding_sandbox.coding_sandbox import CodingSandbox
        from app.agents.actions.coding_sandbox import coding_sandbox as mod

        fake_docx = tmp_path / "brief.docx"
        fake_docx.write_bytes(b"PK\x03\x04stub")

        sandbox = CodingSandbox(_make_state())
        with patch.object(
            mod, "_find_latest_sandbox_file", return_value=fake_docx,
        ), patch.object(
            mod, "_summarise_docx",
            return_value={"paragraph_count": 12, "styles": [], "sections": []},
        ):
            success, result_json = await sandbox.ingest_template(
                file_name="brief.docx",
            )

        assert success is True
        data = json.loads(result_json)
        assert data["summary"]["paragraph_count"] == 12

    @pytest.mark.asyncio
    async def test_unsupported_extension(self, tmp_path):
        from app.agents.actions.coding_sandbox.coding_sandbox import CodingSandbox
        from app.agents.actions.coding_sandbox import coding_sandbox as mod

        fake = tmp_path / "sheet.xlsx"
        fake.write_bytes(b"PK\x03\x04stub")

        sandbox = CodingSandbox(_make_state())
        with patch.object(mod, "_find_latest_sandbox_file", return_value=fake):
            success, result_json = await sandbox.ingest_template(
                file_name="sheet.xlsx",
            )

        assert success is False
        data = json.loads(result_json)
        assert data["error_code"] == "unsupported_extension"


# ---------------------------------------------------------------------------
# render_artifact_preview
# ---------------------------------------------------------------------------


class TestRenderArtifactPreview:
    """Exercise the visual-QA render tool."""

    @pytest.mark.asyncio
    async def test_disabled_via_env_flag(self, monkeypatch):
        from app.agents.actions.coding_sandbox.coding_sandbox import CodingSandbox

        monkeypatch.setenv("SANDBOX_VISUAL_QA", "off")
        sandbox = CodingSandbox(_make_state())
        success, result_json = await sandbox.render_artifact_preview(
            file_name="deck.pptx",
        )
        assert success is False
        data = json.loads(result_json)
        assert data["error_code"] == "visual_qa_disabled"

    @pytest.mark.asyncio
    async def test_file_not_found(self, monkeypatch):
        from app.agents.actions.coding_sandbox.coding_sandbox import CodingSandbox
        from app.agents.actions.coding_sandbox import coding_sandbox as mod

        monkeypatch.setenv("SANDBOX_VISUAL_QA", "on")
        sandbox = CodingSandbox(_make_state())
        with patch.object(mod, "_find_latest_sandbox_file", return_value=None):
            success, result_json = await sandbox.render_artifact_preview(
                file_name="missing.pptx",
            )
        assert success is False
        data = json.loads(result_json)
        assert data["error_code"] == "file_not_found"

    @pytest.mark.asyncio
    async def test_unsupported_extension(self, monkeypatch, tmp_path):
        from app.agents.actions.coding_sandbox.coding_sandbox import CodingSandbox
        from app.agents.actions.coding_sandbox import coding_sandbox as mod

        monkeypatch.setenv("SANDBOX_VISUAL_QA", "on")
        fake = tmp_path / "a.txt"
        fake.write_text("hi")
        sandbox = CodingSandbox(_make_state())
        with patch.object(mod, "_find_latest_sandbox_file", return_value=fake):
            success, result_json = await sandbox.render_artifact_preview(
                file_name="a.txt",
            )
        assert success is False
        data = json.loads(result_json)
        assert data["error_code"] == "unsupported_extension"

    @pytest.mark.asyncio
    async def test_renderer_missing_returns_actionable_error(
        self, monkeypatch, tmp_path,
    ):
        from app.agents.actions.coding_sandbox.coding_sandbox import CodingSandbox
        from app.agents.actions.coding_sandbox import coding_sandbox as mod

        monkeypatch.setenv("SANDBOX_VISUAL_QA", "on")
        fake = tmp_path / "deck.pptx"
        fake.write_bytes(b"PK\x03\x04stub")
        sandbox = CodingSandbox(_make_state())

        with patch.object(mod, "_find_latest_sandbox_file", return_value=fake), \
             patch.object(
                 mod, "_render_to_images",
                 side_effect=FileNotFoundError("soffice missing"),
             ):
            success, result_json = await sandbox.render_artifact_preview(
                file_name="deck.pptx",
            )

        assert success is False
        data = json.loads(result_json)
        assert data["error_code"] == "renderer_missing"
        assert "soffice missing" in data["message"]

    @pytest.mark.asyncio
    async def test_render_failure_is_captured(self, monkeypatch, tmp_path):
        from app.agents.actions.coding_sandbox.coding_sandbox import CodingSandbox
        from app.agents.actions.coding_sandbox import coding_sandbox as mod

        monkeypatch.setenv("SANDBOX_VISUAL_QA", "on")
        fake = tmp_path / "deck.pptx"
        fake.write_bytes(b"PK\x03\x04stub")
        sandbox = CodingSandbox(_make_state())

        with patch.object(mod, "_find_latest_sandbox_file", return_value=fake), \
             patch.object(
                 mod, "_render_to_images", side_effect=RuntimeError("broken pdf"),
             ):
            success, result_json = await sandbox.render_artifact_preview(
                file_name="deck.pptx",
            )

        assert success is False
        data = json.loads(result_json)
        assert data["error_code"] == "render_failed"
        assert "broken pdf" in data["message"]

    @pytest.mark.asyncio
    async def test_success_surfaces_page_images_as_artifacts(
        self, monkeypatch, tmp_path,
    ):
        from app.agents.actions.coding_sandbox.coding_sandbox import CodingSandbox
        from app.agents.actions.coding_sandbox import coding_sandbox as mod

        monkeypatch.setenv("SANDBOX_VISUAL_QA", "on")
        fake = tmp_path / "deck.pptx"
        fake.write_bytes(b"PK\x03\x04stub")
        page1 = tmp_path / "deck-page-1.jpg"
        page1.write_bytes(b"\xff\xd8\xff")
        page2 = tmp_path / "deck-page-2.jpg"
        page2.write_bytes(b"\xff\xd8\xff")

        sandbox = CodingSandbox(_make_state())

        with patch.object(mod, "_find_latest_sandbox_file", return_value=fake), \
             patch.object(
                 mod, "_render_to_images", return_value=([page1, page2], []),
             ), patch.object(sandbox, "_schedule_artifact_upload") as mock_schedule:
            success, result_json = await sandbox.render_artifact_preview(
                file_name="deck.pptx", max_pages=10,
            )

        assert success is True
        data = json.loads(result_json)
        assert data["page_count"] == 2
        assert len(data["artifacts"]) == 2
        # Every surfaced preview image must be a JPEG — the downstream viewer
        # relies on the MIME type to render thumbnails correctly.
        for a in data["artifacts"]:
            assert a["mimeType"] == "image/jpeg"
        # The upload pipeline must have been invoked with the preview source tool.
        mock_schedule.assert_called_once()
        assert (
            mock_schedule.call_args.kwargs.get("source_tool")
            == "coding_sandbox.render_artifact_preview"
        )


# ---------------------------------------------------------------------------
# _find_latest_sandbox_file (path-traversal defence)
# ---------------------------------------------------------------------------


class TestFindLatestSandboxFile:
    def test_returns_most_recent_match(self, tmp_path, monkeypatch):
        from app.agents.actions.coding_sandbox import coding_sandbox as mod

        older = tmp_path / "a" / "deck.pptx"
        older.parent.mkdir()
        older.write_bytes(b"old")
        newer = tmp_path / "b" / "deck.pptx"
        newer.parent.mkdir()
        newer.write_bytes(b"new")
        # Make `newer` strictly more recent even on filesystems with coarse mtime.
        os.utime(older, (1_700_000_000, 1_700_000_000))
        os.utime(newer, (1_800_000_000, 1_800_000_000))

        monkeypatch.setattr(mod, "_sandbox_roots", lambda: (tmp_path,))
        found = mod._find_latest_sandbox_file("deck.pptx")
        assert found == newer

    def test_rejects_path_traversal(self, monkeypatch):
        from app.agents.actions.coding_sandbox import coding_sandbox as mod

        # Even if there is a real sandbox root, a name that contains a path
        # separator must be rejected so an agent can't escape the tree.
        monkeypatch.setattr(mod, "_sandbox_roots", lambda: (Path("/tmp"),))
        assert mod._find_latest_sandbox_file("../etc/passwd") is None
        assert mod._find_latest_sandbox_file("/etc/passwd") is None

    def test_no_match_returns_none(self, tmp_path, monkeypatch):
        from app.agents.actions.coding_sandbox import coding_sandbox as mod

        monkeypatch.setattr(mod, "_sandbox_roots", lambda: (tmp_path,))
        assert mod._find_latest_sandbox_file("nope.pptx") is None


# ---------------------------------------------------------------------------
# _render_to_images
# ---------------------------------------------------------------------------


class TestRenderToImages:
    def test_missing_soffice_raises_file_not_found(self, tmp_path, monkeypatch):
        from app.agents.actions.coding_sandbox import coding_sandbox as mod

        src = tmp_path / "deck.pptx"
        src.write_bytes(b"PK\x03\x04stub")

        monkeypatch.setattr(mod, "shutil_which", lambda c: None)
        with pytest.raises(FileNotFoundError, match="soffice"):
            mod._render_to_images(src, max_pages=5)

    def test_missing_pdftoppm_raises_file_not_found(self, tmp_path, monkeypatch):
        from app.agents.actions.coding_sandbox import coding_sandbox as mod

        src = tmp_path / "deck.pptx"
        src.write_bytes(b"PK\x03\x04stub")

        monkeypatch.setattr(
            mod, "shutil_which",
            lambda c: "/usr/bin/soffice" if c == "soffice" else None,
        )
        with pytest.raises(FileNotFoundError, match="pdftoppm"):
            mod._render_to_images(src, max_pages=5)

    def test_happy_path_invokes_soffice_then_pdftoppm(
        self, tmp_path, monkeypatch,
    ):
        from app.agents.actions.coding_sandbox import coding_sandbox as mod

        src = tmp_path / "deck.pptx"
        src.write_bytes(b"PK\x03\x04stub")

        monkeypatch.setattr(mod, "shutil_which", lambda c: "/usr/bin/" + c)

        # Track the two subprocess.run calls and fabricate the expected
        # filesystem side effects (pdf + page jpegs) so the function
        # succeeds without LibreOffice actually being installed.
        calls: list[list[str]] = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            preview_dir = src.parent / f"preview_{src.stem}"
            preview_dir.mkdir(parents=True, exist_ok=True)
            if cmd[0] == "soffice":
                (preview_dir / f"{src.stem}.pdf").write_bytes(b"%PDF-1.4")
            elif cmd[0] == "pdftoppm":
                (preview_dir / f"{src.stem}-page-1.jpg").write_bytes(b"\xff\xd8\xff")
                (preview_dir / f"{src.stem}-page-2.jpg").write_bytes(b"\xff\xd8\xff")
            return MagicMock(returncode=0, stderr="", stdout="")

        with patch("subprocess.run", side_effect=fake_run):
            images, warnings = mod._render_to_images(src, max_pages=10)

        assert len(images) == 2
        assert all(p.suffix == ".jpg" for p in images)
        # Soffice must run with --headless + --convert-to pdf, and pdftoppm
        # must receive the -jpeg flag — the UI depends on JPEGs, not PNGs.
        assert calls[0][0] == "soffice"
        assert "--headless" in calls[0]
        assert "pdf" in calls[0]
        assert calls[1][0] == "pdftoppm"
        assert "-jpeg" in calls[1]
        # max_pages must be forwarded as -l so long documents don't blow up.
        assert "10" in calls[1]
        assert warnings == []

    def test_soffice_nonzero_exit_raises_runtime_error(
        self, tmp_path, monkeypatch,
    ):
        from app.agents.actions.coding_sandbox import coding_sandbox as mod

        src = tmp_path / "deck.pptx"
        src.write_bytes(b"PK\x03\x04stub")
        monkeypatch.setattr(mod, "shutil_which", lambda c: "/usr/bin/" + c)

        with patch(
            "subprocess.run",
            return_value=MagicMock(returncode=1, stderr="boom", stdout=""),
        ), pytest.raises(RuntimeError, match="soffice failed"):
            mod._render_to_images(src, max_pages=3)


# ---------------------------------------------------------------------------
# PresentationSpec + render_presentation_from_spec
# ---------------------------------------------------------------------------


class TestPresentationSpecValidation:
    def test_minimal_spec_roundtrips(self):
        from app.agents.actions.coding_sandbox.coding_sandbox import PresentationSpec

        spec = PresentationSpec.model_validate({
            "slides": [{"type": "title", "title": "Hi"}],
        })
        assert spec.theme == "midnightExecutive"
        assert spec.layout == "16x9"
        assert spec.file_name == "presentation.pptx"
        assert spec.slides[0].type == "title"

    def test_rejects_unknown_slide_type(self):
        from app.agents.actions.coding_sandbox.coding_sandbox import PresentationSpec
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            PresentationSpec.model_validate({
                "slides": [{"type": "nope", "title": "Hi"}],
            })

    def test_rejects_unknown_layout(self):
        from app.agents.actions.coding_sandbox.coding_sandbox import PresentationSpec
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            PresentationSpec.model_validate({
                "layout": "portrait",
                "slides": [{"type": "title", "title": "Hi"}],
            })


class TestRenderFromSpecCode:
    """_render_from_spec_code produces the TS program we hand off to the sandbox."""

    def test_code_embeds_spec_and_imports_pipeshub_slides(self):
        from app.agents.actions.coding_sandbox.coding_sandbox import (
            _render_from_spec_code,
        )

        code = _render_from_spec_code(
            json.dumps({"theme": "coralEnergy", "slides": []}),
            "deck.pptx",
        )
        # Must import from the in-repo helper, not raw pptxgenjs — otherwise
        # the design system's palette/layout defaults wouldn't apply.
        assert 'from "pipeshub-slides"' in code
        # File name must be written under OUTPUT_DIR (never an absolute host path).
        assert 'process.env.OUTPUT_DIR' in code
        assert '"deck.pptx"' in code
        # The deck spec must be embedded as a string that JSON.parse() can read.
        assert "JSON.parse(" in code

    def test_code_handles_every_primitive_type(self):
        """Every slide type the schema accepts must have a matching switch branch."""
        from app.agents.actions.coding_sandbox.coding_sandbox import (
            _render_from_spec_code,
        )

        code = _render_from_spec_code(json.dumps({"slides": []}), "x.pptx")
        for slide_type in (
            "title",
            "section_divider",
            "two_column",
            "stat_grid",
            "icon_rows",
            "timeline",
            "closing",
        ):
            assert f'"{slide_type}"' in code, (
                f"render_from_spec must handle slide type {slide_type!r} — "
                f"if you're adding a new type, extend _render_from_spec_code."
            )

    def test_malicious_filename_is_safely_json_encoded(self):
        """A weird file_name must not break out of the JS string literal."""
        from app.agents.actions.coding_sandbox.coding_sandbox import (
            _render_from_spec_code,
        )

        evil = 'evil"; process.exit(1); //.pptx'
        code = _render_from_spec_code(json.dumps({"slides": []}), evil)
        # The file name must be embedded via json.dumps (which always
        # produces a valid JSON string literal that's also a valid JS
        # string literal) rather than pasted raw — that's what stops
        # an agent-provided file_name from injecting trailing JS.
        assert json.dumps(evil) in code
        # And the raw malicious substring must not appear verbatim,
        # because that would mean the quote terminated the literal.
        assert evil not in code


class TestRenderPresentationFromSpec:
    @pytest.mark.asyncio
    async def test_invalid_spec_returns_structured_error(self):
        from app.agents.actions.coding_sandbox.coding_sandbox import CodingSandbox

        sandbox = CodingSandbox(_make_state())
        success, result_json = await sandbox.render_presentation_from_spec(
            spec={"slides": [{"type": "nope"}]},
        )

        assert success is False
        data = json.loads(result_json)
        assert data["error_code"] == "invalid_spec"

    @pytest.mark.asyncio
    async def test_empty_spec_returns_structured_error(self):
        from app.agents.actions.coding_sandbox.coding_sandbox import CodingSandbox

        sandbox = CodingSandbox(_make_state())
        success, result_json = await sandbox.render_presentation_from_spec(
            spec={"slides": []},
        )

        assert success is False
        data = json.loads(result_json)
        assert data["error_code"] == "empty_spec"

    @pytest.mark.asyncio
    async def test_success_uploads_and_returns_artifact(self):
        from app.agents.actions.coding_sandbox.coding_sandbox import CodingSandbox

        sandbox = CodingSandbox(_make_state())

        artifact = ArtifactOutput(
            file_name="deck.pptx",
            file_path="/tmp/deck.pptx",
            mime_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
            size_bytes=12345,
        )
        mock_result = ExecutionResult(
            success=True, stdout="", exit_code=0,
            execution_time_ms=250, artifacts=[artifact],
        )
        with patch(
            "app.agents.actions.coding_sandbox.coding_sandbox.get_executor",
        ) as mock_get:
            mock_executor = MagicMock()
            mock_executor.execute = AsyncMock(return_value=mock_result)
            mock_get.return_value = mock_executor

            with patch.object(sandbox, "_schedule_artifact_upload") as mock_schedule:
                success, result_json = await sandbox.render_presentation_from_spec(
                    spec={
                        "theme": "coralEnergy",
                        "file_name": "myreport",  # extension must be auto-added
                        "slides": [
                            {"type": "title", "title": "Hi"},
                            {"type": "closing", "title": "Bye"},
                        ],
                    },
                )

        assert success is True
        data = json.loads(result_json)
        assert data["slide_count"] == 2
        assert data["theme"] == "coralEnergy"
        assert data["file_name"].endswith(".pptx")
        assert len(data["artifacts"]) == 1

        # Executor must have been invoked with TypeScript + pptxgenjs
        # package (the helper library is resolvable via NODE_PATH, not npm).
        assert mock_executor.execute.await_count == 1
        kwargs = mock_executor.execute.await_args.kwargs
        assert kwargs["language"].value == "typescript"
        assert "pptxgenjs" in kwargs["packages"]
        # The upload pipeline must attribute the artifact to the right tool.
        assert (
            mock_schedule.call_args.kwargs.get("source_tool")
            == "coding_sandbox.render_presentation_from_spec"
        )

    @pytest.mark.asyncio
    async def test_executor_failure_is_surfaced(self):
        from app.agents.actions.coding_sandbox.coding_sandbox import CodingSandbox

        sandbox = CodingSandbox(_make_state())
        with patch(
            "app.agents.actions.coding_sandbox.coding_sandbox.get_executor",
            side_effect=RuntimeError("no executor"),
        ):
            success, result_json = await sandbox.render_presentation_from_spec(
                spec={"slides": [{"type": "title", "title": "Hi"}]},
            )
        assert success is False
        data = json.loads(result_json)
        assert data["error_code"] == "executor_failed"
        assert "no executor" in data["message"]


# ---------------------------------------------------------------------------
# Design skill files on disk -- regression net for accidental moves / renames
# ---------------------------------------------------------------------------


class TestLoadSkillMarkdown:
    """_load_skill_markdown must find the skills across every packaging layout.

    We deliberately simulate each environment rather than relying on the
    actual filesystem, so regressions in one path don't get masked by the
    others succeeding.
    """

    def test_loads_from_source_checkout(self):
        """The common case: the skills live next to the module on disk."""
        from app.agents.actions.coding_sandbox.coding_sandbox import (
            _load_skill_markdown,
        )

        # In the repo, the source file IS there — exercise the happy path
        # end-to-end rather than mocking it.
        content = _load_skill_markdown("pptx")
        assert "PPTX Design Skill" in content

    def test_falls_back_to_importlib_resources(self, monkeypatch, tmp_path):
        """When the source sibling is missing, importlib.resources should win."""
        import app.agents.actions.coding_sandbox.coding_sandbox as mod

        # Force the filesystem-sibling probe to fail by pretending the
        # module lives somewhere that has no skills dir.
        fake_module = tmp_path / "pkg" / "coding_sandbox.py"
        fake_module.parent.mkdir(parents=True)
        fake_module.write_text("# stub")
        monkeypatch.setattr(mod, "__file__", str(fake_module))

        # Stub importlib.resources to return a Path-ish object with a
        # real file behind it.
        real_skill = tmp_path / "docx.md"
        real_skill.write_text("# DOCX Design Skill (stub)")

        class _FakeResource:
            def __init__(self, path: Path):
                self._path = path

            def joinpath(self, *parts: str) -> "_FakeResource":
                return _FakeResource(self._path.joinpath(*parts))

            def is_file(self) -> bool:
                return self._path.is_file()

            def read_text(self, encoding: str = "utf-8") -> str:
                return self._path.read_text(encoding=encoding)

            def __str__(self) -> str:
                return str(self._path)

        monkeypatch.setattr(
            "importlib.resources.files",
            lambda pkg: _FakeResource(tmp_path) if pkg.startswith(
                "app.agents.actions.coding_sandbox",
            ) else None,
        )
        # Layout the fake resource so `joinpath("skills", "docx.md")`
        # lands on our real_skill file.
        (tmp_path / "skills").mkdir(exist_ok=True)
        (tmp_path / "skills" / "docx.md").write_text(
            "# DOCX Design Skill (stub)",
        )

        content = mod._load_skill_markdown("docx")
        assert "DOCX Design Skill" in content

    def test_falls_back_to_pyinstaller_meipass(self, monkeypatch, tmp_path):
        """When nothing else works, we try sys._MEIPASS (frozen binaries)."""
        import sys
        import app.agents.actions.coding_sandbox.coding_sandbox as mod

        # Blow up the source-sibling probe.
        fake_module = tmp_path / "pkg" / "coding_sandbox.py"
        fake_module.parent.mkdir(parents=True)
        fake_module.write_text("# stub")
        monkeypatch.setattr(mod, "__file__", str(fake_module))

        # Disable importlib.resources lookup.
        monkeypatch.setattr(
            "importlib.resources.files",
            lambda pkg: (_ for _ in ()).throw(ModuleNotFoundError(pkg)),
        )

        # Stage a fake PyInstaller extraction root.
        meipass = tmp_path / "_MEIPASS"
        frozen_skill = (
            meipass / "app" / "agents" / "actions"
            / "coding_sandbox" / "skills" / "pptx.md"
        )
        frozen_skill.parent.mkdir(parents=True)
        frozen_skill.write_text("# PPTX Design Skill (frozen)")
        monkeypatch.setattr(sys, "_MEIPASS", str(meipass), raising=False)

        content = mod._load_skill_markdown("pptx")
        assert "frozen" in content

    def test_raises_file_not_found_with_probe_list(self, monkeypatch, tmp_path):
        """When every path fails, raise FileNotFoundError that names each probe."""
        import sys
        import app.agents.actions.coding_sandbox.coding_sandbox as mod

        fake_module = tmp_path / "pkg" / "coding_sandbox.py"
        fake_module.parent.mkdir(parents=True)
        fake_module.write_text("# stub")
        monkeypatch.setattr(mod, "__file__", str(fake_module))
        monkeypatch.setattr(
            "importlib.resources.files",
            lambda pkg: (_ for _ in ()).throw(ModuleNotFoundError(pkg)),
        )
        # Make sure _MEIPASS is absent.
        monkeypatch.delattr(sys, "_MEIPASS", raising=False)

        with pytest.raises(FileNotFoundError) as excinfo:
            mod._load_skill_markdown("pptx")
        # The error message must include at least one attempted path so
        # an operator can see which layout is broken.
        assert "pptx.md" in str(excinfo.value)
        assert "probed" in str(excinfo.value)


class TestSkillFilesOnDisk:
    """The skill markdown files ship alongside the tool. If someone moves them
    the package breaks silently at runtime; fail loudly at test time instead."""

    def test_skill_files_exist(self):
        import app.agents.actions.coding_sandbox.coding_sandbox as mod

        base = Path(mod.__file__).parent / "skills"
        assert (base / "pptx.md").is_file()
        assert (base / "docx.md").is_file()

    def test_skills_mention_the_helper_libraries(self):
        """Skills must reference the helper libraries so the agent knows to use them."""
        import app.agents.actions.coding_sandbox.coding_sandbox as mod

        base = Path(mod.__file__).parent / "skills"
        pptx = (base / "pptx.md").read_text(encoding="utf-8")
        docx = (base / "docx.md").read_text(encoding="utf-8")
        assert "pipeshub-slides" in pptx
        assert "pipeshub-docs" in docx
