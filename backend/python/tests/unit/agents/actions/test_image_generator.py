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
