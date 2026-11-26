"""FIX-P3-B1 (fixed by IMGRESULT): an image in a tool result reaches the model on every provider wire.

Claude Code read a ``.jpg`` in a task (its Read tool returns the image as an ``image`` block in a
``tool_result``). The model gateway parsed it into a ``ToolMessage`` whose content is a list of parts, and the
Responses API formatter concatenated a tool result's content as a string, so it raised ``TypeError`` before any
request left. ``classify_provider_failure`` then called that local error ``provider_unavailable`` (a retryable 502):
Claude Code retried the same history ten times and the run failed.

Each wire is driven through its real SDK (or raw HTTP) path over ``httpx.MockTransport``, so what is asserted is the
body the provider would have received.
"""

from __future__ import annotations

import base64
import json
from typing import TYPE_CHECKING, Any

import httpx
import pytest

from harness_core.gateway import messages_wire
from harness_core.gateway.model_gateway import classify_provider_failure
from harness_core.kernel.core.exceptions import TransportError
from harness_core.kernel.core.messages import (
    AssistantMessage,
    ImagePart,
    ImageSource,
    TextPart,
    ToolCall,
    ToolMessage,
    UserMessage,
)
from harness_core.models.transports.openai import RequestDefaults
from harness_core.models.transports.openai_responses import format_responses_input
from harness_core.models.transports.tool_results import (
    RELOCATED_IMAGES_PREFIX,
    relocated_images,
)
from tests.unit.harness_core.models.transport_wire import (
    anthropic_over,
    azure_over,
    bedrock_over,
    gemini_over,
    ollama_over,
    openai_over,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from harness_core.kernel.core.messages import Message
    from harness_core.models.transports.base import LLMTransport

JPEG = base64.b64encode(bytes.fromhex("ffd8ffe000104a46494600010100000100010000ffd9")).decode()
DATA_URL = f"data:image/jpeg;base64,{JPEG}"
READ_AN_IMAGE = {
    "model": "engine-model", "max_tokens": 100, "stream": True,
    "messages": [
        {"role": "user", "content": "What is in documents/a.jpg?"},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": "documents/a.jpg"}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": JPEG}},
        ]}]},
    ],
}


def _messages() -> list[Message]:
    return list(messages_wire.parse_messages_request(READ_AN_IMAGE, {"anthropic-version": "2023-06-01"}).messages)


def _image(data: str = JPEG, kind: str = "base64") -> ImagePart:
    return ImagePart(source=ImageSource(type=kind, media_type="image/jpeg" if kind == "base64" else None, data=data))


async def _sent(build: Callable[[Callable[[httpx.Request], httpx.Response]], LLMTransport], messages: list[Message]) -> dict[str, Any]:
    """The body the transport put on the wire; the provider then refuses, so the call fails as a provider's."""
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(400, json={"error": {"message": "seen", "type": "invalid_request_error", "code": None}})

    with pytest.raises(TransportError):
        _ = await build(handler).complete(messages, system="system")
    return bodies[0]


def _responses(handler: Callable[[httpx.Request], httpx.Response]) -> LLMTransport:
    transport = openai_over(handler)
    transport._defaults = RequestDefaults(use_responses_api=True)  # pyright: ignore[reportPrivateUsage]
    return transport


def test_fixp3_b1_the_gateway_accepts_an_image_tool_result_from_claude_code() -> None:
    assert isinstance(_messages()[-1].content, list)


def test_fixp3_b1_an_image_tool_result_reaches_the_responses_input() -> None:
    items = format_responses_input(_messages(), "system", keep_state=False)
    assert items[-1] == {
        "type": "function_call_output",
        "call_id": "t1",
        "output": [{"type": "input_image", "image_url": DATA_URL}],
    }


def test_fixp3_b1_a_failure_before_the_provider_is_called_is_not_provider_unavailable() -> None:
    failure = classify_provider_failure(TypeError('can only concatenate list (not "str") to list'))
    assert (failure.status, failure.code) == (400, "request_untranslatable")


@pytest.mark.parametrize("error", [TypeError("x"), AttributeError("x"), KeyError("x"), ValueError("x"), RuntimeError("x")])
def test_imgresult_an_error_raised_by_our_own_code_is_never_retried(error: Exception) -> None:
    assert (classify_provider_failure(error).status, classify_provider_failure(error).code) == (400, "request_untranslatable")


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (TransportError("Error code: 503", status_code=503), 502, "provider_unavailable"),
        (TransportError("no status"), 502, "provider_unavailable"),
        (ConnectionError("reset"), 502, "provider_unavailable"),
        (TimeoutError(), 502, "provider_unavailable"),
        (httpx.ReadTimeout("slow"), 502, "provider_unavailable"),
    ],
)
def test_imgresult_a_provider_or_network_failure_is_classified_as_before(error: Exception, status: int, code: str) -> None:
    assert (classify_provider_failure(error).status, classify_provider_failure(error).code) == (status, code)


async def test_imgresult_responses_wire_keeps_the_image_in_the_function_output_in_order() -> None:
    messages: list[Message] = [
        UserMessage(content="look"),
        AssistantMessage(tool_calls=[ToolCall(id="t1", name="Read", arguments={})]),
        ToolMessage(content=[TextPart(text="a.jpg:"), _image(), _image("https://x.test/b.png", "url")],
                    tool_call_id="t1", step_footer=" [step 2]"),
    ]
    sent = await _sent(_responses, messages)
    assert sent["input"][-1] == {
        "type": "function_call_output",
        "call_id": "t1",
        "output": [
            {"type": "input_text", "text": "a.jpg:"},
            {"type": "input_image", "image_url": DATA_URL},
            {"type": "input_image", "image_url": "https://x.test/b.png"},
            {"type": "input_text", "text": " [step 2]"},
        ],
    }


async def test_imgresult_a_text_only_result_is_still_a_string_on_the_responses_wire() -> None:
    messages: list[Message] = [
        UserMessage(content="look"),
        AssistantMessage(tool_calls=[ToolCall(id="t1", name="Read", arguments={})]),
        ToolMessage(content=[TextPart(text="a"), TextPart(text="b")], tool_call_id="t1", step_footer="!"),
    ]
    sent = await _sent(_responses, messages)
    assert sent["input"][-1]["output"] == "ab!"


@pytest.mark.parametrize("build", [openai_over, azure_over], ids=["openai_chat", "azure_chat"])
async def test_imgresult_chat_completions_sends_the_image_in_a_user_message_after_the_results(
    build: Callable[[Callable[[httpx.Request], httpx.Response]], LLMTransport],
) -> None:
    sent = await _sent(build, _messages())
    assert sent["messages"][-2:] == [
        {"role": "tool", "tool_call_id": "t1", "content": ""},
        # The loop adds this message, so it goes out as a context note (main's TP-01, `relocated_message`).
        {"role": "user", "content": [
            {"type": "text", "text": '<context_note source="agent">\nImages returned by the tool results above (t1):'},
            {"type": "image_url", "image_url": {"url": DATA_URL}},
            {"type": "text", "text": "</context_note>"},
        ]},
    ]


async def test_imgresult_anthropic_keeps_the_image_in_the_tool_result() -> None:
    sent = await _sent(anthropic_over, _messages())
    block = sent["messages"][-1]["content"][0]
    assert block["type"] == "tool_result"
    assert block["content"] == [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": JPEG}}]


async def test_imgresult_bedrock_keeps_the_image_in_the_tool_result() -> None:
    sent = await _sent(bedrock_over, _messages())
    result = sent["messages"][-1]["content"][0]["toolResult"]
    assert result["content"] == [{"image": {"format": "jpeg", "source": {"bytes": JPEG}}}]


async def test_imgresult_gemini_answers_the_call_and_sends_the_image_after_it() -> None:
    sent = await _sent(gemini_over, _messages())
    result, images = sent["contents"][-2:]
    # The gateway's history has no thought signature, so the call and its result replay as text.
    assert result["parts"] == [{"text": "[previous tool result] Read: "}]
    assert images["role"] == "user"
    assert images["parts"][0] == {"text": '<context_note source="agent">\nImages returned by the tool results above (t1):'}
    assert images["parts"][-1] == {"text": "</context_note>"}
    inline = images["parts"][1]["inlineData"]
    assert base64.urlsafe_b64decode(inline["data"]) == base64.b64decode(JPEG) and inline["mime_type"] == "image/jpeg"


async def test_imgresult_ollama_sends_the_image_after_the_results_and_names_a_url_it_cannot_fetch() -> None:
    messages = [*_messages()[:-1], ToolMessage(content=[TextPart(text="two"), _image(), _image("https://x.test/b.png", "url")],
                                                tool_call_id="t1")]
    sent = await _sent(ollama_over, messages)
    assert sent["messages"][-2:] == [
        {"role": "tool", "content": "two"},
        {"role": "user",
         "content": '<context_note source="agent">\nImages returned by the tool results above (t1): [image: https://x.test/b.png]'
                    "\n</context_note>",
         "images": [JPEG]},
    ]


async def test_imgresult_ollama_carries_a_users_own_image() -> None:
    sent = await _sent(ollama_over, [UserMessage(content=[TextPart(text="what is this?"), _image()])])
    assert sent["messages"][-1] == {"role": "user", "content": "what is this?", "images": [JPEG]}


def test_imgresult_one_user_message_follows_each_run_of_results_and_history_stays_append_only() -> None:
    other = base64.b64encode(b"other").decode()
    messages: list[Message] = [
        UserMessage(content="go"),
        AssistantMessage(tool_calls=[ToolCall(id="a", name="Read", arguments={}), ToolCall(id="b", name="Read", arguments={})]),
        ToolMessage(content=[_image()], tool_call_id="a"),
        ToolMessage(content=[_image(other)], tool_call_id="b"),
        AssistantMessage(tool_calls=[ToolCall(id="c", name="Bash", arguments={})]),
        ToolMessage(content="no image", tool_call_id="c"),
    ]
    plan = relocated_images(messages)
    assert list(plan) == [3]
    assert plan[3].call_ids == ("a", "b") and [i.source.data for i in plan[3].images] == [JPEG, other]
    assert relocated_images([*messages, UserMessage(content="next")]) == plan


def test_imgresult_an_image_a_user_message_already_carries_is_not_sent_twice() -> None:
    messages: list[Message] = [
        UserMessage(content=[TextPart(text="go"), _image()]),
        AssistantMessage(tool_calls=[ToolCall(id="a", name="Read", arguments={})]),
        ToolMessage(content=[TextPart(text="read"), _image()], tool_call_id="a"),
    ]
    assert relocated_images(messages) == {}


def test_imgresult_the_langchain_path_relocates_under_the_same_words() -> None:
    from app.agents.agent_loop import converters

    assert converters._RELOCATED_IMAGES_PREFIX == RELOCATED_IMAGES_PREFIX  # pyright: ignore[reportPrivateUsage]
