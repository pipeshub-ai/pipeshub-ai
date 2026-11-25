"""SEC4-9: the model gateway masks the key a call resolved in what its span keeps of the call, with no vector shape."""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING

import pytest

from harness_core.access.types import ENGINE_MODEL_ID
from harness_core.gateway.model_gateway import ChatStream
from harness_core.kernel.core.streaming import TextDeltaEvent
from harness_core.security.secrets import SecretSet
from harness_core.security.token_patterns import SECRET_MASK
from harness_core.testing.canary import (
    Canaries,
    Surface,
    assert_no_canary,
    secret_canary,
)
from harness_core.testing.ids import SeededIds
from harness_core.testing.model_gateway import (
    ROUTE,
    GatewayRig,
    RecordedTransport,
    body_of,
    org_key_route,
    reply_events,
)
from harness_core.testing.telemetry import otel_in_memory, recording

if TYPE_CHECKING:
    from harness_core.kernel.core.streaming import StreamEvent

IDS = SeededIds(4099)
KEY = secret_canary(IDS.uuid4)
ADAPTERS = {"recording": recording, "otel": otel_in_memory}


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("adapter", sorted(ADAPTERS))
async def test_sec4_9_a_key_the_model_echoes_is_masked_in_the_span(adapter: str, stream: bool) -> None:
    tracer, finished = ADAPTERS[adapter](capture_content=True)
    events: list[StreamEvent] = [TextDeltaEvent(delta=f"my key is {KEY}"), reply_events(f"my key is {KEY}")]
    rig = GatewayRig(transport=RecordedTransport(events), tracer=tracer)
    binding = rig.pin()
    rig.resolver.add(
        binding.tenant, ROUTE, dataclasses.replace(org_key_route(ROUTE, rig.transport), secrets=SecretSet.of(KEY))
    )
    body = {"model": ENGINE_MODEL_ID, "messages": [{"role": "user", "content": f"is {KEY} valid?"}], "stream": stream}

    outcome = await rig.gateway.chat(rig.token(binding), {}, body_of(body))
    if isinstance(outcome, ChatStream):
        _ = [chunk async for chunk in outcome.chunks]

    (span,) = finished()
    assert SECRET_MASK in str(span.attributes["gen_ai.output.messages"])
    assert SECRET_MASK in str(span.attributes["gen_ai.input.messages"])
    assert_no_canary([Surface("model span", span)], Canaries(secrets=(KEY,)))
