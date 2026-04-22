"""Tests for app.agents.actions.image_generator.image_generator."""

from __future__ import annotations

import asyncio
import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_state(**overrides):
    """Return a minimal ChatState-like dict for toolset tests."""
    state = {
        "conversation_id": "conv-123",
        "org_id": "org-456",
        "user_id": "user-789",
        "blob_store": MagicMock(),
        "config_service": MagicMock(),
        "graph_provider": MagicMock(),
    }
    state.update(overrides)
    return state


class TestImageGeneratorImport:
    def test_imports(self):
        from app.agents.actions.image_generator.image_generator import (
            ImageGenerator,
        )
        assert ImageGenerator is not None


class TestGenerateImage:
    @pytest.mark.asyncio
    async def test_no_prompt(self):
        from app.agents.actions.image_generator.image_generator import (
            ImageGenerator,
        )
        tool = ImageGenerator(_make_state())
        success, payload = await tool.generate_image(prompt="   ")
        assert success is False
        data = json.loads(payload)
        assert data["success"] is False
        assert "Prompt is required" in data["error"]

    @pytest.mark.asyncio
    async def test_no_config_returns_error(self):
        from app.agents.actions.image_generator import image_generator as mod

        tool = mod.ImageGenerator(_make_state())

        with patch.object(
            mod, "get_image_generation_config", AsyncMock(return_value=None),
        ):
            success, payload = await tool.generate_image(prompt="a cat")

        assert success is False
        data = json.loads(payload)
        assert "No image-generation model" in data["error"]

    @pytest.mark.asyncio
    async def test_happy_path_schedules_upload(self):
        from app.agents.actions.image_generator import image_generator as mod
        from app.config.constants.arangodb import Connectors

        state = _make_state()
        tool = mod.ImageGenerator(state)

        image_bytes = b"\x89PNG-fake"
        mock_adapter = SimpleNamespace(
            provider="openAI",
            model="gpt-image-1",
            generate=AsyncMock(return_value=[image_bytes, image_bytes]),
        )
        fake_config = {
            "provider": "openAI",
            "configuration": {"apiKey": "sk", "model": "gpt-image-1"},
            "isDefault": True,
        }

        mock_upload = AsyncMock(return_value={
            "fileName": "img.png",
            "signedUrl": "https://blob/1",
            "mimeType": "image/png",
            "sizeBytes": len(image_bytes),
            "recordId": "rec-1",
        })

        captured_tasks: list[asyncio.Task] = []

        def _fake_register(conv_id: str, task: asyncio.Task) -> None:
            captured_tasks.append(task)

        with patch.object(
            mod, "get_image_generation_config",
            AsyncMock(return_value=fake_config),
        ), patch(
            "app.utils.aimodels.get_image_generation_model",
            return_value=mock_adapter,
        ), patch.object(
            mod, "upload_bytes_artifact", mock_upload,
        ), patch.object(
            mod, "register_task", _fake_register,
        ):
            success, payload = await tool.generate_image(
                prompt="a watercolor fox",
                size="1024x1792",
                n=2,
            )

            assert success is True
            data = json.loads(payload)
            assert data["success"] is True
            assert data["count"] == 2
            assert data["provider"] == "openAI"
            assert data["model"] == "gpt-image-1"
            assert data["size"] == "1024x1792"

            # A background upload task should have been scheduled.
            assert len(captured_tasks) == 1
            result = await captured_tasks[0]
            assert result is not None
            assert result["type"] == "artifacts"
            assert len(result["artifacts"]) == 2

        mock_adapter.generate.assert_awaited_once_with(
            "a watercolor fox", size="1024x1792", n=2,
        )
        assert mock_upload.await_count == 2
        # Connector is tagged so these rows are distinguishable from
        # coding-sandbox artifacts.
        kwargs = mock_upload.await_args_list[0].kwargs
        assert kwargs["connector_name"] == Connectors.IMAGE_GENERATION
        assert kwargs["mime_type"] == "image/png"
        assert kwargs["source_tool"] == "image_generator.generate_image"

    @pytest.mark.asyncio
    async def test_unsupported_size_falls_back(self):
        from app.agents.actions.image_generator import image_generator as mod

        state = _make_state()
        tool = mod.ImageGenerator(state)

        mock_adapter = SimpleNamespace(
            provider="gemini",
            model="gemini-2.5-flash-image",
            generate=AsyncMock(return_value=[b"ok"]),
        )
        fake_config = {
            "provider": "gemini",
            "configuration": {"apiKey": "x", "model": "gemini-2.5-flash-image"},
            "isDefault": True,
        }

        with patch.object(
            mod, "get_image_generation_config",
            AsyncMock(return_value=fake_config),
        ), patch(
            "app.utils.aimodels.get_image_generation_model",
            return_value=mock_adapter,
        ), patch.object(
            mod, "upload_bytes_artifact", AsyncMock(return_value=None),
        ), patch.object(
            mod, "register_task", lambda *a, **kw: None,
        ):
            success, payload = await tool.generate_image(
                prompt="x", size="9999x9999", n=1,
            )

        assert success is True
        data = json.loads(payload)
        assert data["size"] == "1024x1024"


class TestGenerateImageErrorPaths:
    """Cover the early-exit / failure branches in ``generate_image``."""

    @pytest.mark.asyncio
    async def test_missing_config_service(self):
        from app.agents.actions.image_generator.image_generator import (
            ImageGenerator,
        )

        state = _make_state(config_service=None)
        tool = ImageGenerator(state)

        success, payload = await tool.generate_image(prompt="hello")
        assert success is False
        data = json.loads(payload)
        assert "config_service unavailable" in data["error"]

    @pytest.mark.asyncio
    async def test_get_config_raises(self):
        from app.agents.actions.image_generator import image_generator as mod

        tool = mod.ImageGenerator(_make_state())

        with patch.object(
            mod, "get_image_generation_config",
            AsyncMock(side_effect=RuntimeError("boom")),
        ):
            success, payload = await tool.generate_image(prompt="hello")

        assert success is False
        data = json.loads(payload)
        assert "Failed to load image generation config" in data["error"]
        assert "boom" in data["error"]

    @pytest.mark.asyncio
    async def test_adapter_build_failure(self):
        from app.agents.actions.image_generator import image_generator as mod

        tool = mod.ImageGenerator(_make_state())

        with patch.object(
            mod, "get_image_generation_config",
            AsyncMock(return_value={"provider": "openAI", "configuration": {}}),
        ), patch(
            "app.utils.aimodels.get_image_generation_model",
            side_effect=RuntimeError("sdk missing"),
        ):
            success, payload = await tool.generate_image(prompt="hello")

        assert success is False
        data = json.loads(payload)
        assert "Failed to initialise" in data["error"]
        assert "sdk missing" in data["error"]

    @pytest.mark.asyncio
    async def test_adapter_generate_raises(self):
        from app.agents.actions.image_generator import image_generator as mod

        tool = mod.ImageGenerator(_make_state())

        mock_adapter = SimpleNamespace(
            provider="openAI",
            model="gpt-image-1",
            generate=AsyncMock(side_effect=RuntimeError("rate limit")),
        )
        with patch.object(
            mod, "get_image_generation_config",
            AsyncMock(return_value={"provider": "openAI", "configuration": {}}),
        ), patch(
            "app.utils.aimodels.get_image_generation_model",
            return_value=mock_adapter,
        ):
            success, payload = await tool.generate_image(prompt="hello")

        assert success is False
        data = json.loads(payload)
        assert data["provider"] == "openAI"
        assert data["model"] == "gpt-image-1"
        assert "Image generation failed" in data["error"]
        assert "rate limit" in data["error"]

    @pytest.mark.asyncio
    async def test_adapter_returns_no_images(self):
        from app.agents.actions.image_generator import image_generator as mod

        tool = mod.ImageGenerator(_make_state())

        mock_adapter = SimpleNamespace(
            provider="gemini",
            model="imagen-3",
            generate=AsyncMock(return_value=[]),
        )
        with patch.object(
            mod, "get_image_generation_config",
            AsyncMock(return_value={"provider": "gemini", "configuration": {}}),
        ), patch(
            "app.utils.aimodels.get_image_generation_model",
            return_value=mock_adapter,
        ):
            success, payload = await tool.generate_image(prompt="hello")

        assert success is False
        data = json.loads(payload)
        assert data["provider"] == "gemini"
        assert data["error"] == "Provider returned no images"


class TestScheduleArtifactUpload:
    """Directly exercise ``_schedule_artifact_upload`` branches."""

    @pytest.mark.asyncio
    async def test_skip_when_no_conversation_id(self):
        from app.agents.actions.image_generator import image_generator as mod

        tool = mod.ImageGenerator(_make_state(conversation_id=None))

        captured: list = []
        with patch.object(mod, "register_task", lambda *a, **kw: captured.append(a)):
            tool._schedule_artifact_upload(
                images=[b"abc"],
                blob_store=MagicMock(),
                graph_provider=MagicMock(),
                org_id="org-456",
                conversation_id=None,
                user_id="u",
                model_name="m",
            )

        assert captured == []

    @pytest.mark.asyncio
    async def test_blob_store_fallback_created_when_missing(self):
        from app.agents.actions.image_generator import image_generator as mod

        state = _make_state(blob_store=None)
        tool = mod.ImageGenerator(state)

        fake_store = MagicMock()
        fake_blob_cls = MagicMock(return_value=fake_store)

        captured_tasks: list[asyncio.Task] = []

        mock_upload = AsyncMock(return_value={
            "fileName": "img.png",
            "signedUrl": "https://blob/1",
            "documentId": "doc-1",
        })

        with patch(
            "app.modules.transformers.blob_storage.BlobStorage", fake_blob_cls,
        ), patch.object(
            mod, "upload_bytes_artifact", mock_upload,
        ), patch.object(
            mod, "register_task",
            lambda conv_id, task: captured_tasks.append(task),
        ):
            tool._schedule_artifact_upload(
                images=[b"img-bytes"],
                blob_store=None,
                graph_provider=state["graph_provider"],
                org_id="org-456",
                conversation_id="conv-123",
                user_id="user-789",
                model_name="gpt-image-1",
            )

            assert len(captured_tasks) == 1
            result = await captured_tasks[0]

        assert result is not None
        assert result["type"] == "artifacts"
        assert len(result["artifacts"]) == 1
        fake_blob_cls.assert_called_once()
        mock_upload.assert_awaited_once()
        # upload called with the fallback-built store
        assert mock_upload.await_args.kwargs["blob_store"] is fake_store

    @pytest.mark.asyncio
    async def test_blob_store_fallback_construction_fails(self):
        from app.agents.actions.image_generator import image_generator as mod

        state = _make_state(blob_store=None)
        tool = mod.ImageGenerator(state)

        captured_tasks: list[asyncio.Task] = []
        mock_upload = AsyncMock()

        with patch(
            "app.modules.transformers.blob_storage.BlobStorage",
            side_effect=RuntimeError("no storage config"),
        ), patch.object(
            mod, "upload_bytes_artifact", mock_upload,
        ), patch.object(
            mod, "register_task",
            lambda conv_id, task: captured_tasks.append(task),
        ):
            tool._schedule_artifact_upload(
                images=[b"img-bytes"],
                blob_store=None,
                graph_provider=state["graph_provider"],
                org_id="org-456",
                conversation_id="conv-123",
                user_id="user-789",
                model_name="gpt-image-1",
            )

            assert len(captured_tasks) == 1
            result = await captured_tasks[0]

        assert result is None
        mock_upload.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_upload_exception_is_logged_and_skipped(self):
        from app.agents.actions.image_generator import image_generator as mod

        state = _make_state()
        tool = mod.ImageGenerator(state)

        captured_tasks: list[asyncio.Task] = []
        call_count = {"n": 0}

        async def _upload_side_effect(**kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("transient upload error")
            return {
                "fileName": kwargs["file_name"],
                "signedUrl": "https://blob/ok",
                "documentId": "doc-ok",
            }

        with patch.object(
            mod, "upload_bytes_artifact", AsyncMock(side_effect=_upload_side_effect),
        ), patch.object(
            mod, "register_task",
            lambda conv_id, task: captured_tasks.append(task),
        ):
            tool._schedule_artifact_upload(
                images=[b"first", b"second"],
                blob_store=state["blob_store"],
                graph_provider=state["graph_provider"],
                org_id="org-456",
                conversation_id="conv-123",
                user_id="user-789",
                model_name="gpt-image-1",
            )

            assert len(captured_tasks) == 1
            result = await captured_tasks[0]

        assert result is not None
        assert len(result["artifacts"]) == 1
        assert result["artifacts"][0]["fileName"].startswith("gpt-image-1")

    @pytest.mark.asyncio
    async def test_all_uploads_return_none_yields_no_artifacts(self):
        """When every upload returns a falsy entry, the task should resolve to None."""
        from app.agents.actions.image_generator import image_generator as mod

        state = _make_state()
        tool = mod.ImageGenerator(state)

        captured_tasks: list[asyncio.Task] = []

        with patch.object(
            mod, "upload_bytes_artifact", AsyncMock(return_value=None),
        ), patch.object(
            mod, "register_task",
            lambda conv_id, task: captured_tasks.append(task),
        ):
            tool._schedule_artifact_upload(
                images=[b"a", b"b"],
                blob_store=state["blob_store"],
                graph_provider=state["graph_provider"],
                org_id="org-456",
                conversation_id="conv-123",
                user_id="user-789",
                model_name="gpt-image-1",
            )
            assert len(captured_tasks) == 1
            result = await captured_tasks[0]

        assert result is None


class TestBuildFileName:
    def test_sanitises_model_name(self):
        from app.agents.actions.image_generator.image_generator import (
            _build_file_name,
        )

        name = _build_file_name("gpt-image-1", 0)
        assert name.startswith("gpt-image-1_1_")
        assert name.endswith(".png")

    def test_strips_unsafe_chars(self):
        from app.agents.actions.image_generator.image_generator import (
            _build_file_name,
        )

        name = _build_file_name("my model/v2!", 2)
        # slashes / spaces / ! become "_"; leading/trailing "_" stripped
        assert name.startswith("my_model_v2_3_")

    def test_empty_model_name_falls_back(self):
        from app.agents.actions.image_generator.image_generator import (
            _build_file_name,
        )

        name = _build_file_name("", 0)
        assert name.startswith("image_1_")


def _load_is_internal_tool():
    """Load ``_is_internal_tool`` without importing the full tool_system chain.

    The production module pulls in etcd3, fitz, bs4, googleapiclient, etc. —
    none of which are needed for this pure-function test. We parse the source
    with ast, extract the function, and exec it in a minimal namespace so the
    test remains hermetic and never drifts from production.
    """
    import ast
    from pathlib import Path

    src_path = (
        Path(__file__).resolve().parents[4]
        / "app"
        / "modules"
        / "agents"
        / "qna"
        / "tool_system.py"
    )
    tree = ast.parse(src_path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_is_internal_tool":
            # Strip the string type annotation (``'Tool'``) so we can exec
            # without pulling in the langchain Tool class.
            for arg in node.args.args:
                arg.annotation = None
            node.returns = None
            module = ast.Module(body=[node], type_ignores=[])
            ast.fix_missing_locations(module)
            ns: dict = {}
            exec(compile(module, str(src_path), "exec"), ns)  # noqa: S102
            return ns["_is_internal_tool"]
    raise RuntimeError("Could not locate _is_internal_tool in tool_system.py")


class TestIsInternalToolAllowsImageGenerator:
    def test_image_generator_app_name_is_internal(self):
        is_internal = _load_is_internal_tool()
        registry_tool = SimpleNamespace(app_name="image_generator")
        assert is_internal(
            "image_generator.generate_image", registry_tool,
        ) is True

    def test_image_generator_pattern_is_internal(self):
        """Fallback pattern match should also flag image_generator tools."""
        is_internal = _load_is_internal_tool()
        registry_tool = SimpleNamespace()  # no app_name or metadata
        assert is_internal(
            "image_generator.generate_image", registry_tool,
        ) is True
