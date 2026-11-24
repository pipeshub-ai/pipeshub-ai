"""A failed span, trace or LangChain run reaches Opik as why it failed (class, provider code, type, status, frames),
never as the error's message: a provider's answer can quote the prompt, an email or a key, and Opik is a third party.

Runs the real Opik SDK (`start_as_current_span`, `OpikTracer`) against a fake upload client."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any
from unittest.mock import MagicMock, patch

import openai
import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage

from app.agent_loop_lib.transport import opik_tracing
from tests.support.provider_errors import (
    MARK,
    SECRET,
    SENTINELS,
    openai_error,
    transport_error,
)

pytestmark = pytest.mark.asyncio

_BODY = {"message": f"{MARK} key {SECRET}", "type": "invalid_request_error", "param": None, "code": "content_filter"}
_REASON = "bad_request: content_filter (BadRequestError, HTTP 400)"


def _leaks(value: Any) -> list[str]:  # noqa: ANN401
    text = json.dumps(value, default=str)
    return [s for s in (*SENTINELS, "F30SECRET") if s in text]


class _FakeOpikClient:
    """What Opik would upload (MagicMock refuses `__internal_api__*__` names as magic methods)."""

    def __init__(self) -> None:
        self.config = MagicMock(log_start_trace_span=False)
        self.sent: list[dict] = []

    def __internal_api__span__(self, **kwargs: Any) -> None:  # noqa: ANN401
        self.sent.append(kwargs)

    def __internal_api__trace__(self, **kwargs: Any) -> None:  # noqa: ANN401
        self.sent.append(kwargs)

    def flush(self) -> None:
        pass


@pytest.fixture
def client() -> Any:  # noqa: ANN401
    fake = _FakeOpikClient()
    with patch("opik.api_objects.opik_client.get_global_client", return_value=fake), \
         patch("opik.get_global_client", return_value=fake):
        yield fake


def _error_infos(client: _FakeOpikClient) -> list[dict]:
    return [sent["error_info"] for sent in client.sent if sent.get("error_info")]


class TestASpanThatFails:
    async def test_it_is_marked_failed_with_why_and_a_frames_only_stack(self, client: _FakeOpikClient,
                                                                         caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.DEBUG)
        with pytest.raises(openai.BadRequestError):
            with opik_tracing.maybe_start_named_span(enabled=True, name="llm"):
                raise openai_error(400, _BODY)

        (info,) = _error_infos(client)
        assert (info["exception_type"], info["message"]) == ("BadRequestError", _REASON)
        assert "BadRequestError: <message omitted>" in info["traceback"]
        assert _leaks(client.sent) == []
        # Opik's own logger used to print the message and the traceback at ERROR.
        assert _leaks([r.getMessage() + (r.exc_text or "") for r in caplog.records]) == []

    async def test_through_the_transport_the_reason_names_the_providers_error(self, client: _FakeOpikClient) -> None:
        with pytest.raises(Exception):  # noqa: B017
            with opik_tracing.maybe_start_named_span(enabled=True, name="llm"):
                raise transport_error(400, _BODY)
        (info,) = _error_infos(client)
        assert (info["exception_type"], info["message"]) == ("TransportError", _REASON)
        assert _leaks(client.sent) == []

    async def test_an_agent_runs_trace_is_marked_failed_too(self, client: _FakeOpikClient) -> None:
        """A run opens its trace through `maybe_start_run_trace`, so the trace carries the marker as well as the span."""
        from types import SimpleNamespace

        ctx = SimpleNamespace(parent_run_id=None, role_name="r", run_id="x", trace_id="t", model="m")
        with pytest.raises(openai.BadRequestError):
            with opik_tracing.maybe_start_run_trace(enabled=True, run_ctx=ctx, goal={"g": 1}):
                with opik_tracing.maybe_start_named_span(enabled=True, name="llm"):
                    raise openai_error(400, _BODY)

        infos = _error_infos(client)
        assert len(infos) == 2 and {info["message"] for info in infos} == {_REASON}
        assert _leaks(client.sent) == []

    async def test_a_success_has_no_error(self, client: _FakeOpikClient) -> None:
        with opik_tracing.maybe_start_named_span(enabled=True, name="llm") as span:
            opik_tracing.record_named_span_output(span, {"answer": "ok"})
        assert client.sent and _error_infos(client) == []

    async def test_a_cancelled_call_is_marked_cancelled(self, client: _FakeOpikClient) -> None:
        with pytest.raises(asyncio.CancelledError):
            with opik_tracing.maybe_start_named_span(enabled=True, name="llm"):
                raise asyncio.CancelledError
        (info,) = _error_infos(client)
        assert info["message"] == "cancelled (CancelledError)"

    async def test_a_stream_closed_early_is_not_a_failure(self, client: _FakeOpikClient) -> None:
        from app.agent_loop_lib.core.streaming import TextDeltaEvent

        inner = MagicMock(provider="p", model_name="m")

        async def _stream(**_: Any) -> Any:  # noqa: ANN401
            yield TextDeltaEvent(delta="a")
            yield TextDeltaEvent(delta="b")

        inner.stream = _stream
        stream = opik_tracing.OpikTracingTransport(inner).stream(messages=[])
        await stream.__anext__()
        await stream.aclose()

        assert client.sent and _error_infos(client) == []

    async def test_a_close_that_fails_never_hides_the_callers_error(self) -> None:
        cm = MagicMock()
        cm.__enter__.return_value = MagicMock()
        cm.__exit__.side_effect = RuntimeError("opik down")
        with pytest.raises(openai.BadRequestError):
            with opik_tracing._guarded_cm(lambda: cm, "span"):
                raise openai_error(400, _BODY)
        cm.__exit__.assert_called_once_with(None, None, None)


class TestALangChainRunThatFails:
    @staticmethod
    def _failing_model() -> BaseChatModel:
        class _Failing(BaseChatModel):
            @property
            def _llm_type(self) -> str:
                return "failing"

            def _generate(self, messages, stop=None, run_manager=None, **kwargs):  # noqa: ANN202
                raise openai_error(400, _BODY)

        return _Failing()

    async def test_the_agent_loops_callback_uploads_only_why(self, client: _FakeOpikClient,
                                                             monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPIK_API_KEY", "test-key")
        monkeypatch.setenv("OPIK_TRACK_DISABLE", "false")  # tests/conftest.py turns tracking off for every other test
        callbacks = opik_tracing.build_langchain_opik_callbacks(project_name="p")
        with pytest.raises(openai.BadRequestError):
            self._failing_model().invoke([HumanMessage(content="q")], config={"callbacks": callbacks})

        (info,) = _error_infos(client)
        assert info["traceback"].startswith(_REASON)
        assert "<message omitted>" in info["traceback"]
        assert _leaks(client.sent) == []

    async def test_the_shared_tracer_is_the_same_class(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.agent_loop_lib.transport.opik_langchain import TextFreeOpikTracer
        from app.utils import opik as shared

        monkeypatch.setenv("OPIK_API_KEY", "test-key")
        assert isinstance(shared._build_tracer(), TextFreeOpikTracer)
        assert [type(c) for c in opik_tracing.build_langchain_opik_callbacks()] == [TextFreeOpikTracer]

    async def test_no_tracer_when_opik_is_not_configured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.utils import opik as shared

        monkeypatch.delenv("OPIK_API_KEY", raising=False)
        monkeypatch.delenv("OPIK_URL_OVERRIDE", raising=False)
        assert shared._build_tracer() is None

    async def test_the_run_error_never_fails_to_render(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.agent_loop_lib.transport import opik_langchain

        def _broken(_exc: BaseException) -> str:
            raise RuntimeError("stack walk failed")

        monkeypatch.setattr(opik_langchain, "stack_text_without_messages", _broken)
        assert opik_langchain.TextFreeOpikTracer._get_stacktrace(openai_error(400, _BODY)) == _REASON


async def test_the_finalizers_span_records_why() -> None:
    """respond.py: a crash after the run (the answer pipeline) is recorded on its span as why."""
    from app.agents.agent_loop.hooks.citations import CitationCollector
    from app.agents.agent_loop.respond import AnswerFinalizer
    from tests.unit.agents.adapter.conftest import make_context

    context = make_context()
    finalizer = AnswerFinalizer(context, CitationCollector(context))
    span = MagicMock()

    async def _crash(*_: Any, **__: Any) -> Any:  # noqa: ANN401
        raise openai_error(400, _BODY)

    class _Sink:
        async def write(self, _event: dict) -> bool:
            return True

    with patch("app.agents.agent_loop.respond.maybe_start_named_span") as start, \
         patch.object(finalizer, "_run_success_path", new=_crash):
        start.return_value.__enter__.return_value = span
        await finalizer.run(agent_success=True, agent_error=None, agent_output="x", event_sink=_Sink())

    assert span.output == {"error": _REASON}
