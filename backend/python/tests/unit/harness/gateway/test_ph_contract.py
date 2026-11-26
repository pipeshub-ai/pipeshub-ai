"""PC-1..PC-3 (recs audit R3): a ``ph call`` is the MCP call, in policy and journal.

``ph`` reaches the run's tools through the same gateway transport (``ToolGatewayApp`` over real HTTP, with a gateway
token for the run's binding) as an engine's MCP client. PC-1 runs the whole ``ToolEndpoint`` contract with ``ph``
as the client. PC-2 makes the same calls once through the MCP SDK client and once through ``ph`` against two
identical runs (allowed read, approval asked then given, asked and still undecided, denied, ungranted, a credential
in the arguments, a tool reached through ``call_tool``, and an external write after an unknown-provenance read
under the flow rule, the Rule of Two's case) and compares what each run decided, what each answered and every
entry its effect log (the journal's source) recorded. PC-3 checks a token for another binding is refused alike.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
from typing import TYPE_CHECKING, Any

import pytest

from harness_core.effects import RequestEffectLog
from harness_core.effects.flow import FlowGuardedPolicy, FlowRule
from harness_core.gateway import (
    BindingMismatch,
    Citation,
    McpResult,
    McpTool,
    TokenRefused,
)
from harness_core.gateway.disclosure import LazyDisclosure
from harness_core.testing.contracts.tool_endpoint import (
    BINDING,
    SECRET,
    ScriptedHold,
    ToolEndpointContract,
    build_endpoint,
    claims_for,
)
from harness_engines.pipeshub import ph
from tests.unit.harness.gateway.support import GatewayServer, served

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping

    from pydantic import JsonValue

    from harness_core.effects import ApprovalPolicy, EffectCall, ToolExecution
    from harness_core.gateway import (
        GatewayClaims,
        Progress,
        RunToolEndpoint,
        ToolEndpoint,
    )
    from harness_core.journal.types import JournalEvent


@pytest.fixture
async def server() -> AsyncIterator[GatewayServer]:
    async with GatewayServer().running() as running:
        yield running


@dataclasses.dataclass
class PhEndpoint:
    """``ToolEndpoint`` as ``ph`` reaches it: its own MCP client (urllib, in a thread as the CLI blocks)."""

    server: GatewayServer
    key: str

    def _gateway(self, claims: GatewayClaims) -> ph.Gateway:
        return ph.Gateway(self.server.url(self.key), self.server.mint.mint(claims.binding), timeout_s=30)

    async def _run(self, work: Any) -> Any:  # noqa: ANN401
        try:
            return await asyncio.to_thread(work)
        except ph.PhError as failed:
            if failed.reason == BindingMismatch.reason_code:
                raise BindingMismatch(failed.reason) from None
            if failed.reason is not None:
                raise TokenRefused(failed.reason, failed.reason) from None
            raise

    async def list_tools(self, claims: GatewayClaims) -> tuple[McpTool, ...]:
        listed = await self._run(self._gateway(claims).tools)
        return tuple(McpTool(t["name"], t.get("description", ""), t.get("inputSchema", {})) for t in listed)

    async def call_tool(
        self,
        claims: GatewayClaims,
        name: str,
        arguments: Mapping[str, JsonValue],
        *,
        progress: Progress | None = None,
    ) -> McpResult:
        gw = self._gateway(claims)
        loop = asyncio.get_running_loop()
        ticks: Any = None
        if progress is not None:

            def ticks(elapsed: float) -> None:
                asyncio.run_coroutine_threadsafe(progress(elapsed), loop).result(timeout=30)

        result = await self._run(lambda: ph.call(gw, name, dict(arguments), on_progress=ticks))
        data: dict[str, Any] = dict(result.get("structuredContent") or {})
        return McpResult(
            text=ph._text(result),  # pyright: ignore[reportPrivateUsage]
            is_error=bool(result.get("isError")),
            disposition=data["disposition"],
            retryable=data["retryable"],
            invocation_id=data.get("invocationId"),
            reason_code=data.get("reasonCode"),
            interaction_id=data.get("interactionId"),
            citations=tuple(Citation(c["ref"], c["url"]) for c in data.get("citations", [])),
        )


@contextlib.asynccontextmanager
async def ph_served(server: GatewayServer, endpoint: RunToolEndpoint) -> AsyncIterator[PhEndpoint]:
    async with server.gateway.serving(endpoint, endpoint.binding) as key:
        yield PhEndpoint(server, key)


class OverPh:
    """As ``test_mcp_transport.OverMcp``: each mount held open by its own task."""

    def __init__(self, server: GatewayServer) -> None:
        self._server = server
        self._open: list[tuple[asyncio.Event, asyncio.Task[None]]] = []

    async def serve(self, endpoint: RunToolEndpoint) -> ToolEndpoint:
        ready: asyncio.Future[PhEndpoint] = asyncio.get_running_loop().create_future()
        stop = asyncio.Event()

        async def hold() -> None:
            async with ph_served(self._server, endpoint) as client:
                ready.set_result(client)
                await stop.wait()

        self._open.append((stop, asyncio.ensure_future(hold())))
        return await ready

    async def close(self) -> None:
        for stop, task in self._open:
            stop.set()
            await task


class TestToolEndpointThroughPh(ToolEndpointContract):
    """PC-1: the endpoint contract, through the transport and ``ph``'s client."""

    @pytest.fixture
    async def endpoint_harness(self, server: GatewayServer) -> AsyncIterator[OverPh]:
        harness = OverPh(server)
        yield harness
        await harness.close()


class RecordingLog(RequestEffectLog):
    """The effect log's entries in order: what the journal records of each call (intent, outcome, refusal)."""

    def __init__(self) -> None:
        super().__init__()
        self.entries: list[tuple[str, ...]] = []

    async def intent(self, call: EffectCall) -> None:
        self.entries.append(("intent", call.key.tool_id, call.key.args_digest, call.invocation_id))
        await super().intent(call)

    async def outcome(self, call: EffectCall, execution: ToolExecution) -> None:
        self.entries.append(("outcome", call.key.tool_id, call.invocation_id, execution.status))
        await super().outcome(call, execution)

    async def unknown(self, call: EffectCall) -> None:
        self.entries.append(("unknown", call.key.tool_id, call.invocation_id))
        await super().unknown(call)

    async def refused(self, call: EffectCall, rule: str, reason: str) -> None:
        self.entries.append(("refused", call.key.tool_id, call.invocation_id, rule))
        await super().refused(call, rule, reason)


class UnknownProvenance:
    """A session that has read something whose sources are unknown (untrusted and possibly private)."""

    def apply(self, events: list[JournalEvent]) -> None:
        pass

    def source_records(self, run_id: str) -> frozenset[str] | None:
        return None


def flow_guard(policy: ApprovalPolicy) -> ApprovalPolicy:
    return FlowGuardedPolicy(policy, FlowRule("ask"), UnknownProvenance(), BINDING.run_id)


async def _lazy_rank(query: str, limit: int) -> list[str]:
    return ["create"][:limit]


# (name, run options, the calls of the run in order)
CASES: list[tuple[str, dict[str, Any], list[tuple[str, dict[str, Any]]]]] = [
    ("allowed-read", {}, [("lookup", {"q": "revenue"}), ("search", {"q": "q3"})]),
    ("asked-then-approved", {"approvals": {"transfer": "ask"}, "hold_factory": lambda: ScriptedHold("approved")}, [("transfer", {"q": "1"})]),
    ("asked-undecided", {"approvals": {"transfer": "ask"}, "hold_factory": lambda: ScriptedHold(None), "hold_s": 0.2}, [("transfer", {"q": "1"})]),
    ("asked-then-denied", {"approvals": {"transfer": "ask"}, "hold_factory": lambda: ScriptedHold("denied")}, [("transfer", {"q": "1"})]),
    ("policy-deny", {"approvals": {"create": "deny"}}, [("create", {"q": "x"})]),
    ("ungranted", {}, [("delete", {"q": "x"})]),
    ("credential-in-arguments", {}, [("create", {"q": SECRET})]),
    ("tool-error-then-unknown", {}, [("reject", {"q": "x"}), ("explode", {"q": "y"})]),
    ("through-call-tool", {"lazy": True}, [("create", {"q": "deferred"})]),
    ("rule-of-two-flow-guard", {"guard": flow_guard, "hold_factory": lambda: ScriptedHold("approved")}, [("lookup", {"q": "a"}), ("create", {"q": "out"})]),
]


def _world(options: dict[str, Any]) -> tuple[Any, RecordingLog, ScriptedHold | None]:
    log = RecordingLog()
    hold = options["hold_factory"]() if "hold_factory" in options else None
    kwargs: dict[str, Any] = {k: v for k, v in options.items() if k in ("approvals", "hold_s", "guard")}
    if options.get("lazy"):
        kwargs["disclosure"] = LazyDisclosure(frozenset({"create", "transfer"}), _lazy_rank)
    return build_endpoint(log=log, hold=hold, **kwargs), log, hold


@pytest.mark.parametrize(("case", "options", "calls"), CASES, ids=[c[0] for c in CASES])
async def test_pc2_a_ph_call_and_the_mcp_call_get_the_same_decision_answer_and_journal(
    server: GatewayServer, case: str, options: dict[str, Any], calls: list[tuple[str, dict[str, Any]]]
) -> None:
    claims = claims_for(BINDING)

    async def through(client_of: Any) -> tuple[list[tuple[Any, ...]], list[tuple[str, ...]], list[str], int]:  # noqa: ANN401
        world, log, hold = _world(options)
        answers = []
        async with client_of(server, world.endpoint) as client:
            for name, arguments in calls:
                if options.get("lazy") and client_of is served:
                    # The engine's MCP client reaches a deferred tool through call_tool, as ph does on its own.
                    result = await client.call_tool(claims, "call_tool", {"name": name, "arguments": arguments})
                else:
                    result = await client.call_tool(claims, name, arguments)
                answers.append(
                    (result.text, result.is_error, result.disposition, result.reason_code, result.invocation_id,
                     result.interaction_id, result.retryable)
                )
        return answers, log.entries, world.runs, hold.calls if hold is not None else 0

    over_mcp = await through(served)
    over_ph = await through(ph_served)
    assert over_ph == over_mcp
    answers, entries, runs, _ = over_mcp
    assert len(answers) == len(calls)
    if case in ("allowed-read", "through-call-tool"):
        assert [e[0] for e in entries] == ["intent", "outcome"] * len(calls) and runs == [c[0] for c in calls]
    if case in ("policy-deny", "ungranted", "credential-in-arguments", "asked-then-denied"):
        assert runs == [] and answers[0][1] is True
    if case == "rule-of-two-flow-guard":
        # The read runs; the external write after an unknown-provenance read waits for a person, both ways.
        assert [a[2] for a in answers] == ["executed", "executed"]
        assert over_mcp[3] == over_ph[3] == 1
    if case == "asked-undecided":
        assert answers[0][2] == "deferred" and answers[0][5] is not None


async def test_pc3_a_token_for_another_binding_is_refused_alike(server: GatewayServer) -> None:
    world = build_endpoint()
    other = claims_for(dataclasses.replace(BINDING, run_id="run-9"))
    async with served(server, world.endpoint) as mcp, ph_served(server, world.endpoint) as through_ph:
        with pytest.raises(BindingMismatch):
            await mcp.call_tool(other, "lookup", {"q": "1"})
        with pytest.raises(BindingMismatch):
            await through_ph.call_tool(other, "lookup", {"q": "1"})
    assert world.runs == []
