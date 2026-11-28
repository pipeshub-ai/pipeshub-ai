"""The records and apps schemas carry a generation, and a change to a rule must raise it.

A process applies its own schema only while the live generation is lower. A rule changed without a new generation
would never reach a database that already holds the old one, so the rule's hash is pinned to the number.
"""

from __future__ import annotations

import copy
import hashlib
import json

import pytest

from app.schema.arango.documents import (
    APP_SCHEMA_GENERATION,
    RECORD_GROUP_SCHEMA_GENERATION,
    RECORD_SCHEMA_GENERATION,
    app_schema,
    record_group_schema,
    record_schema,
)
from app.services.graph_db.arango.record_schema_guard import (
    generation_of_schema,
    is_record_schema_error,
)

# Change the rule -> this hash changes -> bump RECORD_SCHEMA_GENERATION, then update the pair below.
PINNED = {
    2: "53cf2c2c1dd02fc6616e59bc8d67814a25887b4debf7f26399178514fa03b829",
    3: "f3f0ab23062f1b5875c4788e74cfd6da94c6a617e8195eb4a69918890ff962b6",  # + pendingVirtualRecordId
    4: "6bb2f59179f808ffc4c9e0a65699433dcda3e743e648964959aff7798ddf290b",  # + refreshIntentId, refreshIntentAtMs, refreshDoneId
    5: "2bd8de874f749e4b50db9eb74e190a8454421135c5de15bf53119f546b5cf632",  # + incarnationId
    6: "de869b01e604dcf9eba8ac2c4d1854afe61e37deaf19615098f2ebe0fea54538",  # + indexedParser, indexedParserAt, indexedParserVrid, indexedParserSource
    7: "1e9862aa1f14c599127cd4014e6cbeef6c75f1136661ceed458d5f832f7e8add",  # + indexedMd5, pendingTaxonomyRefs
    8: "38cb8cba9a68b08cd185a0e5384b2f785d564d87d4975ceb30b208614f957b7b",  # + CLOUDFLARE R2 connectorName
    9: "eea21e16acad8d9b84cbe912b9c2d9fb1e7919bfc4739b33685ce402f1ddef82",  # + editionFields, chat-attachment uploader fields
}
# Change record_group_schema's rule -> bump RECORD_GROUP_SCHEMA_GENERATION, then pin the new hash here.
RECORD_GROUPS_PINNED = {
    1: "dc77da539f5b1945ade1c17b978919b1eea54a4ad46b0a210670978ef85114d6",  # + repoKey
}
# Change app_schema's rule -> bump APP_SCHEMA_GENERATION, then pin the new hash here.
APPS_PINNED = {
    1: "98a503a14a4e9c5c6c9d7268889bc88f8e0422abf815da390f0252ad88c51fe8",
}


def _hash(schema: dict) -> str:
    rule = {k: v for k, v in schema["rule"].items() if k != "$comment"}
    return hashlib.sha256(json.dumps(rule, sort_keys=True).encode()).hexdigest()


def test_the_rule_hash_is_pinned_to_its_generation() -> None:
    assert RECORD_SCHEMA_GENERATION in PINNED, "a new generation needs its rule hash pinned here"
    assert _hash(record_schema) == PINNED[RECORD_SCHEMA_GENERATION], (
        "record_schema changed: raise RECORD_SCHEMA_GENERATION and pin the new hash in this file"
    )


def test_the_apps_rule_hash_is_pinned_to_its_generation() -> None:
    assert APP_SCHEMA_GENERATION in APPS_PINNED, "a new generation needs its rule hash pinned here"
    assert _hash(app_schema) == APPS_PINNED[APP_SCHEMA_GENERATION], (
        "app_schema changed: raise APP_SCHEMA_GENERATION and pin the new hash in this file"
    )
    assert generation_of_schema(app_schema) == APP_SCHEMA_GENERATION


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


def test_the_record_groups_rule_hash_is_pinned_to_its_generation() -> None:
    assert RECORD_GROUP_SCHEMA_GENERATION in RECORD_GROUPS_PINNED, "a new generation needs its rule hash pinned here"
    assert _hash(record_group_schema) == RECORD_GROUPS_PINNED[RECORD_GROUP_SCHEMA_GENERATION], (
        "record_group_schema changed: raise RECORD_GROUP_SCHEMA_GENERATION and pin the new hash in this file"
    )
    assert generation_of_schema(record_group_schema) == RECORD_GROUP_SCHEMA_GENERATION
