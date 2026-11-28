# pyright: ignore-file

"""The Linear workspaces the linear_sim suite loads into the simulator (``PUT /_sim/linear/scenario``).

Every workspace is the organization ``acme``: the connector calls ``https://api.linear.app`` (GraphQL, OAuth token),
``https://linear.app`` (the authorize page) and ``https://uploads.linear.app`` (files), all answered by the overlay's TLS
front. Nothing here is a real workspace: people, addresses, keys and the OAuth client are made up.

Each person with an address has a PipesHub account with that address (made once per session), so the permission query can
be asked for them; ``OUTSIDER`` is a workspace member of PipesHub with no Linear account. The clock is the real time: a
PipesHub stack checkpoints its listings with its own clock.
"""

from __future__ import annotations

import os
from typing import Any

URL_KEY = "acme"
API_KEY = "lin_api_simulated_0000000000000000000000000000"
OUTSIDER = "outsider@acme.example"
# Where the platform's OAuth callback is served: FRONTEND_PUBLIC_URL of the stack plus the connector callback path.
REDIRECT_URI = os.getenv("PIPESHUB_BASE_URL", "http://localhost:25300").rstrip("/") + "/connectors/oauth/callback/Linear"
CLIENT = {"client_id": "lin-client", "client_secret": "lin-secret", "user": "sync", "redirect_uris": [REDIRECT_URI]}
PEOPLE_KEYS = ["sync", "alice", "bob", "carol", "gail", "sam", "rita", "dora", "erin"]


def email_of(key: str) -> str:
    return f"{key}@{URL_KEY}.example"


def _auth(extra: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"api_keys": {API_KEY: "sync", **{f"lin_api_{p}": p for p in PEOPLE_KEYS}},
            "oauth": {**CLIENT, **(extra or {})}}


def workspace() -> dict[str, Any]:
    """Every team visibility, sub-teams, a guest, a suspended person, an app user, issues with parents, threads of
    comments, links, documents, uploads, projects across teams (one reaching a private team), milestones, updates, trash
    and archive."""
    return {
        "seed": 11, "url_key": URL_KEY, "org_name": "Acme", "viewer": "sync", "clock": "wall", "auth": _auth(),
        "users": [
            {"key": "sync", "name": "Sync Admin", "admin": True},
            {"key": "alice", "name": "Alice Archer"},
            {"key": "bob", "name": "Bob Baker"},
            {"key": "carol", "name": "Carol Chen"},
            {"key": "gail", "name": "Gail Guest", "guest": True},
            {"key": "sam", "name": "Sam Suspended", "active": False},
            {"key": "rita", "name": "Rita Reyes"},
            {"key": "dora", "name": "Dora Dunn", "app": True},
            {"key": "erin", "name": "Erin Extra"},
        ],
        "teams": [
            {"key": "ENG", "name": "Engineering", "members": ["sync", "alice", "bob", "sam"]},
            {"key": "PORTAL", "name": "Customer portal", "members": ["sync", "gail"]},
            {"key": "SEC", "name": "Security", "visibility": "private", "members": ["sync", "alice"]},
            {"key": "SECOPS", "name": "Security operations", "visibility": "restricted", "parent": "SEC",
             "members": ["sync", "rita"]},
            {"key": "ENGWEB", "name": "Web", "parent": "ENG", "members": ["sync", "carol"]},
            {"key": "ENGHR", "name": "People ops", "visibility": "private", "parent": "ENG",
             "members": ["sync", "carol"]},
            {"key": "OPS", "name": "Operations", "members": ["sync", "bob"]},
        ],
        "labels": [{"key": "bug", "name": "Bug"}, {"key": "feature", "name": "Feature", "team": "ENG"}],
        "uploads": [
            {"key": "spec", "name": "spec.pdf", "size": 4000, "mime": "application/pdf"},
            {"key": "logo", "name": "logo.png", "size": 3000, "mime": "image/png"},
            {"key": "notes", "name": "notes.txt", "size": 500, "mime": "text/plain"},
            {"key": "odd", "name": "Q3-report-éè-日本.txt", "size": 800, "mime": "text/plain"},
            {"key": "empty", "name": "empty.txt", "size": 0, "mime": "text/plain"},
        ],
        "issues": [
            {"key": "eng-1", "team": "ENG", "title": "Ship the importer", "creator": "alice", "assignee": "bob",
             "labels": ["bug", "feature"], "priority": 2, "state": "in_progress",
             "description": "See the spec: [spec.pdf]({{upload:spec}}) and ![logo]({{upload:logo}}) and [odd]({{upload:odd}})",
             "comments": [
                 {"key": "c1", "user": "alice", "body": "Started. Notes: [notes]({{upload:notes}})"},
                 {"key": "c1r", "user": "bob", "body": "Reply in thread", "parent": "c1"},
                 {"key": "c2", "user": "bob", "body": "Second thread"}],
             "attachments": [{"key": "a1", "title": "PR 12", "subtitle": "open", "url": "https://github.example/pr/12"}],
             "documents": [{"key": "d1", "title": "Design notes", "content": "# Design\n\nBody", "creator": "alice"}],
             "relations": [{"type": "blocks", "to": "eng-2"}]},
            {"key": "eng-2", "team": "ENG", "title": "Résumé – 日本 \U0001f389 import", "creator": "bob",
             "state": "todo"},
            {"key": "eng-3", "team": "ENG", "title": "Sub-issue of the importer", "parent": "eng-1", "creator": "bob"},
            {"key": "eng-4", "team": "ENG", "title": "Done thing", "state": "done", "creator": "alice",
             "description": "empty file: [e]({{upload:empty}})"},
            {"key": "web-1", "team": "ENGWEB", "title": "Web page", "creator": "carol"},
            {"key": "hr-1", "team": "ENGHR", "title": "Review cycle", "creator": "carol"},
            {"key": "portal-1", "team": "PORTAL", "title": "Portal login", "creator": "gail"},
            {"key": "sec-1", "team": "SEC", "title": "Pen test scope", "creator": "alice"},
            {"key": "secops-1", "team": "SECOPS", "title": "Rotate on-call keys", "creator": "rita",
             "comments": [{"key": "sc1", "user": "rita", "body": "keys rotated"}]},
            {"key": "ops-1", "team": "OPS", "title": "Patch window", "creator": "bob",
             "attachments": [{"key": "a2", "title": "Runbook", "url": "https://wiki.example/runbook"}]},
            {"key": "ops-trash", "team": "OPS", "title": "Trashed", "creator": "bob"},
            {"key": "ops-archived", "team": "OPS", "title": "Archived", "creator": "bob"},
        ],
        "projects": [
            {"key": "p1", "name": "Launch", "teams": ["ENG", "OPS"], "lead_team": "ENG", "lead": "alice",
             "creator": "alice", "content": "Launch plan. File: [spec]({{upload:spec}})", "issues": ["eng-1", "ops-1"],
             "documents": [{"key": "pd1", "title": "Launch brief", "content": "brief", "creator": "alice"}],
             "links": [{"key": "l1", "label": "Roadmap", "url": "https://roadmap.example/launch"}],
             "milestones": [{"key": "m1", "name": "Beta", "target_date": "2026-11-01"},
                            {"key": "m2", "name": "GA", "target_date": "2026-12-01"}],
             "updates": [{"key": "u1", "user": "alice", "body": "On track", "health": "onTrack",
                          "comments": [{"key": "uc1", "user": "bob", "body": "Nice"}]}],
             "comments": [{"key": "pc1", "user": "bob", "body": "Project comment"}]},
            {"key": "p2", "name": "Hardening", "teams": ["SEC"], "creator": "alice", "content": "private project"},
            {"key": "p3", "name": "Shared with security", "teams": ["ENG", "SEC"], "creator": "alice",
             "content": "visible to ENG and SEC members"},
            {"key": "p4", "name": "Portal revamp", "teams": ["PORTAL"], "creator": "gail", "content": "portal"},
            {"key": "p5", "name": "Old", "teams": ["OPS"], "creator": "bob", "content": "gone"},
        ],
        "ops": [
            {"op": "trash_issue", "issue": "ops-trash"},
            {"op": "archive_issue", "issue": "ops-archived"},
            {"op": "trash_project", "project": "p5"},
        ],
    }


def small() -> dict[str, Any]:
    """Two public teams, three people, six issues, one link, one comment, one project: for the fault and token cases, where
    a sync must be short."""
    return {
        "seed": 3, "url_key": URL_KEY, "org_name": "Acme", "viewer": "sync", "clock": "wall", "auth": _auth(),
        "users": [{"key": "sync", "name": "Sync Admin", "admin": True}, {"key": "alice", "name": "Alice Archer"},
                  {"key": "bob", "name": "Bob Baker"}],
        "teams": [{"key": "ENG", "name": "Engineering", "members": ["sync", "alice"]},
                  {"key": "OPS", "name": "Operations", "members": ["sync", "bob"]}],
        "uploads": [{"key": "spec", "name": "spec.pdf", "size": 1500, "mime": "application/pdf"}],
        "issues": [
            {"key": "e1", "team": "ENG", "title": "First", "creator": "alice", "description": "[s]({{upload:spec}})",
             "comments": [{"key": "ec1", "user": "alice", "body": "hello"}],
             "attachments": [{"key": "ea1", "title": "PR", "url": "https://github.example/pr/1"}]},
            {"key": "e2", "team": "ENG", "title": "Second", "creator": "alice"},
            {"key": "e3", "team": "ENG", "title": "Third", "creator": "alice", "parent": "e1"},
            {"key": "o1", "team": "OPS", "title": "Ops one", "creator": "bob"},
            {"key": "o2", "team": "OPS", "title": "Ops two", "creator": "bob"},
            {"key": "o3", "team": "OPS", "title": "Ops three", "creator": "bob"}],
        "projects": [{"key": "pr1", "name": "Plan", "teams": ["ENG"], "creator": "alice", "content": "plan",
                      "links": [{"key": "pl1", "label": "Doc", "url": "https://x.example/doc"}]}],
    }


def many(n: int, other: int = 5) -> dict[str, Any]:
    """``n`` issues in one public team (BIG) and ``other`` in a second (TWO): for paging."""
    spec = small()
    spec["teams"] = [{"key": "BIG", "name": "Big", "members": ["sync", "alice"]},
                     {"key": "TWO", "name": "Two", "members": ["sync", "bob"]}]
    spec["projects"] = []
    spec["uploads"] = []
    spec["issues"] = ([{"key": f"i{i}", "team": "BIG", "title": f"Issue {i}", "creator": "alice",
                        "comments": [{"user": "alice", "body": f"comment {i}"}] if i % 5 == 0 else []}
                       for i in range(n)]
                      + [{"key": f"t{i}", "team": "TWO", "title": f"Two {i}", "creator": "bob"} for i in range(other)])
    return spec


def _emails() -> list[str]:
    docs = (workspace(), small(), many(3))
    return sorted({email_of(u["key"]) for d in docs for u in d["users"]})


# Everyone who may read something in any workspace: each has a PipesHub account (made once per session).
PEOPLE = [*_emails(), OUTSIDER]
