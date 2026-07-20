"""Opik LLM-call tracing as an `LLMTransport` decorator.

`LLMTransport` (see `transport/base.py`) is the ONE choke point every LLM
call in this library passes through, no matter how it got there:
`Agent.step()`'s main turn loop, the planning/critique/route_task tools,
`parse_intent`, `best_of_n`, the auto-compact summarizer
(`control_plane.py::_make_llm_summarizer`), `RubricGrader`, `SkillExtractor`
— all of them ultimately call `TransportRegistry.resolve(provider)` (either
directly or via `ModelSpec.resolve()` -> `TransportModel`) to get a
transport instance. Wrapping *there* — see `wrap_if_enabled()` below, used
by `ControlPlane.start()` at transport-registration time — traces every one
of those call sites uniformly with zero per-call-site changes, replacing
the legacy pattern of a duplicated `OpikTracer` singleton +
`config={"callbacks": [...]}` at every individual LangChain call site (see
`app/modules/agents/deep/state.py`, `app/modules/agents/qna/nodes.py`,
`app/utils/streaming.py`, `app/api/routes/agent.py` — four separate,
drifting copies). There is no LangChain layer here, so that integration
(`opik.integrations.langchain.OpikTracer`) does not apply; this uses Opik's
own manual span/trace API instead.

Correlation across a whole agent run (multiple turns, each its own LLM
call) comes for free from Opik's own context-managed tracing
(`opik.start_as_current_span`): `opik.context_storage` is itself
contextvar-based, so a span opened here automatically nests under whatever
trace/span is already active in the current asyncio task — including the
per-root-`Agent.run()` trace opened by `agent/__init__.py::Agent.run()` (see
`maybe_start_run_trace`) — with no bespoke correlation plumbing (no second
contextvar, no manual trace-id threading) needed on this side. A call made
with no active trace still gets recorded as its own standalone root trace,
which is why installing this on every transport is safe even for call sites
that run outside any `Agent.run()` (e.g. `run_from_message()`'s pre-run
`IntentParser`/`GoalBuilder` calls).

Tracing must never be able to take down a real LLM call: every Opik
SDK call here is best-effort — failures are logged at DEBUG and the
underlying transport call proceeds untraced. See `_safe_span`.
"""

from __future__ import annotations

import contextlib
import logging
import os
import types
from collections.abc import AsyncIterator, Callable
from typing import TYPE_CHECKING, Any

from app.agent_loop_lib.transport.base import LLMTransport

if TYPE_CHECKING:
    from app.agent_loop_lib.core.context import RunContext
    from app.agent_loop_lib.core.messages import Message
    from app.agent_loop_lib.core.responses import ModelResponse, StructuredResponse
    from app.agent_loop_lib.core.streaming import StreamEvent
    from app.agent_loop_lib.core.tool_schema import ToolSchema
    from app.agent_loop_lib.tools.base import ToolOutput

__all__ = [
    "OpikTracingTransport",
    "is_opik_configured",
    "wrap_if_enabled",
    "resolve_opik_gate",
    "traced_transport_factory",
    "build_langchain_opik_callbacks",
    "maybe_start_run_trace",
    "maybe_start_tool_span",
    "record_tool_span_output",
    "maybe_start_agent_span",
    "record_agent_span_result",
    "maybe_start_named_span",
    "record_named_span_output",
]

_logger = logging.getLogger(__name__)


def is_opik_configured() -> bool:
    """Whether enough Opik configuration is present in the environment to
    make tracing worthwhile. Mirrors the legacy gate (`OPIK_API_KEY` for
    Opik Cloud) but also accepts a self-hosted deployment identified by
    `OPIK_URL_OVERRIDE` — self-hosted Opik has no required API key (see
    `opik.config.OpikConfig.api_key`), which the legacy
    `OPIK_API_KEY and OPIK_WORKSPACE` gate could never satisfy."""
    return bool(os.getenv("OPIK_API_KEY")) or bool(os.getenv("OPIK_URL_OVERRIDE"))


def wrap_if_enabled(
    transport: LLMTransport, *, enabled: bool, project_name: str | None = None
) -> LLMTransport:
    """Wrap `transport` in `OpikTracingTransport` when `enabled` — the one
    call `ControlPlane.start()` makes around each transport factory. Kept
    as a free function (rather than inlining the `if` at each of the three
    call sites) so the "when do we actually trace" decision has one home."""
    if not enabled:
        return transport
    return OpikTracingTransport(transport, project_name=project_name)


def resolve_opik_gate(enabled_flag: bool) -> bool:
    """The "feature flag AND environment actually configured" AND-gate
    both `ControlPlane.start()` and `PipesHubAgentFactory.create()` use to
    decide whether Opik tracing is active for a given run/request — the
    ONE function both call, so the gate can't quietly drift between the
    two paths. (This adapter layer builds its own `TransportRegistry`/
    `AgentRuntime` directly rather than going through `ControlPlane`, so
    nothing here is inherited for free — see `factory.py::create()`.)"""
    return bool(enabled_flag) and is_opik_configured()


def traced_transport_factory(
    factory: Callable[[], LLMTransport], *, opik_active: bool, project_name: str | None = None,
) -> Callable[[], LLMTransport]:
    """Wrap a zero-arg transport factory so the transport it produces is
    traced via `wrap_if_enabled` — the exact `lambda: wrap_if_enabled(
    factory(), ...)` shape both `ControlPlane.start()`'s `_traced()`
    helper and `PipesHubAgentFactory.create()`'s inline lambda used to
    duplicate independently."""
    return lambda: wrap_if_enabled(factory(), enabled=opik_active, project_name=project_name)


def build_langchain_opik_callbacks(project_name: str | None = None) -> list[Any]:
    """One `OpikTracer` (LangChain callback handler) when Opik is
    configured, else `[]` — for the handful of adapter call sites
    (`LangChainTransport`, `intent.py`) that invoke a raw LangChain
    `BaseChatModel`/`Runnable` directly rather than through this module's
    own manual span API.

    Why both exist: our own `OpikTracingTransport`/`maybe_start_named_span`
    spans record a hand-built JSON blob good enough for tool-call/usage
    summaries, but Opik's frontend "Pretty" message view is keyed off
    providers it recognizes from raw LangChain callback data — this is how
    the legacy `chatbot.py`/`utils/streaming.py` path (confirmed rendering
    the system prompt correctly) actually gets traced: it never builds its
    own span JSON, it just hands `config={"callbacks": [opik_tracer]}` to
    `astream()`/`ainvoke()` and lets LangChain's own `on_chat_model_start`
    callback capture the exact `BaseMessage` list — including the
    `SystemMessage` — it sent to the provider. Attaching the same native
    callback at these call sites gets that same well-supported rendering
    for agent-loop's LLM calls too, nested under whatever Opik span/trace
    is already active via `opik.context_storage` (contextvar-based, shared
    with our own manual spans) — `OpikTracer`'s own span is additional
    detail alongside, not a replacement for, our manual spans.

    Built fresh per call site (never a module-level singleton, unlike the
    legacy `utils/streaming.py::opik_tracer`) to avoid one tracer's
    internal per-run-id state bleeding across concurrent requests.
    """
    if not is_opik_configured():
        return []
    try:
        from opik.integrations.langchain import OpikTracer

        return [OpikTracer(project_name=project_name)]
    except Exception:
        _logger.debug("Failed to build LangChain OpikTracer callback", exc_info=True)
        return []


def _serialize_messages(messages: "list[Message]", system: str | None = None) -> list[dict]:
    serialized = [m.model_dump(mode="json") for m in messages]
    if system:
        serialized = [{"role": "system", "content": system}, *serialized]
    return serialized


@contextlib.contextmanager
def _guarded_cm(build_cm, label: str):
    """Enter `build_cm()` (an Opik context manager — a span or a trace),
    yielding its `__enter__()` result, or a harmless `SimpleNamespace`
    stand-in if EITHER building it or entering it fails. Only exceptions
    raised by the code inside the caller's `with` block (the real work
    being traced) ever propagate out of here — Opik failures, at any stage,
    are logged at DEBUG and swallowed. Shared by `OpikTracingTransport`'s
    per-call spans and `maybe_start_run_trace`'s per-run trace: tracing must
    never be able to break a real agent run or LLM call.
    """
    try:
        cm = build_cm()
        value = cm.__enter__()
    except Exception:
        _logger.debug("Opik %s failed to start; continuing untraced", label, exc_info=True)
        yield types.SimpleNamespace()
        return

    try:
        yield value
    except BaseException as exc:
        try:
            cm.__exit__(type(exc), exc, exc.__traceback__)
        except Exception:
            _logger.debug("Opik %s failed to close on error", label, exc_info=True)
        raise
    else:
        try:
            cm.__exit__(None, None, None)
        except Exception:
            _logger.debug("Opik %s failed to close", label, exc_info=True)


def maybe_start_run_trace(
    *, enabled: bool, run_ctx: "RunContext", goal: Any, project_name: str | None = None
):
    """Context manager: opens one Opik trace for a ROOT `Agent.run()` call
    (`run_ctx.parent_run_id is None`), so every turn's LLM-call span made
    during that run nests under a single trace instead of each becoming its
    own standalone root trace (see this module's docstring). A no-op for
    disabled tracing AND for sub-agent runs — Opik's own trace context does
    not support nesting one `start_as_current_trace` inside another (the
    inner call replaces the outer trace in context rather than nesting
    under it, and un-sets it entirely on exit); sub-agent spans still nest
    correctly under whatever trace/span is already active without this.
    """
    if not enabled or run_ctx.parent_run_id is not None:
        return contextlib.nullcontext()

    def _build():
        import opik

        goal_input = goal.model_dump(mode="json") if hasattr(goal, "model_dump") else {"goal": str(goal)}
        return opik.start_as_current_trace(
            name=f"agent.{run_ctx.role_name}",
            input=goal_input,
            metadata={"run_id": run_ctx.run_id, "trace_id": run_ctx.trace_id, "model": run_ctx.model},
            project_name=project_name,
        )

    return _guarded_cm(_build, f"trace for run_id={run_ctx.run_id}")


def maybe_start_tool_span(
    *, enabled: bool, name: str, arguments: dict[str, Any], project_name: str | None = None
):
    """Context manager: opens one Opik `"tool"` span around a single tool
    execution (`ToolExecutor.call_tool()`'s `_run()` call, between
    PRE_TOOL_USE and POST_TOOL_USE — see `tools/executor.py`) so every tool
    call shows up as its own node in the trace tree, nested under whichever
    agent/LLM span is currently active via Opik's contextvar-based tracing.
    No-op when disabled — mirrors `maybe_start_run_trace`."""
    if not enabled:
        return contextlib.nullcontext()

    def _build():
        import opik

        return opik.start_as_current_span(
            name=f"tool.{name}",
            type="tool",
            input={"name": name, "arguments": arguments},
            project_name=project_name,
        )

    return _guarded_cm(_build, f"tool span {name!r}")


def record_tool_span_output(span: Any, *, result: "ToolOutput") -> None:
    """Best-effort recording of a tool call's full result onto its Opik span —
    same try/except-and-log-at-DEBUG shape as `OpikTracingTransport`'s
    `_record_completion`, so a malformed/unserializable result never breaks
    the tool call itself."""
    try:
        span.output = {
            "success": result.success,
            "data": result.data,
            "error": result.error,
            "sources": [s.model_dump(mode="json") for s in result.sources] if result.sources else [],
        }
    except Exception:
        _logger.debug("Opik tool span output recording failed", exc_info=True)


def maybe_start_agent_span(
    *, enabled: bool, role_name: str, goal: Any, project_name: str | None = None
):
    """Context manager: opens one Opik `"general"` span around a sub-agent's
    entire `Agent.run()` call (`AgentRuntime.run_child()`) so the child's own
    LLM-call/tool-call spans nest under a single `agent.{role_name}` node
    rather than appearing to run at the same level as the parent. Nests
    under whatever the caller's active span is (typically the `spawn_agent`
    tool span) automatically via Opik's contextvar-based tracing — no manual
    parent-id threading needed. The child's own `maybe_start_run_trace` call
    is already a no-op once it has a `parent_run_id`, so this span is the
    only thing marking the sub-agent boundary. No-op when disabled."""
    if not enabled:
        return contextlib.nullcontext()

    def _build():
        import opik

        goal_input = goal.model_dump(mode="json") if hasattr(goal, "model_dump") else {"goal": str(goal)}
        return opik.start_as_current_span(
            name=f"agent.{role_name}",
            type="general",
            input=goal_input,
            project_name=project_name,
        )

    return _guarded_cm(_build, f"agent span {role_name!r}")


def record_agent_span_result(span: Any, *, result: Any) -> None:
    """Best-effort recording of a sub-agent's `AgentResult` onto its Opik
    span — mirrors `record_tool_span_output`. Records the full result
    including all fields (output, tool history, usage, etc.)."""
    try:
        if hasattr(result, "model_dump"):
            span.output = result.model_dump(mode="json")
        else:
            span.output = {"result": result}
    except Exception:
        _logger.debug("Opik agent span output recording failed", exc_info=True)


def maybe_start_named_span(
    *,
    enabled: bool,
    name: str,
    span_type: str = "general",
    span_input: dict[str, Any] | None = None,
    project_name: str | None = None,
):
    """General-purpose Opik span for call sites outside the three dedicated
    choke points (`LLMTransport`, `ToolExecutor.call_tool()`,
    `AgentRuntime.run_child()`) that still make their own LLM call and want
    it to nest under whatever trace/span is already active —
    `RespondPipeline`'s synthesis phase and the PipesHub adapter's intent/
    routing call (see `agents/agent_loop/respond.py`, `.../intent.py`) both
    call raw LangChain LLMs directly rather than through a wrapped
    `LLMTransport`, so they need an explicit span of their own rather than
    inheriting one for free. No-op when disabled."""
    if not enabled:
        return contextlib.nullcontext()

    def _build():
        import opik

        return opik.start_as_current_span(
            name=name, type=span_type, input=span_input or {}, project_name=project_name,
        )

    return _guarded_cm(_build, f"span {name!r}")


def record_named_span_output(span: Any, output: Any) -> None:
    """Best-effort recording of a `maybe_start_named_span` span's result —
    same shape as `record_tool_span_output`/`record_agent_span_result`."""
    try:
        span.output = output if isinstance(output, dict) else {"output": output}
    except Exception:
        _logger.debug("Opik span output recording failed", exc_info=True)


def _usage_dict(usage: Any) -> dict[str, int]:
    input_tokens = getattr(usage, "input_tokens", 0) or 0
    output_tokens = getattr(usage, "output_tokens", 0) or 0
    return {
        "prompt_tokens": input_tokens,
        "completion_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "cache_read_tokens": getattr(usage, "cache_read_tokens", 0) or 0,
        "cache_write_tokens": getattr(usage, "cache_write_tokens", 0) or 0,
    }


class OpikTracingTransport(LLMTransport):
    """Decorates any `LLMTransport`, recording one Opik `"llm"` span per
    `complete`/`complete_structured`/`stream` call. Delegates every actual
    LLM call to `inner` unchanged — this class only observes, never alters,
    the request/response."""

    def __init__(self, inner: LLMTransport, *, project_name: str | None = None) -> None:
        self._inner = inner
        self._project_name = project_name

    @property
    def provider(self) -> str:
        return self._inner.provider

    @property
    def model_name(self) -> str:
        return self._inner.model_name

    def _safe_span(self, name: str, **span_kwargs: Any):
        """Yields a real Opik span, or a harmless stand-in if Opik itself
        fails at any point — creating the span, or tearing it down. See
        `_guarded_cm`: only exceptions raised by the WRAPPED call (the
        actual LLM request) ever propagate out of this context manager."""
        def _build():
            import opik

            return opik.start_as_current_span(
                name=name, type="llm", project_name=self._project_name, **span_kwargs
            )

        return _guarded_cm(_build, f"span {name!r}")

    def _record_completion(self, span: Any, response: "ModelResponse") -> None:
        try:
            span.output = {
                "content": response.message.model_dump(mode="json"),
                "stop_reason": response.stop_reason.value,
            }
            span.usage = _usage_dict(response.usage)
            span.model = response.model or self.model_name
        except Exception:
            _logger.debug("Opik span output/usage recording failed", exc_info=True)

    def _record_structured(self, span: Any, response: "StructuredResponse") -> None:
        try:
            span.output = response.model_dump(mode="json")
            span.usage = _usage_dict(response.usage)
            span.model = response.model or self.model_name
        except Exception:
            _logger.debug("Opik span output/usage recording failed", exc_info=True)

    async def complete(
        self,
        messages: "list[Message]",
        tools: "list[ToolSchema] | None" = None,
        system: str | None = None,
        model: str | None = None,
        thinking_budget: int | None = None,
        effort: str | None = None,
    ) -> "ModelResponse":
        span_input = {
            "messages": _serialize_messages(messages, system),
            "tools": [t.model_dump(mode="json") for t in tools] if tools else None,
        }
        metadata = {
            "thinking_budget": thinking_budget,
            "effort": effort,
        }
        with self._safe_span(
            f"{self.provider}.complete", input=span_input, model=model or self.model_name,
            provider=self.provider, metadata=metadata,
        ) as span:
            response = await self._inner.complete(
                messages=messages, tools=tools, system=system, model=model,
                thinking_budget=thinking_budget, effort=effort,
            )
            self._record_completion(span, response)
            return response

    async def complete_structured(
        self,
        messages: "list[Message]",
        output_schema: dict[str, Any],
        system: str | None = None,
        model: str | None = None,
    ) -> "StructuredResponse":
        span_input = {"messages": _serialize_messages(messages, system)}
        metadata = {"output_schema": output_schema}
        with self._safe_span(
            f"{self.provider}.complete_structured", input=span_input, model=model or self.model_name,
            provider=self.provider, metadata=metadata,
        ) as span:
            response = await self._inner.complete_structured(
                messages=messages, output_schema=output_schema, system=system, model=model,
            )
            self._record_structured(span, response)
            return response

    def stream(
        self,
        messages: "list[Message]",
        tools: "list[ToolSchema] | None" = None,
        system: str | None = None,
        model: str | None = None,
        thinking_budget: int | None = None,
        effort: str | None = None,
    ) -> AsyncIterator["StreamEvent"]:
        return self._traced_stream(messages, tools, system, model, thinking_budget, effort)

    async def _traced_stream(
        self,
        messages: "list[Message]",
        tools: "list[ToolSchema] | None",
        system: str | None,
        model: str | None,
        thinking_budget: int | None,
        effort: str | None,
    ) -> AsyncIterator["StreamEvent"]:
        from app.agent_loop_lib.core.streaming import StreamCompleteEvent

        span_input = {
            "messages": _serialize_messages(messages, system),
            "tools": [t.model_dump(mode="json") for t in tools] if tools else None,
        }
        metadata = {
            "thinking_budget": thinking_budget,
            "effort": effort,
            "streaming": True,
        }
        with self._safe_span(
            f"{self.provider}.stream", input=span_input, model=model or self.model_name,
            provider=self.provider, metadata=metadata,
        ) as span:
            final_response: "ModelResponse | None" = None
            async for event in self._inner.stream(
                messages=messages, tools=tools, system=system, model=model,
                thinking_budget=thinking_budget, effort=effort,
            ):
                if isinstance(event, StreamCompleteEvent):
                    final_response = event.response
                yield event
            if final_response is not None:
                self._record_completion(span, final_response)
