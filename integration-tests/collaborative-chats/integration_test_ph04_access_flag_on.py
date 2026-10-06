"""PH-04 on the real stack with the collaboration flag ON: guards, lists, caches.

Given a chat shared directly, through teams and through a project, and a flag that is on
When every actor calls the chat and agent routes, the list surfaces, and the routes that change access
Then each gets the status and code the 51 section 2 table gives, denials are JSON before any SSE byte, lists agree
with detail, no collaborator ids leak to non-owners, and access changes take effect on the next request

Owning phase: PH-04 (80-implementation-plan section 5); PH04-01 to PH04-16, N-ACL, SEC-14, TM-02/03, D11.
Flag-off behaviour is J-02 (`integration_test_j02_flag_off_parity.py`).
"""

from __future__ import annotations

import json
import time

import pytest

from helper.collab_stack import chats, parity
from helper.collab_stack.fake_backend import FakeBackend
from helper.collab_stack.identity import Directory
from helper.collab_stack.node_api import DB_NAME
from helper.collab_stack.parity import CALLS, Call
from helper.collab_stack.seeds import insert_session, session_doc, user_row
from helper.collab_stack.stack import CollabStack
from helper.collab_stack.worlds import SHARED_ROLES, CollabWorld, seed_collab_world

pytestmark = [pytest.mark.integration, pytest.mark.collab_chats, pytest.mark.collab_stack, pytest.mark.usefixtures("flag_on_for_module")]

NOT_FOUND = "CONVERSATION_NOT_FOUND"
READ_ONLY = "CONVERSATION_READ_ONLY"
OWNER_ONLY = "CONVERSATION_OWNER_ONLY"
NOT_ASKER = "REGENERATE_NOT_ALLOWED"
PROJECT_REQUIRED = "PROJECT_ACCESS_REQUIRED"
# The shared chat lives in project PRJ; these write collaborators are not members of it (D7: the sender needs the project too).
NOT_IN_PROJECT = frozenset({"Writer", "TeamWriter"})
RANK = {"none": 0, "read": 1, "write": 2, "owner": 3}

# PH-04 section 3: the operation each route is guarded with, and the least role it needs.
OPERATIONS: dict[str, tuple[str, str]] = {
    "read": ("read", OWNER_ONLY),
    "feedback": ("read", OWNER_ONLY),
    "archiveSelf": ("read", OWNER_ONLY),
    "send": ("write", READ_ONLY),
    "cancel": ("write", READ_ONLY),
    "regenerate": ("write", READ_ONLY),
    "manageCollaborators": ("owner", OWNER_ONLY),
    "rename": ("owner", OWNER_ONLY),
    "linkProject": ("owner", OWNER_ONLY),
    "delete": ("owner", OWNER_ONLY),
}
ROUTE_OPERATION = {
    "C1": "send", "C2": "send", "C3": "send", "C4": "send", "C5": "read", "C6": "delete", "C7": "manageCollaborators",
    "C8": "manageCollaborators", "C9": "linkProject", "C10": "linkProject", "C11": "regenerate", "C12": "cancel",
    "C13": "rename", "C14": "feedback", "C15": "archiveSelf", "C16": "archiveSelf",
    "A1": "send", "A2": "send", "A3": "send", "A4": "regenerate", "A5": "cancel", "A6": "feedback", "A7": "read",
    "A8": "delete", "A9": "rename", "A10": "linkProject", "A11": "linkProject", "A12": "archiveSelf", "A13": "archiveSelf",
}  # fmt: skip
ROUTES = [c for c in CALLS if c.id in ROUTE_OPERATION]
ACTORS = tuple(SHARED_ROLES)
NOTHING_TO_UNARCHIVE = ("C16", "A13")  # everyone with read access passes the guard (archive is per user since PH-05); the chat is not archived for them, so the handler answers 400


def expected_denial(call_id: str, role: str, actor: str = "") -> tuple[int, str] | None:
    """The (status, code) the 51 section 2 table gives, or None when the actor may proceed."""
    minimum, below = OPERATIONS[ROUTE_OPERATION[call_id]]
    if role == "none":
        return 404, NOT_FOUND
    if RANK[role] < RANK[minimum]:
        return 403, below
    if ROUTE_OPERATION[call_id] == "send" and actor in NOT_IN_PROJECT:
        return 403, PROJECT_REQUIRED  # PH-05 runLease(): a send needs the project access of the sender
    if ROUTE_OPERATION[call_id] == "regenerate" and role != "owner":
        return 403, NOT_ASKER  # the question being regenerated was asked by the owner (legacy rows: author = owner)
    return None


def error_of(status: int, text: str) -> tuple[int, str | None]:
    try:
        return status, json.loads(text)["error"]["code"]
    except (ValueError, KeyError, TypeError):
        return status, None


@pytest.fixture
def world(stack: CollabStack) -> CollabWorld:
    return seed_collab_world(stack)


def play(stack: CollabStack, world: CollabWorld, call: Call, actor: str):  # noqa: ANN201
    return parity.request(stack, world, call, world.actors[actor])


# ---- N-ACL: every route x every actor ----------------------------------------------------------


@pytest.mark.parametrize("call", ROUTES, ids=lambda c: c.id)
def test_ph04_route_decides_by_role(stack: CollabStack, call: Call) -> None:
    """The guard's decision per route, per actor: 404 none, 403 READ_ONLY / OWNER_ONLY / REGENERATE_NOT_ALLOWED, else through."""
    failures: list[str] = []
    for actor in ACTORS:
        stack.reset_state()
        world = seed_collab_world(stack)
        status, headers, text = play(stack, world, call, actor)
        denial = expected_denial(call.id, SHARED_ROLES[actor], actor)
        got = error_of(status, text)
        if denial is not None:
            if got != denial:
                failures.append(f"{actor}: expected {denial}, got {got}")
            if "text/event-stream" in headers.get("Content-Type", headers.get("content-type", "")) or text.lstrip().startswith("event:"):
                failures.append(f"{actor}: denial was written as an event stream")
            ai_calls = stack.fake.requests_for("chat", "agent_chat", "chat_stream", "agent_chat_stream", "chat_cancel")
            if ai_calls:
                failures.append(f"{actor}: a denied request reached the AI backend")
        elif status >= 400 and call.id not in NOTHING_TO_UNARCHIVE:
            failures.append(f"{actor}: should pass the guard and succeed, got {got}")
    assert not failures, f"{call.id} ({ROUTE_OPERATION[call.id]}): " + "; ".join(failures)


# ---- the guard's decisions are visible to the client -------------------------------------------


@pytest.mark.parametrize("call_id", ["C3", "C4", "C11", "A2", "A3", "A4"])
def test_ph04_denied_stream_is_json_before_any_sse_byte(stack: CollabStack, call_id: str) -> None:
    """DV-1: Reader (read) and Stranger (none) are refused with a JSON error; no `text/event-stream`, no event bytes, no AI call."""
    call = next(c for c in CALLS if c.id == call_id)
    for actor, denial in (("Reader", (403, READ_ONLY)), ("Stranger", (404, NOT_FOUND))):
        stack.reset_state()
        world = seed_collab_world(stack)
        status, headers, text = play(stack, world, call, actor)
        content_type = headers.get("Content-Type", headers.get("content-type", ""))
        assert error_of(status, text) == denial, f"{call_id}/{actor}: {status} {text[:200]}"
        assert content_type.startswith("application/json") and "event:" not in text
        assert not stack.fake.requests_for("chat", "chat_stream", "agent_chat", "agent_chat_stream")


def test_ph04_allowed_stream_reaches_the_ai_backend_as_the_sender(stack: CollabStack, api, world: CollabWorld) -> None:
    """A write recipient who also has the project is accepted and the AI backend sees the sender's identity, not the owner's."""
    writer, owner = world.actors["Writer"], world.actors["Owner"]
    path = f"/api/v1/conversations/{world.ids['chat']}/messages/stream"
    body = {"query": "go on", "chatMode": "internal_search"}
    # D7: the chat is in a project the writer cannot open, so the write role alone is not enough.
    refused = api.stream(path, writer, json_body=body).finish()
    assert error_of(refused.status, refused.text) == (403, PROJECT_REQUIRED)
    assert not stack.fake.requests_for("chat_stream")
    assert chats.add_project_member(api, owner, world.ids["project"], writer.user_id).status_code == 200
    call = api.stream(path, writer, json_body=body).finish()
    assert call.status == 200 and call.result, call.text[:300]
    sent = stack.fake.requests_for("chat_stream")[0]
    assert sent.user_id == writer.user_id and sent.body["conversationId"] == world.ids["chat"]


@pytest.mark.parametrize(
    ("actor", "role", "can_send", "can_manage"),
    [("Owner", "owner", True, True), ("Writer", "write", True, False), ("Reader", "read", False, False), ("TeamWriter", "write", True, False), ("ProjectViewer", "read", False, False)],
)
def test_ph04_detail_carries_the_access_view(api, world: CollabWorld, actor: str, role: str, can_send: bool, can_manage: bool) -> None:
    """PH04-13: with the flag on `access` is the PDP view; a write recipient reads `accessLevel: write` (it was always `read`)."""
    resp = chats.get_chat(api, world.actors[actor], world.ids["chat"])
    assert resp.status_code == 200, resp.text[:300]
    access = resp.json()["conversation"]["access"]
    assert access["role"] == role and access["isOwner"] == (role == "owner")
    assert (access["canSend"], access["canManage"]) == (can_send, can_manage)
    assert access["accessLevel"] == ("write" if role in ("owner", "write") else "read") or role == "owner"
    assert access["isCollaborative"] is True


def test_ph04_project_editor_writes_where_the_project_ceiling_is_editor(stack: CollabStack, api, world: CollabWorld) -> None:
    """H2: a project editor's role in a project-visible chat is capped by the project's chat ceiling (viewer by default)."""
    editor = world.actors["ProjectEditor"]
    under_viewer_ceiling = chats.send_message(api, editor, world.ids["chat"])
    assert error_of(under_viewer_ceiling.status_code, under_viewer_ceiling.text) == (403, READ_ONLY)
    under_editor_ceiling = chats.send_message(api, editor, world.sessions["peditor"].sid)
    assert under_editor_ceiling.status_code == 200, under_editor_ceiling.text[:300]
    viewer = chats.send_message(api, world.actors["ProjectViewer"], world.sessions["peditor"].sid)
    assert error_of(viewer.status_code, viewer.text) == (403, READ_ONLY)


def test_ph04_bare_is_shared_does_not_make_a_chat_visible(stack: CollabStack, api, world: CollabWorld) -> None:
    """SEC-03: `isShared: true` with no recipients is not access."""
    bare = insert_session(stack.db, "bare", world.actors["Owner"], is_shared=True, shared_with=[])
    stranger = world.actors["Stranger"]
    assert error_of(*_sc(chats.get_chat(api, stranger, bare.sid))) == (404, NOT_FOUND)
    regen = chats.regenerate(api, stranger, bare.sid, bare.answer_id)
    assert error_of(*_sc(regen)) == (404, NOT_FOUND)
    assert not stack.fake.requests_for("chat", "chat_stream")


def _sc(resp):  # noqa: ANN001, ANN202
    return resp.status_code, resp.text


# ---- lists agree with detail -------------------------------------------------------------------


def ids_of(resp, key: str = "conversations") -> set[str]:  # noqa: ANN001
    assert resp.status_code == 200, resp.text[:300]
    return {c.get("id") or c.get("_id") for c in resp.json()[key]}


@pytest.mark.parametrize("actor", [a for a in ACTORS if a != "Owner"])
def test_ph04_shared_list_equals_what_detail_opens(stack: CollabStack, api, world: CollabWorld, actor: str) -> None:
    """For each actor, `?source=shared` lists exactly the chats whose detail they can open (list and detail share one policy)."""
    who = world.actors[actor]
    chats_of_owner = {k: v for k, v in world.sessions.items() if k in ("shared", "peditor", "team-only", "private")}
    openable = {s.sid for s in chats_of_owner.values() if api.get(f"/api/v1/conversations/{s.sid}", who).status_code == 200}
    listed = ids_of(api.get("/api/v1/conversations", who, params={"source": "shared"}))
    assert listed == openable, f"{actor}: listed {sorted(listed)}, can open {sorted(openable)}"
    if SHARED_ROLES[actor] == "none":
        assert listed == set()


def test_ph04_project_team_member_lists_and_opens_a_project_visible_chat(api, world: CollabWorld) -> None:
    """A member through a project team row sees the project, its chats in the lists, and can open one by id (list/detail consistency)."""
    member = world.actors["ProjectTeamMember"]
    chat = world.sessions["shared"].sid
    assert api.get(f"/api/v1/conversations/{chat}", member).status_code == 200
    assert chat in ids_of(api.get("/api/v1/conversations", member, params={"source": "shared"}))
    assert world.ids["project"] in chats.project_ids(api.get("/api/v1/projects", member, params={"scope": "shared"}))
    in_project = api.get(f"/api/v1/projects/{world.ids['project']}/conversations", member)
    assert chat in ids_of(in_project)
    agent = world.sessions["agent"].sid
    assert api.get(f"/api/v1/agents/agent-1/conversations/{agent}", member).status_code == 200
    assert agent in ids_of(api.get("/api/v1/agents/agent-1/conversations", member, params={"source": "shared"}))


def test_ph04_team_row_member_lists_the_team_shared_chat(api, world: CollabWorld) -> None:
    team_reader = world.actors["TeamReader"]
    assert world.sessions["team-only"].sid in ids_of(api.get("/api/v1/conversations", team_reader, params={"source": "shared"}))
    assert world.actors["TeamWriter"] and world.sessions["team-only"].sid not in ids_of(api.get("/api/v1/conversations", world.actors["TeamWriter"], params={"source": "shared"}))


# ---- SEC-14: collaborator ids are for the owner ------------------------------------------------


def other_people(world: CollabWorld, me: str) -> set[str]:
    """Ids (users and teams) that a non-owner must not learn from a chat they can open."""
    users = {world.actors[n].user_id for n in ("Writer", "Reader", "TeamWriter", "TeamReader", "ProjectViewer", "ProjectEditor", "ProjectTeamMember") if n != me}
    return users | set(world.teams.values())


@pytest.mark.parametrize("actor", ["Reader", "Writer", "TeamReader", "ProjectViewer"])
def test_ph04_non_owner_never_sees_other_collaborators(stack: CollabStack, api, world: CollabWorld, actor: str) -> None:
    """SEC-14: detail, archives, archive search, the agent list and the project conversation list carry no other collaborator ids."""
    who = world.actors[actor]
    # Archived twins of the shared chats, so the archive surfaces have something to list (an archived chat does not open by id).
    rows = session_doc(stack.db, world.sessions["shared"].id)["sharedWith"]
    owner = world.actors["Owner"]
    insert_session(stack.db, "shared-archived", owner, shared_with=rows, isArchived=True, project=world.projects["PRJ"], project_visibility="project")
    insert_session(stack.db, "agent-archived", owner, kind="agent", shared_with=rows, isArchived=True, project=world.projects["PRJ"], project_visibility="project")
    responses = {
        "detail": api.get(f"/api/v1/conversations/{world.ids['chat']}", who),
        "list shared": api.get("/api/v1/conversations", who, params={"source": "shared"}),
        "archives": api.get("/api/v1/conversations/show/archives", who),
        "archive search": api.get("/api/v1/conversations/show/archives/search", who, params={"search": "shared"}),
        "agent list": api.get("/api/v1/agents/agent-1/conversations", who, params={"source": "shared"}),
        "agent detail": api.get(f"/api/v1/agents/agent-1/conversations/{world.ids['agent']}", who),
        "project conversations": api.get(f"/api/v1/projects/{world.ids['project']}/conversations", who),
    }
    leaked: list[str] = []
    for name, resp in responses.items():
        if resp.status_code != 200:
            continue
        body = resp.text
        leaked += [f"{name}: {i}" for i in other_people(world, actor) if i in body]
    assert not leaked, f"{actor} learned: {leaked}"
    assert [n for n, r in responses.items() if r.status_code == 200] != [], "no surface answered, so nothing was checked"
    assert responses["detail"].status_code == 200
    assert responses["detail"].json()["conversation"].get("sharedWith") in (None, [])


def test_ph04_owner_still_sees_the_collaborators(api, world: CollabWorld) -> None:
    body = chats.get_chat(api, world.actors["Owner"], world.ids["chat"]).text
    assert world.actors["Reader"].user_id in body and world.teams["T-read"] in body


# ---- team directory outages --------------------------------------------------------------------


def test_ph04_team_outage_is_a_503_only_where_a_team_row_could_decide(stack: CollabStack, api, fake: FakeBackend, world: CollabWorld, flags) -> None:  # noqa: ANN001
    """TM-04: a failing team lookup gives 503 TEAM_RESOLUTION_UNAVAILABLE for a team-only path, never for the owner or a direct row; flag off it is a 404."""
    team_reader, reader, owner = world.actors["TeamReader"], world.actors["Reader"], world.actors["Owner"]
    team_chat = world.sessions["team-only"].sid
    with fake.failing("team_ids", status=500):
        denied = chats.get_chat(api, team_reader, team_chat)
        assert error_of(*_sc(denied)) == (503, "TEAM_RESOLUTION_UNAVAILABLE")
        assert chats.get_chat(api, reader, world.ids["chat"]).status_code == 200, "a direct row must not need the team directory"
        assert chats.get_chat(api, owner, team_chat).status_code == 200
        assert not _body_has_leak(denied.text, world)
    with flags.value(False):
        with fake.failing("team_ids", status=500):
            off = chats.get_chat(api, team_reader, team_chat)
        assert error_of(*_sc(off)) == (404, NOT_FOUND)


def _body_has_leak(text: str, world: CollabWorld) -> bool:
    return any(i in text for i in other_people(world, "TeamReader"))


# ---- D11: an inactive owner ---------------------------------------------------------------------


def fresh_owner_chat(stack: CollabStack, world: CollabWorld, label: str, *, disabled: bool = False):  # noqa: ANN201
    """A new owner (the API remembers an owner's status for 60 s, so a reused one would hide a change) and a chat shared with the writer."""
    owner = stack.directory.user(f"{label}{time.time_ns() % 10**9}")  # type: ignore[union-attr]
    chat = insert_session(stack.db, f"{label}-{owner.name}", owner, shared_with=[user_row(world.actors["Writer"], "write", principal_type=True)])
    if disabled:
        stack.directory.set_disabled(owner, True)  # type: ignore[union-attr]
    return owner, chat


def test_ph04_disabled_owner_blocks_editors_from_sending_but_not_from_reading(stack: CollabStack, api, world: CollabWorld) -> None:
    """D11: send and regenerate need an active owner (403 OWNER_INACTIVE); reads do not."""
    writer = world.actors["Writer"]
    _, active_chat = fresh_owner_chat(stack, world, "Active")
    _, gone_chat = fresh_owner_chat(stack, world, "Gone", disabled=True)

    assert chats.send_message(api, writer, active_chat.sid).status_code == 200, "an active owner does not block the editor"
    blocked = chats.send_message(api, writer, gone_chat.sid)
    assert error_of(*_sc(blocked)) == (403, "OWNER_INACTIVE"), blocked.text[:300]
    assert chats.get_chat(api, writer, gone_chat.sid).status_code == 200
    assert error_of(*_sc(chats.regenerate(api, writer, gone_chat.sid, gone_chat.answer_id)))[0] == 403
    assert [r.body["conversationId"] for r in stack.fake.requests_for("chat")] == [active_chat.sid]


def test_ph04_owner_directory_outage_fails_closed_with_503(stack: CollabStack, api, world: CollabWorld) -> None:
    """D11 fail-closed: the owner's status cannot be read -> 503 OWNER_STATUS_UNAVAILABLE for editors; reads are unaffected."""
    writer = world.actors["Writer"]
    _, chat = fresh_owner_chat(stack, world, "Silent")
    # Mongo serves the writer's session check (the first `users` find) and then fails the guard's read of the owner.
    with stack.infra.failing_finds(DB_NAME, "users", skip=1):
        outcome = chats.send_message(api, writer, chat.sid)
    assert error_of(*_sc(outcome)) == (503, "OWNER_STATUS_UNAVAILABLE"), outcome.text[:300]
    assert chats.get_chat(api, writer, chat.sid).status_code == 200
    assert not [r for r in stack.fake.requests_for("chat") if r.body["conversationId"] == chat.sid]


# ---- internal routes ----------------------------------------------------------------------------


def test_ph04_internal_routes_hydrate_the_user_before_the_guard(stack: CollabStack, api, world: CollabWorld) -> None:
    """SEC-18 / PH04-09: a disabled user's scoped token is refused before the guard; the owner's token passes it and the turn runs."""
    chat = world.ids["chat"]
    disabled = api.post(f"/api/v1/conversations/internal/{chat}/messages", token=Directory.scoped_token(world.actors["Disabled"]), json_body={})
    assert disabled.status_code == 401
    owner = api.post(f"/api/v1/conversations/internal/{chat}/messages", token=Directory.scoped_token(world.actors["Owner"]), json_body={"query": "hi"})
    assert owner.status_code == 200, owner.text[:300]
    reader = api.post(f"/api/v1/conversations/internal/{chat}/messages", token=Directory.scoped_token(world.actors["Reader"]), json_body={"query": "hi"})
    assert error_of(*_sc(reader)) == (403, READ_ONLY)
    stranger = api.post(f"/api/v1/conversations/internal/{chat}/messages", token=Directory.scoped_token(world.actors["Stranger"]), json_body={"query": "hi"})
    assert error_of(*_sc(stranger)) == (404, NOT_FOUND)


# ---- the decision cache never outlives a change ------------------------------------------------


def test_ph04_unshare_takes_effect_on_the_next_request(api, world: CollabWorld) -> None:
    """The decision is cached in Redis per aclVersion; unshare bumps it, so the cached allow is not served (no 30 s window)."""
    owner, reader = world.actors["Owner"], world.actors["Reader"]
    chat = chats.create_chat(api, owner)
    assert chats.share(api, owner, chat, reader).status_code == 200
    assert chats.get_chat(api, reader, chat).status_code == 200
    assert chats.get_chat(api, reader, chat).status_code == 200  # served from the cache
    assert chats.unshare(api, owner, chat, reader).status_code == 200
    assert error_of(*_sc(chats.get_chat(api, reader, chat))) == (404, NOT_FOUND)


def test_ph04_removing_a_project_member_takes_effect_on_the_next_request(api, world: CollabWorld) -> None:
    owner, viewer = world.actors["Owner"], world.actors["ProjectViewer"]
    chat = world.ids["chat"]
    assert chats.get_chat(api, viewer, chat).status_code == 200
    assert chats.get_chat(api, viewer, chat).status_code == 200
    removed = api.delete(f"/api/v1/projects/{world.ids['project']}/members/{viewer.user_id}", owner)
    assert removed.status_code in (200, 204), removed.text[:200]
    assert error_of(*_sc(chats.get_chat(api, viewer, chat))) == (404, NOT_FOUND)


def test_ph04_team_change_through_the_node_teams_route_takes_effect_on_the_next_request(stack: CollabStack, api, fake: FakeBackend, world: CollabWorld) -> None:
    """TM-03: removing a member via the Node Teams route bumps `teamsVersion`; the cached team ids and decision are not served."""
    owner, member = world.actors["Owner"], world.actors["TeamReader"]
    chat = world.sessions["team-only"].sid
    assert chats.get_chat(api, member, chat).status_code == 200
    assert chats.get_chat(api, member, chat).status_code == 200
    before = len(fake.requests_for("team_ids"))
    update = api.put(f"/api/v1/teams/{world.teams['T-read']}", owner, json_body={"removeUserIds": [member.user_id]})
    assert update.status_code == 200, update.text[:300]
    assert error_of(*_sc(chats.get_chat(api, member, chat))) == (404, NOT_FOUND)
    assert len(fake.requests_for("team_ids")) > before, "the next request resolved the member's teams again"


def test_ph04_team_change_behind_nodes_back_is_served_from_cache_for_a_while(stack: CollabStack, api, fake: FakeBackend, world: CollabWorld) -> None:
    """Control for the test above, and N1: membership changed in the connectors service alone is picked up only when the cache expires (at most 60 s)."""
    member = world.actors["TeamReader"]
    chat = world.sessions["team-only"].sid
    assert chats.get_chat(api, member, chat).status_code == 200
    fake.teams[world.teams["T-read"]].members.pop(member.user_id)
    assert chats.get_chat(api, member, chat).status_code == 200, "expected the cached allow; without it the bump test above proves nothing"
