"""Chat and project documents written straight into Mongo.

Direct seeding is for states the public API cannot produce on purpose: legacy rows, team rows,
write-level rows with the flag off, or a project member added before the chat existed. Ids derive
from a label, so a seed is byte-identical from run to run and across API versions.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from bson import ObjectId
from pymongo.database import Database

from helper.collab_stack.identity import Actor, stable_oid

EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)
AGENT_KEY = "agent-1"


@dataclass(frozen=True)
class Seeded:
    key: str
    id: ObjectId
    message_ids: tuple[ObjectId, ...]

    @property
    def sid(self) -> str:
        return str(self.id)

    @property
    def answer_id(self) -> str:
        """The bot_response message id."""
        return str(self.message_ids[-1])

    @property
    def question_id(self) -> str:
        return str(self.message_ids[0])


def user_row(actor: Actor, level: str = "read", *, principal_type: bool = False, **extra: Any) -> dict[str, Any]:
    row: dict[str, Any] = {"userId": actor.oid, "accessLevel": level, "addedAt": EPOCH, **extra}
    if principal_type:
        row["principalType"] = "user"
    return row


def team_row(team_id: str, level: str = "read", **extra: Any) -> dict[str, Any]:
    return {"principalType": "team", "teamId": team_id, "accessLevel": level, "addedAt": EPOCH, **extra}


def insert_session(
    db: Database,
    key: str,
    owner: Actor,
    *,
    kind: str = "chat",
    shared_with: list[dict[str, Any]] | None = None,
    is_shared: bool | None = None,
    project: ObjectId | None = None,
    project_visibility: str | None = None,
    messages: list[tuple[str, str]] | None = None,
    age_minutes: int = 0,
    acl_version: int | None = 1,
    **fields: Any,
) -> Seeded:
    """A chat (``kind='chat'``) or agent chat (``kind='agent'``) with a question and an answer."""
    sid = stable_oid(f"session:{key}")
    shared = list(shared_with or [])
    stamp = EPOCH + timedelta(minutes=age_minutes)
    doc: dict[str, Any] = {
        "_id": sid,
        "sessionType": kind,
        "nextSeq": 0,
        "userId": owner.oid,
        "orgId": owner.org_oid,
        "initiator": owner.oid,
        "title": key,
        "isShared": bool(shared) if is_shared is None else is_shared,
        "sharedWith": shared,
        "isDeleted": False,
        "isArchived": False,
        "status": "Complete",
        "lastActivityAt": int(stamp.timestamp() * 1000),
        "rev": 0,
        "aclVersion": 1,
        "schemaVersion": 1,
        "conversationErrors": [],
        "modelInfo": {"chatMode": "quick"},
        "createdAt": stamp,
        "updatedAt": stamp,
        "__v": 0,
    }
    if kind == "agent":
        doc["agentKey"] = AGENT_KEY
        doc["conversationSource"] = "agent_chat"
    if project is not None:
        doc["projectId"] = project
        doc["projectVisibility"] = project_visibility or "private"
    doc.update(fields)
    if acl_version is None:
        doc.pop("aclVersion")
    else:
        doc["aclVersion"] = acl_version
    db["chatSessions"].replace_one({"_id": sid}, doc, upsert=True)
    turns = messages if messages is not None else [("user_query", f"question {key}"), ("bot_response", f"answer {key}")]
    ids: list[ObjectId] = []
    for seq, (mtype, content) in enumerate(turns, start=1):
        mid = stable_oid(f"message:{key}:{seq}")
        ids.append(mid)
        db["chatSessionMessages"].replace_one(
            {"_id": mid},
            {
                "_id": mid,
                "sessionId": sid,
                "orgId": owner.org_oid,
                "seq": seq,
                "schemaVersion": 1,
                "messageType": mtype,
                "content": content,
                "contentFormat": "MARKDOWN",
                "citations": [],
                "followUpQuestions": [],
                "feedback": [],
                "createdAt": stamp + timedelta(seconds=seq),
                "updatedAt": stamp + timedelta(seconds=seq),
            },
            upsert=True,
        )
    db["chatSessions"].update_one({"_id": sid}, {"$set": {"nextSeq": len(turns)}})
    return Seeded(key, sid, tuple(ids))


def project_member(actor: Actor, role: str, added_by: Actor) -> dict[str, Any]:
    return {"principalType": "user", "principalId": actor.oid, "role": role, "addedBy": added_by.oid, "addedAt": EPOCH}


def project_team_member(team_id: str, role: str, added_by: Actor) -> dict[str, Any]:
    return {"principalType": "team", "teamId": team_id, "role": role, "addedBy": added_by.oid, "addedAt": EPOCH}


def insert_project(
    db: Database,
    key: str,
    owner: Actor,
    members: list[dict[str, Any]] | None = None,
    *,
    visibility: str = "private",
    acl_version: int | None = 1,
    **fields: Any,
) -> ObjectId:
    pid = stable_oid(f"project:{key}")
    doc: dict[str, Any] = {
        "_id": pid,
        "orgId": owner.org_oid,
        "userId": owner.oid,
        "name": key,
        "description": f"project {key}",
        "instructions": "",
        "knowledgeScope": {"apps": [], "kb": []},
        "appliedFilters": {"apps": [], "kb": []},
        "tools": [],
        "linkedKnowledgeBaseId": None,
        "visibility": visibility,
        "chatSharing": "private",
        "projectChatAccess": "viewer",
        "aclVersion": 1,
        "members": members or [],
        "isPinned": False,
        "isArchived": False,
        "isDeleted": False,
        "lastActivityAt": int(time.time() * 1000),
        "createdAt": EPOCH,
        "updatedAt": EPOCH,
        "__v": 0,
    }
    doc.update(fields)
    if acl_version is None:
        doc.pop("aclVersion")
    else:
        doc["aclVersion"] = acl_version
    db["projects"].replace_one({"_id": pid}, doc, upsert=True)
    return pid


@dataclass
class Collected:
    """What a test needs to look at after the fact."""

    sessions: list[Seeded] = field(default_factory=list)


def session_doc(db: Database, sid: ObjectId | str) -> dict[str, Any] | None:
    return db["chatSessions"].find_one({"_id": ObjectId(str(sid))})


def messages_of(db: Database, sid: ObjectId | str) -> list[dict[str, Any]]:
    return list(db["chatSessionMessages"].find({"sessionId": ObjectId(str(sid))}).sort("seq", 1))
