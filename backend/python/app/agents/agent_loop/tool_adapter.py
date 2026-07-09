"""Adapters bridging PipesHub's tool ecosystem to agent-loop's `Tool` ABC,
without modifying any existing action file.

Two adapter classes cover every PipesHub tool category:

- `PipesHubToolAdapter` wraps a registry tool (`app.agents.tools.models.Tool`
  + its metadata) — the ~30 connector actions registered via the `@tool`
  decorator (`app.agents.tools.decorator`) and looked up through
  `_global_tools_registry`.
- `PipesHubStructuredToolAdapter` wraps an already-constructed LangChain
  `StructuredTool` — the per-request "dynamic" tools built by factory
  functions in `tool_system.py`/`app/utils/*_tool.py` (`web_search`,
  `fetch_url`, `execute_sql_query`, `fetch_slack_thread`,
  `fetch_slack_nearby_messages`, `fetch_full_record`) that have no registry
  entry to adapt from.

Special tool categories from the migration plan (retrieval mutating
`AgentContext.tool_state`, `ask_user_question` needing SSE emission,
background-artifact tools calling `register_task()`) are deliberately NOT
special-cased here: they already communicate through side effects on the
shared `tool_state` dict or through `register_task()`, both of which keep
working unmodified because `RegistryToolWrapper`/`StructuredTool` execution
is untouched. Phase 5's POST_TOOL_USE hooks observe those side effects.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from app.agent_loop_lib.tools.base import ParameterType, Tool, ToolOutput, ToolParameter
from app.agents.tools.wrapper import RegistryToolWrapper
from app.modules.agents.context.tool_descriptions import _extract_parameters_from_schema
from app.modules.agents.context.tool_result_extractor import ToolResultExtractor
from app.modules.agents.qna.nodes import clean_tool_result

if TYPE_CHECKING:
    from collections.abc import Callable

    from langchain_core.tools import StructuredTool

    from app.agents.agent_loop.context import AgentContext
    from app.agents.tools.models import Tool as RegistryTool

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
    """Anything unrecognized (e.g. Pydantic's lowercased class names for
    enums, unions, or custom models) degrades to STRING rather than raising
    — the LLM still sees a usable schema, just untyped, same trade-off
    `_extract_parameters_from_schema` already makes for the prompt text."""
    return _JSON_TYPE_TO_PARAMETER_TYPE.get((raw_type or "").lower(), ParameterType.STRING)


def _resolve_json_refs(node: Any, defs: dict[str, Any]) -> Any:  # noqa: ANN401
    """Inline every `$ref`/`$defs` in a Pydantic `model_json_schema()`
    output — LLM function-calling APIs expect a single self-contained
    schema per tool, not JSON Schema's `$ref`-to-`$defs` indirection (which
    Pydantic v2 always emits for nested `BaseModel` fields)."""
    if isinstance(node, dict):
        ref = node.get("$ref")
        if ref:
            resolved = _resolve_json_refs(defs.get(ref.rsplit("/", 1)[-1], {}), defs)
            # Sibling keys (e.g. a field-level `description` next to the
            # `$ref`) take precedence over the referenced definition's own.
            return {**resolved, **{k: v for k, v in node.items() if k != "$ref"}}
        return {k: _resolve_json_refs(v, defs) for k, v in node.items() if k != "$defs"}
    if isinstance(node, list):
        return [_resolve_json_refs(item, defs) for item in node]
    return node


def _unwrap_any_of(prop_schema: dict[str, Any]) -> dict[str, Any]:
    """Pydantic v2 emits `anyOf: [<real type>, {"type": "null"}]` for
    `Optional[X]`/`X | None` fields instead of a flat `type: X` — pick the
    first non-null variant so downstream type/nesting resolution sees the
    real shape, mirroring what `_get_field_type_name` already does for the
    plain-text tool-description path by unwrapping `Union`/`Optional`."""
    variants = prop_schema.get("anyOf") or prop_schema.get("oneOf")
    if not variants:
        return prop_schema
    non_null = [v for v in variants if v.get("type") != "null"]
    chosen = dict(non_null[0] if non_null else variants[0])
    if "description" not in chosen and "description" in prop_schema:
        chosen["description"] = prop_schema["description"]
    return chosen


def _json_schema_dict_from_source(schema: Any) -> dict[str, Any] | None:  # noqa: ANN401
    """Normalize a tool's `args_schema` — a Pydantic v1/v2 model class, or
    an already-plain JSON-schema dict — into a plain, `$ref`-free JSON
    schema dict. Returns `None` for anything else (e.g. no schema at all)."""
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
    """One JSON-schema `properties` entry -> one `ToolParameter`, recursing
    into `items`/`properties` for arrays and objects so nested structure
    (e.g. an array of Jira-filter objects) survives into the schema the LLM
    actually sees, instead of collapsing to a bare `object`/`array`."""
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
    """Shared by both adapters: converts a tool's Pydantic `args_schema`
    (or JSON-schema dict) into agent-loop's `ToolParameter` list with full
    JSON-schema fidelity — deeply nested object/array-of-object fields keep
    their inner `properties`/`items` structure (see
    `_tool_parameter_from_json_schema`), unlike the flat, text-only
    extraction `_extract_parameters_from_schema` does for the ReAct
    planner's prompt description (a lossy summary is fine there; it's never
    used as the actual function-calling schema the LLM must produce
    arguments against).

    Falls back to the old flat extraction if the schema can't be resolved
    this way (e.g. an exotic non-Pydantic `args_schema`) — a degraded but
    still-usable schema beats a hard failure to load the tool at all.
    """
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
    """Mirrors `nodes.py::ToolExecutor._execute_single_tool`'s non-retrieval
    success/content extraction (`ToolResultExtractor.extract_success_status`
    + `clean_tool_result`) so a tool routed through agent-loop produces the
    same success/failure verdict and payload shape it would on the legacy
    LangGraph path, for any of `RegistryToolWrapper.arun()`'s return shapes
    (`str`, `(bool, data)` tuple, `dict`)."""
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
        registry_tool: RegistryTool,
        app_name: str,
        tool_name: str,
        context_ref: Callable[[], AgentContext],
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
        return _to_tool_output(result)


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
        """See `PipesHubToolAdapter.app_name` — same rationale."""
        return self._app_name

    @property
    def name(self) -> str:
        return f"{self._app_name}_{self._tool_name}"

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


def split_original_tool_name(structured_tool: StructuredTool) -> tuple[str, str]:
    """Recovers `(app_name, tool_name)` from a dynamic `StructuredTool`.

    Tools created from a registry wrapper (`get_agent_tools_with_schemas`)
    carry the original dotted name on `_original_name`
    (e.g. ``"slack.fetch_slack_thread"``); standalone factory tools
    (`web_search`, `fetch_url`, `fetch_full_record`) have no dot and are
    bucketed under a synthetic ``"dynamic"`` app name.
    """
    original = getattr(structured_tool, "_original_name", None) or structured_tool.name
    if "." in original:
        app_name, tool_name = original.split(".", 1)
        return app_name, tool_name
    return "dynamic", original


__all__ = [
    "PipesHubStructuredToolAdapter",
    "PipesHubToolAdapter",
    "split_original_tool_name",
]
