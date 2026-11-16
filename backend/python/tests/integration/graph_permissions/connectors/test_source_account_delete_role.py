"""The ArangoDB delete checks take the strongest role among the user's own grants and those of the source account it is linked to
in the record's connector (Neo4j and PostgreSQL decide deletes another way)."""

import pytest

from app.config.constants.arangodb import CollectionNames

from ..write_path_support import ORG, TS, _seed_app, _suffix

pytestmark = pytest.mark.integration


async def test_the_strongest_grant_of_the_user_and_its_linked_account_decides(arango_provider) -> None:
    class _Backend:
        provider = arango_provider
    p = arango_provider
    connector_id, user, account, record = f"conn-{_suffix()}", f"user-{_suffix()}", f"acct-{_suffix()}", f"rec-{_suffix()}"
    await _seed_app(_Backend(), connector_id)
    await p.batch_upsert_nodes([{"_key": user, "email": f"{user}@acme.com", "orgId": ORG, "isActive": True,
                                 "createdAtTimestamp": TS, "updatedAtTimestamp": TS}], CollectionNames.USERS.value)
    await p.batch_upsert_nodes([{"_key": account, "orgId": ORG, "connectorId": connector_id, "sourceUserId": "s-1", "email": f"{user}@acme.com",
                                 "state": "listed", "createdAtTimestamp": TS, "updatedAtTimestamp": TS}], CollectionNames.SOURCE_ACCOUNTS.value)
    await p.batch_upsert_nodes([{"_key": record, "orgId": ORG, "connectorId": connector_id, "recordName": "r", "recordType": "FILE",
                                 "externalRecordId": record, "origin": "CONNECTOR", "connectorName": "CONFLUENCE", "version": 0,
                                 "createdAtTimestamp": TS, "updatedAtTimestamp": TS}], CollectionNames.RECORDS.value)
    await p.batch_create_edges([{"from_id": user, "from_collection": CollectionNames.USERS.value, "to_id": account,
                                 "to_collection": CollectionNames.SOURCE_ACCOUNTS.value, "connectorId": connector_id, "via": "directory",
                                 "orgId": ORG, "createdAtTimestamp": TS, "updatedAtTimestamp": TS}], CollectionNames.AUTHENTICATED_AS.value)
    # the weaker grant is written first, so a check that takes whichever edge it reads first answers it
    for holder, coll, role in ((account, CollectionNames.SOURCE_ACCOUNTS.value, "READER"), (user, CollectionNames.USERS.value, "OWNER")):
        await p.batch_create_edges([{"from_id": holder, "from_collection": coll, "to_id": record, "to_collection": CollectionNames.RECORDS.value,
                                     "type": "USER", "role": role, "createdAtTimestamp": TS, "updatedAtTimestamp": TS}],
                                   CollectionNames.PERMISSION.value)
    try:
        assert await p._check_record_permission(record, user) == "OWNER"
        assert await p._check_drive_permissions(record, user) == "OWNER"
        assert await p._check_gmail_permissions(record, user) == "OWNER"
        # withdrawn, the account links nobody: with the user's own grant READER, its OWNER grant no longer counts
        await p.batch_create_edges([{"from_id": holder, "from_collection": coll, "to_id": record, "to_collection": CollectionNames.RECORDS.value,
                                     "type": "USER", "role": role, "createdAtTimestamp": TS, "updatedAtTimestamp": TS}
                                    for holder, coll, role in ((account, CollectionNames.SOURCE_ACCOUNTS.value, "OWNER"),
                                                               (user, CollectionNames.USERS.value, "READER"))],
                                   CollectionNames.PERMISSION.value)
        assert await p.update_node(account, CollectionNames.SOURCE_ACCOUNTS.value, {"state": "withdrawn"})
        assert await p._check_record_permission(record, user) == "READER"
        assert await p._check_drive_permissions(record, user) == "READER"
        assert await p._check_gmail_permissions(record, user) == "READER"
    finally:
        await p.delete_nodes_and_edges([record], CollectionNames.RECORDS.value)
        await p.delete_nodes_and_edges([account], CollectionNames.SOURCE_ACCOUNTS.value)
        await p.delete_nodes_and_edges([user], CollectionNames.USERS.value)
