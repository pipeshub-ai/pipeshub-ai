"""P4.3 PS-1..PS-4: provider state captured, journaled, restored, and sent back on its own wire only."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from harness_core.failpoints import Raise
from harness_core.kernel.core.messages import (
    AssistantMessage,
    ProviderState,
    TextPart,
    ToolCall,
    ToolMessage,
    UserMessage,
)
from harness_core.kernel.durable.adapters import TransportModelPort
from harness_core.models.provider_state import ANTHROPIC_MESSAGES, OPENAI_RESPONSES
from harness_core.models.transport_model import TransportModel
from harness_core.models.transports.anthropic import AnthropicTransport
from harness_core.models.transports.openai import OpenAITransport, RequestDefaults
from harness_core.models.transports.openai_responses import RawEvent
from harness_core.testing import armed
from tests.unit.harness_core.durable.support import Crash, World

THINKING = {"type": "thinking", "thinking": "check the tracker first", "signature": "sig-abc=="}
REDACTED = {"type": "redacted_thinking", "data": "ENCRYPTED=="}


def _ns(**fields: Any) -> SimpleNamespace:  # noqa: ANN401
    return SimpleNamespace(**fields)


def _anthropic_response(*blocks: dict[str, Any], stop: str = "tool_use") -> SimpleNamespace:
    return _ns(
        content=[_ns(**b) for b in blocks],
        stop_reason=stop,
        usage=_ns(input_tokens=10, output_tokens=5, cache_read_input_tokens=0, cache_creation_input_tokens=0),
    )


TOOL_USE = {"type": "tool_use", "id": "toolu_01", "name": "lookup", "input": {"q": "bug"}}


class _Stream:
    def __init__(self, chunks: list[str], final: SimpleNamespace) -> None:
        self._chunks, self._final = chunks, final

    async def __aenter__(self) -> _Stream:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    @property
    def text_stream(self) -> Any:  # noqa: ANN401
        async def gen() -> Any:  # noqa: ANN401
            for chunk in self._chunks:
                yield chunk

        return gen()

    async def get_final_message(self) -> SimpleNamespace:
        return self._final


class FakeAnthropic:
    """Answers each request with the next scripted response and records what it was sent."""

    def __init__(self, *responses: SimpleNamespace) -> None:
        self.responses = list(responses)
        self.requests: list[dict[str, Any]] = []
        self.messages = _ns(create=self._create, stream=self._stream)

    async def _create(self, **kwargs: Any) -> SimpleNamespace:  # noqa: ANN401
        self.requests.append(kwargs)
        return self.responses.pop(0)

    def _stream(self, **kwargs: Any) -> _Stream:  # noqa: ANN401
        self.requests.append(kwargs)
        final = self.responses.pop(0)
        return _Stream([b.text for b in final.content if b.type == "text"], final)


def _anthropic(client: FakeAnthropic, *, keep: bool) -> AnthropicTransport:
    transport = AnthropicTransport(api_key="sk-test", model="claude-x", client=client)
    if keep:
        assert transport.keep_provider_state()
    return transport


async def test_ps1_anthropic_complete_captures_thinking_and_redacted_blocks_in_order() -> None:
    client = FakeAnthropic(_anthropic_response(THINKING, REDACTED, {"type": "text", "text": "looking"}, TOOL_USE))
    response = await _anthropic(client, keep=True).complete([UserMessage(content="find it")])
    state = response.message.provider_state
    assert state == ProviderState(wire=ANTHROPIC_MESSAGES, blocks=[THINKING, REDACTED])
    assert response.message.text == "looking" and response.message.tool_calls


async def test_ps1_anthropic_stream_captures_state_on_the_assembled_message() -> None:
    client = FakeAnthropic(_anthropic_response(THINKING, {"type": "text", "text": "hi"}, stop="end_turn"))
    events = [e async for e in _anthropic(client, keep=True).stream([UserMessage(content="x")])]
    final = events[-1].response  # type: ignore[union-attr]
    assert final.message.provider_state == ProviderState(wire=ANTHROPIC_MESSAGES, blocks=[THINKING])


async def test_ps1_without_keeping_state_nothing_is_captured() -> None:
    client = FakeAnthropic(_anthropic_response(THINKING, TOOL_USE))
    response = await _anthropic(client, keep=False).complete([UserMessage(content="x")])
    assert response.message.provider_state is None


def _history(state: ProviderState | None) -> list[Any]:
    return [
        UserMessage(content="find it"),
        AssistantMessage(
            content=[TextPart(text="looking")],
            tool_calls=[ToolCall(id="toolu_01", name="lookup", arguments={"q": "bug"})],
            provider_state=state,
        ),
        ToolMessage(content="PROJ-1", tool_call_id="toolu_01"),
    ]


async def test_ps2_anthropic_sends_its_own_state_first_and_verbatim() -> None:
    client = FakeAnthropic(_anthropic_response({"type": "text", "text": "done"}, stop="end_turn"))
    await _anthropic(client, keep=True).complete(_history(ProviderState(wire=ANTHROPIC_MESSAGES, blocks=[THINKING, REDACTED])))
    assistant = client.requests[0]["messages"][1]
    assert assistant["content"][:2] == [THINKING, REDACTED]
    assert [b["type"] for b in assistant["content"][2:]] == ["text", "tool_use"]


async def test_ps2_another_wires_state_never_reaches_anthropic() -> None:
    client = FakeAnthropic(_anthropic_response({"type": "text", "text": "done"}, stop="end_turn"))
    foreign = ProviderState(wire=OPENAI_RESPONSES, blocks=[{"type": "reasoning", "id": "rs_1", "summary": []}])
    await _anthropic(client, keep=True).complete(_history(foreign))
    assert [b["type"] for b in client.requests[0]["messages"][1]["content"]] == ["text", "tool_use"]


REASONING = {"type": "reasoning", "id": "rs_1", "encrypted_content": "gAAAA==", "summary": [{"type": "summary_text", "text": "s"}]}


def _openai(seen: list[dict[str, Any]], response: Any) -> OpenAITransport:  # noqa: ANN401
    transport = OpenAITransport(
        api_key="sk-test", model="gpt-x", defaults=RequestDefaults(model="gpt-x", use_responses_api=True)
    )

    async def create(**kwargs: Any) -> Any:  # noqa: ANN401
        seen.append(kwargs)
        return response

    transport._client = _ns(responses=_ns(create=create))  # pyright: ignore[reportAttributeAccessIssue]
    assert transport.keep_provider_state()
    return transport


async def test_ps3_responses_requests_encrypted_reasoning_and_captures_it() -> None:
    seen: list[dict[str, Any]] = []
    # As the SSE path hands it over: a decoded payload, not the SDK's model.
    response = RawEvent(
        {
            "output": [
                {**REASONING, "status": "completed"},
                {"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": "lookup", "arguments": '{"q":"bug"}'},
            ],
            "usage": {"input_tokens": 10, "output_tokens": 3, "input_tokens_details": {"cached_tokens": 0}},
            "status": "completed",
        }
    )
    result = await _openai(seen, response).complete([UserMessage(content="find it")])
    assert seen[0]["include"] == ["reasoning.encrypted_content"] and seen[0]["store"] is False
    assert result.message.provider_state == ProviderState(wire=OPENAI_RESPONSES, blocks=[REASONING])


async def test_ps3_responses_replays_reasoning_before_the_turns_items_own_wire_only() -> None:
    seen: list[dict[str, Any]] = []
    done = RawEvent({"output": [], "usage": {}, "status": "completed"})
    transport = _openai(seen, done)
    await transport.complete(_history(ProviderState(wire=OPENAI_RESPONSES, blocks=[REASONING])))
    types = [item.get("type") for item in seen[0]["input"]]
    assert types == ["message", "reasoning", "message", "function_call", "function_call_output"]
    assert seen[0]["input"][1] == REASONING
    await transport.complete(_history(ProviderState(wire=ANTHROPIC_MESSAGES, blocks=[THINKING])))
    assert "reasoning" not in [item.get("type") for item in seen[1]["input"]]


async def test_ps4_state_is_journaled_and_a_resume_sends_it_back() -> None:
    world = World()
    world.admit()
    client = FakeAnthropic(
        _anthropic_response(THINKING, {"type": "text", "text": "looking"}, TOOL_USE),
        _anthropic_response({"type": "text", "text": "PROJ-1 it is"}, stop="end_turn"),
    )
    transport = _anthropic(client, keep=True)

    def port() -> TransportModelPort:
        return TransportModelPort(TransportModel(transport), provider="anthropic", transport="direct")

    with armed({"worker.model.after_response_commit": Raise(Crash)}):
        await world.crash(world.kernel(port()))
    (response,) = world.events("model.response")
    raw = await world.blob(response.data["rawRef"])  # type: ignore[arg-type]
    assert AssistantMessage.model_validate_json(raw).provider_state == ProviderState(
        wire=ANTHROPIC_MESSAGES, blocks=[THINKING]
    )
    done = await world.run(world.kernel(port()))
    assert done.status == "completed"
    resumed = client.requests[-1]["messages"]
    assistant = next(m for m in resumed if m["role"] == "assistant")
    assert assistant["content"][0] == THINKING, "the signature goes back on the request answering the call"
