# pyright: ignore-file

"""Instances the gitlab_sim tests start from (the shape is the connectors-simulator's GitLab scenario; a copy, with
this suite's addresses, of its harness/gitlab/scenarios.py ``scenario``).

One job: describe a realistic GitLab with the hard cases in known places: a private project with a member at every
role, a confidential issue with an assignee, an internal note that links an upload, a merge request, nested folders,
a project under a subgroup, a public project, an internal one, a blocked member, an awaiting member, an external
user and a user who has no PipesHub account.
"""

from __future__ import annotations

import copy
import os
from datetime import UTC, datetime
from typing import Any

# The sync account is the login the suite runs PipesHub as: the connector's creator is that PipesHub user, and a
# creator whose address is the sync account's own is linked to it (no second, unrelated person).
SYNC_EMAIL = os.getenv("PIPESHUB_TEST_USER_EMAIL", "pipeshub-it@example.com")
DOMAIN = "example.com"

USERNAMES = ["admin", "maya", "dev", "rita", "pat", "gus", "olga", "blocked", "nina", "ext"]
# Two more people only some tests load: an instance administrator and an auditor, neither a member of any project.
EXTRA_USERNAMES = ["root", "audit"]


def email_of(username: str) -> str:
    """The address a GitLab person has in this suite (also their PipesHub account's)."""
    return SYNC_EMAIL if username == "admin" else f"gl-{username}@{DOMAIN}"


# PipesHub accounts, made once per session before any connector: every GitLab person, and one who has no GitLab
# account (``nobody``), to see that a stranger reads nothing.
PEOPLE = [email_of(u) for u in USERNAMES + EXTRA_USERNAMES] + [f"gl-nobody@{DOMAIN}", f"gl-late@{DOMAIN}"]

OAUTH_APP = {"client_id": "app1", "client_secret": "s3cret", "redirect_uris": ["https://pipeshub.test/cb"]}


def _user(name: str, **kw: Any) -> dict[str, Any]:
    return {"username": name, "email": email_of(name), **kw}


def base_instance(**overrides: Any) -> dict[str, Any]:
    """The acme instance (see the module docstring). ``overrides`` replace top-level keys."""
    doc: dict[str, Any] = {
        # GitLab's clock starts at the wall clock, as a real instance's does: the connector compares the creation
        # time of what GitLab lists with its own clock when it looks for deleted issues.
        "now": datetime.now(UTC).isoformat(),
        "users": [_user(n, **({"state": "blocked"} if n == "blocked" else {}),
                        **({"external": True} if n == "ext" else {})) for n in USERNAMES],
        "groups": [{"key": "acme", "path": "acme", "members": {"admin": "owner", "maya": "maintainer"}},
                   {"key": "platform", "path": "platform", "parent": "acme", "members": {"olga": "guest"}}],
        "projects": [
            {"key": "widgets", "path": "widgets", "namespace": "acme",
             "members": {"dev": "developer", "rita": "reporter", "pat": "planner", "gus": "guest",
                         "blocked": "guest", "nina": {"access_level": 10, "state": "awaiting"}},
             "files": {"README.md": "# widgets\n", "src/app.py": "print('hi')\n",
                       "src/util/helpers.py": "def h():\n    return 1\n",
                       "docs/odd name (1) & caf\u00e9.md": "# odd\n", "docs/empty.md": ""},
             "uploads": [{"key": "shot", "filename": "shot.txt", "text": "a screenshot"},
                         {"key": "secret", "filename": "secret.txt", "text": "internal only"}],
             "issues": [
                 {"key": "open", "title": "Open issue", "author": "dev", "description": "see [shot]({{upload:shot}})",
                  "notes": [{"key": "pub", "author": "rita", "body": "a public comment"},
                            {"key": "int", "author": "rita", "body": "internal [secret]({{upload:secret}})",
                             "internal": True}]},
                 {"key": "conf", "title": "Confidential issue", "author": "dev", "confidential": True,
                  "assignees": ["gus"]},
                 {"key": "closed", "title": "Closed issue", "author": "rita", "state": "closed"}],
             "mrs": [{"key": "mr1", "title": "Add feature", "author": "dev",
                      "notes": [{"author": "rita", "body": "looks good"}],
                      "changes": [{"path": "src/new.py", "new": "x = 1\n", "new_file": True}]}]},
            {"key": "api", "path": "api", "namespace": "platform", "members": {"dev": "reporter"},
             "files": {"main.go": "package main\n"}, "issues": [{"key": "api1", "title": "API issue", "author": "dev"}]},
            {"key": "oss", "path": "oss", "namespace": "acme", "visibility": "public",
             "files": {"LICENSE.md": "MIT\n"}, "issues": [{"key": "oss1", "title": "Public issue", "author": "dev"}]},
            {"key": "intra", "path": "intranet", "namespace": "acme", "visibility": "internal",
             "files": {"notes.md": "n\n"}, "issues": [{"key": "intra1", "title": "Internal issue", "author": "dev"}]}],
        "tokens": [{"key": "t:admin", "user": "admin"}, {"key": "t:dev", "user": "dev"},
                   {"key": "t:gus", "user": "gus"},
                   {"key": "oa", "user": "admin", "kind": "oauth", "client_id": "app1"}],
        "oauth_apps": [OAUTH_APP],
        "sync_account": "admin"}
    doc.update(overrides)
    return copy.deepcopy(doc)


def issues(n: int, *, prefix: str = "Issue", project: str = "widgets") -> list[dict[str, Any]]:
    """``n`` add_issue operations: enough of them make a listing run to a second page."""
    return [{"op": "add_issue", "project": project, "title": f"{prefix} {i:03}", "author": "dev",
             "key": f"{prefix.lower()}{i}"} for i in range(n)]


def with_instance_admins(instance: dict[str, Any]) -> dict[str, Any]:
    """``instance`` with an administrator (``root``) and an auditor (``audit``) who are members of nothing."""
    instance["users"] += [_user("root", admin=True), _user("audit", auditor=True)]
    return instance
