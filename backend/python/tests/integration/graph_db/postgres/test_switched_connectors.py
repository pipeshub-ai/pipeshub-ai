"""The connectors switched to `scopes`, read from their app documents on PostgreSQL (`currency.switched_connectors`): the viewer's organisation's
apps whose mode is exactly `scopes`, and no other organisation's.

Needs a migrated database: PG_CORE_URL (a `pipeshub_connectors_login` DSN), PG_POOLER=none.
"""
from __future__ import annotations

import uuid

import pytest

from app.config.constants.arangodb import CollectionNames
from app.services.permissions.currency import MODE_KEY, switched_connectors

pytestmark = pytest.mark.integration


async def test_the_switched_connectors_are_the_organisation_s_apps_whose_mode_is_exactly_scopes(pg_provider, org_ctx) -> None:
    org, other = uuid.uuid4().hex[:24], uuid.uuid4().hex[:24]
    with org_ctx(other):
        assert await pg_provider.batch_upsert_nodes([{"_key": "x1", "orgId": other, MODE_KEY: "scopes"}], CollectionNames.APPS.value)
    with org_ctx(org):
        apps = [{"_key": "c1", "orgId": org, MODE_KEY: "scopes"}, {"_key": "c2", "orgId": org, MODE_KEY: "scopes"},
                {"_key": "c3", "orgId": org, MODE_KEY: "legacy"}, {"_key": "c4", "orgId": org, MODE_KEY: "Scopes"},
                {"_key": "c5", "orgId": org, MODE_KEY: " scopes"}, {"_key": "c6", "orgId": org}]
        assert await pg_provider.batch_upsert_nodes(apps, CollectionNames.APPS.value)
        assert await switched_connectors(pg_provider, org) == {"c1", "c2"}      # not another org's x1, not a value that is not exactly `scopes`
        assert await pg_provider.update_node("c1", CollectionNames.APPS.value, {MODE_KEY: "legacy"})
        assert await switched_connectors(pg_provider, org) == {"c2"}            # a rollback leaves the set at the next read
    with org_ctx(other):
        assert await switched_connectors(pg_provider, other) == {"x1"}
