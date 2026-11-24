"""Links from a record to the CRM account it belongs to (KG-13, slice 3a).

A CRM connector files an account's deals, cases and tasks in the account's
record group, and links that group to the account's external organisation
(``dealOf``). This module writes the record -> organisation
``entityRelations`` edge (``FOR_ACCOUNT``, origin INFERRED), so the
organisation is an entity reached from its records like a person is.

Only an organisation of the record's own org is linked: an external
organisation carries the org it belongs to as ``parentOrgId``.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from app.config.constants.arangodb import CollectionNames, EntityOrigin, EntityRelations
from app.connectors.core.base.data_processor.record_people import is_programming_error
from app.models.entities import RecordType
from app.utils.time_conversion import get_epoch_timestamp_in_ms

if TYPE_CHECKING:
    from collections.abc import Callable
    from logging import Logger

    from app.models.entities import Record

# Record types a CRM files under an account. By record type, not record group
# type: a record read back from the graph does not carry the latter.
ACCOUNT_RECORD_TYPES = frozenset({RecordType.DEAL, RecordType.CASE, RecordType.TASK})


class OrganizationStore(Protocol):
    async def get_record_group_organization(self, record_group_id: str, org_id: str) -> str | None: ...
    async def replace_record_entity_relations(
        self, record_id: str, to_collections: list[str], origin: str, edges: list[dict],
    ) -> None: ...


async def link_record_organization(
    record: Record,
    store: OrganizationStore,
    logger: Logger,
    must_propagate: Callable[[Exception], bool] | None = None,
) -> int:
    """Replace ``record``'s account edge with the account its group belongs
    to now. Returns the number of edges written (0 or 1).

    A failed lookup or write leaves the existing edge alone; a failed write is logged,
    not raised, since ``FOR_ACCOUNT`` is a new edge type an older pod's schema
    can reject during a rolling deploy (as in ``record_people``). Failures
    ``must_propagate`` names (a conflict the caller retries, a failure that
    ended its transaction) are raised instead."""
    propagate = must_propagate or (lambda _exc: False)
    if record.record_type not in ACCOUNT_RECORD_TYPES or not record.record_group_id:
        return 0
    try:
        organization_id = await store.get_record_group_organization(record.record_group_id, record.org_id)
    except Exception as exc:
        if propagate(exc) or is_programming_error(exc):
            raise
        logger.warning("Account lookup failed for record %s: %s", record.id, exc)
        return 0
    now = get_epoch_timestamp_in_ms()
    edges = [{
        "_from": f"{CollectionNames.RECORDS.value}/{record.id}",
        "_to": f"{CollectionNames.ORGS.value}/{organization_id}",
        "edgeType": EntityRelations.FOR_ACCOUNT.value,
        "origin": EntityOrigin.INFERRED.value,
        "source": record.connector_id,
        "createdAtTimestamp": now,
        "updatedAtTimestamp": now,
    }] if organization_id else []
    try:
        # One replace, so a failed write keeps the old edge. Only INFERRED:
        # extraction's links to organisations the record names are its own to replace.
        await store.replace_record_entity_relations(
            record.id, [CollectionNames.ORGS.value], EntityOrigin.INFERRED.value, edges,
        )
    except Exception as exc:
        if propagate(exc) or is_programming_error(exc):
            raise
        logger.warning("Record %s: account edge not replaced: %s", record.id, exc)
        return 0
    return len(edges)
