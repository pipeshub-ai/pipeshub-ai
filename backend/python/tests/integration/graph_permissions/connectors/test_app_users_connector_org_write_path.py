"""A connector's listed users are looked up and created in the connector's org, on both graphs, through the real processor.

The store holds two orgs. The other one is written first and its key sorts first, so it is the first org the store lists; the
connector's org is written last. One listed user is already stored in the connector's org, one is new.
"""

import pytest

from app.config.constants.arangodb import CollectionNames, Connectors
from app.models.entities import AppUser

from ..processor_harness import build_processor
from ..write_path_support import TS, _suffix

pytestmark = pytest.mark.integration


async def _org(backend, key: str, name: str) -> None:
    await backend.provider.batch_upsert_nodes([{"_key": key, "accountType": "enterprise", "isActive": True, "name": name,
                                                "createdAtTimestamp": TS, "updatedAtTimestamp": TS}], collection=CollectionNames.ORGS.value)


async def test_listed_users_are_found_and_created_in_the_connectors_org_never_in_the_first_org(backend) -> None:
    provider = backend.provider
    first, org, connector_id = f"0-first-{_suffix()}", f"org-late-{_suffix()}", f"conn-{_suffix()}"
    await _org(backend, first, "First Org")
    await _org(backend, org, "Late Org")
    await provider.batch_upsert_nodes([{"_key": connector_id, "orgId": org, "name": "Confluence", "type": Connectors.CONFLUENCE.value,
                                        "appGroup": "Atlassian", "scope": "team", "isActive": True, "createdAtTimestamp": TS}],
                                      collection=CollectionNames.APPS.value)
    stored, new = f"stored-{_suffix()}@acme.com", f"new-{_suffix()}@acme.com"
    stored_key = f"user-{_suffix()}"
    await provider.batch_upsert_nodes([{"_key": stored_key, "email": stored, "orgId": org, "isActive": True,
                                        "createdAtTimestamp": TS, "updatedAtTimestamp": TS}], collection=CollectionNames.USERS.value)
    processor, _ = build_processor(provider, org)

    def listed(email: str, sid: str) -> AppUser:
        return AppUser(app_name=Connectors.CONFLUENCE, connector_id=connector_id, source_user_id=sid, email=email, full_name=sid, org_id=org)

    async def users_with(email: str) -> list[dict]:
        return await provider.get_nodes_by_filters(CollectionNames.USERS.value, {"email": email})

    async def orgs_of(user_key: str) -> set[str]:
        edges = await provider.get_edges_from_node(f"{CollectionNames.USERS.value}/{user_key}", CollectionNames.BELONGS_TO.value)
        return {e.get("_to") or f"{e.get('to_collection')}/{e.get('to_id')}" for e in edges}

    try:
        await processor.on_new_app_users([listed(stored, "s-stored"), listed(new, "s-new")])

        found = await users_with(stored)
        assert [(u.get("_key") or u.get("id"), u.get("orgId")) for u in found] == [(stored_key, org)], \
            "the user already stored in the connector's org is the one listed, not a copy in another org"
        created = await users_with(new)
        assert [u.get("orgId") for u in created] == [org], "a new user is created in the connector's org"
        assert await orgs_of(created[0].get("_key") or created[0].get("id")) == {f"{CollectionNames.ORGS.value}/{org}"}
        for email in (stored, new):
            assert await provider.get_user_by_email(email, org_id=first) is None, "nothing is written in the first org"
    finally:
        stale = [u.get("_key") or u.get("id") for email in (stored, new) for u in await users_with(email)]
        await provider.delete_nodes_and_edges(stale, CollectionNames.USERS.value)
        await provider.delete_nodes_and_edges([connector_id], CollectionNames.APPS.value)
        await provider.delete_nodes_and_edges([first, org], CollectionNames.ORGS.value)
