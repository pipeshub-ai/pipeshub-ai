# pyright: ignore-file

"""The GitHub worlds the github_teams_sim suite loads into the simulator (``PUT /_sim/github/scenario``).

Every world is the organisation ``acme``. The connector calls ``https://api.github.com`` and ``https://github.com``
(and the signed storage host an upload redirects to), all answered by the overlay's TLS front. Nothing here is a real
account: people, addresses, tokens and the OAuth app are made up.

Each person with an address has a PipesHub account with that address (made once per session), so the permission query
can be asked for them. ``CAROL`` has no GitHub account; ``CLIENT_EMAIL`` is the PipesHub account the suite's API client
signs in as: it creates every connector, so it is the "creator" whose reach follows the GitHub account it connected.
"""

from __future__ import annotations

import os
import time
from typing import Any

ADMIN = "admin@acme.com"          # GitHub login admin: the owner and the sync account
ALICE = "alice@acme.com"          # member, in team core (write)
BOB = "bob@acme.com"              # member, no grant
OLGA = "olga@contractor.io"       # outside collaborator (read)
CAROL = "carol@acme.com"          # a PipesHub user with no GitHub account
DAVE = "dave@acme.com"            # a GitHub account that is in no organisation
CLIENT_EMAIL = os.getenv("PIPESHUB_TEST_USER_EMAIL", "pipeshub-it@example.com")
PEOPLE = [ADMIN, ALICE, BOB, OLGA, CAROL, DAVE, CLIENT_EMAIL]
LOGIN = {ADMIN: "admin", ALICE: "alice", BOB: "bob", OLGA: "olga", DAVE: "dave"}

ADMIN_TOKEN = "ghp_sim_admin"
OAUTH_APP = {"client_id": "pipeshub-gh-client", "client_secret": "pipeshub-gh-secret", "user": "admin",
             "redirect_uri": "http://localhost:25400/connectors/oauth/callback/Github%20Teams"}

API_HOST = "api.github.com"
WEB_HOST = "github.com"
STORAGE_HOST = "private-user-images.githubusercontent.com"
HOSTS = (API_HOST, WEB_HOST, STORAGE_HOST)
# What the connector may call: nothing else is aliased to the front.
NOT_ALIASED = "uploads.github.com"


def now() -> int:
    """The world's clock starts two hours behind the wall clock: everything is in the past for the connector, and every
    sync adds a few minutes to it (``GithubWorld.sync``), so it never runs ahead of the real time."""
    return int(time.time()) - 7200


def acme(*, repos: int = 1, **extra: Any) -> dict[str, Any]:
    """The standard organisation: an owner (the sync account), two members, an outside collaborator, a GitHub account
    outside the organisation, a team, and private repository ``widgets`` (more with ``repos``: ``gadgets``...).

    Keys: admin, alice, bob, olga, dave, acme, core, widgets.
    """
    spec: dict[str, Any] = {
        "now": now(),
        "users": [{"login": "admin", "email": ADMIN}, {"login": "alice", "email": ALICE},
                  {"login": "bob", "email": BOB}, {"login": "olga", "email": OLGA}, {"login": "dave", "email": DAVE}],
        "orgs": [{"login": "acme", "owners": ["admin"], "members": ["alice", "bob"], "base_permission": None,
                  "teams": [{"key": "core", "slug": "core", "members": ["alice"]}]}],
        "repos": [{"key": "widgets", "owner": "acme", "name": "widgets",
                   "files": {"README.md": "# widgets\n", "src/app.py": "print('hi')\n"},
                   "teams": {"core": "write"}, "collaborators": {"olga": "read"}}],
        "tokens": [{"user": "alice", "value": "ghp_sim_alice", "scopes": ["repo", "read:org", "user:email"]},
                   {"user": "bob", "value": "ghp_sim_bob", "scopes": ["repo", "read:org", "user:email"]}],
        "sync_account": "admin",
        "oauth_app": dict(OAUTH_APP),
    }
    if repos > 1:
        spec["repos"].append({"key": "gadgets", "owner": "acme", "name": "gadgets", "files": {"g.py": "g = 1\n"},
                              "teams": {}, "collaborators": {"bob": "read"}})
    spec.update(extra)
    return spec


def issues(n: int, prefix: str = "Issue", repo: str = "widgets") -> list[dict[str, Any]]:
    """``n`` ops adding issues titled ``<prefix> 000``..."""
    return [{"op": "add_issue", "repo": repo, "title": f"{prefix} {i:03}", "key": f"{prefix.lower()}{i}"}
            for i in range(n)]


def rich(spec: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Operations that give ``widgets`` one of each object: issues with comments, sub-issues, a blocking link, a pull
    request with files, a review and a review comment."""
    return [
        {"op": "add_issue", "repo": "widgets", "title": "Login fails", "body": "Steps: click login", "key": "login",
         "comments": [{"user": "alice", "body": "seen it too"}, {"user": "bob", "body": "same here"}]},
        {"op": "add_issue", "repo": "widgets", "title": "Epic", "key": "epic"},
        {"op": "add_issue", "repo": "widgets", "title": "Task", "key": "task", "parent": "epic"},
        {"op": "add_issue", "repo": "widgets", "title": "Blocked", "key": "blocked"},
        {"op": "add_issue", "repo": "widgets", "title": "Blocker", "key": "blocker", "blocking": ["blocked"]},
        {"op": "add_pull", "repo": "widgets", "title": "Fix login", "changes": {"src/login.py": "ok\n"}, "key": "fix"},
        {"op": "commit", "repo": "widgets", "changes": {"docs/guide.md": "# guide\n", "docs/a/b/deep.txt": "deep\n",
                                                          "Makefile": "all:\n"}},
    ]
