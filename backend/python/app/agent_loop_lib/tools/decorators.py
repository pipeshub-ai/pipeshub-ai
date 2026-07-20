"""`@tool` decorator: a single decorator for both standalone async functions
and class methods.

**Standalone function** — the decorator replaces the function with a
`FunctionTool` instance that IS a `Tool` and can be registered directly::

    @tool(
        path="/toolsets/web/echo",
        short_description="Echo input back",
        description="Returns the given text unchanged.",
        parameters=[ToolParameter("text", ParameterType.STRING, "Text to echo")],
    )
    async def echo(text: str) -> ToolOutput:
        return ToolOutput(success=True, data=text)

    registry.register_tool(echo)

**Class method** — the decorator annotates the method with `ToolMeta`
metadata without replacing it, so ``self`` binding works normally.  A
`ToolsetBuilder` (see ``toolset.py``) later collects these into
`BoundMethodTool` instances::

    class Jira:
        @tool(
            path="/tools/jira/get_projects",
            short_description="List JIRA projects",
            description="...",
        )
        async def get_projects(self) -> tuple[bool, str]:
            ...

    toolset = ToolsetBuilder(jira_instance, name="jira", ...)
    toolset.register_into(registry)

The decorator detects which mode to use by inspecting the first parameter
of the wrapped function: if it's named ``self`` or ``cls``, the function
is treated as a method (metadata-only annotation); otherwise it's wrapped
into a `FunctionTool`.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field as dc_field
from typing import TYPE_CHECKING, Any

from app.agent_loop_lib.tools.base import Tag, Tool, ToolOutput, ToolParameter
from app.agent_loop_lib.tools.summarizer import ArgsFormatter, ResultFormatter

if TYPE_CHECKING:
    from app.agent_loop_lib.core.types import ToolCall
    from app.agent_loop_lib.core.types import ToolResult as CoreToolResult
    from app.agent_loop_lib.tools.builtin.planning.task_complete import (
        TaskCompletionOutcome,
    )

__all__ = [
    "tool",
    "FunctionTool",
    "BoundMethodTool",
    "ToolMeta",
    "TOOL_META_ATTR",
]


def _default_terminal_outcome(
    tr: "CoreToolResult", call: "ToolCall", fallback_text: str
) -> "TaskCompletionOutcome":
    """Shared `TerminalTool.extract_outcome` default for `@tool`-defined
    tools (both `FunctionTool` and `BoundMethodTool`) that opt into
    `TAG_LIFECYCLE_TERMINAL` — e.g. `ask_user_question`, whose own result
    is a UI payload, not something worth returning as the run's final
    text. Unlike `TaskCompleteTool.extract_outcome`, this never refuses on
    an empty result: this tool's job was to hand something to the human
    (its own result, or an SSE side-channel a POST_TOOL_USE hook already
    emitted), not to produce the run's output — an empty `fallback_text`
    just means "nothing more to say," not "call again." A tool with more
    specific needs (custom final_output shape, its own refusal rule)
    should still override `extract_outcome` itself; this default only
    covers the common case of "stop the run, return whatever text the
    model already wrote this turn."
    """
    from app.agent_loop_lib.tools.builtin.planning.task_complete import (
        TaskCompletionOutcome,
    )
    return TaskCompletionOutcome(task_done=True, final_output=fallback_text or "")

ToolFunction = Callable[..., Awaitable[ToolOutput]]

TOOL_META_ATTR = "_agent_tool_meta"


def _safe_summarize_args(formatter: ArgsFormatter | None, args: dict[str, Any]) -> str | None:
    """Shared by `FunctionTool`/`BoundMethodTool`: a contributor's one-line
    lambda is arbitrary code, so a typo (`args["typo_key"]`) must degrade to
    "no summary" — not break the tool loop. See `Tool.summarize_args`."""
    if formatter is None:
        return None
    try:
        return formatter(args)
    except Exception:
        return None


def _safe_summarize_result(
    formatter: ResultFormatter | None, args: dict[str, Any], result: "CoreToolResult"
) -> str | None:
    if formatter is None:
        return None
    try:
        return formatter(args, result)
    except Exception:
        return None


def _is_method(func: Callable) -> bool:
    """Heuristic: a function whose first parameter is ``self`` or ``cls``
    is a method being decorated at class-body scope."""
    try:
        params = list(inspect.signature(func).parameters.keys())
    except (ValueError, TypeError):
        return False
    return bool(params) and params[0] in ("self", "cls")


# ---------------------------------------------------------------------------
# Standalone function tool
# ---------------------------------------------------------------------------


class FunctionTool(Tool):
    """A `Tool` implementation that wraps a single async function.

    The original function is preserved on `self.func` so it can be invoked
    directly (bypassing validation/middleware) in tests or scripts, e.g.
    ``await my_tool.func(query="x")``. Calling the tool instance itself
    (``await my_tool(query="x")``) goes through `Tool.__call__`, which
    validates/normalizes arguments first, exactly like any other `Tool`.
    """

    def __init__(
        self,
        func: ToolFunction,
        *,
        path: str,
        short_description: str,
        description: str,
        parameters: list[ToolParameter] | None = None,
        tags: list[Tag] | None = None,
        args_summary: ArgsFormatter | None = None,
        result_summary: ResultFormatter | None = None,
    ) -> None:
        if not inspect.iscoroutinefunction(func):
            raise TypeError(
                f"@tool can only wrap async functions; {func.__name__!r} is not a "
                "coroutine function (define it with 'async def')."
            )
        self.func = func
        self._path = path
        self._name = path.rsplit("/", 1)[-1]
        self._app_name = path.rsplit("/", 2)[-2]
        self._short_description = short_description
        self._description = description
        self._parameters = list(parameters or [])
        self._tags = list(tags or [])
        self._args_summary = args_summary
        self._result_summary = result_summary

        self.__name__ = getattr(func, "__name__", self._name)
        self.__doc__ = func.__doc__

    @property
    def name(self) -> str:
        return self.get_tool_name()

    @property
    def short_description(self) -> str:
        return self._short_description

    @property
    def description(self) -> str:
        return self._description

    @property
    def path(self) -> str:
        return self._path

    @property
    def tags(self) -> list[Tag]:
        return list(self._tags)

    @property
    def parameters(self) -> list[ToolParameter]:
        return list(self._parameters)

    async def execute(self, **kwargs: Any) -> ToolOutput:
        return await self.func(**kwargs)

    def __repr__(self) -> str:
        return f"FunctionTool(path={self._path!r})"

    def get_tool_name(self) -> str:
        """Get the tool name in the format of app_name__tool_name"""
        return f"{self._app_name}__{self._name}"

    def summarize_args(self, args: dict[str, Any]) -> str | None:
        return _safe_summarize_args(self._args_summary, args)

    def summarize_result(self, args: dict[str, Any], result: "CoreToolResult") -> str | None:
        return _safe_summarize_result(self._result_summary, args, result)

    def extract_outcome(
        self, tr: "CoreToolResult", call: "ToolCall", fallback_text: str
    ) -> "TaskCompletionOutcome":
        """See `_default_terminal_outcome` — only ever consulted by the
        turn loop when `TAG_LIFECYCLE_TERMINAL` is among this tool's own
        `tags` (set via `@tool(tags=[...])`)."""
        return _default_terminal_outcome(tr, call, fallback_text)


# ---------------------------------------------------------------------------
# Method-tool metadata (stored on the function, collected later)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolMeta:
    """Metadata stored on a class method by ``@tool`` (method mode).

    Does NOT replace the method — it's attached as ``_agent_tool_meta`` so
    the method stays callable with normal ``self`` binding.

    ``args_summary``/``result_summary`` are optional, colocated formatters
    for the agent-activity timeline (see `Tool.summarize_args`/
    `summarize_result`) — a contributor adding a new connector tool
    declares them right here alongside ``parameters``/``tags`` instead of
    editing a separate central registry.
    """

    path: str
    short_description: str
    description: str
    parameters: list[ToolParameter] = dc_field(default_factory=list)
    tags: list[Tag] = dc_field(default_factory=list)
    args_summary: ArgsFormatter | None = None
    result_summary: ResultFormatter | None = None


# ---------------------------------------------------------------------------
# Bound method tool (created by ToolsetBuilder)
# ---------------------------------------------------------------------------


def _normalize_legacy_output(result: Any) -> ToolOutput:
    """Convert legacy ``(bool, str)`` returns to `ToolOutput`.

    Connector tools (Jira, Confluence, Slack, ...) return
    ``(success: bool, json_payload: str)``. This bridges that convention
    to the `ToolOutput` envelope the agent-loop expects.
    """
    if isinstance(result, ToolOutput):
        return result
    if isinstance(result, tuple) and len(result) == 2:
        success, payload = result
        if isinstance(success, bool):
            if success:
                return ToolOutput(success=True, data=payload)
            return ToolOutput(
                success=False,
                error=payload if isinstance(payload, str) else str(payload),
            )
    return ToolOutput(success=True, data=result)


class BoundMethodTool(Tool):
    """Wraps a bound async method (with ``self`` already captured) as a `Tool`.

    Created by `ToolsetBuilder` from ``@tool``-decorated methods on a class
    instance. Validation is intentionally permissive (no-op) because
    connector tools do their own input normalization internally (e.g.
    Pydantic model validators that handle LLM input variations).
    """

    def __init__(
        self,
        bound_method: Callable[..., Any],
        meta: ToolMeta,
    ) -> None:
        self._bound_method = bound_method
        self._meta = meta
        self._short_name = meta.path.rsplit("/", 1)[-1]
        self._app_name = meta.path.rsplit("/", 2)[-2]

    @property
    def name(self) -> str:
        return f"{self._app_name}__{self._short_name}"

    @property
    def short_description(self) -> str:
        return self._meta.short_description

    @property
    def description(self) -> str:
        return self._meta.description

    @property
    def path(self) -> str:
        return self._meta.path

    @property
    def tags(self) -> list[Tag]:
        return list(self._meta.tags)

    @property
    def parameters(self) -> list[ToolParameter]:
        return list(self._meta.parameters)

    def validate(self, kwargs: dict[str, Any]) -> None:
        """Connector tools handle their own input normalization; skip strict
        validation to preserve the same lenient behavior the legacy adapter
        provided (see ``tool_adapter._PermissiveValidationMixin``)."""

    async def execute(self, **kwargs: Any) -> ToolOutput:
        result = await self._bound_method(**kwargs)
        return _normalize_legacy_output(result)

    def __repr__(self) -> str:
        return f"BoundMethodTool(path={self._meta.path!r})"

    def summarize_args(self, args: dict[str, Any]) -> str | None:
        return _safe_summarize_args(self._meta.args_summary, args)

    def summarize_result(self, args: dict[str, Any], result: "CoreToolResult") -> str | None:
        return _safe_summarize_result(self._meta.result_summary, args, result)

    def extract_outcome(
        self, tr: "CoreToolResult", call: "ToolCall", fallback_text: str
    ) -> "TaskCompletionOutcome":
        """See `_default_terminal_outcome` — only ever consulted by the
        turn loop when `TAG_LIFECYCLE_TERMINAL` is among this tool's own
        `tags` (set via `@tool(tags=[...])` on the wrapped method)."""
        return _default_terminal_outcome(tr, call, fallback_text)


# ---------------------------------------------------------------------------
# Unified @tool decorator
# ---------------------------------------------------------------------------


def tool(
    *,
    path: str,
    short_description: str,
    description: str,
    parameters: list[ToolParameter] | None = None,
    tags: list[Tag] | None = None,
    args_summary: ArgsFormatter | None = None,
    result_summary: ResultFormatter | None = None,
) -> Callable:
    """Decorator factory for both standalone async functions and class methods.

    *   **Standalone function** (no ``self``/``cls`` first param): returns a
        `FunctionTool` instance that replaces the original function.
    *   **Class method** (``self``/``cls`` first param): annotates the method
        with `ToolMeta` and returns it unchanged — a `ToolsetBuilder`
        collects these later.

    ``args_summary``/``result_summary`` are optional one-line formatters
    (see `app.agent_loop_lib.tools.summarizer.ArgsFormatter`/
    `ResultFormatter`) that turn this call's arguments/outcome into a
    short, human-readable description for the agent-activity timeline —
    declared right here, next to ``parameters``, so a contributor never
    needs to know a separate summarizer registry exists. Both default to
    `None` ("no opinion"): `agent/tool_loop.py` falls back to
    `AgentRuntime.summarizer` and, ultimately, a generic formatter.

    Example::

        @tool(
            path="/tools/jira/search_issues",
            ...,
            args_summary=lambda args: f'Searching Jira issues: "{args.get("jql", "")}"',
            result_summary=lambda args, result: f"Found {len(result.content)} issues",
        )
        async def search_issues(self, jql: str) -> tuple[bool, str]:
            ...
    """

    def decorator(func: Callable) -> Callable:
        if not inspect.iscoroutinefunction(func):
            raise TypeError(
                f"@tool can only wrap async functions; {func.__name__!r} is not a "
                "coroutine function (define it with 'async def')."
            )

        if _is_method(func):
            meta = ToolMeta(
                path=path,
                short_description=short_description,
                description=description,
                parameters=list(parameters or []),
                tags=list(tags or []),
                args_summary=args_summary,
                result_summary=result_summary,
            )
            setattr(func, TOOL_META_ATTR, meta)
            return func

        return FunctionTool(
            func,
            path=path,
            short_description=short_description,
            description=description,
            parameters=parameters,
            tags=tags,
            args_summary=args_summary,
            result_summary=result_summary,
        )

    return decorator
