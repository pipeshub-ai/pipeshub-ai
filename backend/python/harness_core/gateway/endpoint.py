"""``RunToolEndpoint``: one run's (or one request's) tools, served to an engine over MCP.

ARCHITECTURE §4.4.2. ``tools/list`` is the grant; ``tools/call`` checks the
token's binding, refuses credential shapes in the arguments, then goes through
the Invoker and ``EffectRunner``, so dedupe, approvals and the effect log are
the durable kernel's. Every ``EffectResult`` disposition has one MCP answer, and
the answer says whether calling again may run the tool. Knowledge results keep
their ``refN`` and carry structured citations; what a call added to the run's
citations rides its outcome (``CitationSink``).

Product-neutral: no clock reads, ids from the injected ``IdSource``, sleeping
injected for the progress ticker.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import dataclasses
import json
import time
from typing import TYPE_CHECKING

from harness_core.effects.runner import (
    EditingHold,
    ExplainingHold,
    NamedHold,
    ReasonedHold,
)
from harness_core.effects.types import tool_id_for
from harness_core.engines.errors import GrantTooLarge, ToolNameTooLong
from harness_core.engines.native_policy import POLICY_CHECK_TOOL, no_policy_verdict
from harness_core.engines.policy import check_grant_names, check_grant_size
from harness_core.engines.types import APPROVAL_HOLD_S
from harness_core.gateway.arguments import ArgumentCheck, invalid_arguments_text
from harness_core.gateway.asking import (
    ASK_USER,
    answered_result,
    ask_user_tool,
    asked_result,
    invalid_question,
    parse_question,
    question_key,
)
from harness_core.gateway.disclosure import (
    CALL_TOOL,
    SEARCH_TOOLS,
    LazyDisclosure,
    unwrap_call,
)
from harness_core.gateway.errors import BindingMismatch, ForbiddenArguments
from harness_core.gateway.ports import NamesUntrusted
from harness_core.gateway.types import (
    PROGRESS_INTERVAL_S,
    McpResult,
    McpTool,
    RunAttemptBinding,
    render_content,
    untrusted_note,
)
from harness_core.interactions.connect_wait import (
    AUTH_REQUIRED,
    BridgeConnectWait,
    ConnectAnswer,
)
from harness_core.invoker.invoker import Invoker
from harness_core.invoker.types import ToolRefusal
from harness_core.kernel.core.messages import ToolCall
from harness_core.kernel.fold import interaction_id
from harness_core.logs import HarnessLog
from harness_core.security.secrets import masking
from harness_core.security.token_patterns import find_forbidden_in, redact_secrets_in
from harness_core.telemetry import genai
from harness_core.telemetry.memory import NOOP_TRACER
from harness_core.telemetry.metrics import NOOP_GATEWAY_METRICS, ToolCallObserved
from harness_core.telemetry.ports import RunRef
from harness_core.tool_identity.spec_names import ModelNames

if TYPE_CHECKING:
    from collections.abc import (
        AsyncIterator,
        Awaitable,
        Callable,
        Iterable,
        Mapping,
        Sequence,
    )

    from pydantic import JsonValue

    from harness_core.effects.edits import ApprovedEdit
    from harness_core.effects.runner import ApprovalHold, EffectRunner, HoldDecision
    from harness_core.effects.types import (
        EffectCall,
        EffectResult,
        ToolContext,
        ToolEffect,
        ToolExecution,
    )
    from harness_core.engines.native_policy import NativeCallPolicy
    from harness_core.engines.types import EngineCapabilities
    from harness_core.gateway.ports import CitationSink, Progress
    from harness_core.gateway.types import GatewayBinding, GatewayClaims
    from harness_core.ids import IdSource
    from harness_core.invoker.ports import ToolCatalog
    from harness_core.invoker.types import InvocationScope, InvokeResult
    from harness_core.kernel.core.tool_schema import ToolSchema
    from harness_core.provenance.stamp import ProvenanceStamp
    from harness_core.provenance.types import ResultProvenance
    from harness_core.telemetry.metrics import GatewayMetrics
    from harness_core.telemetry.ports import AttrValue, HarnessTracer
    from harness_core.tools.read_cache import RunReadCache

    Sleep = Callable[[float], Awaitable[None]]


class RunSlots:
    """TG-15: the tool calls one run executes at once. A call waiting on a person (an approval hold) gives its slot
    back while it waits and takes one again before it runs, so a pending approval never queues the run's reads."""

    def __init__(self, size: int) -> None:
        if size < 1:
            raise ValueError("a run's endpoint executes at least one concurrent tool call")
        self._semaphore = asyncio.Semaphore(size)
        # Whether the call running in this context holds a slot now (each call runs in a task of its own).
        self._holding = contextvars.ContextVar(f"run_slot_{id(self)}", default=False)

    @contextlib.asynccontextmanager
    async def executing(self) -> AsyncIterator[None]:
        await self._semaphore.acquire()
        token = self._holding.set(True)
        try:
            yield
        finally:
            if self._holding.get():
                self._semaphore.release()
            self._holding.reset(token)

    @contextlib.asynccontextmanager
    async def waiting(self) -> AsyncIterator[None]:
        if not self._holding.get():
            yield
            return
        self._semaphore.release()
        self._holding.set(False)
        try:
            yield
        finally:
            # Cancelled while taking it back: not holding, so ``executing`` releases nothing.
            await self._semaphore.acquire()
            self._holding.set(True)


@dataclasses.dataclass(frozen=True, slots=True)
class BoundedHold:
    """An ``ApprovalHold`` that answers within ``within_s``: undecided by then is "defer".

    The engine's MCP timeout is at least 150 s (``MIN_TOOL_TIMEOUT_S``); a hold
    that overran it would turn a pending approval into an engine-side failure.
    """

    inner: ApprovalHold
    within_s: float = APPROVAL_HOLD_S
    slots: RunSlots | None = None

    async def hold(self, call: EffectCall) -> HoldDecision | None:
        async with self.slots.waiting() if self.slots is not None else contextlib.nullcontext():
            try:
                return await asyncio.wait_for(self.inner.hold(call), self.within_s)
            except TimeoutError:
                return None

    def interaction_for(self, call: EffectCall) -> str | None:
        return self.inner.interaction_for(call) if isinstance(self.inner, NamedHold) else None

    def explain(self, call: EffectCall, reason: Mapping[str, JsonValue]) -> None:
        if isinstance(self.inner, ExplainingHold):
            self.inner.explain(call, reason)

    def denial_reason(self, call: EffectCall) -> str | None:
        return self.inner.denial_reason(call) if isinstance(self.inner, ReasonedHold) else None

    def approved_edit(self, call: EffectCall) -> ApprovedEdit | None:
        return self.inner.approved_edit(call) if isinstance(self.inner, EditingHold) else None


class _CitingCatalog:
    """The run's catalog, with each executed call's citation delta riding its outcome."""

    def __init__(
        self,
        inner: ToolCatalog,
        citations: CitationSink,
        provenance: ProvenanceStamp | None = None,
        read_cache: RunReadCache[ToolExecution] | None = None,
    ) -> None:
        self._inner = inner
        self._citations = citations
        self._provenance = provenance
        self._read_cache = read_cache
        # Invocation -> the untrusted sources its result named (KN-08), until the endpoint answers it.
        self.untrusted: dict[str, tuple[str, ...]] = {}

    def describe_call(self, call: ToolCall) -> ToolEffect | None:
        return self._inner.describe_call(call)

    def schemas(self) -> list[ToolSchema] | None:
        return self._inner.schemas()

    async def execute(self, call: ToolCall, ctx: ToolContext) -> ToolExecution:
        effect = self._inner.describe_call(call) if self._read_cache is not None else None
        slot = (
            self._read_cache.slot(ctx, call.name, effect, effect.effect, call.arguments)
            if self._read_cache is not None and effect is not None
            else None
        )
        cached = self._read_cache.hit(slot) if self._read_cache is not None and slot is not None else None
        if cached is not None:
            # C7.14: the first answer, with the integrity and provenance it was recorded with; its citations are
            # already the run's, so no delta rides it again.
            if cached.untrusted:
                self.untrusted[ctx.invocation_id] = cached.untrusted
            execution = dataclasses.replace(cached.payload, integrity=cached.integrity)  # pyright: ignore[reportArgumentType]
            return self._stamped(call, ctx, execution, cached.provenance)
        inner = await self._inner.execute(call, ctx)
        execution = self._cited(ctx, inner)
        if self._provenance is not None:
            execution = await self._provenance.reviewed(call.name, execution)
        decided = None
        if slot is not None and self._read_cache is not None and not inner.events:
            decided = self._provenance.provenance(call.name, execution) if self._provenance is not None else None
            self._read_cache.keep(
                slot, inner, execution, provenance=decided, untrusted=self.untrusted.get(ctx.invocation_id, ())
            )
        return self._stamped(call, ctx, execution, decided)

    def _cited(self, ctx: ToolContext, execution: ToolExecution) -> ToolExecution:
        events = self._citations.record(ctx.invocation_id)
        if not events:
            return execution
        untrusted = (
            self._citations.untrusted_sources(events) if isinstance(self._citations, NamesUntrusted) else ()
        )
        if not untrusted:
            return dataclasses.replace(execution, events=(*execution.events, *events))
        self.untrusted[ctx.invocation_id] = untrusted
        return dataclasses.replace(execution, events=(*execution.events, *events), integrity="external")

    def _stamped(
        self, call: ToolCall, ctx: ToolContext, execution: ToolExecution, decided: ResultProvenance | None = None
    ) -> ToolExecution:
        """ADR-RUN-01 D3: the result recorded with its provenance once its citation delta rides it."""
        if self._provenance is None:
            return execution
        effect = self._inner.describe_call(call)
        tool_id = effect.tool_id if effect is not None else ""
        return self._provenance.stamp(call.name, tool_id, ctx.invocation_id, execution, decided)


_UNKNOWN_TEXT = (
    "The effect of this call is unknown: the request may have been processed. "
    "Do not call it again; it will not be executed again."
)
_IN_FLIGHT_TEXT = "An identical call is still in progress. Do not repeat it."
# ADR-RUN-04 D1: an ask never stops the work. "End this turn" left Codex idle for the 40 minutes the person took to
# allow a clone; the decision now reaches the turn that is still running (D2).
WHILE_WAITING = (
    "Keep working on the rest of the task meanwhile: the decision comes to you as a message, in this turn if you are "
    "still working. End your turn only when nothing is left that you can do without this call."
)


# TG-15: tool calls one run's endpoint executes at once; past it a call waits its turn (still sending progress).
# Above the widest parallel wave an engine sends (PF-18: ten calls take about as long as the slowest).
RUN_CALL_CONCURRENCY = 16


class RunToolEndpoint:
    def __init__(
        self,
        *,
        binding: GatewayBinding,
        catalog: ToolCatalog,
        effects: EffectRunner,
        scope: InvocationScope,
        ids: IdSource,
        citations: CitationSink,
        capabilities: EngineCapabilities | None = None,
        hold_s: float = APPROVAL_HOLD_S,
        progress_every_s: float = PROGRESS_INTERVAL_S,
        sleep: Sleep = asyncio.sleep,
        logs: HarnessLog | None = None,
        tracer: HarnessTracer = NOOP_TRACER,
        disclosure: LazyDisclosure | None = None,
        needs_authentication: frozenset[str] = frozenset(),
        asks: bool = False,
        answered: Mapping[str, str] | None = None,
        connect: BridgeConnectWait | None = None,
        native: NativeCallPolicy | None = None,
        metrics: GatewayMetrics = NOOP_GATEWAY_METRICS,
        max_concurrent_calls: int = RUN_CALL_CONCURRENCY,
        provenance: ProvenanceStamp | None = None,
        read_cache: RunReadCache[ToolExecution] | None = None,
        prepare: Callable[[Sequence[str]], Awaitable[Mapping[str, str] | None]] | None = None,
    ) -> None:
        """``max_concurrent_calls``: the run's tool calls that execute at once (TG-15); a call past it waits for a slot
        and sends progress meanwhile, so one run flooding the gateway queues its own calls, never another run's.
        ``capabilities``: the engine's, whose ``max_tools`` bounds what ``tools/list`` shows (``GrantTooLarge``).
        ``disclosure``: ``tools/list`` shows the grant less ``disclosure.deferred`` and the two disclosure tools
        (MCP-E2E §9); what may be called is the grant either way. ``needs_authentication``: granted tools whose item
        is not connected, listed with that mark (WS-09); their calls answer how to connect it. ``asks``: the run has
        a person to ask, so the gateway's own ``ask_user`` is listed (``asking.py``); ``answered``: the questions the
        person already answered in the run's task (``question_key`` → answer), answered from it, not asked again
        (ADR-RUN-03 D2). ``connect``: a call of one of
        those tools waits for its owner to connect the item instead (G-12), deferred as an approval is. ``native``: the
        run's policy for the engine's own tools, which its managed hook asks through ``POLICY_CHECK_TOOL`` (G-25);
        unset, every such check is refused. ``read_cache``: the run's cache of its declared connector reads (C7.14)."""
        self._slots = RunSlots(max_concurrent_calls)
        if effects.hold is not None:
            bounded = effects.hold if isinstance(effects.hold, BoundedHold) else BoundedHold(effects.hold, hold_s)
            effects = dataclasses.replace(effects, hold=dataclasses.replace(bounded, slots=self._slots))
        self._binding = binding
        self._effects = effects
        self._citations = citations
        self._capabilities = capabilities
        self._progress_every_s = progress_every_s
        self._sleep = sleep
        self._logs = logs or HarnessLog.named(
            "harness_core.gateway",
            orgId=scope.org_id,
            runId=scope.run_id,
            attemptId=scope.attempt_id,
        )
        # ADR-RUN-01 D3: every result the gateway serves recorded with its provenance (the host tools' included).
        self._catalog = _CitingCatalog(catalog, citations, provenance, read_cache)
        self._invoker = Invoker(self._catalog, effects, scope, ids=ids, logs=self._logs)
        self._tracer = tracer
        self._metrics = metrics
        self._secrets = effects.secrets
        self._arguments = ArgumentCheck()
        self._run = (
            RunRef(binding.tenant, binding.run_id, binding.scope, binding.attempt_id, binding.fence)
            if isinstance(binding, RunAttemptBinding)
            else RunRef(scope.org_id, scope.run_id, attempt_id=scope.attempt_id)
        )
        # Builds a tool its source makes at the run's first use of it (an MCP server listed then, D-LAZY-4) before
        # its call is resolved.
        self._prepare = prepare
        granted = self._invoker.granted()
        self._needs_authentication = needs_authentication
        self._connect = connect
        self._native = native
        self._disclosure = disclosure if disclosure is not None and disclosure.applies(granted) else None
        spec = capabilities is not None and capabilities.spec_tool_names
        # OWD-7: the model sees spec names, and either form of a name reaches the same grant (``spec_names``).
        self._names = (
            ModelNames.spec([*(s.name for s in granted), SEARCH_TOOLS, CALL_TOOL]) if spec else ModelNames.legacy()
        )
        reserved = capabilities.reserved_tool_names if capabilities is not None else frozenset[str]()
        self._ask_tool = ask_user_tool(reserved | {ASK_USER} if spec else reserved)
        # A granted tool of the same name keeps it: the gateway never shadows the grant.
        self._asks = asks and all(self._names.shown(s.name) != self._ask_tool.name for s in granted)
        self._answered = dict(answered or {})
        # The session's list when an earlier run of the conversation fixed it (``tool_pin``); else this run's.
        self._session_list: tuple[McpTool, ...] | None = None
        listed = self._listed(granted)
        self._check_size(len(listed))
        self._check_names(t.name for t in listed)

    @property
    def binding(self) -> GatewayBinding:
        return self._binding

    def authorize(self, claims: GatewayClaims) -> None:
        if claims.binding != self._binding:
            self._logs.warning(
                "gateway.refused",
                reason=BindingMismatch.reason_code,
                tokenBinding=type(claims.binding).__name__,
                kid=claims.kid,
            )
            raise BindingMismatch("the gateway token is for another binding")

    def granted_tools(self) -> tuple[McpTool, ...]:
        """The whole grant (``GrantedTools``), whatever ``tools/list`` shows of it."""
        return tuple(self._shown(self._tool(s)) for s in self._invoker.granted())

    def internal_name(self, name: str) -> str:
        """The grant's own name for a name the engine used (a spec name or today's): what the run's registry, its
        activity labels and its journal know the tool by."""
        return self._names.internal(name)

    def _shown(self, tool: McpTool) -> McpTool:
        shown = self._names.shown(tool.name)
        return tool if shown == tool.name else dataclasses.replace(tool, name=shown)

    async def list_tools(self, claims: GatewayClaims) -> tuple[McpTool, ...]:
        self.authorize(claims)
        listed = self._session_list if self._session_list is not None else self._listed(self._invoker.granted())
        self._check_size(len(listed))
        return listed

    def served_tools(self) -> tuple[McpTool, ...]:
        """``tools/list`` as this run's grant gives it (``PinsToolList``)."""
        return self._listed(self._invoker.granted())

    def hold_list(self, tools: tuple[McpTool, ...]) -> None:
        """Serve the session's list (OWD-7, TG-14): the engine sees what it saw on the conversation's first turn."""
        self._session_list = tools

    def _removed(self, name: str) -> McpResult | None:
        """A tool the session's list shows that this run no longer has: why it cannot run, in plain words."""
        if self._session_list is None or all(t.name != self._names.shown(name) for t in self._session_list):
            return None
        if self._invoker.resolve(ToolCall(id="resolve", name=name, arguments={})) is not None:
            return None
        return McpResult(
            text=(
                f"{name} is no longer available: it was removed after this chat started. "
                "Start a new chat to use the tools available now."
            ),
            is_error=True,
            disposition="refused",
            retryable=False,
            reason_code="not_found",
        )

    def _tool(self, schema: ToolSchema) -> McpTool:
        masked = "needs_authentication" if schema.name in self._needs_authentication else None
        return McpTool(schema.name, schema.description, dict(schema.input_schema), auth=masked)

    def _listed(self, granted: list[ToolSchema]) -> tuple[McpTool, ...]:
        tools = tuple(self._tool(s) for s in granted)
        if self._disclosure is not None:
            tools = self._disclosure.listed(tools)
        tools = tuple(self._shown(t) for t in tools)
        return (*tools, self._ask_tool) if self._asks else tools

    def _check_names(self, names: Iterable[str]) -> None:
        """Never renamed: a grant with a name the engine cannot carry is refused (E8 FB-14)."""
        if self._capabilities is None:
            return
        try:
            check_grant_names(self._capabilities, names)
        except ToolNameTooLong as refusal:
            self._logs.warning(
                "gateway.refused", reason=refusal.reason_code, longest=refusal.longest, maxLength=refusal.max_length
            )
            raise

    def _check_size(self, tools: int) -> None:
        """Never truncated: a grant the engine cannot show whole is refused (§4.4.2)."""
        if self._capabilities is None:
            return
        try:
            check_grant_size(self._capabilities, tools)
        except GrantTooLarge as refusal:
            self._logs.warning(
                "gateway.refused", reason=refusal.reason_code, tools=tools, maxTools=refusal.max_tools
            )
            raise

    async def call_tool(
        self,
        claims: GatewayClaims,
        name: str,
        arguments: Mapping[str, JsonValue],
        *,
        progress: Progress | None = None,
    ) -> McpResult:
        self.authorize(claims)
        started = time.monotonic()
        family, answer = await self._call_with_family(name, arguments, progress)
        self._metrics.tool_call(ToolCallObserved(family, answer.disposition, time.monotonic() - started))
        return answer

    async def _call_with_family(
        self, name: str, arguments: Mapping[str, JsonValue], progress: Progress | None
    ) -> tuple[str, McpResult]:
        """The call's answer and its family for the metrics: the effect class, or the gateway's own tools."""
        if name == POLICY_CHECK_TOOL:
            return "gateway", self._policy_check(arguments)
        name = self._names.internal(name)
        if self._disclosure is not None:
            if name == SEARCH_TOOLS:
                return "disclosure", await self._disclosure.search(arguments, self._invoker.granted, self._names.shown)
            if name == CALL_TOOL:
                # One path: the named tool runs as if called directly, under its own name (grant, approval, journal).
                unwrapped = unwrap_call(arguments)
                if isinstance(unwrapped, McpResult):
                    return "disclosure", unwrapped
                name, arguments = unwrapped
                name = self._names.internal(name)
        if self._prepare is not None:
            # A tool's own name at its server (``ph tools call create_ticket``) runs as the tool it names.
            name = (await self._prepare([name]) or {}).get(name, name)
        effect = self._invoker.resolve(ToolCall(id="resolve", name=name, arguments=dict(arguments)))
        own = "gateway" if self._asks and name == self._ask_tool.name else "ungranted"
        tool_id = effect.tool_id if effect is not None else tool_id_for(name, source=own)
        attributes: dict[str, AttrValue | None] = {
            genai.OPERATION: genai.EXECUTE_TOOL,
            genai.TOOL_NAME: name,
            genai.TOOL_TYPE: "function",
            "pipeshub.tool_id": tool_id,
        }
        # The span is the call's surface too: what the call's boundary resolved is masked in it exactly (SEC4).
        with masking(self._secrets), self._tracer.span(
            f"{genai.EXECUTE_TOOL} {name}", kind="internal", run=self._run, attributes=attributes
        ) as span:
            if self._tracer.capture_content:
                span.set({genai.TOOL_ARGUMENTS: json.dumps(redact_secrets_in(dict(arguments)), ensure_ascii=False, default=str)})
            answer = await self._call(name, arguments, progress)
            span.set({genai.TOOL_CALL_ID: answer.invocation_id, "pipeshub.disposition": answer.disposition})
            if self._tracer.capture_content:
                span.set({genai.TOOL_RESULT: answer.text})
            if answer.is_error:
                span.end(error_type=answer.reason_code or "tool_error")
        # The gateway's request log: one line per call, what the grant auditor reads (P9).
        self._logs.info(
            "gateway.call",
            invocationId=answer.invocation_id,
            toolId=tool_id,
            disposition=answer.disposition,
            reasonCode=answer.reason_code,
        )
        return (effect.effect if effect is not None else own), answer

    def _policy_check(self, arguments: Mapping[str, JsonValue]) -> McpResult:
        """A native call the engine's managed hook asks about (G-25): answered from the run's policy, never run, never
        journaled as a tool call. A refusal is logged with its reason, so it reaches the harness as well as the engine."""
        verdict = no_policy_verdict() if self._native is None else self._native.check(arguments)
        tool = arguments.get("tool")
        self._logs.info(
            "gateway.policy_check",
            decision=verdict.decision,
            reasonCode=verdict.code,
            tool=tool if isinstance(tool, str) else None,
        )
        if verdict.allowed:
            return McpResult(text="allowed", is_error=False, disposition="executed", retryable=True)
        return McpResult(
            text=verdict.reason, is_error=True, disposition="denied", retryable=False, reason_code=verdict.code
        )

    async def _call(self, name: str, arguments: Mapping[str, JsonValue], progress: Progress | None) -> McpResult:
        kinds = find_forbidden_in(dict(arguments))
        if kinds:
            refusal = ForbiddenArguments(kinds)
            self._logs.warning("gateway.refused", reason=refusal.reason_code, kinds=list(kinds))
            return McpResult(
                text=str(refusal), is_error=True, disposition="refused", retryable=False,
                reason_code=refusal.reason_code,
            )
        if self._asks and name == self._ask_tool.name:
            asked = parse_question(arguments)
            if isinstance(asked, str):
                return invalid_question(asked)
            answer = self._answered.get(question_key(asked.question))
            return answered_result(answer) if answer is not None else asked_result(asked)
        removed = self._removed(name)
        if removed is not None:
            return removed
        invalid = self._invalid(name, arguments)
        if invalid is not None:
            return invalid
        invocation = self._invoker.allocate()
        call = ToolCall(id=invocation, name=name, arguments=dict(arguments))
        # WP-64: a connected toolset whose saved sign-in the vendor rejected during the attempt waits the same way.
        if (
            self._connect is not None
            and (name in self._needs_authentication or self._connect.gate.need(call) is not None)
            and await self._would_run(call)
        ):
            waited = await self._connect.answer(ToolCall(id="connect", name=name, arguments=dict(arguments)))
            if waited is not None:
                return self._connect_answer(name, waited)
        # Shielded: an engine that disconnects mid-call must not cut an effect
        # between its intent and its outcome.
        work = asyncio.ensure_future(self._slotted(call, invocation))
        result = await self._ticking(asyncio.shield(work), progress)
        return self._answer(result)

    async def _slotted(self, call: ToolCall, invocation: str) -> InvokeResult:
        async with self._slots.executing():
            return await self._invoker.invoke(call, invocation_id=invocation)

    async def _would_run(self, call: ToolCall) -> bool:
        """Whether a call of an unconnected item is worth a connect wait: not one the run's rules (or its plan gate)
        refuse, which the invoker answers at once, nor a dry run's shadowed call, which never uses the credential.

        Decided as the runner decides it, over a fresh policy that has asked the org's webhook (G-40) about this very
        call: the answer is kept for the call's own invocation, so the webhook is asked once."""
        effect = self._invoker.resolve(call)
        if effect is None:
            return True
        if self._effects.shadowed(effect):
            return False
        await self._effects.refresh()
        await self._effects.consult(call, effect)
        return self._effects.approve(call, effect) != "deny"

    def _invalid(self, name: str, arguments: Mapping[str, JsonValue]) -> McpResult | None:
        """A granted tool's arguments that break its input schema are refused before anything runs (TG-05). A name
        the grant holds only through repair (a doubled name) is left to the executor's own checks."""
        schema = next((s.input_schema for s in self._invoker.granted() if s.name == name), None)
        if schema is None:
            return None
        errors = self._arguments.check(name, schema, arguments)
        if not errors:
            return None
        self._logs.info("gateway.invalid_arguments", toolName=name, fields=[e.field for e in errors])
        return McpResult(
            text=invalid_arguments_text(name, errors), is_error=True, disposition="refused",
            # Nothing ran: the same tool with corrected arguments may run.
            retryable=True, reason_code="invalid_arguments", field_errors=errors,
        )

    async def _ticking(self, work: Awaitable[InvokeResult], progress: Progress | None) -> InvokeResult:
        if progress is None:
            return await work
        ticker = asyncio.ensure_future(self._tick(progress))
        try:
            return await work
        finally:
            ticker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await ticker

    async def _tick(self, progress: Progress) -> None:
        elapsed = 0.0
        while True:
            await self._sleep(self._progress_every_s)
            elapsed += self._progress_every_s
            try:
                await progress(elapsed)
            except Exception as exc:  # the client went away; the call itself carries on
                self._logs.info("gateway.progress_stopped", errorType=type(exc).__name__)
                return

    def _answer(self, result: InvokeResult) -> McpResult:
        if isinstance(result, ToolRefusal):
            return McpResult(
                text=result.message, is_error=True, disposition="refused", retryable=False,
                invocation_id=result.invocation_id, reason_code=result.reason,
            )
        match result.disposition:
            case "executed" | "recorded":
                return self._completed(result)
            case "denied":
                return self._denied(result)
            case "held":
                return self._held(result)
            case "unknown":
                return self._unknown(result)
            case "in_flight":
                return self._in_flight(result)

    def _completed(self, result: EffectResult) -> McpResult:
        execution = result.execution
        assert execution is not None
        text = render_content(execution.content, parts=execution.parts)
        ok = execution.status == "ok"
        if execution.retry_after_s is not None and not ok:
            text = f"{text}\n\nThe service is limiting calls: wait {execution.retry_after_s:g} s before calling it again."
        untrusted = self._catalog.untrusted.pop(result.invocation_id or "", ())
        return McpResult(
            text=untrusted_note(untrusted) + text if ok and untrusted else text,
            is_error=not ok,
            disposition="executed" if result.disposition == "executed" else "recorded",
            # A definite error is "no effect": the engine may correct the call and try again.
            retryable=not ok,
            invocation_id=result.invocation_id,
            reason_code=None if ok else f"tool_{execution.status}",
            citations=self._citations.cite(text) if ok else (),
            error_code=None if ok else execution.error_code,
            retry_after_s=None if ok else execution.retry_after_s,
            untrusted_sources=untrusted if ok else (),
        )

    def _denied(self, result: EffectResult) -> McpResult:
        execution = result.execution
        return McpResult(
            text=render_content(execution.content, parts=False) if execution else "denied",
            is_error=True, disposition="denied", retryable=False,
            invocation_id=result.invocation_id, reason_code="denied",
        )

    def _connect_answer(self, name: str, answer: ConnectAnswer) -> McpResult:
        self._logs.info(
            "gateway.connect", toolName=name, state=answer.state, interactionId=answer.interaction_id,
            itemKind=answer.need.item_kind,
        )
        if answer.state == "declined":
            return McpResult(
                text=answer.text, is_error=True, disposition="denied", retryable=False,
                reason_code="denied", interaction_id=answer.interaction_id,
            )
        return McpResult(
            text=(
                f"{answer.need.item} is not connected for the user; they are asked to connect it "
                f"(interaction {answer.interaction_id}). {WHILE_WAITING} Do not call the tool again until you are "
                "told they connected it."
            ),
            is_error=False, disposition="deferred", retryable=False,
            reason_code=AUTH_REQUIRED, interaction_id=answer.interaction_id,
        )

    def _held(self, result: EffectResult) -> McpResult:
        assert result.invocation_id is not None
        pending = result.interaction_id or interaction_id(result.invocation_id, "approve")
        return McpResult(
            text=(
                f"Waiting for a person to approve this call (interaction {pending}). {WHILE_WAITING} Do not call "
                "the tool again until you are told the decision."
            ),
            is_error=False, disposition="deferred", retryable=False,
            invocation_id=result.invocation_id, reason_code="approval_pending",
            interaction_id=pending,
        )

    def _unknown(self, result: EffectResult) -> McpResult:
        return McpResult(
            text=_UNKNOWN_TEXT, is_error=True, disposition="unknown", retryable=False,
            invocation_id=result.invocation_id, reason_code="effect_unknown",
        )

    def _in_flight(self, result: EffectResult) -> McpResult:
        return McpResult(
            text=_IN_FLIGHT_TEXT, is_error=False, disposition="in_flight", retryable=False,
            invocation_id=result.invocation_id, reason_code="effect_in_flight",
        )



__all__ = ["RUN_CALL_CONCURRENCY", "WHILE_WAITING", "BoundedHold", "RunSlots", "RunToolEndpoint"]
