"""Payload indexes a package registers at start-up are added to the index list; with none registered the list is the one we ship."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.models.record_metadata import reserved_property_names
from app.services.vector_db.const import const
from app.services.vector_db.const.const import (
    PAYLOAD_KEYWORD_INDEXES,
    guarded_payload_fields,
    keyword_required_fields,
    payload_keyword_indexes,
    register_payload_keyword_indexes,
)
from app.services.vector_db.strategy import RecordContext
from tests.unit.services.vector_db.test_collection_registry import (
    _make_registry,
    _make_vdb,
)

FIELD = "metadata.registeredField"
OTHER = "registeredOther"


def test_with_nothing_registered_the_index_list_is_the_shipped_one() -> None:
    assert payload_keyword_indexes() == PAYLOAD_KEYWORD_INDEXES
    assert [name for name, _ in PAYLOAD_KEYWORD_INDEXES] == [
        "metadata.virtualRecordId", "metadata.orgId", "metadata.blockId", "metadata.contentRev", "metadata.embedKey",
        "connectorIds", "recordGroupIds", "rootRecordGroupIds",
    ]
    assert guarded_payload_fields() == frozenset()
    assert keyword_required_fields() == frozenset()


def test_a_registered_field_is_appended_once_and_never_duplicates_a_shipped_one() -> None:
    register_payload_keyword_indexes([(FIELD, {"type": "keyword"})])
    register_payload_keyword_indexes([(FIELD, {"type": "keyword"}), ("connectorIds", {"type": "keyword"})])

    names = [name for name, _ in payload_keyword_indexes()]

    assert names[: len(PAYLOAD_KEYWORD_INDEXES)] == [n for n, _ in PAYLOAD_KEYWORD_INDEXES]
    assert names.count(FIELD) == 1
    assert names.count("connectorIds") == 1


def test_guarded_and_keyword_required_are_recorded_per_field() -> None:
    register_payload_keyword_indexes([(FIELD, {"type": "keyword"})], guarded=True)
    register_payload_keyword_indexes([(OTHER, {"type": "keyword"})], keyword_required=True)

    assert guarded_payload_fields() == {FIELD}
    assert keyword_required_fields() == {OTHER}


def test_the_registry_is_empty_again_in_the_next_test() -> None:
    assert payload_keyword_indexes() == PAYLOAD_KEYWORD_INDEXES
    assert guarded_payload_fields() == frozenset()


def test_the_reset_helper_forgets_everything_it_was_given() -> None:
    register_payload_keyword_indexes([(FIELD, {"type": "keyword"})], guarded=True, keyword_required=True)

    const._reset_payload_registry()

    assert payload_keyword_indexes() == PAYLOAD_KEYWORD_INDEXES
    assert guarded_payload_fields() == frozenset() == keyword_required_fields()


def test_a_field_registered_after_import_is_a_reserved_property_name_and_not_before() -> None:
    assert "registeredField" not in reserved_property_names()

    register_payload_keyword_indexes([(FIELD, {"type": "keyword"})])

    assert "registeredField" in reserved_property_names()


def test_the_reserved_names_with_nothing_registered_are_what_the_models_alone_give() -> None:
    from app.models.record_metadata import PIPELINE_FIELD_NAMES

    assert PIPELINE_FIELD_NAMES <= reserved_property_names()
    assert {"virtualRecordId", "orgId", "contentRev", "embedKey"} <= reserved_property_names()


def test_a_property_named_like_a_registered_field_is_refused_once_it_is_registered() -> None:
    from app.models.record_metadata import PropertyDefinition, PropertyType

    register_payload_keyword_indexes([(FIELD, {"type": "keyword"})])

    with pytest.raises(ValueError, match="reserved"):
        PropertyDefinition(name="registeredField", type=PropertyType.STRING)


async def test_the_collection_registry_creates_the_registered_indexes() -> None:
    register_payload_keyword_indexes([(FIELD, {"type": "keyword"})])
    vdb = _make_vdb(exists=True, dimension=8)
    registry = _make_registry(vector_db_service=vdb)

    await registry.ensure_collection(RecordContext(org_id="org-1"), embedding_size=8)

    created = {call.kwargs["field_name"] for call in vdb.create_index.await_args_list}
    assert FIELD in created
    assert {name for name, _ in PAYLOAD_KEYWORD_INDEXES} <= created


async def test_without_registrations_the_collection_registry_creates_exactly_the_shipped_indexes() -> None:
    vdb = _make_vdb(exists=True, dimension=8)
    registry = _make_registry(vector_db_service=vdb)

    await registry.ensure_collection(RecordContext(org_id="org-1"), embedding_size=8)

    assert {call.kwargs["field_name"] for call in vdb.create_index.await_args_list} == {n for n, _ in PAYLOAD_KEYWORD_INDEXES}


async def test_a_guarded_field_that_could_not_be_indexed_keeps_the_collection_not_ready_until_it_can() -> None:
    register_payload_keyword_indexes([(FIELD, {"type": "keyword"})], guarded=True)
    failing = [True]

    async def create_index(collection_name: str, field_name: str, field_schema: dict) -> None:
        if field_name == FIELD and failing[0]:
            raise RuntimeError("Unknown field")

    vdb = _make_vdb(exists=True, dimension=8)
    vdb.create_index = AsyncMock(side_effect=create_index)
    registry = _make_registry(vector_db_service=vdb)
    ctx = RecordContext(org_id="org-1")

    await registry.ensure_collection(ctx, embedding_size=8)
    assert registry.fields_ready("records") is False

    failing[0] = False
    vdb.create_index.reset_mock()
    await registry.ensure_collection(ctx, embedding_size=8)  # only the guarded fields are retried

    assert registry.fields_ready("records") is True
    assert {c.kwargs["field_name"] for c in vdb.create_index.await_args_list} >= {FIELD}


async def test_an_unguarded_registered_field_that_fails_does_not_hold_the_collection() -> None:
    register_payload_keyword_indexes([(FIELD, {"type": "keyword"})])

    async def create_index(collection_name: str, field_name: str, field_schema: dict) -> None:
        if field_name == FIELD:
            raise RuntimeError("Unknown field")

    vdb = _make_vdb(exists=True, dimension=8)
    vdb.create_index = AsyncMock(side_effect=create_index)
    registry = _make_registry(vector_db_service=vdb)

    await registry.ensure_collection(RecordContext(org_id="org-1"), embedding_size=8)

    assert registry.fields_ready("records") is True


# ---- the store that maps dynamically -----------------------------------------------------------------------------------


def _opensearch(mapping: dict):  # noqa: ANN202
    from unittest.mock import MagicMock

    from app.services.vector_db.opensearch.opensearch import OpenSearchService

    svc = OpenSearchService.__new__(OpenSearchService)
    svc._assert_connected = AsyncMock()
    svc.client = MagicMock()
    svc.client.indices.get_mapping = AsyncMock(return_value={"idx": {"mappings": {"properties": mapping}}})
    svc.client.indices.put_mapping = AsyncMock()
    return svc


async def test_a_keyword_required_field_is_mapped_as_keyword_before_the_first_write_that_carries_it() -> None:
    register_payload_keyword_indexes([(OTHER, {"type": "keyword"})], keyword_required=True)
    svc = _opensearch({})

    assert await svc._guard_scope_mapping("idx", {OTHER})

    mapped = svc.client.indices.put_mapping.await_args.kwargs["body"]["properties"]
    assert mapped[OTHER] == {"type": "keyword"}


async def test_a_keyword_required_field_mapped_as_text_keeps_it_off_the_point() -> None:
    register_payload_keyword_indexes([(OTHER, {"type": "keyword"})], keyword_required=True)
    svc = _opensearch({OTHER: {"type": "text"}})

    assert not await svc._guard_scope_mapping("idx", {OTHER})

    assert svc._without_scope({OTHER: ["x"], "connectorIds": ["c"]}) == {"connectorIds": ["c"]}


async def test_without_registrations_a_write_that_carries_no_scope_field_asks_the_store_nothing() -> None:
    svc = _opensearch({})

    assert await svc._guard_scope_mapping("idx", {"connectorIds"})

    svc.client.indices.get_mapping.assert_not_awaited()
