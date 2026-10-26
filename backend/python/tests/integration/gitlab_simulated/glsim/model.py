"""The state of one simulated GitLab instance, and GitLab's own answer to "who can read what".

Every rule here names where it comes from in ``SOURCES.md``. Behaviour the
documentation leaves open is a ``Behaviour`` switch, so a scenario can run under each
answer.
"""

from __future__ import annotations

import base64
import hashlib
import itertools
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

GUEST, PLANNER, REPORTER, DEVELOPER, MAINTAINER, OWNER = 10, 15, 20, 30, 40, 50
MINIMAL = 5
START = datetime(2026, 1, 5, 9, 0, tzinfo=timezone.utc).timestamp()


def iso(t: float) -> str:
    dt = datetime.fromtimestamp(t, tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def parse_iso(value: str) -> float:
    v = value.strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(v)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def git_blob_sha(content: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(content) + content).hexdigest()


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


@dataclass
class Behaviour:
    """Switches for behaviour GitLab's documentation leaves open (see SOURCES.md)."""

    note_bumps_issue: bool = True          # a new, edited or deleted comment moves updated_at
    compare_detects_renames: bool = True   # compare reports exact-content moves as renamed_file
    compare_max_files: int = 1000          # beyond this the compare answers overflow=true
    issues_keyset: bool = True             # GitLab >= 18.3: pagination=keyset on project issues
    members_expose_public_email: bool = True  # UserBasic carries public_email (current GitLab)
    graphql_tree_page_size: int = 100
    x_total_cap: int = 10000               # above this GitLab omits x-total / x-total-pages
    rate_limit_per_minute: int | None = None  # gitlab.com: 2000 authenticated requests/min


class Clock:
    def __init__(self) -> None:
        self.t = START
        self._lock = threading.Lock()

    def advance(self, seconds: float) -> None:
        with self._lock:
            self.t += seconds

    def tick(self) -> float:
        """A fresh, strictly later instant for a write (1.5 ms apart)."""
        with self._lock:
            self.t += 0.0015
            return self.t


@dataclass(eq=False)
class User:
    id: int
    username: str
    name: str
    email: str
    public_email: str = ""
    state: str = "active"  # active | blocked | deactivated | banned | blocked_pending_approval
    is_admin: bool = False
    is_auditor: bool = False
    external: bool = False
    bot: bool = False


@dataclass
class Membership:
    access_level: int
    state: str = "active"  # membership_state: active | awaiting
    expires_at: str | None = None


@dataclass(eq=False)
class Group:
    id: int
    path: str
    name: str
    parent: "Group" | None = None
    visibility: str = "private"
    members: dict[int, Membership] = field(default_factory=dict)
    shared_with: dict[int, int] = field(default_factory=dict)  # invited group id -> max level
    deleted: bool = False

    @property
    def full_path(self) -> str:
        return f"{self.parent.full_path}/{self.path}" if self.parent else self.path

    def ancestors(self) -> list["Group"]:
        out, g = [], self.parent
        while g is not None:
            out.append(g)
            g = g.parent
        return out


@dataclass(eq=False)
class Note:
    id: int
    body: str
    author: User
    created_at: float
    updated_at: float
    system: bool = False
    internal: bool = False
    position: dict[str, Any] | None = None  # a diff note on a merge request


@dataclass(eq=False)
class Issue:
    id: int
    iid: int
    project: "Project"
    title: str
    description: str
    author: User
    created_at: float
    updated_at: float
    state: str = "opened"
    confidential: bool = False
    assignees: list[User] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    issue_type: str = "issue"
    closed_at: float | None = None
    due_date: str | None = None
    notes: list[Note] = field(default_factory=list)
    deleted: bool = False
    moved_to_id: int | None = None


@dataclass(eq=False)
class MergeRequest:
    id: int
    iid: int
    project: "Project"
    title: str
    description: str
    author: User
    created_at: float
    updated_at: float
    state: str = "opened"
    assignees: list[User] = field(default_factory=list)
    reviewers: list[User] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    notes: list[Note] = field(default_factory=list)
    files: list[dict[str, Any]] = field(default_factory=list)   # the changes payload
    commits: list[str] = field(default_factory=list)            # shas, newest first
    sha: str = ""
    merged_at: float | None = None
    merged_by: User | None = None
    closed_at: float | None = None
    deleted: bool = False


@dataclass
class Commit:
    sha: str
    tree: dict[str, bytes]
    parents: list[str]
    date: float
    message: str


@dataclass(eq=False)
class Project:
    id: int
    path: str
    name: str
    namespace: Any  # Group or User
    visibility: str = "private"
    default_branch: str = "main"
    archived: bool = False
    deleted: bool = False
    issues_enabled: bool = True
    merge_requests_enabled: bool = True
    members: dict[int, Membership] = field(default_factory=dict)
    shared_with: dict[int, int] = field(default_factory=dict)  # invited group id -> max level
    issues: dict[int, Issue] = field(default_factory=dict)
    mrs: dict[int, MergeRequest] = field(default_factory=dict)
    commits: dict[str, Commit] = field(default_factory=dict)
    branches: dict[str, str] = field(default_factory=dict)
    uploads: dict[tuple[str, str], bytes] = field(default_factory=dict)
    old_paths: list[str] = field(default_factory=list)  # redirect routes after a rename or transfer

    @property
    def path_with_namespace(self) -> str:
        ns = self.namespace.full_path if isinstance(self.namespace, Group) else self.namespace.username
        return f"{ns}/{self.path}"

    def head(self) -> Commit | None:
        sha = self.branches.get(self.default_branch)
        return self.commits.get(sha) if sha else None

    def tree(self) -> dict[str, bytes]:
        head = self.head()
        return dict(head.tree) if head else {}


@dataclass
class Token:
    value: str
    user: User
    kind: str = "pat"  # pat | oauth
    revoked: bool = False


@dataclass
class Fault:
    """Answer requests that match with a failure instead of the real reply."""

    matches: Callable[[str, str, dict[str, str]], bool]
    kind: Any  # an HTTP status, "drop" (close the socket), "truncate" (cut the body), "hang", "hook", "empty"
    times: int = 1
    skip: int = 0  # let this many matching requests through first
    headers: dict[str, str] = field(default_factory=dict)
    action: Callable[[], None] | None = None  # "hook": change GitLab, then answer normally


@dataclass
class Call:
    method: str
    path: str
    query: dict[str, str]
    status: int
    token: str | None
    resource: str


class World:
    def __init__(self, behaviour: Behaviour | None = None) -> None:
        self.behaviour = behaviour or Behaviour()
        self.clock = Clock()
        self.lock = threading.RLock()
        self._ids = itertools.count(1000)
        self.users: dict[int, User] = {}
        self.groups: dict[int, Group] = {}
        self.projects: dict[int, Project] = {}
        self.tokens: dict[str, Token] = {}
        self.faults: list[Fault] = []
        self.calls: list[Call] = []
        self.gaps: list[str] = []
        self.sync_token: Token | None = None
        self.minute_counts: dict[tuple[str, int], int] = {}
        self.bytes_sent: dict[str, int] = {}  # upload "secret/filename" -> bytes the client actually read
        self.base_url = ""  # set by the server once it listens

    # ------------------------------------------------------------------ building

    def next_id(self) -> int:
        return next(self._ids)

    def add_user(self, username: str, email: str, *, public_email: str | None = None, **kw: Any) -> User:  # noqa: ANN401
        u = User(self.next_id(), username, username.title(), email,
                 public_email=email if public_email is None else public_email, **kw)
        self.users[u.id] = u
        return u

    def add_token(self, user: User, kind: str = "pat") -> Token:
        tok = Token(f"glpat-sim-{user.username}-{self.next_id()}", user, kind)
        self.tokens[tok.value] = tok
        return tok

    def set_sync_account(self, user: User) -> Token:
        self.sync_token = self.add_token(user)
        return self.sync_token

    def add_group(self, path: str, *, parent: Group | None = None, members: dict[User, int] | None = None,
                  visibility: str = "private") -> Group:
        g = Group(self.next_id(), path, path.title(), parent, visibility)
        for u, lvl in (members or {}).items():
            g.members[u.id] = Membership(lvl)
        self.groups[g.id] = g
        return g

    def add_project(self, namespace: Any, path: str, *, members: dict[User, int] | None = None,  # noqa: ANN401
                    files: dict[str, str | bytes] | None = None, visibility: str = "private") -> Project:
        p = Project(self.next_id(), path, path, namespace, visibility)
        for u, lvl in (members or {}).items():
            p.members[u.id] = Membership(lvl)
        self.projects[p.id] = p
        self.commit(p, files or {"README.md": "# readme\n"}, message="initial")
        return p

    def commit(self, p: Project, changes: dict[str, Any], *, message: str = "change", branch: str | None = None,
               replace: bool = False) -> Commit:
        """``changes``: path -> content (str/bytes) or None to delete. ``replace`` starts from empty."""
        branch = branch or p.default_branch
        parent_sha = p.branches.get(branch)
        tree = {} if replace or parent_sha is None else dict(p.commits[parent_sha].tree)
        for path, content in changes.items():
            if content is None:
                tree.pop(path, None)
            else:
                tree[path] = content.encode() if isinstance(content, str) else content
        t = self.clock.tick()
        sha = hashlib.sha1(f"{p.id}:{t}:{message}:{sorted(tree.items())!r}".encode()).hexdigest()
        c = Commit(sha, tree, [parent_sha] if parent_sha else [], t, message)
        p.commits[sha] = c
        p.branches[branch] = sha
        return c

    def add_issue(self, p: Project, title: str, *, author: User, description: str = "", **kw: Any) -> Issue:  # noqa: ANN401
        t = self.clock.tick()
        iid = max(p.issues, default=0) + 1
        i = Issue(self.next_id(), iid, p, title, description, author, t, t, **kw)
        p.issues[iid] = i
        return i

    def touch(self, item: Any) -> None:  # noqa: ANN401
        item.updated_at = self.clock.tick()

    def add_note(self, item: Any, body: str, *, author: User, internal: bool = False, system: bool = False,  # noqa: ANN401
                 position: dict[str, Any] | None = None) -> Note:
        t = self.clock.tick()
        n = Note(self.next_id(), body, author, t, t, system, internal, position)
        item.notes.append(n)
        if self.behaviour.note_bumps_issue:
            item.updated_at = self.clock.tick()
        return n

    def add_mr(self, p: Project, title: str, *, author: User, description: str = "",
               files: list[dict[str, Any]] | None = None, **kw: Any) -> MergeRequest:  # noqa: ANN401
        t = self.clock.tick()
        iid = max(p.mrs, default=0) + 1
        sha = hashlib.sha1(f"mr{p.id}{iid}{t}".encode()).hexdigest()
        mr = MergeRequest(self.next_id(), iid, p, title, description, author, t, t, sha=sha,
                          files=files or [], commits=[sha], **kw)
        p.mrs[iid] = mr
        p.commits.setdefault(sha, Commit(sha, p.tree(), [], t, f"{title}\n\ncommit body"))
        return mr

    def add_upload(self, p: Project, filename: str, data: bytes) -> str:
        secret = hashlib.md5(f"{p.id}{filename}{self.next_id()}".encode()).hexdigest()
        p.uploads[(secret, filename)] = data
        return f"/uploads/{secret}/{filename}"

    # ------------------------------------------------------------------ lookups

    def project_by_ref(self, ref: str) -> Project | None:
        """A project by numeric id or full path (``None`` = no such project)."""
        if ref.isdigit():
            p = self.projects.get(int(ref))
            return p if p and not p.deleted else None
        for p in self.projects.values():
            if not p.deleted and p.path_with_namespace == ref:
                return p
        return None

    def project_redirect(self, ref: str) -> Project | None:
        for p in self.projects.values():
            if not p.deleted and ref in p.old_paths:
                return p
        return None

    def group_by_ref(self, ref: str) -> Group | None:
        if ref.isdigit():
            g = self.groups.get(int(ref))
            return g if g and not g.deleted else None
        for g in self.groups.values():
            if not g.deleted and g.full_path == ref:
                return g
        return None

    # ------------------------------------------------------------------ GitLab's access rules

    def group_member_rows(self, g: Group) -> dict[int, tuple[Membership, str]]:
        """``/groups/:id/members/all``: direct, inherited (ancestors) and shared-in groups."""
        rows: dict[int, tuple[Membership, str]] = {}

        def offer(uid: int, m: Membership, how: str) -> None:
            cur = rows.get(uid)
            if cur is None or m.access_level > cur[0].access_level:
                rows[uid] = (m, how)

        for anc in [g, *g.ancestors()]:
            for uid, m in anc.members.items():
                offer(uid, m, "direct" if anc is g else "inherited")
            for gid, cap in anc.shared_with.items():
                invited = self.groups.get(gid)
                if invited is None or invited.deleted:
                    continue
                for uid, (m, _) in self.group_member_rows(invited).items():
                    offer(uid, Membership(min(m.access_level, cap), m.state, m.expires_at), "shared")
        return rows

    def project_member_rows(self, p: Project) -> dict[int, tuple[Membership, str]]:
        """``/projects/:id/members/all``: direct, inherited from the namespace's groups, invited groups."""
        rows: dict[int, tuple[Membership, str]] = {}

        def offer(uid: int, m: Membership, how: str) -> None:
            cur = rows.get(uid)
            if cur is None or m.access_level > cur[0].access_level:
                rows[uid] = (m, how)

        for uid, m in p.members.items():
            offer(uid, m, "direct")
        if isinstance(p.namespace, Group):
            for uid, (m, _) in self.group_member_rows(p.namespace).items():
                if m.access_level >= GUEST:  # Minimal Access does not reach projects
                    offer(uid, m, "inherited")
        elif isinstance(p.namespace, User):
            offer(p.namespace.id, Membership(OWNER), "owner")
        for gid, cap in p.shared_with.items():
            invited = self.groups.get(gid)
            if invited is None or invited.deleted:
                continue
            for uid, (m, _) in self.group_member_rows(invited).items():
                offer(uid, Membership(min(m.access_level, cap), m.state, m.expires_at), "shared")
        return rows

    def access_level(self, u: User | None, p: Project) -> int:
        """The role GitLab gives ``u`` on ``p`` (0 = none). Blocked users and pending
        (awaiting) memberships give nothing."""
        if u is None or u.state != "active" or p.deleted:
            return 0
        if u.is_admin:
            return OWNER
        row = self.project_member_rows(p).get(u.id)
        if row is None or row[0].state != "active":
            return 0
        return row[0].access_level if row[0].access_level >= GUEST else 0

    def can_read_project(self, u: User | None, p: Project) -> bool:
        if p.deleted:
            return False
        if u is not None and u.state != "active":
            return False
        if p.visibility == "public":
            return True
        if p.visibility == "internal" and u is not None and not u.external:
            return True
        return self.access_level(u, p) >= GUEST or bool(u and (u.is_admin or u.is_auditor) and u.state == "active")

    def can_read_issue(self, u: User | None, i: Issue) -> bool:
        p = i.project
        if i.deleted or not p.issues_enabled or not self.can_read_project(u, p):
            return False
        if not i.confidential:
            return True
        if u is None:
            return False
        if u.is_admin or u.is_auditor or self.access_level(u, p) >= PLANNER:
            return True
        return u.id == i.author.id or any(a.id == u.id for a in i.assignees)

    def can_read_internal_note(self, u: User | None, p: Project) -> bool:
        return bool(u and u.state == "active" and (u.is_admin or u.is_auditor or self.access_level(u, p) >= PLANNER))

    def can_read_code(self, u: User | None, p: Project) -> bool:
        """Repository and merge requests: Reporter+ on a private project; any reader of a
        public or internal project."""
        if not self.can_read_project(u, p):
            return False
        if p.visibility in ("public", "internal"):
            return True
        return bool(u and (u.is_admin or u.is_auditor)) or self.access_level(u, p) >= REPORTER

    def can_read_mr(self, u: User | None, mr: MergeRequest) -> bool:
        return not mr.deleted and mr.project.merge_requests_enabled and self.can_read_code(u, mr.project)

    def visible_groups(self, u: User) -> list[Group]:
        out = []
        for g in self.groups.values():
            if g.deleted:
                continue
            if u.is_admin or g.visibility == "public" or (g.visibility == "internal" and not u.external):
                out.append(g)
                continue
            row = self.group_member_rows(g).get(u.id)
            if row and row[0].state == "active" and row[0].access_level >= MINIMAL:
                out.append(g)
                continue
            # A member of a descendant can see the ancestors' names.
            if any(self.group_member_rows(d).get(u.id) for d in self.groups.values() if g in d.ancestors()):
                out.append(g)
        return out
