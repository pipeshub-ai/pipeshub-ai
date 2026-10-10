"""R2-03: a user the source reports deactivated loses the connector gate, on both backends.

The ids come from ``get_all_app_users``, as the Jira DC sync reads them, so this also
proves that an app user's id is its user node on each store.
"""

import pytest

from app.config.constants.arangodb import CollectionNames

from .processor_harness import build_processor
from .test_write_path import ORG, TS, _seed_app, _seed_user, _suffix, backend  # noqa: F401

pytestmark = pytest.mark.integration

USERS, APPS, ORGS = CollectionNames.USERS.value, CollectionNames.APPS.value, CollectionNames.ORGS.value
GATES = CollectionNames.USER_APP_RELATION.value


async def _gated_user(backend, connector_id: str, email: str) -> str:
    user_id = await _seed_user(backend, email)
    await backend.provider.batch_create_edges([{
        "from_id": user_id, "from_collection": USERS, "to_id": ORG, "to_collection": ORGS,
        "entityType": "ORGANIZATION", "createdAtTimestamp": TS, "updatedAtTimestamp": TS,
    }], CollectionNames.BELONGS_TO.value)
    await backend.provider.batch_create_edges([{
        "from_id": user_id, "from_collection": USERS, "to_id": connector_id, "to_collection": APPS,
        "sourceUserId": f"key-{user_id}", "syncState": "NOT_STARTED",
        "createdAtTimestamp": TS, "updatedAtTimestamp": TS,
    }], GATES)
    return user_id


async def test_only_the_deactivated_users_gate_goes(backend) -> None:
    processor, _ = build_processor(backend.provider, ORG)
    connector_id = f"conn-{_suffix()}"
    await _seed_app(backend, connector_id)
    gone = await _gated_user(backend, connector_id, f"gone-{_suffix()}@example.com")
    stays = await _gated_user(backend, connector_id, f"stays-{_suffix()}@example.com")

    stored = {u.id: u for u in await processor.get_all_app_users(connector_id)}
    assert {gone, stays} <= set(stored), "an app user's id is not its user node"

    removed = await processor.remove_app_users_deactivated_at_source(connector_id, [stored[gone]])

    assert removed == 1
    assert not await backend.edge_exists(GATES, USERS, gone, APPS, connector_id)
    assert await backend.edge_exists(GATES, USERS, stays, APPS, connector_id)
