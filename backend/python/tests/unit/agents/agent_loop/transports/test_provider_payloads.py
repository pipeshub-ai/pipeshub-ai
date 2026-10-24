"""What every provider is actually sent, built with the real formatters and no network.

- TP-01: text the loop adds (summaries, nudges, relocated images) never reaches a provider as
  the person speaking; it is wrapped as a context note.
- TP-02: a tool result carrying an image formats on every transport.
- TP-04: a turn that failed before an answer never merges with the current question into one
  human turn (docs/context-engineering.md, CE-05, CE-06, CE-11).
- TP-05: the results of one assistant turn's parallel calls reach the provider the way it
  documents them (CE-20).
"""

from __future__ import annotations

import base64
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agent_loop_lib.core.messages import (
    AssistantMessage,
    ImagePart,
    ImageSource,
    Message,
    TextPart,
    ToolCall,
    ToolMessage,
    UserMessage,
)
from app.agent_loop_lib.transport.anthropic import AnthropicTransport
from app.agent_loop_lib.transport.gemini import GeminiTransport
from app.agent_loop_lib.transport.ollama import OllamaTransport
from app.agent_loop_lib.transport.openai import OpenAITransport
from app.agent_loop_lib.transport.openai_responses import format_responses_input
from app.agents.agent_loop.context import AgentContext
from app.agents.agent_loop.converters import convert_messages_to_langchain
from app.agents.agent_loop.factory import PipesHubAgentFactory

if TYPE_CHECKING:
    from collections.abc import Callable

NOTE_OPEN = '<context_note source="agent">'
NOTE_CLOSE = "</context_note>"
FOOTER = "\n\n[loop: step 3/20, stale_rounds=0]"
PNG = base64.b64encode(b"\x89PNG\r\n\x1a\nfake").decode()

HUMAN = "What is our PTO policy?"
SUMMARY = "Earlier work: searched Slack and Confluence for the PTO policy."
NUDGE = "Self-critique: the last turn had 1 failing tool call."

Turns = list[tuple[str, list[str]]]


def _anthropic_texts(content: Any) -> list[str]:  # noqa: ANN401
    if isinstance(content, str):
        return [content]
    texts: list[str] = []
    for block in content or []:
        if block.get("type") == "text":
            texts.append(block["text"])
        elif block.get("type") == "tool_result":
            texts.extend(_anthropic_texts(block.get("content")))
    return texts


def _openai_texts(content: Any) -> list[str]:  # noqa: ANN401
    if content is None:
        return []
    if isinstance(content, str):
        return [content]
    return [block.get("text", "") for block in content if block.get("type") in ("text", "input_text")]


def _langchain_anthropic(messages: list[Message]) -> Turns:
    from langchain_anthropic import ChatAnthropic

    payload = ChatAnthropic(model="claude-sonnet-4-5", api_key="x")._get_request_payload(
        convert_messages_to_langchain(messages),
    )
    return [(m["role"], _anthropic_texts(m["content"])) for m in payload["messages"]]


def _langchain_openai(messages: list[Message]) -> Turns:
    from langchain_openai import ChatOpenAI

    payload = ChatOpenAI(model="gpt-4.1", api_key="x")._get_request_payload(
        convert_messages_to_langchain(messages, strip_tool_images=True),
    )
    return [(m["role"], _openai_texts(m.get("content"))) for m in payload["messages"]]


def _direct_anthropic(messages: list[Message]) -> Turns:
    transport = AnthropicTransport(api_key="x", model="claude-sonnet-4-5")
    formatted = transport._format_messages(messages)
    return [(m["role"], _anthropic_texts(m["content"])) for m in formatted]


def _direct_chat_completions(messages: list[Message]) -> Turns:
    formatted = OpenAITransport(api_key="x")._format_messages(messages, None)
    return [(m["role"], _openai_texts(m.get("content"))) for m in formatted]


def _direct_responses(messages: list[Message]) -> Turns:
    turns: Turns = []
    for item in format_responses_input(messages, None):
        if item["type"] == "message":
            turns.append((item["role"], _openai_texts(item["content"])))
        elif item["type"] == "function_call":
            turns.append(("assistant", []))
        elif item["type"] == "function_call_output":
            turns.append(("tool", _openai_texts(item["output"])))
    return turns


def _direct_gemini(messages: list[Message]) -> Turns:
    turns: Turns = []
    for content in GeminiTransport(api_key="x")._format_contents(messages):
        texts = [p.text for p in content.parts if p.text]
        texts += [str(p.function_response.response["result"]) for p in content.parts if p.function_response]
        turns.append(("assistant" if content.role == "model" else "user", texts))
    return turns


def _direct_ollama(messages: list[Message]) -> Turns:
    formatted = OllamaTransport()._format_messages(messages, None)
    return [(m["role"], _openai_texts(m.get("content"))) for m in formatted]


PROVIDERS: dict[str, Callable[[list[Message]], Turns]] = {
    "langchain-anthropic": _langchain_anthropic,
    "langchain-openai": _langchain_openai,
    "direct-anthropic": _direct_anthropic,
    "direct-chat-completions": _direct_chat_completions,
    "direct-responses": _direct_responses,
    "direct-gemini": _direct_gemini,
    "direct-ollama": _direct_ollama,
}


def _texts_with(turns: Turns, needle: str) -> list[str]:
    return [text for _role, texts in turns for text in texts if needle in text]


def _merged_user_turns(turns: Turns) -> list[str]:
    """Each run of consecutive user-role entries as one text: Anthropic and Gemini merge them
    into one turn server-side, so that is what the model reads."""
    merged: list[str] = []
    previous = None
    for role, texts in turns:
        if role == "user":
            if previous == "user":
                merged[-1] += "\n" + "\n".join(texts)
            else:
                merged.append("\n".join(texts))
        previous = role
    return merged


def _image_tool_turn() -> list[Message]:
    return [
        UserMessage(content=HUMAN),
        AssistantMessage(content="", tool_calls=[ToolCall(id="call_b", name="charts__render", arguments={})]),
        ToolMessage(
            content=[
                TextPart(text="chart result"),
                ImagePart(source=ImageSource(type="base64", media_type="image/png", data=PNG)),
            ],
            tool_call_id="call_b",
            step_footer=FOOTER,
        ),
    ]


class TestLoopTextIsAContextNote:
    """TP-01."""

    @staticmethod
    def _messages() -> list[Message]:
        return [
            UserMessage(content=HUMAN),
            UserMessage(content=SUMMARY, injected=True),
            AssistantMessage(content="", tool_calls=[ToolCall(id="call_a", name="kg__search", arguments={"q": "pto"})]),
            ToolMessage(content="kg result", tool_call_id="call_a", step_footer=FOOTER),
            UserMessage(content=[TextPart(text=NUDGE)], injected=True),
        ]

    @pytest.mark.parametrize("provider", PROVIDERS)
    def test_injected_text_is_wrapped_and_the_human_text_is_not(self, provider: str) -> None:
        turns = PROVIDERS[provider](self._messages())

        for injected in (SUMMARY, NUDGE):
            sent = _texts_with(turns, injected)
            assert sent, f"{provider}: the injected text was not sent"
            for text in sent:
                assert NOTE_OPEN in text and NOTE_CLOSE in text, f"{provider}: {text!r}"
                assert text.index(NOTE_OPEN) < text.index(injected) < text.index(NOTE_CLOSE)
        for text in _texts_with(turns, HUMAN):
            assert NOTE_OPEN not in text, f"{provider}: the person's words were wrapped"

    def test_images_relocated_from_tool_results_come_as_a_context_note(self) -> None:
        converted = convert_messages_to_langchain(_image_tool_turn(), relocate_tool_images=True)

        relocated = converted[-1]
        texts = [block["text"] for block in relocated.content if block.get("type") == "text"]
        assert any(block.get("type") == "image_url" for block in relocated.content)
        assert NOTE_OPEN in texts[0] and NOTE_CLOSE in texts[-1]


class TestImageToolResultOnEveryTransport:
    """TP-02."""

    @pytest.mark.parametrize("provider", PROVIDERS)
    def test_formats_and_keeps_the_text_and_the_footer(self, provider: str) -> None:
        turns = PROVIDERS[provider](_image_tool_turn())

        assert _texts_with(turns, "chart result"), provider
        assert _texts_with(turns, "stale_rounds=0"), provider

    def test_responses_sends_the_image_inside_the_tool_output(self) -> None:
        items = format_responses_input(_image_tool_turn(), None)

        output = next(i for i in items if i["type"] == "function_call_output")["output"]
        assert isinstance(output, list)
        assert [b["type"] for b in output].count("input_image") == 1
        assert "chart result" in output[0]["text"]
        assert output[-1] == {"type": "input_text", "text": FOOTER}

    def test_gemini_sends_the_image_beside_the_function_response(self) -> None:
        transport = GeminiTransport(api_key="x")
        transport._thought_signatures["call_b"] = b"signature"
        contents = transport._format_contents(_image_tool_turn())

        tool_turn = contents[-1]
        response = next(p.function_response for p in tool_turn.parts if p.function_response)
        assert response.response["result"] == "chart result" + FOOTER
        assert any(p.inline_data is not None for p in tool_turn.parts)

    def test_gemini_keeps_the_image_when_the_call_is_replayed_as_text(self) -> None:
        contents = GeminiTransport(api_key="x")._format_contents(_image_tool_turn())

        tool_turn = contents[-1]
        assert any(p.text and "chart result" in p.text for p in tool_turn.parts)
        assert any(p.inline_data is not None for p in tool_turn.parts)


def _context() -> AgentContext:
    return AgentContext(org_id="org", user_id="user", user_email="user@example.com")


class _Agent:
    def __init__(self) -> None:
        self.context = None

    def seed_context(self, context: Any) -> None:  # noqa: ANN401
        self.context = context


FAILED_QUESTION = "List every open Jira ticket for the billing team"
CURRENT = "What did the audit find?"


async def _sent_after_failed_turn(previous: list[dict[str, Any]]) -> list[Message]:
    agent = _Agent()
    await PipesHubAgentFactory._seed_conversation_history(agent, previous, _context())
    return [*await agent.context.messages(), UserMessage(content=CURRENT)]


HISTORIES = {
    "error row dropped (old payload)": [
        {"role": "user_query", "content": "Summarise the Q3 board deck"},
        {"role": "bot_response", "content": "The Q3 deck covers revenue and hiring."},
        {"role": "user_query", "content": FAILED_QUESTION},
    ],
    "error row sent with a status": [
        {"role": "user_query", "content": "Summarise the Q3 board deck"},
        {"role": "bot_response", "content": "The Q3 deck covers revenue and hiring."},
        {"role": "user_query", "content": FAILED_QUESTION},
        {"role": "bot_response", "content": "", "status": "failed"},
    ],
}


class TestFailedTurnNeverMergesWithTheCurrentQuestion:
    """TP-04."""

    @pytest.mark.parametrize("history", HISTORIES)
    @pytest.mark.parametrize("provider", PROVIDERS)
    async def test_one_human_question_per_user_turn(self, provider: str, history: str) -> None:
        turns = PROVIDERS[provider](await _sent_after_failed_turn(HISTORIES[history]))

        for merged in _merged_user_turns(turns):
            assert not (FAILED_QUESTION in merged and CURRENT in merged), f"{provider}: {merged!r}"


PARALLEL_CALLS = [
    ToolCall(id="call_a", name="kg__search", arguments={"q": "pto"}),
    ToolCall(id="call_b", name="charts__render", arguments={}),
    ToolCall(id="call_c", name="jira__search", arguments={"q": "leave"}),
]


def _parallel_turn() -> list[Message]:
    return [
        UserMessage(content=HUMAN),
        AssistantMessage(content="", tool_calls=PARALLEL_CALLS),
        ToolMessage(content="kg result", tool_call_id="call_a", step_footer=FOOTER),
        ToolMessage(
            content=[
                TextPart(text="chart result"),
                ImagePart(source=ImageSource(type="base64", media_type="image/png", data=PNG)),
            ],
            tool_call_id="call_b",
            step_footer=FOOTER,
        ),
        ToolMessage(content="jira failed", tool_call_id="call_c", step_footer=FOOTER, is_error=True),
        UserMessage(content=NUDGE, injected=True),
    ]


class TestParallelToolResults:
    """TP-05."""

    @pytest.mark.parametrize("method", ["complete", "stream"])
    async def test_anthropic_sends_every_result_in_one_user_message(self, method: str) -> None:
        """Anthropic's parallel tool use guide: a separate user message per result is "Wrong" and
        "teaches" Claude to stop calling tools in parallel."""
        transport = AnthropicTransport(api_key="x", model="claude-sonnet-4-5")
        response = SimpleNamespace(
            content=[SimpleNamespace(type="text", text="ok")],
            stop_reason="end_turn",
            usage=SimpleNamespace(
                input_tokens=1, output_tokens=1, cache_read_input_tokens=0, cache_creation_input_tokens=0,
            ),
        )
        sent: dict[str, Any] = {}

        def _capture(**kwargs: Any) -> MagicMock:  # noqa: ANN401
            sent.update(kwargs)
            manager = MagicMock()
            stream = MagicMock()

            async def _no_text() -> Any:  # noqa: ANN401
                return
                yield

            stream.text_stream = _no_text()
            stream.get_final_message = AsyncMock(return_value=response)
            manager.__aenter__ = AsyncMock(return_value=stream)
            manager.__aexit__ = AsyncMock(return_value=False)
            return manager

        transport._client = MagicMock()
        transport._client.messages.create = AsyncMock(side_effect=lambda **kw: (sent.update(kw), response)[1])
        transport._client.messages.stream = MagicMock(side_effect=_capture)
        if method == "complete":
            await transport.complete(_parallel_turn())
        else:
            [event async for event in transport.stream(_parallel_turn())]

        messages = sent["messages"]
        carriers = [
            m for m in messages
            if isinstance(m["content"], list) and any(b.get("type") == "tool_result" for b in m["content"])
        ]
        assert len(carriers) == 1, [m["role"] for m in messages]
        results = carriers[0]["content"]
        assert [b["tool_use_id"] for b in results if b["type"] == "tool_result"] == ["call_a", "call_b", "call_c"]
        assert messages[messages.index(carriers[0]) - 1]["role"] == "assistant"
        chart = next(b for b in results if b.get("tool_use_id") == "call_b")
        assert [b["type"] for b in chart["content"]] == ["text", "image", "text"]
        assert chart["content"][-1]["text"] == FOOTER
        assert results[0]["content"] == "kg result" + FOOTER
        assert results[2].get("is_error") is True
        assert _texts_with([(m["role"], _anthropic_texts(m["content"])) for m in messages], NUDGE)

    def test_anthropic_matches_the_langchain_turns(self) -> None:
        direct = _direct_anthropic(_parallel_turn())
        langchain = _langchain_anthropic(_parallel_turn())

        assert [role for role, _ in direct][:3] == [role for role, _ in langchain][:3]
        assert direct[2][1][:3] == langchain[2][1][:3]

    def test_gemini_replays_unsigned_sibling_calls_as_function_calls(self) -> None:
        """Gemini signs only the first function_call of a parallel set (seen live on
        gemini-2.5-flash/pro, gemini-3-flash-preview and gemini-3.1-pro-preview), so the
        siblings must not be flattened to text with their results in the user's voice."""
        transport = GeminiTransport(api_key="x")
        transport._thought_signatures["call_a"] = b"signature"

        contents = transport._format_contents(_parallel_turn())

        model_turn = next(c for c in contents if c.role == "model")
        calls = [p for p in model_turn.parts if p.function_call]
        assert [p.function_call.name for p in calls] == ["kg__search", "charts__render", "jira__search"]
        assert [p.thought_signature for p in calls] == [b"signature", None, None]
        responses = [p.function_response for c in contents for p in c.parts if p.function_response]
        assert [r.name for r in responses] == ["kg__search", "charts__render", "jira__search"]
        assert all(str(r.response["result"]).endswith(FOOTER) for r in responses)
        chart_turn = next(c for c in contents if any(
            p.function_response and p.function_response.name == "charts__render" for p in c.parts
        ))
        assert any(p.inline_data is not None for p in chart_turn.parts)
        assert not [p.text for c in contents for p in c.parts if p.text and p.text.startswith("[previous tool")]

    def test_gemini_pairs_repeated_calls_to_one_function_by_their_ids(self) -> None:
        """One function called for several inputs: only Gemini's own ids tell the results apart."""
        transport = GeminiTransport(api_key="x")
        cities = {"g-paris": "Paris", "g-london": "London", "g-tokyo": "Tokyo"}
        for call_id in cities:
            transport._call_id(SimpleNamespace(id=call_id))
        transport._thought_signatures["g-paris"] = b"signature"
        messages = [
            UserMessage(content="Weather in Paris, London and Tokyo?"),
            AssistantMessage(content=None, tool_calls=[
                ToolCall(id=call_id, name="get_weather", arguments={"city": city}) for call_id, city in cities.items()
            ]),
            *[ToolMessage(content=f"{city}: {20 + i}C", tool_call_id=call_id)
              for i, (call_id, city) in enumerate(cities.items())],
        ]

        contents = transport._format_contents(messages)

        calls = [p.function_call for c in contents for p in c.parts if p.function_call]
        responses = [p.function_response for c in contents for p in c.parts if p.function_response]
        assert [(c.id, c.args["city"]) for c in calls] == list(cities.items())
        assert [(r.id, r.response["result"]) for r in responses] == [
            ("g-paris", "Paris: 20C"), ("g-london", "London: 21C"), ("g-tokyo", "Tokyo: 22C"),
        ]

    def test_gemini_never_sends_an_id_it_did_not_issue(self) -> None:
        transport = GeminiTransport(api_key="x")
        transport._thought_signatures["call_a"] = b"signature"

        contents = transport._format_contents(_parallel_turn())

        parts = [p.function_call or p.function_response for c in contents for p in c.parts]
        assert all(getattr(p, "id", None) is None for p in parts if p is not None)

    def test_gemini_still_flattens_a_turn_with_no_signature(self) -> None:
        contents = GeminiTransport(api_key="x")._format_contents(_parallel_turn())

        assert not [p for c in contents for p in c.parts if p.function_call or p.function_response]
