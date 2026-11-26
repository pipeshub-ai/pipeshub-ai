"""C13.04: open models are served as a pinned row says (vLLM version, chat template, parsers), and each row passes
conformance on the OpenAI-compatible transport.

The conformance cases drive the real ``OpenAITransport`` (the real SDK, only the socket fake) against
``FakeVllm``, which answers as vLLM does once that row's tool-call and reasoning parsers have run.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import httpx
import openai
import pytest

from harness_core.kernel.core.messages import (
    AssistantMessage,
    ToolCall,
    ToolMessage,
    UserMessage,
)
from harness_core.kernel.core.streaming import (
    StreamCompleteEvent,
    ThinkingDeltaEvent,
    ToolCallDeltaEvent,
)
from harness_core.kernel.core.tool_schema import ToolSchema
from harness_core.models.capabilities import model_capabilities
from harness_core.models.open_models import (
    OPEN_MODEL_PINS,
    REASONING_FIELDS,
    VLLM_VERSION,
    ChatTemplate,
    OpenModelPin,
    check_template,
    open_model_pin,
    template_digest,
)
from harness_core.models.transports.openai import OpenAITransport
from harness_core.testing.fake_vllm import FakeVllm, ScriptedTurn

if TYPE_CHECKING:
    from harness_core.kernel.core.messages import Message
    from harness_core.kernel.core.streaming import StreamEvent

ENDPOINT = "http://vllm.internal:8000/v1"
SEARCH = ToolSchema(
    name="search_records",
    description="Search the organisation's records.",
    input_schema={
        "type": "object",
        "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}},
        "required": ["query"],
    },
)
ARGS = {"query": "quarterly revenue", "limit": 5}
PINS = pytest.mark.parametrize("pin", OPEN_MODEL_PINS, ids=[p.key for p in OPEN_MODEL_PINS])


def _served(pin: OpenModelPin) -> str:
    return pin.repository.split("/", 1)[1]


def _over(server: FakeVllm, model: str | None = None) -> OpenAITransport:
    transport = OpenAITransport(api_key="vllm-test", model=model or server.served_name, base_url=ENDPOINT)
    transport._client = openai.AsyncOpenAI(  # pyright: ignore[reportPrivateUsage]
        api_key="vllm-test", base_url=ENDPOINT, max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(server)),
    )
    return transport


async def _stream(transport: OpenAITransport, messages: list[Message]) -> list[StreamEvent]:
    return [event async for event in transport.stream(messages, tools=[SEARCH], system="Answer from records.")]


def _final(events: list[StreamEvent]) -> AssistantMessage:
    done = [e for e in events if isinstance(e, StreamCompleteEvent)]
    assert len(done) == 1
    return done[0].response.message


def _turns(pin: OpenModelPin) -> list[ScriptedTurn]:
    thinking = "The person asks for revenue; search first." if pin.reasoning_parser else ""
    return [ScriptedTurn(reasoning=thinking, calls=(("search_records", ARGS),)), ScriptedTurn(text="Revenue rose 4%.")]


def test_wp91_vllm_1_each_named_model_has_one_complete_row() -> None:
    """WP91-VLLM-1: Qwen3-Coder, Kimi K2 and GLM (checklist C13.04) each have a row naming the vLLM version, the
    template, the tool-call parser and, where the model thinks, the reasoning parser."""
    keys = {pin.key for pin in OPEN_MODEL_PINS}
    assert {"qwen3-coder", "kimi-k2", "glm-4.6"} <= keys
    assert len(keys) == len(OPEN_MODEL_PINS)
    for pin in OPEN_MODEL_PINS:
        assert pin.vllm_version == VLLM_VERSION
        assert pin.template.name and pin.tool_parser and pin.source.startswith("https://docs.vllm.ai/")
        assert pin.reasoning_field in REASONING_FIELDS
    assert open_model_pin("glm-4.6").reasoning_parser == "glm45"  # type: ignore[union-attr]
    assert open_model_pin("Qwen/Qwen3-Coder-30B-A3B-Instruct").tool_parser == "qwen3_coder"  # type: ignore[union-attr]
    assert open_model_pin("kimi-k2-instruct-0905").tool_parser == "kimi_k2"  # type: ignore[union-attr]
    assert open_model_pin("gpt-4o") is None
    assert open_model_pin(None) is None


@PINS
def test_wp91_vllm_2_the_serve_command_is_the_rows(pin: OpenModelPin) -> None:
    """WP91-VLLM-2: ``vllm serve`` for a row carries its parsers and auto tool choice, and nothing it does not pin."""
    args = pin.serve_args("engine-model")
    assert args[:3] == ("vllm", "serve", pin.repository)
    assert ("--tool-call-parser", pin.tool_parser) == args[args.index("--tool-call-parser") :][:2]
    assert "--enable-auto-tool-choice" in args
    assert ("--reasoning-parser" in args) == (pin.reasoning_parser is not None)
    assert ("--trust-remote-code" in args) == pin.trust_remote_code
    assert ("--revision" in args) == (pin.revision is not None)
    assert ("--chat-template" in args) == (pin.template.source == "vllm")


def test_wp91_vllm_3_a_template_is_checked_by_its_digest_and_never_trusted_unrecorded() -> None:
    """WP91-VLLM-3: a recorded digest admits only that template; an unrecorded one is reported unverified."""
    text = "{% for m in messages %}{{ m.content }}{% endfor %}"
    pinned = OpenModelPin("x", "org/x", OPEN_MODEL_PINS[0].served, ChatTemplate("t.jinja", sha256=template_digest(text)),
                          "hermes", revision="0" * 40)
    assert pinned.verified
    assert check_template(pinned, text) == "verified"
    assert check_template(pinned, text + " ") == "mismatch"
    for pin in OPEN_MODEL_PINS:
        assert check_template(pin, text) == ("unverified" if pin.template.sha256 is None else "mismatch")
        assert pin.verified == (pin.template.sha256 is not None and pin.revision is not None)


@PINS
def test_wp91_vllm_4_an_open_model_takes_no_images(pin: OpenModelPin) -> None:
    """WP91-VLLM-4: a pinned text-only model at an admin's endpoint is known not to take image input, so the health
    check does not probe it and nothing sends it an image; another model there stays unknown."""
    assert model_capabilities(_served(pin), ENDPOINT).image_input is False
    assert model_capabilities("my-finetune", ENDPOINT).image_input is None
    # A vendor's own API (no base URL) is the caller's rule, as for every row.
    assert model_capabilities(_served(pin), None).image_input is None


@PINS
async def test_wp91_vllm_5_conformance_a_tool_turn_round_trips(pin: OpenModelPin) -> None:
    """WP91-VLLM-5 (conformance): the transport reads the row's streamed tool call (name, arguments, the model's own
    id), and the next request sends the call and its result back under that id, which vLLM accepts."""
    server = FakeVllm(pin, _served(pin), _turns(pin))
    transport = _over(server)
    events = await _stream(transport, [UserMessage(content="What was revenue last quarter?")])
    first = _final(events)
    assert first.tool_calls is not None and len(first.tool_calls) == 1
    call = first.tool_calls[0]
    assert (call.name, call.arguments) == ("search_records", ARGS)
    if pin.tool_call_id is not None:
        assert pin.tool_call_id.fullmatch(call.id)
    deltas = [e for e in events if isinstance(e, ToolCallDeltaEvent)]
    assert deltas[0].name == "search_records"
    request = server.requests[0]
    assert request["model"] == _served(pin)
    assert [t["function"]["name"] for t in request["tools"]] == ["search_records"]

    history: list[Message] = [
        UserMessage(content="What was revenue last quarter?"),
        AssistantMessage(content=None, tool_calls=[ToolCall(id=call.id, name=call.name, arguments=call.arguments)]),
        ToolMessage(content=json.dumps({"records": ["Q3: +4%"]}), tool_call_id=call.id),
    ]
    second = _final(await _stream(transport, history))
    assert second.text == "Revenue rose 4%."
    sent: dict[str, Any] = server.requests[1]
    assert [m["role"] for m in sent["messages"]] == ["system", "user", "assistant", "tool"]
    assert sent["messages"][2]["tool_calls"][0]["id"] == call.id == sent["messages"][3]["tool_call_id"]


@PINS
async def test_wp91_vllm_6_conformance_reasoning_reaches_the_loop(pin: OpenModelPin) -> None:
    """WP91-VLLM-6 (conformance): a row with a reasoning parser has its thinking streamed as thinking, never as the
    answer's text."""
    if pin.reasoning_parser is None:
        pytest.skip("the model has no reasoning parser")
    server = FakeVllm(pin, _served(pin), _turns(pin))
    events = await _stream(_over(server), [UserMessage(content="What was revenue last quarter?")])
    thinking = "".join(e.delta for e in events if isinstance(e, ThinkingDeltaEvent))
    assert thinking == _turns(pin)[0].reasoning
    assert not _final(events).text


@pytest.mark.parametrize("field", REASONING_FIELDS)
async def test_wp91_vllm_7_either_reasoning_field_is_read(field: str) -> None:
    """WP91-VLLM-7: vLLM's reasoning field is ``reasoning_content``, and later releases send ``reasoning``; the
    transport reads both, so upgrading a row's vLLM does not silently turn its thinking off."""
    base = next(p for p in OPEN_MODEL_PINS if p.reasoning_parser is not None)
    pin = OpenModelPin(base.key, base.repository, base.served, base.template, base.tool_parser, base.reasoning_parser,
                       field)  # type: ignore[arg-type]
    server = FakeVllm(pin, _served(pin), [ScriptedTurn(reasoning="Think it through.", text="Done.")])
    events = await _stream(_over(server), [UserMessage(content="hi")])
    assert "".join(e.delta for e in events if isinstance(e, ThinkingDeltaEvent)) == "Think it through."
    assert _final(events).text == "Done."


@PINS
async def test_wp91_vllm_8_conformance_a_whole_response_carries_the_call(pin: OpenModelPin) -> None:
    """WP91-VLLM-8 (conformance): the non-streamed path reads the same tool call."""
    server = FakeVllm(pin, _served(pin), _turns(pin))
    response = await _over(server).complete([UserMessage(content="What was revenue?")], tools=[SEARCH])
    calls = response.message.tool_calls or []
    assert [(c.name, c.arguments) for c in calls] == [("search_records", ARGS)]
    assert server.requests[0].get("stream") is not True


async def test_wp91_vllm_9_the_fake_refuses_what_vllm_refuses() -> None:
    """WP91-VLLM-9: the fake holds the transport to vLLM's rules: the served name, and a tool result's id."""
    pin = OPEN_MODEL_PINS[0]
    wrong = FakeVllm(pin, "another-name", _turns(pin))
    with pytest.raises(Exception, match="does not exist"):
        await _stream(_over(wrong, _served(pin)), [UserMessage(content="hi")])
    orphan = FakeVllm(pin, _served(pin), _turns(pin))
    with pytest.raises(Exception, match="unknown call"):
        await _stream(_over(orphan), [UserMessage(content="hi"), ToolMessage(content="x", tool_call_id="nope")])

