"""Integration tests for MiniMax provider in app.utils.aimodels.

These tests verify MiniMax provider integration with the ChatOpenAI class
using mocked HTTP responses. They do NOT require a real MiniMax API key.
Set MINIMAX_API_KEY to run against the real API.
"""

import os
from unittest.mock import MagicMock, patch

import pytest

from app.utils.aimodels import LLMProvider, get_generator_model


class TestMiniMaxIntegration:
    """Integration tests for MiniMax LLM provider."""

    def _minimax_config(self, model="MiniMax-M2.7", temperature=None):
        config = {
            "configuration": {
                "model": model,
                "apiKey": os.environ.get("MINIMAX_API_KEY", "test-minimax-key"),
            },
            "isDefault": True,
        }
        if temperature is not None:
            config["configuration"]["temperature"] = temperature
        return config

    @patch("langchain_openai.ChatOpenAI")
    def test_minimax_provider_creates_chatopenai(self, mock_cls):
        """MiniMax provider should create a ChatOpenAI instance with correct base_url."""
        mock_cls.return_value = MagicMock()
        config = self._minimax_config()
        result = get_generator_model(LLMProvider.MINIMAX.value, config)
        mock_cls.assert_called_once()
        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs["base_url"] == "https://api.minimax.io/v1"
        assert call_kwargs["model"] == "MiniMax-M2.7"

    @patch("langchain_openai.ChatOpenAI")
    def test_minimax_m27_highspeed_model(self, mock_cls):
        """MiniMax-M2.7-highspeed model should be accepted."""
        mock_cls.return_value = MagicMock()
        config = self._minimax_config("MiniMax-M2.7-highspeed")
        result = get_generator_model(LLMProvider.MINIMAX.value, config)
        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs["model"] == "MiniMax-M2.7-highspeed"

    @patch("langchain_openai.ChatOpenAI")
    def test_minimax_temperature_zero_clamped(self, mock_cls):
        """Temperature 0 should be clamped to 0.01 for MiniMax."""
        mock_cls.return_value = MagicMock()
        config = self._minimax_config(temperature=0.0)
        result = get_generator_model(LLMProvider.MINIMAX.value, config)
        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs["temperature"] == pytest.approx(0.01)

    @patch("langchain_openai.ChatOpenAI")
    def test_minimax_temperature_above_one_clamped(self, mock_cls):
        """Temperature > 1.0 should be clamped to 1.0 for MiniMax."""
        mock_cls.return_value = MagicMock()
        config = self._minimax_config(temperature=1.5)
        result = get_generator_model(LLMProvider.MINIMAX.value, config)
        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs["temperature"] == 1.0

    @patch("langchain_openai.ChatOpenAI")
    def test_minimax_default_temperature(self, mock_cls):
        """Default temperature should be 0.2 for MiniMax."""
        mock_cls.return_value = MagicMock()
        config = self._minimax_config()
        result = get_generator_model(LLMProvider.MINIMAX.value, config)
        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs["temperature"] == 0.2

    def test_minimax_enum_value(self):
        """MiniMax enum value should be 'minimax'."""
        assert LLMProvider.MINIMAX.value == "minimax"

    def test_minimax_in_provider_list(self):
        """MiniMax should be in the list of LLM providers."""
        provider_values = [p.value for p in LLMProvider]
        assert "minimax" in provider_values
