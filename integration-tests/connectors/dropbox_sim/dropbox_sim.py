"""The Dropbox simulator's control API, and the tenants the Dropbox suites sync.

One job: let a test build a Dropbox team or a personal account, change it the way people do (add, edit, move,
delete, share, suspend a member, ...), inject a fault, and read what a correct sync must contain. Calls the
simulator only, never Dropbox.
"""

from __future__ import annotations

import os
from typing import Any

import requests

DROPBOX_SIM_URL = os.getenv("DROPBOX_SIM_URL", "http://localhost:8109")
CLIENT_ID_ENV = "DROPBOX_SIM_CLIENT_ID"
CLIENT_SECRET_ENV = "DROPBOX_SIM_CLIENT_SECRET"
REFRESH_TOKEN_ENV = "DROPBOX_SIM_REFRESH_TOKEN"
DOMAIN = "dbx-it.test-pipeshub.com"
SYNC_TIMEOUT_SEC = int(os.getenv("DROPBOX_SIM_SYNC_TIMEOUT", "300"))

TEAM_SCOPES = ["account_info.read", "events.read", "file_requests.read", "files.content.read", "files.metadata.read",
               "groups.read", "members.read", "sharing.read", "sharing.write", "team_data.member",
               "team_data.team_space", "team_info.read"]
PERSONAL_SCOPES = ["account_info.read", "files.content.read", "files.metadata.read", "sharing.read", "sharing.write"]


class DropboxSim:
    def __init__(self, base_url: str = DROPBOX_SIM_URL, timeout: float = 60) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _call(self, method: str, path: str, body: Any = None) -> Any:
        resp = requests.request(method, f"{self.base_url}{path}", json=body, timeout=self.timeout)
        if resp.status_code >= 400:
            raise RuntimeError(f"Dropbox simulator {method} {path}: {resp.status_code} {resp.text[:400]}")
        return resp.json()

    def reachable(self) -> bool:
        try:
            return requests.get(f"{self.base_url}/_sim/health", timeout=5).ok
        except requests.RequestException:
            return False

    @property
    def token_url(self) -> str:
        return f"{self.base_url}/oauth2/token"

    def load(self, scenario: dict[str, Any], behaviours: dict[str, Any] | None = None) -> None:
        self._call("DELETE", "/_sim/dropbox/faults")
        self._call("DELETE", "/_sim/dropbox/behaviours")
        if behaviours:
            self._call("PUT", "/_sim/dropbox/behaviours", behaviours)
        self._call("PUT", "/_sim/dropbox/scenario", scenario)

    def ops(self, *ops: dict[str, Any]) -> list[dict[str, Any]]:
        return self._call("POST", "/_sim/dropbox/ops", list(ops))["results"]

    def expected(self) -> dict[str, Any]:
        return self._call("GET", "/_sim/dropbox/expected")

    def item(self, key: str) -> dict[str, Any]:
        return next(i for i in self.expected()["items"] if i["key"] == key)

    def item_id(self, key: str) -> str:
        return self.expected()["keys"][key]

    def key_id(self, key: str) -> str:
        """The Dropbox id behind a simulator key: ``group:eng`` (a group), ``ns:projects`` (a team or shared
        folder, whose id is also its record group's), ``member:bob`` (a team member) or an item key."""
        return self.expected()["keys"][key]

    def readers(self, key: str) -> set[str]:
        """The emails Dropbox lets read the item now (empty for a deleted item or an archived team folder's)."""
        item = self.item(key)
        return set() if item["deleted"] else set(item["readers"])

    def content(self, key: str) -> bytes:
        resp = requests.get(f"{self.base_url}/_sim/dropbox/content/{key}", timeout=self.timeout)
        resp.raise_for_status()
        return resp.content

    def faults(self, *rules: dict[str, Any]) -> None:
        self._call("PUT", "/_sim/dropbox/faults", list(rules))

    def fault_log(self) -> list[dict[str, Any]]:
        return self._call("GET", "/_sim/dropbox/faults/log")

    def clear_faults(self) -> None:
        self._call("DELETE", "/_sim/dropbox/faults")

    def expire_cursors(self, kind: str | None = None) -> None:
        self._call("POST", "/_sim/dropbox/cursors/expire", {"kind": kind} if kind else {})

    def revoke_refresh_token(self) -> None:
        self._call("POST", "/_sim/dropbox/refresh/revoke")

    def expire_access_tokens(self) -> None:
        """Every access token issued so far answers ``expired_access_token``: the next call must refresh."""
        self._call("POST", "/_sim/dropbox/tokens/expire")

    def problems(self) -> list[dict[str, Any]]:
        """Requests the simulator does not model, and answers that broke the Stone spec: both must stay empty."""
        return self._call("GET", "/_sim/dropbox/unmodeled") + self._call("GET", "/_sim/dropbox/spec-problems")


def oauth(kind: str, member: str) -> dict[str, Any]:
    return {"refresh_token": os.environ.get(REFRESH_TOKEN_ENV, "dbx-sim-refresh-token"), "kind": kind,
            "member": member, "scopes": TEAM_SCOPES if kind == "team" else PERSONAL_SCOPES}


def app() -> dict[str, str]:
    return {"key": os.environ.get(CLIENT_ID_ENV, "dbx-sim-app-key"),
            "secret": os.environ.get(CLIENT_SECRET_ENV, "dbx-sim-app-secret")}


def team_scenario(domain: str) -> dict[str, Any]:
    """alice (admin), bob, carol, dan; Engineering (bob, carol); the Projects team folder (Engineering, alice);
    Design, carol's shared folder with bob; Secret, nested in Projects without inheritance, dan only."""
    return {
        "seed": 21,
        "members": [{"key": k, "name": n, "email": f"dbx-it-{k}@{domain}", **({"role": "team_admin"} if k == "alice" else {})}
                    for k, n in (("alice", "Alice Admin"), ("bob", "Bob Builder"), ("carol", "Carol Chen"),
                                 ("dan", "Dan Diaz"))],
        "groups": [{"key": "eng", "name": "Engineering", "members": ["bob", "carol"]}],
        "team_folders": [{"key": "projects", "name": "Projects",
                          "members": {"group:eng": "editor", "user:alice": "owner"}}],
        "shared_folders": [{"key": "design", "name": "Design", "owner": "carol", "members": {"user:bob": "viewer"}}],
        "items": [{"key": "alice-notes", "in": "home:alice", "name": "notes.txt"},
                  {"key": "alice-plans", "in": "home:alice", "name": "Plans", "folder": True},
                  {"key": "alice-q3", "in": "alice-plans", "name": "q3.md"},
                  {"key": "bob-todo", "in": "home:bob", "name": "todo.txt"},
                  {"key": "p-spec", "in": "projects", "name": "spec.md"},
                  {"key": "p-internal", "in": "projects", "name": "Internal", "folder": True},
                  {"key": "d-logo", "in": "design", "name": "logo.txt"}],
        "nested": [{"key": "secret", "name": "Secret", "parent": "projects", "at": "p-internal", "inherit": False,
                    "members": {"user:dan": "viewer"}}],
        "nested_items": [{"key": "s-plan", "in": "secret", "name": "secret.txt"}],
        "file_shares": [{"item": "alice-notes", "principal": "user:bob", "access": "viewer"}],
        "oauth": oauth("team", "alice"),
        "app": app(),
    }


def personal_scenario(account_email: str, other_email: str) -> dict[str, Any]:
    """The connected account, with a folder another account shares with it (mounted)."""
    return {
        "seed": 22, "kind": "personal",
        "members": [{"key": "me", "name": "Personal Owner", "email": account_email},
                    {"key": "other", "name": "Other Person", "email": other_email}],
        "shared_folders": [{"key": "fromother", "name": "From Other", "owner": "other", "members": {"user:me": "viewer"}}],
        "items": [{"key": "me-one", "in": "home:me", "name": "one.txt"},
                  {"key": "me-dir", "in": "home:me", "name": "Work", "folder": True},
                  {"key": "me-two", "in": "me-dir", "name": "two.md"},
                  {"key": "other-shared", "in": "fromother", "name": "shared.txt"},
                  {"key": "other-private", "in": "home:other", "name": "private.txt"}],
        "oauth": oauth("user", "me"),
        "app": app(),
    }


def fault(route: str, status: int = 503, **match: Any) -> dict[str, Any]:
    return {"route": route, "effect": {"status": status}, **match}


def _member(key: str, name: str, domain: str, **extra: Any) -> dict[str, Any]:
    return {"key": key, "name": name, "email": f"dbx-it-{key}@{domain}", **extra}


def access_scenario(domain: str) -> dict[str, Any]:
    """Groups and people. alice (admin), bob, carol, dan, erin; Engineering (bob, carol) and Finance (dan); the
    Projects team folder (Engineering, alice) and the Finance team folder (Finance, alice); Design, carol's
    shared folder, with bob and gus, a collaborator from outside the team. erin is in no group and no folder."""
    return {
        "seed": 31,
        "members": [_member("alice", "Alice Admin", domain, role="team_admin"), _member("bob", "Bob Builder", domain),
                    _member("carol", "Carol Chen", domain), _member("dan", "Dan Diaz", domain),
                    _member("erin", "Erin Eze", domain)],
        "externals": [{"key": "gus", "name": "Gus Guest", "email": f"dbx-it-gus@partner.{domain}"}],
        "groups": [{"key": "eng", "name": "Engineering", "members": ["bob", "carol"]},
                   {"key": "fin", "name": "Finance", "members": ["dan"]}],
        "team_folders": [{"key": "projects", "name": "Projects", "members": {"group:eng": "editor", "user:alice": "owner"}},
                         {"key": "finance", "name": "Finance", "members": {"group:fin": "editor", "user:alice": "owner"}}],
        "shared_folders": [{"key": "design", "name": "Design", "owner": "carol",
                            "members": {"user:bob": "viewer", "ext:gus": "viewer"}}],
        "items": [{"key": "alice-notes", "in": "home:alice", "name": "notes.txt"},
                  {"key": "bob-todo", "in": "home:bob", "name": "todo.txt"},
                  {"key": "erin-cv", "in": "home:erin", "name": "cv.txt"},
                  {"key": "p-spec", "in": "projects", "name": "spec.md"},
                  {"key": "f-budget", "in": "finance", "name": "budget.txt"},
                  {"key": "d-logo", "in": "design", "name": "logo.txt"}],
        "oauth": oauth("team", "alice"),
        "app": app(),
    }


def team_member_root_scenario(domain: str) -> dict[str, Any]:
    """access_scenario plus a folder with a subfolder and files in the Projects team folder (Plans), for the team
    member root layout, where a folder can stop inheriting the team folder's members."""
    base = access_scenario(domain)
    base["seed"] = 37
    base["items"] += [{"key": "p-plans", "in": "projects", "name": "Plans", "folder": True},
                      {"key": "p-plans-a", "in": "p-plans", "name": "gantt.xlsx"},
                      {"key": "p-plans-sub", "in": "p-plans", "name": "Notes", "folder": True},
                      {"key": "p-plans-b", "in": "p-plans-sub", "name": "issues.txt"}]
    return base


def filter_scenario(domain: str) -> dict[str, Any]:
    """Files of every kind the sync filters tell apart, in a home, a team folder and a shared folder: extensions
    (txt, md, pdf, csv and none), and dates (modified 40 days ago and created 60 days ago, against today)."""
    base = team_scenario(domain)
    base["seed"] = 32
    base["items"] = [
        {"key": "a-note", "in": "home:alice", "name": "note.txt"},
        {"key": "a-plan", "in": "home:alice", "name": "plan.md"},
        {"key": "a-dir", "in": "home:alice", "name": "Reports", "folder": True},
        {"key": "a-report", "in": "a-dir", "name": "report.pdf", "modified_days_ago": 40, "created_days_ago": 60},
        {"key": "a-sheet", "in": "a-dir", "name": "sheet.csv", "modified_days_ago": 40},
        {"key": "a-readme", "in": "home:alice", "name": "README"},
        {"key": "p-spec", "in": "projects", "name": "spec.md"},
        {"key": "p-old", "in": "projects", "name": "old.txt", "modified_days_ago": 40, "created_days_ago": 60},
        {"key": "d-logo", "in": "design", "name": "logo.txt"},
    ]
    base["nested"], base["nested_items"], base["file_shares"] = [], [], []
    return base


def lifecycle_scenario(domain: str) -> dict[str, Any]:
    """team_scenario plus, in alice's home, two files with known text (for the vectors), a file to move, a folder
    tree to delete; and a second team folder (Archive) to archive and restore."""
    base = team_scenario(domain)
    base["seed"] = 33
    base["team_folders"].append({"key": "archive", "name": "Archive", "members": {"group:eng": "viewer",
                                                                                 "user:alice": "owner"}})
    base["items"] += [
        {"key": "l-trash", "in": "home:alice", "name": "trash-me.txt", "text": "Kestrel moulting log for the spring."},
        {"key": "c-text", "in": "home:alice", "name": "falconry.txt",
         "text": "Merlin hoods are stitched from calfskin. The Merlin hood ledger lists every fitting."},
        {"key": "l-mover", "in": "home:alice", "name": "mover.txt"},
        {"key": "l-doomed", "in": "home:alice", "name": "Doomed", "folder": True},
        {"key": "l-doomed-sub", "in": "l-doomed", "name": "Inner", "folder": True},
        {"key": "l-doomed-a", "in": "l-doomed", "name": "x.txt"},
        {"key": "l-doomed-b", "in": "l-doomed-sub", "name": "y.txt"},
        {"key": "ar-file", "in": "archive", "name": "minutes.txt"},
    ]
    return base


def matrix_scenario(domain: str) -> dict[str, Any]:
    """The smallest team the scenario matrix needs: alice (who owns the matrix's files) and bob (shared with)."""
    return {
        "seed": 35,
        "members": [_member("alice", "Alice Admin", domain, role="team_admin"), _member("bob", "Bob Builder", domain)],
        "items": [{"key": "mx-dir", "in": "home:alice", "name": "Matrix", "folder": True}],
        "oauth": oauth("team", "alice"),
        "app": app(),
    }


def extension_filter(extensions: list[str], operator: str = "in") -> dict[str, Any]:
    return {"file_extensions": {"operator": operator, "type": "multiselect", "value": extensions}}


def date_filter(name: str, operator: str, start_ms: int | None = None, end_ms: int | None = None) -> dict[str, Any]:
    """``name`` is ``modified`` or ``created``; ``operator`` is ``is_after``, ``is_before`` or ``is_between``."""
    return {name: {"operator": operator, "type": "datetime", "value": {"start": start_ms, "end": end_ms}}}
