"""On PostgreSQL: the startup migration removes the creator-to-admin link of the three connector
types in every org, the creator then reaches only their own grants (access check, Knowledge Hub, entity context), other
links stay, a same-id connector of another org is untouched, and the kg projection follows. PG_CORE_URL required."""
from __future__ import annotations

import logging
import uuid
from typing import Any

import pytest

from app.core.org_context import system
from app.migrations.workspace_admin_link_migration import WorkspaceAdminLinkMigrationService

pytestmark = [pytest.mark.integration, pytest.mark.timeout(120)]

TYPES = {"gm": "Gmail Workspace", "dr": "Drive Workspace", "db": "Dropbox", "dbp": "Dropbox Personal", "jira": "Jira"}


class _Config:
    def __init__(self) -> None:
        self.values: dict[str, Any] = {}

    async def get_config(self, key: str, **_: object) -> Any:  # noqa: ANN401
        return self.values.get(key)

    async def set_config(self, key: str, value: Any) -> None:  # noqa: ANN401
        self.values[key] = value


async def _seed(p, org_ctx, org: str, types: dict[str, str]) -> None:
    with org_ctx(org):
        await p.batch_upsert_nodes([{"_key": org, "name": "Org", "isActive": True}], "organizations")
        await p.batch_upsert_nodes([{"_key": k, "orgId": org, "userId": f"uid-{k}", "email": f"{k}@{org}.x", "isActive": True}
                                    for k in ("creator", "admin")], "users")
        await p.batch_upsert_nodes([{"_key": k, "orgId": org, "type": t, "isActive": True, "name": t} for k, t in types.items()], "apps")
        for k in types:
            await p.batch_create_edges([{"from_collection": "users", "from_id": "creator", "to_collection": "apps", "to_id": k,
                                         "type": "USER", "role": "READER"}], "permission")
            await p.batch_upsert_nodes([{"_key": f"adm-{k}", "orgId": org, "connectorId": k, "recordName": f"adm-{k}"},
                                        {"_key": f"own-{k}", "orgId": org, "connectorId": k, "recordName": f"own-{k}"}], "records")
            for r in (f"adm-{k}", f"own-{k}"):
                await p.batch_create_edges([{"from_collection": "apps", "from_id": k, "to_collection": "records", "to_id": r,
                                             "relationshipType": "PARENT_CHILD"}], "nodeRelations")
            await p.batch_create_edges([{"from_collection": "users", "from_id": "admin", "to_collection": "records", "to_id": f"adm-{k}",
                                         "type": "USER", "role": "READER"}], "permission")
            await p.batch_create_edges([{"from_collection": "users", "from_id": "creator", "to_collection": "records", "to_id": f"own-{k}",
                                         "type": "USER", "role": "READER"}], "permission")
            await p.batch_create_edges([{"from_collection": "users", "from_id": "creator", "to_collection": "apps", "to_id": k,
                                         "sourceUserId": "src-1"}], "userAppRelation")  # the connector lists the creator
            await p.upsert_authenticated_as("creator", "admin", k, org)


async def _links(p, org_ctx, org: str) -> set[str]:
    with org_ctx(org):
        return {e.get("connectorId") for e in await p.get_edges_from_node("users/creator", "authenticatedAs")}


async def _allowed(p, org_ctx, org: str, keys: list[str]) -> set[str]:
    with org_ctx(org):
        return set((await p.check_access("creator", org, node_ids=keys)).node_ids)


@pytest.fixture
async def orgs(pg_provider, pg_db, org_ctx, monkeypatch):
    a, b = uuid.uuid4().hex[:24], uuid.uuid4().hex[:24]
    try:
        await _seed(pg_provider, org_ctx, a, TYPES)
        # org b reuses the connector ids with every type rotated: the "gm" connector of b is a Jira connector, and so on
        await _seed(pg_provider, org_ctx, b, {"gm": "Jira", "dr": "Dropbox Personal", "db": "Gmail Workspace", "dbp": "Drive Workspace", "jira": "Dropbox"})
        real = pg_provider.get_all_orgs

        async def these(**kw):
            return [o for o in await real(**kw) if (o.get("_key") or o.get("id")) in (a, b)]

        monkeypatch.setattr(pg_provider, "get_all_orgs", these)
        yield a, b
    finally:
        for org in (a, b):
            with org_ctx(org):
                await pg_db.run(lambda tx, o=org: tx.execute("DELETE FROM graph.edges WHERE org_id = $1", o), write=True)
                await pg_db.run(lambda tx, o=org: tx.execute("DELETE FROM graph.nodes WHERE org_id = $1", o), write=True)


async def test_pg_migration_removes_only_the_three_types_in_each_org_and_access_follows(pg_provider, org_ctx, orgs) -> None:
    a, b = orgs
    adm = [f"adm-{k}" for k in TYPES]
    own = [f"own-{k}" for k in TYPES]
    assert await _allowed(pg_provider, org_ctx, a, adm + own) == set(adm + own), "the seeded leak: the link admits the admin's records"
    with org_ctx(a):
        ctx_before = {x["id"] for x in (await pg_provider.get_entity_access_context("uid-creator", a))["apps"]}
    config = _Config()
    with system("wsleak-it"):
        result = await WorkspaceAdminLinkMigrationService(pg_provider, config, logging.getLogger("wsleak-it")).migrate()
    assert result == {"success": True, "removed": 6}, result
    assert await _links(pg_provider, org_ctx, a) == {"dbp", "jira"}
    assert await _links(pg_provider, org_ctx, b) == {"gm", "dr"}, "org b keeps the links of its own non-admin types, whatever ids org a uses"
    assert await _allowed(pg_provider, org_ctx, a, adm + own) == set(own) | {"adm-dbp", "adm-jira"}
    assert await _allowed(pg_provider, org_ctx, b, adm + own) == set(own) | {"adm-gm", "adm-dr"}
    with org_ctx(a):
        kh = await pg_provider.get_knowledge_hub_access_v3("creator", a)
        for k in ("gm", "dr", "db"):
            assert f"adm-{k}" not in kh["by_connector"].get(k, []), kh
        ctx_after = {x["id"] for x in (await pg_provider.get_entity_access_context("uid-creator", a))["apps"]}
    assert ctx_before == set(TYPES), "before: every connector reached through its link"
    assert ctx_after == set(TYPES), "the creator is listed on every connector, so each stays reachable through their own account, not the link"
    with system("wsleak-it"):
        again = await WorkspaceAdminLinkMigrationService(pg_provider, config, logging.getLogger("wsleak-it")).migrate()
    assert again == {"success": True, "removed": 0}, "a second startup finds nothing and runs again without a flag"


async def test_pg_remove_is_org_bound(pg_provider, org_ctx, orgs) -> None:
    a, b = orgs
    with org_ctx(a):
        assert await pg_provider.remove_authenticated_as("gm") is True
    assert "gm" not in await _links(pg_provider, org_ctx, a)
    assert "gm" in await _links(pg_provider, org_ctx, b), "removing org a's link must not touch org b's same-id connector"


async def test_pg_a_link_moved_by_a_verified_email_change_is_still_removed(pg_provider, org_ctx, pg_db) -> None:
    org = uuid.uuid4().hex[:24]
    email = "admin-moved@corp.example"
    try:
        with org_ctx(org):
            await pg_provider.batch_upsert_nodes([{"_key": org, "name": "Org", "isActive": True}], "organizations")
            await pg_provider.batch_upsert_nodes([
                {"_key": "creator", "orgId": org, "userId": "75a1b2c3d4e5f6a7b8c9d0e1", "email": "creator@corp.example", "isActive": True},
                {"_key": "adminlogin", "orgId": org, "userId": "65a1b2c3d4e5f6a7b8c9d0e1", "email": "old@corp.example", "isActive": True},
                {"_key": "adminstub", "orgId": org, "userId": "g-src-1", "email": email, "isActive": False},
            ], "users")
            await pg_provider.batch_upsert_nodes([{"_key": "gm", "orgId": org, "type": "Gmail Workspace", "isActive": True, "name": "gm"}], "apps")
            await pg_provider.batch_create_edges([{"from_collection": "users", "from_id": "creator", "to_collection": "apps", "to_id": "gm",
                                                   "sourceUserId": "src-1"}], "userAppRelation")
            await pg_provider.upsert_authenticated_as("creator", "adminstub", "gm", org)
            await pg_provider.apply_verified_user_email("65a1b2c3d4e5f6a7b8c9d0e1", org, email)
            moved = await pg_provider.get_edges_from_node("users/creator", "authenticatedAs")
            assert [e.get("connectorId") for e in moved] == ["gm"], "the link moved with the stub's edges"
            assert await pg_provider.remove_authenticated_as("gm") is True
            assert await pg_provider.get_edges_from_node("users/creator", "authenticatedAs") == []
    finally:
        with org_ctx(org):
            await pg_db.run(lambda tx: tx.execute("DELETE FROM graph.edges WHERE org_id = $1", org), write=True)
            await pg_db.run(lambda tx: tx.execute("DELETE FROM graph.nodes WHERE org_id = $1", org), write=True)


async def test_pg_startup_cost_grows_linearly_with_the_orgs(pg_provider, org_ctx, pg_db, monkeypatch) -> None:
    """PostgreSQL reads one org at a time (RLS), so the removal goes org by org: 3 reads per org, and each read sees only that
    org's apps. WSLEAK_PG_ORGS sets the number of orgs (default 100: 4 apps and 3 links each)."""
    import os
    import time

    n = int(os.environ.get("WSLEAK_PG_ORGS", "100"))
    orgs = [uuid.uuid4().hex[:24] for _ in range(n)]
    try:
        for org in orgs:
            with org_ctx(org):
                await pg_provider.batch_upsert_nodes([{"_key": org, "name": "Org", "isActive": True}], "organizations")
                await pg_provider.batch_upsert_nodes([{"_key": k, "orgId": org, "userId": f"u-{k}", "email": f"{k}@{org}.x", "isActive": True}
                                                      for k in ("creator", "admin")], "users")
                await pg_provider.batch_upsert_nodes([{"_key": f"a{j}-{org}", "orgId": org, "type": t, "isActive": True, "name": t}
                                                      for j, t in enumerate(("Gmail Workspace", "Drive Workspace", "Dropbox", "Jira"))], "apps")
                for j in range(4):
                    await pg_provider.batch_create_edges([{"from_collection": "users", "from_id": "creator", "to_collection": "apps",
                                                           "to_id": f"a{j}-{org}", "sourceUserId": "src-1"}], "userAppRelation")
                    await pg_provider.upsert_authenticated_as("creator", "admin", f"a{j}-{org}", org)
        real = pg_provider.get_all_orgs

        async def these(**kw):
            return [o for o in await real(**kw) if (o.get("_key") or o.get("id")) in set(orgs)]

        monkeypatch.setattr(pg_provider, "get_all_orgs", these)
        t0 = time.monotonic()
        with system("wsleak-it"):
            first = await WorkspaceAdminLinkMigrationService(pg_provider, _Config(), logging.getLogger("wsleak-it")).migrate()
        t1 = time.monotonic()
        with system("wsleak-it"):
            again = await WorkspaceAdminLinkMigrationService(pg_provider, _Config(), logging.getLogger("wsleak-it")).migrate()
        t2 = time.monotonic()
        print(f"\nWSLEAK-SCALE postgres orgs={n} first_start={t1 - t0:.2f}s removed={first['removed']} steady_start={t2 - t1:.2f}s removed={again['removed']}")
        assert first == {"success": True, "removed": 3 * n} and again == {"success": True, "removed": 0}
        assert t1 - t0 < 30 and t2 - t1 < 30
    finally:
        for org in orgs:
            with org_ctx(org):
                await pg_db.run(lambda tx, o=org: tx.execute("DELETE FROM graph.edges WHERE org_id = $1", o), write=True)
                await pg_db.run(lambda tx, o=org: tx.execute("DELETE FROM graph.nodes WHERE org_id = $1", o), write=True)


# ---- partial failure, inactive and external orgs, and the guard -------------------------------------------------

async def _small_org(p, org_ctx, org, *, active=True, external=False) -> None:
    with org_ctx(org):
        await p.batch_upsert_nodes([{"_key": org, "name": "Org", "isActive": active, "isExternal": external}], "organizations")
        await p.batch_upsert_nodes([{"_key": k, "orgId": org, "isActive": True} for k in ("c", "s")], "users")
        await p.batch_upsert_nodes([{"_key": f"a{i}", "orgId": org, "type": t} for i, t in enumerate(("Gmail Workspace", "Jira"))], "apps")
        for i in range(2):
            await p.batch_create_edges([{"from_collection": "users", "from_id": "c", "to_collection": "apps", "to_id": f"a{i}",
                                         "sourceUserId": "src-1"}], "userAppRelation")
            await p.upsert_authenticated_as("c", "s", f"a{i}", org)


async def _small_links(p, org_ctx, org) -> list[str]:
    with org_ctx(org):
        return sorted(e.get("connectorId") for e in await p.get_edges_from_node("users/c", "authenticatedAs"))


async def test_pg_inactive_and_external_orgs_are_covered_and_a_failing_org_keeps_the_others_cache_drops(
    pg_provider, org_ctx, pg_db, monkeypatch
) -> None:
    import app.migrations.workspace_admin_link_migration as mig
    from app.core.org_context import current_org_id

    a, inactive, external, bad = (uuid.uuid4().hex[:24] for _ in range(4))
    try:
        await _small_org(pg_provider, org_ctx, a)
        await _small_org(pg_provider, org_ctx, inactive, active=False)
        await _small_org(pg_provider, org_ctx, external, external=True)
        await _small_org(pg_provider, org_ctx, bad)
        real_orgs = pg_provider.get_all_orgs
        mine = {a, inactive, external, bad}

        async def these(**kw):
            return [o for o in await real_orgs(**kw) if (o.get("_key") or o.get("id")) in mine]

        monkeypatch.setattr(pg_provider, "get_all_orgs", these)
        real_q = pg_provider._q

        async def flaky(fn, tx, **kw):
            if current_org_id() == bad and kw.get("write"):
                raise RuntimeError("injected")
            return await real_q(fn, tx, **kw)

        monkeypatch.setattr(pg_provider, "_q", flaky)
        dropped: list[tuple] = []

        async def spy(connector_id, org=None):
            dropped.append((connector_id, org))

        monkeypatch.setattr(mig, "notify_connector_sync_completed", spy)

        result = await WorkspaceAdminLinkMigrationService(pg_provider, _Config(), logging.getLogger("wsleak-it")).migrate()
        monkeypatch.setattr(pg_provider, "_q", real_q)

        assert result["success"] is False and result["failed_orgs"] == [bad] and result["removed"] == 3
        assert await _small_links(pg_provider, org_ctx, a) == ["a1"]
        assert await _small_links(pg_provider, org_ctx, inactive) == ["a1"], "an inactive org is covered"
        assert await _small_links(pg_provider, org_ctx, external) == ["a1"], "an external org is covered"
        assert await _small_links(pg_provider, org_ctx, bad) == ["a0", "a1"], "the failing org keeps its links and is retried at the next start"
        assert sorted(dropped) == sorted([("a0", a), ("a0", inactive), ("a0", external)]), (
            f"the links already removed lost their cache drop: {dropped}"
        )
    finally:
        for org in (a, inactive, external, bad):
            with org_ctx(org):
                await pg_db.run(lambda tx, o=org: tx.execute("DELETE FROM graph.edges WHERE org_id = $1", o), write=True)
                await pg_db.run(lambda tx, o=org: tx.execute("DELETE FROM graph.nodes WHERE org_id = $1", o), write=True)


async def test_pg_the_cross_org_removal_refuses_a_request_bound_to_one_org(pg_provider, org_ctx) -> None:
    from app.core.org_context import SystemContextNotAllowed

    with org_ctx(uuid.uuid4().hex[:24]), pytest.raises(SystemContextNotAllowed):
        await pg_provider.remove_listed_authenticator_links(["Dropbox"])
    with pytest.raises(SystemContextNotAllowed):
        await pg_provider.remove_listed_authenticator_links(["Dropbox"])


async def test_pg_an_unlisted_creator_and_a_relation_of_an_external_collaborator_keep_their_link_and_a_self_link_goes(
    pg_provider, org_ctx, pg_db
) -> None:
    org = uuid.uuid4().hex[:24]
    try:
        with org_ctx(org):
            await pg_provider.batch_upsert_nodes([{"_key": org, "name": "Org", "isActive": True}], "organizations")
            await pg_provider.batch_upsert_nodes([{"_key": k, "orgId": org, "isActive": True} for k in ("c", "s")], "users")
            await pg_provider.batch_upsert_nodes(
                [{"_key": k, "orgId": org, "type": "Gmail Workspace"} for k in ("listed", "unlisted", "external", "selfl")], "apps"
            )
            for k, extra in (("listed", {}), ("external", {"isExternalUser": True})):
                await pg_provider.batch_create_edges([{"from_collection": "users", "from_id": "c", "to_collection": "apps", "to_id": k,
                                                       "sourceUserId": "src-1", **extra}], "userAppRelation")
            for k in ("listed", "unlisted", "external"):
                await pg_provider.upsert_authenticated_as("c", "s", k, org)
            await pg_provider.upsert_authenticated_as("c", "c", "selfl", org)
        from app.core.org_context import system

        with system("wsleak-it"):
            removed, failed = await pg_provider.remove_listed_authenticator_links(["Gmail Workspace"])
        mine = sorted(r["connectorId"] for r in removed if r["org"] == org)
        assert failed == [] or org not in failed
        assert mine == ["listed", "selfl"]
        assert await _small_links(pg_provider, org_ctx, org) == ["external", "unlisted"]
    finally:
        with org_ctx(org):
            await pg_db.run(lambda tx: tx.execute("DELETE FROM graph.edges WHERE org_id = $1", org), write=True)
            await pg_db.run(lambda tx: tx.execute("DELETE FROM graph.nodes WHERE org_id = $1", org), write=True)
