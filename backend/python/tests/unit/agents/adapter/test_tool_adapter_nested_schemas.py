"""Nested tool-parameter schema conversion (`app/agents/agent_loop/
tool_adapter.py::_params_from_schema`) — the fix for the "flat tool schema"
non-standard implementation flagged in `tool_adapter.py`'s old docstring:
deeply nested object/array-of-object `args_schema` fields must keep their
inner `properties`/`items` structure in the `ToolSchema` the LLM actually
sees, not collapse to a bare `{"type": "object"}`/`{"type": "array"}`.

Also proves the fix survives the OTHER direction of this same round-trip —
`converters.py::convert_tool_schema_to_langchain` (used by `LangChainTransport
._bind_tools`) already recursively rebuilds a Pydantic model from a JSON
schema fragment; this suite checks the two ends actually connect for a
realistic nested tool.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.agent_loop_lib.tools.base import ParameterType
from app.agents.agent_loop.converters import convert_tool_schema_to_langchain
from app.agents.agent_loop.tool_adapter import PipesHubToolAdapter, _params_from_schema


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


class TestToolAdapterToSchemaRoundTrip:
    """`PipesHubToolAdapter.to_schema()` (inherited default from `Tool`) must
    carry the nested structure all the way into `ToolSchema.input_schema`,
    and `LangChainTransport._bind_tools`'s conversion back to a Pydantic
    model (via `convert_tool_schema_to_langchain`) must be able to consume
    it without errors and without flattening it away."""

    def test_to_schema_input_schema_has_nested_properties(self) -> None:
        context = object()
        adapter = PipesHubToolAdapter.__new__(PipesHubToolAdapter)
        adapter._registry_tool = type("Fake", (), {
            "args_schema": _JiraSearchArgs, "description": "search jira",
            "llm_description": None, "when_to_use": [], "when_not_to_use": [],
        })()
        adapter._app_name = "jira"
        adapter._tool_name = "search"
        adapter._context_ref = lambda: context

        schema = adapter.to_schema()
        filters_schema = schema.input_schema["properties"]["filters"]
        assert filters_schema["type"] == "array"
        assert filters_schema["items"]["properties"]["field"]["type"] == "string"

    def test_langchain_round_trip_preserves_nested_array_of_objects(self) -> None:
        context = object()
        adapter = PipesHubToolAdapter.__new__(PipesHubToolAdapter)
        adapter._registry_tool = type("Fake", (), {
            "args_schema": _JiraSearchArgs, "description": "search jira",
            "llm_description": None, "when_to_use": [], "when_not_to_use": [],
        })()
        adapter._app_name = "jira"
        adapter._tool_name = "search"
        adapter._context_ref = lambda: context

        tool_schema = adapter.to_schema()
        lc_tool = convert_tool_schema_to_langchain(tool_schema)

        args_model = lc_tool.args_schema
        rebuilt_schema = args_model.model_json_schema()
        filters_field = rebuilt_schema["properties"]["filters"]
        # `filters` isn't in the tool schema's top-level `required` list
        # (only `project` is), so Pydantic wraps the rebuilt field as
        # `Optional[...]` (`anyOf: [<array type>, null]`) — same as any
        # other optional field, unwrap it the same way real callers would.
        array_variant = next(v for v in filters_field["anyOf"] if v.get("type") == "array")
        assert array_variant["type"] == "array"
        # The item type is itself a dynamically-built nested model — walk
        # through $defs the same way the LLM-facing schema had to, proving
        # the nested object survived the round trip intact.
        item_ref = array_variant["items"]["$ref"]
        item_def_name = item_ref.rsplit("/", 1)[-1]
        item_def = rebuilt_schema["$defs"][item_def_name]
        assert set(item_def["properties"].keys()) == {"field", "value"}
