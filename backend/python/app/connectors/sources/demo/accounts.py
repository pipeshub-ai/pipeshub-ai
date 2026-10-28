"""CRM accounts for the demo, written the way the Salesforce connector writes them.

``SalesforceConnector._sync_accounts`` makes each account an external
organisation of the tenant (``parentOrgId``), files the account's deals and
cases in a record group named after it, and links that group to the
organisation with ``dealOf`` and the tenant to it with ``prospect``.
``record_organizations`` follows ``dealOf`` to give each deal and case its
``FOR_ACCOUNT`` edge, so the demo's accounts must exist in exactly that shape.

An account keeps the organisation node it already has, matched by name as the
Salesforce connector matches it; one that extraction made from documents
naming the company first is taken over the same way.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from app.config.constants.arangodb import CollectionNames
from app.models.entities import Org
from app.modules.entity_resolution.organizations import (
    accounts_last,
    extracted_organization_id,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from app.connectors.core.base.data_store.data_store import DataStoreProvider
    from app.models.entities import RecordGroup


def _existing_keys(rows: list[dict], tenant_id: str) -> dict[str, str]:
    return {
        row["name"]: row.get("id", row.get("_key"))
        for row in accounts_last(rows)
        if row.get("parentOrgId") == tenant_id
    }


def _settle_key(name: str, by_name: Mapping[str, str], tenant_id: str) -> str | None:
    existing = by_name.get(name)
    if existing is not None:
        return existing
    extracted = extracted_organization_id(tenant_id, name)
    return extracted if extracted in set(by_name.values()) else None


async def sync_accounts(
    store: DataStoreProvider,
    tenant_id: str,
    accounts: list[tuple[RecordGroup, Mapping[str, Any]]],
    now: int,
) -> dict[str, str]:
    """Upsert each (account record group, fixture organisation) pair and its
    ``dealOf`` and ``prospect`` edges. The record groups must already be
    stored. Returns the organisation key by fixture organisation id."""
    if not accounts:
        return {}
    keys: dict[str, str] = {}
    async with store.transaction() as tx:
        by_name = _existing_keys(await tx.get_all_orgs(is_external=True) or [], tenant_id)
        orgs: list[Org] = []
        for group, organization in accounts:
            key = _settle_key(organization["name"], by_name, tenant_id)
            if key is not None:
                await tx.delete_edges_to(
                    to_id=key, to_collection=CollectionNames.ORGS.value, collection=CollectionNames.PROSPECT.value,
                )
                await tx.delete_edges_from(group.id, CollectionNames.RECORD_GROUPS.value, CollectionNames.DEAL_OF.value)
            org = Org(
                id=key or str(uuid.uuid4()),
                name=organization["name"],
                is_external=True,
                parent_org_id=tenant_id,
                website=f"https://{organization['domain']}" if organization.get("domain") else None,
                source_created_at=now,
                source_updated_at=now,
            )
            orgs.append(org)
            by_name[org.name] = org.id
            keys[organization["id"]] = org.id
        await tx.batch_upsert_orgs([org.to_arango_org() for org in orgs])
        stamps = {"createdAtTimestamp": now, "updatedAtTimestamp": now}
        await tx.batch_create_edges(
            [
                {"from_id": group.id, "from_collection": CollectionNames.RECORD_GROUPS.value,
                 "to_id": keys[organization["id"]], "to_collection": CollectionNames.ORGS.value, **stamps}
                for group, organization in accounts
            ],
            collection=CollectionNames.DEAL_OF.value,
        )
        await tx.batch_create_edges(
            [
                {"from_id": tenant_id, "from_collection": CollectionNames.ORGS.value,
                 "to_id": org.id, "to_collection": CollectionNames.ORGS.value, "startTime": now, **stamps}
                for org in orgs
            ],
            collection=CollectionNames.PROSPECT.value,
        )
    return keys
