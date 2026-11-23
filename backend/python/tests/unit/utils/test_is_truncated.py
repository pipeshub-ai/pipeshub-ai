"""Detecting an answer cut off at the output-token cap, in every provider's response shape."""

from __future__ import annotations

from enum import Enum
from types import SimpleNamespace
from typing import Any

import pytest

from app.utils.is_truncated import is_truncated


class _FinishReason(Enum):
    MAX_TOKENS = 2
    STOP = 1


def _message(**metadata: Any) -> SimpleNamespace:  # noqa: ANN401
    return SimpleNamespace(content="partial", response_metadata=metadata)


# provider -> (a cut-off answer's response_metadata, a normally finished one's). Shapes as the LangChain wrappers return them.
SHAPES: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {
    "openAI": ({"finish_reason": "length"}, {"finish_reason": "stop"}),
    "openAI-responses": (
        {"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}},
        {"status": "completed", "incomplete_details": None},
    ),
    "azureOpenAI": ({"finish_reason": "length", "model_name": "gpt"}, {"finish_reason": "stop"}),
    "groq": ({"finish_reason": "length"}, {"finish_reason": "stop"}),
    "mistral": ({"finish_reason": "length"}, {"finish_reason": "stop"}),
    "xai": ({"finish_reason": "length"}, {"finish_reason": "stop"}),
    "anthropic": ({"stop_reason": "max_tokens"}, {"stop_reason": "end_turn"}),
    "gemini": ({"finish_reason": "MAX_TOKENS"}, {"finish_reason": "STOP"}),
    "vertexAI": ({"finish_reason": _FinishReason.MAX_TOKENS}, {"finish_reason": _FinishReason.STOP}),
    "vertexAI-repr": ({"finish_reason": "FinishReason.MAX_TOKENS"}, {"finish_reason": "FinishReason.STOP"}),
    "bedrock": ({"stopReason": "max_tokens"}, {"stopReason": "end_turn"}),
    "ollama": ({"done_reason": "length"}, {"done_reason": "stop"}),
    "cohere": ({"finish_reason": "MAX_TOKENS"}, {"finish_reason": "COMPLETE"}),
}


@pytest.mark.parametrize("provider", sorted(SHAPES))
def test_every_provider_shape(provider: str) -> None:
    cut, finished = SHAPES[provider]
    assert is_truncated(_message(**cut)) is True, f"{provider}: a cut-off answer was not detected"
    assert is_truncated(_message(**finished)) is False, f"{provider}: a finished answer was taken for a cut-off one"


@pytest.mark.parametrize("message", [None, "text", SimpleNamespace(), SimpleNamespace(response_metadata=None),
                                     SimpleNamespace(response_metadata="x"), _message(), _message(finish_reason=None)])
def test_a_message_with_no_reason_is_not_truncated(message: object) -> None:
    assert is_truncated(message) is False


def test_a_content_filter_stop_is_not_a_truncation() -> None:
    """A filtered answer is a different stop: sending it again with more room would not change it."""
    assert is_truncated(_message(finish_reason="content_filter")) is False
    assert is_truncated(_message(status="incomplete", incomplete_details={"reason": "content_filter"})) is False
