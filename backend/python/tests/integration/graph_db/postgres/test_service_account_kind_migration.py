"""The service-account kind backfill covers every organisation of a PostgreSQL graph, and an organisation never sees another's users.

The staged list holds `userId`s of every organisation, and the migration runs unbound at startup. On PostgreSQL every statement is
filtered on the bound org, so the migration enumerates the organisations itself (system context) and runs one pass per organisation
(`for_org`). Needs a migrated database: PG_CORE_URL, PG_POOLER=none.
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.org_context import OrgContext, bind, system
from app.migrations.service_account_kind_migration import (
    ServiceAccountKindMigrationService,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.integration


@pytest.fixture
async def two_orgs(pg_db, org_ctx, pg_provider) -> AsyncIterator[dict]:
    world: dict = {}
    for tag in ("a", "b"):
        org = uuid.uuid4().hex[:24]
        world[tag] = {"org": org, "svc": f"mongo-svc-{tag}-{org[:6]}", "person": f"mongo-person-{tag}-{org[:6]}"}
        with org_ctx(org):
            assert await pg_provider.batch_upsert_nodes(
                [{"_key": org, "name": org, "accountType": "individual", "isActive": True}], "organizations",
            )
            assert await pg_provider.batch_upsert_nodes([
                {"_key": f"k-{world[tag]['svc']}", "orgId": org, "userId": world[tag]["svc"], "email": f"{tag}-svc@example.com", "isActive": True},
                {"_key": f"k-{world[tag]['person']}", "orgId": org, "userId": world[tag]["person"], "email": f"{tag}-p@example.com", "isActive": True},
            ], "users")
    yield world
    for entry in world.values():
        with org_ctx(entry["org"]):
            await pg_db.run(lambda tx, o=entry["org"]: tx.execute("DELETE FROM graph.nodes WHERE org_id = $1", o), write=True)


async def test_both_orgs_are_backfilled_and_neither_sees_the_other(pg_provider, two_orgs, org_ctx) -> None:
    a, b = two_orgs["a"], two_orgs["b"]
    stale = f"mongo-nobody-{uuid.uuid4().hex[:6]}"
    state = {"version": 1, "userIds": [a["svc"], b["svc"], stale], "disabledUserIds": [b["svc"]]}
    config = MagicMock()
    config.get_config = AsyncMock(return_value=state)
    config.set_config = AsyncMock()

    # Unbound, as at startup: the migration binds each org itself.
    result = await ServiceAccountKindMigrationService(pg_provider, config, logging.getLogger("kind-migration-it")).migrate()

    assert result["success"] is True
    assert result["stamped"] == 2 and result["missing"] == 1  # the id no org holds is a straggler, not an error
    for mine, other in ((a, b), (b, a)):
        with org_ctx(mine["org"]):
            svc = await pg_provider.get_document(f"k-{mine['svc']}", "users")
            person = await pg_provider.get_document(f"k-{mine['person']}", "users")
            assert svc["kind"] == "service" and svc["isDisabled"] is (mine is b)
            assert person.get("kind") != "service"  # an account that was not staged is left alone
            assert await pg_provider.get_document(f"k-{other['svc']}", "users") is None  # the other org's user is not visible here
            assert await pg_provider.get_user_by_user_id(other["svc"]) is None


async def test_the_system_scope_lookup_joins_the_active_orgs_and_refuses_outside_a_system_scope(pg_provider, two_orgs, org_ctx) -> None:
    a, b = two_orgs["a"], two_orgs["b"]
    name = "get_graph_user_keys_by_mongo_user_ids" + "_all_orgs"
    nobody = f"mongo-nobody-{uuid.uuid4().hex[:6]}"
    with system("test"):
        found = await getattr(pg_provider, name)([a["svc"], b["svc"], nobody], chunk_size=10)
    assert found == {a["svc"]: f"k-{a['svc']}", b["svc"]: f"k-{b['svc']}"}  # one answer, whatever org holds the user; the stranger is absent
    with pytest.raises(PermissionError, match="explicit system context"):  # no context
        await getattr(pg_provider, name)([a["svc"]], chunk_size=10)
    with bind(OrgContext(b["org"], None, "job", "job:test", "t")), pytest.raises(PermissionError, match="explicit system context"):
        await getattr(pg_provider, name)([a["svc"]], chunk_size=10)  # bound to org B asking for org A's user


async def test_a_deleted_org_is_skipped_and_no_lookup_is_per_id(pg_provider, pg_db, two_orgs, org_ctx) -> None:
    a, b = two_orgs["a"], two_orgs["b"]
    with org_ctx(b["org"]):  # org B is deleted: its organisation node is inactive
        assert await pg_provider.batch_upsert_nodes([{"_key": b["org"], "isActive": False}], "organizations")
    config = MagicMock()
    config.get_config = AsyncMock(return_value={"version": 1, "userIds": [a["svc"], b["svc"]], "disabledUserIds": []})
    config.set_config = AsyncMock()
    spy = AsyncMock(wraps=pg_provider.get_user_by_user_id)
    pg_provider.get_user_by_user_id = spy  # type: ignore[method-assign]
    try:
        result = await ServiceAccountKindMigrationService(pg_provider, config, logging.getLogger("kind-migration-it")).migrate()
    finally:
        del pg_provider.get_user_by_user_id
    assert result["success"] is True and result["stamped"] == 1 and result["missing"] == 1  # the deleted org's account is a straggler, left alone
    spy.assert_not_called()  # batched per org, never one lookup per id
    with org_ctx(b["org"]):
        assert (await pg_provider.get_document(f"k-{b['svc']}", "users")).get("kind") != "service"
