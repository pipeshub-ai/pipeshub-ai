"""In-memory GitHub: accounts, organisations, teams, repositories and their content.

Access follows GitHub's documented rules (docs/SOURCES.md has the quotes):
- an organisation owner is admin on every repository of the organisation;
- a member gets the organisation's base permission, the role of every team they are in
  (a child team's members also get the parent team's access), and any direct grant;
- an outside collaborator gets only direct grants;
- public repositories are readable by everyone, internal ones by organisation members,
  private ones only through a role.
The highest role wins.
"""

from __future__ import annotations

import base64
import hashlib
import itertools
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

ROLES = ["read", "triage", "write", "maintain", "admin"]  # ascending
ROLE_PERMISSION = {"read": "pull", "triage": "triage", "write": "push", "maintain": "maintain", "admin": "admin"}

_ids = itertools.count(1000)


def next_id() -> int:
    return next(_ids)


def rank(role: str | None) -> int:
    return ROLES.index(role) + 1 if role in ROLES else 0


def best(*roles: str | None) -> str | None:
    present = [r for r in roles if r]
    return max(present, key=rank) if present else None


def git_blob_sha(content: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(content) + content).hexdigest()


class Clock:
    """Seconds since the epoch; GitHub timestamps have one-second resolution."""

    def __init__(self, start: int = 1_790_000_000) -> None:
        self.t = float(start)

    def advance(self, seconds: float) -> None:
        self.t += seconds

    def now(self) -> int:
        return int(self.t)

    def now_ms(self) -> int:
        return int(self.t * 1000)


def iso(seconds: int) -> str:
    import datetime as _dt
    return _dt.datetime.fromtimestamp(seconds, tz=_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class Behaviour:
    """Switches for behaviour the documentation leaves open. Each scenario that
    depends on one runs under every answer (see docs/SOURCES.md)."""

    # Does editing or deleting a comment change the issue's updated_at? Not documented.
    comment_edit_bumps_issue: bool = True
    # Recursive trees above this many entries come back truncated (GitHub: 100,000 / 7 MB).
    tree_truncate_over: int = 100_000
    # Compare reports a pure move as "renamed" (GitHub's rename detection) rather than
    # as removed + added.
    compare_detects_renames: bool = True
    # github.com/user-attachments with an API token: live-verified 404 (even on a public
    # repository); without one, 302 to signed storage for a public repository.
    attachment_token_accepted: bool = False
    # The issues listing refuses ``page`` once page x per_page reaches this many items
    # (422, "use cursor based pagination"); its Link header carries an ``after`` cursor
    # instead. Live: apache/airflow at per_page=100, page 99 -> 200, page 100 -> 422.
    issues_page_limit: int = 10_000


@dataclass
class Token:
    value: str
    user_id: int
    scopes: tuple[str, ...] = ("repo", "read:org", "user:email")
    revoked: bool = False


@dataclass
class User:
    id: int
    login: str
    name: str | None
    email: str | None  # public profile email; None when private
    corporate_email: str | None = None  # shown via organizationVerifiedDomainEmails
    type: str = "User"


@dataclass
class Team:
    id: int
    slug: str
    org: "Org"
    parent: "Team" | None = None
    members: set[int] = field(default_factory=set)
    repo_roles: dict[int, str] = field(default_factory=dict)

    def all_members(self, ws: "World") -> set[int]:
        """Members of this team and of every child team."""
        out = set(self.members)
        for t in self.org.teams.values():
            if t.parent is self:
                out |= t.all_members(ws)
        return out


@dataclass
class Org:
    id: int
    login: str
    members: dict[int, str] = field(default_factory=dict)  # user id -> "admin" | "member"
    base_permission: str | None = "read"  # None = "No permission"
    verified_domain: bool = False
    teams: dict[str, Team] = field(default_factory=dict)
    type: str = "Organization"


@dataclass
class Comment:
    id: int
    body: str
    user: User
    created_at: int
    updated_at: int


@dataclass
class Review:
    id: int
    body: str
    user: User
    state: str
    submitted_at: int


@dataclass
class ReviewComment:
    id: int
    body: str
    user: User
    path: str
    line: int | None
    in_reply_to_id: int | None
    created_at: int
    updated_at: int


@dataclass
class Issue:
    number: int
    id: int
    repo: "Repo"
    title: str
    body: str | None
    user: User
    created_at: int
    updated_at: int
    state: str = "open"
    state_reason: str | None = None
    assignees: list[User] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    comments: list[Comment] = field(default_factory=list)
    deleted: bool = False
    transferred_to: "Issue" | None = None
    closed_at: int | None = None
    parent: "Issue" | None = None  # sub-issue of


@dataclass
class PullRequest:
    number: int
    id: int
    repo: "Repo"
    title: str
    body: str | None
    user: User
    created_at: int
    updated_at: int
    head_sha: str
    base_sha: str
    state: str = "open"
    merged_at: int | None = None
    assignees: list[User] = field(default_factory=list)
    requested_reviewers: list[User] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    comments: list[Comment] = field(default_factory=list)
    reviews: list[Review] = field(default_factory=list)
    review_comments: list[ReviewComment] = field(default_factory=list)
    files: list[dict[str, Any]] = field(default_factory=list)  # diff-entry fields
    commits: list[str] = field(default_factory=list)  # shas


@dataclass
class Commit:
    sha: str
    parent: str | None
    tree: dict[str, bytes]
    date: int
    message: str


@dataclass
class Repo:
    id: int
    owner: Any  # Org | User
    name: str
    visibility: str = "private"
    default_branch: str = "main"
    archived: bool = False
    has_issues: bool = True
    deleted: bool = False
    collaborators: dict[int, str] = field(default_factory=dict)  # direct grants
    issues: dict[int, Issue] = field(default_factory=dict)
    pulls: dict[int, PullRequest] = field(default_factory=dict)
    commits: dict[str, Commit] = field(default_factory=dict)
    branches: dict[str, str] = field(default_factory=dict)
    old_names: list[str] = field(default_factory=list)  # (owner_login/name) that redirect here
    _number: int = 0

    @property
    def full_name(self) -> str:
        return f"{self.owner.login}/{self.name}"

    def next_number(self) -> int:
        self._number += 1
        return self._number

    def head(self, branch: str | None = None) -> Commit | None:
        sha = self.branches.get(branch or self.default_branch)
        return self.commits.get(sha) if sha else None


@dataclass
class Upload:
    url: str
    repo: Repo
    data: bytes
    content_type: str


@dataclass
class Fault:
    """An injected failure. ``kind``: status | drop | slow | secondary | primary."""

    match: Callable[[str, str, dict[str, str]], bool]
    kind: str = "status"
    status: int = 502
    times: int | None = 1  # None = always
    seconds: float = 0.0
    hits: int = 0
    action: Callable[[], None] | None = None  # kind "hook": change GitHub, then answer normally


@dataclass
class Call:
    method: str
    host: str
    path: str
    query: dict[str, str]
    status: int
    resource: str | None
    authorization: bool


class World:
    def __init__(self, behaviour: Behaviour | None = None) -> None:
        self.behaviour = behaviour or Behaviour()
        self.clock = Clock()
        self.users: dict[int, User] = {}
        self.orgs: dict[str, Org] = {}
        self.repos: dict[int, Repo] = {}
        self.tokens: dict[str, Token] = {}
        self.uploads: dict[str, Upload] = {}
        self.faults: list[Fault] = []
        self.calls: list[Call] = []
        self.gaps: list[str] = []
        self.bytes_read: dict[str, int] = {}  # url -> body bytes the client actually read
        self.sync_token: Token | None = None
        self.issue_numbers_lag = False  # GraphQL lists no issue numbers yet (search index lag)
        # (resource, token value) -> [window reset epoch, used]
        self.rate: dict[tuple[str, str], list[int]] = {}
        self.rate_limits = {"core": 5000, "graphql": 5000, "search": 30}

    # ------------------------------------------------------------ building

    def tick(self, seconds: float = 1) -> None:
        self.clock.advance(seconds)

    def add_user(self, login: str, email: str | None = None, *, name: str | None = None,
                 corporate_email: str | None = None, type: str = "User") -> User:  # noqa: A002 - GitHub's field name
        user = User(next_id(), login, name or login, email, corporate_email, type)
        self.users[user.id] = user
        return user

    def user(self, login: str) -> User:
        return next(u for u in self.users.values() if u.login.lower() == login.lower())

    def add_org(self, login: str, *, owners: list[User] = (), members: list[User] = (),
                base_permission: str | None = "read", verified_domain: bool = False) -> Org:
        org = Org(next_id(), login, base_permission=base_permission, verified_domain=verified_domain)
        for u in owners:
            org.members[u.id] = "admin"
        for u in members:
            org.members[u.id] = "member"
        self.orgs[login.lower()] = org
        return org

    def add_team(self, org: Org, slug: str, members: list[User] = (), *, parent: Team | None = None) -> Team:
        team = Team(next_id(), slug, org, parent, {u.id for u in members})
        org.teams[slug] = team
        return team

    def add_repo(self, owner: Any, name: str, *, visibility: str = "private",  # noqa: ANN401
                 files: dict[str, str] | None = None, default_branch: str = "main") -> Repo:
        repo = Repo(next_id(), owner, name, visibility=visibility, default_branch=default_branch)
        self.repos[repo.id] = repo
        self.commit(repo, files if files is not None else {"README.md": f"# {name}\n"}, "initial", replace=True)
        return repo

    def token_for(self, user: User, value: str | None = None, **kw: Any) -> Token:  # noqa: ANN401
        token = Token(value or f"ghp_{uuid.uuid4().hex}", user.id, **kw)
        self.tokens[token.value] = token
        return token

    def set_sync_account(self, user: User, **kw: Any) -> Token:  # noqa: ANN401
        self.sync_token = self.token_for(user, **kw)
        return self.sync_token

    # ------------------------------------------------------------ git

    def commit(self, repo: Repo, changes: dict[str, str | None], message: str = "change", *,
               branch: str | None = None, replace: bool = False, parent: Any = "head") -> Commit:  # noqa: ANN401
        """Commit ``changes`` (path -> text, None deletes). ``replace`` starts a fresh
        tree; ``parent=None`` makes an unrelated history (a force push)."""
        branch = branch or repo.default_branch
        head = repo.head(branch)
        tree = {} if replace or head is None else dict(head.tree)
        for path, text in changes.items():
            if text is None:
                tree.pop(path, None)
            else:
                tree[path] = text.encode()
        self.tick(1)
        parent_sha = (head.sha if head else None) if parent == "head" else parent
        sha = hashlib.sha1(f"{repo.id}:{parent_sha}:{sorted(tree.items())}:{self.clock.now()}:{message}".encode()).hexdigest()
        c = Commit(sha, parent_sha, tree, self.clock.now(), message)
        repo.commits[sha] = c
        repo.branches[branch] = sha
        return c

    def ancestors(self, repo: Repo, sha: str) -> list[str]:
        out, cur = [], sha
        while cur:
            out.append(cur)
            cur = repo.commits[cur].parent
        return out

    def resolve(self, repo: Repo, ref: str | None) -> str | None:
        """The commit a ref names, as git does: HEAD (the default branch), a branch or a
        full commit sha; None when it names nothing."""
        if ref in (None, "", "HEAD"):
            head = repo.head()
            return head.sha if head else None
        if ref in repo.branches:
            return repo.branches[ref]
        return ref if ref in repo.commits else None

    def path_history(self, repo: Repo, path: str, sha: str | None = None) -> list[Commit]:
        """Commits reachable from ``sha`` (default: the default branch) that changed
        ``path``, newest first."""
        start = sha or self.resolve(repo, None)
        out = []
        for commit_sha in self.ancestors(repo, start) if start else []:
            c = repo.commits[commit_sha]
            prev = repo.commits[c.parent].tree.get(path) if c.parent else None
            if c.tree.get(path) != prev and (c.tree.get(path) is not None or prev is not None):
                out.append(c)
        return out

    # ------------------------------------------------------------ issues / PRs

    def add_issue(self, repo: Repo, title: str, *, body: str = "", user: User | None = None,
                  assignees: list[User] = (), labels: list[str] = (), state: str = "open") -> Issue:
        self.tick(1)
        now = self.clock.now()
        issue = Issue(repo.next_number(), next_id(), repo, title, body, user or self.users[self.sync_token.user_id],
                      now, now, state=state, assignees=list(assignees), labels=list(labels))
        repo.issues[issue.number] = issue
        return issue

    def edit_issue(self, issue: Issue, **changes: Any) -> None:  # noqa: ANN401
        self.tick(1)
        for k, v in changes.items():
            setattr(issue, k, v)
        if changes.get("state") == "closed":
            issue.closed_at = self.clock.now()
        issue.updated_at = self.clock.now()

    def comment(self, issue: Any, body: str, user: User | None = None) -> Comment:  # noqa: ANN401
        self.tick(1)
        c = Comment(next_id(), body, user or self.users[self.sync_token.user_id], self.clock.now(), self.clock.now())
        issue.comments.append(c)
        issue.updated_at = self.clock.now()  # documented: a new comment updates the issue
        return c

    def edit_comment(self, issue: Any, comment: Comment, body: str | None) -> None:  # noqa: ANN401
        """Edit, or delete with ``body=None``."""
        self.tick(1)
        if body is None:
            issue.comments.remove(comment)
        else:
            comment.body, comment.updated_at = body, self.clock.now()
        if self.behaviour.comment_edit_bumps_issue:
            issue.updated_at = self.clock.now()

    def delete_issue(self, issue: Issue) -> None:
        issue.deleted = True

    def transfer_issue(self, issue: Issue, target: Repo) -> Issue:
        self.tick(1)
        moved = Issue(target.next_number(), issue.id, target, issue.title, issue.body, issue.user,
                      issue.created_at, self.clock.now(), state=issue.state, assignees=list(issue.assignees),
                      labels=list(issue.labels), comments=list(issue.comments))
        target.issues[moved.number] = moved
        issue.transferred_to = moved
        return moved

    def add_pull(self, repo: Repo, title: str, *, body: str = "", user: User | None = None,
                 changes: dict[str, str | None] | None = None, state: str = "open") -> PullRequest:
        base = repo.head()
        branch = f"pr-{repo._number + 1}"
        repo.branches[branch] = base.sha
        head = self.commit(repo, changes or {"pr.txt": title}, f"pr {title}", branch=branch)
        self.tick(1)
        now = self.clock.now()
        files = []
        for path, text in (changes or {"pr.txt": title}).items():
            status = "removed" if text is None else ("modified" if path in base.tree else "added")
            files.append({"filename": path, "status": status, "additions": 1 if text else 0,
                          "deletions": 0 if text else 1, "changes": 1, "patch": f"@@ -0,0 +1 @@\n+{text or ''}",
                          "sha": git_blob_sha((text or "").encode())})
        pr = PullRequest(repo.next_number(), next_id(), repo, title, body, user or self.users[self.sync_token.user_id],
                         now, now, head.sha, base.sha, state=state, files=files, commits=[head.sha])
        repo.pulls[pr.number] = pr
        return pr

    def edit_pull(self, pr: PullRequest, **changes: Any) -> None:  # noqa: ANN401
        self.tick(1)
        for k, v in changes.items():
            setattr(pr, k, v)
        pr.updated_at = self.clock.now()

    def upload(self, repo: Repo, name: str, data: bytes, *, image: bool = False) -> str:
        if image:
            url = f"https://github.com/user-attachments/assets/{uuid.uuid4()}"
        else:
            url = f"https://github.com/user-attachments/files/{next_id()}/{name}"
        self.uploads[url] = Upload(url, repo, data, "image/png" if image else "application/octet-stream")
        return url

    # ------------------------------------------------------------ access

    def org_of(self, repo: Repo) -> Org | None:
        return repo.owner if isinstance(repo.owner, Org) else None

    def role(self, user: User | None, repo: Repo) -> str | None:
        """The user's highest role on the repository from every source of grants."""
        if user is None or repo.deleted:
            return None
        org = self.org_of(repo)
        if org is None:
            return "admin" if repo.owner.id == user.id else repo.collaborators.get(user.id)
        membership = org.members.get(user.id)
        if membership == "admin":
            return "admin"
        roles = [repo.collaborators.get(user.id)]
        if membership == "member":
            roles.append(org.base_permission)
            roles.extend(
                team.repo_roles[repo.id] for team in org.teams.values()
                if repo.id in team.repo_roles and user.id in team.all_members(self)
            )
        return best(*roles)

    def can_read(self, user: User | None, repo: Repo) -> bool:
        if user is None or repo.deleted:
            return False
        if repo.visibility == "public":
            return True
        org = self.org_of(repo)
        if repo.visibility == "internal" and org is not None and user.id in org.members:
            return True
        return rank(self.role(user, repo)) >= rank("read")

    def collaborators(self, repo: Repo) -> list[tuple[User, str]]:
        """``affiliation=all``: everyone holding a role, by any source."""
        out = []
        for user in self.users.values():
            role = self.role(user, repo)
            if role:
                out.append((user, role))
        return out

    def repo_by_name(self, owner: str, name: str) -> Repo | None:
        for repo in self.repos.values():
            if repo.full_name.lower() == f"{owner}/{name}".lower() and not repo.deleted:
                return repo
        for repo in self.repos.values():
            if f"{owner}/{name}".lower() in (n.lower() for n in repo.old_names) and not repo.deleted:
                return repo
        return None

    def rename_repo(self, repo: Repo, name: str) -> None:
        repo.old_names.append(repo.full_name)
        repo.name = name

    def transfer_repo(self, repo: Repo, owner: Any) -> None:  # noqa: ANN401
        repo.old_names.append(repo.full_name)
        repo.owner = owner

    def token_user(self, token: Token | None) -> User | None:
        return self.users.get(token.user_id) if token else None


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()
