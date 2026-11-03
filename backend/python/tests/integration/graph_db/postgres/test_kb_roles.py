"""A collection (a knowledge base) on PostgreSQL: the user's effective role, as ``Neo4jProvider`` and the team-grant rule give it.

A direct grant gives its role. A team the collection is shared with gives each member the role on the team's own edge, else their role in the team,
never more than WRITER; a retired role reads as READER; a role that is none of OWNER, WRITER or READER gives nothing. The highest wins.

Needs a migrated database: PG_CORE_URL (a `pipeshub_connectors_login` DSN), PG_POOLER=none.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from app.core.org_context import MissingOrgContext

from .test_identity import w as _identity_world  # noqa: F401

if TYPE_CHECKING:
    from .test_access import World

pytestmark = pytest.mark.integration


@pytest.fixture
def w(request: pytest.FixtureRequest) -> World:
    return request.getfixturevalue("_identity_world")


async def kb(w: World, **doc: object) -> None:
    await w.nodes("apps", {"_key": "kb1", "type": "KB", "name": "Docs", "createdBy": "me", "createdAtTimestamp": 1, "updatedAtTimestamp": 2, **doc})
    await w.nodes("users", {"_key": "me", "userId": "uid-me"})


async def via_team(w: World, *, grant: str | None, member: str | None, team: str = "tm") -> None:
    await w.nodes("teams", {"_key": team})
    await w.edge("permission", ("users", "me"), ("teams", team), type="USER", **({"role": member} if member else {}))
    await w.edge("permission", ("teams", team), ("apps", "kb1"), type="TEAM", **({"role": grant} if grant is not None else {}))


@pytest.mark.parametrize(("grant", "member", "expected"), [
    ("READER", "OWNER", "READER"),          # the grant's role is what every member gets
    ("WRITER", "READER", "WRITER"),
    ("OWNER", "READER", "WRITER"),          # a team never makes anyone an owner
    (None, "READER", "READER"),             # a grant without a role gives the member's team role
    ("", "WRITER", "WRITER"),
    (None, "OWNER", "WRITER"),              # ... capped
    (None, "COMMENTER", "READER"),          # a retired role reads as READER
    ("ORGANIZER", "OWNER", "READER"),
    (None, "ADMIN", None),                  # not a grant role: nothing
    ("EDITOR", "OWNER", None),
])
async def test_a_team_gives_its_members_the_grants_role_capped_and_never_an_unknown_one(w: World, grant: str | None, member: str, expected: str | None) -> None:
    await kb(w)
    await via_team(w, grant=grant, member=member)
    assert await w.p.get_user_kb_permission("kb1", "me") == expected


async def test_the_highest_of_the_direct_grant_and_the_teams_wins_and_a_retired_direct_role_reads_as_reader(w: World) -> None:
    await kb(w)
    await via_team(w, grant="READER", member="OWNER")
    await w.edge("permission", ("users", "me"), ("apps", "kb1"), type="USER", role="OWNER")
    assert await w.p.get_user_kb_permission("kb1", "me") == "OWNER"
    await w.p.delete_edges_to("kb1", "apps", "permission")
    await w.edge("permission", ("users", "me"), ("apps", "kb1"), type="USER", role="OTHERS")
    assert await w.p.get_user_kb_permission("kb1", "me") == "READER"


async def test_no_role_without_the_user_the_collection_a_user_edge_and_the_org(w: World) -> None:
    await kb(w)
    await w.nodes("apps", {"_key": "plain", "type": "CONNECTOR"})
    await w.edge("permission", ("users", "me"), ("apps", "plain"), type="USER", role="OWNER")          # not a collection
    await w.edge("permission", ("users", "me"), ("apps", "kb1"), type="GROUP", role="OWNER")          # not a user grant
    await w.edge("permission", ("users", "ghost"), ("apps", "kb1"), type="USER", role="OWNER")        # a user that is not there
    p = w.p
    assert await p.get_user_kb_permission("plain", "me") is None and await p.get_user_kb_permission("kb1", "me") is None
    assert await p.get_user_kb_permission("kb1", "ghost") is None and await p.get_user_kb_permission("nope", "me") is None


async def test_a_collection_reads_with_the_users_role_status_and_folders_and_is_updated_in_place(w: World) -> None:
    await kb(w, status="ACTIVE")
    await w.edge("permission", ("users", "me"), ("apps", "kb1"), type="USER", role="WRITER")
    await w.nodes("records", {"_key": "folder", "recordName": "F", "mimeType": "application/vnd.folder", "connectorId": "kb1"},
                  {"_key": "file", "recordName": "x.pdf", "mimeType": "application/pdf", "connectorId": "kb1"})
    for r in ("folder", "file"):
        await w.edge("belongsTo", ("records", r), ("apps", "kb1"))
    got = await w.p.get_knowledge_base("kb1", "me")
    assert (got["name"], got["userRole"], got["status"], got["createdBy"]) == ("Docs", "WRITER", "ACTIVE", "me")
    assert [f["id"] for f in got["folders"]] == ["folder"]
    assert await w.p.get_knowledge_base("kb1", "stranger") is None and await w.p.get_knowledge_base("nope", "me") is None
    await w.p.update_knowledge_base("kb1", {"name": None, "description": "d", "id": "ignored"})
    again = await w.p.get_knowledge_base("kb1", "me")
    assert again["name"] == "Untitled" and again["description"] == "d" and again["id"] == "kb1"            # a null removes the field
    assert await w.p.update_knowledge_base("plain-nope", {"name": "x"}) is None
    assert await w.p._get_kb_context_for_record("file") == {"kb_id": "kb1", "kb_name": None, "org_id": w.org}
    assert await w.p._get_kb_context_for_record("nope") is None


@pytest.mark.parametrize("call", ["get_user_kb_permission", "get_knowledge_base"])
async def test_no_org_is_an_error(pg_provider, call: str) -> None:
    with pytest.raises(MissingOrgContext):
        await getattr(pg_provider, call)("kb1", "me")
