"""A connector's listed users are created in the connector's org, on both graphs, through the real processor.

The store holds several orgs; the connector's org is written last, so it is never the first org the store lists.
"""

import pytest

from app.config.constants.arangodb import CollectionNames, Connectors
from app.models.entities import AppUser

from ..processor_harness import build_processor
from ..write_path_support import TS, _seed_org, _suffix

pytestmark = pytest.mark.integration


async def test_a_listed_user_is_created_in_the_connectors_org(backend) -> None:
    await _seed_org(backend)        # another org, written first
    org, connector_id = f"org-late-{_suffix()}", f"conn-{_suffix()}"
    await backend.provider.batch_upsert_nodes([{"_key": org, "accountType": "enterprise", "isActive": True, "name": "Late Org",
                                                "createdAtTimestamp": TS, "updatedAtTimestamp": TS}], collection=CollectionNames.ORGS.value)
    await backend.provider.batch_upsert_nodes([{"_key": connector_id, "orgId": org, "name": "Confluence", "type": Connectors.CONFLUENCE.value,
                                                "appGroup": "Atlassian", "scope": "team", "isActive": True, "createdAtTimestamp": TS}],
                                              collection=CollectionNames.APPS.value)
    processor, _ = build_processor(backend.provider, org)
    email = f"new-{_suffix()}@acme.com"
    try:
        await processor.on_new_app_users([AppUser(app_name=Connectors.CONFLUENCE, connector_id=connector_id, source_user_id="s-new",
                                                  email=email, full_name="New", org_id=org)])

        user = await backend.provider.get_user_by_email(email, org_id=org)
        assert user is not None, "the user is created in the connector's org"
        assert user.org_id == org
        edges = await backend.provider.get_edges_from_node(f"{CollectionNames.USERS.value}/{user.id}", CollectionNames.BELONGS_TO.value)
        orgs = {e.get("_to") or f"{e.get('to_collection')}/{e.get('to_id')}" for e in edges}
        assert orgs == {f"{CollectionNames.ORGS.value}/{org}"}, "and belongs to that org only"
    finally:
        stale = await backend.provider.get_nodes_by_filters(CollectionNames.USERS.value, {"email": email})
        await backend.provider.delete_nodes_and_edges([u.get("_key") or u.get("id") for u in stale], CollectionNames.USERS.value)
        await backend.provider.delete_nodes_and_edges([connector_id], CollectionNames.APPS.value)
        await backend.provider.delete_nodes_and_edges([org], CollectionNames.ORGS.value)
