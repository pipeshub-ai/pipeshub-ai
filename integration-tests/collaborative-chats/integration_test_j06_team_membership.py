"""Journey J-06: team share and membership change.

Given a chat is shared with a team
When a member is added or removed through the Node Teams route, or outside Node
Then access changes on the next request (teamsVersion bump) through Node, and within 60 s otherwise

Owning phase: PH-06 (80-implementation-plan section 5); TM-02..04, LC-12..17, 00-README 2026-10-02 (60 s bound).
Teams live in the lane's connectors fake. "Outside Node" is a change to the fake alone.

The 60 s bound is the team-ids cache (30 s) plus the decision cache (30 s). The default run asserts the cache
state (both entries exist, each TTL <= 30 s, the stale answer is served while they live, and the answer flips
once both are gone) instead of waiting. ``PCC_E2E_REAL_STALENESS_WAIT=1`` polls in real time for the flip.
"""

from __future__ import annotations

import os
import time

import pytest

from helper.collab_stack import chats, collab
from helper.collab_stack.seeds import messages_of, session_doc, team_row

pytestmark = [pytest.mark.integration, pytest.mark.collab_chats, pytest.mark.collab_stack, pytest.mark.usefixtures("flag_on_for_module")]

NOT_FOUND = (404, "CONVERSATION_NOT_FOUND")
READ_ONLY = (403, "CONVERSATION_READ_ONLY")
REAL_WAIT = os.environ.get("PCC_E2E_REAL_STALENESS_WAIT") == "1"
BOUND_S = 60
CACHE_TTL_S = 30


class Setup:
    def __init__(self, stack, api, fake, roster) -> None:  # noqa: ANN001
        self.stack, self.api, self.fake, self.roster = stack, api, fake, roster
        self.owner = collab.fresh_actor(stack, "J6")
        org = self.owner.org_id
        self.writers = fake.add_team(org, "writers", {self.owner.user_id: "OWNER", roster.team_writer.user_id: "WRITER"})
        self.readers = fake.add_team(org, "readers", {self.owner.user_id: "OWNER", roster.team_reader.user_id: "READER"})
        self.chat = chats.create_chat(api, self.owner, "A question")
        collab.ok(
            collab.put(api, self.owner, self.chat, collab.team(self.writers.team_id, "write"), collab.team(self.readers.team_id, "read")),
            "share with the teams",
        )


@pytest.fixture
def setup(stack, api, fake, roster) -> Setup:  # noqa: ANN001
    return Setup(stack, api, fake, roster)


def keys(stack, pattern: str) -> list[str]:  # noqa: ANN001
    return list(stack.infra.redis.scan_iter(match=pattern, count=500))


def test_j06_team_members_read_and_send_per_level(setup: Setup) -> None:
    api, roster, chat = setup.api, setup.roster, setup.chat
    writer, reader, stranger = roster.team_writer, roster.team_reader, roster.stranger
    assert chats.get_chat(api, writer, chat).status_code == 200
    assert chats.send_message(api, writer, chat, "from the writing team").status_code == 200
    assert chats.get_chat(api, reader, chat).status_code == 200
    assert chats.error_of(chats.send_message(api, reader, chat, "from the reading team")) == READ_ONLY
    assert chats.error_of(chats.get_chat(api, stranger, chat)) == NOT_FOUND
    rows = messages_of(setup.stack.db, chat)
    assert [r["content"] for r in rows if r["messageType"] == "user_query"] == ["A question", "from the writing team"]
    assert str(rows[-2]["authorUserId"]) == writer.user_id
    stored = session_doc(setup.stack.db, chat)["sharedWith"]
    assert {(r["teamId"], r["accessLevel"]) for r in stored} == {(setup.writers.team_id, "write"), (setup.readers.team_id, "read")}


def test_j06_removing_a_member_through_the_node_teams_route_denies_the_next_request(setup: Setup) -> None:
    """No stale window: the Teams route bumps `teamsVersion`, so neither the cached team ids nor the cached decision is read."""
    api, stack, fake, chat, member = setup.api, setup.stack, setup.fake, setup.chat, setup.roster.team_writer
    assert chats.send_message(api, member, chat, "before").status_code == 200
    assert chats.get_chat(api, member, chat).status_code == 200  # decision cached
    version_keys = keys(stack, f"*teamids:ver:{setup.owner.org_id}")
    version_before = int(stack.infra.redis.get(version_keys[0])) if version_keys else 0
    lookups = len(fake.requests_for("team_ids"))

    update = api.put(f"/api/v1/teams/{setup.writers.team_id}", setup.owner, json_body={"removeUserIds": [member.user_id]})
    assert update.status_code == 200, update.text[:300]

    assert chats.error_of(chats.send_message(api, member, chat, "after")) == NOT_FOUND
    assert chats.error_of(collab.feed(api, member, chat)) == NOT_FOUND
    assert len(fake.requests_for("team_ids")) > lookups, "the member's teams were resolved again, not served from cache"
    bumped = keys(stack, f"*teamids:ver:{setup.owner.org_id}")
    assert bumped and int(stack.infra.redis.get(bumped[0])) == version_before + 1


def test_j06_adding_a_member_through_the_node_teams_route_grants_the_next_request(setup: Setup) -> None:
    api, newcomer = setup.api, setup.roster.stranger
    assert chats.error_of(chats.get_chat(api, newcomer, setup.chat)) == NOT_FOUND
    assert chats.error_of(chats.get_chat(api, newcomer, setup.chat)) == NOT_FOUND  # a cached deny must not outlive the bump
    update = api.put(f"/api/v1/teams/{setup.readers.team_id}", setup.owner, json_body={"addUserRoles": [{"userId": newcomer.user_id, "role": "READER"}]})
    assert update.status_code == 200, update.text[:300]
    assert chats.get_chat(api, newcomer, setup.chat).status_code == 200
    assert chats.error_of(chats.send_message(api, newcomer, setup.chat, "read-only team")) == READ_ONLY


def test_j06_a_change_outside_node_is_served_from_cache_then_denied_within_the_bound(setup: Setup) -> None:
    api, stack, fake, chat, member = setup.api, setup.stack, setup.fake, setup.chat, setup.roster.team_reader
    org, redis = setup.owner.org_id, stack.infra.redis
    assert chats.get_chat(api, member, chat).status_code == 200
    started = time.monotonic()
    fake.teams[setup.readers.team_id].members.pop(member.user_id)  # connectors-side change, Node is not told

    assert chats.get_chat(api, member, chat).status_code == 200, "within the bound the cached answer is served"
    team_keys = keys(stack, f"*teamids:v1:{org}:{member.user_id}:*")
    decision_keys = keys(stack, f"*authz:v1:{org}:*{member.user_id}*")
    assert team_keys and decision_keys, "both caches hold an entry for the member"
    for key in team_keys + decision_keys:
        ttl = redis.ttl(key)
        assert 0 < ttl <= CACHE_TTL_S, f"{key} lives at most {CACHE_TTL_S} s (ttl={ttl})"
    assert 2 * CACHE_TTL_S <= BOUND_S

    if REAL_WAIT:
        deadline = started + BOUND_S + 5
        while time.monotonic() < deadline:
            if chats.get_chat(api, member, chat).status_code == 404:
                break
            time.sleep(2)
        assert chats.error_of(chats.get_chat(api, member, chat)) == NOT_FOUND
        assert time.monotonic() - started <= BOUND_S + 5
        return

    # Expire the decision entry first: the answer is recomputed from the still-cached team ids, so it is still stale.
    redis.delete(*decision_keys)
    assert chats.get_chat(api, member, chat).status_code == 200, "the team-ids cache alone keeps the stale answer"
    # Expire both layers, as 30 s + 30 s of TTL would: the next request resolves the teams again and is denied.
    redis.delete(*keys(stack, f"*teamids:v1:{org}:{member.user_id}:*"), *keys(stack, f"*authz:v1:{org}:*{member.user_id}*"))
    assert chats.error_of(chats.get_chat(api, member, chat)) == NOT_FOUND


def test_j06_deleting_the_team_removes_its_rows(setup: Setup) -> None:
    api, stack, member = setup.api, setup.stack, setup.roster.team_writer
    assert chats.send_message(api, member, setup.chat, "before").status_code == 200
    gone = api.delete(f"/api/v1/teams/{setup.writers.team_id}", setup.owner)
    assert gone.status_code == 200, gone.text[:300]

    doc = session_doc(stack.db, setup.chat)
    assert [r["teamId"] for r in doc["sharedWith"]] == [setup.readers.team_id], "the deleted team's row is pulled, the other stays"
    assert chats.error_of(chats.get_chat(api, member, setup.chat)) == NOT_FOUND
    view = collab.ok(collab.list_(api, setup.owner, setup.chat), "list")
    assert [c["principalId"] for c in view["collaborators"]] == [setup.readers.team_id] and view["collaboratorCount"] == 1
    assert chats.get_chat(api, setup.roster.team_reader, setup.chat).status_code == 200

    # Deleting the last team clears isShared.
    assert api.delete(f"/api/v1/teams/{setup.readers.team_id}", setup.owner).status_code == 200
    final = session_doc(stack.db, setup.chat)
    assert final["sharedWith"] == [] and final["isShared"] is False


def test_j06_a_dangling_team_row_shows_as_deleted_grants_nobody_and_can_be_removed(setup: Setup) -> None:
    """LC-13: a row whose team no longer exists (cleanup missed it) lists as a deleted team; DELETE needs no existence check."""
    api, stack = setup.api, setup.stack
    ghost = "3c1a9a0e-6f55-4d2f-8c3c-7a41c0a5d9aa"
    stack.db["chatSessions"].update_one({"_id": session_doc(stack.db, setup.chat)["_id"]}, {"$push": {"sharedWith": team_row(ghost, "write")}})
    view = collab.ok(collab.list_(api, setup.owner, setup.chat), "list")
    row = next(c for c in view["collaborators"] if c["principalId"] == ghost)
    assert (row["state"], row["displayName"]) == ("deleted_team", "Deleted team")
    assert chats.error_of(chats.get_chat(api, setup.roster.stranger, setup.chat)) == NOT_FOUND
    after = collab.ok(collab.remove(api, setup.owner, setup.chat, ghost, "team"), "remove the ghost")
    assert ghost not in [c["principalId"] for c in after["collaborators"]]
