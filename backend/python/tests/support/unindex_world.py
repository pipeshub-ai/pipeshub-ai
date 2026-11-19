"""One scenario for ``unindex_record`` against a real graph store, shared by the Neo4j, ArangoDB and PostgreSQL tests.

A refused record and a twin share one virtual record. Both name an organisation the classifier extracted; the refused
record also names one a rule inferred. After the routine, the refused record keeps only the inferred link and the twin
keeps everything.
"""
from __future__ import annotations

import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from app.config.constants.arangodb import CollectionNames, EntityOrigin, EntityRelations
from app.models.entities import DealRecord, RecordGroupType, RecordType
from app.modules.indexing.unindex import unindex_record
from tests.integration.graph_db.test_record_organizations_real_backends import (
    _account,
    _base,
)

ORGS = CollectionNames.ORGS.value
RECORDS = CollectionNames.RECORDS.value
logger = logging.getLogger("unindex-it")


def _edge(org: str, record: str, target: str, kind: str, origin: str) -> dict[str, Any]:
    return {
        "_from": f"{RECORDS}/{org}-{record}", "_to": f"{ORGS}/{org}-{target}", "edgeType": kind,
        "origin": origin, "createdAtTimestamp": 1,
    }


async def seed(provider: Any, org: str) -> str:  # noqa: ANN401
    vrid = f"{org}-v"
    await provider.batch_upsert_nodes(
        [_account(org, "acme", "Acme", parent=org), _account(org, "globex", "Globex", parent=org)], ORGS
    )
    await provider.batch_upsert_records([
        DealRecord(
            record_name=key, record_type=RecordType.DEAL, record_group_type=RecordGroupType.DEAL,
            external_record_group_id="001A", **_base(org, key, "rg"),
        )
        for key in ("refused", "twin")
    ])
    for key in ("refused", "twin"):
        await provider.update_node(f"{org}-{key}", RECORDS, {"virtualRecordId": vrid})
    extracted, inferred = EntityOrigin.EXTRACTED.value, EntityOrigin.INFERRED.value
    await provider.batch_create_entity_relations([
        _edge(org, "refused", "acme", EntityRelations.MENTIONS.value, extracted),
        _edge(org, "refused", "globex", EntityRelations.FOR_ACCOUNT.value, inferred),
        _edge(org, "twin", "acme", EntityRelations.MENTIONS.value, extracted),
    ])
    return vrid


def _processor(provider: Any) -> MagicMock:  # noqa: ANN401
    processor = MagicMock()
    processor.graph_provider = provider
    processor._release_skipped_content = AsyncMock()
    processor.sink_orchestrator.entity_vector_store = None
    return processor


async def _orgs(provider: Any, org: str, key: str) -> list[str]:  # noqa: ANN401
    return [row["id"] for row in await provider.get_record_organizations(f"{org}-{key}", org)]


async def run_scenario(provider: Any, org: str) -> None:  # noqa: ANN401
    vrid = await seed(provider, org)
    refused_doc = {"_key": f"{org}-refused", "orgId": org, "virtualRecordId": vrid}
    assert await _orgs(provider, org, "refused") == [f"{org}-acme", f"{org}-globex"]

    # A failed taxonomy read stops the routine before anything is cleared.
    real_read = provider.get_record_taxonomy_links
    provider.get_record_taxonomy_links = AsyncMock(side_effect=RuntimeError("read failed"))
    try:
        processor = _processor(provider)
        try:
            await unindex_record(refused_doc, org, processor=processor, logger=logger)
        except RuntimeError:
            pass
        else:
            raise AssertionError("a failed taxonomy read must fail the routine")
        assert await _orgs(provider, org, "refused") == [f"{org}-acme", f"{org}-globex"]
        processor._release_skipped_content.assert_not_awaited()
    finally:
        provider.get_record_taxonomy_links = real_read

    stage_deletes = AsyncMock(wraps=provider.stage_states_delete_for_virtual_records)
    provider.stage_states_delete_for_virtual_records = stage_deletes
    processor = _processor(provider)
    await unindex_record(refused_doc, org, processor=processor, logger=logger)

    assert await _orgs(provider, org, "refused") == [f"{org}-globex"], "only the extracted link goes"
    assert await _orgs(provider, org, "twin") == [f"{org}-acme"], "a twin keeps its own"
    processor._release_skipped_content.assert_awaited_once_with(org, f"{org}-refused", vrid)
    stage_deletes.assert_not_awaited()  # the twin still holds the virtual record

    await unindex_record(refused_doc, org, processor=_processor(provider), logger=logger)
    assert await _orgs(provider, org, "refused") == [f"{org}-globex"], "a re-run changes nothing"

    # With no other holder left, the stage states of the virtual record go too.
    await provider.update_node(f"{org}-twin", RECORDS, {"virtualRecordId": None})
    await unindex_record(refused_doc, org, processor=_processor(provider), logger=logger)
    stage_deletes.assert_awaited_once_with([vrid])
