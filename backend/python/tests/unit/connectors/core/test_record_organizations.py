"""Record -> CRM account edges (KG-13 slice 3a): a deal, case or task links
to its account's organisation, INFERRED, replacing only account edges."""
from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

from app.config.constants.arangodb import Connectors, OriginTypes
from app.connectors.core.base.data_processor.record_organizations import (
    link_record_organization,
)
from app.models.entities import DealRecord, FileRecord, RecordType, TicketRecord

BASE = {
    "id": "rec-1", "org_id": "org-1", "external_record_id": "ext-1", "record_name": "r",
    "origin": OriginTypes.CONNECTOR.value, "connector_name": Connectors.SALESFORCE, "connector_id": "conn-1",
    "version": 1, "record_group_id": "rg-acme",
}


def _store(organization: str | None = "acme") -> MagicMock:
    store = MagicMock()
    store.get_record_group_organization = AsyncMock(return_value=organization)
    store.delete_edges_between_collections = AsyncMock()
    store.batch_create_entity_relations = AsyncMock()
    return store


def _deal(**kw: object) -> DealRecord:
    return DealRecord(record_type=RecordType.DEAL, **{**BASE, **kw})


async def test_a_deal_links_to_its_account_as_inferred() -> None:
    store = _store()
    assert await link_record_organization(_deal(), store, logging.getLogger("t")) == 1
    store.get_record_group_organization.assert_awaited_once_with("rg-acme", "org-1")
    store.delete_edges_between_collections.assert_awaited_once_with(
        "rec-1", "records", "entityRelations", "organizations",
    )
    (edge,) = store.batch_create_entity_relations.await_args.args[0]
    assert (edge["_from"], edge["_to"], edge["edgeType"]) == ("records/rec-1", "organizations/acme", "FOR_ACCOUNT")
    assert (edge["origin"], edge["source"]) == ("INFERRED", "conn-1")


async def test_a_case_whose_group_has_no_account_loses_its_old_edge() -> None:
    store = _store(organization=None)
    case = TicketRecord(record_type=RecordType.CASE, **BASE)
    assert await link_record_organization(case, store, logging.getLogger("t")) == 0
    store.delete_edges_between_collections.assert_awaited_once()
    store.batch_create_entity_relations.assert_not_awaited()


async def test_other_record_types_are_left_alone() -> None:
    store = _store()
    file_record = FileRecord(record_type=RecordType.FILE, is_file=True, extension="txt", **BASE)
    assert await link_record_organization(file_record, store, logging.getLogger("t")) == 0
    assert await link_record_organization(_deal(record_group_id=None), store, logging.getLogger("t")) == 0
    store.get_record_group_organization.assert_not_awaited()
    store.delete_edges_between_collections.assert_not_awaited()


async def test_a_failed_lookup_keeps_the_existing_edge() -> None:
    store = _store()
    store.get_record_group_organization.side_effect = RuntimeError("db")
    assert await link_record_organization(_deal(), store, logging.getLogger("t")) == 0
    store.delete_edges_between_collections.assert_not_awaited()


async def test_a_rejected_write_does_not_fail_the_record() -> None:
    """FOR_ACCOUNT is a new edge type an older pod's schema can reject."""
    store = _store()
    store.batch_create_entity_relations.side_effect = RuntimeError("Document does not match the entity relations schema")
    assert await link_record_organization(_deal(), store, logging.getLogger("t")) == 0
