"""TG-05 (C3.30): ``tools/call`` validates arguments against the tool's JSON Schema 2020-12 at the gateway.

Invalid arguments answer a structured validation error naming each field and its fix; nothing runs and nothing is
recorded. The executor's leniency (``null`` for an optional argument, an enum in other case) holds at the gateway.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from harness_core.effects import EffectRunner, RequestEffectLog, ToolExecution
from harness_core.gateway.arguments import MAX_FIELD_ERRORS, ArgumentCheck, FieldError
from harness_core.gateway.endpoint import RunToolEndpoint
from harness_core.gateway.memory import InMemoryCitationSink
from harness_core.invoker import CatalogTool, InMemoryToolCatalog, InvocationScope
from harness_core.kernel.core.tool_schema import ToolSchema
from harness_core.testing.contracts.tool_catalog import offered
from harness_core.testing.contracts.tool_endpoint import BINDING, claims_for
from harness_core.testing.ids import SeededIds

if TYPE_CHECKING:
    from harness_core.effects import ToolContext
    from harness_core.kernel.core.messages import ToolCall

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "minLength": 1, "description": "What to search for"},
        "limit": {"type": "integer", "minimum": 1, "maximum": 50},
        "order": {"type": "string", "enum": ["Newest First", "Oldest First"]},
        "filters": {
            "type": "object",
            "properties": {
                "since": {"type": "string", "format": "date"},
                "project": {"type": "string", "pattern": "^[A-Z]+$"},
            },
            "additionalProperties": False,
        },
        "labels": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
        "pair": {"type": "array", "prefixItems": [{"type": "string"}, {"type": "integer"}]},
        "owner": {"anyOf": [{"type": "string"}, {"type": "null"}]},
    },
    "required": ["query"],
}


def _check(arguments: dict[str, Any], schema: dict[str, Any] = SCHEMA) -> tuple[FieldError, ...]:
    return ArgumentCheck().check("search", schema, arguments)


def test_tg05_valid_arguments_pass() -> None:
    assert _check({"query": "q3", "limit": 5, "filters": {"since": "2026-01-31", "project": "OPS"}, "pair": ["a", 1]}) == ()


def test_tg05_a_missing_required_argument_names_the_field_and_how_to_fix_it() -> None:
    (error,) = _check({"limit": 5})
    assert error == FieldError("query", "is required", "add `query` (string: What to search for)")


def test_tg05_every_missing_required_argument_is_named() -> None:
    schema = {**SCHEMA, "required": ["query", "limit"]}
    assert [e.field for e in _check({}, schema)] == ["limit", "query"]


def test_tg05_a_wrong_type_names_the_type_wanted() -> None:
    (error,) = _check({"query": "q", "limit": "five"})
    assert (error.field, error.problem, error.fix) == ("limit", "must be integer, not string", "send integer")


@pytest.mark.parametrize(
    ("arguments", "field", "fix"),
    [
        ({"query": "q", "filters": {"project": "ops"}}, "filters.project", "send a value matching ^[A-Z]+$"),
        ({"query": "q", "filters": {"since": "31/01/2026"}}, "filters.since", "send a valid date"),
        ({"query": "q", "filters": {"owner": "me"}}, "filters.owner", "remove `owner`; allowed: project, since"),
        ({"query": "q", "labels": ["a", 3]}, "labels[1]", "send string"),
        ({"query": "q", "labels": ["a", "a"]}, "labels", "send each item once"),
        ({"query": "q", "pair": ["a", "b"]}, "pair[1]", "send integer"),
        ({"query": "q", "limit": 99}, "limit", "send a number at most 50"),
        ({"query": ""}, "query", "send a string at least 1 characters long"),
        ({"query": "q", "owner": 3}, "owner", "send string"),
    ],
)
def test_tg05_nested_format_and_pattern_errors_name_the_path(arguments: dict[str, Any], field: str, fix: str) -> None:
    errors = _check(arguments)
    assert [(e.field, e.fix) for e in errors] == [(field, fix)], errors


def test_tg05_an_enum_error_lists_the_members() -> None:
    (error,) = _check({"query": "q", "order": "by date"})
    assert error.field == "order" and error.fix == 'use one of: "Newest First", "Oldest First"'


def test_tg05_the_executors_leniency_holds_at_the_gateway() -> None:
    """``null`` for an optional argument is read as absent and an enum in other case as its member, as the
    executor reads them; the gateway must not refuse what the tool would run."""
    assert _check({"query": "q", "limit": None, "order": "newest first"}) == ()
    (error,) = _check({"query": None})
    assert error.field == "query"


def test_tg05_a_number_or_boolean_sent_as_a_string_is_read_as_the_tools_read_it() -> None:
    """The MCP adapter and the connectors' Pydantic models coerce ``"5"`` and ``"true"``; the gateway must not
    refuse a call they run. Anything else in a string is still refused."""
    schema = {
        "type": "object",
        "properties": {
            "limit": {"type": "integer"},
            "ratio": {"type": "number"},
            "draft": {"type": "boolean"},
            "rows": {"type": "array", "items": {"type": "object", "properties": {"n": {"type": "integer"}, "tag": {"type": "string"}}}},
        },
    }
    assert _check({"limit": "5", "ratio": "0.5", "draft": "TRUE", "rows": [{"n": "2", "tag": None}]}, schema) == ()
    assert [e.field for e in _check({"limit": "5.5", "ratio": "nan", "draft": "yes", "rows": [{"n": "x"}]}, schema)] == [
        "draft", "limit", "ratio", "rows[0].n",
    ]


def test_tg05_the_tool_receives_the_arguments_as_sent() -> None:
    arguments = {"query": "q", "limit": "5", "order": "newest first"}
    assert _check(arguments) == ()
    assert arguments == {"query": "q", "limit": "5", "order": "newest first"}


def test_tg05_errors_are_capped() -> None:
    schema = {"type": "object", "properties": {f"f{i}": {"type": "integer"} for i in range(20)}}
    assert len(_check({f"f{i}": "x" for i in range(20)}, schema)) == MAX_FIELD_ERRORS


def test_tg05_a_schema_naming_another_draft_is_read_as_that_draft() -> None:
    draft7 = {"$schema": "http://json-schema.org/draft-07/schema#", "type": "object", "dependencies": {"a": ["b"]}}
    assert [e.field for e in _check({"a": 1}, draft7)] == [""]
    # 2020-12 has no ``dependencies``: the same keyword without a $schema checks nothing.
    assert _check({"a": 1}, {k: v for k, v in draft7.items() if k != "$schema"}) == ()


def test_tg05_a_schema_the_validator_cannot_read_never_refuses_the_call() -> None:
    check = ArgumentCheck()
    broken = {"type": "object", "properties": {"q": {"type": "strng"}}}
    assert not check.readable("t", broken)
    assert check.check("t", broken, {"q": 1}) == ()


def validating_endpoint() -> tuple[RunToolEndpoint, list[str], RequestEffectLog]:
    runs: list[str] = []
    tool = offered("search")

    async def execute(call: ToolCall, ctx: ToolContext) -> ToolExecution:
        runs.append(call.name)
        return ToolExecution(status="ok", content=f"found {call.arguments['query']}")

    log = RequestEffectLog()
    catalog = InMemoryToolCatalog(
        [CatalogTool(ToolSchema(name="search", description="Search.", input_schema=SCHEMA), tool.effect, execute)],
        [tool.effect.tool_id],
    )
    endpoint = RunToolEndpoint(
        binding=BINDING,
        catalog=catalog,
        effects=EffectRunner(log),
        scope=InvocationScope(BINDING.run_id, BINDING.tenant, BINDING.attempt_id),
        ids=SeededIds(),
        citations=InMemoryCitationSink(),
    )
    return endpoint, runs, log


async def test_tg05_the_gateway_refuses_invalid_arguments_before_anything_runs() -> None:
    endpoint, runs, log = validating_endpoint()
    result = await endpoint.call_tool(claims_for(BINDING), "search", {"limit": "five"})
    assert (result.is_error, result.disposition, result.reason_code, result.retryable) == (
        True, "refused", "invalid_arguments", True,
    )
    assert [e.field for e in result.field_errors] == ["limit", "query"]
    assert result.invocation_id is None and runs == [] and log.outcomes() == ()
    structured = result.structured()
    assert structured["errors"] == [e.as_json() for e in result.field_errors]
    assert "`query` is required. Fix: add `query`" in result.text
    assert "nothing was run" in result.text


async def test_tg05_corrected_arguments_then_run() -> None:
    endpoint, runs, _ = validating_endpoint()
    _ = await endpoint.call_tool(claims_for(BINDING), "search", {"limit": 5})
    result = await endpoint.call_tool(claims_for(BINDING), "search", {"query": "q3", "limit": 5})
    assert (result.is_error, result.disposition, result.text) == (False, "executed", "found q3")
    assert runs == ["search"]
