"""OB-01 (C9.03): GenAI spans carry cache-token counts, loop runs are traced like engine runs, and an engine's own
OpenTelemetry joins the run's trace through the launch environment (W3C ``TRACEPARENT``)."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

import pytest

from harness_core.access.types import ENGINE_MODEL_ID
from harness_core.kernel.core.responses import TokenUsage
from harness_core.telemetry import NOOP_TRACER
from harness_core.telemetry.ids import run_root_span_id, run_trace_id
from harness_core.testing.engines.durable import EngineWorld, ScriptedEngineAdapter
from harness_core.testing.model_gateway import (
    GatewayRig,
    RecordedTransport,
    body_of,
    reply_events,
)
from harness_core.testing.telemetry import otel_in_memory, recording
from tests.unit.harness_core.durable.support import RUN, World, reply, scripted

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

    from harness_core.engines.events import EngineState
    from harness_core.engines.ports import EngineSession
    from harness_core.engines.types import EngineLaunch
    from harness_core.kernel.durable import DeltaKind, ModelCall, SampledTurn
    from harness_core.sandbox.ports import SandboxHandle
    from harness_core.testing.durable import ScriptedModelPort

ADAPTERS = {"recording": recording, "otel": otel_in_memory}
CACHE_READ = "gen_ai.usage.cache_read.input_tokens"
CACHE_CREATION = "gen_ai.usage.cache_creation.input_tokens"


@pytest.mark.parametrize("adapter", sorted(ADAPTERS))
async def test_ob01_a_model_gateway_chat_span_carries_cache_tokens(adapter: str) -> None:
    tracer, finished = ADAPTERS[adapter]()
    usage = TokenUsage(input_tokens=1200, output_tokens=40, cache_read_tokens=900, cache_write_tokens=200)
    rig = GatewayRig(transport=RecordedTransport([reply_events("hi", usage=usage)]), tracer=tracer)
    binding = rig.pin()
    body = {"model": ENGINE_MODEL_ID, "messages": [{"role": "user", "content": "hi"}]}
    _ = await rig.gateway.chat(rig.token(binding), {}, body_of(body))
    (span,) = finished()
    assert span.attributes["gen_ai.usage.input_tokens"] == 1200
    assert span.attributes[CACHE_READ] == 900
    assert span.attributes[CACHE_CREATION] == 200


class _CachedPort:
    """The scripted port, every response reporting cache reads and writes as a provider would."""

    def __init__(self, inner: ScriptedModelPort) -> None:
        self.inner = inner

    async def sample(self, call: ModelCall, on_delta: Callable[[DeltaKind, str], Awaitable[None]]) -> SampledTurn:
        sampled = await self.inner.sample(call, on_delta)
        return replace(
            sampled,
            usage=TokenUsage(input_tokens=500, output_tokens=12, cache_read_tokens=300, cache_write_tokens=50),
            model="scripted-model",
        )

    def __getattr__(self, name: str) -> object:
        return getattr(self.inner, name)


async def test_ob01_a_loop_run_is_one_invoke_agent_span_parenting_its_model_and_tool_calls() -> None:
    tracer, finished = recording()
    world = World()
    world.admit()
    _, port = scripted(reply("", ("lookup", {"q": "x"})), reply("done"))
    done = await world.run(world.kernel(_CachedPort(port), tracer=tracer))  # type: ignore[arg-type]
    assert done.status == "completed"
    spans = finished()
    (run,) = [s for s in spans if s.name.startswith("invoke_agent")]
    assert run.trace_id == run_trace_id(RUN) and run.parent_id == run_root_span_id(RUN)
    assert run.attributes["gen_ai.operation.name"] == "invoke_agent"
    assert run.attributes["gen_ai.agent.name"] == "loop"
    assert run.attributes["gen_ai.conversation.id"] == run.attributes["pipeshub.session_id"]
    assert run.attributes["pipeshub.disposition"] == "completed" and run.error_type is None
    chats = [s for s in spans if s.attributes.get("gen_ai.operation.name") == "chat"]
    assert len(chats) == 2
    for chat in chats:
        assert chat.parent_id == run.span_id and chat.kind == "client"
        assert chat.attributes["gen_ai.response.model"] == "scripted-model"
        assert (chat.attributes["gen_ai.usage.input_tokens"], chat.attributes["gen_ai.usage.output_tokens"]) == (500, 12)
        assert (chat.attributes[CACHE_READ], chat.attributes[CACHE_CREATION]) == (300, 50)
    (tool,) = [s for s in spans if s.name == "execute_tool lookup"]
    assert tool.parent_id == run.span_id
    assert tool.attributes["gen_ai.tool.name"] == "lookup"
    assert tool.attributes["gen_ai.tool.call.id"]
    assert tool.attributes["pipeshub.attempt_id"] == run.attributes["pipeshub.attempt_id"]


async def test_ob01_a_loop_write_is_an_execute_tool_span_too() -> None:
    tracer, finished = recording()
    world = World()
    world.admit()
    _, port = scripted(reply("", ("create_issue", {"title": "t"})), reply("done"))
    done = await world.run(world.kernel(port, tracer=tracer))
    assert done.status == "completed"
    (tool,) = [s for s in finished() if s.name == "execute_tool create_issue"]
    assert tool.trace_id == run_trace_id(RUN)
    assert tool.error_type is None


async def test_ob01_a_failed_model_call_ends_its_chat_span_failed() -> None:
    tracer, finished = recording()
    world = World()
    world.admit()
    _, port = scripted()  # no turns: the scripted model fails the first call
    _ = await world.run(world.kernel(port, tracer=tracer))
    chats = [s for s in finished() if s.attributes.get("gen_ai.operation.name") == "chat"]
    assert chats and all(c.error_type is not None for c in chats)


class _LaunchRecorder(ScriptedEngineAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.envs: list[dict[str, str]] = []

    async def open(  # type: ignore[override]
        self,
        sandbox: SandboxHandle,
        launch: EngineLaunch,
        restore: EngineState | None,
        *,
        correlation: Mapping[str, str] | None = None,
    ) -> EngineSession:
        self.envs.append(dict(launch.env))
        return await super().open(sandbox, launch, restore, correlation=correlation)


async def test_ob01_an_engine_launch_carries_the_attempts_trace_context() -> None:
    tracer, finished = recording()
    adapter = _LaunchRecorder()
    world = EngineWorld(tracer=tracer, adapter=adapter)
    world.admit("run-ob01", "say done")
    assert (await world.attempt("run-ob01")).status == "completed"
    (attempt,) = [s for s in finished() if s.name.startswith("invoke_agent ")]
    (env,) = adapter.envs
    assert env["TRACEPARENT"] == f"00-{attempt.trace_id}-{attempt.span_id}-01"
    resource = dict(pair.split("=", 1) for pair in env["OTEL_RESOURCE_ATTRIBUTES"].split(","))
    assert resource["pipeshub.run_id"] == "run-ob01"
    assert resource["pipeshub.attempt_id"] == attempt.attributes["pipeshub.attempt_id"]
    assert "pipeshub.org_id" not in resource, "the org id stays out of what the engine's process reads"


async def test_ob01_with_tracing_off_the_launch_env_is_unchanged() -> None:
    adapter = _LaunchRecorder()
    world = EngineWorld(tracer=NOOP_TRACER, adapter=adapter)
    world.admit("run-ob01-off", "say done")
    assert (await world.attempt("run-ob01-off")).status == "completed"
    (env,) = adapter.envs
    assert "TRACEPARENT" not in env and "OTEL_RESOURCE_ATTRIBUTES" not in env
