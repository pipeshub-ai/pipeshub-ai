"""The records schema carries a generation, and a change to the rule must raise it.

A process applies its own schema only while the live generation is lower. A rule changed without a new generation
would never reach a database that already holds the old one, so the rule's hash is pinned to the number.
"""

from __future__ import annotations

import copy
import hashlib
import json

import pytest

from app.schema.arango.documents import RECORD_SCHEMA_GENERATION, record_schema
from app.services.graph_db.arango.record_schema_guard import (
    generation_of_schema,
    is_record_schema_error,
)

# Change the rule -> this hash changes -> bump RECORD_SCHEMA_GENERATION, then update the pair below.
PINNED = {2: "3d9b95b3b681695d983f6b59c9e624a3dee0712358d00025877324f352630ad9"}


def _hash(schema: dict) -> str:
    rule = {k: v for k, v in schema["rule"].items() if k != "$comment"}
    return hashlib.sha256(json.dumps(rule, sort_keys=True).encode()).hexdigest()


def test_the_rule_hash_is_pinned_to_its_generation() -> None:
    assert RECORD_SCHEMA_GENERATION in PINNED, "a new generation needs its rule hash pinned here"
    assert _hash(record_schema) == PINNED[RECORD_SCHEMA_GENERATION], (
        "record_schema changed: raise RECORD_SCHEMA_GENERATION and pin the new hash in this file"
    )


def test_a_change_without_a_bump_fails_the_pin() -> None:
    """The negative control: a property added to the rule changes the hash."""
    changed = copy.deepcopy(record_schema)
    changed["rule"]["properties"]["aNewField"] = {"type": ["string", "null"]}
    assert _hash(changed) != PINNED[RECORD_SCHEMA_GENERATION]


def test_the_marker_is_in_the_rule_and_parses_to_the_generation() -> None:
    assert generation_of_schema(record_schema) == RECORD_SCHEMA_GENERATION


@pytest.mark.parametrize("schema", [None, {}, {"rule": {}}, {"rule": {"$comment": "other"}}, {"rule": {"$comment": "gen=x"}}, {"rule": "x"}])
def test_a_missing_or_unparsable_generation_is_zero(schema: object) -> None:
    assert generation_of_schema(schema) == 0  # type: ignore[arg-type]


def test_a_schema_with_a_higher_generation_reads_higher() -> None:
    assert generation_of_schema({"rule": {"$comment": f"pipeshub-record-schema gen={RECORD_SCHEMA_GENERATION + 1}"}}) > RECORD_SCHEMA_GENERATION


def test_only_the_records_schema_message_is_a_narrowing() -> None:
    assert is_record_schema_error('{"errorNum":1620,"errorMessage":"Document does not match the record schema."}')
    assert not is_record_schema_error('{"errorNum":1620,"errorMessage":"Document does not match the file record schema."}')
    assert not is_record_schema_error('{"errorNum":1200,"errorMessage":"Document does not match the record schema."}')
