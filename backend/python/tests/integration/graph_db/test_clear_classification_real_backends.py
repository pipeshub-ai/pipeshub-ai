"""``GraphDBTransformer.clear_classification`` against a real Neo4j and a real ArangoDB.

It removes what a classification that found nothing would: the record's taxonomy edges and its EXTRACTED organisation
links, whether or not the summary named organisations. Another record's edges, and links of another origin
(INFERRED, or written by a connector), stay.

Runs on Neo4j and ArangoDB (backend-matrix). Environment: NEO4J_IT_URI, NEO4J_IT_PASSWORD, ARANGO_IT_URL, ARANGO_IT_PASSWORD.
PostgreSQL: ``postgres/test_clear_classification.py``.
"""
from __future__ import annotations

import logging
from typing import Any

import pytest

from app.config.constants.arangodb import EntityOrigin, EntityRelations
from app.models.blocks import SemanticMetadata
from app.modules.transformers.graphdb import GraphDBTransformer
from tests.integration.graph_db.test_extracted_organizations_real_backends import (
    ORGS,
    _account,
    _edge,
    _edges_from,
    _records,
    backend,  # noqa: F401 - the fixture
)

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]
logger = logging.getLogger("clear-classification-it")


def _metadata() -> SemanticMetadata:
    return SemanticMetadata(
        categories=["Finance"], topics=["Quarterly planning"], languages=["English"], departments=[],
    )


async def _classify(provider: Any, record_id: str) -> None:  # noqa: ANN401
    await GraphDBTransformer(graph_provider=provider, logger=logger).write_taxonomy(record_id, _metadata())


async def _links(provider: Any, *record_ids: str) -> list[tuple[str, str]]:  # noqa: ANN401
    rows = await provider.get_record_taxonomy_links(list(record_ids), include_departments=True)
    return sorted((r["recordId"], r["collection"]) for r in rows)


async def _seed(provider: Any, org: str) -> tuple[str, str]:  # noqa: ANN401
    await provider.ensure_schema()
    await provider.batch_upsert_nodes([{"id": org, "accountType": "individual", "isActive": True}], ORGS)
    await provider.batch_upsert_nodes([_account(org, "acme", "Acme", parent=org), _account(org, "globex", "Globex", parent=org)], ORGS)
    await _records(provider, org, "deal", "twin")
    mine, twin = f"{org}-deal", f"{org}-twin"
    for key in (mine, twin):
        await _classify(provider, key)
    await provider.batch_create_entity_relations([
        _edge(org, "deal", f"{org}-acme", EntityRelations.FOR_ACCOUNT.value, EntityOrigin.INFERRED.value),
        _edge(org, "deal", f"{org}-globex", EntityRelations.MENTIONS.value, EntityOrigin.EXTRACTED.value),
        _edge(org, "twin", f"{org}-globex", EntityRelations.MENTIONS.value, EntityOrigin.EXTRACTED.value),
    ])
    return mine, twin


async def test_the_records_taxonomy_and_extracted_organisation_links_go_and_nothing_else(backend) -> None:  # noqa: F811
    provider, org = backend
    mine, twin = await _seed(provider, org)
    before = await _links(provider, mine, twin)
    assert {c for r, c in before if r == mine} == {c for r, c in before if r == twin} and len([1 for r, _ in before if r == mine]) >= 3

    await GraphDBTransformer(graph_provider=provider, logger=logger).clear_classification(mine)

    assert await _links(provider, mine) == []
    assert await _links(provider, twin) == [x for x in before if x[0] == twin]
    assert await _edges_from(provider, mine) == [(f"{org}-acme", "FOR_ACCOUNT", "INFERRED")]
    assert await _edges_from(provider, twin) == [(f"{org}-globex", "MENTIONS", "EXTRACTED")]


async def test_a_second_run_changes_nothing(backend) -> None:  # noqa: F811
    provider, org = backend
    mine, twin = await _seed(provider, org)
    transformer = GraphDBTransformer(graph_provider=provider, logger=logger)
    await transformer.clear_classification(mine)
    await transformer.clear_classification(mine)
    assert await _links(provider, mine) == []
    assert await _links(provider, twin) != []


async def test_organisation_links_go_even_when_a_summary_names_none(backend) -> None:  # noqa: F811
    """The classifier's own write returns before its delete when the summary names no organisations (unknown), which is
    why the clear deletes them directly."""
    provider, org = backend
    mine, _ = await _seed(provider, org)
    transformer = GraphDBTransformer(graph_provider=provider, logger=logger)
    record = type("R", (), {"id": mine, "semantic_metadata": type("S", (), {"organizations": None})()})()
    resolution = type("Res", (), {"org_id": org, "entries": {}})()
    assert await transformer.link_organizations(record, resolution) == []
    assert (f"{org}-globex", "MENTIONS", "EXTRACTED") in await _edges_from(provider, mine), "the classifier's write leaves them"

    await transformer.clear_classification(mine)

    assert await _edges_from(provider, mine) == [(f"{org}-acme", "FOR_ACCOUNT", "INFERRED")]
