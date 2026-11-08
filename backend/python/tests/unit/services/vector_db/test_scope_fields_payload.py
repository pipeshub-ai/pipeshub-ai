"""Scope fields in the payload mapping of each store that maps fields by name (OpenSearch documents, Redis hashes, pgvector
, and the index gate: the fields are mapped and indexed only while they are written."""

from __future__ import annotations

import pytest

from app.services.permissions.scope_fields import (
    SCOPE_KEYS,
    ScopeFields,
    Stamp,
)
from app.services.vector_db.const.const import (
    PAYLOAD_KEYWORD_INDEXES,
    SCOPE_KEYWORD_INDEXES,
)
from app.services.vector_db.models import VectorPoint
from app.services.vector_db.opensearch.utils import OpenSearchUtils
from app.services.vector_db.redis.utils import (
    hash_doc_to_payload,
    vector_point_to_hash_fields,
)

FIELDS = ScopeFields(Stamp.OVERRIDE, ("a", "b"), ("b",))


def point(extra: dict | None = None) -> VectorPoint:
    return VectorPoint(id="p", dense_vector=[0.1, 0.2], payload={
        "page_content": "t", "metadata": {"orgId": "o"}, "connectorIds": ["c"], "recordGroupIds": ["g"], "rootRecordGroupIds": [],
        **(extra or {})})


def test_an_opensearch_document_carries_the_fields_only_when_the_point_does() -> None:
    assert not set(SCOPE_KEYS) & set(OpenSearchUtils.vector_point_to_document(point()))
    doc = OpenSearchUtils.vector_point_to_document(point(FIELDS.to_payload()))
    assert ScopeFields.from_payload(doc) == FIELDS and doc["connectorIds"] == ["c"]


def test_a_search_hit_payload_is_unchanged_by_the_fields() -> None:
    hit = {"_id": "p", "_score": 1.0, "_source": {"metadata": {}, "page_content": "t", "connectorIds": ["c"], **FIELDS.to_payload()}}
    assert not set(SCOPE_KEYS) & set(OpenSearchUtils.hit_to_search_result(hit).payload)  # search results are as before


def test_a_redis_hash_round_trips_the_fields_and_omits_them_when_absent() -> None:
    assert not set(SCOPE_KEYS) & set(vector_point_to_hash_fields(point(), "FLOAT32"))
    flat = vector_point_to_hash_fields(point(FIELDS.to_payload()), "FLOAT32")
    assert flat["pathIds"] == "a,b" and flat["aclScope"] == "b" and flat["scopeStamp"] == "override"
    back = hash_doc_to_payload({k: str(v) for k, v in flat.items() if k != "dense_embedding"})
    assert ScopeFields.from_payload(back) == FIELDS
    assert ScopeFields.from_payload(hash_doc_to_payload({"connectorIds": "c"})) is None


def test_the_scope_indexes_are_separate_so_they_can_be_gated() -> None:
    assert {f for f, _ in SCOPE_KEYWORD_INDEXES} == {"pathIds", "aclScope", "scopeStamp"}
    assert not {f for f, _ in SCOPE_KEYWORD_INDEXES} & {f for f, _ in PAYLOAD_KEYWORD_INDEXES}


@pytest.mark.parametrize("on", [False, True])
async def test_the_registry_creates_the_scope_indexes_only_while_scope_fields_are_written(monkeypatch, on: bool) -> None:
    from unittest.mock import AsyncMock, MagicMock

    from app.services.vector_db.collection_registry import CollectionRegistry

    if on:
        monkeypatch.setenv("ACL_SCOPE_FIELDS", "on")
    else:
        monkeypatch.delenv("ACL_SCOPE_FIELDS", raising=False)
    svc = MagicMock()
    svc.create_index = AsyncMock()
    reg = CollectionRegistry.__new__(CollectionRegistry)
    reg._vector_db_service, reg._logger = svc, MagicMock()
    await CollectionRegistry._ensure_payload_indexes(reg, "c")
    made = {c.kwargs["field_name"] for c in svc.create_index.await_args_list}
    assert ("pathIds" in made) is on and "connectorIds" in made and "grantTokens" not in made


def _opensearch(mapping: dict):  # noqa: ANN202
    from unittest.mock import AsyncMock, MagicMock

    from app.services.vector_db.opensearch.opensearch import OpenSearchService

    svc = OpenSearchService.__new__(OpenSearchService)
    svc._assert_connected = AsyncMock()
    svc.client = MagicMock()
    svc.client.indices.get_mapping = AsyncMock(return_value={"idx": {"mappings": {"properties": mapping}}})
    svc.client.indices.put_mapping = AsyncMock()
    return svc


async def test_an_index_without_the_scope_fields_gets_keyword_mappings_before_the_first_stamp() -> None:
    svc = _opensearch({"connectorIds": {"type": "keyword"}})
    assert await svc._guard_scope_mapping("idx", set(SCOPE_KEYS))
    mapped = svc.client.indices.put_mapping.await_args.kwargs["body"]["properties"]
    assert set(mapped) == {f for f, _ in SCOPE_KEYWORD_INDEXES} and all(v == {"type": "keyword"} for v in mapped.values())
    await svc._guard_scope_mapping("idx", set(SCOPE_KEYS))
    assert svc.client.indices.put_mapping.await_count == 1  # once per index


async def test_an_index_that_maps_a_scope_field_as_text_gets_the_point_without_the_fields() -> None:
    svc = _opensearch({"pathIds": {"type": "text"}})
    assert not await svc._guard_scope_mapping("idx", set(SCOPE_KEYS))
    svc.client.indices.put_mapping.assert_not_awaited()
    assert svc._without_scope({**FIELDS.to_payload(), "connectorIds": ["c"]}) == {"connectorIds": ["c"]}
    assert not await svc._guard_scope_mapping("idx", {"connectorIds"})  # blocked for good, whatever the next write carries


async def test_a_missing_index_does_not_raise_into_the_write_and_is_not_remembered() -> None:
    from opensearchpy.exceptions import NotFoundError

    svc = _opensearch({})
    svc.client.indices.get_mapping.side_effect = NotFoundError(404, "index_not_found_exception", {})
    assert await svc._guard_scope_mapping("idx", set(SCOPE_KEYS))
    assert "idx" not in svc.__dict__.get("_scope_mapped", set())


async def test_every_index_behind_an_alias_is_checked_and_mapped() -> None:
    from unittest.mock import AsyncMock

    svc = _opensearch({})
    svc.client.indices.get_mapping = AsyncMock(return_value={
        "idx-1": {"mappings": {"properties": {}}}, "idx-2": {"mappings": {"properties": {"pathIds": {"type": "text"}}}}})
    assert not await svc._guard_scope_mapping("alias", set(SCOPE_KEYS))  # one index maps a field as text: the whole name is blocked
    svc2 = _opensearch({})
    svc2.client.indices.get_mapping = AsyncMock(return_value={"a": {"mappings": {"properties": {}}}, "b": {"mappings": {"properties": {}}}})
    assert await svc2._guard_scope_mapping("alias", set(SCOPE_KEYS))
    assert {c.kwargs["index"] for c in svc2.client.indices.put_mapping.await_args_list} == {"a", "b"}


async def test_what_was_learned_about_an_index_is_forgotten_when_it_is_created_or_deleted() -> None:
    svc = _opensearch({"pathIds": {"type": "text"}})
    assert not await svc._guard_scope_mapping("idx", set(SCOPE_KEYS))
    svc._forget_scope_mapping("idx")
    svc.client.indices.get_mapping.return_value = {"idx": {"mappings": {"properties": {}}}}
    assert await svc._guard_scope_mapping("idx", set(SCOPE_KEYS))
