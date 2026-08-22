import json
import logging
from typing import List, Literal, Optional, Tuple

from pydantic import BaseModel, Field, field_validator

from app.agent_loop_lib.tools.base import ParameterType, Tag, ToolParameter
from app.agent_loop_lib.tools.decorators import tool
from app.agents.actions.util.tool_summaries import (
    args_template,
    entity_summary,
    list_summary,
)
from app.connectors.core.registry.auth_builder import (
    AuthBuilder,
    AuthType,
    OAuthScopeConfig,
)
from app.connectors.core.constants import IconPaths
from app.connectors.core.registry.connector_builder import CommonFields
from app.connectors.core.registry.tool_builder import (
    ToolsetBuilder,
    ToolsetCategory,
)
from app.connectors.core.registry.types import DocumentationLink
from app.sources.client.github.github import GitHubClient, GitHubResponse
from app.sources.external.github.github_ import GitHubDataSource

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Agent-activity summary labels — see `jira.py`'s equivalent block. GitHub's
# `_handle_response` puts list endpoints' items directly at `data` (a bare
# list, not `data.<key>`), so `list_summary` below is called with the
# explicit path `("data",)` rather than the string shorthand.
# ---------------------------------------------------------------------------


def _github_repo_label(repo: dict) -> str:
    return repo.get("full_name") or repo.get("name") or "?"


def _github_issue_label(issue: dict) -> str:
    number = issue.get("number")
    title = issue.get("title") or ""
    return f"#{number}: {title}" if number is not None else (title or "?")


# PRs come back from the GitHub API as issue-shaped objects (number + title),
# so the label is identical — kept as a separate name for readability at call sites.
_github_pr_label = _github_issue_label


def _github_comment_label(comment: dict) -> str:
    user = comment.get("user")
    login = user.get("login") if isinstance(user, dict) else None
    body = (comment.get("body") or "").strip().splitlines()[0][:60] if comment.get("body") else ""
    if login and body:
        return f"{login}: {body}"
    return login or body or "?"


def _github_review_label(review: dict) -> str:
    user = review.get("user")
    login = user.get("login") if isinstance(user, dict) else "?"
    return f"{login}: {review.get('state') or '?'}"


def _github_owner_label(owner: dict) -> str:
    return owner.get("login") or "?"


def _github_commit_label(commit: dict) -> str:
    sha = (commit.get("sha") or "")[:7] or "?"
    message = ((commit.get("commit") or {}).get("message") or "").splitlines()[0][:60]
    return f"{sha}: {message}" if message else sha


def _github_file_change_label(entry: dict) -> str:
    return f"{entry.get('filename', '?')} ({entry.get('status', '?')})"


# ---------------------------------------------------------------------------
# Pydantic input schemas
# ---------------------------------------------------------------------------

class CreateRepositoryInput(BaseModel):
    name: str = Field(description="Repository name from the user query. Only required field.")
    private: bool = Field(default=True, description="Whether the repository should be private. Default True. Do not ask the user if not specified.")
    description: Optional[str] = Field(default=None, description="Short description of the repository. Optional. Omit if user did not provide; do not ask.")
    auto_init: bool = Field(default=True, description="Initialize with a README. Default True. Do not ask the user if not specified.")


class GetRepositoryInput(BaseModel):
    owner: str = Field(description="Repository owner (username or org). For 'my' repo call get_owner(owner='me') first and use the returned 'login' here.")
    repo: str = Field(description="Repository name")


class GetOwnerInput(BaseModel):
    owner: str = Field(
        description="GitHub username or organization login. Use 'me' only here to get the authenticated user's profile (returns login for use in other tools).",
    )
    owner_type: Literal["user", "organization"] = Field(
        default="user",
        description="Type of owner: 'user' for a user account, 'organization' for an org",
    )


class ListRepositoriesInput(BaseModel):
    user: str = Field(
        description="GitHub username whose repositories to list. For the authenticated user, call get_owner(owner='me') first and use the returned 'login' here.",
    )
    type: Literal["all", "owner", "member"] = Field(default="owner", description="Filter: 'all', 'owner', or 'member'")
    per_page: Optional[int] = Field(default=None, ge=1, le=50, description="Number of repos per page. Default 10 when omitted; max 50.")
    page: Optional[int] = Field(default=None, ge=1, description="Page number (1-based). Default 1 when omitted.")


class CreateIssueInput(BaseModel):
    owner: str = Field(description="Repository owner (username or org). For 'my' repo call get_owner(owner='me') first and use the returned 'login' here.")
    repo: str = Field(description="Repository name")
    title: str = Field(description="Issue title")
    body: Optional[str] = Field(default=None, description="Issue body/description")
    assignees: Optional[List[str]] = Field(default=None, description="GitHub usernames to assign")
    labels: Optional[List[str]] = Field(default=None, description="Label names to apply")


class GetIssueInput(BaseModel):
    owner: str = Field(description="Repository owner (username or org). For 'my' repo call get_owner(owner='me') first and use the returned 'login' here.")
    repo: str = Field(description="Repository name")
    number: int = Field(description="Issue number")


class ListIssuesInput(BaseModel):
    owner: str = Field(description="Repository owner (username or org). For 'my' repo call get_owner(owner='me') first and use the returned 'login' here.")
    repo: str = Field(description="Repository name")
    state: Literal["open", "closed", "all"] = Field(default="open", description="Filter: 'open', 'closed', or 'all'")
    labels: Optional[List[str]] = Field(default=None, description="Filter by label names")
    assignee: Optional[str] = Field(default=None, description="Filter by assignee username")
    per_page: int = Field(default=10, ge=1, le=50, description="Issues per page (default 10, max 50).")
    page: int = Field(default=1, ge=1, description="Page number (1-based).")

    @field_validator("assignee", mode="before")
    @classmethod
    def normalize_assignee(cls, v: object) -> Optional[str]:
        if v is None:
            return None
        s = str(v).strip() if v else None
        return s if s else None


class CloseIssueInput(BaseModel):
    owner: str = Field(description="Repository owner (username or org). For 'my' repo call get_owner(owner='me') first and use the returned 'login' here.")
    repo: str = Field(description="Repository name")
    number: int = Field(description="Issue number to close")


def _normalize_assignees(v: object) -> Optional[List[str]]:
    """Accept list of strings or list of dicts (from get_issue) and return list of logins."""
    if v is None:
        return None
    if not isinstance(v, list):
        return None
    out: List[str] = []
    for item in v:
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, dict) and "login" in item:
            out.append(str(item["login"]))
        elif isinstance(item, dict):
            out.append(str(item.get("login", item)))
        else:
            out.append(str(item))
    return out if out else None


def _normalize_labels(v: object) -> Optional[List[str]]:
    """Accept list of strings or list of dicts (from get_issue) and return list of label names."""
    if v is None:
        return None
    if not isinstance(v, list):
        return None
    out: List[str] = []
    for item in v:
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, dict) and "name" in item:
            out.append(str(item["name"]))
        elif isinstance(item, dict):
            out.append(str(item.get("name", item)))
        else:
            out.append(str(item))
    return out if out else None


class UpdateIssueInput(BaseModel):
    owner: str = Field(description="Repository owner (username or org). For 'my' repo call get_owner(owner='me') first and use the returned 'login' here.")
    repo: str = Field(description="Repository name")
    number: int = Field(description="Issue number to update")
    title: Optional[str] = Field(default=None, description="New title (omit to leave unchanged)")
    body: Optional[str] = Field(default=None, description="New body/description (omit to leave unchanged)")
    state: Optional[Literal["open", "closed"]] = Field(default=None, description="'open' or 'closed' (omit to leave unchanged)")
    assignees: Optional[List[str]] = Field(default=None, description="Replace assignees with these usernames (omit to leave unchanged)")
    labels: Optional[List[str]] = Field(default=None, description="Replace labels with these names (omit to leave unchanged)")

    @field_validator("title", "body", mode="before")
    @classmethod
    def normalize_empty_title_body(cls, v: object) -> Optional[str]:
        """Treat empty or whitespace-only strings as omitted (None) so GitHub doesn't receive invalid empty values."""
        if v is None:
            return None
        s = str(v).strip()
        return s if s else None

    @field_validator("assignees", mode="before")
    @classmethod
    def coerce_assignees(cls, v: object) -> Optional[List[str]]:
        return _normalize_assignees(v)

    @field_validator("labels", mode="before")
    @classmethod
    def coerce_labels(cls, v: object) -> Optional[List[str]]:
        return _normalize_labels(v)


class ListIssueCommentsInput(BaseModel):
    owner: str = Field(description="Repository owner (username or org). For 'my' repo call get_owner(owner='me') first and use the returned 'login' here.")
    repo: str = Field(description="Repository name")
    number: int = Field(description="Issue number")


class GetIssueCommentInput(BaseModel):
    owner: str = Field(description="Repository owner (username or org). For 'my' repo call get_owner(owner='me') first and use the returned 'login' here.")
    repo: str = Field(description="Repository name")
    number: int = Field(description="Issue number")
    comment_id: int = Field(description="Comment ID (from list_issue_comments)")


class CreateIssueCommentInput(BaseModel):
    owner: str = Field(description="Repository owner (username or org). For 'my' repo call get_owner(owner='me') first and use the returned 'login' here.")
    repo: str = Field(description="Repository name")
    number: int = Field(description="Issue number")
    body: str = Field(description="Comment text (Markdown supported)")


class ListPullRequestCommentsInput(BaseModel):
    owner: str = Field(description="Repository owner (username or org). For 'my' repo call get_owner(owner='me') first and use the returned 'login' here.")
    repo: str = Field(description="Repository name")
    number: int = Field(description="Pull request number")


class CreatePullRequestReviewCommentInput(BaseModel):
    """Create a new line-level or file-level review comment on a PR."""
    owner: str = Field(description="Repository owner (username or org). For 'my' repo call get_owner(owner='me') first and use the returned 'login' here.")
    repo: str = Field(description="Repository name")
    number: int = Field(description="Pull request number")
    body: str = Field(description="Comment text")
    commit_id: str = Field(
        description="Commit SHA. Call get_pull_request_commits first, then use placeholder: {{github.get_pull_request_commits.last_commit_sha}}. Do NOT use data[-1].sha.",
    )
    path: str = Field(description="File path (e.g. 'src/main.py')")
    line: Optional[int] = Field(default=None, description="Line number in the file")
    side: Optional[str] = Field(default=None, description="'LEFT' or 'RIGHT' for diff side")


class CreatePullRequestInput(BaseModel):
    owner: str = Field(description="Repository owner (username or org). For 'my' repo call get_owner(owner='me') first and use the returned 'login' here.")
    repo: str = Field(description="Repository name")
    title: str = Field(description="Pull request title")
    head: str = Field(description="Source branch to merge from")
    base: str = Field(description="Target branch to merge into")
    body: Optional[str] = Field(default=None, description="Pull request description")
    draft: bool = Field(default=False, description="Whether to create as a draft PR")

class GetPullRequestInput(BaseModel):
    owner: str = Field(description="Repository owner (username or org). For 'my' repo call get_owner(owner='me') first and use the returned 'login' here.")
    repo: str = Field(description="Repository name")
    number: int = Field(description="Pull request number")

class GetPullRequestFileChangesInput(BaseModel):
    owner: str = Field(description="Repository owner (username or org). For 'my' repo call get_owner(owner='me') first and use the returned 'login' here.")
    repo: str = Field(description="Repository name")
    number: int = Field(description="Pull request number")
    fetch_full_content: bool = Field(default=True)
    max_changes_per_file: int = Field(
        default=10000,
        description="Skip files with more than this many changes to prevent context overflow"
    )
    max_diff_lines: int = Field(
        default=10000,
        description="Truncate diffs longer than this to prevent context overflow"
    )
    context_lines: int = Field(
        default=2,
        description="Number of context lines around changes (1=minimal, 3=standard, 10=verbose)"
    )

class CreatePullRequestReviewInput(BaseModel):
    owner: str = Field(description="Repository owner (username or org). For 'my' repo call get_owner(owner='me') first and use the returned 'login' here.")
    repo: str = Field(description="Repository name")
    number: int = Field(description="Pull request number")
    event: Literal["APPROVE", "REQUEST_CHANGES", "COMMENT"] = Field(
        default="COMMENT",
        description="Review outcome: APPROVE, REQUEST_CHANGES, or COMMENT (general comment without approve/changes). Default COMMENT.",
    )
    body: Optional[str] = Field(default=None, description="Review summary text (optional for APPROVE; recommended for REQUEST_CHANGES or COMMENT)")


class GetPullRequestCommitsInput(BaseModel):
    owner: str = Field(description="Repository owner (username or org). For 'my' repo call get_owner(owner='me') first and use the returned 'login' here.")
    repo: str = Field(description="Repository name")
    number: int = Field(description="Pull request number")


class ListPullRequestsInput(BaseModel):
    owner: str = Field(description="Repository owner (username or org). For 'my' repo call get_owner(owner='me') first and use the returned 'login' here.")
    repo: str = Field(description="Repository name")
    state: Literal["open", "closed", "all"] = Field(default="open", description="Filter: 'open', 'closed', or 'all'")
    head: Optional[str] = Field(default=None, description="Filter by head branch name")
    base: Optional[str] = Field(default=None, description="Filter by base branch name")
    per_page: int = Field(default=10, ge=1, le=50, description="PRs per page (default 10, max 50).")
    page: int = Field(default=1, ge=1, description="Page number (1-based).")

    @field_validator("head", "base", mode="before")
    @classmethod
    def normalize_branch(cls, v: object) -> Optional[str]:
        if v is None:
            return None
        s = str(v).strip() if v else None
        return s if s else None


class MergePullRequestInput(BaseModel):
    owner: str = Field(description="Repository owner (username or org). For 'my' repo call get_owner(owner='me') first and use the returned 'login' here.")
    repo: str = Field(description="Repository name")
    number: int = Field(description="Pull request number to merge")
    commit_message: Optional[str] = Field(default=None, description="Custom commit message for the merge")
    merge_method: Literal["merge", "squash", "rebase"] = Field(default="merge", description="Merge method: 'merge', 'squash', or 'rebase'")


class SearchRepositoriesInput(BaseModel):
    query: str = Field(
        description="GitHub search query. By default searches name, description, and topics. "
        "Use 'X in:name' to search only repo name, 'X in:description' for description, or combine (e.g. 'python in:name,description'). "
        "Examples: 'machine learning', 'react in:name', 'language:python stars:>100'"
    )
    per_page: Optional[int] = Field(default=None, ge=1, le=50, description="Results per page. Default 10 when omitted; max 50.")
    page: Optional[int] = Field(default=None, ge=1, description="Page number (1-based). Default 1 when omitted.")


# ---------------------------------------------------------------------------
# Toolset registration
# ---------------------------------------------------------------------------

@ToolsetBuilder("GitHub")\
    .in_group("Development")\
    .with_description("GitHub integration for repository management, issues, and pull requests")\
    .with_category(ToolsetCategory.APP)\
    .with_auth([
        AuthBuilder.type(AuthType.OAUTH).oauth(
            connector_name="GitHub",
            authorize_url="https://github.com/login/oauth/authorize",
            token_url="https://github.com/login/oauth/access_token",
            redirect_uri="toolsets/oauth/callback/github",
            scopes=OAuthScopeConfig(
                personal_sync=[],
                team_sync=[],
                agent=[
                    "repo",
                    "read:org",
                    "read:user",
                    "user:email",
                    "public_repo",
                ]
            ),
            additional_params={
                "prompt": "consent",
            },
            fields=[
                CommonFields.client_id("GitHub Developer Settings"),
                CommonFields.client_secret("GitHub Developer Settings"),
            ],
            icon_path=IconPaths.connector_icon("github"),
            app_group="Development",
            app_description="GitHub OAuth application for agent integration",
        ),
    ])\
    .configure(lambda builder: builder.with_icon(IconPaths.connector_icon("github"))
        .add_documentation_link(DocumentationLink(
            "GitHub API Setup",
            "https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/creating-an-oauth-app",
            "setup",
        ))
        .add_documentation_link(DocumentationLink(
            "Pipeshub Documentation",
            "https://docs.pipeshub.com/toolsets/github/github",
            "pipeshub",
        )))\
    .build_decorator()
class GitHub:
    """GitHub tools exposed to agents using GitHubDataSource."""

    def __init__(self, client: GitHubClient) -> None:
        self.client = GitHubDataSource(client)

    # Keys to keep when sending repo list to LLM (avoids huge payloads)
    _REPO_LIST_KEYS = frozenset({
        "id", "name", "full_name", "description", "html_url", "clone_url", "ssh_url",
        "private", "visibility", "archived", "disabled",
        "default_branch", "created_at", "updated_at", "pushed_at",
        "stargazers_count", "forks_count", "open_issues_count", "watchers_count",
        "language", "homepage", "topics", "size",
    })

    def _handle_response(
        self, response: GitHubResponse, success_message: str
    ) -> Tuple[bool, str]:
        """Return a standardised (success, json_string) tuple."""
        if response.success:
            data = response.data
            # Prefer cached _rawData to avoid refetching per item; fallback to raw_data
            if hasattr(data, "raw_data") and not isinstance(data, list):
                raw = getattr(data, "_rawData", None) or getattr(data, "raw_data", None)
                serialisable = raw if raw is not None else data.raw_data
            elif isinstance(data, list):
                out = []
                for item in data:
                    raw = getattr(item, "_rawData", None) or getattr(item, "raw_data", None)
                    if raw is not None and isinstance(raw, dict):
                        # Only trim to repo keys when item is repo-like (has full_name); keep full payload for issues/PRs
                        if "full_name" in raw:
                            out.append({k: raw[k] for k in self._REPO_LIST_KEYS if k in raw})
                        else:
                            out.append(raw)
                    elif hasattr(item, "raw_data"):
                        r = item.raw_data
                        if isinstance(r, dict):
                            if "full_name" in r:
                                out.append({k: r[k] for k in self._REPO_LIST_KEYS if k in r})
                            else:
                                out.append(r)
                        else:
                            out.append(r)
                    else:
                        out.append(str(item))
                serialisable = out
            else:
                # Single object: try raw_data / _rawData; fallback to repr if not JSON-serializable
                raw = getattr(data, "_rawData", None) or getattr(data, "raw_data", None)
                if raw is not None and isinstance(raw, dict):
                    serialisable = raw
                else:
                    serialisable = data
            try:
                return True, json.dumps({"message": success_message, "data": serialisable})
            except (TypeError, ValueError):
                # Fallback if data or any item is not JSON-serializable
                return True, json.dumps({
                    "message": success_message,
                    "data": str(serialisable),
                    "_serialization_fallback": True,
                })
        return False, json.dumps({"error": response.error or "Unknown error"})

    # ------------------------------------------------------------------
    # Repository tools
    # ------------------------------------------------------------------

    @tool(
        path="/tools/github/create_repository",
        short_description="Create a new repository on GitHub",
        description=(
            "Creates a repo under the authenticated user. Only name is required (from user query). "
            "Do NOT call get_owner first. For private, description, auto_init: use defaults "
            "(private=True, description=omit, auto_init=True) if the user did not specify; never ask "
            "the user for these. Use when the user wants to create or set up a new GitHub repository. "
            "Do not use when the user wants to list or search repositories (use list_repositories or "
            "search_repositories instead)."
        ),
        parameters=[
            ToolParameter(name="name", type=ParameterType.STRING, description="Repository name from the user query. Only required field.", required=True),
            ToolParameter(name="private", type=ParameterType.BOOLEAN, description="Whether the repository should be private. Default True. Do not ask the user if not specified.", required=False),
            ToolParameter(name="description", type=ParameterType.STRING, description="Short description of the repository. Optional. Omit if user did not provide; do not ask.", required=False),
            ToolParameter(name="auto_init", type=ParameterType.BOOLEAN, description="Initialize with a README. Default True. Do not ask the user if not specified.", required=False),
        ],
        tags=[Tag(key="category", value="development"), Tag(key="type", value="write")],
        args_summary=args_template('Creating GitHub repository "{name}"', "name"),
        result_summary=entity_summary(lambda e: f"Created repository: {_github_repo_label(e)}"),
    )
    async def create_repository(
        self,
        name: str,
        private: bool = True,
        description: Optional[str] = None,
        auto_init: bool = True,
    ) -> Tuple[bool, str]:
        """Create a new repository on GitHub."""
        try:
            logger.info("github.create_repository called with args: %s", {"name": name, "private": private, "description": description, "auto_init": auto_init})
            response = self.client.create_repo(
                name=name,
                private=private,
                description=description,
                auto_init=auto_init,
            )
            return self._handle_response(response, "Repository created successfully")
        except Exception as e:
            logger.error(f"Error creating repository: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        path="/tools/github/get_repository",
        short_description="Get details of a specific GitHub repository",
        description=(
            "Returns repo info (description, stars, forks, default_branch, etc.). For the current "
            "user's repo, get login from get_owner(owner='me') and use it as owner. Use when the user "
            "asks for information about a specific GitHub repository or wants to inspect a repo's "
            "details. Do not use for owner/user/org profile (use get_owner), listing multiple repos "
            "(use list_repositories), or searching repos by keyword (use search_repositories)."
        ),
        parameters=[
            ToolParameter(name="owner", type=ParameterType.STRING, description="Repository owner (username or org). For 'my' repo call get_owner(owner='me') first and use the returned 'login' here.", required=True),
            ToolParameter(name="repo", type=ParameterType.STRING, description="Repository name", required=True),
        ],
        tags=[Tag(key="category", value="development"), Tag(key="type", value="read")],
        args_summary=args_template("Fetching GitHub repository {owner}/{repo}", "owner", "repo"),
        result_summary=entity_summary(lambda e: f"Fetched repository: {_github_repo_label(e)}"),
    )
    async def get_repository(self, owner: str, repo: str) -> Tuple[bool, str]:
        """Get details of a specific GitHub repository."""
        try:
            logger.info("github.get_repository called with args: %s", {"owner": owner, "repo": repo})
            response = self.client.get_repo(owner=owner, repo=repo)
            return self._handle_response(response, "Repository fetched successfully")
        except Exception as e:
            logger.error(f"Error getting repository: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        path="/tools/github/get_owner",
        short_description="Get details of a GitHub user or organization",
        description=(
            "Returns profile info (login, name, avatar_url, bio, public_repos). Use owner='me' to "
            "get the authenticated user's profile; use the returned 'login' as owner/user in other "
            "tools (list_repositories, get_repository, create_issue, etc.). owner_type: 'user' or "
            "'organization'. Use when the user wants details or profile of a GitHub user or "
            "organization. Do not use for listing repos (use list_repositories) or getting a specific "
            "repository's details (use get_repository)."
        ),
        parameters=[
            ToolParameter(name="owner", type=ParameterType.STRING, description="GitHub username or organization login. Use 'me' only here to get the authenticated user's profile (returns login for use in other tools).", required=True),
            ToolParameter(name="owner_type", type=ParameterType.STRING, description="Type of owner: 'user' for a user account, 'organization' for an org", required=False),
        ],
        tags=[Tag(key="category", value="development"), Tag(key="type", value="read")],
        args_summary=args_template("Fetching GitHub profile for {owner}", "owner"),
        result_summary=entity_summary(lambda e: f"Fetched profile: {_github_owner_label(e)}"),
    )
    async def get_owner(
        self,
        owner: str,
        owner_type: str = "user",
    ) -> Tuple[bool, str]:
        """Get details of a GitHub user or organization."""
        try:
            kind = owner_type.strip().lower() if owner_type else "user"
            if kind not in ("user", "organization"):
                kind = "user"
            logger.info("github.get_owner called with args: %s", {"owner": owner, "owner_type": kind})
            response = self.client.get_owner(login=owner, kind=kind)
            return self._handle_response(response, "Owner details fetched successfully")
        except Exception as e:
            logger.error(f"Error getting owner: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        path="/tools/github/list_repositories",
        short_description="List repositories for a GitHub user",
        description=(
            "Lists repos for a user. For the authenticated user's repos, call get_owner(owner='me') "
            "first and use the returned login as user. Optional: type (owner/all/member), per_page "
            "(max 50), page. Use when the user wants to see all repositories for a GitHub user or "
            "list repos for a given username. Do not use for owner/user/org profile (use get_owner), "
            "details of a single repo (use get_repository), or searching repos by keyword (use "
            "search_repositories)."
        ),
        parameters=[
            ToolParameter(name="user", type=ParameterType.STRING, description="GitHub username whose repositories to list. For the authenticated user, call get_owner(owner='me') first and use the returned 'login' here.", required=True),
            ToolParameter(name="type", type=ParameterType.STRING, description="Filter: 'all', 'owner', or 'member'", required=False),
            ToolParameter(name="per_page", type=ParameterType.INTEGER, description="Number of repos per page. Default 10 when omitted; max 50.", required=False),
            ToolParameter(name="page", type=ParameterType.INTEGER, description="Page number (1-based). Default 1 when omitted.", required=False),
        ],
        tags=[Tag(key="category", value="development"), Tag(key="type", value="read")],
        args_summary=args_template("Listing GitHub repositories for {user}", "user"),
        result_summary=list_summary(("data",), _github_repo_label, "repository"),
    )
    async def list_repositories(
        self,
        user: str,
        type: str = "owner",
        per_page: Optional[int] = None,
        page: Optional[int] = None,
    ) -> Tuple[bool, str]:
        """List repositories for a GitHub user. Default 10 per page, max 50."""
        try:
            per_page = per_page if per_page is not None else 10
            per_page = min(50, max(1, per_page))
            page = page if page is not None else 1
            page = max(1, page)
            logger.info("github.list_repositories called with args: %s", {"user": user, "type": type, "per_page": per_page, "page": page})
            response = self.client.list_user_repos(
                user=user, type=type, per_page=per_page, page=page
            )
            return self._handle_response(response, "Repositories fetched successfully")
        except Exception as e:
            logger.error(f"Error listing repositories: {e}")
            return False, json.dumps({"error": str(e)})

    # ------------------------------------------------------------------
    # Issue tools
    # ------------------------------------------------------------------

    @tool(
        path="/tools/github/create_issue",
        short_description="Create a new issue in a GitHub repository",
        description=(
            "Creates an issue. Need owner, repo, title. Optional: body, assignees (list of GitHub "
            "usernames), labels. For current user's repo use get_owner(owner='me') to get owner. "
            "Use when the user wants to open a GitHub issue or report a bug/feature request. "
            "Do not use for owner/user/org profile (use get_owner) or looking up an existing issue "
            "(use get_issue)."
        ),
        parameters=[
            ToolParameter(name="owner", type=ParameterType.STRING, description="Repository owner (username or org). For 'my' repo call get_owner(owner='me') first and use the returned 'login' here.", required=True),
            ToolParameter(name="repo", type=ParameterType.STRING, description="Repository name", required=True),
            ToolParameter(name="title", type=ParameterType.STRING, description="Issue title", required=True),
            ToolParameter(name="body", type=ParameterType.STRING, description="Issue body/description", required=False),
            ToolParameter(name="assignees", type=ParameterType.ARRAY, description="GitHub usernames to assign", required=False),
            ToolParameter(name="labels", type=ParameterType.ARRAY, description="Label names to apply", required=False),
        ],
        tags=[Tag(key="category", value="development"), Tag(key="type", value="write")],
        args_summary=args_template("Creating GitHub issue in {owner}/{repo}", "owner", "repo"),
        result_summary=entity_summary(lambda e: f"Created issue: {_github_issue_label(e)}"),
    )
    async def create_issue(
        self,
        owner: str,
        repo: str,
        title: str,
        body: Optional[str] = None,
        assignees: Optional[List[str]] = None,
        labels: Optional[List[str]] = None,
    ) -> Tuple[bool, str]:
        """Create a new issue in a GitHub repository."""
        try:
            logger.info("github.create_issue called with args: %s", {"owner": owner, "repo": repo, "title": title, "body": body, "assignees": assignees, "labels": labels})
            response = self.client.create_issue(
                owner=owner,
                repo=repo,
                title=title,
                body=body,
                assignees=assignees,
                labels=labels,
            )
            return self._handle_response(response, "Issue created successfully")
        except Exception as e:
            logger.error(f"Error creating issue: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        path="/tools/github/get_issue",
        short_description="Get details of a specific GitHub issue by number",
        description=(
            "Returns a single issue by owner, repo, and issue number. Use for 'show issue #N', "
            "'status of issue X', or when you need full issue details (title, body, state, assignees, "
            "labels). Do not use for owner/user/org profile (use get_owner), creating an issue (use "
            "create_issue), or closing an issue (use close_issue)."
        ),
        parameters=[
            ToolParameter(name="owner", type=ParameterType.STRING, description="Repository owner (username or org). For 'my' repo call get_owner(owner='me') first and use the returned 'login' here.", required=True),
            ToolParameter(name="repo", type=ParameterType.STRING, description="Repository name", required=True),
            ToolParameter(name="number", type=ParameterType.INTEGER, description="Issue number", required=True),
        ],
        tags=[Tag(key="category", value="development"), Tag(key="type", value="read")],
        args_summary=args_template("Fetching GitHub issue {owner}/{repo}#{number}", "owner", "repo", "number"),
        result_summary=entity_summary(lambda e: f"Fetched issue: {_github_issue_label(e)}"),
    )
    async def get_issue(self, owner: str, repo: str, number: int) -> Tuple[bool, str]:
        """Get details of a specific issue from a GitHub repository."""
        try:
            logger.info("github.get_issue called with args: %s", {"owner": owner, "repo": repo, "number": number})
            response = self.client.get_issue(owner=owner, repo=repo, number=number)
            return self._handle_response(response, "Issue fetched successfully")
        except Exception as e:
            logger.error(f"Error getting issue: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        path="/tools/github/list_issues",
        short_description="List issues in a GitHub repository",
        description=(
            "Lists issues in a repo. Always returns one page (default 10 per page, max 50). "
            "Params: owner, repo; optional state ('open'/'closed'/'all'), labels, assignee, per_page "
            "(default 10), page (default 1). Use when the user wants to list or see all issues in a "
            "repo, or filter by state/label/assignee. Do not use for owner/user/org profile (use "
            "get_owner), a single issue by number (use get_issue), or creating an issue (use create_issue)."
        ),
        parameters=[
            ToolParameter(name="owner", type=ParameterType.STRING, description="Repository owner (username or org). For 'my' repo call get_owner(owner='me') first and use the returned 'login' here.", required=True),
            ToolParameter(name="repo", type=ParameterType.STRING, description="Repository name", required=True),
            ToolParameter(name="state", type=ParameterType.STRING, description="Filter: 'open', 'closed', or 'all'", required=False),
            ToolParameter(name="labels", type=ParameterType.ARRAY, description="Filter by label names", required=False),
            ToolParameter(name="assignee", type=ParameterType.STRING, description="Filter by assignee username", required=False),
            ToolParameter(name="per_page", type=ParameterType.INTEGER, description="Issues per page (default 10, max 50).", required=False),
            ToolParameter(name="page", type=ParameterType.INTEGER, description="Page number (1-based).", required=False),
        ],
        tags=[Tag(key="category", value="development"), Tag(key="type", value="read")],
        args_summary=args_template("Listing issues in {owner}/{repo}", "owner", "repo"),
        result_summary=list_summary(("data",), _github_issue_label, "issue"),
    )
    async def list_issues(
        self,
        owner: str,
        repo: str,
        state: str = "open",
        labels: Optional[List[str]] = None,
        assignee: Optional[str] = None,
        per_page: int = 10,
        page: int = 1,
    ) -> Tuple[bool, str]:
        """List issues in a GitHub repository. Always returns one page (default 10 per page, max 50)."""
        try:
            # Normalize empty optional params so GitHub API does not receive them (422 if assignee="" or labels=[])
            _labels = labels if labels else None
            _assignee = assignee.strip() if (isinstance(assignee, str) and assignee.strip()) else None
            _per_page = min(50, max(1, per_page))
            _page = max(1, page)
            logger.info("github.list_issues called with args: %s", {"owner": owner, "repo": repo, "state": state, "labels": _labels, "assignee": _assignee, "per_page": _per_page, "page": _page})
            response = self.client.list_issues_only(
                owner=owner,
                repo=repo,
                state=state,
                labels=_labels,
                assignee=_assignee,
                per_page=_per_page,
                page=_page,
            )
            return self._handle_response(response, "Issues fetched successfully")
        except Exception as e:
            logger.error(f"Error listing issues: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        path="/tools/github/close_issue",
        short_description="Close an issue in a GitHub repository",
        description=(
            "Marks an issue as closed. Requires owner, repo, and issue number. Use when user wants "
            "to close or resolve an issue. For reopening, use update_issue with state='open' instead. "
            "Do not use for viewing an issue (use get_issue) or creating an issue (use create_issue)."
        ),
        parameters=[
            ToolParameter(name="owner", type=ParameterType.STRING, description="Repository owner (username or org). For 'my' repo call get_owner(owner='me') first and use the returned 'login' here.", required=True),
            ToolParameter(name="repo", type=ParameterType.STRING, description="Repository name", required=True),
            ToolParameter(name="number", type=ParameterType.INTEGER, description="Issue number to close", required=True),
        ],
        tags=[Tag(key="category", value="development"), Tag(key="type", value="write")],
        args_summary=args_template("Closing GitHub issue {owner}/{repo}#{number}", "owner", "repo", "number"),
        result_summary=entity_summary(lambda e: f"Closed issue: {_github_issue_label(e)}"),
    )
    async def close_issue(self, owner: str, repo: str, number: int) -> Tuple[bool, str]:
        """Close an issue in a GitHub repository."""
        try:
            logger.info("github.close_issue called with args: %s", {"owner": owner, "repo": repo, "number": number})
            response = self.client.close_issue(owner=owner, repo=repo, number=number)
            return self._handle_response(response, "Issue closed successfully")
        except Exception as e:
            logger.error(f"Error closing issue: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        path="/tools/github/update_issue",
        short_description="Update a GitHub issue's fields",
        description=(
            "Updates an issue. Pass only the fields to change; omit others. Can set state to 'open' "
            "or 'closed' (reopen/close). assignees and labels replace existing values. Use when the "
            "user wants to edit or update a GitHub issue (title, body, state, assignees, or labels). "
            "Do not use for viewing an issue (use get_issue), creating an issue (use create_issue), "
            "or simply closing an issue (use close_issue)."
        ),
        parameters=[
            ToolParameter(name="owner", type=ParameterType.STRING, description="Repository owner (username or org). For 'my' repo call get_owner(owner='me') first and use the returned 'login' here.", required=True),
            ToolParameter(name="repo", type=ParameterType.STRING, description="Repository name", required=True),
            ToolParameter(name="number", type=ParameterType.INTEGER, description="Issue number to update", required=True),
            ToolParameter(name="title", type=ParameterType.STRING, description="New title (omit to leave unchanged)", required=False),
            ToolParameter(name="body", type=ParameterType.STRING, description="New body/description (omit to leave unchanged)", required=False),
            ToolParameter(name="state", type=ParameterType.STRING, description="'open' or 'closed' (omit to leave unchanged)", required=False),
            ToolParameter(name="assignees", type=ParameterType.ARRAY, description="Replace assignees with these usernames (omit to leave unchanged)", required=False),
            ToolParameter(name="labels", type=ParameterType.ARRAY, description="Replace labels with these names (omit to leave unchanged)", required=False),
        ],
        tags=[Tag(key="category", value="development"), Tag(key="type", value="write")],
        args_summary=args_template("Updating GitHub issue {owner}/{repo}#{number}", "owner", "repo", "number"),
        result_summary=entity_summary(lambda e: f"Updated issue: {_github_issue_label(e)}"),
    )
    async def update_issue(
        self,
        owner: str,
        repo: str,
        number: int,
        title: Optional[str] = None,
        body: Optional[str] = None,
        state: Optional[str] = None,
        assignees: Optional[List[str]] = None,
        labels: Optional[List[str]] = None,
    ) -> Tuple[bool, str]:
        """Update an existing issue. Only provided fields are changed."""
        try:
            logger.info("github.update_issue called with args: %s", {"owner": owner, "repo": repo, "number": number, "title": title, "body": body, "state": state, "assignees": assignees, "labels": labels})
            response = self.client.update_issue(
                owner=owner,
                repo=repo,
                number=number,
                title=title,
                body=body,
                state=state,
                assignees=assignees,
                labels=labels,
            )
            return self._handle_response(response, "Issue updated successfully")
        except Exception as e:
            logger.error(f"Error updating issue: {e}")
            return False, json.dumps({"error": str(e)})

    # ------------------------------------------------------------------
    # Issue comment tools
    # ------------------------------------------------------------------

    @tool(
        path="/tools/github/list_issue_comments",
        short_description="List all comments on a GitHub issue",
        description=(
            "Returns all comments on an issue (owner, repo, issue number). Use for 'show comments on "
            "issue #N', 'discussion on this issue'. Comment ids from here can be used with "
            "get_issue_comment. Do not use for adding a comment (use create_issue_comment) or "
            "getting a single comment by ID (use get_issue_comment)."
        ),
        parameters=[
            ToolParameter(name="owner", type=ParameterType.STRING, description="Repository owner (username or org). For 'my' repo call get_owner(owner='me') first and use the returned 'login' here.", required=True),
            ToolParameter(name="repo", type=ParameterType.STRING, description="Repository name", required=True),
            ToolParameter(name="number", type=ParameterType.INTEGER, description="Issue number", required=True),
        ],
        tags=[Tag(key="category", value="development"), Tag(key="type", value="read")],
        args_summary=args_template("Listing comments on {owner}/{repo}#{number}", "owner", "repo", "number"),
        result_summary=list_summary(("data",), _github_comment_label, "comment"),
    )
    async def list_issue_comments(self, owner: str, repo: str, number: int) -> Tuple[bool, str]:
        """List all comments on an issue."""
        try:
            logger.info("github.list_issue_comments called with args: %s", {"owner": owner, "repo": repo, "number": number})
            response = self.client.list_issue_comments(owner=owner, repo=repo, number=number)
            return self._handle_response(response, "Issue comments listed successfully")
        except Exception as e:
            logger.error(f"Error listing issue comments: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        path="/tools/github/get_issue_comment",
        short_description="Get a single issue comment by ID",
        description=(
            "Returns one comment by comment_id. Get comment_id from list_issue_comments. "
            "Use when the user wants one specific comment by ID. Do not use when the user wants all "
            "comments (use list_issue_comments instead)."
        ),
        parameters=[
            ToolParameter(name="owner", type=ParameterType.STRING, description="Repository owner (username or org). For 'my' repo call get_owner(owner='me') first and use the returned 'login' here.", required=True),
            ToolParameter(name="repo", type=ParameterType.STRING, description="Repository name", required=True),
            ToolParameter(name="number", type=ParameterType.INTEGER, description="Issue number", required=True),
            ToolParameter(name="comment_id", type=ParameterType.INTEGER, description="Comment ID (from list_issue_comments)", required=True),
        ],
        tags=[Tag(key="category", value="development"), Tag(key="type", value="read")],
        args_summary=args_template(
            "Fetching comment {comment_id} on {owner}/{repo}#{number}", "owner", "repo", "number", "comment_id"
        ),
        result_summary=entity_summary(lambda e: f"Fetched comment: {_github_comment_label(e)}"),
    )
    async def get_issue_comment(self, owner: str, repo: str, number: int, comment_id: int) -> Tuple[bool, str]:
        """Get a single issue comment by ID."""
        try:
            logger.info("github.get_issue_comment called with args: %s", {"owner": owner, "repo": repo, "number": number, "comment_id": comment_id})
            response = self.client.get_issue_comment(owner=owner, repo=repo, number=number, comment_id=comment_id)
            return self._handle_response(response, "Issue comment fetched successfully")
        except Exception as e:
            logger.error(f"Error getting issue comment: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        path="/tools/github/create_issue_comment",
        short_description="Add a comment to a GitHub issue",
        description=(
            "Posts a comment on an issue. Markdown supported. Use for 'comment on issue #N', 'reply "
            "to this issue'. For PRs use the PR's issue number. Do not use for listing or reading "
            "comments (use list_issue_comments or get_issue_comment) or adding a line-level review "
            "comment on a PR (use create_pull_request_review_comment)."
        ),
        parameters=[
            ToolParameter(name="owner", type=ParameterType.STRING, description="Repository owner (username or org). For 'my' repo call get_owner(owner='me') first and use the returned 'login' here.", required=True),
            ToolParameter(name="repo", type=ParameterType.STRING, description="Repository name", required=True),
            ToolParameter(name="number", type=ParameterType.INTEGER, description="Issue number", required=True),
            ToolParameter(name="body", type=ParameterType.STRING, description="Comment text (Markdown supported)", required=True),
        ],
        tags=[Tag(key="category", value="development"), Tag(key="type", value="write")],
        args_summary=args_template("Commenting on {owner}/{repo}#{number}", "owner", "repo", "number"),
        result_summary=entity_summary(lambda e: f"Comment added: {_github_comment_label(e)}"),
    )
    async def create_issue_comment(self, owner: str, repo: str, number: int, body: str) -> Tuple[bool, str]:
        """Add a comment to an issue."""
        try:
            logger.info("github.create_issue_comment called with args: %s", {"owner": owner, "repo": repo, "number": number, "body": body[:100] + "..." if len(body) > 100 else body})
            response = self.client.create_issue_comment(owner=owner, repo=repo, number=number, body=body)
            return self._handle_response(response, "Issue comment created successfully")
        except Exception as e:
            logger.error(f"Error creating issue comment: {e}")
            return False, json.dumps({"error": str(e)})

    # ------------------------------------------------------------------
    # Pull request tools
    # ------------------------------------------------------------------

    @tool(
        path="/tools/github/create_pull_request",
        short_description="Create a new pull request on GitHub",
        description=(
            "Creates a PR: owner, repo, title, head (source branch), base (target branch). Optional: "
            "body, draft (default False). Use when the user wants to open a pull request or create a "
            "PR to merge one branch into another. Do not use for viewing a PR (use get_pull_request) "
            "or merging a PR (use merge_pull_request)."
        ),
        parameters=[
            ToolParameter(name="owner", type=ParameterType.STRING, description="Repository owner (username or org). For 'my' repo call get_owner(owner='me') first and use the returned 'login' here.", required=True),
            ToolParameter(name="repo", type=ParameterType.STRING, description="Repository name", required=True),
            ToolParameter(name="title", type=ParameterType.STRING, description="Pull request title", required=True),
            ToolParameter(name="head", type=ParameterType.STRING, description="Source branch to merge from", required=True),
            ToolParameter(name="base", type=ParameterType.STRING, description="Target branch to merge into", required=True),
            ToolParameter(name="body", type=ParameterType.STRING, description="Pull request description", required=False),
            ToolParameter(name="draft", type=ParameterType.BOOLEAN, description="Whether to create as a draft PR", required=False),
        ],
        tags=[Tag(key="category", value="development"), Tag(key="type", value="write")],
        args_summary=args_template("Creating pull request in {owner}/{repo}", "owner", "repo"),
        result_summary=entity_summary(lambda e: f"Created PR: {_github_pr_label(e)}"),
    )
    async def create_pull_request(
        self,
        owner: str,
        repo: str,
        title: str,
        head: str,
        base: str,
        body: Optional[str] = None,
        draft: bool = False,
    ) -> Tuple[bool, str]:
        """Create a new pull request in a GitHub repository."""
        try:
            logger.info("github.create_pull_request called with args: %s", {"owner": owner, "repo": repo, "title": title, "head": head, "base": base, "body": body, "draft": draft})
            response = self.client.create_pull(
                owner=owner,
                repo=repo,
                title=title,
                head=head,
                base=base,
                body=body,
                draft=draft,
            )
            return self._handle_response(response, "Pull request created successfully")
        except Exception as e:
            logger.error(f"Error creating pull request: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        path="/tools/github/get_pull_request",
        short_description="Get details of a specific pull request with comments",
        description=(
            "Returns a single PR by owner, repo, and PR number, plus conversation comments "
            "(issue-level discussion). Use for 'show PR #N', 'status of this PR', 'PR with comments'. "
            "Response has data.pr (title, state, head/base branches, reviewers, etc.) and "
            "data.conversation_comments (list of discussion comments). Do not use for listing PRs "
            "(use list_pull_requests), file changes (use get_pull_request_file_changes), creating a "
            "PR (use create_pull_request), or merging a PR (use merge_pull_request)."
        ),
        parameters=[
            ToolParameter(name="owner", type=ParameterType.STRING, description="Repository owner (username or org). For 'my' repo call get_owner(owner='me') first and use the returned 'login' here.", required=True),
            ToolParameter(name="repo", type=ParameterType.STRING, description="Repository name", required=True),
            ToolParameter(name="number", type=ParameterType.INTEGER, description="Pull request number", required=True),
        ],
        tags=[Tag(key="category", value="development"), Tag(key="type", value="read")],
        args_summary=args_template("Fetching pull request {owner}/{repo}#{number}", "owner", "repo", "number"),
        result_summary=entity_summary(lambda e: f"Fetched PR: {_github_pr_label(e)}", path=("data", "pr")),
    )
    async def get_pull_request(self, owner: str, repo: str, number: int) -> Tuple[bool, str]:
        """Get details of a specific pull request and its conversation comments (issue comments)."""
        try:
            logger.info("github.get_pull_request called with args: %s", {"owner": owner, "repo": repo, "number": number})
            pr_response = self.client.get_pull(owner=owner, repo=repo, number=number)
            success_pr, json_str_pr = self._handle_response(pr_response, "Pull request fetched successfully")
            if not success_pr:
                return False, json_str_pr
            comments_response = self.client.list_issue_comments(owner=owner, repo=repo, number=number)
            success_comments, json_str_comments = self._handle_response(
                comments_response, "Issue comments listed successfully"
            )
            pr_payload = json.loads(json_str_pr)
            pr_data = pr_payload["data"]
            if success_comments:
                comments_payload = json.loads(json_str_comments)
                conversation_comments = comments_payload["data"]
            else:
                conversation_comments = []
            combined = {
                "message": "Pull request and conversation fetched successfully",
                "data": {"pr": pr_data, "conversation_comments": conversation_comments},
            }
            return True, json.dumps(combined)
        except Exception as e:
            logger.error(f"Error getting pull request: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        path="/tools/github/get_pull_request_commits",
        short_description="Get commits in a pull request",
        description=(
            "Returns commits in the PR. Response includes last_commit_sha — use that as commit_id "
            "when calling create_pull_request_review_comment (or use placeholder "
            "{{github.get_pull_request_commits.last_commit_sha}}). Call this before adding a "
            "line-level review comment. Do not use for PR details only (use get_pull_request) or "
            "merging/listing PRs (use merge_pull_request or list_pull_requests)."
        ),
        parameters=[
            ToolParameter(name="owner", type=ParameterType.STRING, description="Repository owner (username or org). For 'my' repo call get_owner(owner='me') first and use the returned 'login' here.", required=True),
            ToolParameter(name="repo", type=ParameterType.STRING, description="Repository name", required=True),
            ToolParameter(name="number", type=ParameterType.INTEGER, description="Pull request number", required=True),
        ],
        tags=[Tag(key="category", value="development"), Tag(key="type", value="read")],
        args_summary=args_template("Fetching commits for {owner}/{repo}#{number}", "owner", "repo", "number"),
        result_summary=list_summary(("data",), _github_commit_label, "commit"),
    )
    async def get_pull_request_commits(self, owner: str, repo: str, number: int) -> Tuple[bool, str]:
        """Get commits of a pull request. Use the last commit's sha as commit_id for create_pull_request_review_comment."""
        try:
            logger.info("github.get_pull_request_commits called with args: %s", {"owner": owner, "repo": repo, "number": number})
            response = self.client.get_pull_commits(owner=owner, repo=repo, number=number)
            success, json_str = self._handle_response(response, "Pull request commits fetched successfully")
            if not success:
                return success, json_str
            payload = json.loads(json_str)
            data = payload.get("data") or []
            if isinstance(data, list):
                payload["length"] = len(data)
                payload["last_commit_sha"] = (
                    data[-1].get("sha") if data and isinstance(data[-1], dict) else None
                )
            else:
                payload["length"] = 0
                payload["last_commit_sha"] = None
            return True, json.dumps(payload)
        except Exception as e:
            logger.error(f"Error getting pull request commits: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        path="/tools/github/get_pull_request_file_changes",
        short_description="Get files changed in a pull request with diffs",
        description=(
            "Returns list of changed files in a PR with diffs. By default fetches FULL CONTENT "
            "for large files with truncated patches, generating complete diffs locally. "
            "Use for 'review this PR', 'see what changed', 'what files in this PR', 'diff for PR #N'. "
            "Set fetch_full_content=False for quick overview without expanding truncated files. "
            "Do not use for PR metadata only (use get_pull_request), commits list (use "
            "get_pull_request_commits), or review comments (use list_pull_request_comments)."
        ),
        parameters=[
            ToolParameter(name="owner", type=ParameterType.STRING, description="Repository owner (username or org). For 'my' repo call get_owner(owner='me') first and use the returned 'login' here.", required=True),
            ToolParameter(name="repo", type=ParameterType.STRING, description="Repository name", required=True),
            ToolParameter(name="number", type=ParameterType.INTEGER, description="Pull request number", required=True),
            ToolParameter(name="fetch_full_content", type=ParameterType.BOOLEAN, description="Fetch full content for large files with truncated patches. Default True.", required=False),
            ToolParameter(name="max_changes_per_file", type=ParameterType.INTEGER, description="Skip files with more than this many changes to prevent context overflow", required=False),
            ToolParameter(name="max_diff_lines", type=ParameterType.INTEGER, description="Truncate diffs longer than this to prevent context overflow", required=False),
            ToolParameter(name="context_lines", type=ParameterType.INTEGER, description="Number of context lines around changes (1=minimal, 3=standard, 10=verbose)", required=False),
        ],
        tags=[Tag(key="category", value="development"), Tag(key="type", value="read")],
        args_summary=args_template("Fetching file changes for {owner}/{repo}#{number}", "owner", "repo", "number"),
        result_summary=list_summary(("data",), _github_file_change_label, "file"),
    )
    async def get_pull_request_file_changes(
        self,
        owner: str,
        repo: str,
        number: int,
        fetch_full_content: bool = True,
        max_changes_per_file: int = 10000,
        max_diff_lines: int = 10000,
        context_lines: int = 2,
    ) -> Tuple[bool, str]:
        """Get PR file changes with complete diffs and safety limits."""
        try:
            response = self.client.get_pull_file_changes(
                owner=owner,
                repo=repo,
                number=number,
                fetch_full_content=fetch_full_content,
                max_changes_per_file=max_changes_per_file,
                max_diff_lines=max_diff_lines,
                context_lines=context_lines,
            )
            
            return self._handle_response(
                response,
                "Pull request file changes fetched successfully"
            )
            
        except Exception as e:
            logger.error(f"Error getting pull request file changes: {e}")
            return False, json.dumps({"error": str(e)})
    @tool(
        path="/tools/github/list_pull_requests",
        short_description="List pull requests in a GitHub repository",
        description=(
            "Lists PRs in a repo. Always returns one page (default 10 per page, max 50). "
            "Params: owner, repo; optional state ('open'/'closed'/'all'), head, base, per_page "
            "(default 10), page (default 1). Use when the user wants to list or see all pull "
            "requests in a repo or filter by state/branch. Do not use for a single PR by number "
            "(use get_pull_request) or creating a PR (use create_pull_request)."
        ),
        parameters=[
            ToolParameter(name="owner", type=ParameterType.STRING, description="Repository owner (username or org). For 'my' repo call get_owner(owner='me') first and use the returned 'login' here.", required=True),
            ToolParameter(name="repo", type=ParameterType.STRING, description="Repository name", required=True),
            ToolParameter(name="state", type=ParameterType.STRING, description="Filter: 'open', 'closed', or 'all'", required=False),
            ToolParameter(name="head", type=ParameterType.STRING, description="Filter by head branch name", required=False),
            ToolParameter(name="base", type=ParameterType.STRING, description="Filter by base branch name", required=False),
            ToolParameter(name="per_page", type=ParameterType.INTEGER, description="PRs per page (default 10, max 50).", required=False),
            ToolParameter(name="page", type=ParameterType.INTEGER, description="Page number (1-based).", required=False),
        ],
        tags=[Tag(key="category", value="development"), Tag(key="type", value="read")],
        args_summary=args_template("Listing pull requests in {owner}/{repo}", "owner", "repo"),
        result_summary=list_summary(("data",), _github_pr_label, "pull request"),
    )
    async def list_pull_requests(
        self,
        owner: str,
        repo: str,
        state: str = "open",
        head: Optional[str] = None,
        base: Optional[str] = None,
        per_page: int = 10,
        page: int = 1,
    ) -> Tuple[bool, str]:
        """List pull requests in a GitHub repository. Always returns one page (default 10 per page, max 50)."""
        try:
            # Normalize empty optional params so they are not sent to the API
            _head = head.strip() if (isinstance(head, str) and head.strip()) else None
            _base = base.strip() if (isinstance(base, str) and base.strip()) else None
            _per_page = min(50, max(1, per_page))
            _page = max(1, page)
            logger.info("github.list_pull_requests called with args: %s", {"owner": owner, "repo": repo, "state": state, "head": _head, "base": _base, "per_page": _per_page, "page": _page})
            response = self.client.list_pulls(
                owner=owner,
                repo=repo,
                state=state,
                head=_head,
                base=_base,
                per_page=_per_page,
                page=_page,
            )
            return self._handle_response(response, "Pull requests fetched successfully")
        except Exception as e:
            logger.error(f"Error listing pull requests: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        path="/tools/github/merge_pull_request",
        short_description="Merge a GitHub pull request",
        description=(
            "Merges a PR. Optional: commit_message, merge_method ('merge', 'squash', or 'rebase'; "
            "default 'merge'). Use when the user wants to merge or accept a GitHub pull request. "
            "Do not use for viewing a PR (use get_pull_request) or creating a PR (use "
            "create_pull_request)."
        ),
        parameters=[
            ToolParameter(name="owner", type=ParameterType.STRING, description="Repository owner (username or org). For 'my' repo call get_owner(owner='me') first and use the returned 'login' here.", required=True),
            ToolParameter(name="repo", type=ParameterType.STRING, description="Repository name", required=True),
            ToolParameter(name="number", type=ParameterType.INTEGER, description="Pull request number to merge", required=True),
            ToolParameter(name="commit_message", type=ParameterType.STRING, description="Custom commit message for the merge", required=False),
            ToolParameter(name="merge_method", type=ParameterType.STRING, description="Merge method: 'merge', 'squash', or 'rebase'", required=False),
        ],
        tags=[Tag(key="category", value="development"), Tag(key="type", value="write")],
        args_summary=args_template("Merging pull request {owner}/{repo}#{number}", "owner", "repo", "number"),
        result_summary=entity_summary(
            lambda e: "Pull request merged" if e.get("merged") else f"Merge result: {e.get('message', 'unknown')}"
        ),
    )
    async def merge_pull_request(
        self,
        owner: str,
        repo: str,
        number: int,
        commit_message: Optional[str] = None,
        merge_method: str = "merge",
    ) -> Tuple[bool, str]:
        """Merge a pull request in a GitHub repository."""
        try:
            logger.info("github.merge_pull_request called with args: %s", {"owner": owner, "repo": repo, "number": number, "commit_message": commit_message, "merge_method": merge_method})
            response = self.client.merge_pull(
                owner=owner,
                repo=repo,
                number=number,
                commit_message=commit_message,
                merge_method=merge_method,
            )
            return self._handle_response(response, "Pull request merged successfully")
        except Exception as e:
            logger.error(f"Error merging pull request: {e}")
            return False, json.dumps({"error": str(e)})

    # ------------------------------------------------------------------
    # Pull request review and review comment tools
    # ------------------------------------------------------------------

    @tool(
        path="/tools/github/get_pull_request_reviews",
        short_description="Get reviews on a pull request",
        description=(
            "Returns review summary (who approved, requested changes, or commented) with state "
            "(APPROVED, CHANGES_REQUESTED, COMMENT), user, body. Use for 'who approved', 'reviews "
            "on this PR'. For line-level review comments use list_pull_request_comments instead. "
            "Do not use for PR metadata (use get_pull_request)."
        ),
        parameters=[
            ToolParameter(name="owner", type=ParameterType.STRING, description="Repository owner (username or org). For 'my' repo call get_owner(owner='me') first and use the returned 'login' here.", required=True),
            ToolParameter(name="repo", type=ParameterType.STRING, description="Repository name", required=True),
            ToolParameter(name="number", type=ParameterType.INTEGER, description="Pull request number", required=True),
        ],
        tags=[Tag(key="category", value="development"), Tag(key="type", value="read")],
        args_summary=args_template("Fetching reviews on {owner}/{repo}#{number}", "owner", "repo", "number"),
        result_summary=list_summary(("data",), _github_review_label, "review"),
    )
    async def get_pull_request_reviews(self, owner: str, repo: str, number: int) -> Tuple[bool, str]:
        """Get reviews (approve / request changes / comment) on a pull request."""
        try:
            logger.info("github.get_pull_request_reviews called with args: %s", {"owner": owner, "repo": repo, "number": number})
            response = self.client.get_pull_reviews(owner=owner, repo=repo, number=number)
            return self._handle_response(response, "Pull request reviews fetched successfully")
        except Exception as e:
            logger.error(f"Error getting pull request reviews: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        path="/tools/github/create_pull_request_review",
        short_description="Submit a PR review (approve, request changes, or comment)",
        description=(
            "Submits an overall review on a PR. event defaults to COMMENT (omit for general comment); "
            "use APPROVE to approve, REQUEST_CHANGES to request changes. Use when the user wants to "
            "approve a PR, request changes, or submit a general review comment. Do not use for "
            "line-level or file-level comments (use create_pull_request_review_comment) or seeing "
            "existing reviews (use get_pull_request_reviews)."
        ),
        parameters=[
            ToolParameter(name="owner", type=ParameterType.STRING, description="Repository owner (username or org). For 'my' repo call get_owner(owner='me') first and use the returned 'login' here.", required=True),
            ToolParameter(name="repo", type=ParameterType.STRING, description="Repository name", required=True),
            ToolParameter(name="number", type=ParameterType.INTEGER, description="Pull request number", required=True),
            ToolParameter(name="event", type=ParameterType.STRING, description="Review outcome: APPROVE, REQUEST_CHANGES, or COMMENT (general comment without approve/changes). Default COMMENT.", required=False),
            ToolParameter(name="body", type=ParameterType.STRING, description="Review summary text (optional for APPROVE; recommended for REQUEST_CHANGES or COMMENT)", required=False),
        ],
        tags=[Tag(key="category", value="development"), Tag(key="type", value="write")],
        args_summary=args_template("Submitting review on {owner}/{repo}#{number}", "owner", "repo", "number"),
        result_summary=entity_summary(lambda e: f"Review submitted: {e.get('state') or '?'}"),
    )
    async def create_pull_request_review(
        self,
        owner: str,
        repo: str,
        number: int,
        event: str = "COMMENT",
        body: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """Submit a PR review (approve, request changes, or comment). Default event is COMMENT."""
        try:
            logger.info("github.create_pull_request_review called with args: %s", {"owner": owner, "repo": repo, "number": number, "event": event, "body": body})
            response = self.client.create_pull_request_review(
                owner=owner, repo=repo, number=number, event=event, body=body
            )
            return self._handle_response(response, "Pull request review submitted successfully")
        except Exception as e:
            logger.error(f"Error creating pull request review: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        path="/tools/github/list_pull_request_comments",
        short_description="List review comments on a pull request",
        description=(
            "Returns line-level and file-level review comments on a PR (id, body, path, line, user). "
            "Use for 'review comments on PR #N', 'code review discussion'. Distinct from issue/PR "
            "discussion comments (list_issue_comments). Do not use for issue comments (use "
            "list_issue_comments) or adding a review comment (use create_pull_request_review_comment)."
        ),
        parameters=[
            ToolParameter(name="owner", type=ParameterType.STRING, description="Repository owner (username or org). For 'my' repo call get_owner(owner='me') first and use the returned 'login' here.", required=True),
            ToolParameter(name="repo", type=ParameterType.STRING, description="Repository name", required=True),
            ToolParameter(name="number", type=ParameterType.INTEGER, description="Pull request number", required=True),
        ],
        tags=[Tag(key="category", value="development"), Tag(key="type", value="read")],
        args_summary=args_template("Listing review comments on {owner}/{repo}#{number}", "owner", "repo", "number"),
        result_summary=list_summary(("data",), _github_comment_label, "comment"),
    )
    async def list_pull_request_comments(self, owner: str, repo: str, number: int) -> Tuple[bool, str]:
        """List review comments on a pull request."""
        try:
            logger.info("github.list_pull_request_comments called with args: %s", {"owner": owner, "repo": repo, "number": number})
            response = self.client.get_pull_review_comments(owner=owner, repo=repo, number=number)
            return self._handle_response(response, "Pull request comments listed successfully")
        except Exception as e:
            logger.error(f"Error listing pull request comments: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        path="/tools/github/create_pull_request_review_comment",
        short_description="Add a line-level review comment on a pull request",
        description=(
            "Adds a review comment on a specific file/line in a PR. You must call "
            "get_pull_request_commits first and use the returned last_commit_sha as commit_id (or "
            "placeholder {{github.get_pull_request_commits.last_commit_sha}}). Provide path (file "
            "path in repo) and body (comment text). Optional: line (line number), side ('LEFT' or "
            "'RIGHT'). Do not use for PR overall discussion comments (use create_issue_comment with "
            "the PR issue number)."
        ),
        parameters=[
            ToolParameter(name="owner", type=ParameterType.STRING, description="Repository owner (username or org). For 'my' repo call get_owner(owner='me') first and use the returned 'login' here.", required=True),
            ToolParameter(name="repo", type=ParameterType.STRING, description="Repository name", required=True),
            ToolParameter(name="number", type=ParameterType.INTEGER, description="Pull request number", required=True),
            ToolParameter(name="body", type=ParameterType.STRING, description="Comment text", required=True),
            ToolParameter(name="commit_id", type=ParameterType.STRING, description="Commit SHA. Call get_pull_request_commits first, then use placeholder: {{github.get_pull_request_commits.last_commit_sha}}. Do NOT use data[-1].sha.", required=True),
            ToolParameter(name="path", type=ParameterType.STRING, description="File path (e.g. 'src/main.py')", required=True),
            ToolParameter(name="line", type=ParameterType.INTEGER, description="Line number in the file", required=False),
            ToolParameter(name="side", type=ParameterType.STRING, description="'LEFT' or 'RIGHT' for diff side", required=False),
        ],
        tags=[Tag(key="category", value="development"), Tag(key="type", value="write")],
        args_summary=args_template("Adding review comment on {owner}/{repo}#{number}", "owner", "repo", "number"),
        result_summary=entity_summary(lambda e: f"Review comment added: {_github_comment_label(e)}"),
    )
    async def create_pull_request_review_comment(
        self,
        owner: str,
        repo: str,
        number: int,
        body: str,
        commit_id: str,
        path: str,
        line: Optional[int] = None,
        side: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """Create a new review comment on a PR (line or file)."""
        try:
            logger.info("github.create_pull_request_review_comment called with args: %s", {"owner": owner, "repo": repo, "number": number, "body": body[:100] + "..." if len(body) > 100 else body, "commit_id": commit_id, "path": path, "line": line, "side": side})
            response = self.client.create_pull_request_review_comment(
                owner=owner,
                repo=repo,
                number=number,
                body=body,
                commit_id=commit_id,
                path=path,
                line=line,
                side=side,
            )
            return self._handle_response(response, "Pull request review comment created successfully")
        except Exception as e:
            logger.error(f"Error creating pull request review comment: {e}")
            return False, json.dumps({"error": str(e)})

    # ------------------------------------------------------------------
    # Search tools
    # ------------------------------------------------------------------

    @tool(
        path="/tools/github/search_repositories",
        short_description="Search for repositories on GitHub",
        description=(
            "Searches GitHub repos by keyword, language, or criteria. Query can include keywords; "
            "use 'in:name' for repo name, 'in:description', 'language:python', 'stars:>100', etc. "
            "Returns same trimmed fields as list_repositories. Use when the user wants to find "
            "GitHub repositories by keyword, language, or other criteria. Do not use for owner/"
            "user/org profile (use get_owner), repos for a specific user (use list_repositories), "
            "or details of a known repo (use get_repository)."
        ),
        parameters=[
            ToolParameter(name="query", type=ParameterType.STRING, description="GitHub search query. Use 'X in:name' to search repo name, 'X in:description' for description, 'language:python', 'stars:>100', etc.", required=True),
            ToolParameter(name="per_page", type=ParameterType.INTEGER, description="Results per page. Default 10 when omitted; max 50.", required=False),
            ToolParameter(name="page", type=ParameterType.INTEGER, description="Page number (1-based). Default 1 when omitted.", required=False),
        ],
        tags=[Tag(key="category", value="development"), Tag(key="type", value="search")],
        args_summary=args_template('Searching GitHub repositories: "{query}"', "query"),
        result_summary=list_summary(("data",), _github_repo_label, "repository"),
    )
    async def search_repositories(
        self,
        query: str,
        per_page: Optional[int] = None,
        page: Optional[int] = None,
    ) -> Tuple[bool, str]:
        """Search for repositories on GitHub. Default 10 per page, max 50. Results use same trimmed fields as list_repositories."""
        try:
            per_page = per_page if per_page is not None else 10
            per_page = min(50, max(1, per_page))
            page = page if page is not None else 1
            page = max(1, page)
            logger.info("github.search_repositories called with args: %s", {"query": query, "per_page": per_page, "page": page})
            response = self.client.search_repositories(
                query=query, per_page=per_page, page=page
            )
            return self._handle_response(response, "Repository search completed successfully")
        except Exception as e:
            logger.error(f"Error searching repositories: {e}")
            return False, json.dumps({"error": str(e)})
