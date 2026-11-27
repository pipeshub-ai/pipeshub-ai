"""A connector instance created and then discarded leaves nothing behind, on PostgreSQL against a real database.

A connector create that fails after its instance write discards the instance (``ConnectorRegistry.discard_connector_instance``,
the provider's own ``delete_connector_instance``) so that a retry with the same name works. This creates two instances through
the registry, discards one, and checks that its node and edge are gone, that it is no longer listed, that its name is free for
either scope, and that the other instance is untouched. The Neo4j and ArangoDB twin is ../test_connector_create_discard_real_backends.py.

Needs a migrated database: PG_CORE_URL (a `pipeshub_connectors_login` DSN), PG_POOLER=none.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import CollectionNames
from app.connectors.core.registry.connector_registry import (
    ConnectorInstanceNotDeletedError,
    ConnectorRegistry,
)

from .test_access import World, _org
from .test_access import w as w

if TYPE_CHECKING:
    from app.core.db import Tx

pytestmark = pytest.mark.integration

APPS = CollectionNames.APPS.value
USER = "u"


def _registry(w: World) -> ConnectorRegistry:
    container = MagicMock()
    container.logger = MagicMock(return_value=logging.getLogger("pg-create-discard"))
    # No licence key reads as licensed, so a personal create takes no cap lock.
    container.config_service.return_value.get_config = AsyncMock(return_value=None)
    registry = ConnectorRegistry(container)
    registry._graph_provider = w.p
    registry._connectors["Drive"] = {"appGroup": "Google Workspace", "supportedAuthTypes": ["OAUTH"], "config": {}}
    registry.get_connector_metadata = AsyncMock(return_value=registry._connectors["Drive"])  # type: ignore[method-assign]
    return registry


async def _create(registry: ConnectorRegistry, w: World, name: str, scope: str) -> str:
    instance = await registry.create_connector_instance_on_configuration(
        connector_type="Drive", instance_name=name, scope=scope, created_by=USER, org_id=w.org, is_admin=True,
        selected_auth_type="OAUTH",
    )
    assert instance is not None
    return instance["_key"]


async def _edges_of(w: World, key: str) -> int:
    async def go(tx: Tx) -> int:
        return await tx.fetchval(
            "SELECT count(*) FROM graph.edges WHERE org_id = $1 AND ((from_coll = 'apps' AND from_id = $2) OR (to_coll = 'apps' AND to_id = $2))",
            w.org, key)
    return await w.p._q(go, None)


async def _listed(w: World) -> set[str]:
    documents, _total = await w.p.get_filtered_connector_instances(
        collection=APPS, edge_collection=CollectionNames.ORG_APP_RELATION.value,
        org_id=w.org, user_id=USER, skip=0, limit=50, is_admin=True,
    )
    return {d.get("_key") or d.get("id") for d in documents}


async def _name_taken(w: World, name: str, scope: str) -> bool:
    return await w.p.check_connector_name_exists(collection=APPS, instance_name=name, scope=scope, org_id=w.org, user_id=USER)


@pytest.mark.parametrize("scope", ["personal", "team"])
async def test_a_fresh_instance_discarded_leaves_no_node_no_edge_and_frees_its_name(w: World, scope: str) -> None:
    await w.nodes(CollectionNames.ORGS.value, {"_key": w.org, "name": "Acme", "isActive": True})
    registry = _registry(w)
    doomed = await _create(registry, w, "Doomed", scope)
    kept = await _create(registry, w, "Kept", scope)
    assert await _edges_of(w, doomed) == 1
    assert doomed in await _listed(w)
    assert await _name_taken(w, "Doomed", scope)

    await registry.discard_connector_instance(doomed, w.org)

    assert await w.p.get_document(doomed, APPS) is None
    assert await _edges_of(w, doomed) == 0
    assert doomed not in await _listed(w) and kept in await _listed(w)
    assert not await _name_taken(w, "Doomed", scope)
    assert await _name_taken(w, "Kept", scope)
    assert await _edges_of(w, kept) == 1
    await _create(registry, w, "Doomed", scope)


async def test_a_discard_under_another_org_raises_and_deletes_nothing(w: World) -> None:
    await w.nodes(CollectionNames.ORGS.value, {"_key": w.org, "name": "Acme", "isActive": True})
    registry = _registry(w)
    key = await _create(registry, w, "Mine", "personal")
    with pytest.raises(ConnectorInstanceNotDeletedError):
        await registry.discard_connector_instance(key, _org())
    assert (await w.p.get_document(key, APPS))["name"] == "Mine"
    assert await _edges_of(w, key) == 1

