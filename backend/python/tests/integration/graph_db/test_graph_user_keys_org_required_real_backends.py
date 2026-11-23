"""`get_graph_user_keys_by_mongo_user_ids` is always bound to an organisation.

A call without an org used to drop the org filter and resolve a user of any organisation. It now refuses
(ValueError, which every caller already treats as "user not found", so the request fails closed), and an id
that belongs to another org answers as a missing id.
"""

from __future__ import annotations

import logging
import uuid

import pytest

from app.core.org_context import OrgContext, bind, for_org, system
from tests.integration.real_graph import connect_arango, connect_neo4j

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
log = logging.getLogger("graph-user-keys-org")


async def _world(kind, monkeypatch):
    p = await (connect_neo4j(log, monkeypatch) if kind == "neo4j" else connect_arango(log, f"userkeys_{uuid.uuid4().hex[:8]}"))
    org_a, org_b = uuid.uuid4().hex[:24], uuid.uuid4().hex[:24]
    uid_a, uid_b = uuid.uuid4().hex[:24], uuid.uuid4().hex[:24]
    key_a, key_b = str(uuid.uuid4()), str(uuid.uuid4())
    for key, org, uid in ((key_a, org_a, uid_a), (key_b, org_b, uid_b)):
        assert await p.batch_upsert_nodes([{"_key": key, "orgId": org, "userId": uid, "email": f"{uid}@example.com"}], "users")
    return p, (org_a, uid_a, key_a), (org_b, uid_b, key_b)


@pytest.mark.parametrize("kind", ["arango", "neo4j"])
async def test_lookup_is_bound_to_the_org(kind, monkeypatch):
    p, (org_a, uid_a, key_a), (org_b, uid_b, key_b) = await _world(kind, monkeypatch)
    assert await p.get_graph_user_keys_by_mongo_user_ids([uid_a], org_a, chunk_size=10) == {uid_a: key_a}
    with pytest.raises(ValueError, match="Users not found"):  # another org's user answers as a missing one
        await p.get_graph_user_keys_by_mongo_user_ids([uid_b], org_a, chunk_size=10)
    await p.disconnect()


@pytest.mark.parametrize("kind", ["arango", "neo4j"])
@pytest.mark.parametrize("org", [None, ""])
async def test_missing_org_is_refused_not_unscoped(kind, org, monkeypatch):
    p, (_org_a, uid_a, _key_a), (_org_b, uid_b, _key_b) = await _world(kind, monkeypatch)
    with pytest.raises(ValueError, match="org_id is required"):
        await p.get_graph_user_keys_by_mongo_user_ids([uid_a, uid_b], org, chunk_size=10)
    await p.disconnect()


@pytest.mark.parametrize("kind", ["arango", "neo4j"])
async def test_system_scope_lookup_resolves_users_of_every_org_in_one_batch(kind, monkeypatch):
    """The service-account kind backfill holds ids of every org: one batched call under an explicit system scope; an id nobody holds is absent."""
    p, (_org_a, uid_a, key_a), (_org_b, uid_b, key_b) = await _world(kind, monkeypatch)
    nobody = uuid.uuid4().hex[:24]
    with system("test"):
        assert await p.get_graph_user_keys_by_mongo_user_ids_all_orgs([uid_a, uid_b, nobody], chunk_size=10) == {uid_a: key_a, uid_b: key_b}
    await p.disconnect()


@pytest.mark.parametrize("kind", ["arango", "neo4j"])
async def test_system_scope_lookup_refuses_unless_the_scope_is_system(kind, monkeypatch):
    """Not reachable from a request: bound to an org, with no context, or through getattr, it refuses and returns nothing."""
    p, (org_a, uid_a, _key_a), (_org_b, uid_b, _key_b) = await _world(kind, monkeypatch)
    name = "get_graph_user_keys_by_mongo_user_ids" + "_all_orgs"  # a name built at run time, as a text scan would miss
    with pytest.raises(PermissionError, match="explicit system context"):
        await getattr(p, name)([uid_b], chunk_size=10)  # no context at all
    with bind(OrgContext(org_a, "user-a", "user", "jwt", "t")), pytest.raises(PermissionError, match="explicit system context"):
        await getattr(p, name)([uid_b], chunk_size=10)  # org A asking for org B's user
    with for_org(org_a, job="test"), pytest.raises(PermissionError, match="explicit system context"):
        await getattr(p, name)([uid_a], chunk_size=10)
    await p.disconnect()
