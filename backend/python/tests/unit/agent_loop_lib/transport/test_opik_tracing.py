"""`opik_tracing.py` (`app/agent_loop_lib/transport/opik_tracing.py`) — Opik
LLM-call tracing as an `LLMTransport` decorator, plus the tool/sub-agent/
named-span helpers layered on top of the same `_guarded_cm` primitive.

All tests mock `opik.start_as_current_span`/`opik.start_as_current_trace`
directly (real functions confirmed importable/decorated `@contextmanager`
generators) rather than requiring a live Opik server — same approach
`test_plan_ahead.py` uses for mocking transports."""

from __future__ import annotations

import contextlib
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from app.agent_loop_lib.core.context import RunContext
from app.agent_loop_lib.core.messages import AssistantMessage, TextPart, UserMessage
from app.agent_loop_lib.core.responses import (
    ModelResponse,
    StopReason,
    StructuredResponse,
    TokenUsage,
)
from app.agent_loop_lib.core.streaming import StreamCompleteEvent, TextDeltaEvent
from app.agent_loop_lib.transport.opik_tracing import (
    OpikTracingTransport,
    is_opik_configured,
    maybe_start_agent_span,
    maybe_start_named_span,
    maybe_start_run_trace,
    maybe_start_tool_span,
    record_agent_span_result,
    record_named_span_output,
    record_tool_span_output,
    resolve_opik_gate,
    traced_transport_factory,
    wrap_if_enabled,
)


class _FakeSpan(SimpleNamespace):
    """Stand-in for opik's `SpanData` — a plain attribute bag, same shape
    the real `_guarded_cm.__enter__()` value gets `.output`/`.usage`/
    `.model` assigned onto."""


@contextlib.contextmanager
def _fake_span_cm(**_kwargs: Any):
    yield _FakeSpan()


@contextlib.contextmanager
def _fake_trace_cm(**_kwargs: Any):
    yield _FakeSpan()


class _FakeTransport:
    """Minimal `LLMTransport` double recording calls made through it."""

    def __init__(self, response: ModelResponse | None = None, structured: StructuredResponse | None = None) -> None:
        self._response = response
        self._structured = structured
        self.complete_calls: list[dict] = []

    @property
    def provider(self) -> str:
        return "fake"

    @property
    def model_name(self) -> str:
        return "fake-model"

    async def complete(self, **kwargs: Any) -> ModelResponse:
        self.complete_calls.append(kwargs)
        return self._response

    async def complete_structured(self, **kwargs: Any) -> StructuredResponse:
        return self._structured

    async def stream(self, **kwargs: Any):
        yield TextDeltaEvent(delta="hi")
        yield StreamCompleteEvent(response=self._response)


def _response(text: str = "hello", tool_calls: list | None = None) -> ModelResponse:
    return ModelResponse(
        message=AssistantMessage(content=[TextPart(text=text)], tool_calls=tool_calls),
        usage=TokenUsage(input_tokens=10, output_tokens=5),
        stop_reason=StopReason.TOOL_USE if tool_calls else StopReason.END_TURN,
        model="fake-model",
    )


class TestIsOpikConfigured:
    def test_false_when_no_env_vars_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OPIK_API_KEY", raising=False)
        monkeypatch.delenv("OPIK_URL_OVERRIDE", raising=False)
        assert is_opik_configured() is False

    def test_true_when_api_key_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPIK_API_KEY", "key-123")
        monkeypatch.delenv("OPIK_URL_OVERRIDE", raising=False)
        assert is_opik_configured() is True

    def test_true_when_only_self_hosted_url_override_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Self-hosted Opik has no required API key — must not require
        `OPIK_API_KEY` the way the legacy gate did."""
        monkeypatch.delenv("OPIK_API_KEY", raising=False)
        monkeypatch.setenv("OPIK_URL_OVERRIDE", "http://localhost:5000")
        assert is_opik_configured() is True


class TestWrapIfEnabled:
    def test_returns_unwrapped_when_disabled(self) -> None:
        transport = _FakeTransport()
        result = wrap_if_enabled(transport, enabled=False)
        assert result is transport

    def test_returns_wrapped_when_enabled(self) -> None:
        transport = _FakeTransport()
        result = wrap_if_enabled(transport, enabled=True, project_name="proj")
        assert isinstance(result, OpikTracingTransport)
        assert result.provider == "fake"


class TestResolveOpikGate:
    """`resolve_opik_gate` is the ONE AND-gate both `ControlPlane.start()`
    and `PipesHubAgentFactory.create()` call — must be False whenever
    either the feature flag is off OR the environment isn't configured,
    True only when both hold."""

    def test_false_when_flag_off_even_if_env_configured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPIK_API_KEY", "key-123")
        assert resolve_opik_gate(False) is False

    def test_false_when_flag_on_but_env_not_configured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OPIK_API_KEY", raising=False)
        monkeypatch.delenv("OPIK_URL_OVERRIDE", raising=False)
        assert resolve_opik_gate(True) is False

    def test_true_when_flag_on_and_env_configured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPIK_API_KEY", "key-123")
        assert resolve_opik_gate(True) is True


class TestTracedTransportFactory:
    def test_wraps_when_opik_active(self) -> None:
        factory = traced_transport_factory(_FakeTransport, opik_active=True, project_name="proj")
        result = factory()
        assert isinstance(result, OpikTracingTransport)
        assert result.provider == "fake"

    def test_does_not_wrap_when_opik_inactive(self) -> None:
        factory = traced_transport_factory(_FakeTransport, opik_active=False)
        result = factory()
        assert isinstance(result, _FakeTransport)

    def test_calls_underlying_factory_fresh_each_time(self) -> None:
        """Each call to the wrapped factory must build a NEW inner
        transport, not reuse/cache one — matches the `lambda: ...` shape
        the two duplicated call sites both used before extraction."""
        built: list[_FakeTransport] = []

        def _build() -> _FakeTransport:
            t = _FakeTransport()
            built.append(t)
            return t

        factory = traced_transport_factory(_build, opik_active=False)
        first = factory()
        second = factory()
        assert first is not second
        assert len(built) == 2


class TestMaybeStartRunTrace:
    def test_noop_when_disabled(self) -> None:
        from app.agent_loop_lib.core.types import Goal

        run_ctx = RunContext(role_name="assistant", model="gpt-4")
        cm = maybe_start_run_trace(enabled=False, run_ctx=run_ctx, goal=Goal(description="do the thing"))
        with cm as value:
            assert value is None  # nullcontext's __enter__ returns None

    def test_noop_for_sub_agent_run(self) -> None:
        from app.agent_loop_lib.core.types import Goal

        parent = RunContext(role_name="assistant", model="gpt-4")
        child_ctx = parent.child("researcher")
        assert child_ctx.parent_run_id is not None
        cm = maybe_start_run_trace(enabled=True, run_ctx=child_ctx, goal=Goal(description="research"))
        with patch("opik.start_as_current_trace") as mock_trace:
            with cm:
                pass
        mock_trace.assert_not_called()

    def test_opens_real_trace_for_root_run(self) -> None:
        from app.agent_loop_lib.core.types import Goal

        run_ctx = RunContext(role_name="assistant", model="gpt-4")
        goal = Goal(description="do the thing", requirements=["be thorough"])
        with patch("opik.start_as_current_trace", return_value=_fake_trace_cm()) as mock_trace:
            with maybe_start_run_trace(
                enabled=True, run_ctx=run_ctx, goal=goal, project_name="proj",
            ) as span:
                assert isinstance(span, _FakeSpan)
        mock_trace.assert_called_once()
        _, kwargs = mock_trace.call_args
        assert kwargs["name"] == "agent.assistant"
        assert kwargs["project_name"] == "proj"
        assert kwargs["input"]["description"] == "do the thing"
        assert kwargs["input"]["requirements"] == ["be thorough"]

    def test_opik_start_failure_degrades_to_untraced(self) -> None:
        from app.agent_loop_lib.core.types import Goal

        run_ctx = RunContext(role_name="assistant", model="gpt-4")
        with patch("opik.start_as_current_trace", side_effect=RuntimeError("opik down")):
            with maybe_start_run_trace(enabled=True, run_ctx=run_ctx, goal=Goal(description="x")) as span:
                assert isinstance(span, SimpleNamespace)


class TestMaybeStartToolSpan:
    def test_noop_when_disabled(self) -> None:
        with patch("opik.start_as_current_span") as mock_span:
            with maybe_start_tool_span(enabled=False, name="search", arguments={"q": "x"}):
                pass
        mock_span.assert_not_called()

    def test_opens_span_with_tool_name_and_arguments(self) -> None:
        with patch("opik.start_as_current_span", return_value=_fake_span_cm()) as mock_span:
            with maybe_start_tool_span(enabled=True, name="jira_search", arguments={"query": "bugs"}) as span:
                assert isinstance(span, _FakeSpan)
        _, kwargs = mock_span.call_args
        assert kwargs["name"] == "tool.jira_search"
        assert kwargs["type"] == "tool"
        assert kwargs["input"] == {"name": "jira_search", "arguments": {"query": "bugs"}}

    def test_start_failure_yields_stand_in_and_does_not_raise(self) -> None:
        with patch("opik.start_as_current_span", side_effect=RuntimeError("boom")):
            with maybe_start_tool_span(enabled=True, name="t", arguments={}) as span:
                assert isinstance(span, SimpleNamespace)

    def test_exception_inside_block_still_propagates(self) -> None:
        with patch("opik.start_as_current_span", return_value=_fake_span_cm()):
            with pytest.raises(ValueError):
                with maybe_start_tool_span(enabled=True, name="t", arguments={}):
                    raise ValueError("real tool failure")


class TestRecordToolSpanOutput:
    def test_records_full_tool_output(self) -> None:
        from app.agent_loop_lib.tools.base import ToolOutput

        span = _FakeSpan()
        result = ToolOutput(success=True, data="3 issues found")
        record_tool_span_output(span, result=result)
        assert span.output["success"] is True
        assert span.output["data"] == "3 issues found"
        assert span.output["error"] is None
        assert span.output["sources"] == []

    def test_swallows_exception_from_bad_span(self) -> None:
        from app.agent_loop_lib.tools.base import ToolOutput

        class _BrokenSpan:
            def __setattr__(self, key: str, value: Any) -> None:
                raise RuntimeError("frozen span")

        record_tool_span_output(_BrokenSpan(), result=ToolOutput(success=True, data="x"))  # must not raise


class TestMaybeStartAgentSpan:
    def test_noop_when_disabled(self) -> None:
        from app.agent_loop_lib.core.types import Goal

        with patch("opik.start_as_current_span") as mock_span:
            with maybe_start_agent_span(enabled=False, role_name="researcher", goal=Goal(description="dig in")):
                pass
        mock_span.assert_not_called()

    def test_opens_general_span_named_after_role(self) -> None:
        from app.agent_loop_lib.core.types import Goal

        goal = Goal(description="dig in", constraints=["be fast"])
        with patch("opik.start_as_current_span", return_value=_fake_span_cm()) as mock_span:
            with maybe_start_agent_span(enabled=True, role_name="researcher", goal=goal) as span:
                assert isinstance(span, _FakeSpan)
        _, kwargs = mock_span.call_args
        assert kwargs["name"] == "agent.researcher"
        assert kwargs["type"] == "general"
        assert kwargs["input"]["description"] == "dig in"
        assert kwargs["input"]["constraints"] == ["be fast"]


class TestRecordAgentSpanResult:
    def test_records_full_agent_result(self) -> None:
        from app.agent_loop_lib.core.types import AgentResult, Goal

        span = _FakeSpan()
        result = AgentResult(goal=Goal(description="test"), success=True, output="42")
        record_agent_span_result(span, result=result)
        assert span.output["success"] is True
        assert span.output["output"] == "42"
        assert span.output["goal"]["description"] == "test"


class TestMaybeStartNamedSpan:
    def test_noop_when_disabled(self) -> None:
        with patch("opik.start_as_current_span") as mock_span:
            with maybe_start_named_span(enabled=False, name="respond_pipeline.synthesis"):
                pass
        mock_span.assert_not_called()

    def test_defaults_type_general_and_empty_input(self) -> None:
        with patch("opik.start_as_current_span", return_value=_fake_span_cm()) as mock_span:
            with maybe_start_named_span(enabled=True, name="intent.parse_intent_and_route"):
                pass
        _, kwargs = mock_span.call_args
        assert kwargs["type"] == "general"
        assert kwargs["input"] == {}

    def test_custom_type_and_input_forwarded(self) -> None:
        with patch("opik.start_as_current_span", return_value=_fake_span_cm()) as mock_span:
            with maybe_start_named_span(
                enabled=True, name="x", span_type="llm", span_input={"query": "hi"}, project_name="proj",
            ):
                pass
        _, kwargs = mock_span.call_args
        assert kwargs["type"] == "llm"
        assert kwargs["input"] == {"query": "hi"}
        assert kwargs["project_name"] == "proj"


class TestRecordNamedSpanOutput:
    def test_dict_output_recorded_as_is(self) -> None:
        span = _FakeSpan()
        record_named_span_output(span, {"answer": "42", "citations": 2})
        assert span.output == {"answer": "42", "citations": 2}

    def test_non_dict_output_wrapped(self) -> None:
        span = _FakeSpan()
        record_named_span_output(span, "just text")
        assert span.output == {"output": "just text"}


class TestOpikTracingTransportComplete:
    async def test_creates_llm_span_and_records_output(self) -> None:
        response = _response("The answer is 42.")
        inner = _FakeTransport(response=response)
        transport = OpikTracingTransport(inner, project_name="proj")

        with patch("opik.start_as_current_span", return_value=_fake_span_cm()) as mock_span:
            result = await transport.complete([UserMessage(content="what is the answer?")])

        assert result is response
        _, kwargs = mock_span.call_args
        assert kwargs["name"] == "fake.complete"
        assert kwargs["type"] == "llm"
        assert kwargs["provider"] == "fake"
        assert kwargs["model"] == "fake-model"

    async def test_records_tool_calls_and_usage_on_span(self) -> None:
        from app.agent_loop_lib.core.messages import ToolCall

        response = _response("", tool_calls=[ToolCall(id="1", name="search", arguments={"q": "x"})])
        inner = _FakeTransport(response=response)
        transport = OpikTracingTransport(inner)

        captured_span = {}

        @contextlib.contextmanager
        def _capture(**_kwargs: Any):
            span = _FakeSpan()
            yield span
            captured_span["span"] = span

        with patch("opik.start_as_current_span", side_effect=lambda **kw: _capture(**kw)):
            await transport.complete([UserMessage(content="search something")])

        span = captured_span["span"]
        assert span.output["stop_reason"] == "tool_use"
        assert span.output["content"]["tool_calls"][0]["name"] == "search"
        assert span.usage["prompt_tokens"] == 10
        assert span.usage["completion_tokens"] == 5

    async def test_opik_failure_does_not_break_the_llm_call(self) -> None:
        """Tracing must never be able to take down a real LLM call — see
        module docstring."""
        response = _response("still works")
        inner = _FakeTransport(response=response)
        transport = OpikTracingTransport(inner)

        with patch("opik.start_as_current_span", side_effect=RuntimeError("opik is down")):
            result = await transport.complete([UserMessage(content="hi")])

        assert result is response

    async def test_delegates_kwargs_to_inner_transport(self) -> None:
        response = _response("ok")
        inner = _FakeTransport(response=response)
        transport = OpikTracingTransport(inner)

        with patch("opik.start_as_current_span", return_value=_fake_span_cm()):
            await transport.complete(
                [UserMessage(content="hi")], model="override-model", thinking_budget=1024, effort="high",
            )

        assert inner.complete_calls[0]["model"] == "override-model"
        assert inner.complete_calls[0]["thinking_budget"] == 1024
        assert inner.complete_calls[0]["effort"] == "high"

    async def test_provider_and_model_name_proxy_to_inner(self) -> None:
        inner = _FakeTransport()
        transport = OpikTracingTransport(inner)
        assert transport.provider == "fake"
        assert transport.model_name == "fake-model"


class TestOpikTracingTransportCompleteStructured:
    async def test_creates_span_and_captures_data(self) -> None:
        structured = StructuredResponse(data={"route": "react"}, usage=TokenUsage(input_tokens=3, output_tokens=1))
        inner = _FakeTransport(structured=structured)
        transport = OpikTracingTransport(inner)

        captured_span = {}

        @contextlib.contextmanager
        def _capture(**_kwargs: Any):
            span = _FakeSpan()
            yield span
            captured_span["span"] = span

        with patch("opik.start_as_current_span", side_effect=lambda **kw: _capture(**kw)):
            result = await transport.complete_structured(
                [UserMessage(content="classify")], output_schema={"type": "object"},
            )

        assert result is structured
        assert captured_span["span"].output["data"] == {"route": "react"}


class TestOpikTracingTransportStream:
    async def test_records_final_response_from_stream_complete_event(self) -> None:
        response = _response("streamed answer")
        inner = _FakeTransport(response=response)
        transport = OpikTracingTransport(inner)

        captured_span = {}

        @contextlib.contextmanager
        def _capture(**_kwargs: Any):
            span = _FakeSpan()
            yield span
            captured_span["span"] = span

        with patch("opik.start_as_current_span", side_effect=lambda **kw: _capture(**kw)):
            events = [event async for event in transport.stream([UserMessage(content="hi")])]

        assert any(isinstance(e, StreamCompleteEvent) for e in events)
        content = captured_span["span"].output["content"]
        text_parts = [p["text"] for p in content["content"] if p["type"] == "text"]
        assert "streamed answer" in "".join(text_parts)

    async def test_stream_events_forwarded_unchanged(self) -> None:
        response = _response("done")
        inner = _FakeTransport(response=response)
        transport = OpikTracingTransport(inner)

        with patch("opik.start_as_current_span", return_value=_fake_span_cm()):
            events = [e async for e in transport.stream([UserMessage(content="hi")])]

        assert isinstance(events[0], TextDeltaEvent)
        assert events[0].delta == "hi"
