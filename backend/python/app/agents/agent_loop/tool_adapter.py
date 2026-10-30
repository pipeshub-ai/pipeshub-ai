"""Adapters bridging PipesHub's dynamic tool ecosystem to agent-loop's `Tool` ABC.

`PipesHubStructuredToolAdapter` wraps per-request LangChain `StructuredTool`
objects (web_search, fetch_url, execute_sql_query, fetch_slack_thread, etc.)
that are built by factory functions and have no toolset registry entry.
"""

from __future__ import annotations

import copy
import functools
import json
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
from app.agent_loop_lib.tools.decorators import (
    accepted_argument_names,
    reject_unknown_arguments,
)
from app.agent_loop_lib.tools.errors import ToolValidationError
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


# Nodes one schema may cost while its `$ref`s are inlined. Definitions that share sub-definitions
# expand exponentially (a 2 KB schema became 17 MB), so past this a reference is left as a
# placeholder: one server's schema can't stall the event loop or flood the model's context.
MAX_INLINED_SCHEMA_NODES = 5_000


class _NodeBudget:
    __slots__ = ("remaining",)

    def __init__(self, limit: int) -> None:
        self.remaining = limit

    def spend(self) -> bool:
        """Take one node; False once the budget is gone."""
        self.remaining -= 1
        return self.remaining >= 0

    @property
    def exhausted(self) -> bool:
        return self.remaining < 0


def _ref_target(ref: str, defs: dict[str, Any], root: Any) -> Any:  # noqa: ANN401
    """What a local `$ref` points at, or None. `#/$defs/X` and `#/definitions/X` use `defs`;
    any other `#/...` pointer is walked through the document; a remote reference isn't
    fetched. A pointer that doesn't resolve falls back to its last segment in `defs`, which
    is how references were looked up before."""
    if not ref.startswith("#"):
        return None
    segments = [part.replace("~1", "/").replace("~0", "~") for part in ref[2:].split("/")] if ref[2:] else []
    if len(segments) == 2 and segments[0] in ("$defs", "definitions") and segments[1] in defs:
        return defs[segments[1]]
    node = root
    for part in segments:
        if isinstance(node, dict) and part in node:
            node = node[part]
        elif isinstance(node, list) and part.isdigit() and int(part) < len(node):
            node = node[int(part)]
        else:
            node = None
            break
    if node is not None:
        return node
    return defs.get(segments[-1]) if segments else None


def _resolve_json_refs(
    node: Any,  # noqa: ANN401
    defs: dict[str, Any],
    seen: frozenset[str] = frozenset(),
    budget: _NodeBudget | None = None,
    root: Any = None,  # noqa: ANN401
) -> Any:  # noqa: ANN401
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

    `budget` caps the nodes one schema may cost (`MAX_INLINED_SCHEMA_NODES`); once it is spent,
    a reference is left as a placeholder like a recursive one, so the walk stays linear in the
    input however the definitions are shared.
    """
    if budget is None:
        budget = _NodeBudget(MAX_INLINED_SCHEMA_NODES)
    if isinstance(node, dict):
        within_budget = budget.spend()
        ref = node.get("$ref")
        if isinstance(ref, str) and ref:
            ref_name = ref.rsplit("/", 1)[-1] or ref
            if ref in seen:
                return {
                    "type": "object",
                    "description": f"(recursive reference to '{ref_name}', not expanded further)",
                }
            if not within_budget:
                return {
                    "type": "object",
                    "description": f"(reference to '{ref_name}' not expanded: the schema is too large)",
                }
            target = _ref_target(ref, defs, root)
            if not isinstance(target, dict):
                # `{}` here would be a typeless schema, which Gemini rejects for the whole request.
                target = {"type": "object", "description": f"(reference to '{ref_name}' could not be resolved)"}
            resolved = _resolve_json_refs(target, defs, seen | {ref}, budget, root)
            # Siblings are resolved too, not copied verbatim: a `$ref` nested
            # inside one (`{"$ref": ..., "items": {"$ref": ...}}`, legal since
            # draft 2019-09) would otherwise survive to the transports, where
            # `_sanitize_tool_input_schema` drops the key outright and the
            # nested constraint is lost silently. `seen` (not `seen |
            # {ref_name}`) because a sibling is at the referencing node's
            # level, not inside the definition being expanded.
            siblings = {
                key: value if key in _NON_SCHEMA_VALUE_KEYS else _resolve_json_refs(value, defs, seen, budget, root)
                for key, value in node.items()
                if key != "$ref"
            }
            return _normalized_schema_keywords({**resolved, **siblings})
        return _normalized_schema_keywords({
            k: v if k in _NON_SCHEMA_VALUE_KEYS else _resolve_json_refs(v, defs, seen, budget, root)
            for k, v in node.items()
            if k != "$defs"
        })
    if isinstance(node, list):
        budget.spend()
        return [_resolve_json_refs(item, defs, seen, budget, root) for item in node]
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


# Resolved schemas of pydantic model classes. Every request binds the same tool
# classes, and generating plus inlining each schema was ~2% of query CPU.
_resolved_schema_by_class: dict[type, dict[str, Any] | None] = {}


def _json_schema_dict_from_source(schema: Any) -> dict[str, Any] | None:  # noqa: ANN401
    if isinstance(schema, type):
        if schema not in _resolved_schema_by_class:
            # Tools built per request can carry a fresh class each time.
            if len(_resolved_schema_by_class) >= 2000:
                _resolved_schema_by_class.clear()
            _resolved_schema_by_class[schema] = _json_schema_dict_uncached(schema)
        cached = _resolved_schema_by_class[schema]
        # Callers adjust the dict they get (descriptions, required lists).
        return copy.deepcopy(cached) if cached is not None else None
    return _json_schema_dict_uncached(schema)


def _json_schema_dict_uncached(schema: Any) -> dict[str, Any] | None:  # noqa: ANN401
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
    budget = _NodeBudget(MAX_INLINED_SCHEMA_NODES)
    resolved = _resolve_json_refs(raw, defs, budget=budget, root=raw)
    if budget.exhausted:
        logger.warning(
            "Tool schema %r exceeded %d nodes while inlining references; the rest were left as placeholders",
            raw.get("title") or "<untitled>", MAX_INLINED_SCHEMA_NODES,
        )
    return resolved


_COMBINATORS = ("anyOf", "oneOf", "allOf")
# Missing `items` is rejected by OpenAI and Gemini; a string is what every provider accepts.
_DEFAULT_ARRAY_ITEMS = {"type": "string"}


def _is_object_schema(node: Any) -> bool:  # noqa: ANN401
    return isinstance(node, dict) and (node.get("type") == "object" or "properties" in node)


def _merge_object_arms(base: dict[str, Any], arms: list[dict[str, Any]], *, all_apply: bool) -> dict[str, Any]:
    """One object from a root `anyOf`/`oneOf` (any one arm applies) or `allOf` (all apply):
    every arm's properties, and as required what every arm requires, or what any does."""
    properties = dict(base.get("properties") or {})
    for arm in arms:
        for name, prop in (arm.get("properties") or {}).items():
            properties.setdefault(name, prop)
    required_sets = [set(arm.get("required") or []) for arm in arms]
    arm_required = set.union(*required_sets) if all_apply else set.intersection(*required_sets)
    required = [name for name in properties if name in set(base.get("required") or []) | arm_required]
    merged = {k: v for k, v in base.items() if k not in (*_COMBINATORS, "properties", "required", "type")}
    merged.update(type="object", properties=properties)
    if required:
        merged["required"] = required
    return merged


def _object_root(schema: dict[str, Any]) -> dict[str, Any]:
    """A tool's parameters as one object without a combinator at the top: Anthropic rejects
    `anyOf`/`oneOf`/`allOf` there and OpenAI wants `type: object`, and one tool they reject
    fails every tool in the request."""
    combinator = next((key for key in _COMBINATORS if isinstance(schema.get(key), list) and schema[key]), None)
    if combinator:
        # A `null` arm only makes the whole argument object optional, which it already is.
        arms = [arm for arm in schema[combinator] if isinstance(arm, dict) and arm.get("type") != "null"]
        object_arms = [arm for arm in arms if _is_object_schema(arm)]
        if object_arms and (len(object_arms) == len(arms) or _is_object_schema(schema)):
            return _merge_object_arms(schema, object_arms, all_apply=combinator == "allOf")
    if _is_object_schema(schema):
        return {**{k: v for k, v in schema.items() if k not in _COMBINATORS}, "type": "object"}
    if schema.get("type") is None and not combinator and not any(k in schema for k in ("items", "enum", "const")):
        return {**schema, "type": "object", "properties": {}}
    logger.warning("Tool schema %r is not an object; the tool is offered without parameters", schema.get("title"))
    return {"type": "object", "properties": {}, **({"description": schema["description"]} if "description" in schema else {})}


def _with_array_items(node: Any) -> Any:  # noqa: ANN401
    if isinstance(node, list):
        return [_with_array_items(item) for item in node]
    if not isinstance(node, dict):
        return node
    out = {k: v if k in _NON_SCHEMA_VALUE_KEYS else _with_array_items(v) for k, v in node.items()}
    if out.get("type") == "array" and not isinstance(out.get("items"), (dict, list)):
        out["items"] = dict(_DEFAULT_ARRAY_ITEMS)
    return out


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
    if not resolved:
        return {"type": "object", "properties": {}}
    return _with_array_items(_object_root(resolved))


def _tool_parameter_from_json_schema(name: str, prop_schema: dict[str, Any], required: bool) -> ToolParameter:
    # A union's own enum applies to every arm, so it wins over the first arm's.
    own_enum = prop_schema.get("enum")
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

    enum = own_enum or prop_schema.get("enum")
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


_MAX_DERIVED_SHORT_DESCRIPTION_CHARS = 160


def _derive_short_description(description: str, fallback: str) -> str:
    """Best-effort one-liner from a full LangChain tool docstring: the
    first paragraph (up to the first blank line — the convention every
    docstring in `app/utils/*_tool.py` already follows), whitespace-
    collapsed and capped so a verbose opening paragraph still can't
    balloon the system prompt the way the full multi-paragraph docstring
    would. Callers that know a better one-liner pass `short_description`."""
    text = (description or "").strip()
    if not text:
        return fallback
    first_paragraph = text.split("\n\n", 1)[0]
    one_line = " ".join(first_paragraph.split())
    if not one_line:
        return fallback
    if len(one_line) > _MAX_DERIVED_SHORT_DESCRIPTION_CHARS:
        one_line = one_line[: _MAX_DERIVED_SHORT_DESCRIPTION_CHARS - 1].rstrip() + "…"
    return one_line


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


def _accepts_null(prop_schema: Any) -> bool:  # noqa: ANN401
    """Whether a property's resolved schema (`resolve_json_schema_refs`, which turns
    `["string", "null"]` into `nullable`) lets an explicit null through. A property the
    schema doesn't describe, or describes without a type, takes any JSON value."""
    if not isinstance(prop_schema, dict):
        return True
    if "const" in prop_schema:
        return prop_schema["const"] is None
    enum = prop_schema.get("enum")
    if isinstance(enum, list):
        return None in enum
    # Checked before `allOf`: `nullable` beside `allOf: [$ref]` is how OpenAPI 3.0 writes a
    # nullable reference.
    if prop_schema.get("nullable") is True or prop_schema.get("type") == "null":
        return True
    # The property's own type applies alongside any union or `allOf`, whose arms may be untyped.
    if "type" in prop_schema:
        return False
    all_of = prop_schema.get("allOf")
    if isinstance(all_of, list) and not all(_accepts_null(arm) for arm in all_of):
        return False
    arms = prop_schema.get("anyOf") or prop_schema.get("oneOf")
    if isinstance(arms, list) and arms:
        return any(_accepts_null(arm) for arm in arms)
    return True


def _enum_is_authoritative(prop_schema: Any) -> bool:  # noqa: ANN401
    """Whether the enum `parameters` holds for a property is its whole contract. The property's
    own enum is. A union's comes from its first arm (`_unwrap_any_of`), which is the whole
    contract only when it is the one non-null arm."""
    if not isinstance(prop_schema, dict) or isinstance(prop_schema.get("enum"), list):
        return True
    arms = prop_schema.get("anyOf") or prop_schema.get("oneOf")
    if not isinstance(arms, list):
        return True
    return sum(1 for arm in arms if not (isinstance(arm, dict) and arm.get("type") == "null")) <= 1


def _is_null_text(value: Any) -> bool:  # noqa: ANN401
    return isinstance(value, str) and value.strip().lower() == "null"


def _checked_value(
    param: ToolParameter, value: Any, *, type_is_authoritative: bool, enum_is_authoritative: bool,  # noqa: ANN401
) -> Any:  # noqa: ANN401
    """`value` as the schema takes it (a stringified number or boolean coerced, an enum
    member matched loosely), or `ToolValidationError`."""
    accepted_types = _PYTHON_TYPES.get(param.type) if type_is_authoritative else None
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
                    f"argument '{param.name}' expected type {param.type.value!r}, got {type(value).__name__!r}"
                )
            value = coerced

    if enum_is_authoritative and param.enum is not None and value not in param.enum:
        matched = _fuzzy_match_enum(value, param.enum)
        if matched is None:
            raise ToolValidationError(f"argument '{param.name}' must be one of {param.enum}, got {value!r}")
        value = matched
    return value


class SchemaShallowValidationMixin:
    """`validate` for a tool whose schema comes from outside PipesHub (an MCP server, an
    OpenAPI spec). The tool supplies `parameters` and `raw_input_schema`."""

    def _schema_properties(self) -> dict[str, Any]:
        properties = (self.raw_input_schema or {}).get("properties")
        return properties if isinstance(properties, dict) else {}

    @staticmethod
    def _properties_with_authoritative_type(properties: dict[str, Any]) -> frozenset[str]:
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
        return frozenset(
            name
            for name, prop in properties.items()
            if isinstance(prop, dict)
            and isinstance(prop.get("type"), str)
            and not prop.get("anyOf")
            and not prop.get("oneOf")
        )

    def validate(self, kwargs: dict[str, Any]) -> None:
        """Shallow validation against the tool's own schema — an MCP server's or an
        OpenAPI spec's (via
        `parameters`, sourced from the same `raw_input_schema` above):
        required keys present, enum membership (unless the enum belongs to
        one arm of a multi-type union), and — only for a property
        that declares a single concrete type (see
        `_properties_with_authoritative_type`) — a loose primitive-type
        check that coerces the common case of an LLM stringifying a number
        or boolean rather than rejecting it outright.

        Deliberately NOT `Tool.validate()`'s stricter default: that also
        rejects unknown keys, which is too easy to false-positive on here —
        a remote `additionalProperties` schema legitimately allows keys
        `parameters` doesn't know about, and a false rejection blocks a call
        that would have succeeded, which is worse than letting the server
        reject it itself. A validation failure here still turns into a
        normal, correctable `ToolOutput(success=False, ...)` for the model
        (`ToolExecutor._run`, `executor.py`) instead of an opaque provider
        400 after a network round trip — that's the point of overriding the
        old no-op mixin at all.

        Models often write `null`, or the string `"null"`, for an optional
        argument they mean to leave unset. `kwargs` goes to the server as is,
        and in JSON Schema an absent key and a null are different things: a
        server that checks its own schema refuses `"limit": null` for an
        integer, and three refusals block the tool. So an optional argument's
        null is left out unless the schema accepts null (where it can mean
        "clear this field"), and its `"null"` is left out where the schema
        would refuse that string.
        """
        params_by_name = {p.name: p for p in self.parameters}
        properties = self._schema_properties()
        typed_properties = self._properties_with_authoritative_type(properties)
        for param in params_by_name.values():
            if param.name not in kwargs:
                if param.required:
                    # No tool prefix: the message is that call's own result, and the card shows it.
                    raise ToolValidationError(f"missing required argument '{param.name}'")
                continue

            value = kwargs[param.name]
            if value is None:
                if not param.required and not _accepts_null(properties.get(param.name)):
                    del kwargs[param.name]
                continue

            try:
                kwargs[param.name] = _checked_value(
                    param, value,
                    type_is_authoritative=param.name in typed_properties,
                    enum_is_authoritative=_enum_is_authoritative(properties.get(param.name)),
                )
            except ToolValidationError:
                if param.required or not _is_null_text(value):
                    raise
                del kwargs[param.name]


class PipesHubStructuredToolAdapter(Tool):
    """Wraps a per-request dynamic LangChain `StructuredTool` (built by the
    factory functions in `tool_system.py`/`app/utils/*_tool.py`) as an
    agent-loop `Tool`. These have no `_global_tools_registry` entry, so
    identity/description/parameters come from the `StructuredTool` object
    itself rather than a `RegistryTool`."""

    def __init__(
        self,
        structured_tool: StructuredTool,
        app_name: str,
        tool_name: str,
        *,
        short_description: str | None = None,
    ) -> None:
        self._structured_tool = structured_tool
        self._app_name = app_name
        self._tool_name = tool_name
        self._short_description = short_description

    @property
    def app_name(self) -> str:
        return self._app_name

    @property
    def name(self) -> str:
        return f"{self._app_name}__{self._tool_name}"

    @property
    def short_description(self) -> str:
        if self._short_description:
            return self._short_description
        # Code-graph tools carry their one-liner in the StructuredTool's metadata.
        meta = getattr(self._structured_tool, "metadata", None) or {}
        if isinstance(meta, dict):
            short = meta.get("short_description")
            if isinstance(short, str) and short.strip():
                return short.strip()
        return _derive_short_description(self._structured_tool.description, self.name)

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
        """Only unknown argument names are rejected; dynamic tools normalize
        loosely typed values themselves."""
        reject_unknown_arguments(self._accepted_args, kwargs)

    @functools.cached_property
    def _accepted_args(self) -> frozenset[str] | None:
        target = getattr(self._structured_tool, "coroutine", None) or getattr(self._structured_tool, "func", None)
        return accepted_argument_names(target) if target is not None else None

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
