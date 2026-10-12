"""`MCPToolAdapter` — wraps a single MCP-discovered tool as an agent-loop `Tool`.

Parallel to `PipesHubStructuredToolAdapter` (`tool_adapter.py`): identity/description/
parameters come straight from the live-discovered `MCPToolInfo` `discovery.py` (Phase 1)
produces. There is no schema-less fallback tool anymore — a discovery failure registers
nothing for that instance (see `mcp_tool_loader.py`'s module docstring). Execution goes
through `MCPSessionManager` for per-request connection reuse + on-demand OAuth refresh,
instead of `MCPClientManager.connect()`'s per-call connect/disconnect.
"""
from __future__ import annotations

import asyncio
import copy
import logging
import math
import time
from contextlib import suppress
from typing import TYPE_CHECKING, Any

import httpx
import httpx2

from app.agent_loop_lib.core.messages import ImagePart, TextPart
from app.agent_loop_lib.tools.base import (
    _PYTHON_TYPES,
    ParameterType,
    Tool,
    ToolOutput,
    ToolParameter,
    _fuzzy_match_enum,
)
from app.agent_loop_lib.tools.errors import ToolValidationError
from app.agents.actions.util.result_view import Described, describe
from app.agents.actions.util.tool_summaries import first_line, inline_args
from app.agents.agent_loop.mcp_result import mcp_result_to_tool_output
from app.agents.agent_loop.mcp_sign_in import BUTTON_HINT, note_sign_in_needed
from app.agents.agent_loop.tool_adapter import (
    _params_from_schema,
    resolve_json_schema_refs,
)
from app.agents.mcp.client import (
    DEFAULT_CALL_TIMEOUT_SECONDS,
    LIST_TOOLS_TIMEOUT_SECONDS,
    MCPCallTooLongError,
    MCPConnectionError,
    MCPListingTimeoutError,
)
from app.agents.mcp.errors import (
    MCPCallInterruptedError,
    MCPHttpStatusError,
    MCPInsufficientScopeError,
    MCPToolNotOfferedError,
    first_leaf,
    is_http_unauthorized,
)
from app.agents.mcp.failure import MCPFailureReason, classify_mcp_failure
from app.agents.mcp.oauth_client import MCPOAuthError
from app.agents.mcp.token_refresh import MCPTokenRefreshError
from app.utils.url_redaction import redact_urls_in_text

if TYPE_CHECKING:
    from mcp.shared.dispatcher import ProgressFnT

    from app.agent_loop_lib.core.types import ToolResult
    from app.agents.agent_loop.context import AgentContext
    from app.agents.agent_loop.mcp_access import ResolvedMCPServer
    from app.agents.agent_loop.mcp_session import MCPSessionManager
    from app.agents.mcp.models import MCPToolInfo
    from app.agents.mcp.tool_kind import ToolKind

logger = logging.getLogger(__name__)

__all__ = ["MCPToolAdapter"]


class _StoppedByUser(Exception):
    """The user stopped the run while this call was waiting on the server."""

# Matches `executor.py::_usage_hint`'s per-parameter description cap — cheap
# discovery tiers (`list_toolsets`, `search_tools` ranking) should pay a
# one-line summary, not an MCP server's full multi-paragraph tool
# description (some Rovo tools' `description` runs to 1000+ characters).
_SHORT_DESCRIPTION_MAX_LEN = 160


_MAX_RESULT_SUMMARY_CHARS = 200
_MAX_ERROR_CHARS = 300
# A server's progress on the activity row: at most this often, this long.
_PROGRESS_INTERVAL_SECONDS = 0.5
_MAX_PROGRESS_MESSAGE_CHARS = 200


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}{'s' if count != 1 else ''}"


def _is_multimodal(content: Any) -> bool:  # noqa: ANN401
    return isinstance(content, list) and bool(content) and all(isinstance(p, (TextPart, ImagePart)) for p in content)


def _summarize_mcp_result(result: "ToolResult", described: Described | None = None) -> str:
    """One line for the tool card: what came back ("Found 23 issues"), not a repeat of it."""
    content = result.content
    if result.is_error:
        return f"Failed: {first_line(str(content or 'unknown error'))[:_MAX_RESULT_SUMMARY_CHARS]}"
    if _is_multimodal(content):
        images = sum(isinstance(p, ImagePart) for p in content)
        text = next((p.text for p in content if isinstance(p, TextPart) and p.text.strip()), "")
        summary = f"Returned {_plural(images, 'image')}"
        return f"{summary}: {first_line(text)[:_MAX_RESULT_SUMMARY_CHARS]}" if text else summary
    if described is not None and described.summary:
        return described.summary
    if isinstance(content, dict):
        for key, value in content.items():
            if isinstance(value, list):
                return f"Returned {_plural(len(value), 'item')} in {key}"
        return f"Returned {_plural(len(content), 'field')}"
    if isinstance(content, list):
        return f"Returned {_plural(len(content), 'item')}"
    text = str(content or "").strip()
    return first_line(text)[:_MAX_RESULT_SUMMARY_CHARS] if text else "No output"


_NO_COERCION = object()


def _coerce_primitive(value: Any, accepted_types: tuple[type, ...]) -> Any:  # noqa: ANN401
    """Best-effort coercion for the common LLM stringification case — e.g.
    `"5"` sent for an integer parameter, or `"true"` for a boolean one.
    Returns `_NO_COERCION` rather than guessing when `value` isn't
    unambiguously one of `accepted_types`, so a genuine type mismatch
    (a dict where a string was expected) still fails validation instead of
    being silently passed through."""
    if isinstance(value, bool):
        # `isinstance(True, int)` is True, so bool has to be rejected up
        # front or it coerces into every numeric parameter.
        return _NO_COERCION
    if isinstance(value, float) and int in accepted_types and float not in accepted_types:
        # JSON has a single number type: `5.0` for an integer parameter means
        # 5, but `isinstance(5.0, int)` is False. A fractional value is a real
        # mismatch and still rejected.
        return int(value) if value.is_integer() else _NO_COERCION
    if not isinstance(value, str):
        return _NO_COERCION
    if int in accepted_types and float not in accepted_types:
        try:
            return int(value)
        except ValueError:
            return _NO_COERCION
    if float in accepted_types:
        try:
            return float(value)
        except ValueError:
            return _NO_COERCION
    if accepted_types == (bool,) and value.lower() in ("true", "false"):
        return value.lower() == "true"
    return _NO_COERCION


class MCPToolAdapter(Tool):
    """Wraps one `MCPToolInfo` — discovered from `server` — as an agent-loop `Tool`."""

    def __init__(
        self,
        server: "ResolvedMCPServer",
        tool_info: "MCPToolInfo",
        session_manager: "MCPSessionManager",
        *,
        context: "AgentContext | None" = None,
    ) -> None:
        self._server = server
        self._tool_info = tool_info
        self._session_manager = session_manager
        # Needed only for images in results (multimodal flag, image admission);
        # without it every image degrades to a text note.
        self._context = context
        self._parameters: list[ToolParameter] | None = None
        self._raw_input_schema: dict[str, Any] | None = None
        # The last result read for the card, so its summary and view parse it once.
        self._last_described: tuple[Any, Described] | None = None

    @property
    def name(self) -> str:
        return self._tool_info.namespaced_name

    @property
    def server(self) -> "ResolvedMCPServer":
        return self._server

    @property
    def tool_info(self) -> "MCPToolInfo":
        return self._tool_info

    @property
    def instance_id(self) -> str:
        return self._server.instance_id

    @property
    def raw_name(self) -> str:
        """The server's own name for the tool. Unlike `name`, it doesn't depend on which other
        servers this request loaded."""
        return self._tool_info.name

    @property
    def kind(self) -> "ToolKind":
        """What the tool does to data (`mcp/tool_kind.py`); sets its starting approval rule."""
        return self._tool_info.kind

    @property
    def short_description(self) -> str:
        description = (self._tool_info.description or self._tool_info.name).strip()
        first_line = description.splitlines()[0] if description else description
        if len(first_line) > _SHORT_DESCRIPTION_MAX_LEN:
            return first_line[: _SHORT_DESCRIPTION_MAX_LEN - 3] + "..."
        return first_line

    @property
    def description(self) -> str:
        return self._tool_info.description or f"{self._server.display_name}: {self._tool_info.name}"

    @property
    def path(self) -> str:
        return f"/mcp/{self._server.instance_id}/{self._tool_info.name}"

    @property
    def display_name(self) -> str:
        # The namespaced name (`mcp_{type}_{tool}`) has no `__` separator, so the UI
        # cannot derive the server from it; say it here instead.
        return f"{self._server.display_name} · {self._tool_info.name}"

    def summarize_args(self, args: dict[str, Any]) -> str | None:
        return inline_args(args)

    def summarize_result(self, args: dict[str, Any], result: "ToolResult") -> str | None:
        return _summarize_mcp_result(result, self._described(result))

    def result_view(self, args: dict[str, Any], result: "ToolResult") -> dict[str, Any] | None:
        described = self._described(result)
        return described.view if described is not None else None

    def _described(self, result: "ToolResult") -> Described | None:
        content = result.content
        if result.is_error or not isinstance(content, (str, dict, list)) or _is_multimodal(content):
            return None
        # Holding the content keeps its id from being reused by another result.
        last = self._last_described
        if last is not None and last[0] is content:
            return last[1]
        described = describe(content)
        self._last_described = (content, described)
        return described

    @property
    def parameters(self) -> list[ToolParameter]:
        # Built once: the schema walk behind it is read on every call and every request build.
        if self._parameters is None:
            self._parameters = _params_from_schema(self._tool_info.input_schema, self.name)
        return list(self._parameters)

    @property
    def raw_input_schema(self) -> dict[str, Any] | None:
        """The MCP server's own `inputSchema`, `$ref`/`$defs`-inlined but
        otherwise verbatim — see `Tool.raw_input_schema`'s docstring for why
        `to_schema()` needs this instead of rebuilding from `parameters`.
        Inlined once; each caller gets its own copy."""
        if self._tool_info.input_schema is None:
            return None
        if self._raw_input_schema is None:
            self._raw_input_schema = resolve_json_schema_refs(self._tool_info.input_schema)
        return copy.deepcopy(self._raw_input_schema)

    def _properties_with_authoritative_type(self) -> frozenset[str]:
        """Property names whose schema declares exactly ONE concrete JSON
        type — the only ones a local type check can enforce without
        contradicting the server's own contract.

        `parameters` is a lossy view of the schema: `_tool_parameter_from_json_schema`
        (`tool_adapter.py`) reports STRING for a property that declares no
        `type` at all, and `_unwrap_any_of` collapses a union to its first
        non-null arm. Type-checking against that view rejects calls the real
        schema permits — an untyped property accepts any JSON value, a
        `string | array` union accepts both — and because `ToolExecutor`'s
        `_usage_hint` is built from the same `parameters`, the correction
        handed back to the model repeats the wrong type, so it cannot
        recover. Three such turns and `ToolErrorTracker` blocks the tool for
        the rest of the request.
        """
        properties = (self.raw_input_schema or {}).get("properties")
        if not isinstance(properties, dict):
            return frozenset()
        return frozenset(
            name
            for name, prop in properties.items()
            if isinstance(prop, dict)
            and isinstance(prop.get("type"), str)
            and not prop.get("anyOf")
            and not prop.get("oneOf")
        )

    def validate(self, kwargs: dict[str, Any]) -> None:
        """Shallow validation against the MCP server's own schema (via
        `parameters`, sourced from the same `raw_input_schema` above):
        required keys present, enum membership, and — only for a property
        that declares a single concrete type (see
        `_properties_with_authoritative_type`) — a loose primitive-type
        check that coerces the common case of an LLM stringifying a number
        or boolean rather than rejecting it outright.

        Deliberately NOT `Tool.validate()`'s stricter default: that also
        rejects unknown keys, which is too easy to false-positive on here —
        an MCP `additionalProperties` schema legitimately allows keys
        `parameters` doesn't know about, and a false rejection blocks a call
        that would have succeeded, which is worse than letting the server
        reject it itself. A validation failure here still turns into a
        normal, correctable `ToolOutput(success=False, ...)` for the model
        (`ToolExecutor._run`, `executor.py`) instead of an opaque provider
        400 after a network round trip — that's the point of overriding the
        old no-op mixin at all.
        """
        params_by_name = {p.name: p for p in self.parameters}
        typed_properties = self._properties_with_authoritative_type()
        for param in params_by_name.values():
            if param.name not in kwargs:
                if param.required:
                    raise ToolValidationError(
                        f"{self.path}: missing required argument '{param.name}'"
                    )
                continue

            value = kwargs[param.name]
            if value is None:
                continue

            accepted_types = (
                _PYTHON_TYPES.get(param.type) if param.name in typed_properties else None
            )
            if accepted_types:
                is_bool_leaking_into_numeric = isinstance(value, bool) and param.type in (
                    ParameterType.INTEGER, ParameterType.FLOAT,
                )
                if not isinstance(value, accepted_types) or is_bool_leaking_into_numeric:
                    coerced = (
                        _NO_COERCION if is_bool_leaking_into_numeric
                        else _coerce_primitive(value, accepted_types)
                    )
                    if coerced is _NO_COERCION:
                        raise ToolValidationError(
                            f"{self.path}: argument '{param.name}' expected type "
                            f"{param.type.value!r}, got {type(value).__name__!r}"
                        )
                    kwargs[param.name] = value = coerced

            if param.enum is not None and value not in param.enum:
                matched = _fuzzy_match_enum(value, param.enum)
                if matched is not None:
                    kwargs[param.name] = matched
                else:
                    raise ToolValidationError(
                        f"{self.path}: argument '{param.name}' must be one of "
                        f"{param.enum}, got {value!r}"
                    )

    async def execute(self, **kwargs: Any) -> ToolOutput:  # noqa: ANN401
        try:
            raw_result = await self._call_unless_stopped(kwargs)
        except _StoppedByUser:
            return ToolOutput(
                success=False,
                error=f"Stopped before the {self._server.display_name} MCP server answered. "
                "The call may still finish on the server.",
            )
        except Exception as exc:
            reason = classify_mcp_failure(exc)
            # A timeout, a dead server or an expired sign-in needs no stack trace, and the
            # error's text can carry the request URL with an API key in its query.
            logger.warning(
                "MCP tool %s failed (%s): %s: %s", self.name, reason.value, type(exc).__name__,
                redact_urls_in_text(str(exc)),
                exc_info=reason is MCPFailureReason.ERROR,
            )
            error = self._error_message(exc)
            if note_sign_in_needed(self._context, self._server, exc):
                error = f"{error} {BUTTON_HINT}"
            return ToolOutput(success=False, error=error)
        return await mcp_result_to_tool_output(raw_result, self._context)

    async def _call_unless_stopped(self, arguments: dict[str, Any]) -> Any:  # noqa: ANN401
        """The call, raced against the run's Stop. The loop only checks for Stop between tool
        calls, so without this a stopped turn waits up to the call timeout (60 s by default,
        up to 600 s) for a call nobody wants any more."""
        token = getattr(self._context, "cancellation_token", None) if self._context is not None else None
        call = asyncio.ensure_future(self._session_manager.call(
            self._server, self._tool_info.name, arguments, on_progress=self._progress_reporter(),
        ))
        if token is None:
            return await call
        stop = asyncio.ensure_future(token.wait())
        try:
            await asyncio.wait({call, stop}, return_when=asyncio.FIRST_COMPLETED)
        except asyncio.CancelledError:
            call.cancel()
            raise
        finally:
            stop.cancel()
        if call.done():
            return call.result()
        call.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await call
        raise _StoppedByUser

    def _progress_reporter(self) -> "ProgressFnT | None":
        """Shows the server's progress on the chat's activity row, at most twice a second."""
        context = self._context
        sink = getattr(context, "event_sink", None) if context is not None else None
        if sink is None:
            return None
        last_shown = -math.inf

        async def report(progress: float, total: float | None, message: str | None) -> None:
            nonlocal last_shown
            now = time.monotonic()
            if now - last_shown < _PROGRESS_INTERVAL_SECONDS:
                return
            last_shown = now
            text = " ".join(message.split())[:_MAX_PROGRESS_MESSAGE_CHARS] if message else ""
            for event in context.formatter.tool_progress(
                context, tool=self.name, progress=progress, total=total, message=text or None,
            ):
                await sink.write(event)

        return report

    def _error_message(self, exc: BaseException) -> str:
        """What the model and the tool card see. Token-endpoint bodies, request URLs (which
        can carry an API key) and stack detail stay in the server log."""
        server = self._server.display_name
        if isinstance(exc, (MCPTokenRefreshError, MCPOAuthError)):
            return (
                f"Authentication with the {server} MCP server has expired and could not be refreshed. "
                "Reconnect it in Workspace → MCP Servers."
            )
        if isinstance(exc, (MCPCallInterruptedError, MCPToolNotOfferedError, MCPInsufficientScopeError)):
            return str(exc)
        if is_http_unauthorized(exc):
            return f"The {server} MCP server rejected the stored credentials (HTTP 401). Update them in Workspace → MCP Servers."
        # Some SDK paths raise the transport task group's ExceptionGroup rather than its member.
        exc = first_leaf(exc)
        if isinstance(exc, (httpx.HTTPStatusError, httpx2.HTTPStatusError)):
            return f"The {server} MCP server returned HTTP {exc.response.status_code}."
        if isinstance(exc, MCPHttpStatusError):
            return f"The {server} MCP server returned HTTP {exc.status_code}."
        if isinstance(exc, MCPListingTimeoutError):
            return f"The {server} MCP server didn't list its tools within {LIST_TOOLS_TIMEOUT_SECONDS:.0f} seconds."
        if isinstance(exc, MCPCallTooLongError):
            return (
                f"The {server} MCP server was still working after {exc.limit_seconds:.0f} seconds, "
                "the limit for one call, so PipesHub stopped waiting. It may still finish on the server."
            )
        if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
            seconds = self._server.instance.get("callTimeoutSeconds") or DEFAULT_CALL_TIMEOUT_SECONDS
            return f"The {server} MCP server did not respond within {seconds:.0f} seconds."
        if isinstance(exc, MCPConnectionError):
            return str(exc)
        text = redact_urls_in_text(first_line(str(exc)))[:_MAX_ERROR_CHARS]
        return f"The MCP tool call failed ({type(exc).__name__}){': ' + text if text else ''}"
