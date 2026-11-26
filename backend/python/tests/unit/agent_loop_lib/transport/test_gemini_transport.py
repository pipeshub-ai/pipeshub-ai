"""Gemini direct transport.

Gemini's wire shape differs from OpenAI's more than Anthropic's does -- two
roles, tool results as parts, an OpenAPI-subset schema -- so most of these guard
the mapping rather than the transport plumbing.
"""

from __future__ import annotations

import base64
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.agent_loop_lib.core.messages import (
    AssistantMessage,
    ToolCall,
    ToolMessage,
    UserMessage,
)
from app.agent_loop_lib.core.responses import StopReason
from app.agent_loop_lib.core.streaming import (
    StreamCompleteEvent,
    TextDeltaEvent,
    ThinkingDeltaEvent,
    ToolCallDeltaEvent,
)
from app.agent_loop_lib.core.tool_schema import ToolSchema
from app.agent_loop_lib.transport.gemini import (
    GeminiTransport,
    sanitize_schema,
    signature_block,
)
from harness_core.models.provider_state import GEMINI_GENERATE_CONTENT, with_state


def _transport(**kwargs) -> GeminiTransport:
    return GeminiTransport(api_key="k", model="gemini-3-flash-preview", **kwargs)


def _signed(message: AssistantMessage, signatures: dict[str, bytes]) -> AssistantMessage:
    """``message`` as the transport returns it after Gemini signed its calls."""
    blocks = [
        {"callId": call_id, "thoughtSignature": base64.b64encode(sig).decode("ascii")}
        for call_id, sig in signatures.items()
    ]
    return with_state(message, GEMINI_GENERATE_CONTENT, blocks)


def _part(text=None, function_call=None, thought=False):
    return SimpleNamespace(text=text, function_call=function_call, thought=thought)


def _chunk(parts, finish=None, usage=None):
    return SimpleNamespace(
        candidates=[SimpleNamespace(
            content=SimpleNamespace(parts=parts),
            finish_reason=SimpleNamespace(name=finish) if finish else None,
        )],
        usage_metadata=usage,
    )


def _call(name="search", args=None, cid=None):
    return SimpleNamespace(name=name, args=args or {"q": "x"}, id=cid)


def _wire_stream(transport: GeminiTransport, chunks: list) -> None:
    async def _gen():
        for c in chunks:
            yield c

    async def _create(**kwargs):
        transport._last_kwargs = kwargs
        return _gen()

    transport._client = MagicMock()
    transport._client.aio.models.generate_content_stream = _create


class TestSchemaSanitizer:
    """Gemini's `parameters` is an OpenAPI subset; JSON-Schema keywords we emit
    are rejected outright, failing every tool-bearing request."""

    def test_strips_unsupported_keywords_at_every_depth(self) -> None:
        out = sanitize_schema({
            "type": "object",
            "$schema": "http://json-schema.org/draft-07/schema#",
            "$comment": "dropped",
            "multipleOf": 2,
            "properties": {
                "nested": {
                    "type": "object",
                    "uniqueItems": True,
                    "properties": {"a": {"type": "string", "pattern": "^a"}},
                },
            },
        })
        assert "$schema" not in out
        assert "$comment" not in out, "types.Schema is extra=forbid; this 400s"
        assert "multipleOf" not in out
        assert "uniqueItems" not in out["properties"]["nested"]
        # a real Schema field survives
        assert out["properties"]["nested"]["properties"]["a"]["pattern"] == "^a"

    def test_object_without_properties_still_gets_the_key(self) -> None:
        """A bare `{"type": "object"}` is rejected as a malformed declaration."""
        assert sanitize_schema({"type": "object"})["properties"] == {}

    def test_anyof_is_kept_but_sanitized(self) -> None:
        out = sanitize_schema(
            {"anyOf": [{"type": "string"}, {"type": "object", "$comment": "x"}]}
        )
        assert len(out["anyOf"]) == 2
        assert "$comment" not in out["anyOf"][1]

    def test_output_actually_constructs_a_declaration(self) -> None:
        """The point of the allow-list: asserting keys are absent from a dict
        proves nothing if genai still rejects the result. types.Schema is
        extra="forbid", so this is the assertion that matters."""
        from google.genai import types

        out = sanitize_schema({
            "type": "object", "$comment": "x", "multipleOf": 2, "title": "T",
            "properties": {"q": {"type": "string", "uniqueItems": True}},
            "required": ["q"],
        })
        types.FunctionDeclaration(name="f", description="d", parameters=out)

    def test_array_items_are_sanitized(self) -> None:
        out = sanitize_schema(
            {"type": "array", "items": {"type": "object", "$ref": "#/x", "properties": {}}}
        )
        assert "$ref" not in out["items"]


class TestMessageMapping:
    def test_tool_result_becomes_a_function_response_part(self) -> None:
        """Gemini has no tool role: a result is a user message carrying a
        function_response part, or the model loses track of what it called."""
        t = _transport()
        contents = t._format_contents([
            ToolMessage(content="42", tool_call_id="call_1"),
        ])
        assert contents[0].role == "user"
        assert contents[0].parts[0].function_response is not None

    def test_assistant_tool_calls_round_trip_as_model_parts(self) -> None:
        t = _transport()
        contents = t._format_contents([
            _signed(AssistantMessage(
                content="thinking",
                tool_calls=[ToolCall(id="c1", name="search", arguments={"q": "x"})],
            ), {"c1": b"sig"}),
        ])
        assert contents[0].role == "model"
        kinds = [p.function_call is not None for p in contents[0].parts]
        assert True in kinds, "the tool call must survive into history"

    def test_user_message_maps_to_user_role(self) -> None:
        t = _transport()
        contents = t._format_contents([UserMessage(content="hi")])
        assert contents[0].role == "user"
        assert contents[0].parts[0].text == "hi"


class TestConfig:
    def test_thinking_config_is_sent_when_captured(self) -> None:
        cfg = _transport(thinking_level="high")._build_config(None, None, None)
        assert cfg.thinking_config is not None
        # The SDK normalises the string aimodels supplies onto its own enum, so
        # compare on value rather than identity.
        assert str(cfg.thinking_config.thinking_level.value).lower() == "high"

    def test_no_thinking_config_when_none_configured(self) -> None:
        assert _transport()._build_config(None, None, None).thinking_config is None

    def test_system_prompt_goes_to_system_instruction(self) -> None:
        """Gemini has no system role -- it is a separate config field."""
        cfg = _transport()._build_config(None, "be brief", None)
        assert cfg.system_instruction == "be brief"

    def test_system_blocks_are_joined_when_no_system(self) -> None:
        cfg = _transport()._build_config(None, None, ["a", "b"])
        assert cfg.system_instruction == "a\n\nb"

    def test_tools_become_function_declarations(self) -> None:
        schema = ToolSchema(
            name="search", description="d",
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        )
        cfg = _transport()._build_config([schema], None, None)
        decl = cfg.tools[0].function_declarations[0]
        assert decl.name == "search"


class TestStreaming:
    @pytest.mark.asyncio
    async def test_text_streams_and_completes(self) -> None:
        t = _transport()
        _wire_stream(t, [
            _chunk([_part(text="Hel")]),
            _chunk([_part(text="lo")], finish="STOP",
                   usage=SimpleNamespace(prompt_token_count=10, candidates_token_count=2,
                                         thoughts_token_count=5,
                                         cached_content_token_count=3)),
        ])
        events = [e async for e in t.stream(messages=[UserMessage(content="hi")])]

        assert [e.delta for e in events if isinstance(e, TextDeltaEvent)] == ["Hel", "lo"]
        final = next(e for e in events if isinstance(e, StreamCompleteEvent)).response
        assert final.message.text == "Hello"
        assert final.stop_reason == StopReason.END_TURN
        assert final.usage.input_tokens == 10
        # thinking tokens are billed output; excluding them under-reports cost
        assert final.usage.output_tokens == 7
        assert final.usage.cache_read_tokens == 3

    @pytest.mark.asyncio
    async def test_tool_call_arrives_as_one_fragment_carrying_the_name(self) -> None:
        """Gemini sends a whole function_call in a single chunk rather than
        streaming its arguments. The agent loop reads `name` off the FIRST delta
        for an index, so that fragment must carry both name and arguments."""
        t = _transport()
        _wire_stream(t, [
            _chunk([_part(function_call=_call(name="final_answer",
                                              args={"answer_markdown": "hi"}))],
                   finish="STOP"),
        ])
        events = [e async for e in t.stream(messages=[UserMessage(content="hi")])]

        frags = [e for e in events if isinstance(e, ToolCallDeltaEvent)]
        assert len(frags) == 1
        assert frags[0].name == "final_answer"
        assert json.loads(frags[0].arguments_delta) == {"answer_markdown": "hi"}

        final = next(e for e in events if isinstance(e, StreamCompleteEvent)).response
        assert final.stop_reason == StopReason.TOOL_USE
        assert final.message.tool_calls[0].arguments == {"answer_markdown": "hi"}

    @pytest.mark.asyncio
    async def test_thought_parts_become_thinking_not_answer_text(self) -> None:
        t = _transport()
        _wire_stream(t, [
            _chunk([_part(text="pondering", thought=True), _part(text="answer")],
                   finish="STOP"),
        ])
        events = [e async for e in t.stream(messages=[UserMessage(content="hi")])]

        assert [e.delta for e in events if isinstance(e, ThinkingDeltaEvent)] == ["pondering"]
        final = next(e for e in events if isinstance(e, StreamCompleteEvent)).response
        assert final.message.text == "answer", "thoughts must not leak into the answer"

    @pytest.mark.asyncio
    async def test_truncation_beats_tool_calls(self) -> None:
        """A reply cut off mid-call must not be reported as a usable TOOL_USE."""
        t = _transport()
        _wire_stream(t, [
            _chunk([_part(function_call=_call())], finish="MAX_TOKENS"),
        ])
        events = [e async for e in t.stream(messages=[UserMessage(content="hi")])]
        final = next(e for e in events if isinstance(e, StreamCompleteEvent)).response
        assert final.stop_reason == StopReason.MAX_TOKENS
        assert final.message.truncated is True

    @pytest.mark.asyncio
    async def test_stream_error_is_wrapped(self) -> None:
        t = _transport()

        async def _boom(**kwargs):
            raise RuntimeError("resource_exhausted: quota")

        t._client = MagicMock()
        t._client.aio.models.generate_content_stream = _boom
        with pytest.raises(Exception, match="resource_exhausted"):
            [e async for e in t.stream(messages=[UserMessage(content="hi")])]


class TestConfigCapture:
    def test_reads_thinking_and_credentials_off_the_langchain_model(self) -> None:
        llm = SimpleNamespace(
            google_api_key="secret", model="gemini-3-flash-preview",
            temperature=0.2, thinking_level="high", thinking_budget=None,
            max_output_tokens=None, timeout=360.0,
        )
        t = GeminiTransport.from_langchain_model(llm, model_name="gemini-3-flash-preview")
        assert t._thinking_level == "high"
        assert t._temperature == 0.2
        assert t.model_name == "gemini-3-flash-preview"

    def test_missing_key_is_rejected_loudly(self) -> None:
        llm = SimpleNamespace(google_api_key=None, model="x")
        with pytest.raises(ValueError, match="google_api_key"):
            GeminiTransport.from_langchain_model(llm)


class TestToolResultCorrelation:
    """Gemini matches a function_response to its function_call by NAME.

    `ToolMessage` carries only `tool_call_id`, so the name has to be recovered
    from the assistant message that made the call. Sending the id instead leaves
    the model unable to see its own tool result -- it answers as if the tool
    never ran, which is exactly how this surfaced: a turn that executed a tool
    and then returned an empty answer.
    """

    def test_response_name_comes_from_the_matching_call(self) -> None:
        t = _transport()
        contents = t._format_contents([
            UserMessage(content="find x"),
            _signed(AssistantMessage(
                tool_calls=[ToolCall(id="call_1", name="knowledgegraph__search",
                                     arguments={"q": "x"})],
            ), {"call_1": b"sig"}),
            ToolMessage(content="results", tool_call_id="call_1"),
        ])
        response_part = contents[-1].parts[0].function_response
        assert response_part.name == "knowledgegraph__search"
        assert response_part.name != "call_1", "the id is not a function name"

    def test_unmatched_tool_call_id_does_not_crash(self) -> None:
        """History can be compacted, dropping the assistant turn that made the
        call; a missing name must degrade, not raise."""
        t = _transport()
        contents = t._format_contents([ToolMessage(content="r", tool_call_id="orphan")])
        assert contents[0].parts[0].function_response.name == "tool"


class TestThoughtSignature:
    """Gemini 3 rejects a replayed function_call whose thought_signature is
    missing: "Function call is missing a thought_signature in functionCall
    parts. This is required for tools to work correctly." It is opaque bytes
    with nowhere to live on our provider-neutral ToolCall, so it travels on the
    message's provider state (H4 GS) and is re-attached when history is rebuilt.
    """

    @pytest.mark.asyncio
    async def test_signature_is_captured_from_the_stream(self) -> None:
        t = _transport()
        part = _part(function_call=_call(name="lookup", cid="call_9"))
        part.thought_signature = b"sig-bytes"
        _wire_stream(t, [_chunk([part], finish="STOP")])

        events = [e async for e in t.stream(messages=[UserMessage(content="hi")])]

        message = events[-1].response.message
        assert t._format_contents([message])[0].parts[0].thought_signature == b"sig-bytes"

    def test_signature_is_replayed_onto_history(self) -> None:
        t = _transport()
        contents = t._format_contents([
            _signed(AssistantMessage(
                tool_calls=[ToolCall(id="call_9", name="lookup", arguments={"q": "x"})],
            ), {"call_9": b"sig-bytes"}),
        ])
        assert contents[0].parts[0].thought_signature == b"sig-bytes"

    def test_absent_signature_leaves_the_part_clean(self) -> None:
        """Turns that never produced one (thinking off) must not send an empty
        field."""
        t = _transport()
        contents = t._format_contents([
            AssistantMessage(
                tool_calls=[ToolCall(id="unknown", name="lookup", arguments={})],
            ),
        ])
        assert contents[0].parts[0].thought_signature is None


class TestUnsignedHistoryReplay:
    """A conversation spans several HTTP requests, and the registry -- so the
    transport and its signature map -- is rebuilt per request
    (PipesHubAgentFactory.create). Replaying an earlier turn's function_call
    without its signature is exactly the 400 the signature cache was added to
    avoid, so those exchanges are replayed as text instead.
    """

    def test_unsigned_call_is_replayed_as_text_not_a_function_call(self) -> None:
        t = _transport()
        contents = t._format_contents([
            AssistantMessage(
                tool_calls=[ToolCall(id="old", name="lookup", arguments={"q": "x"})],
            ),
        ])
        parts = contents[0].parts
        assert all(p.function_call is None for p in parts), (
            "an unsigned function_call part is rejected by Gemini 3"
        )
        assert "lookup" in parts[0].text

    def test_result_of_a_flattened_call_is_also_text(self) -> None:
        """A function_response with no matching function_call is invalid."""
        t = _transport()
        contents = t._format_contents([
            AssistantMessage(
                tool_calls=[ToolCall(id="old", name="lookup", arguments={})],
            ),
            ToolMessage(content="the result", tool_call_id="old"),
        ])
        assert contents[-1].parts[0].function_response is None
        assert "the result" in contents[-1].parts[0].text

    def test_signed_call_still_uses_the_function_call_path(self) -> None:
        t = _transport()
        contents = t._format_contents([
            _signed(AssistantMessage(
                tool_calls=[ToolCall(id="fresh", name="lookup", arguments={})],
            ), {"fresh": b"sig"}),
            ToolMessage(content="r", tool_call_id="fresh"),
        ])
        assert contents[0].parts[0].function_call is not None
        assert contents[-1].parts[0].function_response is not None


class TestCallIdUniqueness:
    """AI Studio leaves function_call.id unset. A positional fallback restarted
    at 0 every request, so turn 2 overwrote turn 1's signature and an older tool
    result resolved to the newer call's name."""

    @pytest.mark.asyncio
    async def test_ids_do_not_collide_across_turns(self) -> None:
        t = _transport()
        seen = []
        messages = []
        for turn in range(2):
            part = _part(function_call=_call(name="lookup", cid=None))
            part.thought_signature = f"sig-{turn}".encode()
            _wire_stream(t, [_chunk([part], finish="STOP")])
            events = [e async for e in t.stream(messages=[UserMessage(content="hi")])]
            seen.append(next(
                e for e in events if isinstance(e, ToolCallDeltaEvent)
            ).id)
            messages.append(events[-1].response.message)

        assert seen[0] != seen[1], "a second turn must not reuse the first id"
        replayed = t._format_contents(messages)
        assert [c.parts[0].thought_signature for c in replayed] == [b"sig-0", b"sig-1"], (
            "neither signature may be lost"
        )


def _wire_complete(transport: GeminiTransport, response: object) -> None:
    async def _create(**kwargs: object) -> object:
        transport._last_kwargs = kwargs
        return response

    transport._client = MagicMock()
    transport._client.aio.models.generate_content = _create


def _signed_part(call_id: str, signature: bytes | None, name: str = "lookup") -> SimpleNamespace:
    part = _part(function_call=_call(name=name, cid=call_id))
    part.thought_signature = signature
    return part


_COUNTERS = frozenset({
    "total_input_tokens", "total_output_tokens", "total_llm_calls",
    "total_cache_read_tokens", "total_cache_write_tokens", "_client", "_last_kwargs",
})


def _instance_state(t: GeminiTransport) -> dict[str, object]:
    return {k: repr(v) for k, v in vars(t).items() if k not in _COUNTERS}


class TestSignaturesTravelWithTheMessage:
    """H4 GS: the transport is cached per org configuration and shared by every
    run (the model gateway), so a signature kept on it outlived its run, was
    never erased with it, and grew without bound. It lives on the message's
    provider state instead, which the durable kernel journals with the response
    (``rawRef``) and session erasure erases."""

    @pytest.mark.asyncio
    async def test_gs1_complete_puts_the_signature_on_the_message(self) -> None:
        t = _transport()
        _wire_complete(t, _chunk([_signed_part("c1", b"\x00sig"), _signed_part("c2", None)], finish="STOP"))
        response = await t.complete(messages=[UserMessage(content="hi")])
        state = response.message.provider_state
        assert state is not None and state.wire == GEMINI_GENERATE_CONTENT
        # Gemini issued both ids, so both ride the message for replay (main #5413's id pairing, kept off the
        # transport); only the first is signed.
        assert state.blocks == [
            {"callId": "c1", "thoughtSignature": base64.b64encode(b"\x00sig").decode(), "providerId": True},
            {"callId": "c2", "providerId": True},
        ]

    @pytest.mark.asyncio
    async def test_gs1_no_signature_no_state(self) -> None:
        # AI Studio: no id on the call and no signature, so nothing rides the message.
        t = _transport()
        _wire_complete(t, _chunk([_signed_part(None, None)], finish="STOP"))
        response = await t.complete(messages=[UserMessage(content="hi")])
        assert response.message.provider_state is None
        assert "provider_state" not in response.message.model_dump_json()

    @pytest.mark.asyncio
    async def test_gs2_stream_puts_the_signature_on_the_final_message(self) -> None:
        t = _transport()
        _wire_stream(t, [_chunk([_signed_part("c1", b"s1")]), _chunk([_signed_part("c2", b"s2")], finish="STOP")])
        events = [e async for e in t.stream(messages=[UserMessage(content="hi")])]
        complete = events[-1]
        assert isinstance(complete, StreamCompleteEvent)
        assert complete.response.message.provider_state.blocks == [
            {"callId": "c1", "thoughtSignature": base64.b64encode(b"s1").decode(), "providerId": True},
            {"callId": "c2", "thoughtSignature": base64.b64encode(b"s2").decode(), "providerId": True},
        ]

    def test_gs3_a_signed_and_an_unsigned_call_in_one_message(self) -> None:
        t = _transport()
        message = _signed(AssistantMessage(tool_calls=[
            ToolCall(id="signed", name="lookup", arguments={}),
            ToolCall(id="unsigned", name="other", arguments={}),
        ]), {"signed": b"sig"})
        contents = t._format_contents([
            message,
            ToolMessage(content="r1", tool_call_id="signed"),
            ToolMessage(content="r2", tool_call_id="unsigned"),
        ])
        first, second = contents[0].parts
        assert first.function_call is not None and first.thought_signature == b"sig"
        # Gemini signs only the first call of a parallel set, so it vouches for its unsigned sibling (main).
        assert second.function_call is not None and second.function_call.name == "other"
        assert contents[1].parts[0].function_response is not None
        assert contents[2].parts[0].function_response is not None

    def test_gs3_an_unsigned_first_call_flattens_the_turn(self) -> None:
        t = _transport()
        message = _signed(AssistantMessage(tool_calls=[
            ToolCall(id="unsigned", name="other", arguments={}),
            ToolCall(id="signed", name="lookup", arguments={}),
        ]), {"signed": b"sig"})
        contents = t._format_contents([
            message,
            ToolMessage(content="r1", tool_call_id="unsigned"),
            ToolMessage(content="r2", tool_call_id="signed"),
        ])
        first, second = contents[0].parts
        assert first.function_call is None and "other" in first.text
        assert second.function_call is not None and second.thought_signature == b"sig"
        assert contents[1].parts[0].function_response is None and "r1" in contents[1].parts[0].text

    def test_gs5_only_ids_gemini_issued_are_echoed(self) -> None:
        t = _transport()
        issued = with_state(
            AssistantMessage(tool_calls=[
                ToolCall(id="g1", name="lookup", arguments={}), ToolCall(id="call_0123456789abcdef", name="x", arguments={}),
            ]),
            GEMINI_GENERATE_CONTENT,
            [*signature_block("g1", b"sig", provided=True), *signature_block("call_0123456789abcdef", b"s2")],
        )
        contents = t._format_contents([
            issued,
            ToolMessage(content="r1", tool_call_id="g1"),
            ToolMessage(content="r2", tool_call_id="call_0123456789abcdef"),
        ])
        first, second = contents[0].parts
        assert first.function_call.id == "g1" and second.function_call.id is None
        assert contents[1].parts[0].function_response.id == "g1"
        assert contents[2].parts[0].function_response.id is None

    def test_gs4_another_wire_or_another_call_is_ignored(self) -> None:
        t = _transport()
        block = [{"callId": "c1", "thoughtSignature": base64.b64encode(b"sig").decode()}]
        foreign = with_state(
            AssistantMessage(tool_calls=[ToolCall(id="c1", name="lookup", arguments={})]),
            "anthropic_messages", block,
        )
        other_call = _signed(
            AssistantMessage(tool_calls=[ToolCall(id="c2", name="lookup", arguments={})]), {"c1": b"sig"},
        )
        for message in (foreign, other_call):
            part = t._format_contents([message])[0].parts[0]
            assert part.function_call is None and part.thought_signature is None

    @pytest.mark.asyncio
    async def test_gs5_another_instance_replays_the_signature(self) -> None:
        first = _transport()
        _wire_complete(first, _chunk([_signed_part("c1", b"sig")], finish="STOP"))
        message = (await first.complete(messages=[UserMessage(content="hi")])).message
        part = _transport()._format_contents([message])[0].parts[0]
        assert part.function_call is not None and part.thought_signature == b"sig"

    @pytest.mark.asyncio
    async def test_gs6_one_shared_transport_never_lends_a_signature_to_another_run(self) -> None:
        shared = _transport()
        _wire_complete(shared, _chunk([_signed_part("call_same", b"run-a")], finish="STOP"))
        run_a = (await shared.complete(messages=[UserMessage(content="a")])).message
        run_b = AssistantMessage(tool_calls=[ToolCall(id="call_same", name="lookup", arguments={})])
        b_part = shared._format_contents([run_b])[0].parts[0]
        assert b_part.function_call is None and b_part.thought_signature is None
        assert shared._format_contents([run_a])[0].parts[0].thought_signature == b"run-a"

    @pytest.mark.asyncio
    async def test_gs7_the_transport_holds_nothing_of_its_calls(self) -> None:
        t = _transport()
        _wire_complete(t, None)
        before = _instance_state(t)
        for i in range(20):
            _wire_complete(t, _chunk([_signed_part(f"c{i}", b"sig")], finish="STOP"))
            await t.complete(messages=[UserMessage(content="hi")])
            _wire_stream(t, [_chunk([_signed_part(f"s{i}", b"sig")], finish="STOP")])
            [e async for e in t.stream(messages=[UserMessage(content="hi")])]
        assert _instance_state(t) == before

    @pytest.mark.asyncio
    async def test_gs8_the_journaled_message_carries_the_signature_back(self) -> None:
        from harness_core.kernel.durable.adapters import TransportModelPort

        t = _transport()
        _wire_complete(t, _chunk([_signed_part("c1", b"sig")], finish="STOP"))
        message = (await t.complete(messages=[UserMessage(content="hi")])).message
        port = TransportModelPort(MagicMock(), provider="gemini", transport="direct")
        restored = port.restore(port.raw_state(message), provider="gemini", transport="direct")
        assert restored is not None
        assert _transport()._format_contents([restored])[0].parts[0].thought_signature == b"sig"

    @pytest.mark.asyncio
    async def test_gs9_captured_without_the_provider_state_flag(self) -> None:
        assert _transport().keep_provider_state() is True
        fresh = _transport()
        _wire_complete(fresh, _chunk([_signed_part("c1", b"sig")], finish="STOP"))
        assert (await fresh.complete(messages=[UserMessage(content="hi")])).message.provider_state is not None


class TestStructuredOutput:
    @pytest.mark.asyncio
    async def test_raises_when_the_model_answers_with_text(self) -> None:
        """Returning {} would hand the planner an empty structure with no
        error."""
        t = _transport()

        async def _create(**kwargs):
            return _chunk([_part(text="I would rather chat")], finish="STOP")

        t._client = MagicMock()
        t._client.aio.models.generate_content = _create
        with pytest.raises(Exception, match="no structured response"):
            await t.complete_structured(
                messages=[UserMessage(content="hi")],
                output_schema={"type": "object", "properties": {}},
            )

    @pytest.mark.asyncio
    async def test_forces_the_tool(self) -> None:
        captured = {}

        async def _create(**kwargs):
            captured.update(kwargs)
            return _chunk([_part(function_call=_call(name="respond", args={"a": 1}))],
                          finish="STOP")

        t = _transport()
        t._client = MagicMock()
        t._client.aio.models.generate_content = _create
        result = await t.complete_structured(
            messages=[UserMessage(content="hi")],
            output_schema={"type": "object", "properties": {"a": {"type": "integer"}}},
        )
        assert result.data == {"a": 1}
        cfg = captured["config"]
        assert cfg.tool_config.function_calling_config.mode.name == "ANY"


class TestInterfaceConformance:
    """Every transport is called through `LLMTransport`, and callers pass by
    keyword (`models/transport.py` uses `output_schema=`). A renamed parameter
    is therefore a TypeError at the call site, not a type-checker warning --
    which is exactly how Gemini's structured output shipped broken while its own
    tests passed, because they used the wrong name too.

    Covers every implementation, including the tracing decorator, since that is
    what the factory actually hands to the agent loop.
    """

    @staticmethod
    def _implementations() -> list:
        from app.agent_loop_lib.transport.anthropic import AnthropicTransport
        from app.agent_loop_lib.transport.azure_openai import AzureOpenAITransport
        from app.agent_loop_lib.transport.ollama import OllamaTransport
        from app.agent_loop_lib.transport.openai import OpenAITransport
        from app.agent_loop_lib.transport.opik_tracing import OpikTracingTransport

        return [
            GeminiTransport, OpenAITransport, AzureOpenAITransport,
            AnthropicTransport, OllamaTransport, OpikTracingTransport,
        ]

    @pytest.mark.parametrize("method", ["complete", "complete_structured", "stream"])
    def test_every_transport_accepts_the_base_call(self, method: str) -> None:
        """`bind` rather than a name comparison: it also catches a parameter that
        became positional-only or a new required argument, neither of which a
        set-difference on names would see."""
        import inspect

        from app.agent_loop_lib.transport.base import LLMTransport

        base = inspect.signature(getattr(LLMTransport, method))
        call_kwargs = {
            name: object()
            for name, p in base.parameters.items()
            if name != "self" and p.kind is not inspect.Parameter.VAR_KEYWORD
        }

        for transport in self._implementations():
            signature = inspect.signature(getattr(transport, method))
            try:
                signature.bind(object(), **call_kwargs)
            except TypeError as exc:
                pytest.fail(
                    f"{transport.__name__}.{method} cannot accept the base call: "
                    f"{exc} -- a keyword call through LLMTransport raises TypeError"
                )
