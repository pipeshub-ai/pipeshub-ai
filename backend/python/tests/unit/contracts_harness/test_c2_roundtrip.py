"""C2: Python -> JSON -> TS -> JSON -> Python is byte-equal under canonical JSON."""

from __future__ import annotations

import json

import pytest

from harness_core.contracts import dump_contract, validate_contract
from harness_core.contracts.canonical_json import canonical_json

from .support import instance_schemas, node_available, run_node

pytestmark = pytest.mark.skipif(node_available() is not None, reason=str(node_available()))


def test_round_trip_through_typescript_is_byte_equal() -> None:
    cases = [(schema, example) for schema in instance_schemas() for example in schema.examples]
    first_hop = [canonical_json(dump_contract(validate_contract(s.model(), ex))) for s, ex in cases]
    results = run_node(
        "parse",
        [{"schema": s.rel, "json": text.decode("utf-8")} for (s, _), text in zip(cases, first_hop, strict=True)],
    )
    for (schema, example), py_bytes, ts in zip(cases, first_hop, results, strict=True):
        assert ts["ok"], f"{schema}: zod rejected a Python-emitted instance: {ts.get('error')}"
        ts_bytes = ts["canonical"].encode("utf-8")
        back = canonical_json(dump_contract(validate_contract(schema.model(), json.loads(ts_bytes))))
        assert py_bytes == ts_bytes == back, schema
        assert py_bytes == canonical_json(example), schema


def test_invalid_fixtures_are_rejected_by_zod_too() -> None:
    cases = [(s, c) for s in instance_schemas() for c in s.invalid]
    results = run_node("parse", [{"schema": s.rel, "json": json.dumps(c["instance"])} for s, c in cases])
    accepted = [f"{s}: {c['description']}" for (s, c), r in zip(cases, results, strict=True) if r["ok"]]
    assert accepted == []
