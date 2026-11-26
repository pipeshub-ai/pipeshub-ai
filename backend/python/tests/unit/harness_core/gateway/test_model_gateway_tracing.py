"""P9 OT-2, OT-3, OT-8, OT-9: the model gateway's GenAI span, on the in-memory recorder and the OTel SDK."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from harness_core.access.types import ENGINE_MODEL_ID
from harness_core.gateway.errors import GatewayRefusal
from harness_core.gateway.model_gateway import ChatStream, ProviderCallFailed
from harness_core.kernel.core.exceptions import TransportError
from harness_core.kernel.core.streaming import StreamEvent, TextDeltaEvent
from harness_core.telemetry.ids import run_root_span_id, run_trace_id
from harness_core.testing.model_gateway import (
    GatewayRig,
    RecordedTransport,
    body_of,
    reply_events,
)
from harness_core.testing.telemetry import otel_in_memory, recording

if TYPE_CHECKING:
    from collections.abc import Callable

    from harness_core.telemetry.memory import FinishedSpan

SENTINEL = "SENTINEL-prompt-content-p9"
ADAPTERS = {"recording": recording, "otel": otel_in_memory}


def body(**extra: Any) -> dict[str, Any]:  # noqa: ANN401
    return {"model": ENGINE_MODEL_ID, "messages": [{"role": "user", "content": f"hi {SENTINEL}"}], **extra}


def rig_with(adapter: str, *, capture: bool = False, transport: RecordedTransport | None = None) -> tuple[GatewayRig, Callable[[], list[FinishedSpan]]]:
    tracer, finished = ADAPTERS[adapter](capture_content=capture)
    rig = GatewayRig(transport=transport or RecordedTransport(), tracer=tracer)
    return rig, finished


@pytest.mark.parametrize("adapter", sorted(ADAPTERS))
async def test_ot2_a_model_call_is_one_chat_span_with_usage_in_the_runs_trace(adapter: str) -> None:
    rig, finished = rig_with(adapter)
    binding = rig.pin()
    _ = await rig.gateway.chat(rig.token(binding), {}, body_of(body()))
    (span,) = finished()
    assert (span.name, span.kind) == ("chat gpt-x", "client")
    attrs = span.attributes
    assert attrs["gen_ai.operation.name"] == "chat"
    assert attrs["gen_ai.provider.name"] == "openai"
    assert attrs["gen_ai.request.model"] == "gpt-x"
    assert attrs["gen_ai.response.model"]
    assert attrs["gen_ai.request.stream"] is False
    assert isinstance(attrs["gen_ai.usage.input_tokens"], int) and isinstance(attrs["gen_ai.usage.output_tokens"], int)
    assert attrs["pipeshub.model_alias"] == ENGINE_MODEL_ID
    assert attrs["pipeshub.run_id"] == binding.run_id and attrs["pipeshub.attempt_id"] == binding.attempt_id
    assert attrs["pipeshub.org_id"] == binding.tenant and attrs["pipeshub.fence"] == binding.fence
    assert span.trace_id == run_trace_id(binding.run_id) and span.parent_id == run_root_span_id(binding.run_id)
    assert span.error_type is None


@pytest.mark.parametrize("adapter", sorted(ADAPTERS))
async def test_ot2_a_streamed_call_ends_its_span_when_the_stream_settles(adapter: str) -> None:
    rig, finished = rig_with(adapter)
    events: list[StreamEvent] = [TextDeltaEvent(delta="hel"), TextDeltaEvent(delta="lo"), reply_events("hello")]
    rig.transport.events = events
    binding = rig.pin()
    outcome = await rig.gateway.chat(rig.token(binding), {}, body_of(body(stream=True)))
    assert isinstance(outcome, ChatStream)
    assert finished() == []
    _ = [chunk async for chunk in outcome.chunks]
    (span,) = finished()
    assert span.attributes["gen_ai.request.stream"] is True
    assert span.attributes["gen_ai.usage.output_tokens"] == 5


@pytest.mark.parametrize("adapter", sorted(ADAPTERS))
async def test_ot3_a_refused_call_has_its_reason_code(adapter: str) -> None:
    rig, finished = rig_with(adapter)
    binding = rig.pin()
    with pytest.raises(GatewayRefusal):
        _ = await rig.gateway.chat(rig.token(binding), {}, body_of(body(model="not-granted")))
    (span,) = finished()
    assert span.error_type == "model_not_granted"
    assert span.attributes["pipeshub.run_id"] == binding.run_id


@pytest.mark.parametrize("adapter", sorted(ADAPTERS))
async def test_ot3_a_refused_token_still_leaves_a_span(adapter: str) -> None:
    rig, finished = rig_with(adapter)
    with pytest.raises(GatewayRefusal):
        _ = await rig.gateway.chat(None, {}, body_of(body()))
    (span,) = finished()
    assert span.error_type == "token_missing"


@pytest.mark.parametrize("adapter", sorted(ADAPTERS))
async def test_ot3_a_provider_failure_ends_the_span_failed(adapter: str) -> None:
    rig, finished = rig_with(adapter, transport=RecordedTransport(fail=TransportError("down", status_code=503)))
    binding = rig.pin()
    with pytest.raises(ProviderCallFailed):
        _ = await rig.gateway.chat(rig.token(binding), {}, body_of(body()))
    (span,) = finished()
    assert span.error_type is not None and span.error_type != "ok"


@pytest.mark.parametrize("adapter", sorted(ADAPTERS))
async def test_ot8_no_content_is_recorded_by_default(adapter: str) -> None:
    rig, finished = rig_with(adapter)
    binding = rig.pin()
    _ = await rig.gateway.chat(rig.token(binding), {}, body_of(body()))
    (span,) = finished()
    assert "gen_ai.input.messages" not in span.attributes and "gen_ai.output.messages" not in span.attributes
    assert SENTINEL not in repr(span)


@pytest.mark.parametrize("adapter", sorted(ADAPTERS))
async def test_ot9_content_is_recorded_when_the_operator_opts_in(adapter: str) -> None:
    rig, finished = rig_with(adapter, capture=True)
    binding = rig.pin()
    _ = await rig.gateway.chat(rig.token(binding), {}, body_of(body()))
    (span,) = finished()
    assert SENTINEL in str(span.attributes["gen_ai.input.messages"])
    assert '"parts": [{"type": "text"' in str(span.attributes["gen_ai.input.messages"])
    assert "hello" in str(span.attributes["gen_ai.output.messages"])
