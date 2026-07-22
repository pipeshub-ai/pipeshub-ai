"""Adapters bridging PipesHub's dynamic tool ecosystem to agent-loop's `Tool` ABC.

`PipesHubStructuredToolAdapter` wraps per-request LangChain `StructuredTool`
objects (web_search, fetch_url, execute_sql_query, fetch_slack_thread, etc.)
that are built by factory functions and have no toolset registry entry.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from app.agent_loop_lib.tools.base import ParameterType, Tool, ToolOutput, ToolParameter
from app.modules.agents.context.tool_descriptions import _extract_parameters_from_schema
from app.modules.agents.context.tool_result_extractor import ToolResultExtractor
from app.modules.agents.qna.nodes import clean_tool_result


if TYPE_CHECKING:
    from langchain_core.tools import StructuredTool

logger = logging.getLogger(__name__)

_JSON_TYPE_TO_PARAMETER_TYPE: dict[str, ParameterType] = {
    "string": ParameterType.STRING,
    "str": ParameterType.STRING,
    "integer": ParameterType.INTEGER,
    "int": ParameterType.INTEGER,
    "number": ParameterType.FLOAT,
    "float": ParameterType.FLOAT,
    "boolean": ParameterType.BOOLEAN,
    "bool": ParameterType.BOOLEAN,
    "array": ParameterType.ARRAY,
    "list": ParameterType.ARRAY,
    "object": ParameterType.OBJECT,
    "dict": ParameterType.OBJECT,
}


def _resolve_parameter_type(raw_type: str) -> ParameterType:
    return _JSON_TYPE_TO_PARAMETER_TYPE.get((raw_type or "").lower(), ParameterType.STRING)


def _resolve_json_refs(node: Any, defs: dict[str, Any]) -> Any:  # noqa: ANN401
    if isinstance(node, dict):
        ref = node.get("$ref")
        if ref:
            resolved = _resolve_json_refs(defs.get(ref.rsplit("/", 1)[-1], {}), defs)
            return {**resolved, **{k: v for k, v in node.items() if k != "$ref"}}
        return {k: _resolve_json_refs(v, defs) for k, v in node.items() if k != "$defs"}
    if isinstance(node, list):
        return [_resolve_json_refs(item, defs) for item in node]
    return node


def _unwrap_any_of(prop_schema: dict[str, Any]) -> dict[str, Any]:
    variants = prop_schema.get("anyOf") or prop_schema.get("oneOf")
    if not variants:
        return prop_schema
    non_null = [v for v in variants if v.get("type") != "null"]
    chosen = dict(non_null[0] if non_null else variants[0])
    if "description" not in chosen and "description" in prop_schema:
        chosen["description"] = prop_schema["description"]
    return chosen


def _json_schema_dict_from_source(schema: Any) -> dict[str, Any] | None:  # noqa: ANN401
    if schema is None:
        return None
    if isinstance(schema, dict):
        raw = schema
    elif hasattr(schema, "model_json_schema"):
        raw = schema.model_json_schema()
    elif hasattr(schema, "schema"):
        raw = schema.schema()
    else:
        return None
    defs = raw.get("$defs") or raw.get("definitions") or {}
    return _resolve_json_refs(raw, defs) if defs else raw


def _tool_parameter_from_json_schema(name: str, prop_schema: dict[str, Any], required: bool) -> ToolParameter:
    prop_schema = _unwrap_any_of(prop_schema)
    raw_type = prop_schema.get("type")
    param_type = _resolve_parameter_type(raw_type if isinstance(raw_type, str) else "string")

    items: dict[str, Any] | None = None
    properties: dict[str, Any] | None = None
    required_properties: list[str] | None = None
    if param_type == ParameterType.ARRAY:
        items = prop_schema.get("items") or {"type": "string"}
    elif param_type == ParameterType.OBJECT:
        properties = prop_schema.get("properties") or None
        nested_required = prop_schema.get("required")
        required_properties = list(nested_required) if nested_required else None

    enum = prop_schema.get("enum")
    return ToolParameter(
        name=name,
        type=param_type,
        description=prop_schema.get("description") or name,
        required=required,
        enum=list(enum) if enum else None,
        items=items,
        properties=properties,
        required_properties=required_properties,
    )


def _params_from_schema(schema: Any) -> list[ToolParameter]:  # noqa: ANN401
    if schema is None:
        return []
    try:
        json_schema = _json_schema_dict_from_source(schema)
        if json_schema is not None:
            properties = json_schema.get("properties") or {}
            required = set(json_schema.get("required") or [])
            return [
                _tool_parameter_from_json_schema(param_name, prop_schema, param_name in required)
                for param_name, prop_schema in properties.items()
            ]
    except Exception:
        logger.debug("Full JSON-schema extraction failed for %r, falling back to flat extraction", schema, exc_info=True)

    extracted = _extract_parameters_from_schema(schema, logger)
    return [
        ToolParameter(
            name=param_name,
            type=_resolve_parameter_type(info.get("type", "string")),
            description=info.get("description") or param_name,
            required=bool(info.get("required")),
        )
        for param_name, info in extracted.items()
    ]


def _to_tool_output(result: Any) -> ToolOutput:  # noqa: ANN401
    success = ToolResultExtractor.extract_success_status(result)
    content = clean_tool_result(result)
    if isinstance(content, tuple) and len(content) == 2:
        _, content = content
    if success:
        return ToolOutput(success=True, data=content)
    return ToolOutput(success=False, error=_stringify(content))


def _stringify(payload: Any) -> str:  # noqa: ANN401
    if isinstance(payload, str):
        return payload
    try:
        return json.dumps(payload, default=str)
    except TypeError:
        return str(payload)


def _is_toolset_auth_error(result: Any) -> bool:  # noqa: ANN401
    """Detects the `error_type: "toolset_auth"` marker
    `RegistryToolWrapper._format_error` adds (additively) when the failure
    traces back to a `ToolsetAuthError` — i.e. the connector IS attached
    but its credentials are missing/invalid, as opposed to a generic tool
    failure. `result` is `RegistryToolWrapper.arun()`'s raw return value,
    always a JSON string for the error path."""
    if not isinstance(result, str):
        return False
    try:
        payload = json.loads(result)
    except (json.JSONDecodeError, TypeError):
        return False
    return isinstance(payload, dict) and payload.get("error_type") == "toolset_auth"


async def _emit_tool_unavailable(
    context: "AgentContext", *, tool_name: str, toolset: str, reason: str, message: str | None,
) -> None:
    """Emits the same `{"event": "tool_unavailable", ...}` SSE shape
    `SSEEventEmitter` produces for the search-time fallback (see
    `EventType.TOOL_UNAVAILABLE`), but directly through `event_sink` since
    this fires from inside `Tool.execute()`, below the `Agent`/`emit()`
    layer a `RouteContext` would give access to."""
    if context.event_sink is None:
        return
    try:
        for evt in context.formatter.tool_unavailable(
            context, tool=tool_name, toolset=toolset, reason=reason, message=message,
        ):
            await context.event_sink.write(evt)
    except Exception:
        logger.debug("Failed to emit tool_unavailable SSE event for %s", tool_name, exc_info=True)


class _PermissiveValidationMixin:
    """PipesHub tools tolerate loosely-typed LLM tool-call arguments today
    (no Pydantic re-validation happens between `RegistryToolWrapper.arun()`
    and the underlying action — see `_execute_class_method_async`, which
    just filters to known parameter names). `agent_loop.tools.base.Tool`'s
    default `validate()` is strict (rejects unknown keys, raises on type
    mismatches like an LLM sending `"5"` for an int field) and runs BEFORE
    `execute()` inside `ToolExecutor._run()`, so overriding it here is the
    only way to preserve the legacy path's lenient behavior — real failures
    still surface as a normal `ToolOutput(success=False, ...)` from
    `execute()`'s own error handling instead of a hard `ToolValidationError`.
    """

    def validate(self, kwargs: dict[str, Any]) -> None:
        return


class PipesHubToolAdapter(_PermissiveValidationMixin, Tool):
    """Wraps a single `_global_tools_registry` tool as an agent-loop `Tool`."""

    def __init__(
        self,
        registry_tool: "RegistryTool",
        app_name: str,
        tool_name: str,
        context_ref: Callable[[], "AgentContext"],
    ) -> None:
        self._registry_tool = registry_tool
        self._app_name = app_name
        self._tool_name = tool_name
        self._context_ref = context_ref

    @property
    def app_name(self) -> str:
        """Public accessor for domain-grouping consumers (e.g. Phase 10's
        `OrchestratorLoop`, which groups registered tool names by domain to
        build each `spawn_agent` call's explicit `tools` list) — everything
        else on this class already needs `_app_name` privately, so this is
        the one place it's exposed outside the adapter itself."""
        return self._app_name

    @property
    def name(self) -> str:
        return f"{self._app_name}_{self._tool_name}"

    @property
    def short_description(self) -> str:
        return self._registry_tool.description or f"{self._app_name} {self._tool_name}"

    @property
    def description(self) -> str:
        # Per-tool guidance (Layer 1 of the three-layer prompt system):
        # llm_description + when_to_use/when_not_to_use, carried directly on
        # the schema agent-loop sends the LLM — see Phase 3 plan notes on
        # why this replaces a separate per-tool guidance layer.
        parts: list[str] = []
        if self._registry_tool.llm_description:
            parts.append(self._registry_tool.llm_description)
        if self._registry_tool.when_to_use:
            parts.append("When to use: " + "; ".join(self._registry_tool.when_to_use))
        if self._registry_tool.when_not_to_use:
            parts.append("When NOT to use: " + "; ".join(self._registry_tool.when_not_to_use))
        return "\n".join(parts) or self.short_description

    @property
    def path(self) -> str:
        return f"/connectors/{self._app_name}/{self._tool_name}"

    @property
    def parameters(self) -> list[ToolParameter]:
        return _params_from_schema(self._registry_tool.args_schema)

    async def execute(self, **kwargs: Any) -> ToolOutput:  # noqa: ANN401
        ctx = self._context_ref()
        wrapper = RegistryToolWrapper(
            self._app_name, self._tool_name, self._registry_tool, ctx.tool_state
        )
        try:
            result = await wrapper.arun(kwargs)
        except Exception as exc:
            logger.exception("Tool %s raised outside RegistryToolWrapper's own error handling", self.name)
            return ToolOutput(success=False, error=str(exc))
        output = _to_tool_output(result)
        if not output.success and _is_toolset_auth_error(result):
            await _emit_tool_unavailable(
                ctx, tool_name=self.name, toolset=self._app_name,
                reason="not_authenticated", message=output.error,
            )
        return output


class PipesHubStructuredToolAdapter(_PermissiveValidationMixin, Tool):
    """Wraps a per-request dynamic LangChain `StructuredTool` (built by the
    factory functions in `tool_system.py`/`app/utils/*_tool.py`) as an
    agent-loop `Tool`. These have no `_global_tools_registry` entry, so
    identity/description/parameters come from the `StructuredTool` object
    itself rather than a `RegistryTool`."""

    def __init__(self, structured_tool: StructuredTool, app_name: str, tool_name: str) -> None:
        self._structured_tool = structured_tool
        self._app_name = app_name
        self._tool_name = tool_name

    @property
    def app_name(self) -> str:
        return self._app_name

    @property
    def name(self) -> str:
        return f"{self._app_name}__{self._tool_name}"

    @property
    def short_description(self) -> str:
        return self._structured_tool.description or self.name

    @property
    def description(self) -> str:
        return self._structured_tool.description or self.name

    @property
    def path(self) -> str:
        return f"/dynamic/{self._app_name}/{self._tool_name}"

    @property
    def parameters(self) -> list[ToolParameter]:
        return _params_from_schema(getattr(self._structured_tool, "args_schema", None))

    def validate(self, kwargs: dict[str, Any]) -> None:
        """Permissive validation — dynamic tools handle their own input normalization."""

    async def execute(self, **kwargs: Any) -> ToolOutput:  # noqa: ANN401
        coroutine = getattr(self._structured_tool, "coroutine", None)
        try:
            if coroutine is not None:
                result = await coroutine(**kwargs)
            else:
                result = self._structured_tool.func(**kwargs)
        except Exception as exc:
            logger.exception("Dynamic tool %s failed", self.name)
            return ToolOutput(success=False, error=str(exc))
        return _to_tool_output(result)


def split_original_tool_name(structured_tool: "StructuredTool") -> tuple[str, str]:
    original = getattr(structured_tool, "_original_name", None) or structured_tool.name
    if "." in original:
        app_name, tool_name = original.split(".", 1)
        return app_name, tool_name
    return "dynamic", original


__all__ = [
    "PipesHubStructuredToolAdapter",
    "_to_tool_output",
    "split_original_tool_name",
]
