"""Links from a record to the CRM account it belongs to (KG-13, slice 3a).

A CRM connector files an account's deals, cases and tasks in the account's
record group, and links that group to the account's external organisation
(``dealOf``). This module writes the record -> organisation
``entityRelations`` edge (``FOR_ACCOUNT``, origin INFERRED), so the
organisation is an entity reached from its records like a person is.

Only an organisation of the record's tenant is linked: an external
organisation carries its tenant as ``parentOrgId``.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from app.config.constants.arangodb import CollectionNames, EntityOrigin, EntityRelations
from app.models.entities import RecordType
from app.utils.time_conversion import get_epoch_timestamp_in_ms

if TYPE_CHECKING:
    from logging import Logger

    from app.models.entities import Record

# Record types a CRM files under an account. By record type, not record group
# type: a record read back from the graph does not carry the latter.
ACCOUNT_RECORD_TYPES = frozenset({RecordType.DEAL, RecordType.CASE, RecordType.TASK})


class OrganizationStore(Protocol):
    async def get_record_group_organization(self, record_group_id: str, org_id: str) -> str | None: ...
    async def delete_edges_between_collections(
        self, from_id: str, from_collection: str, edge_collection: str, to_collection: str,
    ) -> None: ...
    async def batch_create_entity_relations(self, edges: list[dict]) -> None: ...


async def link_record_organization(record: Record, store: OrganizationStore, logger: Logger) -> int:
    """Replace ``record``'s account edge with the account its group belongs
    to now. Returns the number of edges written (0 or 1).

    A failed lookup leaves the existing edge alone; a failed write is logged,
    not raised, since ``FOR_ACCOUNT`` is a new edge type an older pod's schema
    can reject during a rolling deploy (as in ``record_people``)."""
    if record.record_type not in ACCOUNT_RECORD_TYPES or not record.record_group_id:
        return 0
    try:
        organization_id = await store.get_record_group_organization(record.record_group_id, record.org_id)
    except Exception as exc:
        logger.warning("Account lookup failed for record %s: %s", record.id, type(exc).__name__)
        return 0
    try:
        # TODO(KG-13 slice 3b): extraction will also write record ->
        # organisation edges (EXTRACTED); clear only INFERRED ones then.
        await store.delete_edges_between_collections(
            record.id, CollectionNames.RECORDS.value,
            CollectionNames.ENTITY_RELATIONS.value, CollectionNames.ORGS.value,
        )
    except Exception as exc:  # the edge below replaces a stale one anyway
        logger.warning("Could not clear account edges of record %s: %s", record.id, type(exc).__name__)
    if not organization_id:
        return 0
    now = get_epoch_timestamp_in_ms()
    edge = {
        "_from": f"{CollectionNames.RECORDS.value}/{record.id}",
        "_to": f"{CollectionNames.ORGS.value}/{organization_id}",
        "edgeType": EntityRelations.FOR_ACCOUNT.value,
        "origin": EntityOrigin.INFERRED.value,
        "source": record.connector_id,
        "createdAtTimestamp": now,
        "updatedAtTimestamp": now,
    }
    try:
        await store.batch_create_entity_relations([edge])
    except Exception as exc:
        logger.warning("Record %s: account edge not written: %s", record.id, type(exc).__name__)
        return 0
    return 1
