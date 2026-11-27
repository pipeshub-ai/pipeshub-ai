"""A membership whose group no longer exists grants nothing: a source account the user signed in as keeps the grants of its groups only while each group is a node, as
the user's own groups do. Both reads of a viewer's grants follow it: the read of every grant (``get_knowledge_hub_access_v3``) and the access check's read per node."""
from __future__ import annotations

import logging

import pytest

from app.services.graph_db.postgres.provider import PostgresGraphProvider

from .pg_support import new_org
from .test_kg_derivation import ctx, edge

pytestmark = [pytest.mark.integration, pytest.mark.timeout(120)]


def _perm(frm: str, fc: str, to: str, tc: str, t: str = "USER") -> dict:
    return edge(frm, fc, to, tc, type=t, role="READER")


@pytest.mark.parametrize("member", ["groups", "roles", "teams"])
async def test_a_membership_whose_group_no_longer_exists_grants_nothing(pg_db, monkeypatch, member) -> None:
    """u1 signed in to app1 as src. src is a member of g (a node) and of gone (none: its node was deleted). Each grants a record; only g's counts, on either read."""
    p, org = PostgresGraphProvider(logging.getLogger("live-group"), pg_db), new_org()
    monkeypatch.setenv("PG_KG_READ", "0")      # the walk's answer, whatever kg holds
    with ctx(org):
        await p.batch_upsert_nodes([{"_key": "app1", "name": "Drive", "type": "DRIVE", "orgId": org}], "apps")
        await p.batch_upsert_nodes([{"_key": k, "recordName": k, "connectorId": "app1", "orgId": org, "recordType": "FILE", "origin": "CONNECTOR"} for k in ("r1", "r2")], "records")
        await p.batch_upsert_nodes([{"_key": u, "orgId": org, "email": f"{u}@example.com"} for u in ("u1", "src")], "users")
        await p.batch_upsert_nodes([{"_key": "g", "name": "g", "orgId": org, "connectorId": "app1"}], member)
        await p.batch_create_edges([edge("u1", "users", "app1", "apps")], "userAppRelation")
        await p.batch_create_edges([edge("u1", "users", "src", "users", connectorId="app1")], "authenticatedAs")
        await p.batch_create_edges([_perm("src", "users", "g", member), _perm("src", "users", "gone", member),
                                    _perm("g", member, "r1", "records", "GROUP"), _perm("gone", member, "r2", "records", "GROUP")], "permission")
        access = await p.get_knowledge_hub_access_v3("u1", org)
        checked = await p.check_access("u1", org, node_ids={"r1", "r2"})
    assert access["by_connector"]["app1"] == ["r1"]
    assert set(checked.node_ids) == {"r1"}
