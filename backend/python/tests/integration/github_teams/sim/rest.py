"""The simulated GitHub over real HTTP.

``ApiServer`` is an aiohttp server on 127.0.0.1. ``Internet`` is the httpx transport
the connector's clients are given: it sends ``api.github.com``, ``github.com`` and
``private-user-images.githubusercontent.com`` to that server over a real socket and
records every request, including whether it carried a token. Any other host is
recorded and refused, so a request to an unexpected host is visible to the scenario.

Every request to api.github.com is checked against GitHub's OpenAPI document and
every JSON body sent back is validated against the operation's schema. An operation
the simulator does not implement is a gap (``World.gaps``), and the harness fails the
test that hit it.
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any
from urllib.parse import parse_qsl, unquote, urlencode

import httpx
from aiohttp import web

from tests.integration.github_teams.sim import spec
from tests.integration.github_teams.sim.model import (
    ROLE_PERMISSION,
    Call,
    Fault,
    Issue,
    Org,
    PullRequest,
    Repo,
    User,
    World,
    b64,
    git_blob_sha,
    iso,
    rank,
)

API = "https://api.github.com"
HOSTS = ("api.github.com", "github.com", "private-user-images.githubusercontent.com")


class Reply(Exception):
    def __init__(self, status: int, body: Any = None, headers: dict[str, str] | None = None) -> None:  # noqa: ANN401
        self.status, self.body, self.headers = status, body, headers or {}


def not_found() -> Reply:
    return Reply(404, {"message": "Not Found", "documentation_url": "https://docs.github.com/rest", "status": "404"})


# ---------------------------------------------------------------- object builders


def simple_user(u: User) -> dict[str, Any]:
    return spec.make("simple-user", login=u.login, id=u.id, type=u.type,
                     html_url=f"https://github.com/{u.login}", site_admin=False)


def public_user(u: User) -> dict[str, Any]:
    return spec.make("public-user", login=u.login, id=u.id, type=u.type, name=u.name, email=u.email,
                     html_url=f"https://github.com/{u.login}", user_view_type="public")


def owner_obj(owner: Any) -> dict[str, Any]:  # noqa: ANN401
    if isinstance(owner, Org):
        return spec.make("simple-user", login=owner.login, id=owner.id, type="Organization",
                         html_url=f"https://github.com/{owner.login}", site_admin=False)
    return simple_user(owner)


def repo_obj(r: Repo, component: str = "full-repository") -> dict[str, Any]:
    return spec.make(component, id=r.id, name=r.name, full_name=r.full_name, owner=owner_obj(r.owner),
                     private=r.visibility != "public", visibility=r.visibility,
                     html_url=f"https://github.com/{r.full_name}", default_branch=r.default_branch,
                     archived=r.archived, has_issues=r.has_issues, fork=False, disabled=False)


def label_obj(name: str) -> dict[str, Any]:
    return spec.make("label", name=name)


def issue_obj(i: Issue) -> dict[str, Any]:
    return spec.make("issue", id=i.id, number=i.number, title=i.title, body=i.body, state=i.state,
                     state_reason=i.state_reason, user=simple_user(i.user),
                     assignees=[simple_user(a) for a in i.assignees],
                     assignee=simple_user(i.assignees[0]) if i.assignees else None,
                     labels=[label_obj(n) for n in i.labels], created_at=iso(i.created_at),
                     updated_at=iso(i.updated_at), closed_at=iso(i.closed_at) if i.closed_at else None,
                     html_url=f"https://github.com/{i.repo.full_name}/issues/{i.number}",
                     url=f"{API}/repos/{i.repo.full_name}/issues/{i.number}", comments=len(i.comments),
                     parent_issue_url=(f"{API}/repos/{i.parent.repo.full_name}/issues/{i.parent.number}"
                                       if i.parent and not i.parent.deleted else None))


def pr_as_issue(p: PullRequest) -> dict[str, Any]:
    return spec.make("issue", id=p.id, number=p.number, title=p.title, body=p.body, state=p.state,
                     state_reason=None, user=simple_user(p.user), assignees=[simple_user(a) for a in p.assignees],
                     assignee=None, labels=[label_obj(n) for n in p.labels], created_at=iso(p.created_at),
                     updated_at=iso(p.updated_at), closed_at=None, comments=len(p.comments),
                     html_url=f"https://github.com/{p.repo.full_name}/pull/{p.number}",
                     url=f"{API}/repos/{p.repo.full_name}/issues/{p.number}",
                     pull_request={"url": f"{API}/repos/{p.repo.full_name}/pulls/{p.number}", "html_url": None,
                                   "diff_url": None, "patch_url": None})


def _ref(p: PullRequest, sha: str, ref: str) -> dict[str, Any]:
    return {"label": f"{p.repo.owner.login}:{ref}", "ref": ref, "sha": sha, "user": owner_obj(p.repo.owner),
            "repo": repo_obj(p.repo, "repository")}


def pr_obj(p: PullRequest, component: str) -> dict[str, Any]:
    values = {"id": p.id, "number": p.number, "title": p.title, "body": p.body, "state": p.state, "user": simple_user(p.user),
                  "assignees": [simple_user(a) for a in p.assignees], "assignee": None,
                  "requested_reviewers": [simple_user(r) for r in p.requested_reviewers],
                  "labels": [label_obj(n) for n in p.labels], "created_at": iso(p.created_at),
                  "updated_at": iso(p.updated_at), "merged_at": iso(p.merged_at) if p.merged_at else None,
                  "closed_at": None, "html_url": f"https://github.com/{p.repo.full_name}/pull/{p.number}",
                  "head": _ref(p, p.head_sha, f"pr-{p.number}"), "base": _ref(p, p.base_sha, p.repo.default_branch)}
    if component == "pull-request":
        values.update(merged=p.merged_at is not None, mergeable=None, merged_by=None)
    return spec.make(component, **values)


def comment_obj(c: Any, url: str) -> dict[str, Any]:  # noqa: ANN401
    return spec.make("issue-comment", id=c.id, body=c.body, user=simple_user(c.user), created_at=iso(c.created_at),
                     updated_at=iso(c.updated_at), html_url=f"{url}#issuecomment-{c.id}")


def commit_obj(repo: Repo, sha: str) -> dict[str, Any]:
    c = repo.commits[sha]
    sig = {"name": "dev", "email": "dev@example.com", "date": iso(c.date)}
    return spec.make("commit", sha=sha, html_url=f"https://github.com/{repo.full_name}/commit/{sha}",
                     commit=spec.make("commit", sha=sha)["commit"] | {"message": c.message, "author": sig, "committer": sig},
                     parents=[{"sha": c.parent, "url": "", "html_url": ""}] if c.parent else [])


# ---------------------------------------------------------------- the server


class ApiServer:
    def __init__(self, ws: World) -> None:
        self.ws = ws
        self.runner: web.AppRunner | None = None
        self.port = 0
        self._signed: dict[str, Any] = {}  # per server: one test's signed URLs (and bytes) end with it

    async def start(self) -> None:
        app = web.Application(client_max_size=64 * 1024 * 1024)
        app.router.add_route("*", "/{tail:.*}", self._handle)
        self.runner = web.AppRunner(app, access_log=None)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        self.port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]

    async def stop(self) -> None:
        if self.runner:
            await self.runner.cleanup()

    # -------------------------------------------------------------- dispatch

    async def _handle(self, request: web.Request) -> web.StreamResponse:
        ws = self.ws
        host = request.headers.get("X-Sim-Host", "")
        path = "/" + request.match_info["tail"]
        query = dict(parse_qsl(request.query_string, keep_blank_values=True))
        auth = request.headers.get("Authorization")
        status, body, headers, resource = 500, None, {}, None
        try:
            fault = self._fault(request.method, f"{host}{path}", query)
            if fault is not None and fault.kind == "hook":
                fault.action()  # type: ignore[misc]
                fault = None
            if fault is not None:
                status, body, headers = await self._apply_fault(fault, request)
            elif host == "api.github.com":
                resource = "graphql" if path == "/graphql" else ("search" if path.startswith("/search/") else "core")
                status, body, headers = await self._api(request, path, query, auth, resource)
            elif host == "github.com":
                status, body, headers = self._attachment(path, auth)
            elif host == "private-user-images.githubusercontent.com":
                status, body, headers = self._signed_download(path, query)
            else:
                status, body = 404, {"message": "unknown host"}
        except Reply as r:
            status, body, headers = r.status, r.body, r.headers
        except _Drop:
            ws.calls.append(Call(request.method, host, path, query, 0, resource, bool(auth)))
            request.transport.close()  # type: ignore[union-attr]
            raise web.HTTPInternalServerError() from None
        ws.calls.append(Call(request.method, host, path, query, status, resource, bool(auth)))
        if isinstance(body, (bytes, bytearray)):
            return web.Response(status=status, body=bytes(body), headers=headers)
        if body is None:
            return web.Response(status=status, headers=headers)
        return web.json_response(body, status=status, headers=headers)

    def _fault(self, method: str, where: str, query: dict[str, str]) -> Fault | None:
        for fault in self.ws.faults:
            if fault.times is not None and fault.hits >= fault.times:
                continue
            if fault.match(method, where, query):
                fault.hits += 1
                return fault
        return None

    async def _apply_fault(self, fault: Fault, request: web.Request) -> tuple[int, Any, dict[str, str]]:
        if fault.kind == "drop":
            raise _Drop()
        if fault.kind == "slow":
            await asyncio.sleep(fault.seconds)
            raise _Drop()
        if fault.kind == "secondary":
            return 403, {"message": "You have exceeded a secondary rate limit. Please wait a few minutes before you try again."}, {
                "retry-after": str(int(fault.seconds or 60))}
        if fault.kind == "primary":
            reset = self.ws.clock.now() + int(fault.seconds or 3600)
            return 403, {"message": "API rate limit exceeded for user ID 1."}, {
                "x-ratelimit-limit": "5000", "x-ratelimit-remaining": "0", "x-ratelimit-used": "5000",
                "x-ratelimit-reset": str(reset), "x-ratelimit-resource": "core"}
        if fault.status == 502:
            return 502, b"<html><body><h1>502 Bad Gateway</h1></body></html>", {"content-type": "text/html"}
        return fault.status, {"message": "Server Error"}, {}

    def _authenticate(self, auth: str | None) -> User | None:
        if not auth:
            return None
        value = auth.split(" ", 1)[-1].strip()
        token = self.ws.tokens.get(value)
        if token is None or token.revoked:
            raise Reply(401, {"message": "Bad credentials", "documentation_url": "https://docs.github.com/rest", "status": "401"})
        return self.ws.users[token.user_id]

    def _charge(self, auth: str | None, resource: str) -> dict[str, str]:
        ws = self.ws
        key = (resource, auth or "")
        window = ws.rate.get(key)
        now = ws.clock.now()
        period = 60 if resource == "search" else 3600
        if window is None or now >= window[0]:
            window = ws.rate[key] = [now + period, 0]
        limit = ws.rate_limits[resource]
        if window[1] >= limit:
            raise Reply(403, {"message": "API rate limit exceeded"}, {
                "x-ratelimit-limit": str(limit), "x-ratelimit-remaining": "0", "x-ratelimit-used": str(window[1]),
                "x-ratelimit-reset": str(window[0]), "x-ratelimit-resource": resource})
        window[1] += 1
        return {"x-ratelimit-limit": str(limit), "x-ratelimit-remaining": str(limit - window[1]),
                "x-ratelimit-used": str(window[1]), "x-ratelimit-reset": str(window[0]),
                "x-ratelimit-resource": resource}

    async def _api(self, request: web.Request, path: str, query: dict[str, str], auth: str | None,
                   resource: str) -> tuple[int, Any, dict[str, str]]:
        ws = self.ws
        viewer = self._authenticate(auth)
        rl = self._charge(auth, resource)
        if path == "/graphql" and request.method == "POST":
            from tests.integration.github_teams.sim import gql
            payload = await request.json()
            return 200, gql.execute_query(ws, viewer, payload.get("query", ""), payload.get("variables") or {}), rl
        # Undocumented but live-verified: /repositories/{id}/... answers as /repos/{owner}/{name}/...
        m = re.match(r"^/repositories/(\d+)(/.*)?$", path)
        if m:
            repo = ws.repos.get(int(m.group(1)))
            if repo is None or repo.deleted or not ws.can_read(viewer, repo):
                raise not_found()
            path = f"/repos/{repo.full_name}{m.group(2) or ''}"
        template = spec.match(path)
        if template is None:
            ws.gaps.append(f"{request.method} {path}: not a GitHub REST path")
            raise not_found()
        problems, refusal = spec.check_query(template, request.method, query)
        ws.gaps.extend(problems)
        if refusal:
            raise Reply(422, {"message": "Validation Failed", "errors": [refusal]}, rl)
        handler = HANDLERS.get((request.method, template))
        if handler is None:
            ws.gaps.append(f"{request.method} {template}: not modelled by the simulator")
            raise Reply(501, {"message": "not modelled"})
        params = dict(zip(re.findall(r"\{([^}]+)\}", template), _path_values(template, path)))
        if template.startswith("/repos/{owner}/{repo}"):
            params["__rest"] = "/" + "/".join(path.split("/")[4:]) if path.count("/") > 3 else ""
        status, body, headers = handler(self, viewer, params, query)
        if status == 200 and not isinstance(body, (bytes, bytearray)):
            schema = spec.response_schema(template, request.method)
            if schema is not None:
                errors = spec.validate(schema, body)
                if errors:
                    ws.gaps.append(f"simulator response for {template} breaks GitHub's schema: {errors}")
        return status, body, {**rl, **headers}

    # -------------------------------------------------------------- attachments

    def _attachment(self, path: str, auth: str | None) -> tuple[int, Any, dict[str, str]]:
        ws = self.ws
        upload = ws.uploads.get(f"https://github.com{path}")
        if upload is None or upload.repo.deleted:
            return 404, b"Not Found", {}
        if auth and not ws.behaviour.attachment_token_accepted:
            return 404, b"Not Found", {}  # live-verified: the web host refuses API tokens
        if upload.repo.visibility != "public":
            viewer = self._authenticate(auth) if (auth and ws.behaviour.attachment_token_accepted) else None
            if not ws.can_read(viewer, upload.repo):
                return 404, b"Not Found", {}
        key = uuid.uuid4().hex
        self._signed[key] = upload
        return 302, None, {"Location": f"https://private-user-images.githubusercontent.com/{key}?jwt=signed-{key}"}

    def _signed_download(self, path: str, query: dict[str, str]) -> tuple[int, Any, dict[str, str]]:
        upload = self._signed.get(path.strip("/"))
        if upload is None or query.get("jwt") != f"signed-{path.strip('/')}":
            return 404, b"Not Found", {}
        return 200, upload.data, {"Content-Type": upload.content_type}


class _Drop(Exception):
    pass


def _path_values(template: str, path: str) -> list[str]:
    names = re.findall(r"\{([^}]+)\}", template)
    pattern = "^" + re.sub(r"\\\{[^}]+\\\}", "([^/]+)", re.escape(template)) + "$"
    if template.endswith("{path}"):
        pattern = pattern[:-len("([^/]+)$")] + "(.+)$"
    m = re.match(pattern, path)
    return [unquote(v) for v in m.groups()] if m else [""] * len(names)


# ---------------------------------------------------------------- pagination


def paginate(items: list[Any], query: dict[str, str], base: str) -> tuple[list[Any], dict[str, str]]:
    per_page = min(100, max(1, int(query.get("per_page", 30))))
    page = max(1, int(query.get("page", 1)))
    start = (page - 1) * per_page
    chunk = items[start:start + per_page]
    last = max(1, -(-len(items) // per_page))
    links = []
    q = {k: v for k, v in query.items() if k != "page"}
    if page < last:
        links.append(f'<{API}{base}?{urlencode({**q, "page": page + 1})}>; rel="next"')
        links.append(f'<{API}{base}?{urlencode({**q, "page": last})}>; rel="last"')
    return chunk, ({"Link": ", ".join(links)} if links else {})


# ---------------------------------------------------------------- handlers

# (status, body, headers): body is JSON-ready data, or bytes.
Response = tuple[int, Any, dict[str, str]]
Handler = Callable[..., Response]
HANDLERS: dict[tuple[str, str], Handler] = {}


def route(method: str, template: str) -> Callable[[Handler], Handler]:
    def deco(fn: Handler) -> Handler:
        HANDLERS[(method, template)] = fn
        return fn
    return deco


def _repo(s: ApiServer, viewer: User | None, p: dict[str, str]) -> Repo:
    ws = s.ws
    repo = ws.repo_by_name(p["owner"], p["repo"])
    if repo is None or not ws.can_read(viewer, repo):
        raise not_found()
    if repo.full_name.lower() != f"{p['owner']}/{p['repo']}".lower():
        # Renamed or transferred: GitHub redirects to the canonical location.
        raise Reply(301, {"message": "Moved Permanently", "url": f"{API}/repositories/{repo.id}"},
                    {"Location": f"{API}/repositories/{repo.id}{_rest_of(p)}"})
    return repo


def _rest_of(p: dict[str, str]) -> str:
    return p.get("__rest", "")


def _member_view(s: ApiServer, viewer: User | None, org: Org) -> bool:
    return viewer is not None and viewer.id in org.members


@route("GET", "/user")
def get_user_me(s, viewer, p, q) -> Response:
    if viewer is None:
        raise Reply(401, {"message": "Requires authentication"})
    return 200, spec.make("private-user", login=viewer.login, id=viewer.id, type=viewer.type, name=viewer.name,
                          email=viewer.email, html_url=f"https://github.com/{viewer.login}",
                          user_view_type="private"), {}


@route("GET", "/users/{username}")
def get_user(s, viewer, p, q) -> Response:
    u = next((u for u in s.ws.users.values() if u.login.lower() == p["username"].lower()), None)
    if u is None:
        raise not_found()
    return 200, public_user(u), {}


@route("GET", "/user/orgs")
def list_my_orgs(s, viewer, p, q) -> Response:
    orgs = [o for o in s.ws.orgs.values() if viewer and viewer.id in o.members]
    rows = [spec.make("organization-simple", login=o.login, id=o.id) for o in orgs]
    page, links = paginate(rows, q, "/user/orgs")
    return 200, page, links


@route("GET", "/orgs/{org}/members")
def list_members(s, viewer, p, q) -> Response:
    org = s.ws.orgs.get(p["org"].lower())
    if org is None:
        raise not_found()
    if not _member_view(s, viewer, org):
        return 200, [], {}  # concealed memberships are invisible to outsiders
    role = q.get("role", "all")
    ids = [uid for uid, r in org.members.items() if role == "all" or (role == "admin") == (r == "admin")]
    rows = [simple_user(s.ws.users[i]) for i in sorted(ids)]
    page, links = paginate(rows, q, f"/orgs/{p['org']}/members")
    return 200, page, links


@route("GET", "/orgs/{org}/outside_collaborators")
def list_outside(s, viewer, p, q) -> Response:
    ws = s.ws
    org = ws.orgs.get(p["org"].lower())
    if org is None:
        raise not_found()
    if viewer is None or org.members.get(viewer.id) != "admin":
        raise Reply(403, {"message": "Must be an organization owner"})
    ids = {uid for r in ws.repos.values() if r.owner is org for uid in r.collaborators if uid not in org.members}
    rows = [simple_user(ws.users[i]) for i in sorted(ids)]
    page, links = paginate(rows, q, f"/orgs/{p['org']}/outside_collaborators")
    return 200, page, links


@route("GET", "/orgs/{org}/repos")
def list_org_repos(s, viewer, p, q) -> Response:
    ws = s.ws
    org = ws.orgs.get(p["org"].lower())
    if org is None:
        raise not_found()
    repos = [r for r in ws.repos.values() if r.owner is org and not r.deleted and ws.can_read(viewer, r)]
    sort = q.get("sort", "created")
    repos.sort(key=(lambda r: r.full_name.lower()) if sort == "full_name" else (lambda r: r.id))
    page, links = paginate([repo_obj(r, "minimal-repository") for r in repos], q, f"/orgs/{p['org']}/repos")
    return 200, page, links


@route("GET", "/user/repos")
def list_my_repos(s, viewer, p, q) -> Response:
    ws = s.ws
    repos = [r for r in ws.repos.values() if not r.deleted and viewer and (
        r.owner.id == viewer.id or ws.role(viewer, r))]
    repos.sort(key=lambda r: r.full_name.lower())
    page, links = paginate([repo_obj(r, "repository") for r in repos], q, "/user/repos")
    return 200, page, links


@route("GET", "/search/repositories")
def search_repos(s, viewer, p, q) -> Response:
    ws = s.ws
    terms = q.get("q", "").split()
    words = [t for t in terms if ":" not in t]
    scopes = [t.split(":", 1) for t in terms if t.startswith(("org:", "user:"))]
    hits = []
    for r in ws.repos.values():
        if r.deleted or not ws.can_read(viewer, r):
            continue
        if words and not all(w.lower() in r.name.lower() for w in words):
            continue
        if scopes and not any(r.owner.login.lower() == v.lower() for _, v in scopes):
            continue
        hits.append(r)
    page, links = paginate([repo_obj(r, "repo-search-result-item") | {"score": 1.0} for r in hits], q, "/search/repositories")
    return 200, {"total_count": len(hits), "incomplete_results": False, "items": page}, links


@route("GET", "/repos/{owner}/{repo}")
def get_repo(s, viewer, p, q) -> Response:
    return 200, repo_obj(_repo(s, viewer, p)), {}


@route("GET", "/repos/{owner}/{repo}/collaborators")
def list_collaborators(s, viewer, p, q) -> Response:
    ws = s.ws
    repo = _repo(s, viewer, p)
    if rank(ws.role(viewer, repo)) < rank("write"):
        raise Reply(403, {"message": "Must have push access to view repository collaborators."})
    affiliation = q.get("affiliation", "all")
    org = ws.org_of(repo)
    rows = []
    for user, role in sorted(ws.collaborators(repo), key=lambda x: x[0].id):
        outside = org is not None and user.id not in org.members
        if affiliation == "outside" and not outside:
            continue
        if affiliation == "direct" and user.id not in repo.collaborators:
            continue
        perms = {name: rank(role) >= rank(r) for r, name in ROLE_PERMISSION.items()}
        rows.append(spec.make("collaborator", login=user.login, id=user.id, type=user.type,
                              html_url=f"https://github.com/{user.login}", site_admin=False,
                              permissions=perms, role_name=role))
    page, links = paginate(rows, q, f"/repos/{p['owner']}/{p['repo']}/collaborators")
    return 200, page, links


def _issue_rows(repo: Repo) -> list[Any]:
    rows: list[Any] = [i for i in repo.issues.values() if not i.deleted and i.transferred_to is None]
    rows += list(repo.pulls.values())
    return rows


@route("GET", "/repos/{owner}/{repo}/issues")
def list_issues(s, viewer, p, q) -> Response:
    repo = _repo(s, viewer, p)
    if not repo.has_issues:
        raise Reply(410, {"message": "Issues are disabled for this repo"})
    rows = _issue_rows(repo)
    state = q.get("state", "open")
    if state != "all":
        rows = [r for r in rows if r.state == state]
    if "since" in q:
        from datetime import datetime
        since = int(datetime.fromisoformat(q["since"].replace("Z", "+00:00")).timestamp())
        rows = [r for r in rows if r.updated_at >= since]  # live-verified: inclusive
    key = {"created": lambda r: (r.created_at, r.id), "updated": lambda r: (r.updated_at, r.id),
           "comments": lambda r: (len(r.comments), r.id)}[q.get("sort", "created")]
    rows.sort(key=key, reverse=q.get("direction", "desc") == "desc")
    objs = [pr_as_issue(r) if isinstance(r, PullRequest) else issue_obj(r) for r in rows]
    page, links = paginate(objs, q, f"/repos/{p['owner']}/{p['repo']}/issues")
    return 200, page, links


@route("GET", "/repos/{owner}/{repo}/issues/comments")
def list_repo_comments(s, viewer, p, q) -> Response:
    repo = _repo(s, viewer, p)
    rows = [
        (c, item, f"https://github.com/{repo.full_name}/{'pull' if isinstance(item, PullRequest) else 'issues'}/{item.number}")
        for item in _issue_rows(repo) for c in item.comments
    ]
    if "since" in q:
        from datetime import datetime
        since = int(datetime.fromisoformat(q["since"].replace("Z", "+00:00")).timestamp())
        rows = [r for r in rows if r[0].updated_at >= since]
    rows.sort(key=lambda r: r[0].id)
    objs = [comment_obj(c, url) | {"issue_url": f"{API}/repos/{repo.full_name}/issues/{item.number}"}
            for c, item, url in rows]
    page, links = paginate(objs, q, f"/repos/{p['owner']}/{p['repo']}/issues/comments")
    return 200, page, links


def _issue(s: ApiServer, viewer, p) -> Any:  # noqa: ANN401
    repo = _repo(s, viewer, p)
    n = int(p["issue_number"])
    item = repo.issues.get(n) or repo.pulls.get(n)
    if item is None:
        raise not_found()
    if isinstance(item, Issue) and item.deleted:
        raise Reply(410, {"message": "This issue was deleted"})
    if isinstance(item, Issue) and item.transferred_to is not None:
        target = item.transferred_to
        if not s.ws.can_read(viewer, target.repo):
            raise not_found()
        raise Reply(301, {"message": "Moved Permanently"},
                    {"Location": f"{API}/repositories/{target.repo.id}/issues/{target.number}"})
    return item


@route("GET", "/repos/{owner}/{repo}/issues/{issue_number}")
def get_issue(s, viewer, p, q) -> Response:
    item = _issue(s, viewer, p)
    return 200, (pr_as_issue(item) if isinstance(item, PullRequest) else issue_obj(item)), {}


@route("GET", "/repos/{owner}/{repo}/issues/{issue_number}/comments")
def list_issue_comments(s, viewer, p, q) -> Response:
    item = _issue(s, viewer, p)
    kind = "pull" if isinstance(item, PullRequest) else "issues"
    url = f"https://github.com/{item.repo.full_name}/{kind}/{item.number}"
    rows = [comment_obj(c, url) for c in sorted(item.comments, key=lambda c: c.id)]
    page, links = paginate(rows, q, f"/repos/{p['owner']}/{p['repo']}/issues/{p['issue_number']}/comments")
    return 200, page, links


@route("GET", "/repos/{owner}/{repo}/issues/{issue_number}/dependencies/blocking")
def list_blocking(s, viewer, p, q) -> Response:
    _issue(s, viewer, p)
    return 200, [], {}


@route("GET", "/repos/{owner}/{repo}/pulls")
def list_pulls(s, viewer, p, q) -> Response:
    repo = _repo(s, viewer, p)
    rows = list(repo.pulls.values())
    state = q.get("state", "open")
    if state != "all":
        rows = [r for r in rows if r.state == state]
    sort = q.get("sort", "created")
    key = (lambda r: (r.updated_at, r.id)) if sort == "updated" else (lambda r: (r.created_at, r.id))
    default_dir = "desc" if sort in ("created", "updated") and "direction" not in q else q.get("direction", "desc")
    rows.sort(key=key, reverse=default_dir == "desc")
    page, links = paginate([pr_obj(r, "pull-request-simple") for r in rows], q, f"/repos/{p['owner']}/{p['repo']}/pulls")
    return 200, page, links


def _pull(s: ApiServer, viewer, p) -> PullRequest:
    repo = _repo(s, viewer, p)
    pr = repo.pulls.get(int(p["pull_number"]))
    if pr is None:
        raise not_found()
    return pr


@route("GET", "/repos/{owner}/{repo}/pulls/{pull_number}")
def get_pull(s, viewer, p, q) -> Response:
    return 200, pr_obj(_pull(s, viewer, p), "pull-request"), {}


@route("GET", "/repos/{owner}/{repo}/pulls/{pull_number}/commits")
def pull_commits(s, viewer, p, q) -> Response:
    pr = _pull(s, viewer, p)
    page, links = paginate([commit_obj(pr.repo, sha) for sha in pr.commits], q, f"/repos/{p['owner']}/{p['repo']}/pulls/{p['pull_number']}/commits")
    return 200, page, links


@route("GET", "/repos/{owner}/{repo}/pulls/{pull_number}/reviews")
def pull_reviews(s, viewer, p, q) -> Response:
    pr = _pull(s, viewer, p)
    rows = [spec.make("pull-request-review", id=r.id, body=r.body, user=simple_user(r.user), state=r.state,
                      submitted_at=iso(r.submitted_at), html_url=f"https://github.com/{pr.repo.full_name}/pull/{pr.number}#pullrequestreview-{r.id}")
            for r in pr.reviews]
    page, links = paginate(rows, q, f"/repos/{p['owner']}/{p['repo']}/pulls/{p['pull_number']}/reviews")
    return 200, page, links


@route("GET", "/repos/{owner}/{repo}/pulls/{pull_number}/comments")
def pull_review_comments(s, viewer, p, q) -> Response:
    pr = _pull(s, viewer, p)
    rows = [spec.make("pull-request-review-comment", id=c.id, body=c.body, user=simple_user(c.user), path=c.path,
                      line=c.line, original_line=c.line, created_at=iso(c.created_at), updated_at=iso(c.updated_at),
                      html_url=f"https://github.com/{pr.repo.full_name}/pull/{pr.number}#discussion_r{c.id}",
                      **({"in_reply_to_id": c.in_reply_to_id} if c.in_reply_to_id else {}))
            for c in pr.review_comments]
    page, links = paginate(rows, q, f"/repos/{p['owner']}/{p['repo']}/pulls/{p['pull_number']}/comments")
    return 200, page, links


@route("GET", "/repos/{owner}/{repo}/pulls/{pull_number}/files")
def pull_files(s, viewer, p, q) -> Response:
    pr = _pull(s, viewer, p)
    rows = [spec.make("diff-entry", blob_url="", raw_url="", contents_url="", **f) for f in pr.files[:3000]]
    page, links = paginate(rows, q, f"/repos/{p['owner']}/{p['repo']}/pulls/{p['pull_number']}/files")
    return 200, page, links


@route("GET", "/repos/{owner}/{repo}/branches/{branch}")
def get_branch(s, viewer, p, q) -> Response:
    repo = _repo(s, viewer, p)
    sha = repo.branches.get(p["branch"])
    if sha is None:
        raise not_found()
    return 200, spec.make("branch-with-protection", name=p["branch"],
                          commit=commit_obj(repo, sha)), {}


def _trees(repo: Repo, commit_sha: str) -> tuple[str, dict[str, dict[str, tuple[str, str, int | None]]]]:
    """(root tree sha, tree sha -> {name: (type, sha, size)}) for one commit."""
    dirs: dict[str, dict[str, tuple[str, str, int | None]]] = {"": {}}
    for path, data in repo.commits[commit_sha].tree.items():
        parts = path.split("/")
        for i in range(1, len(parts)):
            dirs.setdefault("/".join(parts[:i]), {})
        dirs["/".join(parts[:-1])][parts[-1]] = ("blob", git_blob_sha(data), len(data))
    shas: dict[str, str] = {}
    for d in sorted(dirs, key=lambda x: x.count("/") if x else -1, reverse=True):
        shas[d] = git_blob_sha(("tree:" + json.dumps(sorted(dirs[d].items()))).encode())
        if d:
            parent, _, name = d.rpartition("/")
            dirs[parent][name] = ("tree", shas[d], None)
    return shas[""], {shas[d]: dirs[d] for d in dirs}


@route("GET", "/repos/{owner}/{repo}/git/trees/{tree_sha}")
def get_tree(s, viewer, p, q) -> Response:
    ws = s.ws
    repo = _repo(s, viewer, p)
    target = p["tree_sha"]
    index = None
    for sha in repo.commits:
        root, trees = _trees(repo, sha)
        if target == sha:
            target, index = root, trees
            break
        if target in trees:
            index = trees
            break
    if index is None:
        raise not_found()
    recursive = q.get("recursive") not in (None, "", "0", "false")
    entries: list[dict[str, Any]] = []

    def walk(tree_sha: str, prefix: str) -> None:
        for name, (kind, sha, size) in sorted(index[tree_sha].items()):
            path = f"{prefix}{name}"
            entry = {"path": path, "mode": "040000" if kind == "tree" else "100644", "type": kind, "sha": sha,
                     "url": f"{API}/repos/{repo.full_name}/git/{'trees' if kind == 'tree' else 'blobs'}/{sha}"}
            if size is not None:
                entry["size"] = size
            entries.append(entry)
            if recursive and kind == "tree":
                walk(sha, path + "/")

    walk(target, "")
    truncated = len(entries) > ws.behaviour.tree_truncate_over
    if truncated:
        entries = entries[: ws.behaviour.tree_truncate_over]
    return 200, {"sha": target, "url": f"{API}/repos/{repo.full_name}/git/trees/{target}",
                 "truncated": truncated, "tree": entries}, {}


def _blob_lookup(repo: Repo, sha: str) -> bytes | None:
    for c in repo.commits.values():
        for data in c.tree.values():
            if git_blob_sha(data) == sha:
                return data
    return None


@route("GET", "/repos/{owner}/{repo}/git/blobs/{file_sha}")
def get_blob(s, viewer, p, q) -> Response:
    repo = _repo(s, viewer, p)
    data = _blob_lookup(repo, p["file_sha"])
    if data is None:
        raise not_found()
    return 200, {"content": b64(data), "encoding": "base64", "url": "", "sha": p["file_sha"], "size": len(data),
                 "node_id": spec.FILLER_STR}, {}


def _resolve_ref(repo: Repo, ref: str | None) -> str | None:
    ref = ref or repo.default_branch
    if ref in repo.branches:
        return repo.branches[ref]
    return ref if ref in repo.commits else None


@route("GET", "/repos/{owner}/{repo}/contents/{path}")
def get_contents(s, viewer, p, q) -> Response:
    repo = _repo(s, viewer, p)
    sha = _resolve_ref(repo, q.get("ref"))
    if sha is None:
        raise not_found()
    data = repo.commits[sha].tree.get(p["path"])
    if data is None:
        raise not_found()
    big = len(data) > 1024 * 1024  # documented: 1-100 MB files come back with empty content
    return 200, spec.make("content-file", type="file", name=p["path"].rsplit("/", 1)[-1], path=p["path"],
                          sha=git_blob_sha(data), size=len(data), encoding="none" if big else "base64",
                          content="" if big else b64(data)), {}


@route("GET", "/repos/{owner}/{repo}/compare/{basehead}")
def compare(s, viewer, p, q) -> Response:
    ws = s.ws
    repo = _repo(s, viewer, p)
    base, head = p["basehead"].split("...", 1)
    base, head = _resolve_ref(repo, base), _resolve_ref(repo, head)
    if base is None or head is None:
        raise not_found()
    head_line, base_line = ws.ancestors(repo, head), ws.ancestors(repo, base)
    if base == head:
        status, ahead, behind = "identical", 0, 0
    elif base in head_line:
        status, ahead, behind = "ahead", head_line.index(base), 0
    elif head in base_line:
        status, ahead, behind = "behind", 0, base_line.index(head)
    else:
        common = next((c for c in head_line if c in base_line), None)
        status = "diverged"
        ahead = head_line.index(common) if common else len(head_line)
        behind = base_line.index(common) if common else len(base_line)
    old, new = repo.commits[base].tree, repo.commits[head].tree
    files = []
    removed = {k: v for k, v in old.items() if k not in new}
    added = {k: v for k, v in new.items() if k not in old}
    if ws.behaviour.compare_detects_renames:
        for path, data in list(added.items()):
            src = next((k for k, v in removed.items() if v == data), None)
            if src:
                files.append({"filename": path, "previous_filename": src, "status": "renamed", "sha": git_blob_sha(data),
                              "additions": 0, "deletions": 0, "changes": 0})
                removed.pop(src)
                added.pop(path)
    for path, data in added.items():
        files.append({"filename": path, "status": "added", "sha": git_blob_sha(data), "additions": 1, "deletions": 0, "changes": 1})
    for path, data in removed.items():
        files.append({"filename": path, "status": "removed", "sha": git_blob_sha(data), "additions": 0, "deletions": 1, "changes": 1})
    files.extend(
        {"filename": path, "status": "modified", "sha": git_blob_sha(new[path]), "additions": 1, "deletions": 1, "changes": 2}
        for path in sorted(set(old) & set(new)) if old[path] != new[path]
    )
    files = [spec.make("diff-entry", blob_url="", raw_url="", contents_url="", **f) for f in files[:300]]
    commits = [commit_obj(repo, c) for c in head_line[:ahead][:250]]
    return 200, spec.make("commit-comparison", status=status, ahead_by=ahead, behind_by=behind,
                          total_commits=ahead, files=files, commits=commits,
                          base_commit=commit_obj(repo, base), merge_base_commit=commit_obj(repo, base)), {}


@route("GET", "/repos/{owner}/{repo}/commits")
def list_commits(s, viewer, p, q) -> Response:
    ws = s.ws
    repo = _repo(s, viewer, p)
    history = ws.path_history(repo, q["path"]) if q.get("path") else [repo.commits[c] for c in ws.ancestors(repo, repo.head().sha)]
    page, links = paginate([commit_obj(repo, c.sha) for c in history], q, f"/repos/{p['owner']}/{p['repo']}/commits")
    return 200, page, links


@route("GET", "/rate_limit")
def rate_limit(s, viewer, p, q) -> Response:
    return 200, spec.make("rate-limit-overview"), {}


# ---------------------------------------------------------------- the transport


class Internet(httpx.AsyncBaseTransport):
    """Routes GitHub's hosts to the simulator over a real socket; refuses the rest."""

    def __init__(self, ws: World, server: ApiServer) -> None:
        self.ws = ws
        self.server = server
        self._inner = httpx.AsyncHTTPTransport()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if host not in HOSTS:
            self.ws.calls.append(Call(request.method, host, request.url.path, dict(request.url.params), 0, None,
                                      "authorization" in request.headers))
            raise httpx.ConnectError(f"simulated internet: no route to {host}", request=request)
        url = request.url.copy_with(scheme="http", host="127.0.0.1", port=self.server.port)
        headers = [(k, v) for k, v in request.headers.raw if k.lower() != b"host"]
        headers.append((b"Host", f"127.0.0.1:{self.server.port}".encode()))
        headers.append((b"X-Sim-Host", host.encode()))
        forwarded = httpx.Request(request.method, url, headers=headers, stream=request.stream,
                                  extensions=request.extensions)
        response = await self._inner.handle_async_request(forwarded)
        url = str(request.url)
        ws = self.ws
        inner_stream = response.stream

        class Counting(httpx.AsyncByteStream):
            async def __aiter__(self) -> AsyncIterator[bytes]:  # type: ignore[override]
                async for chunk in inner_stream:  # type: ignore[union-attr]
                    ws.bytes_read[url] = ws.bytes_read.get(url, 0) + len(chunk)
                    yield chunk

            async def aclose(self) -> None:
                await inner_stream.aclose()  # type: ignore[union-attr]

        return httpx.Response(response.status_code, headers=response.headers, stream=Counting(),
                              extensions=response.extensions, request=request)

    async def aclose(self) -> None:
        await self._inner.aclose()
