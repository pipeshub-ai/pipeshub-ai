"""The conformance gateway: the model and tool endpoints an engine under test talks to.

It stands in for the egress proxy, the model gateway and the run's tool
endpoint at once, and records what the engine sent. The tool side keeps an
effect log with dedupe on ``(tool, args digest)`` and holds approvals through a
``FakeInteractionBridge``: decided within the hold → executed and returned;
not decided → a deferred result, and the effect runs when the approval is
resolved later (ARCHITECTURE §5.3). A re-call is answered from the log.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from harness_core.budget.types import ZERO, Amount, Available, LedgerUsage, Remaining
from harness_core.engines.jsonview import as_list, as_object, as_str
from harness_core.engines.types import APPROVAL_HOLD_S, GatewayEndpoints, JsonValue
from harness_core.interactions.ports import (
    InteractionDecision,
    InteractionRequest,
    InteractionTicket,
    RunBinding,
)
from harness_core.testing.engines.script import (
    ChatMessage,
    ModelUsage,
    ScriptedBrain,
    deferred_result,
    tool_properties,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from harness_core.testing.sandbox.egress import EgressRequest, FakeEgress

MODEL_PATH = "/chat/completions"


@dataclass(frozen=True, slots=True)
class GrantedTool:
    name: str
    description: str = "A granted tool."
    needs_approval: bool = False


@dataclass(frozen=True, slots=True)
class ScriptedDecision:
    decision: InteractionDecision
    after_s: float


class FakeInteractionBridge:
    """In-memory ``InteractionBridge`` with scripted decisions and simulated wait time."""

    def __init__(self) -> None:
        self._scripted: dict[str, ScriptedDecision] = {}
        self._resolved: dict[str, InteractionDecision] = {}
        self.requests: list[InteractionRequest] = []

    def script(self, subject: str, decision: InteractionDecision, *, after_s: float = 1.0) -> None:
        self._scripted[subject] = ScriptedDecision(decision, after_s)

    def resolve(self, interaction_id: str, decision: InteractionDecision) -> None:
        self._resolved[interaction_id] = decision

    async def request(self, binding: RunBinding, req: InteractionRequest) -> InteractionTicket:
        self.requests.append(req)
        return InteractionTicket(interaction_id=req.interaction_id, binding=binding)

    async def wait(
        self, ticket: InteractionTicket, *, within_s: float
    ) -> InteractionDecision | None:
        if ticket.interaction_id in self._resolved:
            return self._resolved[ticket.interaction_id]
        subject = next(
            (r.subject for r in self.requests if r.interaction_id == ticket.interaction_id), None
        )
        scripted = self._scripted.get(subject or "")
        if scripted is None or scripted.after_s > within_s:
            return None
        return scripted.decision

    def waited_s(self, subject: str, within_s: float) -> float:
        scripted = self._scripted.get(subject)
        return within_s if scripted is None else min(scripted.after_s, within_s)


@dataclass(frozen=True, slots=True)
class ModelCall:
    system: str
    messages: tuple[ChatMessage, ...]
    tools: tuple[str, ...]
    authorization: str
    tool_descriptions: tuple[str, ...] = ()

    def shown(self) -> str:
        """Everything the model was shown: every message (the system prompt is the system messages), and each tool's
        name and description."""
        return "\n".join((*(m.content for m in self.messages), *self.tools, *self.tool_descriptions))


@dataclass(frozen=True, slots=True)
class ToolCallRecord:
    name: str
    args_digest: str
    authorization: str


def args_digest(args: JsonValue) -> str:
    canonical = json.dumps(args, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


@dataclass
class ConformanceGateway:
    endpoints: GatewayEndpoints
    grant: Sequence[GrantedTool]
    bridge: FakeInteractionBridge = field(default_factory=FakeInteractionBridge)
    binding: RunBinding = field(default_factory=lambda: RunBinding("tenant", "scope", "run"))
    brain: ScriptedBrain = field(default_factory=ScriptedBrain)
    model_calls: list[ModelCall] = field(default_factory=list[ModelCall])
    # What the provider billed for each model call, in order: the gateway's ledger (CF-13).
    billed: list[ModelUsage] = field(default_factory=list[ModelUsage])
    tool_calls: list[ToolCallRecord] = field(default_factory=list[ToolCallRecord])
    executions: Counter[tuple[str, str]] = field(default_factory=Counter[tuple[str, str]])
    deferred: list[str] = field(default_factory=list[str])
    holding: asyncio.Event = field(default_factory=asyncio.Event)
    _outcomes: dict[tuple[str, str], str] = field(default_factory=dict[tuple[str, str], str])
    _pending: dict[str, tuple[str, str, str]] = field(default_factory=dict[str, tuple[str, str, str]])
    _release: asyncio.Event = field(default_factory=asyncio.Event)
    _calls: int = 0
    # Which shared budget the run's ledger reports binding it (WP56-STOP-1); None: the run's own.
    budget_scope: str | None = None
    # The run's ledger reports its spend at a cap (``Remaining.exhausted``): the gateway refuses its next call.
    exhausted: bool = False

    def install(self, egress: FakeEgress) -> FakeEgress:
        egress.route(self.endpoints.model_base_url, self._model)
        egress.route(self.endpoints.tools_mcp_url, self._tools)
        return egress

    def release(self) -> None:
        self._release.set()

    async def read(self) -> Remaining:
        """``RunUsageLedger``: what the gateway billed so far, as the run's ledger account holds it (OWD-8)."""
        used = LedgerUsage(sum(u.input_tokens for u in self.billed), sum(u.output_tokens for u in self.billed))
        charged = Amount(0, used.input_tokens + used.output_tokens)
        return Remaining(Available(None, None), charged, ZERO, exhausted=self.exhausted, used=used, budget_scope=self.budget_scope)

    async def resolve_async(self, interaction_id: str, decision: InteractionDecision) -> None:
        """``resolve`` for a gateway whose tool side is asynchronous (the real endpoint)."""
        self.resolve(interaction_id, decision)

    def resolve(self, interaction_id: str, decision: InteractionDecision) -> None:
        """Resolve a deferred approval; an approved effect runs now, before the engine re-calls."""
        self.bridge.resolve(interaction_id, decision)
        tool, digest, raw = self._pending.pop(interaction_id)
        if decision == "approved":
            self._execute(tool, digest, raw)
        else:
            self._outcomes[(tool, digest)] = json.dumps({"error": f"approval {decision}"})

    def _execute(self, tool: str, digest: str, raw_args: str) -> str:
        self.executions[(tool, digest)] += 1
        result = f"RESULT for {tool} {raw_args}"
        self._outcomes[(tool, digest)] = result
        return result

    async def _model(self, request: EgressRequest) -> JsonValue:
        body = as_object(request.body)
        messages = tuple(ChatMessage.from_json(m) for m in as_list(body.get("messages")))
        tools = tuple(as_str(as_object(t).get("name")) for t in as_list(body.get("tools")))
        properties = {as_str(as_object(t).get("name")): tool_properties(as_object(t)) for t in as_list(body.get("tools"))}
        descriptions = tuple(as_str(as_object(t).get("description")) for t in as_list(body.get("tools")))
        system = "\n".join(m.content for m in messages if m.role == "system")
        self.model_calls.append(
            ModelCall(system, messages, tools, request.headers.get("authorization", ""), descriptions)
        )
        key = self.brain.pending_hold(messages)
        if key is not None:
            self.holding.set()
            try:
                await self._release.wait()
            finally:
                # An interrupted hold is spent too: the next turn must not block on it again.
                self.brain.holds.add(key)
                self._release.clear()
                self.holding.clear()
        self._calls += 1
        reply = self.brain.reply(messages, tools, f"call_{self._calls}", properties)
        if reply.error is None:
            # A provider bills no call it refused.
            self.billed.append(reply.usage)
        return reply.to_json()

    async def _tools(self, request: EgressRequest) -> JsonValue:
        body = as_object(request.body)
        method = as_str(body.get("method"))
        if method == "tools/list":
            return {"tools": [{"name": t.name, "description": t.description} for t in self.grant]}
        if method != "tools/call":
            return {"error": f"unknown method {method}"}
        params = as_object(body.get("params"))
        name = as_str(params.get("name"))
        args = params.get("arguments")
        digest = args_digest(args)
        self.tool_calls.append(
            ToolCallRecord(name, digest, request.headers.get("authorization", ""))
        )
        tool = next((t for t in self.grant if t.name == name), None)
        if tool is None:
            return {"content": f"tool {name} is not granted", "isError": True, "heldS": 0}
        recorded = self._outcomes.get((name, digest))
        if recorded is not None:
            return {"content": recorded, "isError": False, "heldS": 0}
        raw = json.dumps(args, sort_keys=True)
        if not tool.needs_approval:
            return {"content": self._execute(name, digest, raw), "isError": False, "heldS": 0}
        interaction_id = f"ix_{digest[:12]}"
        ticket = await self.bridge.request(
            self.binding, InteractionRequest(interaction_id, "approval", name, digest)
        )
        decision = await self.bridge.wait(ticket, within_s=APPROVAL_HOLD_S)
        held_s = self.bridge.waited_s(name, APPROVAL_HOLD_S)
        if decision is None:
            self._pending[interaction_id] = (name, digest, raw)
            self.deferred.append(interaction_id)
            return {"content": deferred_result(interaction_id), "isError": False, "heldS": held_s}
        if decision != "approved":
            return {"content": f"approval {decision}", "isError": True, "heldS": held_s}
        return {"content": self._execute(name, digest, raw), "isError": False, "heldS": held_s}


__all__ = [
    "MODEL_PATH",
    "ConformanceGateway",
    "FakeInteractionBridge",
    "GrantedTool",
    "ModelCall",
    "ToolCallRecord",
    "args_digest",
]
