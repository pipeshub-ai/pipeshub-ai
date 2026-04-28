"""Unit tests for the image generation adapter in ``app.utils.aimodels``.

These tests cover the OpenAI + Gemini dispatch logic in
:func:`get_image_generation_model` and the ``generate`` method of the two
concrete adapters, mocking the provider SDK clients to avoid any network
calls.
"""

from __future__ import annotations

import base64
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.utils.aimodels import (
    ImageGenerationAdapter,
    ImageGenerationProvider,
    _normalize_openai_size,
    _size_to_aspect_ratio,
    get_image_generation_model,
)


# ---------------------------------------------------------------------------
# Size helpers
# ---------------------------------------------------------------------------


class TestSizeHelpers:
    def test_normalize_openai_size_passthrough(self):
        assert _normalize_openai_size("1024x1024") == "1024x1024"
        assert _normalize_openai_size("1024x1536") == "1024x1536"
        assert _normalize_openai_size("1536x1024") == "1536x1024"

    def test_normalize_openai_size_aliases_portrait(self):
        assert _normalize_openai_size("1024x1792") == "1024x1536"

    def test_normalize_openai_size_aliases_landscape(self):
        assert _normalize_openai_size("1792x1024") == "1536x1024"

    def test_normalize_openai_size_unknown_defaults_to_square(self):
        assert _normalize_openai_size("42x42") == "1024x1024"

    def test_size_to_aspect_ratio_known(self):
        assert _size_to_aspect_ratio("1024x1024") == "1:1"
        assert _size_to_aspect_ratio("1024x1792") == "9:16"
        assert _size_to_aspect_ratio("1792x1024") == "16:9"

    def test_size_to_aspect_ratio_unknown_defaults_to_square(self):
        assert _size_to_aspect_ratio("foo") == "1:1"


# ---------------------------------------------------------------------------
# get_image_generation_model
# ---------------------------------------------------------------------------


def _openai_config(model: str = "gpt-image-1") -> dict:
    return {
        "configuration": {"apiKey": "sk-test", "model": model},
        "isDefault": True,
    }


def _gemini_config(model: str = "gemini-2.5-flash-image") -> dict:
    return {
        "configuration": {"apiKey": "AIza-test", "model": model},
        "isDefault": True,
    }


class TestGetImageGenerationModel:
    def test_openai_dispatch(self):
        adapter = get_image_generation_model("openAI", _openai_config())
        assert adapter.provider == ImageGenerationProvider.OPENAI.value
        assert adapter.model == "gpt-image-1"

    def test_gemini_dispatch(self):
        adapter = get_image_generation_model("gemini", _gemini_config())
        assert adapter.provider == ImageGenerationProvider.GEMINI.value
        assert adapter.model == "gemini-2.5-flash-image"

    def test_unknown_provider_raises(self):
        with pytest.raises(ValueError, match="Unsupported image generation provider"):
            get_image_generation_model("stability", _openai_config())

    def test_empty_model_list_raises(self):
        config = {"configuration": {"apiKey": "sk", "model": "  , ,  "}, "isDefault": True}
        with pytest.raises(ValueError, match="No image-generation model configured"):
            get_image_generation_model("openAI", config)

    def test_explicit_model_name_must_be_in_list(self):
        cfg = {
            "configuration": {"apiKey": "sk", "model": "a,b"},
            "isDefault": False,
        }
        with pytest.raises(ValueError, match="Model name c not found"):
            get_image_generation_model("openAI", cfg, model_name="c")

    def test_multi_model_picks_first_by_default(self):
        cfg = {
            "configuration": {"apiKey": "sk", "model": "dall-e-3, gpt-image-1"},
            "isDefault": True,
        }
        adapter = get_image_generation_model("openAI", cfg)
        assert adapter.model == "dall-e-3"

    def test_returns_adapter_instance(self):
        adapter = get_image_generation_model("openAI", _openai_config())
        assert isinstance(adapter, ImageGenerationAdapter)


# ---------------------------------------------------------------------------
# OpenAI adapter.generate()
# ---------------------------------------------------------------------------


class TestOpenAIAdapterGenerate:
    @pytest.mark.asyncio
    async def test_generate_decodes_b64_for_gpt_image(self):
        adapter = get_image_generation_model("openAI", _openai_config())

        png_bytes = b"\x89PNG\r\n\x1a\nfake"
        b64 = base64.b64encode(png_bytes).decode("ascii")

        mock_response = SimpleNamespace(
            data=[SimpleNamespace(b64_json=b64), SimpleNamespace(b64_json=b64)]
        )
        mock_client = MagicMock()
        mock_client.images.generate = AsyncMock(return_value=mock_response)
        mock_client.close = AsyncMock()

        with patch("openai.AsyncOpenAI", return_value=mock_client) as mock_cls:
            images = await adapter.generate("a red cube", size="1024x1024", n=2)

        assert images == [png_bytes, png_bytes]
        mock_cls.assert_called_once()
        mock_client.images.generate.assert_awaited_once()
        call_kwargs = mock_client.images.generate.await_args.kwargs
        assert call_kwargs["model"] == "gpt-image-1"
        assert call_kwargs["prompt"] == "a red cube"
        assert call_kwargs["n"] == 2
        assert call_kwargs["size"] == "1024x1024"
        # gpt-image-* rejects response_format, so we must NOT pass it.
        assert "response_format" not in call_kwargs
        mock_client.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_generate_passes_response_format_for_dalle(self):
        adapter = get_image_generation_model(
            "openAI", _openai_config("dall-e-3"),
        )
        mock_response = SimpleNamespace(
            data=[SimpleNamespace(b64_json=base64.b64encode(b"x").decode())]
        )
        mock_client = MagicMock()
        mock_client.images.generate = AsyncMock(return_value=mock_response)
        mock_client.close = AsyncMock()

        with patch("openai.AsyncOpenAI", return_value=mock_client):
            await adapter.generate("y", size="1024x1024", n=1)

        call_kwargs = mock_client.images.generate.await_args.kwargs
        assert call_kwargs["response_format"] == "b64_json"

    @pytest.mark.asyncio
    async def test_generate_normalizes_portrait_size(self):
        adapter = get_image_generation_model("openAI", _openai_config())
        mock_response = SimpleNamespace(data=[])
        mock_client = MagicMock()
        mock_client.images.generate = AsyncMock(return_value=mock_response)
        mock_client.close = AsyncMock()

        with patch("openai.AsyncOpenAI", return_value=mock_client):
            await adapter.generate("x", size="1024x1792", n=1)

        assert (
            mock_client.images.generate.await_args.kwargs["size"] == "1024x1536"
        )

    @pytest.mark.asyncio
    async def test_generate_skips_items_without_b64(self):
        adapter = get_image_generation_model("openAI", _openai_config())
        mock_response = SimpleNamespace(
            data=[
                SimpleNamespace(b64_json=None),
                SimpleNamespace(b64_json=base64.b64encode(b"ok").decode()),
            ]
        )
        mock_client = MagicMock()
        mock_client.images.generate = AsyncMock(return_value=mock_response)
        mock_client.close = AsyncMock()

        with patch("openai.AsyncOpenAI", return_value=mock_client):
            images = await adapter.generate("x")

        assert images == [b"ok"]


# ---------------------------------------------------------------------------
# Gemini adapter.generate()
# ---------------------------------------------------------------------------


class TestGeminiAdapterGenerate:
    @pytest.mark.asyncio
    async def test_imagen_dispatch(self):
        adapter = get_image_generation_model(
            "gemini", _gemini_config("imagen-4.0-generate-001"),
        )
        gi = SimpleNamespace(image=SimpleNamespace(image_bytes=b"IMAGEN_BYTES"))
        response = SimpleNamespace(generated_images=[gi, gi])

        fake_client = MagicMock()
        fake_client.aio.models.generate_images = AsyncMock(return_value=response)

        fake_config_cls = MagicMock()

        with patch("google.genai.Client", return_value=fake_client) as mock_cls, \
             patch("google.genai.types.GenerateImagesConfig", fake_config_cls):
            images = await adapter.generate("a cat", size="1792x1024", n=2)

        assert images == [b"IMAGEN_BYTES", b"IMAGEN_BYTES"]
        mock_cls.assert_called_once_with(api_key="AIza-test")
        # Config carries number_of_images + mapped aspect_ratio
        fake_config_cls.assert_called_once_with(
            number_of_images=2,
            aspect_ratio="16:9",
        )
        kwargs = fake_client.aio.models.generate_images.await_args.kwargs
        assert kwargs["model"] == "imagen-4.0-generate-001"
        assert kwargs["prompt"] == "a cat"

    @pytest.mark.asyncio
    async def test_gemini_image_dispatch(self):
        adapter = get_image_generation_model(
            "gemini", _gemini_config("gemini-2.5-flash-image"),
        )

        def _resp() -> SimpleNamespace:
            inline = SimpleNamespace(data=b"GEMINI_PNG")
            part = SimpleNamespace(inline_data=inline)
            content = SimpleNamespace(parts=[part])
            candidate = SimpleNamespace(content=content)
            return SimpleNamespace(candidates=[candidate])

        fake_client = MagicMock()
        fake_client.aio.models.generate_content = AsyncMock(
            side_effect=[_resp(), _resp(), _resp()],
        )

        with patch("google.genai.Client", return_value=fake_client):
            images = await adapter.generate("a dog", n=3)

        assert images == [b"GEMINI_PNG", b"GEMINI_PNG", b"GEMINI_PNG"]
        assert fake_client.aio.models.generate_content.await_count == 3

    @pytest.mark.asyncio
    async def test_gemini_image_filters_text_parts(self):
        adapter = get_image_generation_model(
            "gemini", _gemini_config("gemini-2.5-flash-image"),
        )
        # one text part (inline_data=None) and one image part
        text_part = SimpleNamespace(inline_data=None)
        image_part = SimpleNamespace(inline_data=SimpleNamespace(data=b"PNG"))
        candidate = SimpleNamespace(
            content=SimpleNamespace(parts=[text_part, image_part])
        )
        response = SimpleNamespace(candidates=[candidate])

        fake_client = MagicMock()
        fake_client.aio.models.generate_content = AsyncMock(return_value=response)

        with patch("google.genai.Client", return_value=fake_client):
            images = await adapter.generate("mixed", n=1)

        assert images == [b"PNG"]

    @pytest.mark.asyncio
    async def test_gemini_image_text_only_response_raises_with_diagnostics(self):
        """When the model returns text but no image (common when the model
        name is invalid or the prompt is refused), the adapter must raise a
        RuntimeError surfacing finish_reason and the refusal text so the
        caller can show a useful message instead of "no images"."""
        adapter = get_image_generation_model(
            "gemini", _gemini_config("gemini-2.5-flash-image"),
        )
        text_part = SimpleNamespace(
            inline_data=None,
            text="I cannot generate images for this request.",
        )
        candidate = SimpleNamespace(
            content=SimpleNamespace(parts=[text_part]),
            finish_reason=SimpleNamespace(name="IMAGE_SAFETY"),
            safety_ratings=[],
        )
        response = SimpleNamespace(
            candidates=[candidate],
            prompt_feedback=None,
        )

        fake_client = MagicMock()
        fake_client.aio.models.generate_content = AsyncMock(return_value=response)

        with patch("google.genai.Client", return_value=fake_client):
            with pytest.raises(RuntimeError) as excinfo:
                await adapter.generate("blocked", n=1)

        msg = str(excinfo.value)
        assert "gemini-2.5-flash-image" in msg
        assert "IMAGE_SAFETY" in msg
        assert "cannot generate images" in msg

    @pytest.mark.asyncio
    async def test_gemini_image_prompt_block_reason_surfaced(self):
        adapter = get_image_generation_model(
            "gemini", _gemini_config("gemini-2.5-flash-image"),
        )
        response = SimpleNamespace(
            candidates=[],
            prompt_feedback=SimpleNamespace(
                block_reason=SimpleNamespace(name="SAFETY"),
                block_reason_message="Prompt contains disallowed content.",
            ),
        )

        fake_client = MagicMock()
        fake_client.aio.models.generate_content = AsyncMock(return_value=response)

        with patch("google.genai.Client", return_value=fake_client):
            with pytest.raises(RuntimeError) as excinfo:
                await adapter.generate("blocked", n=1)

        msg = str(excinfo.value)
        assert "SAFETY" in msg
        assert "disallowed content" in msg

    @pytest.mark.asyncio
    async def test_gemini_image_empty_response_raises_generic_hint(self):
        adapter = get_image_generation_model(
            "gemini", _gemini_config("gemini-2.5-flash-image"),
        )
        response = SimpleNamespace(candidates=[], prompt_feedback=None)

        fake_client = MagicMock()
        fake_client.aio.models.generate_content = AsyncMock(return_value=response)

        with patch("google.genai.Client", return_value=fake_client):
            with pytest.raises(RuntimeError) as excinfo:
                await adapter.generate("x", n=1)

        # Falls back to the hint pointing users at supported models.
        msg = str(excinfo.value)
        assert "gemini-2.5-flash-image" in msg or "imagen-" in msg

    @pytest.mark.asyncio
    async def test_imagen_empty_response_raises(self):
        adapter = get_image_generation_model(
            "gemini", _gemini_config("imagen-4.0-generate-001"),
        )
        # Imagen returns no images with a rai_filtered_reason when blocked.
        gi = SimpleNamespace(
            image=None,
            rai_filtered_reason="Content blocked by safety filters.",
        )
        response = SimpleNamespace(generated_images=[gi])

        fake_client = MagicMock()
        fake_client.aio.models.generate_images = AsyncMock(return_value=response)

        with patch("google.genai.Client", return_value=fake_client), \
             patch("google.genai.types.GenerateImagesConfig", MagicMock()):
            with pytest.raises(RuntimeError) as excinfo:
                await adapter.generate("blocked", n=1)

        assert "Content blocked" in str(excinfo.value)
