"""Integration tests for the OrcaRouter provider in app.utils.aimodels.

All network calls are mocked — no real API key is required.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.config.constants.ai_models import ORCAROUTER_BASE_URL
from app.utils.aimodels import LLMProvider, get_generator_model

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _config(model: str, api_key: str = "sk-orca-test") -> dict:
    return {
        "configuration": {"model": model, "apiKey": api_key},
        "isDefault": True,
    }


# ---------------------------------------------------------------------------
# LLM dispatch
# ---------------------------------------------------------------------------

class TestOrcaRouterLLM:
    @patch("langchain_openai.ChatOpenAI")
    def test_dispatch_creates_chatopenai_with_orcarouter_base_url(self, mock_cls) -> None:
        mock_cls.return_value = MagicMock()
        get_generator_model(LLMProvider.ORCAROUTER.value, _config("orcarouter/auto"))
        mock_cls.assert_called_once()
        kwargs = mock_cls.call_args.kwargs
        assert kwargs["base_url"] == ORCAROUTER_BASE_URL
        assert kwargs["model"] == "orcarouter/auto"
        assert kwargs["api_key"] == "sk-orca-test"

    @patch("langchain_openai.ChatOpenAI")
    def test_slashed_model_preserved(self, mock_cls) -> None:
        """OrcaRouter model ids may contain further slashes, e.g. deepseek/deepseek-v4-pro."""
        mock_cls.return_value = MagicMock()
        get_generator_model(LLMProvider.ORCAROUTER.value, _config("deepseek/deepseek-v4-pro"))
        assert mock_cls.call_args.kwargs["model"] == "deepseek/deepseek-v4-pro"

    @patch("langchain_openai.ChatOpenAI")
    def test_normal_model_uses_default_temperature(self, mock_cls) -> None:
        mock_cls.return_value = MagicMock()
        get_generator_model(LLMProvider.ORCAROUTER.value, _config("orcarouter/auto"))
        assert mock_cls.call_args.kwargs["temperature"] == pytest.approx(0.2)

    @patch("langchain_openai.ChatOpenAI")
    def test_stream_usage_enabled(self, mock_cls) -> None:
        mock_cls.return_value = MagicMock()
        get_generator_model(LLMProvider.ORCAROUTER.value, _config("orcarouter/auto"))
        assert mock_cls.call_args.kwargs["stream_usage"] is True
