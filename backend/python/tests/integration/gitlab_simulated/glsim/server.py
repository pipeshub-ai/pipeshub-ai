"""The simulated GitLab over real HTTP on 127.0.0.1.

The connector's real clients talk to it over a socket: python-gitlab (requests) for
REST and httpx for GraphQL, images and attachments. Every REST request is matched to an
operation in GitLab's OpenAPI document; a parameter GitLab defines but the simulator
does not model, an operation the simulator does not implement, or a response body that
does not validate against the operation's schema is a gap and fails the test.
"""

from __future__ import annotations

import base64
import contextlib
import json
import re
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit

from tests.integration.gitlab_simulated.glsim import gql, spec
from tests.integration.gitlab_simulated.glsim.model import (
    GUEST,
    Call,
    Group,
    Issue,
    MergeRequest,
    Note,
    Project,
    User,
    World,
    b64,
    git_blob_sha,
    iso,
    parse_iso,
)

if TYPE_CHECKING:
    from collections.abc import Callable

HOST = "127.0.0.1"


class Reply(Exception):
    def __init__(self, status: int, body: Any = None, headers: dict[str, str] | None = None,  # noqa: ANN401
                 raw: bytes | None = None, content_type: str = "application/json") -> None:
        self.status, self.body, self.headers, self.raw, self.content_type = status, body, headers or {}, raw, content_type


def not_found(what: str = "404 Not found") -> Reply:
    return Reply(404, {"message": what})


# ---------------------------------------------------------------- object builders


class Render:
    def __init__(self, gl: World) -> None:
        self.gl = gl

    @property
    def base(self) -> str:
        return self.gl.base_url

    def user_basic(self, u: User) -> dict[str, Any]:
        out = spec.make("API_Entities_UserBasic", id=u.id, username=u.username, name=u.name, state=u.state,
                        locked=False, avatar_url=None, web_url=f"{self.base}/{u.username}",
                        public_email=u.public_email or None)
        if not self.gl.behaviour.members_expose_public_email:
            out.pop("public_email", None)
        return out

    def user_full(self, u: User, *, me: bool) -> dict[str, Any]:
        name = "API_Entities_UserPublic" if me else "API_Entities_User"
        values: dict[str, Any] = {"id": u.id, "username": u.username, "name": u.name, "state": u.state, "locked": False,
                                      "avatar_url": None, "web_url": f"{self.base}/{u.username}",
                                      "public_email": u.public_email or None, "bot": u.bot}
        if me:
            values.update(email=u.email, external=u.external)
        out = spec.make(name, **values)
        if me and u.is_admin:
            out["is_admin"] = True
        if me and u.is_auditor:
            out["is_auditor"] = True
        return out

    def member(self, u: User, m: Any) -> dict[str, Any]:  # noqa: ANN401
        out = spec.make("API_Entities_Member", id=u.id, username=u.username, name=u.name, state=u.state,
                        locked=False, avatar_url=None, web_url=f"{self.base}/{u.username}",
                        public_email=u.public_email or None, access_level=m.access_level,
                        created_at=iso(self.gl.clock.t), expires_at=m.expires_at, membership_state=m.state)
        if not self.gl.behaviour.members_expose_public_email:
            out.pop("public_email", None)
        return out

    def namespace(self, ns: Any) -> dict[str, Any]:  # noqa: ANN401
        if isinstance(ns, Group):
            return {"id": ns.id, "name": ns.name, "path": ns.path, "kind": "group", "full_path": ns.full_path,
                    "parent_id": ns.parent.id if ns.parent else None, "avatar_url": None,
                    "web_url": f"{self.base}/groups/{ns.full_path}"}
        return {"id": ns.id, "name": ns.name, "path": ns.username, "kind": "user", "full_path": ns.username,
                "parent_id": None, "avatar_url": None, "web_url": f"{self.base}/{ns.username}"}

    def project(self, p: Project, definition: str = "API_Entities_Projects_WithAccessAndCatalogSetting") -> dict[str, Any]:
        values = {"id": p.id, "name": p.name, "path": p.path, "path_with_namespace": p.path_with_namespace,
                      "name_with_namespace": p.path_with_namespace.replace("/", " / "), "description": None,
                      "web_url": f"{self.base}/{p.path_with_namespace}", "visibility": p.visibility,
                      "namespace": self.namespace(p.namespace), "default_branch": p.default_branch if p.head() else None,
                      "archived": p.archived, "created_at": iso(p.commits[min(p.commits, key=lambda s: p.commits[s].date)].date)
                      if p.commits else iso(self.gl.clock.t), "last_activity_at": iso(self.gl.clock.t),
                      "issues_enabled": p.issues_enabled, "merge_requests_enabled": p.merge_requests_enabled,
                      "empty_repo": not bool(p.head())}
        if definition == "API_Entities_BasicProjectDetails":
            for k in ("archived", "issues_enabled", "merge_requests_enabled", "empty_repo"):
                values.pop(k)
        return spec.make(definition, **values)

    def project_simple(self, p: Project) -> dict[str, Any]:
        # ``simple=true``: GitLab's BasicProjectDetails.
        return self.project(p, "API_Entities_BasicProjectDetails")

    def group(self, g: Group, definition: str = "API_Entities_Group") -> dict[str, Any]:
        return spec.make(definition, id=g.id, name=g.name, path=g.path, full_path=g.full_path,
                         full_name=g.full_path.replace("/", " / "), visibility=g.visibility,
                         parent_id=g.parent.id if g.parent else None, web_url=f"{self.base}/groups/{g.full_path}",
                         description="", created_at=iso(self.gl.clock.t), avatar_url=None)

    def web(self, p: Project, kind: str, iid: int) -> str:
        return f"{self.base}/{p.path_with_namespace}/-/{kind}/{iid}"

    def issue(self, i: Issue) -> dict[str, Any]:
        p = i.project
        return spec.make(
            "API_Entities_Issue", id=i.id, iid=i.iid, project_id=p.id, title=i.title, description=i.description,
            state=i.state, created_at=iso(i.created_at), updated_at=iso(i.updated_at),
            closed_at=iso(i.closed_at) if i.closed_at else None, closed_by=None, labels=list(i.labels),
            milestone=None, assignees=[self.user_basic(a) for a in i.assignees],
            assignee=self.user_basic(i.assignees[0]) if i.assignees else None, author=self.user_basic(i.author),
            type="ISSUE" if i.issue_type == "issue" else i.issue_type.upper(), issue_type=i.issue_type,
            confidential=i.confidential, due_date=i.due_date, web_url=self.web(p, "issues", i.iid),
            moved_to_id=i.moved_to_id, user_notes_count=sum(1 for n in i.notes if not n.system))

    def mr(self, mr: MergeRequest, definition: str = "API_Entities_MergeRequestBasic") -> dict[str, Any]:
        p = mr.project
        return spec.make(
            definition, id=mr.id, iid=mr.iid, project_id=p.id, title=mr.title, description=mr.description,
            state=mr.state, created_at=iso(mr.created_at), updated_at=iso(mr.updated_at),
            merged_at=iso(mr.merged_at) if mr.merged_at else None,
            merged_by=self.user_basic(mr.merged_by) if mr.merged_by else None,
            closed_at=iso(mr.closed_at) if mr.closed_at else None, labels=list(mr.labels),
            author=self.user_basic(mr.author), assignees=[self.user_basic(a) for a in mr.assignees],
            assignee=self.user_basic(mr.assignees[0]) if mr.assignees else None,
            reviewers=[self.user_basic(r) for r in mr.reviewers], merge_status="can_be_merged",
            sha=mr.sha, web_url=self.web(p, "merge_requests", mr.iid), source_branch=f"feature-{mr.iid}",
            target_branch=p.default_branch, draft=False, source_project_id=p.id, target_project_id=p.id)

    def note(self, n: Note, item: Any) -> dict[str, Any]:  # noqa: ANN401
        kind = "Issue" if isinstance(item, Issue) else "MergeRequest"
        out = spec.make("API_Entities_Note", id=n.id, type="DiffNote" if n.position else None, body=n.body,
                        author=self.user_basic(n.author), created_at=iso(n.created_at), updated_at=iso(n.updated_at),
                        system=n.system, noteable_id=item.id, noteable_type=kind, project_id=item.project.id,
                        resolvable=bool(n.position), confidential=n.internal, internal=n.internal,
                        noteable_iid=item.iid, imported=False, imported_from="none", commands_changes={})
        if n.position:
            out["position"] = n.position
        return out

    def commit(self, p: Project, sha: str) -> dict[str, Any]:
        c = p.commits[sha]
        title = c.message.split("\n", 1)[0]
        return spec.make("API_Entities_Commit", id=sha, short_id=sha[:8], created_at=iso(c.date), parent_ids=c.parents,
                         title=title, message=c.message, author_name="Dev", author_email="dev@example.com",
                         authored_date=iso(c.date), committer_name="Dev", committer_email="dev@example.com",
                         committed_date=iso(c.date), web_url=f"{self.base}/{p.path_with_namespace}/-/commit/{sha}")


def _diff_text(old: bytes | None, new: bytes | None) -> str:
    a = (old or b"").decode(errors="replace").splitlines()
    b = (new or b"").decode(errors="replace").splitlines()
    return "@@ -1 +1 @@\n" + "".join(f"-{x}\n" for x in a) + "".join(f"+{x}\n" for x in b)


def diff_entry(old_path: str, new_path: str, old: bytes | None, new: bytes | None, *,
               new_file: bool = False, deleted: bool = False, renamed: bool = False, diff: str | None = None,
               too_large: bool = False, collapsed: bool = False) -> dict[str, Any]:
    binary = b"\x00" in (old or b"") or b"\x00" in (new or b"")
    text = diff if diff is not None else ("" if too_large or collapsed else (
        f"Binary files a/{old_path} and b/{new_path} differ\n" if binary else _diff_text(old, new)))
    return {"old_path": old_path, "new_path": new_path, "a_mode": "0" if new_file else "100644",
            "b_mode": "0" if deleted else "100644", "new_file": new_file, "renamed_file": renamed,
            "deleted_file": deleted, "generated_file": False, "too_large": too_large, "collapsed": collapsed,
            "diff": text}


def tree_diff(gl: World, a: dict[str, bytes], b: dict[str, bytes]) -> list[dict[str, Any]]:
    deleted = sorted(set(a) - set(b))
    added = sorted(set(b) - set(a))
    modified = sorted(p for p in set(a) & set(b) if a[p] != b[p])
    out: list[dict[str, Any]] = []
    if gl.behaviour.compare_detects_renames:
        by_content = {}
        for path in added:
            by_content.setdefault(b[path], []).append(path)
        renamed_from: dict[str, str] = {}
        for path in deleted:
            targets = by_content.get(a[path])
            if targets:
                renamed_from[targets.pop(0)] = path
        for new, old in renamed_from.items():
            out.append(diff_entry(old, new, a[old], b[new], renamed=True, diff=""))
        deleted = [d for d in deleted if d not in renamed_from.values()]
        added = [x for x in added if x not in renamed_from]
    for path in sorted(set(deleted) | set(added) | set(modified)):
        if path in deleted:
            out.append(diff_entry(path, path, a[path], None, deleted=True))
        elif path in added:
            out.append(diff_entry(path, path, None, b[path], new_file=True))
        else:
            out.append(diff_entry(path, path, a[path], b[path]))
    return out


def branch_search(names: list[str], term: str) -> list[str]:
    """GitLab's branch ``search`` (``GitRefsFinder#by_search``): a case-insensitive substring
    match with the exact match first, or, when the term has ``^``, ``$`` or ``*``, an anchored
    pattern with ``*`` as a wildcard and no reordering."""
    if not term:
        return names
    needle = term.lower()
    if any(op in term for op in "^$*"):
        pattern = re.escape(needle).replace(r"\^", "^", 1).replace(r"\*", ".*?").replace(r"\$", "$", 1)
        return [n for n in names if re.search(pattern, n.lower())]
    hits = [n for n in names if needle in n.lower()]
    exact = next((n for n in hits if n.lower() == needle), None)
    return [exact, *(n for n in hits if n != exact)] if exact else hits


# ---------------------------------------------------------------- the API


class Api:
    """Handlers for the operations the simulator implements. Each declares the query
    parameters it models."""

    def __init__(self, gl: World) -> None:
        self.gl = gl
        self.r = Render(gl)
        self.routes: list[tuple[str, re.Pattern[str], Callable[..., Any], set[str]]] = []
        P = r"(?P<id>[^/]+)"
        add = self._add
        add("GET", r"/api/v4/user", self.me, set())
        add("GET", r"/api/v4/users/(?P<uid>\d+)", self.user, set())
        add("GET", r"/api/v4/projects", self.projects,
            {"membership", "min_access_level", "owned", "simple", "search", "search_namespaces", "order_by", "sort",
             "pagination", "id_after", "page", "per_page"})
        add("GET", rf"/api/v4/projects/{P}", self.project, set())
        add("GET", rf"/api/v4/projects/{P}/members/all", self.project_members_all, {"page", "per_page"})
        add("GET", rf"/api/v4/projects/{P}/issues", self.issues,
            {"updated_after", "updated_before", "created_after", "created_before", "order_by", "sort", "state",
             "page", "per_page", "pagination", "cursor", "scope"})
        add("GET", rf"/api/v4/projects/{P}/issues/(?P<iid>\d+)", self.issue, set())
        add("GET", rf"/api/v4/projects/{P}/issues/(?P<iid>\d+)/notes", self.issue_notes,
            {"page", "per_page", "sort", "order_by"})
        add("GET", rf"/api/v4/projects/{P}/merge_requests", self.mrs,
            {"updated_after", "updated_before", "created_after", "created_before", "order_by", "sort", "state",
             "page", "per_page", "scope"})
        add("GET", rf"/api/v4/projects/{P}/merge_requests/(?P<iid>\d+)", self.mr, set())
        add("GET", rf"/api/v4/projects/{P}/merge_requests/(?P<iid>\d+)/notes", self.mr_notes,
            {"page", "per_page", "sort", "order_by"})
        add("GET", rf"/api/v4/projects/{P}/merge_requests/(?P<iid>\d+)/changes", self.mr_changes, {"access_raw_diffs"})
        add("GET", rf"/api/v4/projects/{P}/merge_requests/(?P<iid>\d+)/commits", self.mr_commits, {"page", "per_page"})
        add("GET", rf"/api/v4/projects/{P}/repository/branches", self.branches, {"search", "page", "per_page"})
        add("GET", rf"/api/v4/projects/{P}/repository/branches/(?P<branch>[^/]+)", self.branch, set())
        add("GET", rf"/api/v4/projects/{P}/repository/compare", self.compare, {"from", "to", "straight"})
        add("GET", rf"/api/v4/projects/{P}/repository/tree", self.tree, {"path", "ref", "recursive", "page", "per_page"})
        add("GET", rf"/api/v4/projects/{P}/repository/files/(?P<fp>[^/]+)/raw", self.file_raw, {"ref"})
        add("GET", rf"/api/v4/projects/{P}/repository/files/(?P<fp>[^/]+)", self.file, {"ref"})
        add("GET", rf"/api/v4/projects/{P}/repository/commits", self.commits,
            {"path", "ref_name", "page", "per_page"})
        add("GET", rf"/api/v4/projects/{P}/uploads/(?P<secret>[^/]+)/(?P<fname>[^/]+)", self.upload, set())
        add("GET", r"/api/v4/groups", self.groups,
            {"min_access_level", "all_available", "owned", "search", "order_by", "sort", "page", "per_page"})
        add("GET", rf"/api/v4/groups/{P}", self.group, set())
        add("GET", rf"/api/v4/groups/{P}/members", self.group_members, {"page", "per_page"})
        add("GET", rf"/api/v4/groups/{P}/members/all", self.group_members_all, {"page", "per_page"})
        add("GET", rf"/api/v4/groups/{P}/projects", self.group_projects,
            {"include_subgroups", "simple", "search", "order_by", "sort", "page", "per_page"})
        add("GET", rf"/api/v4/groups/{P}/descendant_groups", self.descendant_groups,
            {"min_access_level", "all_available", "search", "page", "per_page"})
        add("POST", r"/api/graphql", self.graphql, set())

    def _add(self, method: str, pattern: str, fn: Callable[..., Any], modelled: set[str]) -> None:
        self.routes.append((method, re.compile("^" + pattern + "$"), fn, modelled))

    # ------------------------------------------------------------ plumbing

    def viewer(self, token: str | None) -> User:
        tok = self.gl.tokens.get(token or "")
        if tok is None or tok.revoked:
            raise Reply(401, {"message": "401 Unauthorized"})
        return tok.user

    def readable_project(self, me: User, ref: str) -> Project:
        ref = unquote(ref)
        p = self.gl.project_by_ref(ref)
        if p is None:
            moved = self.gl.project_redirect(ref)
            if moved is not None:
                # gitlab.com keeps the old ref from its first dot in the Location (seen live
                # 2026-10-24: user.name/x -> /projects/<id>.name%2Fx), so for a dotted
                # namespace following the redirect ends in 404.
                tail = quote(ref[ref.index("."):], safe=".") if "." in ref else ""
                loc = f"{self.r.base}/api/v4/projects/{moved.id}{tail}"
                raise Reply(301, {"message": f"This resource has been moved permanently to {loc}"}, {"Location": loc})
            raise not_found("404 Project Not Found")
        if not self.gl.can_read_project(me, p):
            raise not_found("404 Project Not Found")
        return p

    def code_project(self, me: User, ref: str) -> Project:
        p = self.readable_project(me, ref)
        if not self.gl.can_read_code(me, p):
            raise Reply(403, {"message": "403 Forbidden"})
        return p

    def paginate(self, items: list[Any], q: dict[str, str], path: str) -> tuple[list[Any], dict[str, str]]:
        per_page = max(1, min(int(q.get("per_page") or 20), 100))
        page = max(1, int(q.get("page") or 1))
        total = len(items)
        pages = max(1, -(-total // per_page))
        chunk = items[(page - 1) * per_page: page * per_page]
        h = {"X-Page": str(page), "X-Per-Page": str(per_page)}
        links = []

        def link(n: int, rel: str) -> str:
            return f"<{self.r.base}{path}?{urlencode({**q, 'page': n, 'per_page': per_page})}>; rel=\"{rel}\""

        if page < pages:
            h["X-Next-Page"] = str(page + 1)
            links.append(link(page + 1, "next"))
        if page > 1:
            h["X-Prev-Page"] = str(page - 1)
            links.append(link(page - 1, "prev"))
        links.append(link(1, "first"))
        if total <= self.gl.behaviour.x_total_cap:
            h["X-Total"] = str(total)
            h["X-Total-Pages"] = str(pages)
            links.append(link(pages, "last"))
        h["Link"] = ", ".join(links)
        return chunk, h

    # ------------------------------------------------------------ users

    def me(self, me: User, q: dict[str, str]) -> Any:  # noqa: ANN401
        return self.r.user_full(me, me=True)

    def user(self, me: User, q: dict[str, str], uid: str) -> Any:  # noqa: ANN401
        u = self.gl.users.get(int(uid))
        if u is None:
            raise not_found("404 User Not Found")
        return self.r.user_full(u, me=False)

    # ------------------------------------------------------------ projects

    def _project_level(self, me: User, p: Project) -> int:
        return self.gl.access_level(me, p)

    def projects(self, me: User, q: dict[str, str]) -> Any:  # noqa: ANN401
        items = [p for p in self.gl.projects.values() if not p.deleted and self.gl.can_read_project(me, p)]
        if q.get("membership") in ("true", "1") or q.get("min_access_level"):
            floor = int(q.get("min_access_level") or GUEST)
            items = [p for p in items if self.gl.project_member_rows(p).get(me.id)
                     and self._project_level(me, p) >= floor]
        if q.get("search"):
            needle = q["search"].lower()
            items = [p for p in items if needle in p.name.lower()
                     or (q.get("search_namespaces") == "true" and needle in p.path_with_namespace.lower())]
        items.sort(key=lambda p: p.id, reverse=q.get("sort") == "desc" and q.get("order_by") == "id")
        render = self.r.project_simple if q.get("simple") == "true" else (lambda p: self.r.project(p, "API_Entities_Project"))
        if q.get("pagination") == "keyset":
            per_page = max(1, min(int(q.get("per_page") or 20), 100))
            after = int(q.get("id_after") or 0)
            rest = [p for p in items if p.id > after]
            chunk = rest[:per_page]
            h = {}
            if len(rest) > per_page:
                h["Link"] = f"<{self.r.base}/api/v4/projects?{urlencode({**q, 'id_after': chunk[-1].id})}>; rel=\"next\""
            raise Reply(200, [render(p) for p in chunk], h)
        chunk, h = self.paginate(items, q, "/api/v4/projects")
        raise Reply(200, [render(p) for p in chunk], h)

    def project(self, me: User, q: dict[str, str], id: str) -> Any:  # noqa: A002, ANN401
        return self.r.project(self.readable_project(me, id))

    def project_members_all(self, me: User, q: dict[str, str], id: str) -> Any:  # noqa: A002, ANN401
        p = self.readable_project(me, id)
        rows = sorted(self.gl.project_member_rows(p).items())
        chunk, h = self.paginate(rows, q, f"/api/v4/projects/{id}/members/all")
        raise Reply(200, [self.r.member(self.gl.users[uid], m) for uid, (m, _) in chunk], h)

    # ------------------------------------------------------------ issues

    def _issue_listing(self, me: User, p: Project) -> list[Issue]:
        if not p.issues_enabled:
            raise Reply(403, {"message": "403 Forbidden"})
        return [i for i in p.issues.values() if self.gl.can_read_issue(me, i)]

    @staticmethod
    def _window(items: list[Any], q: dict[str, str]) -> list[Any]:
        for key, attr, op in (("updated_after", "updated_at", "ge"), ("updated_before", "updated_at", "le"),
                              ("created_after", "created_at", "ge"), ("created_before", "created_at", "le")):
            if q.get(key):
                bound = parse_iso(q[key])
                # GitLab compares at microsecond precision; the simulator's times are ms.
                items = [x for x in items if (getattr(x, attr) >= bound - 1e-6 if op == "ge"
                                              else getattr(x, attr) <= bound + 1e-6)]
        return items

    @staticmethod
    def _order(items: list[Any], q: dict[str, str]) -> list[Any]:
        key = q.get("order_by") or "created_at"
        if key not in ("created_at", "updated_at"):
            raise Reply(400, {"error": "order_by does not have a valid value"})
        desc = (q.get("sort") or "desc") == "desc"
        return sorted(items, key=lambda x: (getattr(x, key), x.id), reverse=desc)

    def issues(self, me: User, q: dict[str, str], id: str) -> Any:  # noqa: A002, ANN401
        p = self.readable_project(me, id)
        if q.get("state") and q["state"] != "all":
            pass
        items = self._order(self._window(self._issue_listing(me, p), q), q)
        if q.get("state") in ("opened", "closed"):
            items = [i for i in items if i.state == q["state"]]
        if q.get("pagination") == "keyset" and self.gl.behaviour.issues_keyset:
            raise Reply(200, *self._keyset(items, q, f"/api/v4/projects/{id}/issues", self.r.issue))
        chunk, h = self.paginate(items, q, f"/api/v4/projects/{id}/issues")
        raise Reply(200, [self.r.issue(i) for i in chunk], h)

    def _keyset(self, items: list[Any], q: dict[str, str], path: str, render: Callable[[Any], Any]) -> tuple[list[Any], dict[str, str]]:
        """Keyset pages: the cursor names the last (order key, id) returned."""
        per_page = max(1, min(int(q.get("per_page") or 20), 100))
        key = q.get("order_by") or "created_at"
        desc = (q.get("sort") or "desc") == "desc"
        if q.get("cursor"):
            last = json.loads(base64.urlsafe_b64decode(q["cursor"].encode()).decode())
            pivot = (last["k"], last["id"])
            items = [x for x in items if ((getattr(x, key), x.id) < tuple(pivot) if desc else (getattr(x, key), x.id) > tuple(pivot))]
        chunk = items[:per_page]
        h: dict[str, str] = {}
        if len(items) > per_page:
            tail = chunk[-1]
            cur = base64.urlsafe_b64encode(json.dumps({"k": getattr(tail, key), "id": tail.id}).encode()).decode()
            h["Link"] = f"<{self.r.base}{path}?{urlencode({**q, 'cursor': cur})}>; rel=\"next\""
        return [render(x) for x in chunk], h

    def _issue(self, me: User, id: str, iid: str) -> Issue:  # noqa: A002
        p = self.readable_project(me, id)
        i = p.issues.get(int(iid))
        if i is None or not self.gl.can_read_issue(me, i):
            raise not_found("404 Not found")
        return i

    def issue(self, me: User, q: dict[str, str], id: str, iid: str) -> Any:  # noqa: A002, ANN401
        return self.r.issue(self._issue(me, id, iid))

    def _notes(self, me: User, item: Any, q: dict[str, str], path: str) -> Any:  # noqa: ANN401
        p = item.project
        notes = [n for n in item.notes if not n.internal or self.gl.can_read_internal_note(me, p)]
        key = q.get("order_by") or "created_at"
        notes.sort(key=lambda n: (getattr(n, key), n.id), reverse=(q.get("sort") or "desc") == "desc")
        chunk, h = self.paginate(notes, q, path)
        raise Reply(200, [self.r.note(n, item) for n in chunk], h)

    def issue_notes(self, me: User, q: dict[str, str], id: str, iid: str) -> Any:  # noqa: A002, ANN401
        return self._notes(me, self._issue(me, id, iid), q, f"/api/v4/projects/{id}/issues/{iid}/notes")

    # ------------------------------------------------------------ merge requests

    def mrs(self, me: User, q: dict[str, str], id: str) -> Any:  # noqa: A002, ANN401
        p = self.code_project(me, id)
        items = [m for m in p.mrs.values() if self.gl.can_read_mr(me, m)]
        items = self._order(self._window(items, q), q)
        if q.get("state") in ("opened", "closed", "merged", "locked"):
            items = [m for m in items if m.state == q["state"]]
        chunk, h = self.paginate(items, q, f"/api/v4/projects/{id}/merge_requests")
        raise Reply(200, [self.r.mr(m) for m in chunk], h)

    def _mr(self, me: User, id: str, iid: str) -> MergeRequest:  # noqa: A002
        p = self.code_project(me, id)
        m = p.mrs.get(int(iid))
        if m is None or not self.gl.can_read_mr(me, m):
            raise not_found("404 Not found")
        return m

    def mr(self, me: User, q: dict[str, str], id: str, iid: str) -> Any:  # noqa: A002, ANN401
        return self.r.mr(self._mr(me, id, iid), "API_Entities_MergeRequest")

    def mr_notes(self, me: User, q: dict[str, str], id: str, iid: str) -> Any:  # noqa: A002, ANN401
        return self._notes(me, self._mr(me, id, iid), q, f"/api/v4/projects/{id}/merge_requests/{iid}/notes")

    def mr_changes(self, me: User, q: dict[str, str], id: str, iid: str) -> Any:  # noqa: A002, ANN401
        m = self._mr(me, id, iid)
        body = self.r.mr(m, "API_Entities_MergeRequestChanges")
        body["changes"] = [dict(f) for f in m.files]
        body["overflow"] = False
        return body

    def mr_commits(self, me: User, q: dict[str, str], id: str, iid: str) -> Any:  # noqa: A002, ANN401
        m = self._mr(me, id, iid)
        chunk, h = self.paginate(m.commits, q, f"/api/v4/projects/{id}/merge_requests/{iid}/commits")
        raise Reply(200, [self.r.commit(m.project, s) for s in chunk], h)

    # ------------------------------------------------------------ repository

    def _resolve_ref(self, p: Project, ref: str | None) -> str:
        ref = unquote(ref or "HEAD")
        if ref == "HEAD":
            ref = p.default_branch
        if ref in p.branches:
            return p.branches[ref]
        if ref in p.commits:
            return ref
        raise not_found("404 Commit Not Found")

    def _branch(self, p: Project, name: str) -> dict[str, Any]:
        sha, default_head = p.branches[name], p.branches.get(p.default_branch)
        reachable = set()
        # A garbage-collected parent (after a force push) ends the walk.
        while default_head in p.commits and default_head not in reachable:
            reachable.add(default_head)
            parents = p.commits[default_head].parents
            default_head = parents[0] if parents else None
        return spec.make("API_Entities_Branch", name=name, commit=self.r.commit(p, sha),
                         merged=name != p.default_branch and sha in reachable, protected=False,
                         developers_can_push=False, developers_can_merge=False, can_push=True,
                         default=name == p.default_branch, web_url=f"{self.r.base}/{p.path_with_namespace}/-/tree/{name}")

    def branches(self, me: User, q: dict[str, str], id: str) -> Any:  # noqa: A002, ANN401
        p = self.code_project(me, id)
        names = branch_search(sorted(p.branches), q.get("search") or "")
        chunk, h = self.paginate(names, q, f"/api/v4/projects/{id}/repository/branches")
        raise Reply(200, [self._branch(p, n) for n in chunk], h)

    def branch(self, me: User, q: dict[str, str], id: str, branch: str) -> Any:  # noqa: A002, ANN401
        p = self.code_project(me, id)
        name = unquote(branch)
        if name not in p.branches:
            raise not_found("404 Branch Not Found")
        return self._branch(p, name)

    def compare(self, me: User, q: dict[str, str], id: str) -> Any:  # noqa: A002, ANN401
        p = self.code_project(me, id)
        if not q.get("from") or not q.get("to"):
            raise Reply(400, {"error": "from is missing, to is missing"})
        a, b = self._resolve_ref(p, q["from"]), self._resolve_ref(p, q["to"])
        if (q.get("straight") or "").lower() not in ("true", "1"):
            self.gl.gaps.append("compare without straight=true (merge-base comparison) is not modelled")
        diffs = tree_diff(self.gl, p.commits[a].tree, p.commits[b].tree)
        cap = self.gl.behaviour.compare_max_files
        timeout = len(diffs) > cap
        commits = []
        sha = b
        while sha and sha != a and sha in p.commits:
            commits.append(sha)
            parents = p.commits[sha].parents
            sha = parents[0] if parents else ""
        return spec.make("API_Entities_Compare", commit=self.r.commit(p, b), commits=[self.r.commit(p, s) for s in reversed(commits)],
                         diffs=diffs[:cap], compare_timeout=timeout, compare_same_ref=a == b,
                         web_url=f"{self.r.base}/{p.path_with_namespace}/-/compare/{a}...{b}")

    def _tree_entries(self, tree: dict[str, bytes], path: str, recursive: bool) -> list[dict[str, Any]]:
        prefix = f"{path.strip('/')}/" if path.strip("/") else ""
        dirs: dict[str, str] = {}
        files: list[dict[str, Any]] = []
        for fp, content in tree.items():
            if not fp.startswith(prefix):
                continue
            rest = fp[len(prefix):]
            if "/" in rest and not recursive:
                d = prefix + rest.split("/", 1)[0]
                dirs[d] = d
                continue
            if recursive:
                parts = rest.split("/")
                for k in range(1, len(parts)):
                    d = prefix + "/".join(parts[:k])
                    dirs[d] = d
            files.append({"id": git_blob_sha(content), "name": fp.rsplit("/", 1)[-1], "type": "blob", "path": fp,
                          "mode": "100644"})
        out = [{"id": git_blob_sha(("".join(sorted(x for x in tree if x.startswith(d + "/")))).encode()),
                "name": d.rsplit("/", 1)[-1], "type": "tree", "path": d, "mode": "040000"} for d in sorted(dirs)]
        return out + sorted(files, key=lambda e: e["path"])

    def tree(self, me: User, q: dict[str, str], id: str) -> Any:  # noqa: A002, ANN401
        p = self.code_project(me, id)
        sha = self._resolve_ref(p, q.get("ref"))
        entries = self._tree_entries(p.commits[sha].tree, unquote(q.get("path") or ""), q.get("recursive") == "true")
        if q.get("path") and not entries:
            raise not_found("404 Tree Not Found")
        chunk, h = self.paginate(entries, q, f"/api/v4/projects/{id}/repository/tree")
        raise Reply(200, [spec.make("API_Entities_TreeObject", **e) for e in chunk], h)

    def file(self, me: User, q: dict[str, str], id: str, fp: str) -> Any:  # noqa: A002, ANN401
        p = self.code_project(me, id)
        if not q.get("ref"):
            raise Reply(400, {"error": "ref is missing"})
        sha = self._resolve_ref(p, q["ref"])
        path = unquote(fp)
        content = p.commits[sha].tree.get(path)
        if content is None:
            raise not_found("404 File Not Found")
        # Not in openapi_v2 (no schema); fields from docs/repository_files.md "Retrieve a file".
        return {"file_name": path.rsplit("/", 1)[-1], "file_path": path, "size": len(content), "encoding": "base64",
                "content": b64(content), "content_sha256": "", "ref": q["ref"], "blob_id": git_blob_sha(content),
                "commit_id": sha, "last_commit_id": sha, "execute_filemode": False}

    def file_raw(self, me: User, q: dict[str, str], id: str, fp: str) -> Any:  # noqa: A002, ANN401
        p = self.code_project(me, id)
        sha = self._resolve_ref(p, q.get("ref") or "HEAD")
        content = p.commits[sha].tree.get(unquote(fp))
        if content is None:
            raise not_found("404 File Not Found")
        raise Reply(200, raw=content, content_type="text/plain")

    def commits(self, me: User, q: dict[str, str], id: str) -> Any:  # noqa: A002, ANN401
        p = self.code_project(me, id)
        sha = self._resolve_ref(p, q.get("ref_name"))
        path = unquote(q.get("path") or "")
        chain = []
        while sha and sha in p.commits:
            c = p.commits[sha]
            parent = c.parents[0] if c.parents and c.parents[0] in p.commits else None
            before = p.commits[parent].tree.get(path) if parent else None
            if not path or c.tree.get(path) != before:
                chain.append(c.sha)
            sha = parent or ""
        chunk, h = self.paginate(chain, q, f"/api/v4/projects/{id}/repository/commits")
        raise Reply(200, [self.r.commit(p, s) for s in chunk], h)

    def upload(self, me: User, q: dict[str, str], id: str, secret: str, fname: str) -> Any:  # noqa: A002, ANN401
        p = self.readable_project(me, id)
        data = p.uploads.get((unquote(secret), unquote(fname)))
        if data is None:
            raise not_found("404 Not found")
        raise Reply(200, raw=data, content_type="application/octet-stream")

    # ------------------------------------------------------------ groups

    def _group(self, me: User, ref: str) -> Group:
        g = self.gl.group_by_ref(unquote(ref))
        if g is None or g not in self.gl.visible_groups(me):
            raise not_found("404 Group Not Found")
        return g

    def groups(self, me: User, q: dict[str, str]) -> Any:  # noqa: ANN401
        items = self.gl.visible_groups(me)
        if q.get("min_access_level"):
            floor = int(q["min_access_level"])
            items = [g for g in items if (row := self.gl.group_member_rows(g).get(me.id)) and row[0].access_level >= floor]
        elif q.get("all_available") not in ("true", "1") and not me.is_admin:
            items = [g for g in items if self.gl.group_member_rows(g).get(me.id)]
        if q.get("search"):
            items = [g for g in items if q["search"].lower() in g.full_path.lower()]
        items.sort(key=lambda g: g.full_path if q.get("order_by") == "path" else g.name)
        chunk, h = self.paginate(items, q, "/api/v4/groups")
        raise Reply(200, [self.r.group(g) for g in chunk], h)

    def group(self, me: User, q: dict[str, str], id: str) -> Any:  # noqa: A002, ANN401
        return self.r.group(self._group(me, id), "API_Entities_GroupDetail")

    def group_members(self, me: User, q: dict[str, str], id: str) -> Any:  # noqa: A002, ANN401
        g = self._group(me, id)
        rows = sorted(g.members.items())
        chunk, h = self.paginate(rows, q, f"/api/v4/groups/{id}/members")
        raise Reply(200, [self.r.member(self.gl.users[uid], m) for uid, m in chunk], h)

    def group_members_all(self, me: User, q: dict[str, str], id: str) -> Any:  # noqa: A002, ANN401
        g = self._group(me, id)
        rows = sorted(self.gl.group_member_rows(g).items())
        chunk, h = self.paginate(rows, q, f"/api/v4/groups/{id}/members/all")
        raise Reply(200, [self.r.member(self.gl.users[uid], m) for uid, (m, _) in chunk], h)

    def group_projects(self, me: User, q: dict[str, str], id: str) -> Any:  # noqa: A002, ANN401
        g = self._group(me, id)
        sub = q.get("include_subgroups") in ("true", "1")
        items = [p for p in self.gl.projects.values() if not p.deleted and isinstance(p.namespace, Group)
                 and (p.namespace is g or (sub and g in p.namespace.ancestors())) and self.gl.can_read_project(me, p)]
        if q.get("search"):
            items = [p for p in items if q["search"].lower() in p.name.lower()]
        items.sort(key=lambda p: p.id)
        chunk, h = self.paginate(items, q, f"/api/v4/groups/{id}/projects")
        render = self.r.project_simple if q.get("simple") == "true" else (lambda p: self.r.project(p, "API_Entities_Project"))
        raise Reply(200, [render(p) for p in chunk], h)

    def descendant_groups(self, me: User, q: dict[str, str], id: str) -> Any:  # noqa: A002, ANN401
        g = self._group(me, id)
        items = [d for d in self.gl.visible_groups(me) if g in d.ancestors()]
        if q.get("min_access_level"):
            floor = int(q["min_access_level"])
            items = [d for d in items if (row := self.gl.group_member_rows(d).get(me.id)) and row[0].access_level >= floor]
        chunk, h = self.paginate(sorted(items, key=lambda d: d.full_path), q, f"/api/v4/groups/{id}/descendant_groups")
        raise Reply(200, [self.r.group(d) for d in chunk], h)

    # ------------------------------------------------------------ GraphQL

    def graphql(self, me: User, q: dict[str, str], body: bytes) -> Any:  # noqa: ANN401
        return gql.execute(self.gl, self.r, me, body)


class Server:
    def __init__(self, gl: World) -> None:
        self.gl = gl
        self.api = Api(gl)
        self.httpd: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None

    def start(self) -> str:
        server = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args: Any) -> None:  # noqa: ANN401
                return

            def do_GET(self) -> None:
                server.handle(self, "GET")

            def do_POST(self) -> None:
                server.handle(self, "POST")

            def do_PUT(self) -> None:
                server.handle(self, "PUT")

            def do_DELETE(self) -> None:
                server.handle(self, "DELETE")

        self.httpd = ThreadingHTTPServer((HOST, 0), Handler)
        self.httpd.daemon_threads = True
        port = self.httpd.server_address[1]
        self.gl.base_url = f"http://{HOST}:{port}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True, name="glsim")
        self.thread.start()
        return self.gl.base_url

    def stop(self) -> None:
        if self.httpd is not None:
            self.httpd.shutdown()
            self.httpd.server_close()

    # ------------------------------------------------------------ dispatch

    def handle(self, h: BaseHTTPRequestHandler, method: str) -> None:
        gl = self.gl
        raw_path = h.path
        parts = urlsplit(raw_path)
        path = parts.path
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        length = int(h.headers.get("Content-Length") or 0)
        body = h.rfile.read(length) if length else b""
        token = h.headers.get("PRIVATE-TOKEN")
        auth = h.headers.get("Authorization") or ""
        if not token and auth.lower().startswith("bearer "):
            token = auth[7:]
        resource = re.sub(r"/\d+", "/:n", re.sub(r"/projects/[^/]+", "/projects/:id", path))
        status, headers, payload, ctype = 500, {}, b"", "application/json"
        fault = None
        with gl.lock:
            for f in gl.faults:
                if f.times > 0 and f.matches(method, path, query):
                    if f.skip > 0:
                        f.skip -= 1
                        continue
                    f.times -= 1
                    fault = f
                    break
            limited = self._rate_limited(token)
        try:
            if fault is not None and fault.kind == "drop":
                gl.calls.append(Call(method, path, query, 0, token, resource))
                h.close_connection = True
                with contextlib.suppress(OSError):
                    h.connection.shutdown(socket.SHUT_RDWR)
                return
            if fault is not None and fault.kind == "hook":
                with gl.lock:
                    fault.action()
                fault = None
            if fault is not None and fault.kind == "hang":
                time.sleep(float(fault.headers.get("seconds", "5")))
            if limited is not None:
                raise limited
            if fault is not None and fault.kind == "empty":
                # A 200 with nothing in it, as a listing that lost its rows would answer.
                raise Reply(200, [], {"X-Total": "0", "X-Total-Pages": "1", "X-Page": "1", "X-Per-Page": "100"})
            if fault is not None and isinstance(fault.kind, int):
                raise Reply(fault.kind, {"message": f"{fault.kind} simulated"}, dict(fault.headers))
            with gl.lock:
                result = self._dispatch(method, path, query, token, body)
            raise Reply(200, result)
        except Reply as r:
            status, headers, ctype = r.status, dict(r.headers), r.content_type
            if r.raw is not None:
                payload = r.raw
            else:
                payload = json.dumps(r.body).encode()
                if status == 200 and path.startswith("/api/v4/"):
                    self._validate(method, path, r.body)
        except Exception as e:  # a simulator bug must show, not pass as a connector failure
            gl.gaps.append(f"simulator raised on {method} {path}: {e!r}")
            status, payload = 500, json.dumps({"message": "500 simulator error"}).encode()
        gl.calls.append(Call(method, path, query, status, token, resource))
        if fault is not None and fault.kind == "truncate":
            payload = payload[: max(1, len(payload) // 2)]
        h.send_response(status)
        h.send_header("Content-Type", ctype)
        h.send_header("Content-Length", str(len(payload) if not (fault and fault.kind == "truncate") else len(payload) * 2))
        for k, v in headers.items():
            h.send_header(k, v)
        h.end_headers()
        upload = re.search(r"/uploads/([^/]+/[^/]+)$", path)
        if upload and len(payload) > 1024 * 1024:
            # Written in pieces so a client that stops reading early is visible.
            key, sent = unquote(upload.group(1)), 0
            try:
                for off in range(0, len(payload), 256 * 1024):
                    h.wfile.write(payload[off:off + 256 * 1024])
                    sent += min(256 * 1024, len(payload) - off)
            except OSError:
                h.close_connection = True
            gl.bytes_sent[key] = sent
            return
        with contextlib.suppress(OSError):
            h.wfile.write(payload)
        if fault is not None and fault.kind == "truncate":
            h.close_connection = True

    def _rate_limited(self, token: str | None) -> Reply | None:
        limit = self.gl.behaviour.rate_limit_per_minute
        if not limit or not token:
            return None
        minute = int(self.gl.clock.t // 60)
        key = (token, minute)
        self.gl.minute_counts[key] = self.gl.minute_counts.get(key, 0) + 1
        if self.gl.minute_counts[key] <= limit:
            return None
        reset = (minute + 1) * 60
        return Reply(429, {"message": "Retry later"}, {
            "RateLimit-Limit": str(limit), "RateLimit-Remaining": "0", "RateLimit-Reset": str(int(reset)),
            "Retry-After": str(max(1, int(reset - self.gl.clock.t)))})

    def _dispatch(self, method: str, path: str, query: dict[str, str], token: str | None, body: bytes) -> Any:  # noqa: ANN401
        gl = self.gl
        for m, pattern, fn, modelled in self.api.routes:
            if m != method:
                continue
            mt = pattern.match(path)
            if not mt:
                continue
            template = spec.match(path) if path != "/api/graphql" else "/api/graphql"
            if template is None:
                gl.gaps.append(f"{method} {path}: not in GitLab's spec")
            else:
                gaps, bad = spec.check_query(template, method, query, modelled)
                gl.gaps.extend(gaps)
                if bad:
                    raise Reply(400, {"error": bad})
            me = self.api.viewer(token)
            if path == "/api/graphql":
                return fn(me, query, body)
            return fn(me, query, **mt.groupdict())
        template = spec.match(path)
        gl.gaps.append(f"{method} {path}: operation not implemented by the simulator"
                       + (f" (GitLab: {template})" if template else " (not in GitLab's spec either)"))
        raise not_found("404 Not Found")

    def _validate(self, method: str, path: str, body: Any) -> None:  # noqa: ANN401
        template = spec.match(path)
        if template is None:
            return
        schema = spec.response_schema(template, method)
        if not schema:
            return
        problems = spec.validate(schema, body, listing=isinstance(body, list))
        if problems:
            self.gl.gaps.append(f"{method} {template}: response does not match GitLab's schema: {problems}")
