"""PH-03 on the real stack: boot-time migrations, indexes and aclVersion bumps.

Given legacy chat data in Mongo that the API has never normalised
When the Node API boots, boots again, and boots after its completion flags are deleted
Then chat_sessions_v1 -> acl_version_v1 -> chat_collaborators_v1 normalise it once and converge, the PH03-08 indexes
exist, and every ACL writer reachable over HTTP bumps `aclVersion` while non-ACL writes do not

Owning phase: PH-03 (80-implementation-plan section 5); PH03-08, PH03-09 to PH03-11, DB-07, G-cum 4.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import pytest
from bson import ObjectId, json_util

from helper.collab_stack import chats, parity
from helper.collab_stack.identity import stable_oid
from helper.collab_stack.parity import canon, diff
from helper.collab_stack.seeds import insert_project, insert_session, project_member, session_doc
from helper.collab_stack.stack import CHAT_MIGRATION_FLAGS, CollabStack

pytestmark = [pytest.mark.integration, pytest.mark.collab_chats, pytest.mark.collab_stack, pytest.mark.usefixtures("flag_off_for_module")]

STACK_KEEP_STATE = True  # the migration tests share one legacy world across several boots
STAMP = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)
TEAM = "6f1c1a0e-2b7c-4b0e-8f55-0d6f6e8f2a11"
CHAT_SESSIONS_FLAG, ACL_FLAG, COLLAB_FLAG = CHAT_MIGRATION_FLAGS


# ---- legacy data -------------------------------------------------------------------------------


def legacy_conversation(owner, shared_with: list[dict[str, Any]], title: str) -> dict[str, Any]:
    """A document of the frozen `conversations` / `agentconversations` collections."""
    return {
        "_id": stable_oid(f"legacy:{title}"),
        "userId": owner.oid,
        "orgId": owner.org_oid,
        "initiator": owner.oid,
        "title": title,
        "isShared": True,
        "sharedWith": shared_with,
        "isDeleted": False,
        "isArchived": False,
        "status": "Complete",
        "lastActivityAt": int(STAMP.timestamp() * 1000),
        "messages": [{"_id": stable_oid(f"legacy:{title}:m1"), "messageType": "user_query", "content": "question", "createdAt": STAMP, "updatedAt": STAMP}],
        "createdAt": STAMP,
        "updatedAt": STAMP,
        "__v": 0,
    }


def seed_legacy_world(stack: CollabStack) -> dict[str, ObjectId]:
    """Every shape PH-03 has to normalise. None of the sessions or projects has an `aclVersion` unless stated."""
    r, db = stack.roster, stack.db
    owner, reader, writer, other = r.owner, r.read_recipient, r.write_recipient, r.stranger
    ids: dict[str, ObjectId] = {}

    def session(key: str, rows: Any, **kw: Any) -> None:
        extra = {} if rows is None else {"sharedWith": rows}
        seeded = insert_session(db, key, owner, acl_version=kw.pop("acl_version", None), **kw)
        ids[key] = seeded.id
        if rows is None:
            db["chatSessions"].update_one({"_id": seeded.id}, {"$unset": {"sharedWith": ""}})
        else:
            db["chatSessions"].update_one({"_id": seeded.id}, {"$set": extra})
        db["chatSessions"].update_one({"_id": seeded.id}, {"$set": {"createdAt": STAMP, "updatedAt": STAMP}})

    session(
        "mixed",
        [
            {"_id": stable_oid("row:1"), "userId": reader.oid, "accessLevel": "read"},
            {"_id": stable_oid("row:2"), "userId": writer.oid, "accessLevel": "write"},
            {"userId": owner.oid, "accessLevel": "read"},
            {"userId": other.oid, "accessLevel": None},
            {"userId": writer.oid, "accessLevel": "read"},
        ],
        is_shared=True,
    )
    session("team", [{"principalType": "team", "teamId": TEAM, "accessLevel": "write"}, {"userId": reader.oid, "accessLevel": "read"}], is_shared=True)
    session("not-shared", None, is_shared=False)
    session("shared-flag-only", None, is_shared=True)
    session(
        "clean",
        [{"principalType": "user", "userId": reader.oid, "accessLevel": "read", "addedBy": owner.oid, "addedAt": STAMP}],
        is_shared=True,
    )
    session("bumped", [{"userId": writer.oid, "accessLevel": "write"}], is_shared=True, acl_version=7)

    ids["project-bare"] = insert_project(db, "project-bare", owner, [project_member(reader, "viewer", owner)], acl_version=None)
    ids["project-bumped"] = insert_project(db, "project-bumped", owner, [], acl_version=3)

    db["conversations"].insert_one(legacy_conversation(owner, [{"_id": stable_oid("row:l1"), "userId": writer.oid, "accessLevel": "write"}], "legacy-chat"))
    legacy_agent = legacy_conversation(owner, [{"userId": reader.oid, "accessLevel": "read"}], "legacy-agent")
    legacy_agent["agentKey"] = "agent-1"
    db["agentconversations"].insert_one(legacy_agent)
    return ids


def snapshot(stack: CollabStack) -> str:
    """Every chat session and project as stored, in a stable order, for byte comparison."""
    docs = {
        name: list(stack.db[name].find().sort("_id", 1))
        for name in ("chatSessions", "chatSessionMessages", "projects")
    }
    return json_util.dumps(docs, sort_keys=True)


def reboot_with_legacy_data(stack: CollabStack) -> dict[str, ObjectId]:
    assert stack.node is not None
    stack.node.stop()
    stack.clear_chat_data()
    for name in ("conversations", "agentconversations"):
        stack.db[name].delete_many({})
    stack.forget_chat_migrations()
    ids = seed_legacy_world(stack)
    stack.node.start()
    stack.wait_for_chat_migrations()
    return ids


def flag(stack: CollabStack, key: str) -> dict[str, Any]:
    raw = stack.kv_get(key)
    assert raw, f"{key} is not set"
    return json.loads(raw)


def rows(stack: CollabStack, oid: ObjectId) -> list[tuple[str, str, str]]:
    doc = session_doc(stack.db, oid)
    return [(r.get("principalType", "?"), str(r.get("userId") or r.get("teamId")), r["accessLevel"]) for r in doc.get("sharedWith", [])]


# ---- migrations --------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def migrated(stack: CollabStack, flag_off_for_module: None) -> dict[str, Any]:
    """Boot 1 on the legacy world; later tests look at its results and then at boots 2 and 3."""
    ids = reboot_with_legacy_data(stack)
    first = snapshot(stack)
    flags_after_first = {k: stack.kv_get(k) for k in CHAT_MIGRATION_FLAGS}
    return {"ids": ids, "first": first, "flags": flags_after_first}


def test_ph03_legacy_collaborator_rows_are_normalised(stack: CollabStack, migrated: dict[str, Any]) -> None:
    """PH03-09: write -> read, `_id` and the owner's own row dropped, null level -> read, duplicates merged, team rows kept."""
    r, ids = stack.roster, migrated["ids"]
    reader, writer, other = r.read_recipient.user_id, r.write_recipient.user_id, r.stranger.user_id

    assert rows(stack, ids["mixed"]) == [("user", reader, "read"), ("user", writer, "read"), ("user", other, "read")]
    for row in session_doc(stack.db, ids["mixed"])["sharedWith"]:
        assert "_id" not in row and str(row["addedBy"]) == r.owner.user_id and row["addedAt"].replace(tzinfo=timezone.utc) == STAMP

    assert rows(stack, ids["team"]) == [("user", reader, "read"), ("team", TEAM, "write")], "the team row is kept as written, user rows are normalised"
    assert rows(stack, ids["clean"]) == [("user", reader, "read")]
    assert rows(stack, ids["bumped"]) == [("user", writer, "read")]


def test_ph03_is_shared_follows_the_rows(stack: CollabStack, migrated: dict[str, Any]) -> None:
    ids = migrated["ids"]
    flags = {key: session_doc(stack.db, ids[key])["isShared"] for key in ("mixed", "team", "clean", "bumped", "not-shared", "shared-flag-only")}
    assert flags == {"mixed": True, "team": True, "clean": True, "bumped": True, "not-shared": False, "shared-flag-only": False}
    assert session_doc(stack.db, ids["shared-flag-only"]).get("sharedWith") == []


def test_ph03_acl_version_is_stamped_and_bumped_only_where_rows_changed(stack: CollabStack, migrated: dict[str, Any]) -> None:
    """acl_version_v1 stamps 0 where it is missing and never lowers a value; the collaborator rewrite then adds 1 to rewritten rows."""
    ids = migrated["ids"]
    versions = {k: session_doc(stack.db, ids[k])["aclVersion"] for k in ("mixed", "team", "not-shared", "shared-flag-only", "clean", "bumped")}
    assert versions == {"mixed": 1, "team": 1, "not-shared": 0, "shared-flag-only": 1, "clean": 0, "bumped": 8}
    assert stack.db["projects"].find_one({"_id": ids["project-bare"]})["aclVersion"] == 0
    assert stack.db["projects"].find_one({"_id": ids["project-bumped"]})["aclVersion"] == 3
    assert stack.db["chatSessions"].count_documents({"aclVersion": {"$exists": False}}) == 0
    assert stack.db["projects"].count_documents({"aclVersion": {"$exists": False}}) == 0


def test_ph03_legacy_conversations_are_copied_then_normalised(stack: CollabStack, migrated: dict[str, Any]) -> None:
    """chat_sessions_v1 copies the frozen collections, and chat_collaborators_v1 then normalises the copies in the same boot."""
    chat = stack.db["chatSessions"].find_one({"title": "legacy-chat"})
    agent = stack.db["chatSessions"].find_one({"title": "legacy-agent"})
    assert chat is not None and agent is not None
    assert [(r["principalType"], str(r["userId"]), r["accessLevel"]) for r in chat["sharedWith"]] == [("user", stack.roster.write_recipient.user_id, "read")]
    assert [(r["principalType"], str(r["userId"]), r["accessLevel"]) for r in agent["sharedWith"]] == [("user", stack.roster.read_recipient.user_id, "read")]
    assert stack.db["chatSessionMessages"].count_documents({"sessionId": {"$in": [chat["_id"], agent["_id"]]}}) == 2
    assert stack.db["conversations"].find_one({"title": "legacy-chat"})["isMigrated"] is True


def test_ph03_completion_flags_carry_the_counts(stack: CollabStack, migrated: dict[str, Any]) -> None:
    sessions = json.loads(migrated["flags"][CHAT_SESSIONS_FLAG])
    assert sessions["conversationsMigrated"] is True and sessions["agentConversationsMigrated"] is True
    assert sessions["totals"]["chat"] == {"sessions": 1, "messages": 1} and sessions["totals"]["agent"] == {"sessions": 1, "messages": 1}

    acl = json.loads(migrated["flags"][ACL_FLAG])
    assert acl["projects"] == 1 and acl["errored"] == 0
    assert acl["chatSessions"] >= 6

    collaborators = json.loads(migrated["flags"][COLLAB_FLAG])
    # scanned: mixed, team, shared-flag-only, clean, bumped + the two copies. unchanged: clean.
    # downgraded legacy writes: mixed (1), bumped (1), legacy-chat (1); the team row's write is not a legacy row.
    assert collaborators == {"scanned": 7, "normalized": 6, "downgradedWrites": 3, "raced": 0, "errored": 0}


def test_ph03_second_boot_is_a_no_op(stack: CollabStack, migrated: dict[str, Any]) -> None:
    """With the flags set nothing is scanned or written: the data is byte-identical and the flags are untouched."""
    stack.node.restart()  # type: ignore[union-attr]
    assert snapshot(stack) == migrated["first"]
    assert {k: stack.kv_get(k) for k in CHAT_MIGRATION_FLAGS} == migrated["flags"]


def test_ph03_deleting_the_flags_converges_to_the_same_state(stack: CollabStack, migrated: dict[str, Any]) -> None:
    """DB-07 / G-cum 4: a re-run finds nothing to change, rewrites the flags and leaves the data as it was."""
    stack.forget_chat_migrations()
    stack.node.restart()  # type: ignore[union-attr]
    stack.wait_for_chat_migrations()
    assert snapshot(stack) == migrated["first"], "re-running the migrations changed already-migrated data"
    collaborators = flag(stack, COLLAB_FLAG)
    assert (collaborators["normalized"], collaborators["downgradedWrites"], collaborators["raced"], collaborators["errored"]) == (0, 0, 0, 0)
    assert collaborators["scanned"] == 6, "the session whose stale isShared flag was cleared is no longer a candidate"
    acl = flag(stack, ACL_FLAG)
    assert (acl["chatSessions"], acl["projects"], acl["errored"]) == (0, 0, 0)


# ---- indexes (PH03-08, DB-09) ------------------------------------------------------------------


def index_specs(stack: CollabStack, collection: str) -> list[dict[str, Any]]:
    return [
        {"key": list(map(tuple, info["key"])), **{k: v for k, v in info.items() if k in ("unique", "partialFilterExpression", "expireAfterSeconds")}}
        for info in stack.db[collection].index_information().values()
    ]


def has_index(specs: list[dict[str, Any]], key: list[tuple[str, int]], **options: Any) -> bool:
    return any(s["key"] == key and all(s.get(k) == v for k, v in options.items()) for s in specs)


def test_ph03_chat_session_indexes(stack: CollabStack, migrated: dict[str, Any]) -> None:
    specs = index_specs(stack, "chatSessions")
    assert has_index(specs, [("orgId", 1), ("sharedWith.userId", 1), ("sessionType", 1), ("isDeleted", 1), ("lastActivityAt", -1)])
    assert has_index(specs, [("orgId", 1), ("sharedWith.teamId", 1), ("sessionType", 1), ("isDeleted", 1), ("lastActivityAt", -1)])
    assert has_index(
        specs, [("orgId", 1), ("initiator", 1), ("creationKey", 1)], unique=True, partialFilterExpression={"creationKey": {"$type": "string"}}
    )
    assert has_index(specs, [("isShared", 1)])
    for spec in specs:
        assert "$exists" not in json.dumps(spec.get("partialFilterExpression", {})), "partial indexes use $type, never $exists (DB-09)"


def test_ph03_message_and_notification_indexes(stack: CollabStack, migrated: dict[str, Any]) -> None:
    messages = index_specs(stack, "chatSessionMessages")
    assert has_index(
        messages,
        [("sessionId", 1), ("authorUserId", 1), ("clientMessageId", 1)],
        unique=True,
        partialFilterExpression={"clientMessageId": {"$type": "string"}},
    )
    assert has_index(messages, [("orgId", 1), ("authorUserId", 1)], partialFilterExpression={"authorUserId": {"$type": "objectId"}})
    assert has_index(messages, [("sessionId", 1), ("seq", 1)], unique=True)
    notifications = index_specs(stack, "notifications")
    assert has_index(notifications, [("assignedTo", 1), ("dedupeKey", 1)], unique=True, partialFilterExpression={"dedupeKey": {"$type": "string"}})


# ---- ACL writers bump aclVersion over HTTP -----------------------------------------------------


def acl_version(stack: CollabStack, collection: str, oid: str) -> int:
    return stack.db[collection].find_one({"_id": ObjectId(oid)}).get("aclVersion", 0)


def test_ph03_conversation_acl_writers_bump_acl_version(stack: CollabStack, migrated: dict[str, Any], api, roster) -> None:  # noqa: ANN001
    """PH03-11: share, unshare, set project, project visibility and delete each add 1; rename does not."""
    owner = roster.owner
    chat = chats.create_chat(api, owner)
    project = chats.create_project(api, owner, "bump")
    steps = [
        ("share", lambda: chats.share(api, owner, chat, roster.read_recipient), 1),
        ("unshare", lambda: chats.unshare(api, owner, chat, roster.read_recipient), 1),
        ("set project", lambda: api.put(f"/api/v1/conversations/{chat}/project", owner, json_body={"projectId": project}), 1),
        ("project visibility", lambda: api.patch(f"/api/v1/conversations/{chat}/project-visibility", owner, json_body={"visibility": "project"}), 1),
        ("rename", lambda: api.patch(f"/api/v1/conversations/{chat}/title", owner, json_body={"title": "renamed"}), 0),
        ("delete", lambda: api.delete(f"/api/v1/conversations/{chat}", owner), 1),
    ]
    for name, act, bump in steps:
        before = acl_version(stack, "chatSessions", chat)
        resp = act()
        assert resp.status_code == 200, f"{name}: {resp.status_code} {resp.text[:200]}"
        assert acl_version(stack, "chatSessions", chat) - before == bump, f"{name} changed aclVersion by {acl_version(stack, 'chatSessions', chat) - before}, expected {bump}"


def test_ph03_project_acl_writers_bump_acl_version(stack: CollabStack, migrated: dict[str, Any], api, roster) -> None:  # noqa: ANN001
    """Member add, member remove, visibility and delete bump the project; pin does not."""
    owner, member = roster.owner, roster.project_viewer
    project = chats.create_project(api, owner, "acl")
    steps = [
        ("add member", lambda: chats.add_project_member(api, owner, project, member.user_id), 1),
        ("pin", lambda: api.post(f"/api/v1/projects/{project}/pin", owner), 0),
        ("remove member", lambda: api.delete(f"/api/v1/projects/{project}/members/{member.user_id}", owner), 1),
        ("visibility", lambda: api.patch(f"/api/v1/projects/{project}", owner, json_body={"visibility": "org"}), 1),
        ("delete", lambda: api.delete(f"/api/v1/projects/{project}", owner), 1),
    ]
    for name, act, bump in steps:
        before = acl_version(stack, "projects", project)
        resp = act()
        assert resp.status_code in (200, 204), f"{name}: {resp.status_code} {resp.text[:200]}"
        assert acl_version(stack, "projects", project) - before == bump, f"{name}: aclVersion moved by {acl_version(stack, 'projects', project) - before}, expected {bump}"


# ---- project list parity -----------------------------------------------------------------------


@pytest.mark.parametrize("call_id", ["P1", "P2", "P3", "P4", "P5"])
def test_ph03_project_reads_equal_the_ph00_baseline_for_user_only_orgs(stack: CollabStack, call_id: str, migrated: dict[str, Any]) -> None:
    """G-cum 4: `GET /api/v1/projects` and the project reads return what PH-00 returned when every member is a user."""
    baseline = parity.load_baseline()["calls"][call_id]
    call = next(c for c in parity.CALLS if c.id == call_id)
    for actor in call.actors:
        current = parity.play(stack, call, actor)
        # PH-03 resolves the caller's teams once per project read (recorded in PH-03 section 6); nothing else may differ.
        assert baseline[actor]["teamLookups"] == 0 and current["teamLookups"] <= 1
        unexplained = diff(canon({**baseline[actor], "teamLookups": 0}), canon({**current, "teamLookups": 0}))
        assert not unexplained, f"{call_id}/{actor}: {unexplained[:5]}"
