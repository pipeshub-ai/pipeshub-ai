"""An in-memory GitLab that answers the REST v4 and GraphQL calls the connector makes.

The connector talks to GitLab through two real HTTP stacks: python-gitlab on
``requests`` for REST, and ``httpx`` for GraphQL, images and attachments. Both
are pointed at this one fake (a ``requests`` transport adapter and an
``httpx.MockTransport``), so the SDK, its pagination, retries and rate-limit
handling, and our client wrappers all run for real. Only the network is gone.

The fake models the parts of GitLab whose semantics the connector depends on:
offset pagination with ``Link`` headers, keyset pagination for ``/projects``,
inherited membership (``/members/all`` walks ancestor groups), confidential
issues, a commit history per repository with a real ``compare`` and a
cursor-paginated ``paginatedTree``. Faults (status codes, rate limits,
failures on the Nth call) are injected per route.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import math
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, unquote, urlencode, urlsplit

import httpx
import requests
from requests.adapters import BaseAdapter
from requests.structures import CaseInsensitiveDict

if TYPE_CHECKING:
    from collections.abc import Callable

BASE_URL = "https://gitlab.example.com"

GUEST, PLANNER, REPORTER, DEVELOPER, MAINTAINER, OWNER = 10, 15, 20, 30, 40, 50
MINIMAL = 5


def parse_time(value: str) -> datetime:
    value = value.strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def blob_sha(content: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(content) + content).hexdigest()


@dataclass
class GLUser:
    id: int
    username: str
    name: str
    public_email: str | None = None
    is_admin: bool = False
    is_auditor: bool = False
    state: str = "active"

    def to_json(self, *, with_roles: bool = False) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id, "username": self.username, "name": self.name,
            "state": self.state, "public_email": self.public_email or "",
            "web_url": f"{BASE_URL}/{self.username}",
        }
        # GitLab omits the role flags for callers who do not hold them.
        if with_roles and self.is_admin:
            out["is_admin"] = True
        if with_roles and self.is_auditor:
            out["is_auditor"] = True
        return out


@dataclass
class GLGroup:
    id: int
    full_path: str
    name: str
    members: dict[int, int] = field(default_factory=dict)

    @property
    def parent_path(self) -> str | None:
        return self.full_path.rpartition("/")[0] or None

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id, "name": self.name, "path": self.full_path.rsplit("/", 1)[-1],
            "full_path": self.full_path, "full_name": self.full_path.replace("/", " / "),
            "web_url": f"{BASE_URL}/groups/{self.full_path}",
        }


@dataclass
class Commit:
    sha: str
    files: dict[str, bytes]
    committed_date: str


@dataclass
class GLProject:
    id: int
    path_with_namespace: str
    namespace_kind: str = "group"
    default_branch: str = "main"
    visibility: str = "private"
    archived: bool = False
    members: dict[int, int] = field(default_factory=dict)
    issues: list[dict[str, Any]] = field(default_factory=list)
    merge_requests: list[dict[str, Any]] = field(default_factory=list)
    notes: dict[tuple[str, int], list[dict[str, Any]]] = field(default_factory=dict)
    commits: list[Commit] = field(default_factory=list)
    compare_overflow: bool = False
    report_renames: bool = True

    @property
    def namespace_path(self) -> str:
        return self.path_with_namespace.rpartition("/")[0]

    @property
    def name(self) -> str:
        return self.path_with_namespace.rsplit("/", 1)[-1]

    @property
    def head(self) -> Commit | None:
        return self.commits[-1] if self.commits else None

    def web_url(self) -> str:
        return f"{BASE_URL}/{self.path_with_namespace}"

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id, "name": self.name, "path": self.name,
            "path_with_namespace": self.path_with_namespace,
            "name_with_namespace": self.path_with_namespace.replace("/", " / "),
            "namespace": {"id": 0, "full_path": self.namespace_path, "kind": self.namespace_kind},
            "default_branch": self.default_branch, "visibility": self.visibility,
            "archived": self.archived, "web_url": self.web_url(),
        }


@dataclass
class Fault:
    method: str
    pattern: re.Pattern[str]
    status: int
    times: int | None
    skip: int
    headers: dict[str, str]
    body: Any
    body_contains: bytes = b""
    raw: bytes | None = None
    seen: int = 0
    fired: int = 0


@dataclass
class SeenRequest:
    method: str
    path: str
    params: dict[str, str]
    headers: dict[str, str]
    body: bytes
    host: str = ""

    @property
    def token(self) -> str | None:
        if self.headers.get("private-token"):
            return self.headers["private-token"]
        auth = self.headers.get("authorization", "")
        return auth[len("Bearer "):] if auth.startswith("Bearer ") else None


@dataclass
class Reply:
    status: int
    body: bytes
    headers: dict[str, str]


def _json(payload: object, status: int = 200, headers: dict[str, str] | None = None) -> Reply:
    return Reply(status, json.dumps(payload).encode(), {"Content-Type": "application/json", **(headers or {})})


def _not_found(what: str = "Not found") -> Reply:
    return _json({"message": f"404 {what}"}, 404)


class FakeGitLab:
    """GitLab state plus the request router. Thread-safe: python-gitlab calls arrive on worker threads."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.users: dict[int, GLUser] = {}
        self.groups: dict[str, GLGroup] = {}
        self.projects: dict[int, GLProject] = {}
        self.uploads: dict[str, bytes] = {}
        self.valid_tokens: set[str] = {"token-1"}
        self.current_user_by_token: dict[str, int] = {}
        self.max_per_page = 100
        self.graphql_page_size = 100
        self.requests: list[SeenRequest] = []
        self._faults: list[Fault] = []
        self._holds: list[tuple[str, re.Pattern[str], Callable[[], bool]]] = []
        self._next_id = 1000

    # ------------------------------------------------------------------ setup

    def _id(self) -> int:
        self._next_id += 1
        return self._next_id

    def add_user(self, user_id: int, username: str, *, email: str | None = None, **kw: object) -> GLUser:
        user = GLUser(user_id, username, kw.pop("name", username.title()), public_email=email, **kw)
        self.users[user_id] = user
        return user

    def token_for(self, user_id: int, token: str = "token-1") -> None:
        self.valid_tokens.add(token)
        self.current_user_by_token[token] = user_id

    def add_group(self, full_path: str, members: dict[int, int] | None = None) -> GLGroup:
        group = GLGroup(self._id(), full_path, full_path.rsplit("/", 1)[-1], dict(members or {}))
        self.groups[full_path] = group
        return group

    def add_project(self, project_id: int, path_with_namespace: str, *, members: dict[int, int] | None = None,
                    files: dict[str, str | bytes] | None = None, **kw: object) -> GLProject:
        project = GLProject(project_id, path_with_namespace, members=dict(members or {}), **kw)
        self.projects[project_id] = project
        if files is not None:
            self.commit(project_id, files)
        return project

    def commit(self, project_id: int, files: dict[str, str | bytes], *, when: str = "2026-09-01T10:00:00Z") -> str:
        project = self.projects[project_id]
        encoded = {p: (c.encode() if isinstance(c, str) else c) for p, c in files.items()}
        sha = hashlib.sha1(f"{project_id}:{len(project.commits)}:{sorted(encoded.items())}".encode()).hexdigest()
        project.commits.append(Commit(sha, encoded, when))
        return sha

    def change_files(self, project_id: int, *, write: dict[str, str] | None = None, delete: tuple[str, ...] = (),
                     rename: dict[str, str] | None = None, when: str = "2026-09-02T10:00:00Z") -> str:
        head = self.projects[project_id].head
        files = dict(head.files) if head else {}
        for old, new in (rename or {}).items():
            files[new] = files.pop(old)
        for path in delete:
            files.pop(path)
        for path, content in (write or {}).items():
            files[path] = content.encode()
        return self.commit(project_id, files, when=when)

    def add_issue(self, project_id: int, iid: int, title: str, updated_at: str, *, confidential: bool = False,
                  author: int | None = None, assignees: tuple[int, ...] = (), description: str = "",
                  created_at: str = "2026-08-01T09:00:00Z", notes: list[dict[str, Any]] | None = None,
                  state: str = "opened") -> dict[str, Any]:
        project = self.projects[project_id]
        issue = {
            "id": project_id * 1000 + iid, "iid": iid, "project_id": project_id, "title": title,
            "description": description, "state": state, "created_at": created_at, "updated_at": updated_at,
            "labels": [], "confidential": confidential, "issue_type": "issue",
            "author": {"id": author, "username": self.users[author].username} if author else None,
            "assignees": [{"id": a, "username": self.users[a].username} for a in assignees],
            "web_url": f"{project.web_url()}/-/issues/{iid}",
        }
        project.issues = [i for i in project.issues if i["iid"] != iid] + [issue]
        project.notes[("issues", iid)] = list(notes or [])
        return issue

    def add_merge_request(self, project_id: int, iid: int, title: str, updated_at: str, *, description: str = "",
                          notes: list[dict[str, Any]] | None = None, changes: list[dict[str, Any]] | None = None,
                          commits: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        project = self.projects[project_id]
        mr = {
            "id": project_id * 100000 + iid, "iid": iid, "project_id": project_id, "title": title,
            "description": description, "state": "opened", "created_at": "2026-08-01T09:00:00Z",
            "updated_at": updated_at, "labels": ["backend"], "assignees": [], "reviewers": [],
            "merged_by": None, "merge_status": "can_be_merged", "sha": project.head.sha if project.head else "0" * 40,
            "web_url": f"{project.web_url()}/-/merge_requests/{iid}",
            "_changes": list(changes or []), "_commits": list(commits or []),
        }
        project.merge_requests = [m for m in project.merge_requests if m["iid"] != iid] + [mr]
        project.notes[("merge_requests", iid)] = list(notes or [])
        return mr

    # ------------------------------------------------------------------ faults

    def fail(self, method: str, path_regex: str, status: int, *, times: int | None = None, skip: int = 0,
             headers: dict[str, str] | None = None, body: object = None, body_contains: str = "",
             raw: bytes | None = None) -> Fault:
        """Answer matching requests with ``status``.

        ``skip`` lets the first N matching requests through; ``times`` bounds how
        many are failed (``None`` = every one after the skipped ones). ``raw``
        sends those bytes as the body instead of JSON, e.g. a truncated response.
        """
        fault = Fault(method.upper(), re.compile(path_regex), status, times, skip, dict(headers or {}),
                      body if body is not None else {"message": f"{status} injected"}, body_contains.encode(), raw)
        with self._lock:
            self._faults.append(fault)
        return fault

    def hold(self, method: str, path_regex: str, until: Callable[[], bool]) -> None:
        """Keep matching requests in flight until ``until()`` is true (at most 10s), then answer normally."""
        self._holds.append((method.upper(), re.compile(path_regex), until))

    def clear_faults(self) -> None:
        with self._lock:
            self._faults.clear()

    def calls(self, method: str, path_regex: str) -> list[SeenRequest]:
        pattern = re.compile(path_regex)
        return [r for r in self.requests if r.method == method.upper() and pattern.search(r.path)]

    # ------------------------------------------------------------------ transports

    def requests_adapter(self) -> BaseAdapter:
        return _RequestsAdapter(self)

    def httpx_transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            raw_path = request.url.raw_path.decode().split("?", 1)[0]
            reply = self.handle(request.method, raw_path, request.url.query.decode(), dict(request.headers),
                                request.content, host=request.url.host)
            return httpx.Response(reply.status, headers=reply.headers, content=reply.body)
        return httpx.MockTransport(handler)

    # ------------------------------------------------------------------ routing

    def handle(self, method: str, raw_path: str, query: str, headers: dict[str, str], body: bytes,
               host: str = "gitlab.example.com") -> Reply:
        for hold_method, pattern, until in self._holds:
            if hold_method == method.upper() and pattern.search(unquote(raw_path)):
                deadline = time.monotonic() + 10
                while not until() and time.monotonic() < deadline:
                    time.sleep(0.005)
        with self._lock:
            params = {k: v[-1] for k, v in parse_qs(query, keep_blank_values=True).items()}
            segs = [unquote(s) for s in raw_path.split("/") if s]
            path = "/" + "/".join(segs)
            seen = SeenRequest(method.upper(), path, params, {k.lower(): v for k, v in headers.items()}, body or b"", host)
            self.requests.append(seen)
            for fault in self._faults:
                if fault.method != seen.method or not fault.pattern.search(path):
                    continue
                if fault.body_contains and fault.body_contains not in seen.body:
                    continue
                fault.seen += 1
                if fault.seen <= fault.skip:
                    continue
                if fault.times is not None and fault.fired >= fault.times:
                    continue
                fault.fired += 1
                if fault.raw is not None:
                    return Reply(fault.status, fault.raw, {"Content-Type": "application/json", **fault.headers})
                return _json(fault.body, fault.status, fault.headers)
            token = seen.token
            if host != "gitlab.example.com" or token not in self.valid_tokens:
                return _json({"message": "401 Unauthorized"}, 401)
            try:
                return self._route(seen, segs, raw_path, query)
            except KeyError as e:
                return _not_found(f"{e} Not Found")

    def _viewer(self, req: SeenRequest) -> GLUser | None:
        uid = self.current_user_by_token.get(req.token or "")
        return self.users.get(uid) if uid is not None else None

    def _route(self, req: SeenRequest, segs: list[str], raw_path: str, query: str) -> Reply:
        if segs[:2] == ["api", "graphql"] and req.method == "POST":
            return self._graphql(req)
        if segs[:2] != ["api", "v4"]:
            return _not_found()
        rest = segs[2:]
        if rest == ["user"]:
            viewer = self._viewer(req)
            return _json(viewer.to_json(with_roles=True)) if viewer else _not_found("User")
        if len(rest) == 2 and rest[0] == "users":
            user = self.users.get(int(rest[1]))
            return _json(user.to_json()) if user else _not_found("User")
        if rest[:1] == ["groups"]:
            return self._groups(req, rest[1:], raw_path, query)
        if rest[:1] == ["projects"]:
            return self._projects(req, rest[1:], raw_path, query)
        return _not_found()

    # -- pagination

    def _offset_page(self, items: list[Any], req: SeenRequest, raw_path: str) -> Reply:
        per_page = min(int(req.params.get("per_page") or 20), self.max_per_page)
        page = max(1, int(req.params.get("page") or 1))
        total_pages = max(1, math.ceil(len(items) / per_page))
        chunk = items[(page - 1) * per_page: page * per_page]
        headers = {"X-Page": str(page), "X-Per-Page": str(per_page), "X-Total": str(len(items)),
                   "X-Total-Pages": str(total_pages)}
        if page < total_pages:
            headers["X-Next-Page"] = str(page + 1)
            nxt = {**req.params, "page": str(page + 1), "per_page": str(per_page)}
            headers["Link"] = f'<{BASE_URL}{raw_path}?{urlencode(nxt)}>; rel="next"'
        return _json(chunk, headers=headers)

    def _keyset_page(self, items: list[dict[str, Any]], req: SeenRequest, raw_path: str) -> Reply:
        per_page = min(int(req.params.get("per_page") or 20), self.max_per_page)
        after = int(req.params.get("id_after") or 0)
        remaining = [i for i in sorted(items, key=lambda i: i["id"]) if i["id"] > after]
        chunk = remaining[:per_page]
        headers: dict[str, str] = {}
        if len(remaining) > per_page:
            nxt = {k: v for k, v in req.params.items() if k != "page"}
            nxt["id_after"] = str(chunk[-1]["id"])
            headers["Link"] = f'<{BASE_URL}{raw_path}?{urlencode(nxt)}>; rel="next"'
        return _json(chunk, headers=headers)

    # -- membership

    def _ancestors(self, path: str) -> list[GLGroup]:
        out: list[GLGroup] = []
        parts = path.split("/")
        for i in range(1, len(parts) + 1):
            group = self.groups.get("/".join(parts[:i]))
            if group:
                out.append(group)
        return out

    def group_members_all(self, group: GLGroup) -> dict[int, int]:
        levels: dict[int, int] = {}
        for g in self._ancestors(group.full_path):
            for uid, level in g.members.items():
                levels[uid] = max(level, levels.get(uid, 0))
        return levels

    def project_members_all(self, project: GLProject) -> dict[int, int]:
        levels: dict[int, int] = {}
        for g in self._ancestors(project.namespace_path):
            for uid, level in g.members.items():
                levels[uid] = max(level, levels.get(uid, 0))
        for uid, level in project.members.items():
            levels[uid] = max(level, levels.get(uid, 0))
        return levels

    def _member_rows(self, levels: dict[int, int]) -> list[dict[str, Any]]:
        # The members API leaves out public_email; only GET /users/:id carries it.
        rows = []
        for uid, level in sorted(levels.items()):
            row = {k: v for k, v in self.users[uid].to_json().items() if k != "public_email"}
            rows.append({**row, "access_level": level})
        return rows

    def _level_for(self, user: GLUser | None, project: GLProject) -> int:
        return 0 if user is None else self.project_members_all(project).get(user.id, 0)

    # -- groups

    def _group(self, ref: str) -> GLGroup:
        if ref.isdigit():
            for group in self.groups.values():
                if group.id == int(ref):
                    return group
            raise KeyError("Group")
        return self.groups[ref]

    def _groups(self, req: SeenRequest, rest: list[str], raw_path: str, query: str) -> Reply:
        viewer = self._viewer(req)
        if not rest:
            groups = sorted(self.groups.values(), key=lambda g: g.full_path)
            if not _flag(req.params, "all_available") and not (viewer and viewer.is_admin):
                floor = int(req.params.get("min_access_level") or GUEST)
                groups = [g for g in groups if viewer and self.group_members_all(g).get(viewer.id, 0) >= floor]
            elif _flag(req.params, "all_available") and viewer and viewer.is_auditor and not viewer.is_admin:
                groups = []  # GitLab's documented auditor listing gap.
            search = req.params.get("search")
            if search:
                groups = [g for g in groups if search.lower() in g.full_path.lower()]
            return self._offset_page([g.to_json() for g in groups], req, raw_path)
        group = self._group(rest[0])
        if len(rest) == 1:
            return _json(group.to_json())
        if rest[1:] == ["members", "all"]:
            return self._offset_page(self._member_rows(self.group_members_all(group)), req, raw_path)
        if rest[1:] == ["projects"]:
            include_sub = _flag(req.params, "include_subgroups")
            projects = [p for p in sorted(self.projects.values(), key=lambda p: p.path_with_namespace)
                        if p.namespace_path == group.full_path
                        or (include_sub and p.namespace_path.startswith(group.full_path + "/"))]
            return self._offset_page([p.to_json() for p in projects], req, raw_path)
        if rest[1:] == ["descendant_groups"]:
            groups = [g.to_json() for g in sorted(self.groups.values(), key=lambda g: g.full_path)
                      if g.full_path.startswith(group.full_path + "/")]
            return self._offset_page(groups, req, raw_path)
        return _not_found()

    # -- projects

    def project(self, ref: str | int) -> GLProject:
        ref = str(ref)
        if ref.isdigit():
            return self.projects[int(ref)]
        for project in self.projects.values():
            if project.path_with_namespace == ref:
                return project
        raise KeyError("Project")

    def _visible_projects(self, req: SeenRequest) -> list[GLProject]:
        viewer = self._viewer(req)
        projects = sorted(self.projects.values(), key=lambda p: p.id)
        if _flag(req.params, "membership") or req.params.get("min_access_level"):
            floor = int(req.params.get("min_access_level") or MINIMAL)
            return [p for p in projects if self._level_for(viewer, p) >= floor]
        if viewer and (viewer.is_admin or viewer.is_auditor):
            return projects
        return [p for p in projects if self._level_for(viewer, p) or p.visibility != "private"]

    def _projects(self, req: SeenRequest, rest: list[str], raw_path: str, query: str) -> Reply:
        if not rest:
            items = [p.to_json() for p in self._visible_projects(req)]
            search = req.params.get("search")
            if search:
                items = [p for p in items if search.lower() in p["path_with_namespace"].lower()]
            if req.params.get("pagination") == "keyset":
                return self._keyset_page(items, req, raw_path)
            return self._offset_page(items, req, raw_path)
        project = self.project(rest[0])
        tail = rest[1:]
        if not tail:
            return _json(project.to_json())
        if tail == ["members", "all"]:
            return self._offset_page(self._member_rows(self.project_members_all(project)), req, raw_path)
        if tail[0] in ("issues", "merge_requests"):
            return self._work_items(req, project, tail, raw_path)
        if tail[0] == "repository":
            return self._repository(req, project, tail[1:], raw_path)
        if tail[0] == "uploads" and len(tail) >= 3:
            key = f"/uploads/{tail[1]}/{'/'.join(tail[2:])}"
            if key not in self.uploads:
                return _not_found("Upload")
            return Reply(200, self.uploads[key], {"Content-Type": "application/octet-stream"})
        return _not_found()

    def _work_items(self, req: SeenRequest, project: GLProject, tail: list[str], raw_path: str) -> Reply:
        kind = tail[0]
        items = project.issues if kind == "issues" else project.merge_requests
        viewer = self._viewer(req)
        if kind == "issues" and not (viewer and viewer.is_admin) and self._level_for(viewer, project) < REPORTER:
            items = [i for i in items if not i["confidential"]]
        if len(tail) == 1:
            selected = list(items)
            for key, cmp in (("updated_after", "ge"), ("updated_before", "le"),
                             ("created_after", "ge"), ("created_before", "le")):
                if key in req.params:
                    bound = parse_time(req.params[key])
                    field_name = "updated_at" if key.startswith("updated") else "created_at"
                    selected = [i for i in selected if (parse_time(i[field_name]) >= bound if cmp == "ge"
                                                        else parse_time(i[field_name]) <= bound)]
            order = req.params.get("order_by", "created_at")
            selected.sort(key=lambda i: (parse_time(i[order]), i["id"]), reverse=req.params.get("sort") == "desc")
            return self._offset_page([_public(i) for i in selected], req, raw_path)
        iid = int(tail[1])
        item = next((i for i in items if i["iid"] == iid), None)
        if item is None:
            return _not_found(kind)
        if len(tail) == 2:
            return _json(_public(item))
        if tail[2] == "notes":
            return self._offset_page(project.notes.get((kind, iid), []), req, raw_path)
        if tail[2] == "changes":
            return _json({**_public(item), "changes": item["_changes"]})
        if tail[2] == "commits":
            return self._offset_page(item["_commits"], req, raw_path)
        return _not_found()

    def _snapshot(self, project: GLProject, ref: str | None) -> Commit | None:
        if not project.commits:
            return None
        if ref in (None, "", "HEAD", project.default_branch):
            return project.head
        for commit in project.commits:
            if commit.sha == ref:
                return commit
        return None

    def _repository(self, req: SeenRequest, project: GLProject, tail: list[str], raw_path: str) -> Reply:
        if tail[:1] == ["branches"]:
            if tail[1] != project.default_branch or project.head is None:
                return _not_found("Branch")
            return _json({"name": tail[1], "commit": {"id": project.head.sha}})
        if tail == ["compare"]:
            old = self._snapshot(project, req.params.get("from"))
            new = self._snapshot(project, req.params.get("to"))
            if old is None or new is None:
                return _not_found("Ref")
            return _json({"diffs": _diff(old.files, new.files, project.report_renames), "overflow": project.compare_overflow,
                          "commit": {"id": new.sha}})
        if tail == ["tree"]:
            snap = self._snapshot(project, req.params.get("ref"))
            if snap is None:
                return _not_found("Tree")
            base = req.params.get("path") or ""
            return self._offset_page(_tree_entries(snap.files, base), req, raw_path)
        if tail[:1] == ["files"] and len(tail) >= 2:
            file_path = "/".join(tail[1:])
            snap = self._snapshot(project, req.params.get("ref"))
            if snap is None or file_path not in snap.files:
                return _not_found("File")
            content = snap.files[file_path]
            return _json({"file_name": file_path.rsplit("/", 1)[-1], "file_path": file_path,
                          "content": base64.b64encode(content).decode(), "encoding": "base64",
                          "blob_id": blob_sha(content), "ref": req.params.get("ref")})
        if tail == ["commits"]:
            path = req.params.get("path")
            touching: list[dict[str, Any]] = []
            previous: dict[str, bytes] = {}
            for commit in project.commits:
                if path and commit.files.get(path) != previous.get(path):
                    touching.append({"id": commit.sha, "committed_date": commit.committed_date})
                previous = commit.files
            touching.reverse()
            return self._offset_page(touching, req, raw_path)
        return _not_found()

    def _graphql(self, req: SeenRequest) -> Reply:
        payload = json.loads(req.body or b"{}")
        variables = payload.get("variables") or {}
        try:
            project = self.project(variables.get("fullPath", ""))
        except KeyError:
            return _json({"data": {"project": None}})
        snap = self._snapshot(project, variables.get("branch"))
        if snap is None:
            return _json({"data": {"project": {"name": project.name, "repository": {"rootRef": None, "paginatedTree": None}}}})
        entries = _recursive_entries(project.path_with_namespace, snap.files)
        start = int(variables.get("afterCursor") or 0)
        page = entries[start:start + self.graphql_page_size]
        end = start + len(page)
        return _json({"data": {"project": {"name": project.name, "repository": {"rootRef": project.default_branch,
            "paginatedTree": {
                "nodes": [{"trees": {"nodes": [e for e in page if e["type"] == "tree"]},
                           "blobs": {"nodes": [e for e in page if e["type"] == "blob"]}}],
                "pageInfo": {"endCursor": str(end), "hasNextPage": end < len(entries)},
            }}}}})


def _flag(params: dict[str, str], key: str) -> bool:
    return params.get(key, "").lower() == "true"


def _public(item: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in item.items() if not k.startswith("_")}


def _folders_of(files: dict[str, bytes]) -> set[str]:
    folders: set[str] = set()
    for path in files:
        parts = path.split("/")
        for i in range(1, len(parts)):
            folders.add("/".join(parts[:i]))
    return folders


def _tree_entries(files: dict[str, bytes], base: str) -> list[dict[str, Any]]:
    prefix = f"{base}/" if base else ""

    def directly_under(path: str) -> bool:
        return path.startswith(prefix) and "/" not in path[len(prefix):]

    trees = [{"id": hashlib.sha1(folder.encode()).hexdigest(), "name": folder.rsplit("/", 1)[-1],
              "type": "tree", "path": folder, "mode": "040000"}
             for folder in sorted(_folders_of(files)) if directly_under(folder)]
    blobs = [{"id": blob_sha(content), "name": path.rsplit("/", 1)[-1], "type": "blob",
              "path": path, "mode": "100644"}
             for path, content in sorted(files.items()) if directly_under(path)]
    return trees + blobs


def _recursive_entries(project_path: str, files: dict[str, bytes]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for folder in sorted(_folders_of(files)):
        web_path = f"/{project_path}/-/tree/HEAD/{folder}"
        entries.append({"name": folder.rsplit("/", 1)[-1], "path": folder, "type": "tree",
                        "sha": hashlib.sha1(folder.encode()).hexdigest(), "webPath": web_path,
                        "webUrl": f"{BASE_URL}{web_path}"})
    for path, content in sorted(files.items()):
        web_path = f"/{project_path}/-/blob/HEAD/{path}"
        entries.append({"name": path.rsplit("/", 1)[-1], "path": path, "type": "blob", "sha": blob_sha(content),
                        "webPath": web_path, "webUrl": f"{BASE_URL}{web_path}"})
    return entries


def _diff(old: dict[str, bytes], new: dict[str, bytes], report_renames: bool = True) -> list[dict[str, Any]]:
    """Git-style diff: exact-content renames are reported as renames, like ``git diff -M``."""
    removed = {p: c for p, c in old.items() if p not in new}
    added = {p: c for p, c in new.items() if p not in old}
    diffs: list[dict[str, Any]] = []
    for new_path, content in list(added.items()) if report_renames else []:
        old_path = next((p for p, c in removed.items() if c == content), None)
        if old_path is not None:
            diffs.append({"old_path": old_path, "new_path": new_path, "renamed_file": True,
                          "new_file": False, "deleted_file": False, "diff": ""})
            removed.pop(old_path)
            added.pop(new_path)
    diffs += [{"old_path": p, "new_path": p, "new_file": False, "deleted_file": True, "renamed_file": False,
               "diff": ""} for p in removed]
    diffs += [{"old_path": p, "new_path": p, "new_file": True, "deleted_file": False, "renamed_file": False,
               "diff": ""} for p in added]
    diffs += [{"old_path": p, "new_path": p, "new_file": False, "deleted_file": False, "renamed_file": False,
               "diff": "@@"} for p in sorted(set(old) & set(new)) if old[p] != new[p]]
    return diffs


class _RequestsAdapter(BaseAdapter):
    """A ``requests`` transport that hands each prepared request to the fake."""

    def __init__(self, server: FakeGitLab) -> None:
        super().__init__()
        self.server = server

    def send(self, request: requests.PreparedRequest, stream: bool = False, timeout: object = None,
             verify: object = True, cert: object = None, proxies: object = None) -> requests.Response:
        parts = urlsplit(request.url or "")
        body = request.body or b""
        if isinstance(body, str):
            body = body.encode()
        reply = self.server.handle(request.method or "GET", parts.path, parts.query, dict(request.headers), body,
                                   host=parts.hostname or "")
        response = requests.Response()
        response.status_code = reply.status
        response.headers = CaseInsensitiveDict(reply.headers)
        response._content = reply.body
        response.raw = io.BytesIO(reply.body)
        response.url = request.url or ""
        response.request = request
        response.reason = "OK" if reply.status < 400 else "Error"
        response.encoding = "utf-8"
        return response

    def close(self) -> None:
        return None

