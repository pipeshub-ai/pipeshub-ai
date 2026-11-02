"""A Linear workspace in memory, answering the Linear connector's GraphQL operations.

Stands in for ``api.linear.app`` and ``uploads.linear.app`` in the real-graph Linear
tests; the connector, its data source and its GraphQL documents run unchanged. What the
fake models comes from Linear's documentation:

* who sees a team (linear.app/docs/private-teams, /members-roles): a private team its
  members; a public team every member but not guests, who see only the teams they were
  added to; a restricted team (a non-private team inside a private one) its members and
  the members of the parent teams up to that private one. Suspended users are left out
  of ``users`` and ``members`` lists (``includeDisabled`` defaults to false);
* lists are newest first, 50 to a page unless the query sizes them, at most 250;
* trashed and archived issues are listed only with ``includeArchived`` and stay readable
  by id; something the API key cannot see is "Entity not found";
* a team filter on issues also matches the issues of the team's sub-teams, and moving
  an issue to another team moves its sub-issues with it (both seen on a live workspace);
* a revoked or unknown API key gets Linear's AUTHENTICATION_ERROR.

To keep the fake from covering for the connector, an answer keeps only fields the query
document names, a nested list the query does not size stops at 50 nodes as on Linear,
and a filter key the fake does not understand fails the test rather than being ignored.
"""

from __future__ import annotations

import itertools
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

import httpx

from app.sources.client.graphql.response import GraphQLResponse

if TYPE_CHECKING:
    from collections.abc import Callable

UPLOAD_HOST = "uploads.linear.app"
DEFAULT_PAGE = 50
MAX_PAGE = 250
_numbers = itertools.count(1)


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{ms % 1000:03d}Z"


def _ms(value: str) -> int:
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)


@dataclass(eq=False)
class User:
    name: str
    email: str
    guest: bool = False
    active: bool = True
    id: str = field(default_factory=lambda: str(uuid.uuid4()))


@dataclass(eq=False)
class Team:
    key: str
    name: str
    visibility: str = "public"  # public | private | restricted
    parent: Team | None = None
    members: list[User] = field(default_factory=list)
    id: str = field(default_factory=lambda: str(uuid.uuid4()))


@dataclass(eq=False)
class Comment:
    body: str
    user: User | None  # None: written by an integration (GitHub, Slack, email)
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: int = 0
    updated_at: int = 0


@dataclass(eq=False)
class Attachment:
    title: str
    url: str
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: int = 0
    updated_at: int = 0


@dataclass(eq=False)
class Document:
    title: str
    content: str
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: int = 0
    updated_at: int = 0


@dataclass(eq=False)
class Issue:
    team: Team
    title: str
    number: int
    description: str = ""
    parent: Issue | None = None
    comments: list[Comment] = field(default_factory=list)
    attachments: list[Attachment] = field(default_factory=list)
    documents: list[Document] = field(default_factory=list)
    trashed: bool = False
    archived_at: int | None = None
    created_at: int = 0
    updated_at: int = 0
    id: str = field(default_factory=lambda: str(uuid.uuid4()))

    @property
    def identifier(self) -> str:
        return f"{self.team.key}-{self.number}"


@dataclass(eq=False)
class Link:
    label: str
    url: str
    id: str = field(default_factory=lambda: str(uuid.uuid4()))


@dataclass(eq=False)
class Project:
    name: str
    teams: list[Team]
    content: str = ""
    documents: list[Document] = field(default_factory=list)
    links: list[Link] = field(default_factory=list)
    lead: Team | None = None
    trashed: bool = False
    archived_at: int | None = None
    created_at: int = 0
    updated_at: int = 0
    id: str = field(default_factory=lambda: str(uuid.uuid4()))


class EntityNotFound(Exception):
    pass


class LinearWorkspace:
    """One workspace, seen through the API key of ``sync_user``."""

    def __init__(self, url_key: str = "acme") -> None:
        self.url_key = url_key
        self.org_id = str(uuid.uuid4())
        self.now = 1_788_220_800_000  # 2026-09-01T00:00:00Z
        self.users: list[User] = []
        self.teams: list[Team] = []
        self.issues: list[Issue] = []
        self.projects: list[Project] = []
        self.uploads: dict[str, bytes] = {}
        self.sync_user: User | None = None
        self.token = f"lin_api_{uuid.uuid4().hex}"
        # Every HTTP request the connector made outside the GraphQL API: (url, carried the token)
        self.requests: list[tuple[str, bool]] = []
        self.operations: list[str] = []
        # Anything the connector asked for that the fake does not model; the test fails on it
        self.gaps: list[str] = []
        # Operation name -> which calls (by their variables) answer 500, as Linear does when overloaded
        self.failing: dict[str, Callable[[dict[str, Any]], bool]] = {}

    # ------------------------------------------------------------------ building

    def tick(self, ms: int = 60_000) -> int:
        self.now += ms
        return self.now

    def user(self, name: str, *, guest: bool = False, active: bool = True) -> User:
        user = User(name, f"{name.lower()}@{self.url_key}.example", guest=guest, active=active)
        self.users.append(user)
        return user

    def team(self, key: str, *, visibility: str = "public", parent: Team | None = None,
             members: list[User] = ()) -> Team:
        team = Team(key, key.title(), visibility, parent, list(members))
        self.teams.append(team)
        return team

    def issue(self, team: Team, title: str, *, description: str = "", parent: Issue | None = None,
              comments: list[Comment] = (), attachments: list[Attachment] = (),
              documents: list[Document] = ()) -> Issue:
        now = self.tick(1000)
        issue = Issue(team, title, next(_numbers), description, parent, list(comments), list(attachments),
                      list(documents), created_at=now, updated_at=now)
        for child in [*issue.comments, *issue.attachments, *issue.documents]:
            child.created_at = child.updated_at = now
        self.issues.append(issue)
        return issue

    def project(self, name: str, teams: list[Team], *, content: str = "",
                documents: list[Document] = (), links: list[Link] = (), lead: Team | None = None) -> Project:
        now = self.tick(1000)
        project = Project(name, list(teams), content, list(documents), list(links), lead=lead,
                          created_at=now, updated_at=now)
        for child in project.documents:
            child.created_at = child.updated_at = now
        self.projects.append(project)
        return project

    def upload(self, filename: str, content: bytes) -> str:
        url = f"https://{UPLOAD_HOST}/{self.org_id}/{uuid.uuid4()}/{filename}"
        self.uploads[url] = content
        return url

    def change(self, issue: Issue, **fields: object) -> None:
        for name, value in fields.items():
            setattr(issue, name, value)
        issue.updated_at = self.tick()

    def move(self, issue: Issue, team: Team) -> None:
        """Linear moves the sub-issues along with their parent."""
        self.change(issue, team=team)
        for child in [c for c in self.issues if c.parent is issue]:
            self.move(child, team)

    def trash(self, issue: Issue) -> None:
        now = self.tick()
        issue.trashed, issue.archived_at, issue.updated_at = True, now, now

    # ------------------------------------------------------------------ visibility

    def boundary_members(self, team: Team) -> set[str]:
        """Members of the parent teams a restricted team is open to."""
        ids: set[str] = set()
        parent = team.parent
        while parent is not None:
            ids |= {u.id for u in parent.members}
            if parent.visibility == "private":
                break
            parent = parent.parent
        return ids

    def can_see_team(self, user: User | None, team: Team) -> bool:
        if user is None or not user.active:
            return False
        member = any(m.id == user.id for m in team.members)
        if team.visibility == "private":
            return member
        if team.visibility == "restricted":
            return member or user.id in self.boundary_members(team)
        return member or not user.guest

    def readers(self, item: Issue | Project) -> set[str]:
        """Emails of the users Linear shows an issue or project to: a project through any
        of its teams."""
        if item.trashed:
            return set()
        teams = item.teams if isinstance(item, Project) else [item.team]
        return {u.email for u in self.users if any(self.can_see_team(u, t) for t in teams)}

    # ------------------------------------------------------------------ transport

    async def execute(self, query: str, variables: dict[str, Any] | None = None, **_: object) -> GraphQLResponse:
        """Answer one GraphQL request by its root field, as Linear would."""
        variables = variables or {}
        operation = re.search(r"\bquery\s+(\w+)[^{]*\{\s*(\w+)", query)
        if not operation:
            return self._gap(f"not a query: {query[:60]!r}")
        name, root = operation.group(1).lower(), operation.group(2)
        self.operations.append(name)
        if name in self.failing and self.failing[name](variables):
            return GraphQLResponse(success=False, message="Internal server error", status_code=500)
        resolve = getattr(self, f"_root_{root}", None)
        if resolve is None:
            return self._gap(f"root field {root!r} ({name})")
        try:
            data = {root: resolve(_arguments(query, root, variables))}
        except EntityNotFound:
            return GraphQLResponse(success=False, message="Entity not found", status_code=200,
                                   errors=[{"message": "Entity not found", "extensions": {"code": "INVALID_INPUT"}}])
        except _Gap as gap:
            return self._gap(f"{gap} ({name})")
        return GraphQLResponse(success=True, data=_select(data, query, variables))

    @staticmethod
    def unauthenticated() -> GraphQLResponse:
        """Linear's answer to a revoked or unknown API key."""
        message = "Authentication required, not authenticated"
        return GraphQLResponse(success=False, message=message, status_code=400, errors=[{
            "message": message, "extensions": {"type": "authentication error", "code": "AUTHENTICATION_ERROR"}}])

    def _gap(self, what: str) -> GraphQLResponse:
        self.gaps.append(what)
        return GraphQLResponse(success=False, message=f"the fake does not model {what}", status_code=400)

    def http(self) -> httpx.MockTransport:
        """``uploads.linear.app``, and every other host, for the data source's file fetches."""
        def handle(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            self.requests.append((url, request.headers.get("authorization") == self.token))
            if request.url.host != UPLOAD_HOST:
                return httpx.Response(200, content=b"internal: do not index")
            if url not in self.uploads:
                return httpx.Response(404)
            body = self.uploads[url]
            return httpx.Response(200, content=b"" if request.method == "HEAD" else body,
                                  headers={"content-length": str(len(body)), "content-type": "application/octet-stream"})
        return httpx.MockTransport(handle)

    # ------------------------------------------------------------------ root fields

    def _visible_issues(self, include_archived: bool) -> list[Issue]:
        return [i for i in self.issues if self.can_see_team(self.sync_user, i.team)
                and (include_archived or not i.archived_at)]

    def _root_viewer(self, _: dict[str, Any]) -> dict[str, Any] | None:
        return self._user(self.sync_user)

    def _root_organization(self, _: dict[str, Any]) -> dict[str, Any]:
        return {"id": self.org_id, "name": "Acme", "urlKey": self.url_key,
                "createdAt": _iso(self.now), "updatedAt": _iso(self.now)}

    def _root_users(self, args: dict[str, Any]) -> dict[str, Any]:
        users = [u for u in self.users if u.active or args.get("includeDisabled")]
        users = [u for u in users if _matches({"id": u.id, "email": u.email, "active": u.active}, args.get("filter"))]
        return _page([self._user(u) for u in users], args)

    def _root_teams(self, args: dict[str, Any]) -> dict[str, Any]:
        teams = [t for t in self.teams if self.can_see_team(self.sync_user, t)
                 and _matches({"id": t.id, "key": t.key}, args.get("filter"))]
        return _page([self._team(t) for t in teams], args)

    def _root_team(self, args: dict[str, Any]) -> dict[str, Any]:
        for team in self.teams:
            if args["id"] in (team.id, team.key) and self.can_see_team(self.sync_user, team):
                return self._team(team)
        raise EntityNotFound(args["id"])

    def _root_issues(self, args: dict[str, Any]) -> dict[str, Any]:
        issues = [i for i in self._visible_issues(bool(args.get("includeArchived")))
                  if _matches(self._issue_facts(i), args.get("filter"))]
        order = (args.get("orderBy") or "createdAt")
        issues.sort(key=lambda i: i.updated_at if order == "updatedAt" else i.created_at, reverse=True)
        return _page([self._issue(i) for i in issues], args)

    def _root_issue(self, args: dict[str, Any]) -> dict[str, Any]:
        for issue in self._visible_issues(include_archived=True):
            if args["id"] in (issue.id, issue.identifier):
                return self._issue(issue)
        raise EntityNotFound(args["id"])

    def _root_comments(self, args: dict[str, Any]) -> dict[str, Any]:
        nodes = [
            {**self._comment(issue, c), "issue": {"id": issue.id}, "project": None, "projectUpdate": None}
            for issue in self._visible_issues(include_archived=False) for c in issue.comments
            if _matches({"updatedAt": c.updated_at, "createdAt": c.created_at,
                         "issue": self._issue_facts(issue)}, args.get("filter"))
        ]
        return _page(nodes, args)

    def _root_attachments(self, args: dict[str, Any]) -> dict[str, Any]:
        nodes = [
            {**self._attachment(a), "issue": self._issue_ref(i)}
            for i in self._visible_issues(include_archived=False) for a in i.attachments
            if _matches({"id": a.id, "updatedAt": a.updated_at, "createdAt": a.created_at,
                         "issue": self._issue_facts(i)}, args.get("filter"))
        ]
        return _page(nodes, args)

    def _root_attachment(self, args: dict[str, Any]) -> dict[str, Any]:
        for issue in self._visible_issues(include_archived=True):
            for attachment in issue.attachments:
                if attachment.id == args["id"]:
                    return {**self._attachment(attachment), "issue": self._issue_ref(issue)}
        raise EntityNotFound(args["id"])

    def _root_documents(self, args: dict[str, Any]) -> dict[str, Any]:
        nodes = [
            {**self._document(d), "issue": self._issue_ref(i), "project": None}
            for i in self._visible_issues(include_archived=False) for d in i.documents
            if _matches({"id": d.id, "updatedAt": d.updated_at, "createdAt": d.created_at,
                         "issue": self._issue_facts(i)}, args.get("filter"))
        ] + [
            {**self._document(d), "issue": None, "project": {
                "id": p.id, "name": p.name, "teams": {"nodes": [{"id": t.id} for t in p.teams]}}}
            for p in self._visible_projects(include_archived=False) for d in p.documents
            if _matches({"id": d.id, "updatedAt": d.updated_at, "createdAt": d.created_at}, args.get("filter"))
        ]
        return _page(nodes, args)

    def _root_document(self, args: dict[str, Any]) -> dict[str, Any]:
        for issue in self._visible_issues(include_archived=True):
            for document in issue.documents:
                if document.id == args["id"]:
                    return {**self._document(document), "issue": self._issue_ref(issue), "project": None}
        raise EntityNotFound(args["id"])

    def _visible_projects(self, include_archived: bool) -> list[Project]:
        return [p for p in self.projects if any(self.can_see_team(self.sync_user, t) for t in p.teams)
                and (include_archived or not p.archived_at)]

    def _project_facts(self, project: Project) -> dict[str, Any]:
        teams = [{"id": t.id, "key": t.key} for t in project.teams]
        return {"id": project.id, "updatedAt": project.updated_at, "createdAt": project.created_at,
                "accessibleTeams": teams, "teams": teams}

    def _root_projects(self, args: dict[str, Any]) -> dict[str, Any]:
        projects = [p for p in self._visible_projects(bool(args.get("includeArchived")))
                    if _matches(self._project_facts(p), args.get("filter"))]
        projects.sort(key=lambda p: p.created_at, reverse=True)
        return _page([self._project(p) for p in projects], args)

    def _root_project(self, args: dict[str, Any]) -> dict[str, Any]:
        for project in self._visible_projects(include_archived=True):
            if project.id == args["id"]:
                return self._project(project)
        raise EntityNotFound(args["id"])

    def _root_projectUpdates(self, args: dict[str, Any]) -> dict[str, Any]:
        _matches({}, args.get("filter"), allow={"updatedAt", "createdAt", "project"})
        return _page([], args)

    # ------------------------------------------------------------------ shapes

    def _user(self, user: User | None) -> dict[str, Any] | None:
        if user is None:
            return None
        return {"id": user.id, "name": user.name, "displayName": user.name.lower(), "email": user.email,
                "avatarUrl": None, "active": user.active, "guest": user.guest, "admin": False,
                "createdAt": _iso(self.now), "updatedAt": _iso(self.now)}

    def _team(self, team: Team) -> dict[str, Any]:
        return {"id": team.id, "name": team.name, "key": team.key, "description": None,
                "private": team.visibility == "private", "visibility": team.visibility,
                "parent": {"id": team.parent.id, "name": team.parent.name, "key": team.parent.key} if team.parent else None,
                "createdAt": _iso(self.now), "updatedAt": _iso(self.now), "archivedAt": None,
                "members": {"nodes": [self._user(u) for u in team.members if u.active]}}

    def _issue_ref(self, issue: Issue) -> dict[str, Any]:
        return {"id": issue.id, "identifier": issue.identifier, "team": {"id": issue.team.id, "key": issue.team.key}}

    def _issue_facts(self, issue: Issue) -> dict[str, Any]:
        teams, team = [], issue.team
        while team is not None:  # a team filter also matches the team's sub-teams' issues
            teams.append({"id": team.id, "key": team.key})
            team = team.parent
        return {"id": issue.id, "team": tuple(teams),
                "updatedAt": issue.updated_at, "createdAt": issue.created_at,
                "archivedAt": issue.archived_at, "trashed": issue.trashed,
                "parent": {"id": issue.parent.id if issue.parent else None}}

    def _attachment(self, attachment: Attachment) -> dict[str, Any]:
        return {"id": attachment.id, "title": attachment.title, "subtitle": None, "url": attachment.url,
                "createdAt": _iso(attachment.created_at), "updatedAt": _iso(attachment.updated_at)}

    def _document(self, document: Document) -> dict[str, Any]:
        return {"id": document.id, "title": document.title, "content": document.content,
                "url": f"https://linear.app/{self.url_key}/document/{document.id}", "slugId": document.id[:12],
                "createdAt": _iso(document.created_at), "updatedAt": _iso(document.updated_at),
                "creator": None}

    def _project(self, project: Project) -> dict[str, Any]:
        url = f"https://linear.app/{self.url_key}/project/{project.id[:12]}"
        return {
            "id": project.id, "name": project.name, "description": "", "slugId": project.id[:12], "icon": None,
            "color": None, "url": url, "priority": 0, "priorityLabel": "No priority", "progress": 0, "scope": 0,
            "health": None, "content": project.content, "trashed": project.trashed or None,
            "createdAt": _iso(project.created_at), "updatedAt": _iso(project.updated_at),
            "archivedAt": _iso(project.archived_at) if project.archived_at else None,
            "startDate": None, "targetDate": None, "startedAt": None, "completedAt": None, "canceledAt": None,
            "status": {"id": "status-1", "name": "Planned", "type": "planned", "color": "#000"},
            "creator": None, "lead": None, "leadTeamId": project.lead.id if project.lead else None,
            "teams": {"nodes": [{"id": t.id, "name": t.name, "key": t.key, "private": t.visibility == "private",
                                 "visibility": t.visibility} for t in project.teams]},
            "issues": {"nodes": []},
            "externalLinks": {"nodes": [
                {"id": link.id, "url": link.url, "label": link.label, "sortOrder": 0, "creator": None,
                 "createdAt": _iso(project.created_at), "updatedAt": _iso(project.created_at)}
                for link in project.links]},
            "documents": {"nodes": [self._document(d) for d in project.documents]},
            "projectMilestones": {"nodes": []}, "comments": {"nodes": []}, "projectUpdates": {"nodes": []},
        }

    def _comment(self, issue: Issue, comment: Comment) -> dict[str, Any]:
        return {"id": comment.id, "body": comment.body,
                "url": f"https://linear.app/{self.url_key}/issue/{issue.identifier}#comment-{comment.id[:8]}",
                "createdAt": _iso(comment.created_at), "updatedAt": _iso(comment.updated_at),
                "parent": None, "user": self._user(comment.user)}

    def _issue(self, issue: Issue) -> dict[str, Any]:
        children = [c for c in self.issues if c.parent is issue and self.can_see_team(self.sync_user, c.team)]
        return {
            "id": issue.id, "identifier": issue.identifier, "number": issue.number, "title": issue.title,
            "description": issue.description, "priority": 0, "estimate": None,
            "url": f"https://linear.app/{self.url_key}/issue/{issue.identifier}",
            "createdAt": _iso(issue.created_at), "updatedAt": _iso(issue.updated_at), "completedAt": None,
            "trashed": issue.trashed or None, "archivedAt": _iso(issue.archived_at) if issue.archived_at else None,
            "canceledAt": None, "state": {"id": "state-todo", "name": "Todo", "type": "unstarted"},
            "assignee": None, "creator": None, "team": self._team(issue.team),
            "labels": {"nodes": []},
            "parent": {"id": issue.parent.id, "identifier": issue.parent.identifier} if issue.parent else None,
            "children": {"nodes": [{"id": c.id, "identifier": c.identifier} for c in children]},
            "relations": {"nodes": []},
            "comments": {"nodes": [self._comment(issue, c) for c in issue.comments]},
            "attachments": {"nodes": [self._attachment(a) for a in issue.attachments]},
            "documents": {"nodes": [self._document(d) for d in issue.documents]},
        }


# ---------------------------------------------------------------------- helpers

class _Gap(Exception):
    pass


_LITERAL = re.compile(r'(\w+)\s*:\s*(\$\w+|true|false|null|-?\d+|"[^"]*")')


def _arguments(query: str, field_name: str, variables: dict[str, Any]) -> dict[str, Any]:
    """The arguments a field is called with, its ``$variables`` resolved."""
    # Not the operation's own header: ``query users($first: Int)`` declares, it does not call.
    body = re.sub(r"\bquery\s+\w+\s*\([^)]*\)", "query", query)
    called = re.search(rf"\b{field_name}\s*\(([^)]*)\)", body)
    out: dict[str, Any] = {}
    for key, raw in _LITERAL.findall(called.group(1) if called else ""):
        if raw.startswith("$"):
            if raw[1:] in variables and variables[raw[1:]] is not None:
                out[key] = variables[raw[1:]]
        elif raw in ("true", "false"):
            out[key] = raw == "true"
        elif raw.startswith('"'):
            out[key] = raw[1:-1]
        elif raw != "null":
            out[key] = int(raw)
    return out


def _page(nodes: list[dict[str, Any]], args: dict[str, Any]) -> dict[str, Any]:
    first = min(int(args.get("first") or DEFAULT_PAGE), MAX_PAGE)
    start = int(args.get("after") or 0)
    chunk = nodes[start:start + first]
    return {"nodes": chunk, "pageInfo": {"hasNextPage": start + first < len(nodes), "hasPreviousPage": start > 0,
                                         "startCursor": str(start), "endCursor": str(start + len(chunk))}}


_COMPARATORS = {
    "eq": lambda a, b: a == b, "neq": lambda a, b: a != b,
    "in": lambda a, b: a in b, "nin": lambda a, b: a not in b,
    "gt": lambda a, b: a > b, "gte": lambda a, b: a >= b,
    "lt": lambda a, b: a < b, "lte": lambda a, b: a <= b,
    "null": lambda a, b: (a is None) == bool(b),
}


def _matches(facts: dict[str, Any], linear_filter: dict[str, Any] | None,
             allow: set[str] | None = None) -> bool:
    """Linear's filter semantics for the keys the connector sends; anything else is a gap."""
    for key, condition in (linear_filter or {}).items():
        if allow is not None and key in allow:
            continue
        if key in ("and", "or"):
            results = [_matches(facts, c) for c in condition]
            if not (all(results) if key == "and" else any(results)):
                return False
            continue
        if key not in facts:
            raise _Gap(f"the filter key {key!r}")
        value = facts[key]
        if isinstance(value, tuple):  # matches when any one of them does
            if not any(_matches(item, condition) for item in value):
                return False
            continue
        if isinstance(value, list):
            if set(condition) - {"some", "every"}:
                raise _Gap(f"the collection filter {sorted(condition)}")
            some, every = condition.get("some"), condition.get("every")
            if some is not None and not any(_matches(item, some) for item in value):
                return False
            if every is not None and not all(_matches(item, every) for item in value):
                return False
            continue
        if isinstance(value, dict):
            if not _matches(value, condition):
                return False
            continue
        for op, given in condition.items():
            if op not in _COMPARATORS:
                raise _Gap(f"the comparator {op!r}")
            expected = _ms(given) if key in ("updatedAt", "createdAt", "archivedAt") and isinstance(given, str) else given
            if value is None and op not in ("eq", "neq", "null"):
                return False
            if not _COMPARATORS[op](value, expected):
                return False
    return True


def _select(data: dict[str, Any], query: str, variables: dict[str, Any]) -> dict[str, Any]:
    """Keep the fields the query names, and page every nested list by the query's own
    arguments: 50 nodes when it gives no ``first``, as Linear does."""
    names = set(re.findall(r"[A-Za-z_]\w*", query))

    def walk(value: object, depth: int, name: str | None) -> object:
        if not isinstance(value, dict):
            return value
        out = {k: walk(v, depth + 1, k) for k, v in value.items() if k in names}
        if isinstance(out.get("nodes"), list):
            if depth > 1 and name:
                paged = _page(out["nodes"], _arguments(query, name, variables))
                out["nodes"] = paged["nodes"]
                if "pageInfo" in names:
                    out["pageInfo"] = {k: v for k, v in paged["pageInfo"].items() if k in names}
            out["nodes"] = [walk(n, depth + 1, None) for n in out["nodes"]]
        return out

    return walk(data, 0, None)
