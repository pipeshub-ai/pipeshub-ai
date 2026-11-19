"""What the typed graph derives for a user (who they are, and the connectors they are linked into) is what the PostgreSQL walk of the graph answers
(``get_knowledge_hub_access_v3``), on the same world in the same database: well-formed links, and the malformed ones the walk refuses."""
from __future__ import annotations

import pytest

from .pg_support import new_org
from .test_kg_derivation import ctx, edge, held, project, prov

pytestmark = pytest.mark.integration


async def load(p, org: str, other_org: str) -> None:
    with ctx(org):
        await p.batch_upsert_nodes([{"_key": "app1", "name": "Drive", "type": "DRIVE", "orgId": org}, {"_key": "appX", "name": "Foreign", "type": "DRIVE", "orgId": other_org}], "apps")
        await p.batch_upsert_nodes([{"_key": f"u{i}", "orgId": org} for i in range(1, 10)] + [{"_key": k, "orgId": org} for k in ("acct", "acct2", "acctX")], "users")
        await p.batch_upsert_nodes([{"_key": "tm", "orgId": org}], "teams")
        await p.batch_upsert_nodes([{"_key": "g1", "orgId": org}, {"_key": "g2", "orgId": org}], "groups")
        await p.batch_upsert_nodes([{"_key": org, "name": "Org"}], "organizations")
        await p.batch_create_edges([
            # u1: a well-formed link to a source account that is in a badge group, and a membership of a team and of the organisation
            edge("u1", "users", "acct", "users", connectorId="app1"),
            edge("u2", "users", "tm", "teams", connectorId="app1"),                   # a link to a team, not to a user
            edge("u3", "users", "acctX", "users", connectorId="appX"),                # a connector of another organisation
            edge("u4", "users", "ghost", "users", connectorId="app1"),                # a source account that does not exist
            edge("u7", "users", "acct2", "users"),                                    # a link without a connector
            edge("u8", "users", "acct2", "users", connectorId="nope"),                # a connector that does not exist
        ], "authenticatedAs")
        await p.batch_create_edges([
            edge("acct", "users", "g1", "groups", type="USER"),                       # the badge of the account
            edge("acctX", "users", "g2", "groups", type="USER"),
            edge("u1", "users", "tm", "teams", type="USER"),                          # u1 is in the team
            edge("u5", "users", "tm", "teams", type="TEAM"),                          # another type: not a member
            edge("u6", "users", "gone", "teams", type="USER"),                        # a team that does not exist
        ], "permission")
        await p.batch_create_edges([edge("u1", "users", org, "organizations"), edge("u6", "users", "ghost-org", "organizations")], "belongsTo")


USERS = [f"u{i}" for i in range(1, 10)]


async def test_who_a_user_is_and_the_connectors_they_are_linked_into_equal_the_walk(pg_db) -> None:
    p, org, other = prov(pg_db), new_org(), new_org()
    await load(p, org, other)
    await project(p, org, {("apps", "app1"), ("apps", "appX"), *(("users", u) for u in USERS), ("users", "acct"), ("users", "acctX"), ("users", "acct2"), ("teams", "tm"),
                           ("groups", "g1"), ("groups", "g2"), ("organizations", org)})
    closure = (await held(p, org))["closure"]
    for u in USERS:
        with ctx(org):
            walk = await p.get_knowledge_hub_access_v3(u, org)
        mine = {pr for (usr, pr, app), _ in closure.items() if usr == u and app == "-"}
        linked = {app for (usr, _, app), via in closure.items() if usr == u and via == "link"}
        assert mine == set(walk["grantee_ids"]), u
        assert linked == set(walk["gated_app_ids"]), u
    # the one well-formed link reaches its badge group in its connector; none of the malformed ones reaches anything
    assert ("u1", "g1", "app1") in closure and closure[("u1", "g1", "app1")] == "link_badge"
    assert not [k for k in closure if k[2] not in ("-", "app1")] and not [k for k in closure if k[0] in ("u2", "u3", "u4", "u7", "u8") and k[2] != "-"]
