"""The connectors switched to `scopes`, read from their app documents on PostgreSQL (`currency.switched_connectors`): the organisation's apps (by
its org-to-app edges) whose mode is exactly `scopes`, and no other organisation's.

Needs a migrated database: PG_CORE_URL (a `pipeshub_connectors_login` DSN), PG_POOLER=none.
"""
from __future__ import annotations

import uuid

import pytest

from app.config.constants.arangodb import CollectionNames
from app.services.permissions.currency import MODE_KEY, switched_connectors

pytestmark = pytest.mark.integration


async def _apps(pg_provider, org: str, apps: dict[str, str | None]) -> None:
    """The organisation's apps, each linked by its org-to-app edge, with the given mode (None: no mode)."""
    await pg_provider.batch_upsert_nodes([{"_key": k, **({MODE_KEY: m} if m is not None else {})} for k, m in apps.items()],
                                         CollectionNames.APPS.value)
    await pg_provider.batch_create_edges([{"from_collection": CollectionNames.ORGS.value, "from_id": org, "to_collection": CollectionNames.APPS.value,
                                           "to_id": k} for k in apps], CollectionNames.ORG_APP_RELATION.value)


async def test_the_switched_connectors_are_the_organisation_s_apps_whose_mode_is_exactly_scopes(pg_provider, org_ctx) -> None:
    org, other = uuid.uuid4().hex[:24], uuid.uuid4().hex[:24]
    with org_ctx(other):
        await _apps(pg_provider, other, {"x1": "scopes"})
    with org_ctx(org):
        await _apps(pg_provider, org, {"c1": "scopes", "c2": "scopes", "c3": "legacy", "c4": "Scopes", "c5": " scopes", "c6": None})
        assert await switched_connectors(pg_provider, org) == {"c1", "c2"}      # not another org's x1, not a value that is not exactly `scopes`
        assert await pg_provider.update_node("c1", CollectionNames.APPS.value, {MODE_KEY: "legacy"})
        assert await switched_connectors(pg_provider, org) == {"c2"}            # a rollback leaves the set at the next read
    with org_ctx(other):
        assert await switched_connectors(pg_provider, other) == {"x1"}
