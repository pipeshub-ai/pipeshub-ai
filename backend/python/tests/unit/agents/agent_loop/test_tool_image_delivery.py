"""Retrieved images reach every provider exactly once per request (CE-17).

Providers that reject an image inside a tool result (OpenAI-family models on
Chat Completions, Ollama) used to get each retrieved image twice: once from
the `pending_tool_images` stash the PRE_MODEL fallback injected into a user
message, and once from the transport moving the tool result's images into a
trailing user message. The transport's move is now the only delivery path, and
the direct Chat Completions transport makes the same move instead of dropping
the images.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.agent_loop_lib.agent.hook_dispatch import dispatch_pre_model
from app.agent_loop_lib.context.base import ContextBudget
from app.agent_loop_lib.core.messages import (
    AssistantMessage,
    TextPart,
    ToolCall,
    ToolMessage,
    UserMessage,
)
from app.agent_loop_lib.transport.openai import OpenAITransport
from app.agents.actions.knowledge_graph.ops.results import tool_output
from app.agents.agent_loop.context import AgentContext
from app.agents.agent_loop.converters import convert_messages_to_langchain
from app.agents.agent_loop.factory import PipesHubAgentFactory

QUESTION = "What does the architecture diagram in the design doc show?"
_IMAGE_URL = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="


def _stored(context: AgentContext) -> list:
    """A search that returned one image, produced by the real tool output
    path for a model whose provider cannot take images in a tool result."""
    context.tool_state.update({"is_multimodal_llm": True, "supports_multipart_tool_result": False})
    output = tool_output("Top 1 blocks\n[ref1] the diagram", [{"image_url": {"url": _IMAGE_URL}}], context.tool_state)
    return [
        UserMessage(content=QUESTION),
        AssistantMessage(content="Searching.", tool_calls=[ToolCall(id="s1", name="knowledgehub__search", arguments={})]),
        ToolMessage(content=output, tool_call_id="s1"),
    ]


async def _sent(context: AgentContext, stored: list) -> list:
    hooks = PipesHubAgentFactory._build_hooks(context, model_name="m")
    budget = ContextBudget.for_model("m", context_window=200_000, reserved_prompt_tokens=20_000)
    return await dispatch_pre_model(hooks, list(stored), budget, turn_index=1, max_turns=15)


def _image_urls(blocks: Any) -> list[str]:  # noqa: ANN401
    if not isinstance(blocks, list):
        return []
    return [b["image_url"]["url"] for b in blocks if isinstance(b, dict) and b.get("type") == "image_url"]


@pytest.mark.asyncio
async def test_langchain_non_multipart_provider_gets_each_image_once() -> None:
    context = AgentContext(org_id="o", user_id="u", user_email="u@example.com")
    stored = _stored(context)
    sent = await _sent(context, stored)

    payload = convert_messages_to_langchain(sent, strip_tool_images=True, relocate_tool_images=True)

    assert "pending_tool_images" not in context.tool_state
    urls = [url for m in payload for url in _image_urls(m.content)]
    assert urls == [_IMAGE_URL]
    assert payload[-1].type == "human"
    assert _image_urls(payload[-1].content) == [_IMAGE_URL]


@pytest.mark.asyncio
async def test_direct_chat_completions_gets_each_image_once() -> None:
    context = AgentContext(org_id="o", user_id="u", user_email="u@example.com")
    sent = await _sent(context, _stored(context))

    formatted = OpenAITransport(api_key="k")._format_messages(sent, "system")

    with_images = [(m["role"], _image_urls(m["content"])) for m in formatted if _image_urls(m["content"])]
    assert with_images == [("user", [_IMAGE_URL])]
    assert formatted[-1]["role"] == "user"
    tool = next(m for m in formatted if m["role"] == "tool")
    assert not _image_urls(tool["content"])


@pytest.mark.asyncio
async def test_direct_chat_completions_without_tool_images_adds_nothing() -> None:
    formatted = OpenAITransport(api_key="k")._format_messages(
        [UserMessage(content=QUESTION), AssistantMessage(content=[TextPart(text="answer")])], None,
    )
    assert [m["role"] for m in formatted] == ["user", "assistant"]


def test_relocated_images_are_marked_as_added_by_the_loop() -> None:
    from app.agent_loop_lib.transport.tool_images import (
        RELOCATED_IMAGES_NOTE,
        relocated_tool_images,
    )

    context = AgentContext(org_id="o", user_id="u", user_email="u@example.com")
    relocated = relocated_tool_images(_stored(context))

    assert relocated is not None
    assert relocated.injected
    assert relocated.content[0] == TextPart(text=RELOCATED_IMAGES_NOTE)
