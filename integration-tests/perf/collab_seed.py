"""Deterministic seed for the collaborative-chats load and explain runs (PH-12 PR-12.4).

Fills ``chatSessions`` / ``chatSessionMessages`` directly in Mongo, with the shapes the API itself
writes, and registers the teams the users belong to. The same ``--seed`` always yields the same
documents (ids included), whatever the size.

Scale knobs: ``COLLAB_SEED_SESSIONS`` (CI 200,000; the manual run uses 5,000,000) and
``COLLAB_SEED_TEAMS`` (2,000). Two users carry the cases the plan needs:

* ``CollabU`` is in the org-wide "All" team and 20 other teams;
* ``CollabU300`` is in the "All" team and 300 other teams, more than the 200 index scans Mongo
  explodes for a sort (PH12-01).

Teams are not written to a graph. In the lane, Node resolves a caller's team ids over HTTP from the
fake of the Python service (``GET /api/v1/entity/user/team-ids``, see ``helper/collab_stack``), so
the seeder registers the teams on that fake. Against a real stack (``--mongo-uri``) it only writes
Mongo and writes the team membership it expects to ``--output`` for the operator to create in the
graph.

Shared sessions carry user rows and team rows. Team picks are skewed towards low team numbers, so
the teams the two users belong to are the popular ones and their "shared with me" lists are long.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

IT_DIR = Path(__file__).resolve().parent.parent
if str(IT_DIR) not in sys.path:
    sys.path.insert(0, str(IT_DIR))

from bson import ObjectId  # noqa: E402
from pymongo import InsertOne, MongoClient  # noqa: E402
from pymongo.database import Database  # noqa: E402

from helper.collab_stack.identity import Actor, Directory, stable_oid  # noqa: E402

DEFAULT_SESSIONS = 200_000
DEFAULT_TEAMS = 2_000
DEFAULT_SEED = 1337
U_TEAMS = 20
U300_TEAMS = 300
OWNER_COUNT = 60
PEER_POOL = 5_000
CHUNK = 5_000
# Anchor for every timestamp: runs seeded months apart stay comparable.
EPOCH = datetime(2026, 9, 1, tzinfo=timezone.utc)
YEAR_MS = 365 * 24 * 3600 * 1000

USER_NAME = "CollabU"
USER300_NAME = "CollabU300"
ORG_NAME = "acme"


def team_id(index: int) -> str:
    return f"team-{index:05d}"


def all_team_id(org_id: str) -> str:
    return f"all_{org_id}"


@dataclass(frozen=True)
class SeedPlan:
    sessions: int = DEFAULT_SESSIONS
    teams: int = DEFAULT_TEAMS
    seed: int = DEFAULT_SEED
    messages_per_session: int = 2
    # Fraction of sessions that carry share rows; the rest are private to their owner.
    shared_fraction: float = 0.10

    @classmethod
    def from_env(cls, **override: Any) -> SeedPlan:
        base = cls(
            sessions=int(os.environ.get("COLLAB_SEED_SESSIONS", DEFAULT_SESSIONS)),
            teams=int(os.environ.get("COLLAB_SEED_TEAMS", DEFAULT_TEAMS)),
            seed=int(os.environ.get("COLLAB_SEED", DEFAULT_SEED)),
        )
        return cls(**{**asdict(base), **{k: v for k, v in override.items() if v is not None}})

    def validate(self) -> None:
        if self.sessions < 1:
            raise ValueError("sessions must be at least 1")
        if self.teams < U300_TEAMS:
            raise ValueError(f"teams must be at least {U300_TEAMS} so CollabU300 can be in {U300_TEAMS} of them")


@dataclass
class SeedReport:
    plan: SeedPlan
    org_id: str
    user: Actor
    user300: Actor
    owners: list[Actor]
    seconds: float = 0.0
    sessions: int = 0
    messages: int = 0
    shared_sessions: int = 0
    share_rows: int = 0
    team_rows: int = 0
    all_team_rows: int = 0
    user_rows: int = 0
    memberships: dict[str, list[str]] = field(default_factory=dict)

    def summary(self) -> dict[str, Any]:
        return {
            "plan": asdict(self.plan),
            "org_id": self.org_id,
            "user": self.user.user_id,
            "user300": self.user300.user_id,
            "seconds": round(self.seconds, 1),
            "sessions": self.sessions,
            "messages": self.messages,
            "shared_sessions": self.shared_sessions,
            "share_rows": self.share_rows,
            "team_rows": self.team_rows,
            "all_team_rows": self.all_team_rows,
            "user_rows": self.user_rows,
            "memberships": {k: len(v) for k, v in self.memberships.items()},
        }


def _oid(kind: int, seed: int, index: int) -> ObjectId:
    """Monotonic in ``index`` and unique per (kind, seed): the same plan gives the same ids."""
    return ObjectId(bytes([kind]) + (seed & 0xFFFFFF).to_bytes(3, "big") + index.to_bytes(8, "big"))


def session_oid(plan: SeedPlan, index: int) -> ObjectId:
    return _oid(0x6C, plan.seed, index)


def _ensure_user(directory: Directory, name: str, **kw: Any) -> Actor:
    key = f"{ORG_NAME}:{name}"
    if key in directory.actors:
        return directory.actors[key]
    try:
        return directory.user(name, ORG_NAME, **kw)
    except Exception:  # noqa: BLE001 - a rerun against the same database finds the user already there
        user_id = stable_oid(f"user:{ORG_NAME}:{name}")
        org_id = directory.orgs[ORG_NAME]
        actor = Actor(name=name, user_id=str(user_id), org_id=org_id, email=f"{name.lower()}@{ORG_NAME}.example.com", role=kw.get("role", "member"))
        directory.actors[key] = actor
        return actor


def make_directory(db: Database, org_id: str) -> Directory:
    return Directory(db, orgs={ORG_NAME: org_id})


def _share_rows(rng: random.Random, plan: SeedPlan, org_id: str, peers: list[ObjectId], u: Actor, u300: Actor, report: SeedReport) -> list[dict[str, Any]]:
    """The ``sharedWith`` rows of one shared session: user rows, team rows, sometimes the org-wide team."""
    rows: list[dict[str, Any]] = []
    added = EPOCH - timedelta(days=rng.randrange(1, 300))

    def level() -> str:
        return "write" if rng.random() < 0.25 else "read"

    def with_type(row: dict[str, Any], kind: str) -> dict[str, Any]:
        # A tenth of rows predate principalType; readers must not depend on it (74 section 1).
        if rng.random() > 0.10:
            row["principalType"] = kind
        return row

    if rng.random() < 0.55:
        for _ in range(1 + int(rng.random() < 0.4) + int(rng.random() < 0.15)):
            if rng.random() < 0.012:
                who = u.oid if rng.random() < 0.5 else u300.oid
            else:
                who = peers[rng.randrange(len(peers))]
            if any(r.get("userId") == who for r in rows):
                continue
            rows.append(with_type({"userId": who, "accessLevel": level(), "addedAt": added}, "user"))
            report.user_rows += 1
    if rng.random() < 0.45:
        for _ in range(1 + int(rng.random() < 0.35)):
            idx = int(plan.teams * rng.random() ** 2)
            tid = team_id(idx)
            if any(r.get("teamId") == tid for r in rows):
                continue
            rows.append(with_type({"teamId": tid, "accessLevel": level(), "addedAt": added}, "team"))
            report.team_rows += 1
    if rng.random() < 0.10:
        rows.append(with_type({"teamId": all_team_id(org_id), "accessLevel": "read", "addedAt": added}, "team"))
        report.all_team_rows += 1
    if not rows:
        rows.append(with_type({"userId": peers[rng.randrange(len(peers))], "accessLevel": "read", "addedAt": added}, "user"))
        report.user_rows += 1
    report.share_rows += len(rows)
    return rows


def _session_and_messages(
    rng: random.Random,
    plan: SeedPlan,
    index: int,
    org_oid: ObjectId,
    org_id: str,
    owners: list[Actor],
    peers: list[ObjectId],
    u: Actor,
    u300: Actor,
    report: SeedReport,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    roll = rng.random()
    owner = u if roll < 0.005 else u300 if roll < 0.010 else owners[rng.randrange(len(owners))]
    sid = session_oid(plan, index)
    last_ms = int(EPOCH.timestamp() * 1000) - rng.randrange(YEAR_MS)
    stamp = datetime.fromtimestamp(last_ms / 1000, tz=timezone.utc)
    shared = rng.random() < plan.shared_fraction
    rows = _share_rows(rng, plan, org_id, peers, u, u300, report) if shared else []
    if shared:
        report.shared_sessions += 1
    agent = rng.random() < 0.10
    doc: dict[str, Any] = {
        "_id": sid,
        "sessionType": "agent" if agent else "chat",
        "nextSeq": plan.messages_per_session,
        "userId": owner.oid,
        "orgId": org_oid,
        "initiator": owner.oid,
        "title": f"chat {index}",
        "isShared": shared,
        "sharedWith": rows,
        "archivedFor": [],
        "hiddenFor": [],
        "isDeleted": rng.random() < 0.02,
        "isArchived": rng.random() < 0.01,
        "status": "Complete",
        "lastActivityAt": last_ms,
        "rev": rng.randrange(0, 6),
        "aclVersion": 1,
        "schemaVersion": 1,
        "conversationErrors": [],
        "modelInfo": {"chatMode": "quick"},
        "createdAt": stamp,
        "updatedAt": stamp,
        "__v": 0,
    }
    if agent:
        doc["agentKey"] = "agent-1"
        doc["conversationSource"] = "agent_chat"
    if shared and rng.random() < 0.01:
        doc["hiddenFor"] = [peers[rng.randrange(len(peers))]]
    if shared and rng.random() < 0.01:
        doc["archivedFor"] = [peers[rng.randrange(len(peers))]]
    messages = []
    for seq in range(1, plan.messages_per_session + 1):
        question = seq % 2 == 1
        messages.append(
            {
                "_id": _oid(0x6D, plan.seed, index * 16 + seq),
                "sessionId": sid,
                "orgId": org_oid,
                "seq": seq,
                "schemaVersion": 1,
                "messageType": "user_query" if question else "bot_response",
                "content": f"{'question' if question else 'answer'} {index}.{seq}",
                "contentFormat": "MARKDOWN",
                "citations": [],
                "followUpQuestions": [],
                "feedback": [],
                **({"authorUserId": owner.oid} if question else {"requestedBy": owner.oid}),
                "createdAt": stamp + timedelta(seconds=seq),
                "updatedAt": stamp + timedelta(seconds=seq),
            }
        )
    return doc, messages


def register_teams(fake: Any, plan: SeedPlan, org_id: str, u: Actor, u300: Actor, extra_members: dict[str, list[str]] | None = None) -> dict[str, list[str]]:
    """Teams on the lane's fake of the Python service; returns who is in which team."""
    members: dict[str, dict[str, str]] = {team_id(i): {} for i in range(plan.teams)}
    members[all_team_id(org_id)] = {u.user_id: "member", u300.user_id: "member"}
    for i in range(U_TEAMS):
        members[team_id(i)][u.user_id] = "member"
    for i in range(U300_TEAMS):
        members[team_id(i)][u300.user_id] = "member"
    for user_id, teams in (extra_members or {}).items():
        for tid in teams:
            members.setdefault(tid, {})[user_id] = "member"
    if fake is not None:
        for tid, who in members.items():
            fake.add_team(org_id, tid, who, tid)
    out: dict[str, list[str]] = {}
    for tid, who in members.items():
        for user_id in who:
            out.setdefault(user_id, []).append(tid)
    return out


def seed(db: Database, fake: Any, org_id: str, plan: SeedPlan, log: Any = print) -> SeedReport:
    """Write the whole plan. ``fake`` is the lane's FakeBackend (None when the teams live in a real graph)."""
    plan.validate()
    started = time.monotonic()
    directory = make_directory(db, org_id)
    u = _ensure_user(directory, USER_NAME)
    u300 = _ensure_user(directory, USER300_NAME)
    owners = [_ensure_user(directory, f"CollabOwner{i:02d}") for i in range(OWNER_COUNT)]
    report = SeedReport(plan, org_id, u, u300, owners)
    rng = random.Random(plan.seed)
    peers = [stable_oid(f"peer:{plan.seed}:{i}") for i in range(PEER_POOL)]
    org_oid = ObjectId(org_id)
    sessions, messages = db["chatSessions"], db["chatSessionMessages"]
    for first in range(0, plan.sessions, CHUNK):
        batch_s: list[dict[str, Any]] = []
        batch_m: list[dict[str, Any]] = []
        for index in range(first, min(first + CHUNK, plan.sessions)):
            s_doc, m_docs = _session_and_messages(rng, plan, index, org_oid, org_id, owners, peers, u, u300, report)
            batch_s.append(s_doc)
            batch_m.extend(m_docs)
        sessions.insert_many(batch_s, ordered=False)
        messages.insert_many(batch_m, ordered=False)
        report.sessions += len(batch_s)
        report.messages += len(batch_m)
        if (first // CHUNK) % 10 == 0:
            log(f"  seeded {report.sessions:,}/{plan.sessions:,} sessions ({time.monotonic() - started:.0f}s)")
    report.memberships = register_teams(fake, plan, org_id, u, u300)
    report.seconds = time.monotonic() - started
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--sessions", type=int, default=None, help=f"default COLLAB_SEED_SESSIONS or {DEFAULT_SESSIONS}")
    parser.add_argument("--teams", type=int, default=None, help=f"default COLLAB_SEED_TEAMS or {DEFAULT_TEAMS}")
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--mongo-uri", help="seed this Mongo instead of booting a lane stack; teams are then only written to --output")
    parser.add_argument("--db", default=None, help="database name (with --mongo-uri)")
    parser.add_argument("--org-id", help="ObjectId of the org the users belong to (with --mongo-uri)")
    parser.add_argument("--output", type=Path, help="write the report (and with --mongo-uri, the team membership) here")
    args = parser.parse_args()
    plan = SeedPlan.from_env(sessions=args.sessions, teams=args.teams, seed=args.seed)

    if args.mongo_uri:
        if not args.org_id or not args.db:
            parser.error("--mongo-uri needs --db and --org-id")
        client = MongoClient(args.mongo_uri)
        report = seed(client[args.db], None, args.org_id, plan)
        membership = register_teams(None, plan, args.org_id, report.user, report.user300)
        payload = {**report.summary(), "team_membership": membership}
    else:
        from helper.collab_stack.stack import CollabStack, install_signal_cleanup

        stack = CollabStack()
        install_signal_cleanup(stack)
        try:
            stack.start()
            report = seed(stack.db, stack.fake, stack.roster.owner.org_id, plan)  # type: ignore[union-attr]
            payload = report.summary()
        finally:
            stack.stop()
    print(json.dumps(payload if not args.mongo_uri else {k: v for k, v in payload.items() if k != "team_membership"}, indent=2))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
