"""P9 OT-5, OT-6: an engine attempt is one ``invoke_agent`` span; its sandbox acquire and tool calls are in it."""

from __future__ import annotations

from harness_core.telemetry.ids import run_root_span_id, run_trace_id
from harness_core.testing.engines.durable import EngineWorld
from harness_core.testing.telemetry import recording

RUN = "run-traced"


async def test_ot6_an_attempt_span_parents_its_acquire_and_tool_calls() -> None:
    tracer, finished = recording()
    world = EngineWorld(tracer=tracer)
    world.admit(RUN, "write out/a.md hello\ntool lookup {\"q\": \"x\"}\nsay done")
    disposition = await world.attempt(RUN)
    assert disposition.status == "completed"
    spans = finished()
    (attempt,) = [s for s in spans if s.name.startswith("invoke_agent ")]
    assert attempt.trace_id == run_trace_id(RUN) and attempt.parent_id == run_root_span_id(RUN)
    assert attempt.attributes["gen_ai.operation.name"] == "invoke_agent"
    assert attempt.attributes["gen_ai.conversation.id"] == attempt.attributes["pipeshub.session_id"]
    assert attempt.attributes["pipeshub.disposition"] == "completed"
    assert attempt.error_type is None
    (acquire,) = [s for s in spans if s.name == "pipeshub.sandbox.acquire"]
    assert acquire.parent_id == attempt.span_id and acquire.kind == "client"
    assert acquire.attributes["pipeshub.sandbox.id"]
    assert acquire.attributes["pipeshub.attempt_id"] == attempt.attributes["pipeshub.attempt_id"]
    tools = [s for s in spans if s.name == "execute_tool lookup"]
    assert len(tools) == 1 and tools[0].trace_id == attempt.trace_id


async def test_ot5_a_refused_acquire_marks_its_span() -> None:
    tracer, finished = recording()
    world = EngineWorld(tracer=tracer)

    async def refuse(*_a: object, **_k: object) -> object:
        raise RuntimeError("the broker refused")

    world.broker.acquire = refuse  # type: ignore[method-assign]
    world.admit(RUN, "say hello")
    _ = await world.attempt(RUN)
    acquire = [s for s in finished() if s.name == "pipeshub.sandbox.acquire"]
    assert acquire and acquire[0].error_type == "RuntimeError"
    (attempt,) = [s for s in finished() if s.name.startswith("invoke_agent ")]
    assert attempt.attributes["pipeshub.disposition"] != "completed"
