"""Contract suite for ``ToolEndpoint``: what any transport of the tool gateway must answer.

Subclass ``ToolEndpointContract`` and provide an ``endpoint_harness`` fixture
(``ToolEndpointHarness``): ``serve`` puts a ``RunToolEndpoint`` the suite built
behind the transport under test and returns the ``ToolEndpoint`` a client sees
(the endpoint itself in process; an MCP client over HTTP for the transport).
The suite builds the endpoint over an in-memory catalog, effect log, citation
sink and approval hold, so a failure names the transport, not the tools.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

import pytest

from harness_core.effects import (
    EffectCall,
    EffectRunner,
    EffectUnknown,
    RequestEffectLog,
    ToolExecution,
    args_digest,
)
from harness_core.engines.errors import GrantTooLarge
from harness_core.engines.types import EngineCapabilities
from harness_core.gateway.endpoint import RUN_CALL_CONCURRENCY, RunToolEndpoint
from harness_core.gateway.errors import BindingMismatch
from harness_core.gateway.memory import InMemoryCitationSink
from harness_core.gateway.types import GatewayClaims, RunAttemptBinding, grant_digest
from harness_core.invoker import CatalogTool, InMemoryToolCatalog, InvocationScope
from harness_core.kernel.core.messages import ToolCall
from harness_core.kernel.core.tool_schema import ToolSchema
from harness_core.security.secrets import NO_SECRETS
from harness_core.telemetry.memory import NOOP_TRACER
from harness_core.telemetry.metrics import NOOP_GATEWAY_METRICS
from harness_core.testing.contracts.tool_catalog import offered
from harness_core.testing.ids import SeededIds

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

    from pydantic import JsonValue

    from harness_core.effects import (
        ApprovalDecision,
        ApprovalHold,
        ApprovalPolicy,
        HoldDecision,
        ToolContext,
        ToolEffect,
    )
    from harness_core.engines.native_policy import NativeCallPolicy
    from harness_core.gateway.disclosure import LazyDisclosure
    from harness_core.gateway.ports import ToolEndpoint
    from harness_core.gateway.types import GatewayBinding
    from harness_core.interactions.connect_wait import BridgeConnectWait
    from harness_core.security.secrets import CallSecrets
    from harness_core.telemetry.metrics import GatewayMetrics
    from harness_core.telemetry.ports import HarnessTracer

_ARGS = {"type": "object", "properties": {"q": {"type": "string", "description": "query"}}}

LOOKUP = offered("lookup")
SEARCH = offered("search")
CREATE = offered("create", "write")
TRANSFER = offered("transfer", "write")
EXPLODE = offered("explode", "write")
REJECT = offered("reject", "write")
DELETE = offered("delete", "write")
OFFERED = (LOOKUP, SEARCH, CREATE, TRANSFER, EXPLODE, REJECT, DELETE)
GRANTED = tuple(t for t in OFFERED if t is not DELETE)

BINDING = RunAttemptBinding(
    tenant="org-1",
    scope="session-1",
    run_id="run-1",
    parent_run_id="run-0",
    attempt_id="run-1.a2",
    fence=2,
    tool_ids_digest=grant_digest(t.effect.tool_id for t in GRANTED),
    routes_digest=grant_digest(()),
)
EARLIER_ATTEMPT = dataclasses.replace(BINDING, attempt_id="run-1.a1", fence=1)
SCOPE = InvocationScope(BINDING.run_id, BINDING.tenant, BINDING.attempt_id)
SECRET = "sk-ant-api03-" + "A" * 40


def claims_for(binding: GatewayBinding) -> GatewayClaims:
    return GatewayClaims(binding, kid="contract", expires_at=4_102_444_800)


class ScriptedHold:
    """Decides after ``after_s`` (``None``: never decides)."""

    def __init__(self, decision: HoldDecision | None, after_s: float = 0.0) -> None:
        self._decision: HoldDecision | None = decision
        self._after_s = after_s
        self.calls = 0

    async def hold(self, call: EffectCall) -> HoldDecision | None:
        self.calls += 1
        if self._decision is None:
            await asyncio.Event().wait()
        await asyncio.sleep(self._after_s)
        return self._decision


@dataclass
class EndpointWorld:
    """The endpoint the suite built, and what its tools did."""

    endpoint: RunToolEndpoint
    log: RequestEffectLog
    citations: InMemoryCitationSink
    runs: list[str] = field(default_factory=list[str])


def build_endpoint(
    *,
    approvals: Mapping[str, ApprovalDecision] | None = None,
    hold: ApprovalHold | None = None,
    max_tools: int | None = None,
    hold_s: float = 0.4,
    progress_every_s: float = 0.05,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    binding: GatewayBinding = BINDING,
    tracer: HarnessTracer = NOOP_TRACER,
    narrowed: bool = True,
    secrets: CallSecrets = NO_SECRETS,
    disclosure: LazyDisclosure | None = None,
    guard: Callable[[ApprovalPolicy], ApprovalPolicy] | None = None,
    log: RequestEffectLog | None = None,
    needs_authentication: frozenset[str] = frozenset(),
    spec_tool_names: bool = False,
    asks: bool = False,
    connect: BridgeConnectWait | None = None,
    dry_run: bool = False,
    native: NativeCallPolicy | None = None,
    metrics: GatewayMetrics = NOOP_GATEWAY_METRICS,
    max_concurrent_calls: int = RUN_CALL_CONCURRENCY,
) -> EndpointWorld:
    """``guard`` narrows the approval policy (a flow rule over a ledger); ``log``: an effect log of the caller's.
    ``spec_tool_names``: an engine shown OWD-7's spec names; ``asks``: the run has a person to ask; ``dry_run``: the
    run's dry-run gate (G-03); ``native``: the run's policy for the engine's own tools (G-25)."""
    log, citations = log or RequestEffectLog(), InMemoryCitationSink()
    runs: list[str] = []

    def tool(name: str):  # noqa: ANN202 - a ToolFn
        async def execute(call: ToolCall, ctx: ToolContext) -> ToolExecution:
            runs.append(name)
            q = str(call.arguments.get("q", ""))
            match name:
                case "search":
                    first = citations.ref_for(f"https://kb.example/{q}/1")
                    second = citations.ref_for(f"https://kb.example/{q}/2")
                    text = f"Revenue grew [{first}]; churn fell [{second}]; see also ref99."
                    return ToolExecution(status="ok", content=text)
                case "explode":
                    raise EffectUnknown("connection reset after the request was sent")
                case "reject":
                    return ToolExecution(status="error", content="project not found")
                case "create" | "transfer":
                    return ToolExecution(status="ok", content={"created": ctx.invocation_id})
                case _:
                    return ToolExecution(status="ok", content=f"{name}:{q}")

        return execute

    decisions: dict[str, ApprovalDecision] = dict(approvals or {})

    def policy(call: ToolCall, effect: ToolEffect) -> ApprovalDecision:
        return decisions.get(call.name, "allow")

    catalog = InMemoryToolCatalog(
        [CatalogTool(ToolSchema(name=t.name, description=f"The {t.name} tool.", input_schema=_ARGS), t.effect, tool(t.name)) for t in OFFERED],
        # Not narrowed (a test-only path, P9 AU-6): the catalog serves every offered tool, granted or not.
        [t.effect.tool_id for t in (GRANTED if narrowed else OFFERED)],
    )
    capabilities = (
        None
        if max_tools is None and not spec_tool_names
        else EngineCapabilities(
            can_steer=False,
            native_skills=False,
            sub_agents=False,
            model_apis=frozenset(),
            max_tools=max_tools,
            drive="port",
            spec_tool_names=spec_tool_names,
        )
    )
    endpoint = RunToolEndpoint(
        binding=binding,
        catalog=catalog,
        effects=EffectRunner(
            log, policy if guard is None else guard(policy), hold=hold, secrets=secrets, dry_run=dry_run
        ),
        scope=SCOPE,
        ids=SeededIds(),
        citations=citations,
        capabilities=capabilities,
        hold_s=hold_s,
        progress_every_s=progress_every_s,
        sleep=sleep,
        tracer=tracer,
        disclosure=disclosure,
        needs_authentication=needs_authentication,
        asks=asks,
        connect=connect,
        native=native,
        metrics=metrics,
        max_concurrent_calls=max_concurrent_calls,
    )
    return EndpointWorld(endpoint, log, citations, runs)


class ToolEndpointHarness(Protocol):
    async def serve(self, endpoint: RunToolEndpoint) -> ToolEndpoint: ...


class ToolEndpointContract:
    @pytest.fixture
    def endpoint_harness(self) -> ToolEndpointHarness:
        raise NotImplementedError("provide an endpoint_harness fixture")

    async def _serve(self, harness: ToolEndpointHarness, **options: object) -> tuple[ToolEndpoint, EndpointWorld]:
        world = build_endpoint(**options)  # type: ignore[arg-type]
        return await harness.serve(world.endpoint), world

    async def test_ep1_tools_list_is_exactly_the_grant(self, endpoint_harness: ToolEndpointHarness) -> None:
        endpoint, _ = await self._serve(endpoint_harness)
        tools = await endpoint.list_tools(claims_for(BINDING))
        assert sorted(t.name for t in tools) == sorted(t.name for t in GRANTED)
        lookup = next(t for t in tools if t.name == "lookup")
        assert lookup.description == "The lookup tool."
        assert dict(lookup.input_schema) == _ARGS

    async def test_ep2_a_grant_above_the_engine_limit_is_refused_never_truncated(
        self, endpoint_harness: ToolEndpointHarness
    ) -> None:
        with pytest.raises(GrantTooLarge):
            build_endpoint(max_tools=len(GRANTED) - 1)
        endpoint, _ = await self._serve(endpoint_harness, max_tools=len(GRANTED))
        assert len(await endpoint.list_tools(claims_for(BINDING))) == len(GRANTED)

    async def test_ep3_a_granted_read_executes(self, endpoint_harness: ToolEndpointHarness) -> None:
        endpoint, world = await self._serve(endpoint_harness)
        result = await endpoint.call_tool(claims_for(BINDING), "lookup", {"q": "revenue"})
        assert (result.text, result.is_error, result.disposition) == ("lookup:revenue", False, "executed")
        assert world.runs == ["lookup"]

    async def test_ep4_an_ungranted_tool_is_refused_and_never_runs(
        self, endpoint_harness: ToolEndpointHarness
    ) -> None:
        endpoint, world = await self._serve(endpoint_harness)
        for name in ("delete", "ghost"):
            result = await endpoint.call_tool(claims_for(BINDING), name, {"q": "x"})
            assert (result.is_error, result.disposition, result.reason_code) == (True, "refused", "not_granted")
            assert result.retryable is False
        assert world.runs == []
        assert await world.log.find(EffectCall(SCOPE.run_id, "i", ToolCall(id="i", name="delete", arguments={"q": "x"}), DELETE.effect, args_digest({"q": "x"})).key) is None

    async def test_ep5_a_credential_in_the_arguments_is_refused_and_never_runs(
        self, endpoint_harness: ToolEndpointHarness, caplog: pytest.LogCaptureFixture
    ) -> None:
        endpoint, world = await self._serve(endpoint_harness)
        with caplog.at_level(logging.INFO):
            result = await endpoint.call_tool(
                claims_for(BINDING), "create", {"q": "note", "body": {"token": SECRET}}
            )
        assert (result.is_error, result.disposition, result.reason_code) == (True, "refused", "forbidden_arguments")
        assert "anthropic_key" in result.text
        assert world.runs == []
        assert SECRET not in caplog.text

    async def test_ep6_an_identical_write_recall_answers_from_the_log(
        self, endpoint_harness: ToolEndpointHarness
    ) -> None:
        endpoint, world = await self._serve(endpoint_harness)
        first = await endpoint.call_tool(claims_for(BINDING), "create", {"q": "a"})
        again = await endpoint.call_tool(claims_for(BINDING), "create", {"q": "a"})
        assert (first.disposition, again.disposition) == ("executed", "recorded")
        assert again.text == first.text and again.invocation_id == first.invocation_id
        assert world.runs == ["create"]

    async def test_ep7_a_policy_denial_is_an_error(self, endpoint_harness: ToolEndpointHarness) -> None:
        endpoint, world = await self._serve(endpoint_harness, approvals={"transfer": "deny"})
        result = await endpoint.call_tool(claims_for(BINDING), "transfer", {"q": "1"})
        assert (result.is_error, result.disposition, result.retryable) == (True, "denied", False)
        assert world.runs == []

    async def test_ep8_approval_without_a_hold_is_deferred(self, endpoint_harness: ToolEndpointHarness) -> None:
        endpoint, world = await self._serve(endpoint_harness, approvals={"transfer": "ask"})
        result = await endpoint.call_tool(claims_for(BINDING), "transfer", {"q": "1"})
        assert (result.is_error, result.disposition) == (False, "deferred")
        assert result.interaction_id == f"ix.approve.{result.invocation_id}"
        assert result.structured()["deferred"] is True
        assert world.runs == []

    async def test_ep9_an_approval_within_the_hold_executes_with_progress(
        self, endpoint_harness: ToolEndpointHarness
    ) -> None:
        hold = ScriptedHold("approved", after_s=0.25)
        endpoint, world = await self._serve(endpoint_harness, approvals={"transfer": "ask"}, hold=hold)
        ticks: list[float] = []

        async def progress(elapsed: float) -> None:
            ticks.append(elapsed)

        result = await endpoint.call_tool(claims_for(BINDING), "transfer", {"q": "1"}, progress=progress)
        assert (result.is_error, result.disposition) == (False, "executed")
        assert world.runs == ["transfer"]
        assert ticks, "no progress while the call was held"

    async def test_ep10_an_undecided_hold_is_bounded_then_deferred(
        self, endpoint_harness: ToolEndpointHarness
    ) -> None:
        endpoint, world = await self._serve(
            endpoint_harness, approvals={"transfer": "ask"}, hold=ScriptedHold(None), hold_s=0.2
        )
        result = await endpoint.call_tool(claims_for(BINDING), "transfer", {"q": "1"})
        assert result.disposition == "deferred"
        assert world.runs == []

    async def test_ep11_a_denial_by_the_approver_is_an_error(self, endpoint_harness: ToolEndpointHarness) -> None:
        endpoint, world = await self._serve(
            endpoint_harness, approvals={"transfer": "ask"}, hold=ScriptedHold("denied")
        )
        result = await endpoint.call_tool(claims_for(BINDING), "transfer", {"q": "1"})
        assert (result.is_error, result.disposition) == (True, "denied")
        assert world.runs == []

    async def test_ep12_an_unknown_effect_is_never_retried(self, endpoint_harness: ToolEndpointHarness) -> None:
        endpoint, world = await self._serve(endpoint_harness)
        first = await endpoint.call_tool(claims_for(BINDING), "explode", {"q": "1"})
        again = await endpoint.call_tool(claims_for(BINDING), "explode", {"q": "1"})
        for result in (first, again):
            assert (result.is_error, result.disposition, result.retryable) == (True, "unknown", False)
        assert world.runs == ["explode"]

    async def test_ep13_another_invocations_dangling_intent_is_in_flight(
        self, endpoint_harness: ToolEndpointHarness
    ) -> None:
        endpoint, world = await self._serve(endpoint_harness)
        args: dict[str, JsonValue] = {"q": "dup"}
        await world.log.intent(
            EffectCall(SCOPE.run_id, "inv_other", ToolCall(id="x", name="create", arguments=args), CREATE.effect, args_digest(args))
        )
        result = await endpoint.call_tool(claims_for(BINDING), "create", args)
        assert (result.is_error, result.disposition, result.retryable) == (False, "in_flight", False)
        assert world.runs == []

    async def test_ep14_a_definite_error_may_be_retried(self, endpoint_harness: ToolEndpointHarness) -> None:
        endpoint, world = await self._serve(endpoint_harness)
        first = await endpoint.call_tool(claims_for(BINDING), "reject", {"q": "1"})
        again = await endpoint.call_tool(claims_for(BINDING), "reject", {"q": "1"})
        assert (first.is_error, first.retryable, first.text) == (True, True, "project not found")
        assert again.disposition == "executed"
        assert world.runs == ["reject", "reject"]

    async def test_ep15_knowledge_results_carry_refs_and_structured_citations(
        self, endpoint_harness: ToolEndpointHarness
    ) -> None:
        endpoint, world = await self._serve(endpoint_harness)
        result = await endpoint.call_tool(claims_for(BINDING), "search", {"q": "q3"})
        assert "[ref1]" in result.text and "[ref2]" in result.text
        assert [(c.ref, c.url) for c in result.citations] == [
            ("ref1", "https://kb.example/q3/1"),
            ("ref2", "https://kb.example/q3/2"),
        ]
        assert result.structured()["citations"] == [
            {"ref": "ref1", "url": "https://kb.example/q3/1"},
            {"ref": "ref2", "url": "https://kb.example/q3/2"},
        ]
        assert [inv for inv, _ in world.citations.recorded] == [result.invocation_id]

    async def test_ep17_claims_for_another_binding_are_refused_first(
        self, endpoint_harness: ToolEndpointHarness
    ) -> None:
        endpoint, world = await self._serve(endpoint_harness)
        with pytest.raises(BindingMismatch):
            await endpoint.list_tools(claims_for(EARLIER_ATTEMPT))
        with pytest.raises(BindingMismatch):
            await endpoint.call_tool(claims_for(EARLIER_ATTEMPT), "lookup", {"q": "x"})
        assert world.runs == []


__all__ = [
    "BINDING",
    "EARLIER_ATTEMPT",
    "GRANTED",
    "OFFERED",
    "SCOPE",
    "SECRET",
    "EndpointWorld",
    "ScriptedHold",
    "ToolEndpointContract",
    "ToolEndpointHarness",
    "build_endpoint",
    "claims_for",
]
