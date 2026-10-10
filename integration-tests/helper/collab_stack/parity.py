"""Scripted calls, a seeded world and a normaliser: the machinery behind flag-off parity (J-02).

``capture(stack)`` plays every call as every actor against a freshly seeded world and returns a JSON
document. Run against the baseline tree (origin/main without the feature) it produces the baseline fixture; run against the tree under test it
produces the document the baseline is diffed with. Ids, timestamps and request ids are normalised so
the two documents are comparable.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bson import ObjectId

from helper.collab_stack.client import parse_sse
from helper.collab_stack.identity import Actor, Directory, stable_oid
from helper.collab_stack.seeds import (
    AGENT_KEY,
    Seeded,
    insert_project,
    insert_session,
    project_member,
    user_row,
)
from helper.collab_stack.stack import CollabStack

FIXTURE = Path(__file__).resolve().parents[2] / "collaborative-chats" / "stack" / "fixtures" / "j02_flag_off_baseline.json"
RUN_ID = "3f2b8c1e-5d4a-4b7e-9a6c-1d2e3f4a5b6c"
AI_ROUTES = ("chat", "chat_stream", "agent_chat", "agent_chat_stream", "chat_cancel", "attachments_validate", "agent_create", "agent_list", "agent_item")
TEAM_ROUTES = ("team_ids", "team_get")
SETTLE_S = 0.15

# Keys that hold a value which differs run to run.
VOLATILE_KEYS = {"requestId", "timestamp", "duration", "durationMs", "processingTimeMs", "slug", "timeToFeedback"}
ISO_TS = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?(Z|[+-]\d\d:\d\d)$")
OID = re.compile(r"^[0-9a-f]{24}$")
EPOCH_MS = range(1_500_000_000_000, 2_500_000_000_000)

ACTOR_NAMES = ("Owner", "Writer", "Reader", "ProjectViewer", "ProjectEditor", "Stranger", "Outsider")


@dataclass(frozen=True)
class Call:
    id: str
    method: str
    path: str
    body: dict[str, Any] | None = None
    params: dict[str, str] | None = None
    internal: bool = False
    stream: bool = False
    actors: tuple[str, ...] = ACTOR_NAMES
    note: str = ""


def _chat(id_: str, method: str, path: str, **kw: Any) -> Call:
    return Call(id_, method, f"/api/v1/conversations{path}", **kw)


def _agent(id_: str, method: str, path: str, **kw: Any) -> Call:
    return Call(id_, method, f"/api/v1/agents/{AGENT_KEY}/conversations{path}", **kw)


TURN = {"query": "More detail", "chatMode": "quick"}
STREAM_TURN = {"query": "More detail", "chatMode": "internal_search"}

# Every route with :conversationId (PH-04 section 3, C1-C16 and A1-A13), then the routes without one.
CALLS: tuple[Call, ...] = (
    _chat("C1", "POST", "/{chat}/messages", body=TURN),
    _chat("C2", "POST", "/internal/{chat}/messages", body=TURN, internal=True),
    _chat("C3", "POST", "/{chat}/messages/stream", body=STREAM_TURN, stream=True),
    _chat("C4", "POST", "/internal/{chat}/messages/stream", body=STREAM_TURN, internal=True, stream=True),
    _chat("C5", "GET", "/{chat}"),
    _chat("C5-filtered", "GET", "/{chat}", params={"search": "no-such-text", "shared": "true"}, note="detail with list filters"),
    _chat("C6", "DELETE", "/{chat}"),
    _chat("C7", "POST", "/{chat}/share", body={"userIds": ["{Stranger}"]}),
    _chat("C8", "POST", "/{chat}/unshare", body={"userIds": ["{Reader}"]}),
    _chat("C9", "PUT", "/{chat}/project", body={"projectId": None}),
    _chat("C10", "PATCH", "/{chat}/project-visibility", body={"visibility": "private"}),
    _chat("C11", "POST", "/{chat}/message/{chat_answer}/regenerate", body={"chatMode": "quick"}),
    _chat("C12", "POST", "/{chat}/cancel", body={"runId": RUN_ID}),
    _chat("C13", "PATCH", "/{chat}/title", body={"title": "Renamed"}),
    _chat("C14", "POST", "/{chat}/message/{chat_answer}/feedback", body={"isHelpful": True}),
    _chat("C15", "PATCH", "/{chat}/archive"),
    _chat("C16", "PATCH", "/{chat}/unarchive"),
    _agent("A1", "POST", "/{agent}/messages", body=TURN),
    _agent("A2", "POST", "/{agent}/messages/stream", body=TURN, stream=True),
    _agent("A3", "POST", "/internal/{agent}/messages/stream", body=TURN, internal=True, stream=True),
    _agent("A4", "POST", "/{agent}/message/{agent_answer}/regenerate", body={"chatMode": "quick"}),
    _agent("A5", "POST", "/{agent}/cancel", body={"runId": RUN_ID}),
    _agent("A6", "POST", "/{agent}/message/{agent_answer}/feedback", body={"isHelpful": True}),
    _agent("A7", "GET", "/{agent}"),
    _agent("A8", "DELETE", "/{agent}"),
    _agent("A8-missing", "DELETE", "/6abf35278cf29be1a243f999", note="agent chat id that does not exist"),
    _agent("A9", "PATCH", "/{agent}/title", body={"title": "Renamed"}),
    _agent("A10", "PUT", "/{agent}/project", body={"projectId": None}),
    _agent("A11", "PATCH", "/{agent}/project-visibility", body={"visibility": "private"}),
    _agent("A12", "POST", "/{agent}/archive"),
    _agent("A13", "POST", "/{agent}/unarchive"),
    # Starting a conversation.
    _chat("N1", "POST", "/create", body={"query": "A new chat", "chatMode": "quick"}),
    _chat("N2", "POST", "/stream", body={"query": "A new chat", "chatMode": "internal_search"}, stream=True),
    _chat("N3", "POST", "/internal/create", body={"query": "A new chat"}, internal=True),
    _chat("N4", "POST", "/internal/stream", body={"query": "A new chat", "chatMode": "internal_search"}, internal=True, stream=True),
    _agent("N5", "POST", "", body={"query": "A new agent chat", "chatMode": "quick"}),
    _agent("N6", "POST", "/stream", body={"query": "A new agent chat", "chatMode": "quick"}, stream=True),
    # Internal callers that must not get past the token.
    _chat("I1", "POST", "/internal/{chat}/messages", body={}, internal=True, actors=("Disabled",), note="disabled user, invalid body"),
    _chat("I2", "POST", "/internal/{chat}/messages", body=TURN, internal=True, actors=("Disabled",), note="disabled user, valid body"),
    # Lists.
    _chat("L1", "GET", ""),
    _chat("L2", "GET", "", params={"source": "shared"}),
    _chat("L3", "GET", "", params={"search": "question"}),
    _chat("L4", "GET", "/show/archives"),
    _chat("L5", "GET", "/show/archives/search", params={"search": "archived"}),
    _agent("L6", "GET", ""),
    _agent("L7", "GET", "", params={"source": "shared"}),
    _agent("L8", "GET", "/show/archives"),
    Call("L9", "GET", "/api/v1/agents/conversations/show/archives"),
    Call("P1", "GET", "/api/v1/projects"),
    Call("P2", "GET", "/api/v1/projects", params={"scope": "shared"}),
    Call("P3", "GET", "/api/v1/projects", params={"scope": "all"}),
    Call("P4", "GET", "/api/v1/projects/{project}"),
    Call("P5", "GET", "/api/v1/projects/{project}/conversations"),
    # Agents (proxied to the AI backend).
    Call("G1", "GET", "/api/v1/agents"),
    Call("G2", "GET", f"/api/v1/agents/{AGENT_KEY}"),
    Call("G3", "POST", "/api/v1/agents/create", body={"name": "Agent X", "description": "d", "systemPrompt": "p", "startMessage": "hi"}),
    Call("G4", "PUT", f"/api/v1/agents/{AGENT_KEY}", body={"name": "Agent Y"}),
    Call("G5", "DELETE", f"/api/v1/agents/{AGENT_KEY}"),
)

# Which actors exist besides the roster.
EXTRA_ACTORS = ("Disabled",)


@dataclass
class World:
    ids: dict[str, str] = field(default_factory=dict)
    sessions: dict[str, Seeded] = field(default_factory=dict)
    project: ObjectId | None = None
    actors: dict[str, Actor] = field(default_factory=dict)

    def fill(self, template: str) -> str:
        out = template
        for key, value in self.ids.items():
            out = out.replace("{" + key + "}", value)
        return out

    def fill_body(self, body: Any) -> Any:
        if isinstance(body, str):
            return self.fill(body)
        if isinstance(body, list):
            return [self.fill_body(b) for b in body]
        if isinstance(body, dict):
            return {k: self.fill_body(v) for k, v in body.items()}
        return body


def actors_by_name(stack: CollabStack) -> dict[str, Actor]:
    r = stack.roster
    assert r is not None
    return {
        "Owner": r.owner,
        "Writer": r.write_recipient,
        "Reader": r.read_recipient,
        "ProjectViewer": r.project_viewer,
        "ProjectEditor": r.project_editor,
        "Stranger": r.stranger,
        "Outsider": r.other_org,
        "Disabled": r.disabled,
    }


def seed_world(stack: CollabStack) -> World:
    """The same documents every time. No team rows: the PH-00 API cannot read them."""
    actors = actors_by_name(stack)
    owner, reader, writer = actors["Owner"], actors["Reader"], actors["Writer"]
    db = stack.db
    project = insert_project(
        db,
        "PRJ",
        owner,
        [project_member(actors["ProjectViewer"], "viewer", owner), project_member(actors["ProjectEditor"], "editor", owner)],
    )
    insert_project(db, "PRJ-reader", reader, [])
    shared = [user_row(reader, "read"), user_row(writer, "write")]
    sessions = {
        "chat": insert_session(db, "chat", owner, shared_with=shared, project=project, project_visibility="project", age_minutes=5),
        "agent": insert_session(db, "agent", owner, kind="agent", shared_with=shared, project=project, project_visibility="project", age_minutes=4),
        "solo": insert_session(db, "solo", owner, age_minutes=3),
        "agent-solo": insert_session(db, "agent-solo", owner, kind="agent", age_minutes=2),
        "archived": insert_session(db, "archived", owner, isArchived=True, archivedBy=owner.oid, age_minutes=1),
        "agent-archived": insert_session(db, "agent-archived", owner, kind="agent", isArchived=True, archivedBy=owner.oid, age_minutes=0),
        "reader-chat": insert_session(db, "reader-chat", reader, age_minutes=6),
        "outsider-chat": insert_session(db, "outsider-chat", actors["Outsider"], age_minutes=7),
    }
    ids = {
        "chat": sessions["chat"].sid,
        "chat_answer": sessions["chat"].answer_id,
        "agent": sessions["agent"].sid,
        "agent_answer": sessions["agent"].answer_id,
        "project": str(project),
        **{name: a.user_id for name, a in actors.items()},
    }
    return World(ids=ids, sessions=sessions, project=project, actors=actors)


class Normalizer:
    """Replaces ids with stable labels and volatile values with placeholders."""

    def __init__(self, world: World) -> None:
        self.labels: dict[str, str] = {}
        for name, actor in world.actors.items():
            self.labels[actor.user_id] = f"user:{name}"
        for org_name, org_id in {"acme": world.actors["Owner"].org_id, "globex": world.actors["Outsider"].org_id}.items():
            self.labels[org_id] = f"org:{org_name}"
        for key, seeded in world.sessions.items():
            self.labels[seeded.sid] = f"session:{key}"
            for n, mid in enumerate(seeded.message_ids, start=1):
                self.labels[str(mid)] = f"message:{key}:{n}"
        if world.project is not None:
            self.labels[str(world.project)] = "project:PRJ"
        self.labels[str(stable_oid("project:PRJ-reader"))] = "project:PRJ-reader"
        self.fresh: dict[str, str] = {}

    def oid(self, value: str) -> str:
        if value in self.labels:
            return f"<{self.labels[value]}>"
        return f"<new:{self.fresh.setdefault(value, str(len(self.fresh) + 1))}>"

    def text(self, value: str) -> str:
        if ISO_TS.match(value):
            return "<ts>"
        if OID.match(value):
            return self.oid(value)
        # Ids embedded in prose or in the fake's history strings.
        return OID.sub(lambda m: self.oid(m.group(0)), value) if OID.search(value) else value

    def value(self, v: Any, key: str | None = None) -> Any:
        if key in VOLATILE_KEYS:
            return "<v>"
        if key == "sharedWith" and isinstance(v, list):
            # Legacy rows carried a Mongoose `_id` that new rows do not (PH-03 `_id:false`); it would shift the numbering of new ids.
            return [self.value({k: x for k, x in row.items() if k != "_id"}) if isinstance(row, dict) else self.value(row) for row in v]
        if isinstance(v, dict):
            return {k: self.value(x, k) for k, x in v.items()}
        if isinstance(v, list):
            return [self.value(x) for x in v]
        if isinstance(v, str):
            return self.text(v)
        if isinstance(v, bool) or v is None:
            return v
        if isinstance(v, int) and v in EPOCH_MS:
            return "<ts-ms>"
        return v


def _snapshot(stack: CollabStack, world: World, norm: Normalizer) -> dict[str, Any]:
    """The state a call could have changed, in fields the PH-00 and later trees both have."""
    out: dict[str, Any] = {}
    for key, seeded in world.sessions.items():
        doc = stack.db["chatSessions"].find_one({"_id": seeded.id})
        if doc is None:
            out[key] = None
            continue
        out[key] = norm.value(
            {
                "title": doc.get("title"),
                "isDeleted": doc.get("isDeleted"),
                "isArchived": doc.get("isArchived"),
                "isShared": doc.get("isShared"),
                "sharedWith": [{"userId": str(r["userId"]), "accessLevel": r["accessLevel"]} for r in doc.get("sharedWith", []) if "userId" in r],
                "projectId": str(doc["projectId"]) if doc.get("projectId") else None,
                "projectVisibility": doc.get("projectVisibility"),
                "messages": stack.db["chatSessionMessages"].count_documents({"sessionId": seeded.id}),
                "feedbackOnAnswer": len((stack.db["chatSessionMessages"].find_one({"_id": seeded.message_ids[-1]}) or {}).get("feedback", [])),
            }
        )
    out["sessionCount"] = stack.db["chatSessions"].count_documents({})
    return out


def _fake_requests(stack: CollabStack, world: World, norm: Normalizer) -> tuple[list[dict[str, Any]], int]:
    uid_to_name = {a.user_id: n for n, a in world.actors.items()}
    seen = []
    for rec in stack.fake.requests_for(*AI_ROUTES):
        claims = rec.token_claims
        seen.append(
            {
                "route": rec.route,
                "agent": rec.match.get("agent_key"),
                "as": uid_to_name.get(claims.get("userId")) or ("scoped" if claims.get("scopes") else "unknown"),
                "body": norm.value(rec.body),
            }
        )
    return seen, len(stack.fake.requests_for(*TEAM_ROUTES))


def request(stack: CollabStack, world: World, call: Call, actor: Actor):  # noqa: ANN202
    api = stack.api
    assert api is not None
    path = world.fill(call.path)
    body = world.fill_body(call.body)
    token = Directory.scoped_token(actor) if call.internal else None
    who = None if call.internal else actor
    if call.stream:
        stream = api.stream(path, who, token=token, json_body=body, method=call.method).finish(60)
        return stream.status, stream.response_headers, stream.text
    resp = api.call(call.method, path, who, token=token, json_body=body, params=call.params)
    return resp.status_code, dict(resp.headers), resp.text


def play(stack: CollabStack, call: Call, actor_name: str) -> dict[str, Any]:
    """One call as one actor on a freshly seeded world."""
    stack.reset_state()
    world = seed_world(stack)
    norm = Normalizer(world)
    status, headers, text = request(stack, world, call, world.actors[actor_name])
    time.sleep(SETTLE_S)
    content_type = headers.get("Content-Type", headers.get("content-type", "")).split(";")[0]
    if content_type == "text/event-stream":
        body: Any = [{"event": e.event, "data": norm.value(e.data)} for e in parse_sse(text)]
    elif content_type == "application/json" and text:
        body = norm.value(json.loads(text))
    else:
        body = text[:500]
    requests_seen, team_lookups = _fake_requests(stack, world, norm)
    return {
        "status": status,
        "contentType": content_type,
        "body": body,
        "aiRequests": requests_seen,
        "teamLookups": team_lookups,
        "after": _snapshot(stack, world, norm),
    }


def capture(stack: CollabStack, only: tuple[str, ...] | None = None) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for call in CALLS:
        if only and call.id not in only:
            continue
        out[call.id] = {name: play(stack, call, name) for name in call.actors}
    return out


# Bookkeeping fields PH-02/PH-03 added to the conversation documents. They are additive and no PH-00 client reads them.
ADDITIVE_KEYS = frozenset({"aclVersion", "activeRun", "archivedFor", "hiddenFor", "ownershipHistory", "rev", "settings", "creationKey"})
# PH-05 "Flag off": written whatever the flag says (inert for flag-off clients): who wrote a user row and who asked a reply.
# `rev` is already above. `runId`, `clientMessageId`, `creationKey` and `activeRun` exist only with a lease, i.e. with the flag on.
PH05_INERT_KEYS = frozenset({"authorUserId", "requestedBy", "inReplyTo"})
# Present in different places across the two trees without carrying behaviour: Mongoose's version key and the schema stamp.
NOISE_KEYS = frozenset({"__v", "schemaVersion"})
NOT_FOUND_CODES = frozenset({"HTTP_NOT_FOUND", "CONVERSATION_NOT_FOUND"})
# New rows say who added them and when (51 section 1.1); a share response lists them.
COLLABORATOR_ADDITIVE_KEYS = frozenset({"addedAt", "addedBy", "principalType"})


def canon(entry: Any, *, in_error: bool = False) -> Any:
    """What two flag-off captures are compared on.

    Drops the additive bookkeeping fields and the noise keys, and reduces a 404 error to its status: PH-04 unified
    the code to CONVERSATION_NOT_FOUND and the text, which the J-02 test asserts on the current side separately.
    """
    if isinstance(entry, dict):
        if entry.get("status") == 404 and isinstance(entry.get("body"), dict) and "error" in entry["body"]:
            err = entry["body"]["error"]
            code = "NOT_FOUND" if err.get("code") in NOT_FOUND_CODES else err.get("code")
            entry = {**entry, "body": {"error": {"code": code}}}
        out = {k: canon(v) for k, v in entry.items() if k not in ADDITIVE_KEYS and k not in PH05_INERT_KEYS and k not in NOISE_KEYS}
        if isinstance(out.get("sharedWith"), list):
            out["sharedWith"] = [{k: v for k, v in row.items() if k not in COLLABORATOR_ADDITIVE_KEYS} for row in out["sharedWith"]]
        return out
    if isinstance(entry, list):
        return [canon(v) for v in entry]
    return entry


def load_baseline() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text())


def write_baseline(calls: dict[str, dict[str, Any]], commit: str) -> None:
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    doc = {
        "meta": {
            "capturedFrom": commit,
            "description": (
                f"Flag-off API behaviour of origin/main ({commit[:9]}), the tree without collaborative chats, "
                "on a fresh database with the J-02 seed; ids, timestamps and request ids normalised."
            ),
            "regenerate": "PCC_E2E_NODE_ROOT=<origin/main worktree>/backend/nodejs/apps PCC_E2E_CAPTURE=write collaborative-chats/stack/run.sh -k j02_capture",
        },
        "calls": calls,
    }
    # One line per (call, actor): reviewable diffs without the size of a fully indented file.
    lines = ['{"meta": ' + json.dumps(doc["meta"], sort_keys=True) + ', "calls": {']
    call_ids = sorted(calls)
    for i, call_id in enumerate(call_ids):
        actors = sorted(calls[call_id])
        lines.append(f"{json.dumps(call_id)}: {{")
        for j, actor in enumerate(actors):
            comma = "," if j < len(actors) - 1 else ""
            lines.append(f"  {json.dumps(actor)}: {json.dumps(calls[call_id][actor], sort_keys=True, separators=(',', ':'))}{comma}")
        lines.append("}" + ("," if i < len(call_ids) - 1 else ""))
    lines.append("}}")
    FIXTURE.write_text("\n".join(lines) + "\n")


def diff(a: Any, b: Any, path: str = "") -> list[str]:
    """Paths where ``a`` (baseline) and ``b`` (current) differ."""
    if type(a) is not type(b):
        return [f"{path or '/'}: {json.dumps(a)[:120]} -> {json.dumps(b)[:120]}"]
    if isinstance(a, dict):
        out: list[str] = []
        for k in sorted(set(a) | set(b)):
            if k not in a:
                out.append(f"{path}/{k}: <absent> -> {json.dumps(b[k])[:120]}")
            elif k not in b:
                out.append(f"{path}/{k}: {json.dumps(a[k])[:120]} -> <absent>")
            else:
                out += diff(a[k], b[k], f"{path}/{k}")
        return out
    if isinstance(a, list):
        if len(a) != len(b):
            return [f"{path}: list of {len(a)} -> list of {len(b)}"]
        return [d for i, (x, y) in enumerate(zip(a, b)) for d in diff(x, y, f"{path}[{i}]")]
    return [] if a == b else [f"{path or '/'}: {json.dumps(a)[:120]} -> {json.dumps(b)[:120]}"]
