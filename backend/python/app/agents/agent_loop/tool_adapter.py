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
from app.modules.agents.qna.helpers import clean_tool_result


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


# Keywords whose value is DATA, not a subschema — a `default` of
# `{"type": ["a", "b"]}` is a literal object the server wants back verbatim,
# so neither ref inlining nor union normalization may touch inside it.
_NON_SCHEMA_VALUE_KEYS = frozenset({"default", "enum", "const", "examples"})


def _normalized_schema_keywords(node: dict[str, Any]) -> dict[str, Any]:
    """Rewrites the two union spellings Gemini cannot consume — a list-valued
    `type` (`{"type": ["string", "null"]}`) and `oneOf` — into the ones every
    provider accepts. Both are common in real MCP schemas, and both are fatal
    rather than degrading:

    - `google.genai`'s `types.Schema.type` is a single enum, so the list form
      raises `ValueError: Invalid type: [...]` inside langchain-google-genai's
      `_format_json_schema_to_gapic`, and a pydantic `ValidationError` in
      `transport/gemini.py::_format_tools`.
    - `oneOf` is not in `_ALLOWED_SCHEMA_FIELDS_SET`, so that same converter
      drops it. A property whose schema was ONLY `oneOf` is then empty,
      `_dict_to_genai_schema` returns `None` for it, and validating
      `properties={"x": None}` fails.

    Either way the result is a NON-retryable `TransportError` that fails the
    whole turn, not just the one tool — every tool goes up in one call.
    Nothing downstream had to handle these before schemas were passed through
    verbatim, because `to_schema()` rebuilt them from `ToolParameter` and
    could only emit a single string type.

    One concrete type plus `null` becomes `nullable`; a genuine multi-type
    union becomes `anyOf`, which `_unwrap_any_of` below already treats
    interchangeably with `oneOf` anyway. Exotic keywords that carry a
    property's ONLY structure (`patternProperties`, `not`, ...) remain a gap
    on the LangChain-Gemini arm for the same "dropped, then empty" reason —
    no MCP server seen in the wild emits one.
    """
    if node.get("oneOf") and not node.get("anyOf"):
        node = {"anyOf" if k == "oneOf" else k: v for k, v in node.items()}

    raw = node.get("type")
    if not isinstance(raw, list):
        return node

    concrete = [t for t in raw if isinstance(t, str) and t != "null"]
    nullable = "null" in raw
    out = {k: v for k, v in node.items() if k != "type"}
    if len(concrete) == 1:
        out["type"] = concrete[0]
        if nullable:
            out["nullable"] = True
    elif concrete:
        arms: list[dict[str, Any]] = [{"type": t} for t in concrete]
        if nullable:
            arms.append({"type": "null"})
        out.setdefault("anyOf", arms)
    else:
        out["type"] = "null"
    return out


def _resolve_json_refs(node: Any, defs: dict[str, Any], seen: frozenset[str] = frozenset()) -> Any:  # noqa: ANN401
    """Inlines every `$ref`/`$defs` indirection in `node` into a single
    self-contained schema fragment, and normalizes union spellings on the way
    through (see `_normalized_schema_keywords`).

    `seen` guards against a self-referential (directly or mutually)
    `$defs` entry — e.g. a Jira `IssueLink` schema whose `parent` field
    `$ref`s back to `IssueLink` itself. Without it, resolving such a ref
    recurses forever and raises `RecursionError`, which callers used to
    catch and silently fall back to a flattened, typeless schema for the
    WHOLE tool (see `_params_from_schema`'s `except` below) — not just the
    recursive branch. Once a ref name is on the current resolution path, a
    revisit returns a bounded placeholder instead of recursing again; every
    OTHER branch of the schema still resolves normally.
    """
    if isinstance(node, dict):
        ref = node.get("$ref")
        if ref:
            ref_name = ref.rsplit("/", 1)[-1]
            if ref_name in seen:
                return {
                    "type": "object",
                    "description": f"(recursive reference to '{ref_name}', not expanded further)",
                }
            resolved = _resolve_json_refs(defs.get(ref_name, {}), defs, seen | {ref_name})
            # Siblings are resolved too, not copied verbatim: a `$ref` nested
            # inside one (`{"$ref": ..., "items": {"$ref": ...}}`, legal since
            # draft 2019-09) would otherwise survive to the transports, where
            # `_sanitize_tool_input_schema` drops the key outright and the
            # nested constraint is lost silently. `seen` (not `seen |
            # {ref_name}`) because a sibling is at the referencing node's
            # level, not inside the definition being expanded.
            siblings = {
                key: value if key in _NON_SCHEMA_VALUE_KEYS else _resolve_json_refs(value, defs, seen)
                for key, value in node.items()
                if key != "$ref"
            }
            return _normalized_schema_keywords({**resolved, **siblings})
        return _normalized_schema_keywords({
            k: v if k in _NON_SCHEMA_VALUE_KEYS else _resolve_json_refs(v, defs, seen)
            for k, v in node.items()
            if k != "$defs"
        })
    if isinstance(node, list):
        return [_resolve_json_refs(item, defs, seen) for item in node]
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
    # Walked even with no `$defs` to resolve: `_resolve_json_refs` also
    # normalizes the union spellings Gemini can't consume, which a schema
    # carrying no indirection at all still needs.
    return _resolve_json_refs(raw, defs)


def resolve_json_schema_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """Public entry point for `_resolve_json_refs`/`_json_schema_dict_from_source`
    — used by `MCPToolAdapter.raw_input_schema` to inline an MCP server's own
    `$ref`/`$defs` once, up front, so every transport downstream (native and
    LangChain alike) sees a self-contained schema instead of having to
    understand JSON Schema indirection itself.

    Never returns an empty schema: `AnthropicTransport._format_tools` forwards
    `input_schema` verbatim, and the API rejects `{}` (its four sibling
    transports substitute the same empty-object schema themselves). An MCP
    server that omits `inputSchema` for a zero-argument tool yields `{}` here
    (`discovery.py`'s `input_schema or {}`), and that would fail every tool in
    the request, since they all go up in one API call.
    """
    resolved = _json_schema_dict_from_source(schema)
    return resolved or {"type": "object", "properties": {}}


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


def _params_from_schema(schema: Any, tool_name: str | None = None) -> list[ToolParameter]:  # noqa: ANN401
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
        # This flattens EVERY parameter of the tool to an untyped fallback
        # (below), not just the branch that failed to resolve — worth a
        # WARNING with the tool's identity, not a DEBUG nobody sees.
        logger.warning(
            "Full JSON-schema extraction failed for tool %r, falling back to flat "
            "extraction (parameters will lose type/enum/nesting information)",
            tool_name or "<unknown>", exc_info=True,
        )

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
    if isinstance(result, str) and "<record>" in result:
        # Pre-formatted retrieval content (see `app.agents.actions.retrieval`)
        # routinely contains words like "error"/"failed"/"traceback" (bug
        # reports, incident postmortems, ...), which `ToolResultExtractor`'s
        # generic substring heuristic below would otherwise false-positive
        # on, misclassifying a successful search as a failed tool call.
        return ToolOutput(success=True, data=result)
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
        return _params_from_schema(getattr(self._structured_tool, "args_schema", None), self.name)

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
    "resolve_json_schema_refs",
    "split_original_tool_name",
]
