"""The conformance gateway with its tool side served by the real ``RunToolEndpoint`` (E2).

``ConformanceGateway`` fakes the tool endpoint (its own effect log and dedupe);
this subclass keeps the wire shape the conformance engines speak through
``FakeEgress`` and the model side, but answers ``tools/list`` and
``tools/call`` through ``RunToolEndpoint`` → Invoker → ``EffectRunner`` with the
gateway's ``FakeInteractionBridge`` as the approval hold. A resolved deferral
runs the call once more through the endpoint, as the controller does when the
run resumes (ARCHITECTURE §5.3), so the engine's re-call is answered from the
effect log.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast, override

from pydantic import JsonValue

from harness_core.effects import EffectRunner, RequestEffectLog, ToolExecution
from harness_core.engines.jsonview import as_object, as_str
from harness_core.engines.native_policy import NativeCallPolicy
from harness_core.engines.policy import NativeTool
from harness_core.engines.types import APPROVAL_HOLD_S
from harness_core.gateway import (
    GatewayClaims,
    InMemoryCitationSink,
    RunAttemptBinding,
    RunToolEndpoint,
    grant_digest,
)
from harness_core.interactions.ports import InteractionRequest
from harness_core.invoker import CatalogTool, InMemoryToolCatalog, InvocationScope
from harness_core.kernel.core.tool_schema import ToolSchema
from harness_core.kernel.fold import interaction_id
from harness_core.testing.contracts.tool_catalog import offered
from harness_core.testing.engines.gateway import (
    ConformanceGateway,
    ToolCallRecord,
    args_digest,
)
from harness_core.testing.engines.script import deferred_result
from harness_core.testing.ids import SeededIds

# Every native tool a build run may have (``expected_native_tools`` with skills and sub-agents on).
BUILD_RUN_TOOLS = frozenset(NativeTool) - {NativeTool.WEB, NativeTool.ASK_USER, NativeTool.TODO}

if TYPE_CHECKING:

    from harness_core.effects import (
        ApprovalDecision,
        EffectCall,
        EffectKey,
        HoldDecision,
        ToolContext,
        ToolEffect,
    )
    from harness_core.engines.types import EngineCapabilities
    from harness_core.journal.types import InteractionDecision
    from harness_core.kernel.core.messages import ToolCall
    from harness_core.testing.sandbox.egress import EgressRequest

_ARGS: dict[str, JsonValue] = {"type": "object", "additionalProperties": True}


class _BridgeHold:
    """``ApprovalHold`` over the conformance gateway's ``FakeInteractionBridge``.

    A re-call gets a new invocation id, so a pending approval is found again by
    the call's effect key (run, tool, arguments), not by invocation: what P2.6c's
    hold must do for "a re-call after deferral is not executed twice"."""

    def __init__(self, gateway: EndpointConformanceGateway) -> None:
        self._gateway = gateway
        self._pending: dict[EffectKey, str] = {}

    async def hold(self, call: EffectCall) -> HoldDecision | None:
        bridge = self._gateway.bridge
        pending = self._pending.setdefault(call.key, interaction_id(call.invocation_id, "approve"))
        ticket = await bridge.request(
            self._gateway.binding,
            InteractionRequest(pending, "approval", call.call.name, call.args_digest),
        )
        decision = await bridge.wait(ticket, within_s=APPROVAL_HOLD_S)
        return decision if decision in ("approved", "denied") else None


@dataclass
class EndpointConformanceGateway(ConformanceGateway):
    capabilities: EngineCapabilities | None = None
    # Which attempt of the run the endpoint (and the tokens it accepts) belongs to.
    attempt: int = 1
    # The model routes the run's gateway token names (E3): a lane with a real model gateway pins one.
    routes_digest: str = field(default_factory=lambda: grant_digest(()))
    # The run has a person to ask, so the endpoint lists the gateway's ``ask_user`` (C3.70).
    asks: bool = False
    # The run's policy for the engine's own tools, which its managed hook asks (G-25): by default a build run's, the
    # conformance profile's mode; ``None`` refuses every check, as a gateway without one does.
    native: NativeCallPolicy | None = field(default_factory=lambda: NativeCallPolicy(BUILD_RUN_TOOLS))
    endpoint: RunToolEndpoint = field(init=False)
    claims: GatewayClaims = field(init=False)
    _deferred_calls: dict[str, tuple[str, Mapping[str, JsonValue]]] = field(
        default_factory=lambda: dict[str, tuple[str, Mapping[str, JsonValue]]]()
    )
    # The questions the engine asked through ``ask_user``, in order.
    asked: list[str] = field(default_factory=list[str])

    def __post_init__(self) -> None:
        self._build()

    def asking(self) -> EndpointConformanceGateway:
        """The same gateway, its endpoint rebuilt to list ``ask_user`` (a run with a person to ask)."""
        self.asks = True
        self._build()
        return self

    def _build(self) -> None:
        tools = {t.name: offered(t.name, "write") for t in self.grant}
        needs_approval = {t.name for t in self.grant if t.needs_approval}

        def run(name: str):  # noqa: ANN202 - a ToolFn
            async def execute(call: ToolCall, ctx: ToolContext) -> ToolExecution:
                raw = json.dumps(call.arguments, sort_keys=True)
                return ToolExecution(status="ok", content=self._execute(name, args_digest(call.arguments), raw))

            return execute

        def policy(call: ToolCall, effect: ToolEffect) -> ApprovalDecision:
            return "ask" if call.name in needs_approval else "allow"

        catalog = InMemoryToolCatalog(
            [
                CatalogTool(ToolSchema(name=t.name, description=t.description, input_schema=_ARGS), tools[t.name].effect, run(t.name))
                for t in self.grant
            ],
            [o.effect.tool_id for o in tools.values()],
        )
        binding = RunAttemptBinding(
            tenant=self.binding.tenant,
            scope=self.binding.scope,
            run_id=self.binding.run_id,
            parent_run_id=None,
            attempt_id=f"{self.binding.run_id}.a{self.attempt}",
            fence=self.attempt,
            tool_ids_digest=grant_digest(o.effect.tool_id for o in tools.values()),
            routes_digest=self.routes_digest,
        )
        self.claims = GatewayClaims(binding, kid="conformance", expires_at=4_102_444_800)
        self.endpoint = RunToolEndpoint(
            binding=binding,
            catalog=catalog,
            effects=EffectRunner(RequestEffectLog(), policy, hold=_BridgeHold(self)),
            scope=InvocationScope(binding.run_id, binding.tenant, binding.attempt_id),
            ids=SeededIds(),
            citations=InMemoryCitationSink(),
            capabilities=self.capabilities,
            asks=self.asks,
            native=self.native,
        )

    @override
    def resolve(self, interaction_id: str, decision: InteractionDecision) -> None:
        raise TypeError("use resolve_async: the endpoint runs the approved call")

    @override
    async def resolve_async(self, interaction_id: str, decision: InteractionDecision) -> None:
        self.bridge.resolve(interaction_id, decision)
        name, args = self._deferred_calls.pop(interaction_id)
        await self.endpoint.call_tool(self.claims, name, args)

    @override
    async def _tools(self, request: EgressRequest) -> JsonValue:
        body = as_object(request.body)
        method = as_str(body.get("method"))
        if method == "tools/list":
            tools = await self.endpoint.list_tools(self.claims)
            return {"tools": [{"name": t.name, "description": t.description} for t in tools]}
        if method != "tools/call":
            return {"error": f"unknown method {method}"}
        params = as_object(body.get("params"))
        name = as_str(params.get("name"))
        args = cast("Mapping[str, JsonValue]", as_object(params.get("arguments")))
        self.tool_calls.append(
            ToolCallRecord(name, args_digest(args), request.headers.get("authorization", ""))
        )
        result = await self.endpoint.call_tool(self.claims, name, args)
        if result.disposition == "asked":
            self.asked.append(str(args.get("question", "")))
            return {"content": result.text, "isError": False, "heldS": 0}
        held_s = self.bridge.waited_s(name, APPROVAL_HOLD_S) if name in {t.name for t in self.grant if t.needs_approval} else 0
        if result.disposition == "deferred" and result.interaction_id is not None:
            self._deferred_calls[result.interaction_id] = (name, args)
            self.deferred.append(result.interaction_id)
            return {"content": deferred_result(result.interaction_id, result.text), "isError": False, "heldS": held_s}
        if result.disposition == "recorded":
            held_s = 0
        return {"content": result.text, "isError": result.is_error, "heldS": held_s}



__all__ = ["EndpointConformanceGateway"]
