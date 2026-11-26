"""A ``DurableToolPort`` over a ``ToolRegistry``: the granted tools, and nothing else.

Execution goes through ``ToolExecutor`` (argument validation, name repair,
the unknown-tool error): authorization happened when the grant was pinned
and the runtime assembled; the hooks the assembler passes are the loop's
own (PRE_TOOL_USE / POST_TOOL_USE). Effects come from the assembler, keyed
by model-visible name, so the kernel journals the instance-scoped ``toolId``
rather than one derived from the name.

A route replaces a tool's ``execute`` for calls that need run state the
tool cannot hold (the legacy loop's special routes, e.g. ``search_tools``
growing the disclosed set); the hooks still run around it. A terminal tool
(``TAG_LIFECYCLE_TERMINAL`` with ``extract_outcome``) answers the run.
"""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Awaitable, Callable
from contextlib import AbstractContextManager, nullcontext
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, cast

from pydantic import JsonValue, TypeAdapter, ValidationError

from harness_core.effects.failures import known_code
from harness_core.kernel.core.messages import ToolCall
from harness_core.kernel.core.tool_content import (
    UNKNOWN_TOOL_ERROR_PREFIX,
    tool_message_content,
)
from harness_core.kernel.core.types import ToolResult as CoreToolResult
from harness_core.kernel.durable.effects import with_call_effect
from harness_core.kernel.durable.ports import ToolExecution
from harness_core.kernel.fold import join_integrity
from harness_core.provenance.rules import UndeclaredProvenance
from harness_core.tools.executor import ToolExecutor, collapse_repeated_name
from harness_core.tools.tags import TAG_LIFECYCLE_TERMINAL
from harness_core.tools.task_complete import TerminalTool

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from harness_core.effects.types import EffectKind
    from harness_core.journal.types import NewEvent
    from harness_core.kernel.core.scope import ToolScope
    from harness_core.kernel.core.tool_schema import ToolSchema
    from harness_core.kernel.core.types import ToolResult
    from harness_core.kernel.durable.effects import CallEffect, ToolEffect
    from harness_core.kernel.durable.ports import ToolContext
    from harness_core.kernel.hooks.registry import HookRegistry
    from harness_core.logs import HarnessLog
    from harness_core.provenance.stamp import ProvenanceStamp
    from harness_core.provenance.types import ResultProvenance
    from harness_core.tools.read_cache import RunReadCache
    from harness_core.tools.registry import ToolRegistry

Integrity = Literal["trusted", "internal", "external"]

_JSON: TypeAdapter[JsonValue] = TypeAdapter(JsonValue)


def _json_content(content: object) -> tuple[JsonValue, bool]:
    """What the model reads, as the legacy loop serializes it (a dict keeps its
    key order: the journal would otherwise store it canonically, sorted), and
    whether it is the message's parts (text and images, as dicts)."""
    shown = tool_message_content(content)
    if isinstance(shown, list):
        return [cast("JsonValue", part.model_dump(mode="json")) for part in shown], True
    try:
        return _JSON.validate_python(shown), False
    except ValidationError:
        return str(shown), False


@dataclass(frozen=True)
class RouteResult:
    result: ToolResult
    # Loop state the call changed, journaled with its outcome.
    slots: Mapping[str, JsonValue] = field(default_factory=dict[str, JsonValue])
    # Presentation events the route made (a sandbox command's record), journaled with its outcome.
    events: tuple[NewEvent, ...] = ()


Route = Callable[[ToolCall], Awaitable[RouteResult]]

_CALL: ContextVar[ToolContext | None] = ContextVar("harness_tool_call", default=None)


def current_tool_context() -> ToolContext | None:
    """The kernel's context of the call this port is running (its idempotency key);
    ``None`` for a call made anywhere else (a composed child agent's, the legacy loop's)."""
    return _CALL.get()


class RegistryToolPort:
    def __init__(
        self,
        registry: ToolRegistry,
        effects: Mapping[str, ToolEffect],
        *,
        integrity: Mapping[str, Integrity] | None = None,
        asks_user: frozenset[str] = frozenset(),
        present: Callable[[ToolCall, ToolResult], Awaitable[tuple[NewEvent, ...]]]
        | None = None,
        routes: Mapping[str, Route] | None = None,
        hooks: HookRegistry | None = None,
        scope: Callable[[ToolCall], ToolScope | None] | None = None,
        late: Callable[[str], ToolEffect | None] | None = None,
        call_effects: Mapping[str, CallEffect] | None = None,
        log: HarnessLog | None = None,
        rate: Callable[[tuple[NewEvent, ...]], Integrity | None] | None = None,
        around: Callable[[], AbstractContextManager[object]] | None = None,
        provenance: ProvenanceStamp | None = None,
        read_cache: RunReadCache[ToolResult] | None = None,
        after_call: Callable[[], tuple[Mapping[str, JsonValue], tuple[NewEvent, ...]]] | None = None,
        deferred: Iterable[str] = (),
        prepare: Callable[[Sequence[str]], Awaitable[Mapping[str, str] | None]] | None = None,
        late_integrity: Callable[[str], Integrity | None] | None = None,
        more_names: Callable[[], Iterable[str]] | None = None,
        withheld: Mapping[str, str] | None = None,
    ) -> None:
        """``rate``: a call's integrity from what its presentation journals (KN-08: a knowledge result some of
        whose sources outsiders write is ``external``); it only lowers the tool's own (``integrity``).
        ``around``: entered for each call, whichever task asks for it (the loop's, a tool gateway request's), so a
        context the run's tools need (where their helpers' model calls go, WP-49) is theirs in every one.
        ``provenance``: each result recorded with its provenance after its presentation (ADR-RUN-01 D3); a granted
        tool the stamp has no rule for is refused here (``UndeclaredProvenance``). Unset (a gateway's port, whose
        endpoint stamps after its own citation delta): nothing recorded here.
        ``read_cache``: the run's cache of its declared connector reads (C7.14); a hit answers with the first answer and
        the provenance recorded for it, without calling the tool.
        ``after_call``: loop state and events the run's tools changed besides the call's own result (an MCP server's
        first-use pin and notices, D-LAZY-5), taken after each call and journaled with its outcome.
        ``deferred``: granted tools built at their source's first use (D-LAZY-3), described once ``prepare`` built
        them; a call of one builds it first. ``late_integrity``: the integrity of a tool registered mid-run (an MCP
        server's tool is ``external`` whenever it is built). ``more_names``: tools the grant gains mid-run (a server
        granted whole names its tools at its first listing, D-LAZY-2), named in ``names`` once it has.
        ``withheld``: tools this run is never given, by name, with what a call to one is told (a sub-agent's
        coordination tools: it cannot start sub-agents, ADR-RUN-04); a held tool is never withheld."""
        missing = [name for name in effects if not registry.has(name)]
        if missing:
            raise ValueError(f"effects name tools the registry lacks: {missing}")
        undeclared = [name for name in effects if provenance is not None and not provenance.declares(name)]
        if undeclared:
            raise UndeclaredProvenance(undeclared)
        self._provenance = provenance
        self._read_cache = read_cache
        self._registry = registry
        self._effects = dict(effects)
        self._integrity: dict[str, Integrity] = dict(integrity or {})
        self._asks_user = asks_user
        self._present = present
        self._routes = dict(routes or {})
        self._scope = scope
        self._late = late
        self._call_effects = dict(call_effects or {})
        self._log = log
        self._rate = rate
        self._around = around
        self._after_call = after_call
        self._deferred = frozenset(deferred)
        self._prepare = prepare
        self._late_integrity = late_integrity
        self._more_names = more_names
        self._withheld = dict(withheld or {})
        self._repaired: set[str] = set()
        self._executor = ToolExecutor(registry, hooks)

    @property
    def names(self) -> tuple[str, ...]:
        more = self._more_names() if self._more_names is not None else ()
        return tuple(sorted({*self._effects, *self._deferred, *more}))

    def describe(self, name: str) -> ToolEffect | None:
        """A name the executor resolves (a doubled name, a toolset with one
        tool) is described as the tool it runs, as the legacy loop runs it."""
        found = self._resolve(name)
        return found[1] if found is not None else None

    def describe_call(self, call: ToolCall) -> ToolEffect | None:
        """A tool with a per-call classifier is classified by this call's
        arguments; a classifier that fails leaves the tool's own class."""
        found = self._resolve(call.name)
        if found is None:
            return None
        resolved, effect = found
        classify = self._call_effects.get(resolved)
        if classify is None:
            return effect
        try:
            kind = classify(call.arguments)
        except Exception as exc:  # a classifier that fails leaves the call a write
            if self._log is not None:
                self._log.warning(
                    "tool.call_effect_failed", toolId=effect.tool_id, errorType=type(exc).__name__
                )
            return effect
        if self._log is not None:
            self._log.debug(
                "tool.call_effect", toolId=effect.tool_id, static=effect.effect, effect=kind
            )
        return with_call_effect(effect, kind)

    def _kind(self, call: ToolCall, resolved: str, effect: ToolEffect) -> EffectKind:
        """``describe_call``'s class without its logs (it already logged for this call)."""
        classify = self._call_effects.get(resolved)
        if classify is None:
            return effect.effect
        try:
            return classify(call.arguments)
        except Exception:  # a classifier that fails leaves the call a write
            return effect.effect

    def _resolve(self, name: str) -> tuple[str, ToolEffect] | None:
        effect = self._granted(name)
        if effect is not None:
            return name, effect
        resolved = self._executor.resolve_tool_name(name)
        if resolved == name:
            return None
        effect = self._granted(resolved)
        if effect is None:
            return None
        if self._log is not None and name not in self._repaired:
            self._repaired.add(name)
            self._log.info(
                "tool.name_resolved",
                toolId=effect.tool_id,
                kind="repeated" if collapse_repeated_name(name) == resolved else "toolset",
            )
        return resolved, effect

    def _granted(self, name: str) -> ToolEffect | None:
        """A tool registered mid-run (a hook grants it) is described when it appears."""
        effect = self._effects.get(name)
        if effect is None and self._late is not None and self._registry.has(name):
            effect = self._late(name)
            if effect is not None:
                self._effects[name] = effect
                integrity = self._late_integrity(name) if self._late_integrity is not None else None
                if integrity is not None and name not in self._integrity:
                    self._integrity[name] = integrity
        return effect

    def unknown(self, name: str) -> str:
        """The legacy error: a toolset name lists every tool of the toolset,
        as legacy's executor does; a tool held but not granted is plainly
        unknown (listing it would invite the same call again). A withheld tool is told why."""
        withheld = self.withheld_reason(name)
        if withheld is not None:
            return withheld
        if self._registry.has(name):
            return f"{UNKNOWN_TOOL_ERROR_PREFIX} {name}"
        refused = self._registry.refusal(name)
        if refused is not None:
            return refused.message
        return self._executor.unknown_tool_error(name)

    def withheld_reason(self, name: str) -> str | None:
        """Why this run is never given ``name``; ``None`` for any tool not withheld."""
        return None if self._registry.has(name) else self._withheld.get(name)

    def ends_run(self, name: str) -> bool:
        resolved = self._resolve(name)
        return resolved is not None and TAG_LIFECYCLE_TERMINAL in self._registry.tags_for_name(resolved[0])

    def schemas(self) -> list[ToolSchema] | None:
        return self._registry.schemas(list(self.names)) if self._effects else None

    async def execute(self, call: ToolCall, ctx: ToolContext) -> ToolExecution:
        request = ToolCall(id=call.id, name=call.name, arguments=dict(call.arguments))
        slots: dict[str, JsonValue] = {}
        routed_events: list[NewEvent] = []
        scope = self._scope(request) if self._scope is not None else None
        if self._prepare is not None and not self._registry.has(call.name):
            named = (await self._prepare([call.name]) or {}).get(call.name)
            if named is not None:
                request = request.model_copy(update={"name": named})
                call = request
        found = self._resolve(call.name)
        if found is None:
            return self._with_after_call(await self._refuse(request, scope))
        kind = self._kind(call, *found)
        resolved, effect = found
        slot = (
            self._read_cache.slot(ctx, resolved, effect, kind, request.arguments)
            if self._read_cache is not None and request.name not in self._routes
            else None
        )
        cached = self._read_cache.hit(slot) if self._read_cache is not None and slot is not None else None
        if cached is not None:
            result = cached.payload.model_copy(update={"tool_call_id": call.id})
        else:
            result = await self._run(ctx, request, scope, slots, routed_events, kind)
        if self._after_call is not None:
            changed, more = self._after_call()
            slots = {**changed, **slots}
            routed_events = [*more, *routed_events]
        answer: str | None = None
        confidence: str | None = None
        if call.name not in self._asks_user:
            result, answer, confidence = self._terminal(request, result, ctx.turn_text)
        integrity: Integrity = self._integrity.get(call.name) or "internal"
        presented = await self._present(call, result) if self._present is not None else ()
        rated = self._rate(presented) if self._rate is not None and presented else None
        if rated is not None:
            integrity = join_integrity(integrity, rated)
        events = (*routed_events, *presented)
        content, parts = _json_content(result.content)
        execution = ToolExecution(
            status="error" if result.is_error else "ok",
            content=content,
            parts=parts,
            integrity=integrity,
            needs_input=call.name in self._asks_user and not result.is_error,
            events=events,
            slots=slots,
            answer=answer,
            confidence=confidence,
            error_code=(known_code(result.error_code) or "failed") if result.is_error else None,
            retry_after_s=result.retry_after_s if result.is_error else None,
            meta=(
                cast("dict[str, JsonValue]", result.artifact_meta.model_dump(mode="json"))
                if result.artifact_meta is not None
                else None
            ),
        )
        decided: ResultProvenance | None = None
        if cached is None and self._provenance is not None:
            execution = await self._provenance.reviewed(resolved, execution)
        if cached is not None:
            execution = dataclasses.replace(execution, integrity=cast("Integrity", cached.integrity))
            decided = cached.provenance
        elif slot is not None and self._read_cache is not None:
            decided = self._provenance.provenance(resolved, execution) if self._provenance is not None else None
            self._read_cache.keep(slot, result, execution, provenance=decided)
        if self._provenance is None:
            return execution
        return self._provenance.stamp(resolved, effect.tool_id, ctx.invocation_id, execution, decided)

    async def _run(
        self,
        ctx: ToolContext,
        request: ToolCall,
        scope: ToolScope | None,
        slots: dict[str, JsonValue],
        routed_events: list[NewEvent],
        kind: EffectKind,
    ) -> ToolResult:
        # A write whose send is ambiguous must reach the effect log as unknown.
        unknown_effects = kind in ("write", "execute")
        token = _CALL.set(ctx)
        try:
            with self._around() if self._around is not None else nullcontext():
                # Each call in its own task, as the legacy turn loop dispatches every call (`asyncio.gather`): a
                # context variable a tool or its hooks set (staged sandbox inputs) stays with that call and never
                # reaches a later call, or a later run driven on the same task (DUR1-F6).
                return await asyncio.create_task(
                    self._call(
                        request, scope, slots, routed_events, unknown_effects=unknown_effects, read=kind == "read"
                    )
                )
        finally:
            _CALL.reset(token)

    async def _call(
        self,
        request: ToolCall,
        scope: ToolScope | None,
        slots: dict[str, JsonValue],
        events: list[NewEvent],
        *,
        unknown_effects: bool,
        read: bool,
    ) -> ToolResult:
        route = self._routes.get(request.name)
        if route is None:
            return await self._executor.call_tool(
                request, scope=scope, unknown_effects=unknown_effects, retry_rate_limited=read
            )

        async def routed() -> ToolResult:
            routed_result = await route(request)
            slots.update(routed_result.slots)
            events.extend(routed_result.events)
            return routed_result.result

        return await self._executor.call_tool(
            request,
            scope=scope,
            override_execute=routed,
            unknown_effects=unknown_effects,
            retry_rate_limited=read,
        )

    async def _refuse(self, call: ToolCall, scope: ToolScope | None) -> ToolExecution:
        """A tool this run does not have: the refusal takes the executor's
        path, so the tool hooks see the call as the legacy loop's do (its
        error tracker disables a name refused three turns running). Nothing
        executes, whatever the registry holds under the name."""
        message = self.unknown(call.name)

        async def refused() -> ToolResult:
            return CoreToolResult(
                tool_call_id=call.id, name=call.name, content=message, is_error=True, error_code="invalid_call"
            )

        result = await self._executor.call_tool(call, scope=scope, override_execute=refused)
        return ToolExecution(
            status="error", content=_json_content(result.content)[0], error_code=known_code(result.error_code)
        )

    def _with_after_call(self, execution: ToolExecution) -> ToolExecution:
        if self._after_call is None:
            return execution
        changed, more = self._after_call()
        if not changed and not more:
            return execution
        return dataclasses.replace(
            execution, slots={**changed, **execution.slots}, events=(*more, *execution.events)
        )

    def _terminal(
        self, call: ToolCall, result: ToolResult, turn_text: str
    ) -> tuple[ToolResult, str | None, str | None]:
        """The legacy loop's terminal-tool rule (``tool_loop.execute_tool_call``):
        the answer and the confidence the tool reported with it."""
        if result.is_error or TAG_LIFECYCLE_TERMINAL not in self._registry.tags_for_name(
            call.name
        ):
            return result, None, None
        tool = self._registry.resolve_by_name(call.name)
        if not isinstance(tool, TerminalTool):
            return result, None, None
        outcome = tool.extract_outcome(result, call, turn_text)
        if outcome.error_result is not None:
            return outcome.error_result, None, None
        if not outcome.task_done:
            return result, None, None
        confidence = getattr(outcome, "confidence", None)
        return (
            result,
            str(outcome.final_output),
            str(confidence.value) if confidence is not None else None,
        )


__all__ = ["RegistryToolPort", "Route", "RouteResult", "current_tool_context"]
