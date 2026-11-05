"""The edges written for accounts and opportunities carry numeric timestamps even when Salesforce sent no date.

The edge schemas type createdAtTimestamp and updatedAtTimestamp as non-null numbers, and one
rejected edge fails the whole page transaction.
"""

import logging
from collections.abc import AsyncIterator
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from app.config.constants.arangodb import CollectionNames
from app.connectors.sources.salesforce.connector import (
    SalesforceAccount,
    SalesforceConnector,
    SalesforceContact,
    SalesforceLead,
    SalesforceOpportunity,
)
from app.schema.node_validator import NodeSchemaValidator
from tests.support.data_store import runs_in_its_transaction

EDGE_COLLECTIONS = {
    CollectionNames.PROSPECT.value,
    CollectionNames.CUSTOMER.value,
    CollectionNames.DEAL_OF.value,
    CollectionNames.DEAL_INFO.value,
    CollectionNames.CONTACT.value,
    CollectionNames.MEMBER_OF.value,
    CollectionNames.LEAD.value,
}


def _connector() -> tuple[SalesforceConnector, MagicMock]:
    tx = MagicMock()
    for name in ("delete_edges_to", "delete_edges_from", "delete_edge", "batch_create_edges", "batch_upsert_orgs", "batch_upsert_people"):
        setattr(tx, name, AsyncMock())
    tx.get_nodes_by_field_in = AsyncMock(return_value=[])
    tx.get_all_orgs = AsyncMock(return_value=[])
    tx.get_record_by_external_id = AsyncMock(return_value=None)
    tx.__aenter__ = AsyncMock(return_value=tx)
    tx.__aexit__ = AsyncMock(return_value=None)

    processor = MagicMock()
    processor.org_id = "org-sf-1"
    for name in ("on_new_records", "on_new_record_groups", "on_record_content_update"):
        setattr(processor, name, AsyncMock())
    store = runs_in_its_transaction(MagicMock())
    store.transaction.return_value = tx
    processor.data_store_provider = store

    connector = SalesforceConnector(
        logger=logging.getLogger("test.salesforce.edges"),
        data_entities_processor=processor,
        data_store_provider=store,
        config_service=MagicMock(get_config=AsyncMock()),
        connector_id="conn-sf-1",
    )
    connector.salesforce_instance_url = "https://myinstance.salesforce.com"
    return connector, tx


async def _pages(*items: object) -> AsyncIterator[list]:
    yield list(items)


def _edges_written(tx: MagicMock) -> list[tuple[str, dict]]:
    return [
        (call.kwargs["collection"], edge)
        for call in tx.batch_create_edges.await_args_list
        for edge in call.args[0]
        if call.kwargs["collection"] in EDGE_COLLECTIONS
    ]


async def test_account_edges_without_dates_still_pass_the_edge_schema() -> None:
    connector, tx = _connector()
    account = SalesforceAccount.model_validate({
        "Id": "acc-1", "Name": "No Dates",
        # A won opportunity makes the customer edge too.
        "Opportunities": {"records": [{"IsWon": True, "IsClosed": True, "CloseDate": "2024-01-15"}]},
    })

    await connector._sync_accounts(_pages(account))

    written = _edges_written(tx)
    assert {collection for collection, _ in written} == {"prospect", "customer", "dealOf"}
    validator = NodeSchemaValidator()
    for collection, edge in written:
        assert isinstance(edge["createdAtTimestamp"], int), collection
        assert isinstance(edge["updatedAtTimestamp"], int), collection
        validator.validate_edge(collection, edge)


async def test_an_account_with_dates_keeps_them_on_its_edges() -> None:
    connector, tx = _connector()
    account = SalesforceAccount.model_validate({
        "Id": "acc-2", "Name": "Dated",
        "CreatedDate": "2024-01-01T00:00:00.000+0000",
        "LastModifiedDate": "2024-06-01T00:00:00.000+0000",
        "Opportunities": {"records": []},
    })

    await connector._sync_accounts(_pages(account))

    (_, prospect), *_ = [e for e in _edges_written(tx) if e[0] == "prospect"]
    assert prospect["createdAtTimestamp"] == 1704067200000
    assert prospect["updatedAtTimestamp"] == 1717200000000


async def test_a_deal_info_edge_without_dates_still_passes_the_edge_schema() -> None:
    connector, tx = _connector()
    # The deal info edge is written once the opportunity's record exists.
    tx.get_record_by_external_id = AsyncMock(
        side_effect=lambda connector_id, external_id: SimpleNamespace(id="rec-1", external_record_id=external_id)
    )
    opp = SalesforceOpportunity.model_validate({"Id": "opp-1", "Name": "Undated", "StageName": "Proposal"})

    await connector._sync_opportunities(_pages(opp))

    deal_info = [edge for collection, edge in _edges_written(tx) if collection == "dealInfo"]
    assert len(deal_info) == 1
    NodeSchemaValidator().validate_edge("dealInfo", deal_info[0])


async def test_a_contact_and_its_member_of_edge_without_dates_pass_the_edge_schema() -> None:
    connector, tx = _connector()
    tx.get_nodes_by_field_in = AsyncMock(
        side_effect=lambda collection, field, values: (
            [{"id": "org-acme", "name": "Acme", "isExternal": True}] if collection == "organizations" else []
        )
    )
    contact = SalesforceContact.model_validate({
        "Id": "c-1", "Email": "c@example.com", "Account": {"Name": "Acme"},
    })

    await connector._sync_contacts(_pages(contact))

    written = _edges_written(tx)
    assert {collection for collection, _ in written} == {"contact", "memberOf"}
    for collection, edge in written:
        assert isinstance(edge["createdAtTimestamp"], int), collection
        assert isinstance(edge["updatedAtTimestamp"], int), collection
        NodeSchemaValidator().validate_edge(collection, edge)


async def test_a_lead_edge_without_dates_still_passes_the_edge_schema() -> None:
    connector, tx = _connector()
    lead = SalesforceLead.model_validate({"Id": "l-1", "Email": "l@example.com", "Company": "Acme"})

    await connector._sync_leads(_pages(lead))

    ((collection, edge),) = _edges_written(tx)
    assert collection == "lead"
    NodeSchemaValidator().validate_edge("lead", edge)


async def test_a_contact_and_lead_with_dates_keep_them_on_their_edges() -> None:
    connector, tx = _connector()
    dates = {"CreatedDate": "2024-01-01T00:00:00.000+0000", "LastModifiedDate": "2024-06-01T00:00:00.000+0000"}
    await connector._sync_contacts(_pages(SalesforceContact.model_validate({"Id": "c-2", "Email": "c2@example.com", **dates})))
    await connector._sync_leads(_pages(SalesforceLead.model_validate({"Id": "l-2", "Email": "l2@example.com", **dates})))

    for _, edge in _edges_written(tx):
        assert edge["createdAtTimestamp"] == 1704067200000
        assert edge["updatedAtTimestamp"] == 1717200000000
