# pyright: basic
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

import copy
import functools
import inspect
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, replace
from dataclasses import field as dc_field
from typing import TYPE_CHECKING, Any

from harness_core.tools.base import Tag, Tool, ToolOutput, ToolParameter
from harness_core.tools.errors import ToolValidationError
from harness_core.tools.summarizer import ArgsFormatter, ResultFormatter

if TYPE_CHECKING:
    from harness_core.kernel.core.types import ToolCall
    from harness_core.kernel.core.types import ToolResult as CoreToolResult
    from harness_core.tools.task_complete import (
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
    from harness_core.tools.task_complete import (
        TaskCompletionOutcome,
    )
    needs_input = None
    if "ask_user_question" in (getattr(call, "name", None) or ""):
        needs_input = "Waiting for user answers"
    return TaskCompletionOutcome(
        task_done=True, final_output=fallback_text or "", needs_input=needs_input,
    )

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
        result_schema: dict[str, Any] | None = None,
        display_name: str | None = None,
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
        self._result_schema = result_schema
        self._display_name = display_name

        self.__name__ = getattr(func, "__name__", self._name)
        self.__doc__ = func.__doc__

    @property
    def name(self) -> str:
        return self.get_tool_name()

    @property
    def app_name(self) -> str:
        return self._app_name

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

    @property
    def display_name(self) -> str | None:
        return self._display_name

    @property
    def result_schema(self) -> dict[str, Any] | None:
        return self._result_schema

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
    result_schema: dict[str, Any] | None = None
    display_name: str | None = None


# ---------------------------------------------------------------------------
# Bound method tool (created by ToolsetBuilder)
# ---------------------------------------------------------------------------


def accepted_argument_names(fn: Callable[..., Any]) -> frozenset[str] | None:
    """Keyword names ``fn`` accepts, or None when it takes ``**kwargs``.

    Read from the signature, not the advertised parameters: hooks inject
    arguments a tool never advertises (attachment ids, output budgets).
    """
    try:
        params = inspect.signature(fn).parameters.values()
    except (TypeError, ValueError):
        return None
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params):
        return None
    return frozenset(
        p.name
        for p in params
        if p.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
    )


def reject_unknown_arguments(accepted: frozenset[str] | None, kwargs: dict[str, Any]) -> None:
    """Raise `ToolValidationError` naming arguments the tool cannot take.

    Without it the call dies on a bare ``TypeError``; dropping them instead
    would let the model believe a filter it invented (a date range on a
    listing) was applied.
    """
    if accepted is None:
        return
    unknown = sorted(set(kwargs) - accepted)
    if unknown:
        raise ToolValidationError(
            f"Unknown argument(s): {', '.join(unknown)}. This tool does not take them, "
            "so nothing was run; call it again without them."
        )


def required_argument_names(fn: Callable[..., Any]) -> frozenset[str]:
    """Keyword names ``fn`` cannot be called without: its parameters with no
    default. Empty when the signature cannot be read.

    Read from the signature, as ``accepted_argument_names`` is: what decides
    whether the call can bind is the function, not the schema the model saw.
    """
    try:
        params = inspect.signature(fn).parameters.values()
    except (TypeError, ValueError):
        return frozenset()
    return frozenset(
        p.name
        for p in params
        if p.default is inspect.Parameter.empty
        and p.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
    )


def reject_missing_arguments(required: frozenset[str], kwargs: dict[str, Any]) -> None:
    """Raise `ToolValidationError` naming required arguments the call left out.

    Without it the call dies on Python's bare ``TypeError`` ("missing 1
    required positional argument"), which the executor reports as an internal
    failure with no usage to correct against; a model that sent ``{}`` then
    tends to send ``{}`` again. Arguments hooks inject are already in
    ``kwargs`` here: pre-tool middleware runs before validation.
    """
    missing = sorted(required - set(kwargs))
    if missing:
        raise ToolValidationError(
            f"Missing required argument(s): {', '.join(missing)}. Nothing was run; "
            "call it again with them."
        )


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
    instance. Validation rejects argument names the method does not take and
    is otherwise permissive: connector tools normalize loosely typed values
    themselves (e.g. Pydantic model validators that handle LLM input variations).
    """

    def __init__(
        self,
        bound_method: Callable[..., Any],
        meta: ToolMeta,
        *,
        path: str | None = None,
        label: str | None = None,
        gate: Callable[[], Awaitable[None]] | None = None,
        call_scope: Callable[[], AbstractAsyncContextManager[object]] | None = None,
    ) -> None:
        self._bound_method = bound_method
        self._meta = meta
        # Awaited before every call; what it raises is the call's failure (a credential gone since the client).
        self._gate = gate
        # Entered around every call, after the gate: the vendor rate budget the call spends (G-31).
        self._call_scope = call_scope
        # The instance a model must tell apart from its type's others ("[Acme Jira] ...").
        self._label = f"[{label}] " if label else ""
        # A toolset built for one of several instances of its type lives at its own path.
        self._path = path or meta.path
        self._short_name = self._path.rsplit("/", 1)[-1]
        self._app_name = self._path.rsplit("/", 2)[-2]

    @property
    def name(self) -> str:
        return f"{self._app_name}__{self._short_name}"

    @property
    def app_name(self) -> str:
        return self._app_name

    @property
    def short_description(self) -> str:
        return self._label + self._meta.short_description

    @property
    def description(self) -> str:
        return self._label + self._meta.description

    @property
    def path(self) -> str:
        return self._path

    @property
    def declared_path(self) -> str:
        """The ``@tool`` path, whatever instance this tool was built for."""
        return self._meta.path

    @property
    def tags(self) -> list[Tag]:
        return list(self._meta.tags)

    @property
    def parameters(self) -> list[ToolParameter]:
        return list(self._meta.parameters)

    @property
    def display_name(self) -> str | None:
        return self._meta.display_name

    @property
    def result_schema(self) -> dict[str, Any] | None:
        return self._meta.result_schema

    def with_tags(self, tags: list[Tag]) -> BoundMethodTool:
        """This tool, at the same path and behind the same gate, with ``tags`` in place of its declared ones."""
        clone = copy.copy(self)
        clone._meta = replace(self._meta, tags=list(tags))
        return clone

    @property
    def bound_instance(self) -> object | None:
        """The toolset instance the method is bound to (its authenticated client)."""
        return getattr(self._bound_method, "__self__", None)

    def validate(self, kwargs: dict[str, Any]) -> None:
        """Only argument names are checked, unknown ones and missing required
        ones: connector tools normalize loosely typed values themselves (e.g.
        Pydantic validators that handle LLM input variations)."""
        reject_unknown_arguments(self._accepted_args, kwargs)
        reject_missing_arguments(self._required_args, kwargs)

    @functools.cached_property
    def _accepted_args(self) -> frozenset[str] | None:
        return accepted_argument_names(self._bound_method)

    @functools.cached_property
    def _required_args(self) -> frozenset[str]:
        return required_argument_names(self._bound_method)

    async def execute(self, **kwargs: Any) -> ToolOutput:
        if self._gate is not None:
            await self._gate()
        if self._call_scope is None:
            result = await self._bound_method(**kwargs)
        else:
            async with self._call_scope():
                result = await self._bound_method(**kwargs)
        return _normalize_legacy_output(result)

    def __repr__(self) -> str:
        return f"BoundMethodTool(path={self._path!r})"

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
    result_schema: dict[str, Any] | None = None,
    display_name: str | None = None,
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
                result_schema=result_schema,
                display_name=display_name,
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
            result_schema=result_schema,
            display_name=display_name,
        )

    return decorator
