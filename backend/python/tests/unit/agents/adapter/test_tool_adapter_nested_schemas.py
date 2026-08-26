"""Nested tool-parameter schema conversion (`app/agents/agent_loop/
tool_adapter.py::_params_from_schema`) — the fix for the "flat tool schema"
non-standard implementation flagged in `tool_adapter.py`'s old docstring:
deeply nested object/array-of-object `args_schema` fields must keep their
inner `properties`/`items` structure in the `ToolSchema` the LLM actually
sees, not collapse to a bare `{"type": "object"}`/`{"type": "array"}`.

Also proves the fix survives the OTHER direction of this same round-trip —
`converters.py::convert_tool_schema_to_langchain_dict` (used by
`LangChainTransport._bind_tools`) passes `ToolSchema.input_schema` straight
through to the OpenAI function-calling dict shape; this suite checks the
two ends actually connect for a realistic nested tool, plus the MCP-sourced
schema-fidelity regressions (enum/default/bounds/additionalProperties/
cyclic $defs) proven in the tool schema fidelity investigation.
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app.agent_loop_lib.tools.base import ParameterType
from app.agents.agent_loop.converters import convert_tool_schema_to_langchain_dict
from app.agents.agent_loop.tool_adapter import (
    PipesHubStructuredToolAdapter,
    _params_from_schema,
    resolve_json_schema_refs,
)
from app.agents.agent_loop.mcp_access import ResolvedMCPServer
from app.agents.agent_loop.mcp_tool_adapter import MCPToolAdapter
from app.agents.mcp.models import MCPToolInfo


class _JiraFilter(BaseModel):
    field: str = Field(description="Field to filter on")
    value: str = Field(description="Value to match")


class _JiraSearchArgs(BaseModel):
    project: str = Field(description="Project key")
    filters: list[_JiraFilter] = Field(default_factory=list, description="Filters to apply")
    options: dict[str, Any] | None = Field(default=None, description="Extra options")
    max_results: int = Field(default=50, description="Max results to return")


def _param(params: list, name: str):
    return next(p for p in params if p.name == name)


class TestParamsFromSchemaNesting:
    def test_flat_string_and_int_fields_still_work(self) -> None:
        params = _params_from_schema(_JiraSearchArgs)

        project = _param(params, "project")
        assert project.type == ParameterType.STRING
        assert project.required is True

        max_results = _param(params, "max_results")
        assert max_results.type == ParameterType.INTEGER
        assert max_results.required is False

    def test_array_of_objects_preserves_nested_properties(self) -> None:
        params = _params_from_schema(_JiraSearchArgs)

        filters = _param(params, "filters")
        assert filters.type == ParameterType.ARRAY
        assert filters.items is not None
        assert filters.items.get("type") == "object"
        nested_props = filters.items.get("properties") or {}
        assert set(nested_props.keys()) == {"field", "value"}
        assert nested_props["field"]["type"] == "string"

    def test_optional_object_field_unwraps_any_of_and_keeps_object_type(self) -> None:
        """`dict[str, Any] | None` renders as `anyOf: [{"type": "object"},
        {"type": "null"}]` in Pydantic v2 — must resolve to a plain
        `object` param, not fall back to STRING."""
        params = _params_from_schema(_JiraSearchArgs)

        options = _param(params, "options")
        assert options.type == ParameterType.OBJECT
        assert options.required is False

    def test_doubly_nested_object_in_array_keeps_full_depth(self) -> None:
        class _Address(BaseModel):
            city: str
            zip_codes: list[str] = Field(default_factory=list)

        class _Contact(BaseModel):
            name: str
            addresses: list[_Address] = Field(default_factory=list)

        class _Args(BaseModel):
            contacts: list[_Contact] = Field(default_factory=list)

        params = _params_from_schema(_Args)
        contacts = _param(params, "contacts")
        assert contacts.type == ParameterType.ARRAY
        contact_item_schema = contacts.items
        assert contact_item_schema["type"] == "object"
        address_field_schema = contact_item_schema["properties"]["addresses"]
        assert address_field_schema["type"] == "array"
        address_item_schema = address_field_schema["items"]
        assert address_item_schema["type"] == "object"
        assert address_item_schema["properties"]["zip_codes"]["type"] == "array"

    def test_no_dollar_ref_leaks_into_output_schema(self) -> None:
        """Pydantic v2 emits `$ref`/`$defs` for nested `BaseModel` fields by
        default — none of that JSON-Schema indirection should survive into
        the `ToolParameter`s (LLM function-calling schemas expect a single
        self-contained object, not a `$ref` the model has to resolve)."""
        params = _params_from_schema(_JiraSearchArgs)
        filters = _param(params, "filters")

        assert "$ref" not in str(filters.items)

    def test_empty_schema_yields_no_params(self) -> None:
        assert _params_from_schema(None) == []

    def test_plain_dict_json_schema_also_supported(self) -> None:
        """`args_schema` may already be a raw JSON-schema dict (not a
        Pydantic model) — same nested-preservation behavior applies."""
        schema = {
            "type": "object",
            "properties": {
                "tags": {
                    "type": "array",
                    "description": "Tags",
                    "items": {"type": "object", "properties": {"key": {"type": "string"}}},
                },
            },
            "required": ["tags"],
        }
        params = _params_from_schema(schema)
        tags = _param(params, "tags")
        assert tags.type == ParameterType.ARRAY
        assert tags.items["properties"]["key"]["type"] == "string"
        assert tags.required is True


def _make_jira_search_adapter() -> PipesHubStructuredToolAdapter:
    structured_tool = StructuredTool.from_function(
        name="search", description="search jira",
        args_schema=_JiraSearchArgs, func=lambda **kwargs: "ok",
    )
    return PipesHubStructuredToolAdapter(structured_tool, "jira", "search")


class TestToolAdapterToSchemaRoundTrip:
    """`PipesHubStructuredToolAdapter.to_schema()` (inherited default from
    `Tool`) must carry the nested structure all the way into
    `ToolSchema.input_schema`, and `LangChainTransport._bind_tools`'s
    conversion to the OpenAI function-calling dict shape (via
    `convert_tool_schema_to_langchain_dict`) must pass it through without
    flattening it away."""

    def test_to_schema_input_schema_has_nested_properties(self) -> None:
        adapter = _make_jira_search_adapter()

        schema = adapter.to_schema()
        filters_schema = schema.input_schema["properties"]["filters"]
        assert filters_schema["type"] == "array"
        assert filters_schema["items"]["properties"]["field"]["type"] == "string"

    def test_langchain_round_trip_preserves_nested_array_of_objects(self) -> None:
        """`convert_tool_schema_to_langchain_dict` passes `input_schema`
        straight through (mirroring `openai.py::_format_tools`) rather than
        rebuilding a Pydantic model from it, so the nested structure must
        survive verbatim, not merely be reconstructible from `$defs`."""
        adapter = _make_jira_search_adapter()

        tool_schema = adapter.to_schema()
        lc_dict = convert_tool_schema_to_langchain_dict(tool_schema)

        filters_field = lc_dict["function"]["parameters"]["properties"]["filters"]
        assert filters_field["type"] == "array"
        assert set(filters_field["items"]["properties"].keys()) == {"field", "value"}


def _mcp_server() -> ResolvedMCPServer:
    return ResolvedMCPServer(
        instance_id="inst-1", name="RovoMCP", display_name="Atlassian Rovo",
        instance={"authMode": "none"}, auth={}, owner_id="user-1", attached_tools=None,
    )


def _mcp_adapter(input_schema: dict[str, Any]) -> MCPToolAdapter:
    tool_info = MCPToolInfo(
        name="search_issues", namespaced_name="mcp_rovo__search_issues",
        description="Search issues", input_schema=input_schema,
    )
    return MCPToolAdapter(_mcp_server(), tool_info, session_manager=None)


class TestResolveJsonSchemaRefsFidelity:
    """`resolve_json_schema_refs` only inlines `$ref`/`$defs` — every other
    JSON-Schema keyword (`enum`, `default`, `minimum`/`maximum`,
    `additionalProperties`, ...) must pass through completely untouched.
    These are the exact losses proven in the MCP tool schema fidelity
    investigation, now regression-guarded at the point they'd first
    reappear if `_params_from_schema`'s lossy `ToolParameter` path were
    ever substituted back in for `raw_input_schema`."""

    def test_enum_survives(self) -> None:
        schema = {
            "type": "object",
            "properties": {"expand": {"type": "string", "enum": ["summary", "status"]}},
        }
        resolved = resolve_json_schema_refs(schema)
        assert resolved["properties"]["expand"]["enum"] == ["summary", "status"]

    def test_default_survives(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "max_results": {"type": "integer", "default": 50},
                "fields": {"type": "array", "default": ["summary", "status"], "items": {"type": "string"}},
            },
        }
        resolved = resolve_json_schema_refs(schema)
        assert resolved["properties"]["max_results"]["default"] == 50
        assert resolved["properties"]["fields"]["default"] == ["summary", "status"]

    def test_minimum_and_maximum_survive(self) -> None:
        schema = {
            "type": "object",
            "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 100}},
        }
        resolved = resolve_json_schema_refs(schema)
        assert resolved["properties"]["limit"]["minimum"] == 1
        assert resolved["properties"]["limit"]["maximum"] == 100

    def test_additional_properties_map_keeps_its_value_type(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "options": {"type": "object", "additionalProperties": {"type": "string"}},
            },
        }
        resolved = resolve_json_schema_refs(schema)
        assert resolved["properties"]["options"]["additionalProperties"] == {"type": "string"}

    def test_cyclic_defs_does_not_raise_and_keeps_sibling_fields_typed(self) -> None:
        """A Jira-style `IssueLink` schema whose `parent` field `$ref`s back
        to itself must resolve without a `RecursionError`, and — critically
        — must not degrade sibling fields on the SAME object to untyped
        fallbacks the way the old unguarded `except Exception` did."""
        schema = {
            "$defs": {
                "IssueLink": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "parent": {"$ref": "#/$defs/IssueLink"},
                    },
                },
            },
            "type": "object",
            "properties": {"link": {"$ref": "#/$defs/IssueLink"}},
        }
        resolved = resolve_json_schema_refs(schema)
        link = resolved["properties"]["link"]
        assert link["properties"]["id"]["type"] == "string"
        # The recursive branch is a bounded placeholder, not an infinite
        # expansion or a dropped field.
        assert link["properties"]["parent"]["type"] == "object"


class TestMCPAdapterRawSchemaFidelity:
    """End to end through `MCPToolAdapter.raw_input_schema` -> `to_schema()`
    -> `convert_tool_schema_to_langchain_dict` — the actual path a Rovo tool
    schema takes before reaching the LLM via the default LangChain
    transport."""

    def test_enum_default_and_bounds_reach_the_langchain_dict_unmodified(self) -> None:
        input_schema = {
            "type": "object",
            "properties": {
                "expand": {"type": "string", "enum": ["summary", "status"]},
                "max_results": {"type": "integer", "default": 50, "minimum": 1, "maximum": 100},
                "options": {"type": "object", "additionalProperties": {"type": "string"}},
            },
            "required": [],
        }
        adapter = _mcp_adapter(input_schema)
        tool_schema = adapter.to_schema()
        lc_dict = convert_tool_schema_to_langchain_dict(tool_schema)

        parameters = lc_dict["function"]["parameters"]
        assert parameters["properties"]["expand"]["enum"] == ["summary", "status"]
        assert parameters["properties"]["max_results"]["default"] == 50
        assert parameters["properties"]["max_results"]["minimum"] == 1
        assert parameters["properties"]["max_results"]["maximum"] == 100
        assert parameters["properties"]["options"]["additionalProperties"] == {"type": "string"}

    def test_cyclic_defs_tool_still_produces_a_bindable_schema(self) -> None:
        input_schema = {
            "$defs": {
                "IssueLink": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "parent": {"$ref": "#/$defs/IssueLink"},
                    },
                },
            },
            "type": "object",
            "properties": {"link": {"$ref": "#/$defs/IssueLink"}},
        }
        adapter = _mcp_adapter(input_schema)
        lc_dict = convert_tool_schema_to_langchain_dict(adapter.to_schema())

        link = lc_dict["function"]["parameters"]["properties"]["link"]
        assert link["properties"]["id"]["type"] == "string"
        assert "$ref" not in str(lc_dict)
