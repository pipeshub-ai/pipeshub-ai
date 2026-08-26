"""`MCPToolAdapter` — wraps a single MCP-discovered tool as an agent-loop `Tool`.

Parallel to `PipesHubStructuredToolAdapter` (`tool_adapter.py`): identity/description/
parameters come straight from the live-discovered `MCPToolInfo` `discovery.py` (Phase 1)
produces. There is no schema-less fallback tool anymore — a discovery failure registers
nothing for that instance (see `mcp_tool_loader.py`'s module docstring). Execution goes
through `MCPSessionManager` for per-request connection reuse + on-demand OAuth refresh,
instead of `MCPClientManager.connect()`'s per-call connect/disconnect.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from app.agent_loop_lib.tools.base import (
    _PYTHON_TYPES,
    ParameterType,
    Tool,
    ToolOutput,
    ToolParameter,
    _fuzzy_match_enum,
)
from app.agent_loop_lib.tools.errors import ToolValidationError
from app.agents.agent_loop.tool_adapter import (
    _params_from_schema,
    _to_tool_output,
    resolve_json_schema_refs,
)
from app.agents.mcp.client import MCPConnectionError
from app.agents.mcp.oauth_client import MCPOAuthError
from app.agents.mcp.token_refresh import MCPTokenRefreshError

if TYPE_CHECKING:
    from app.agents.agent_loop.mcp_access import ResolvedMCPServer
    from app.agents.agent_loop.mcp_session import MCPSessionManager
    from app.agents.mcp.models import MCPToolInfo

logger = logging.getLogger(__name__)

__all__ = ["MCPToolAdapter"]

# Matches `executor.py::_usage_hint`'s per-parameter description cap — cheap
# discovery tiers (`list_toolsets`, `search_tools` ranking) should pay a
# one-line summary, not an MCP server's full multi-paragraph tool
# description (some Rovo tools' `description` runs to 1000+ characters).
_SHORT_DESCRIPTION_MAX_LEN = 160


def _mcp_result_to_tuple(result: Any) -> tuple[bool, Any]:  # noqa: ANN401
    """Normalizes fastmcp's `CallToolResult` (`content`/`data`/`is_error`) into the
    `(success, data)` tuple format `_to_tool_output` (`tool_adapter.py`) already knows how
    to read — it unwraps a 2-tuple back to its bare `data`/error payload before returning
    `ToolOutput`, whereas a `{"success": ..., "data": ...}` dict would come back out with
    that wrapper still attached, since `clean_tool_result` only strips `REMOVE_FIELDS` keys,
    it never unwraps a nested `"data"`. `ToolResultExtractor.extract_success_status` was
    written against dict/str/tuple results, not an arbitrary SDK object — falling through to
    its generic `str(result)` substring scan for `CallToolResult` would be unreliable, so
    this reads `is_error` directly instead."""
    is_error = bool(getattr(result, "is_error", False))
    data = getattr(result, "data", None)
    if data is None:
        content = getattr(result, "content", None) or []
        text_parts = [text for block in content if (text := getattr(block, "text", None))]
        data = "\n".join(text_parts) if text_parts else (content or None)
    if is_error:
        return False, data if isinstance(data, str) else str(data)
    return True, data


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
    ) -> None:
        self._server = server
        self._tool_info = tool_info
        self._session_manager = session_manager

    @property
    def name(self) -> str:
        return self._tool_info.namespaced_name

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
    def parameters(self) -> list[ToolParameter]:
        return _params_from_schema(self._tool_info.input_schema, self.name)

    @property
    def raw_input_schema(self) -> dict[str, Any] | None:
        """The MCP server's own `inputSchema`, `$ref`/`$defs`-inlined but
        otherwise verbatim — see `Tool.raw_input_schema`'s docstring for why
        `to_schema()` needs this instead of rebuilding from `parameters`."""
        if self._tool_info.input_schema is None:
            return None
        return resolve_json_schema_refs(self._tool_info.input_schema)

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
            raw_result = await self._session_manager.call(self._server, self._tool_info.name, kwargs)
        except (MCPConnectionError, MCPTokenRefreshError, MCPOAuthError) as exc:
            return ToolOutput(success=False, error=str(exc))
        except Exception as exc:
            logger.exception("MCP tool %s raised unexpectedly", self.name)
            return ToolOutput(success=False, error=str(exc))
        return _to_tool_output(_mcp_result_to_tuple(raw_result))
