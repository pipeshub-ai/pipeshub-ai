"""Access effect of removing the creator-to-admin link on Neo4j 5.26 and ArangoDB 3.12: in the differential world the user is linked AUTHENTICATED_AS a
source account on connector CONF. With CONF typed as an admin-acting connector (Gmail Workspace, Drive Workspace, Dropbox),
the startup removal drops the link of a creator the connector lists and the access check then admits exactly what the user's own grants admit (no
"auth_same" record, every other admitted record kept). Typed as Dropbox Personal or Confluence, nothing changes."""
from __future__ import annotations

import logging
from typing import Any

import pytest

from app.migrations.workspace_admin_link_migration import (
    WorkspaceAdminLinkMigrationService,
)

from . import test_permitted_entity_records_differential as d

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]


class _Config:
    def __init__(self) -> None:
        self.values: dict[str, Any] = {}

    async def get_config(self, key: str, **_: object) -> Any:  # noqa: ANN401
        return self.values.get(key)

    async def set_config(self, key: str, value: Any) -> None:  # noqa: ANN401
        self.values[key] = value


async def _retype(provider, org: str, conf_type: str) -> None:
    if hasattr(provider, "client") and hasattr(provider.client, "execute_query") and not hasattr(provider, "http_client"):
        await provider.client.execute_query("MATCH (a:App {id: $c}) SET a.type = $t", parameters={"c": d.CONF, "t": conf_type})
        # the creator is a member of the Workspace too (the user sync links members by email): their own gate
        await provider.client.execute_query("MATCH (u:User {id: $u}), (a:App {id: $c}) CREATE (u)-[:USER_APP_RELATION {sourceUserId: 'src-u'}]->(a)", parameters={"u": f"{org}-u", "c": d.CONF})
    else:
        await provider.http_client.execute_aql("FOR a IN apps FILTER a._key == @c UPDATE a WITH {type: @t} IN apps", {"c": d.CONF, "t": conf_type})
        await provider.http_client.execute_aql("INSERT {_from: @u, _to: @a, sourceUserId: 'src-u', syncState: 'COMPLETED', lastSyncUpdate: 1, createdAtTimestamp: 1} INTO userAppRelation",
                                               {"u": f"users/{org}-u", "a": f"apps/{d.CONF}"})


async def _link_exists(provider) -> bool:
    if hasattr(provider, "http_client"):
        rows = await provider.http_client.execute_aql("FOR l IN authenticatedAs FILTER l.connectorId == @c RETURN 1", {"c": d.CONF})
    else:
        rows = await provider.client.execute_query("MATCH ()-[r:AUTHENTICATED_AS {connectorId: $c}]->() RETURN 1 AS n", parameters={"c": d.CONF})
    return bool(rows)


@pytest.mark.parametrize(("conf_type", "removed"), [
    ("Gmail Workspace", True), ("Drive Workspace", True), ("Dropbox", True),
    ("Dropbox Personal", False), ("CONFLUENCE", False), ("Gmail", False), ("Drive", False),
])
async def test_migration_drops_only_the_admin_grant(d_seeded, monkeypatch, conf_type: str, removed: bool) -> None:
    provider, org, records = d_seeded
    await _retype(provider, org, conf_type)
    if hasattr(provider, "http_client"):
        await provider.http_client.execute_aql("FOR l IN authenticatedAs FILTER l.connectorId == @c REMOVE l IN authenticatedAs", {"c": d.CONF})
        await provider.upsert_authenticated_as(f"{org}-u", f"{org}-src", d.CONF, org)
    before = await d.by_access_check(provider, org)
    assert before == d.readable(org, records)
    auth = {r["id"] for r in records if r["grant"] == "auth_same"}
    assert auth & set(before), "the world must have a record only the link admits"

    async def one_org(**_kw: object) -> list[dict[str, str]]:
        return [{"_key": org}]

    monkeypatch.setattr(provider, "get_all_orgs", one_org)
    result = await WorkspaceAdminLinkMigrationService(provider, _Config(), logging.getLogger("wsleak-it")).migrate()
    assert result["success"] is True
    after = await d.by_access_check(provider, org)
    # the removal sweeps the whole store: assert on this world's own link, never on a store-wide count
    assert await _link_exists(provider) is (not removed)
    if removed:
        assert after == [k for k in before if k not in auth], "only the link's records go; the user's own grants stay"
    else:
        assert after == before


d_seeded = d.seeded
