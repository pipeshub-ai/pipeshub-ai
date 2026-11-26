"""C4: instances generated from each schema validate in the generated pydantic and zod models."""

from __future__ import annotations

from typing import Any

import pytest
from hypothesis import HealthCheck, example, given, settings
from hypothesis_jsonschema import from_schema

from harness_core.contracts import RESERVED_KEY, dump_contract, validate_contract
from harness_core.contracts.canonical_json import canonical_json

from .support import (
    CONTRACTS,
    ContractSchema,
    bundle,
    instance_schemas,
    node_available,
    run_node,
    validator,
)

MAX_EXAMPLES = 40


_NO_RESERVED_KEY = {"not": {"const": RESERVED_KEY}}
_OPAQUE_OBJECT = {
    "type": "object",
    "maxProperties": 3,
    "propertyNames": _NO_RESERVED_KEY,
    "additionalProperties": {"type": ["string", "boolean", "null"]},
}


def _merge_all_of(node: dict[str, Any]) -> dict[str, Any]:
    # Typed events narrow InlineEnvelope's type/writer/data; merge so `data` is only the typed one.
    if "allOf" not in node:
        return node
    merged: dict[str, Any] = {}
    for part in [*node["allOf"], {k: v for k, v in node.items() if k != "allOf"}]:
        merged = {
            **merged,
            **part,
            "properties": {**merged.get("properties", {}), **part.get("properties", {})},
            "required": sorted({*merged.get("required", []), *part.get("required", [])}),
        }
    return merged


def _for_generation(node: object) -> object:
    # Python's `$` also matches before a trailing newline; pydantic (Rust regex) and zod do not.
    # Generate with `\Z` so instances follow the stricter reading every validator shares.
    # Free-form payloads (data, receipt, args, extension bodies) are opaque to the contract;
    # bounding them keeps generation fast and stable.
    if isinstance(node, dict):
        node = _merge_all_of(node)
        if node.get("type") == "object" and not {"properties", "patternProperties", "additionalProperties"} & node.keys():
            return {**node, **_OPAQUE_OBJECT}
        out = {k: _for_generation(v) for k, v in node.items()}
        if out.get("type") == "object" and out.get("additionalProperties") is not False:
            # The reserved key is rejected outside the schemas (canonical_json, validate_contract).
            out.setdefault("propertyNames", _NO_RESERVED_KEY)
        pattern = out.get("pattern")
        if isinstance(pattern, str) and pattern.endswith("$"):
            out["pattern"] = pattern[:-1] + r"\Z"
        return out
    if isinstance(node, list):
        return [_for_generation(v) for v in node]
    return node


def _generate(schema: ContractSchema) -> list[Any]:
    model = schema.model()
    seen: list[Any] = []

    @settings(
        max_examples=MAX_EXAMPLES,
        derandomize=True,
        database=None,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much, HealthCheck.data_too_large],
    )
    @given(from_schema(_for_generation(bundle(schema))))
    def check(instance: object) -> None:
        parsed = validate_contract(model, instance)
        assert canonical_json(dump_contract(parsed)) == canonical_json(instance)
        seen.append(instance)

    check()
    return seen


# The largest schemas take most of the default 30 s to generate on an idle host (resolved-run-config: 17 s),
# and a timeout inside generation surfaces as hypothesis's FlakyStrategyDefinition, not as a timeout.
@pytest.mark.timeout(180)
@pytest.mark.parametrize("schema", instance_schemas(), ids=str)
def test_generated_instances_validate_in_pydantic_and_zod(schema: ContractSchema) -> None:
    # Both validators read the instances generated here, once. A separate zod test over every schema reused only
    # what its own xdist worker had generated and regenerated the rest (~230 s of CPU), up to its timeout.
    instances = _generate(schema)
    assert instances
    reason = node_available()
    if reason is not None:
        pytest.skip(f"pydantic accepted {len(instances)} instances; zod not checked: {reason}")
    items = [canonical_json(i) for i in instances]
    results = run_node("parse", [{"schema": schema.rel, "json": b.decode("utf-8")} for b in items])
    rejected = [(b, r["error"]) for b, r in zip(items, results, strict=True) if not r["ok"]]
    assert rejected[:3] == []
    assert [r["canonical"].encode("utf-8") for r in results] == items


def _schema(rel: str) -> ContractSchema:
    return next(s for s in instance_schemas() if s.path == CONTRACTS / rel)


def _wire_carries(schema: ContractSchema, instance: object) -> None:
    # Whatever the schema accepts must parse and round-trip through canonical JSON.
    if validator(schema).is_valid(instance):
        assert canonical_json(dump_contract(validate_contract(schema.model(), instance))) == canonical_json(instance)


_SESSION_ERASED = _schema("core/events/session.erased.schema.json")
_USAGE_RECORDED = _schema("core/events/usage.recorded.schema.json")
_GENERATED_SETTINGS = settings(max_examples=MAX_EXAMPLES, derandomize=True, database=None, deadline=None)


# CPD-2 §6 and CP-SKEL: a count above 2^53 that canonical JSON (and JavaScript) cannot carry exactly.
@example({**_SESSION_ERASED.examples[1], "data": {"reason": "retention", "counts": {"events": 2**53 + 1}}})
@_GENERATED_SETTINGS
@given(from_schema(_for_generation(bundle(_SESSION_ERASED))))
def test_c4_session_erased_admits_only_what_the_wire_carries(instance: object) -> None:
    _wire_carries(_SESSION_ERASED, instance)


# A whole-dollar amount parses as an int, so an unbounded Usd fails the same way.
@example({**_USAGE_RECORDED.examples[0], "data": {**_USAGE_RECORDED.examples[0]["data"], "costUsd": 2**53 + 1}})
@_GENERATED_SETTINGS
@given(from_schema(_for_generation(bundle(_USAGE_RECORDED))))
def test_c4_usage_recorded_admits_only_what_the_wire_carries(instance: object) -> None:
    _wire_carries(_USAGE_RECORDED, instance)
