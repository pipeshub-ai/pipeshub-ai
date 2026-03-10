import json
import logging
from typing import List, Literal, Optional, Tuple

from pydantic import BaseModel, Field, field_validator

from app.agents.tools.config import ToolCategory
from app.agents.tools.decorator import tool
from app.agents.tools.models import ToolIntent
from app.connectors.core.registry.auth_builder import (
    AuthBuilder,
    AuthType,
    OAuthScopeConfig,
)
from app.connectors.core.registry.connector_builder import CommonFields
from app.connectors.core.registry.tool_builder import (
    ToolsetBuilder,
    ToolsetCategory,
)
from app.sources.client.github.github import GitHubClient, GitHubResponse
from app.sources.external.github.github_ import GitHubDataSource

logger = logging.getLogger(__name__)


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
            icon_path="/assets/icons/connectors/github.svg",
            app_group="Development",
            app_description="GitHub OAuth application for agent integration",
        ),
    ])\
    .configure(lambda builder: builder.with_icon("/assets/icons/connectors/github.svg"))\
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
        app_name="github",
        tool_name="create_repository",
        description="Create a new repository on GitHub.",
        llm_description="Creates a repo under the authenticated user. Only name is required (from user query). Do NOT call get_owner first. For private, description, auto_init: use defaults (private=True, description=omit, auto_init=True) if the user did not specify; never ask the user for these.",
        args_schema=CreateRepositoryInput,
        returns="JSON with the created repository details",
        primary_intent=ToolIntent.ACTION,
        category=ToolCategory.CODE_MANAGEMENT,
        when_to_use=[
            "User wants to create a new GitHub repository",
            "User asks to set up a new repo on GitHub",
        ],
        when_not_to_use=[
            "User wants to list or search repositories (use list_repositories or search_repositories)",
            "No GitHub context",
        ],
        typical_queries=["Create a GitHub repo", "Create repo X", "Make a new repository on GitHub"],
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
        app_name="github",
        tool_name="get_repository",
        description="Get details of a specific GitHub repository.",
        llm_description="Returns repo info (description, stars, forks, default_branch, etc.). For the current user's repo, get login from get_owner(owner='me') and use it as owner.",
        args_schema=GetRepositoryInput,
        returns="JSON with repository details including description, stars, forks, etc.",
        primary_intent=ToolIntent.SEARCH,
        category=ToolCategory.CODE_MANAGEMENT,
        when_to_use=[
            "User asks for information about a specific GitHub repository",
            "User wants to inspect a repo's details",
        ],
        when_not_to_use=[
            "User wants owner/user/org profile or details (use get_owner)",
            "User wants to list multiple repos (use list_repositories)",
            "User wants to search repos by keyword (use search_repositories)",
        ],
        typical_queries=["Get info about the repo owner/repo", "Show me the GitHub repository details"],
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
        app_name="github",
        tool_name="get_owner",
        description="Get details of a GitHub user or organization.",
        llm_description="Returns profile info (login, name, avatar_url, bio, public_repos). Use owner='me' to get the authenticated user's profile; use the returned 'login' as owner/user in other tools (list_repositories, get_repository, create_issue, etc.). owner_type: 'user' or 'organization'.",
        args_schema=GetOwnerInput,
        returns="JSON with owner details (login, name, avatar_url, html_url, bio, public_repos, etc.)",
        primary_intent=ToolIntent.SEARCH,
        category=ToolCategory.CODE_MANAGEMENT,
        when_to_use=[
            "User wants details or profile of a GitHub user or organization (the owner)",
            "User asks who is X, info about user/org X, profile for the owner, or details of the owner of a repo",
        ],
        when_not_to_use=[
            "User wants repos list (use list_repositories)",
            "User wants a specific repository's details (use get_repository)",
        ],
        typical_queries=["Who is X on GitHub?", "Get details for org Y", "Profile for user X", "Info about the owner"],
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
        app_name="github",
        tool_name="list_repositories",
        description="List repositories for a GitHub user.",
        llm_description="Lists repos for a user. For the authenticated user's repos, call get_owner(owner='me') first and use the returned login as user. Optional: type (owner/all/member), per_page (max 50), page.",
        args_schema=ListRepositoriesInput,
        returns="JSON array of repositories owned by the user",
        primary_intent=ToolIntent.SEARCH,
        category=ToolCategory.CODE_MANAGEMENT,
        when_to_use=[
            "User wants to see all repositories for a GitHub user",
            "User asks to list repos for a given username",
        ],
        when_not_to_use=[
            "User wants owner/user/org profile or details (use get_owner)",
            "User wants details of a single specific repo (use get_repository)",
            "User wants to search repos by keyword (use search_repositories)",
        ],
        typical_queries=["List my GitHub repos", "Show repositories for user X"],
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
        app_name="github",
        tool_name="create_issue",
        description="Create a new issue in a repository.",
        llm_description="Creates an issue. Need owner, repo, title. Optional: body, assignees (list of GitHub usernames), labels. For current user's repo use get_owner(owner='me') to get owner.",
        args_schema=CreateIssueInput,
        returns="JSON with the created issue details including number and URL",
        primary_intent=ToolIntent.ACTION,
        category=ToolCategory.CODE_MANAGEMENT,
        when_to_use=[
            "User wants to open a GitHub issue",
            "User asks to report a bug or feature request on GitHub",
        ],
        when_not_to_use=[
            "User wants owner/user/org profile (use get_owner)",
            "User wants to look up an existing issue (use get_issue)",
            "No GitHub context",
        ],
        typical_queries=["Create a GitHub issue", "Open a bug report", "File a feature request on GitHub"],
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
        app_name="github",
        tool_name="get_issue",
        description="Get details of a specific issue by number.",
        llm_description="Returns a single issue by owner, repo, and issue number. Use for 'show issue #N', 'status of issue X', or when you need full issue details (title, body, state, assignees, labels).",
        args_schema=GetIssueInput,
        returns="JSON with issue details including title, body, state, assignees, and labels",
        primary_intent=ToolIntent.SEARCH,
        category=ToolCategory.CODE_MANAGEMENT,
        when_to_use=[
            "User wants to look up a specific GitHub issue by number",
            "User asks about the status or details of an issue",
        ],
        when_not_to_use=[
            "User wants owner/user/org profile (use get_owner)",
            "User wants to create an issue (use create_issue)",
            "User wants to close an issue (use close_issue)",
        ],
        typical_queries=["Show me issue #42", "What is the status of GitHub issue 10?"],
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
        app_name="github",
        tool_name="list_issues",
        description="List issues in a repository with optional filters.",
        llm_description="Lists issues in a repo. Always returns one page (default 10 per page, max 50). Params: owner, repo; optional state ('open'/'closed'/'all'), labels, assignee, per_page (default 10), page (default 1).",
        args_schema=ListIssuesInput,
        returns="JSON array of issues in the repository",
        primary_intent=ToolIntent.SEARCH,
        category=ToolCategory.CODE_MANAGEMENT,
        when_to_use=[
            "User wants to list or see all issues in a repo",
            "User asks for open/closed issues, or issues by label/assignee",
        ],
        when_not_to_use=[
            "User wants owner/user/org profile (use get_owner)",
            "User wants a single issue by number (use get_issue)",
            "User wants to create an issue (use create_issue)",
        ],
        typical_queries=["List issues in repo X", "Show open issues", "All issues in my repo"],
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
        app_name="github",
        tool_name="close_issue",
        description="Close an issue in a repository.",
        llm_description="Marks an issue as closed. Requires owner, repo, and issue number. Use when user wants to close or resolve an issue (for reopening use update_issue with state='open').",
        args_schema=CloseIssueInput,
        returns="JSON with the updated issue confirming its closed state",
        primary_intent=ToolIntent.ACTION,
        category=ToolCategory.CODE_MANAGEMENT,
        when_to_use=[
            "User wants to close a GitHub issue",
            "User asks to mark an issue as resolved",
        ],
        when_not_to_use=[
            "User wants owner/user/org profile (use get_owner)",
            "User wants to view an issue (use get_issue)",
            "User wants to create an issue (use create_issue)",
        ],
        typical_queries=["Close issue #5", "Mark GitHub issue 10 as closed"],
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
        app_name="github",
        tool_name="update_issue",
        description="Update an issue (title, body, state, assignees, or labels).",
        llm_description="Updates an issue. Pass only the fields to change; omit others. Can set state to 'open' or 'closed' (reopen/close). assignees and labels replace existing.",
        args_schema=UpdateIssueInput,
        returns="JSON with the updated issue details",
        primary_intent=ToolIntent.ACTION,
        category=ToolCategory.CODE_MANAGEMENT,
        when_to_use=[
            "User wants to edit or update a GitHub issue",
            "User asks to change issue title, body, state, assignees, or labels",
        ],
        when_not_to_use=[
            "User wants owner/user/org profile (use get_owner)",
            "User wants to view an issue (use get_issue)",
            "User wants to create an issue (use create_issue)",
            "User wants only to close an issue (use close_issue)",
        ],
        typical_queries=["Update issue #5", "Edit issue title", "Change issue body", "Reopen issue #3"],
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
        app_name="github",
        tool_name="list_issue_comments",
        description="List all comments on an issue.",
        llm_description="Returns all comments on an issue (owner, repo, issue number). Use for 'show comments on issue #N', 'discussion on this issue'. Comment ids from here can be used with get_issue_comment.",
        args_schema=ListIssueCommentsInput,
        returns="JSON with list of comments (id, body, user, created_at, updated_at)",
        primary_intent=ToolIntent.SEARCH,
        category=ToolCategory.CODE_MANAGEMENT,
        when_to_use=[
            "User wants to see comments on an issue",
            "User asks for discussion or thread on an issue",
        ],
        when_not_to_use=[
            "User wants to add a comment (use create_issue_comment)",
            "User wants a single comment by ID (use get_issue_comment)",
        ],
        typical_queries=["List comments on issue #5", "Show discussion on issue #12"],
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
        app_name="github",
        tool_name="get_issue_comment",
        description="Get a single issue comment by ID.",
        llm_description="Returns one comment by comment_id. Get comment_id from list_issue_comments. Params: owner, repo, number (issue number), comment_id.",
        args_schema=GetIssueCommentInput,
        returns="JSON with comment details (id, body, user, created_at, updated_at)",
        primary_intent=ToolIntent.SEARCH,
        category=ToolCategory.CODE_MANAGEMENT,
        when_to_use=["User wants one specific comment by ID"],
        when_not_to_use=["User wants all comments (use list_issue_comments)"],
        typical_queries=["Get comment 123 on issue #5"],
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
        app_name="github",
        tool_name="create_issue_comment",
        description="Add a comment to an issue.",
        llm_description="Posts a comment on an issue. Params: owner, repo, number (issue number), body (comment text; Markdown supported). Use for 'comment on issue #N', 'reply to this issue'. For PRs use the PR's issue number.",
        args_schema=CreateIssueCommentInput,
        returns="JSON with the created comment details",
        primary_intent=ToolIntent.ACTION,
        category=ToolCategory.CODE_MANAGEMENT,
        when_to_use=[
            "User wants to post a comment on an issue",
            "User asks to reply or respond on an issue",
        ],
        when_not_to_use=[
            "User wants to list or read comments (use list_issue_comments or get_issue_comment)",
            "User wants to add a line-level review comment on a PR (use create_pull_request_review_comment)",
        ],
        typical_queries=["Comment on issue #5", "Add a reply to issue #12"],
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
        app_name="github",
        tool_name="create_pull_request",
        description="Create a new pull request.",
        llm_description="Creates a PR: owner, repo, title, head (source branch), base (target branch). Optional: body, draft (default False).",
        args_schema=CreatePullRequestInput,
        returns="JSON with the created pull request details including number and URL",
        primary_intent=ToolIntent.ACTION,
        category=ToolCategory.CODE_MANAGEMENT,
        when_to_use=[
            "User wants to open a pull request on GitHub",
            "User asks to create a PR to merge one branch into another",
        ],
        when_not_to_use=[
            "User wants owner/user/org profile (use get_owner)",
            "User wants to view a PR (use get_pull_request)",
            "User wants to merge a PR (use merge_pull_request)",
        ],
        typical_queries=["Create a pull request", "Open a PR from feature-branch to main"],
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
        app_name="github",
        tool_name="get_pull_request",
        description="Get details of a specific pull request and its conversation comments.",
        llm_description="Returns a single PR by owner, repo, and PR number, plus conversation comments (issue-level discussion). Use for 'show PR #N', 'status of this PR', 'PR with comments'. Response has data.pr (title, state, head/base branches, reviewers, etc.) and data.conversation_comments (list of discussion comments). Not for listing PRs (use list_pull_requests) or file changes (use get_pull_request_file_changes).",
        args_schema=GetPullRequestInput,
        returns="JSON with data.pr (pull request details) and data.conversation_comments (list of discussion comments)",
        primary_intent=ToolIntent.SEARCH,
        category=ToolCategory.CODE_MANAGEMENT,
        when_to_use=[
            "User wants to look up a specific pull request by number",
            "User asks about the status or details of a PR",
        ],
        when_not_to_use=[
            "User wants owner/user/org profile (use get_owner)",
            "User wants to create a PR (use create_pull_request)",
            "User wants to merge a PR (use merge_pull_request)",
        ],
        typical_queries=["Show me PR #7", "What is the status of pull request 3?", "PR #5 with comments", "Show PR and discussion"],
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
        app_name="github",
        tool_name="get_pull_request_commits",
        description="Get the list of commits in a pull request.",
        llm_description="Returns commits in the PR. Response includes last_commit_sha — use that as commit_id when calling create_pull_request_review_comment (or use placeholder {{github.get_pull_request_commits.last_commit_sha}}). Call this before adding a line-level review comment.",
        args_schema=GetPullRequestCommitsInput,
        returns="JSON with data (array of commits), length, and last_commit_sha.",
        primary_intent=ToolIntent.SEARCH,
        category=ToolCategory.CODE_MANAGEMENT,
        when_to_use=[
            "User wants to comment on a PR or add a review comment (get commit_id from commits first)",
            "User asks about commits in a PR",
        ],
        when_not_to_use=[
            "User wants PR details only (use get_pull_request)",
            "User wants to merge or list PRs (use merge_pull_request or list_pull_requests)",
        ],
        typical_queries=["Comment on this PR", "Add a review comment on PR #7", "What commits are in PR #5?"],
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
        app_name="github",
        tool_name="get_pull_request_file_changes",
        description="Get files changed in a pull request with complete diffs.",
        llm_description=(
            "Returns list of changed files in a PR with diffs. By default fetches FULL CONTENT "
            "for large files with truncated patches, generating complete diffs locally. "
            "Use for 'review this PR', 'see what changed', 'what files in this PR', 'diff for PR #N'. "
            "Set fetch_full_content=False for quick overview without expanding truncated files."
        ),
        args_schema=GetPullRequestFileChangesInput,
        returns="JSON array of changed files with filename, status, additions, deletions, and complete patch/diff",
        primary_intent=ToolIntent.SEARCH,
        category=ToolCategory.CODE_MANAGEMENT,
        when_to_use=[
            "User wants to review a PR or review this PR",
            "User asks what changes in this PR / what files changed / what's in this PR",
            "User asks for the diff or file changes of a pull request",
        ],
        when_not_to_use=[
            "User wants PR metadata only (use get_pull_request)",
            "User wants commits list (use get_pull_request_commits)",
            "User wants review comments (use list_pull_request_comments)",
        ],
        typical_queries=[
            "Review this PR",
            "What changes in PR #5?",
            "What files changed in this PR?",
            "Show me the diff for PR #7"
        ],
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
        app_name="github",
        tool_name="list_pull_requests",
        description="List pull requests in a repository with optional filters.",
        llm_description="Lists PRs in a repo. Always returns one page (default 10 per page, max 50). Params: owner, repo; optional state ('open'/'closed'/'all'), head, base, per_page (default 10), page (default 1).",
        args_schema=ListPullRequestsInput,
        returns="JSON array of pull requests in the repository",
        primary_intent=ToolIntent.SEARCH,
        category=ToolCategory.CODE_MANAGEMENT,
        when_to_use=[
            "User wants to list or see all pull requests in a repo",
            "User asks for open/closed PRs, or PRs by branch",
        ],
        when_not_to_use=[
            "User wants owner/user/org profile (use get_owner)",
            "User wants a single PR by number (use get_pull_request)",
            "User wants to create a PR (use create_pull_request)",
        ],
        typical_queries=["List PRs in repo X", "Show open pull requests", "All PRs in my repo"],
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
        app_name="github",
        tool_name="merge_pull_request",
        description="Merge a pull request.",
        llm_description="Merges a PR. Optional: commit_message, merge_method ('merge', 'squash', or 'rebase'; default 'merge').",
        args_schema=MergePullRequestInput,
        returns="JSON with merge status confirming the PR was merged",
        primary_intent=ToolIntent.ACTION,
        category=ToolCategory.CODE_MANAGEMENT,
        when_to_use=[
            "User wants to merge a GitHub pull request",
            "User asks to accept and merge a PR",
        ],
        when_not_to_use=[
            "User wants owner/user/org profile (use get_owner)",
            "User wants to view a PR (use get_pull_request)",
            "User wants to create a PR (use create_pull_request)",
        ],
        typical_queries=["Merge pull request #4", "Accept and merge PR 12"],
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
        app_name="github",
        tool_name="get_pull_request_reviews",
        description="Get reviews on a pull request (approvals, change requests, comments).",
        llm_description="Returns review summary (who approved, requested changes, or commented) with state (APPROVED, CHANGES_REQUESTED, COMMENT), user, body. Use for 'who approved', 'reviews on this PR'. For line-level review comments use list_pull_request_comments.",
        args_schema=GetPullRequestInput,
        returns="JSON array of reviews with state (APPROVED, CHANGES_REQUESTED, COMMENT), user, body, submitted_at",
        primary_intent=ToolIntent.SEARCH,
        category=ToolCategory.CODE_MANAGEMENT,
        when_to_use=[
            "User wants to see reviews on a PR (who approved, who requested changes)",
            "User asks who reviewed this PR or what are the reviews",
        ],
        when_not_to_use=[
            "User wants line-level review comments (use list_pull_request_comments)",
            "User wants PR metadata (use get_pull_request)",
        ],
        typical_queries=["Reviews on PR #7", "Who approved this PR?", "Get reviews for pull request #5"],
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
        app_name="github",
        tool_name="create_pull_request_review",
        description="Submit a PR review: approve, request changes, or comment.",
        llm_description="Submits an overall review on a PR. event defaults to COMMENT (omit for general comment); use APPROVE to approve, REQUEST_CHANGES to request changes.",
        args_schema=CreatePullRequestReviewInput,
        returns="JSON with the created review details",
        primary_intent=ToolIntent.ACTION,
        category=ToolCategory.CODE_MANAGEMENT,
        when_to_use=[
            "User wants to approve a pull request",
            "User wants to request changes on a PR",
            "User wants to submit a review or leave a general review comment on a PR",
        ],
        when_not_to_use=[
            "User wants to add a line-level or file-level comment (use create_pull_request_review_comment)",
            "User wants to see existing reviews (use get_pull_request_reviews)",
        ],
        typical_queries=["Approve PR #5", "Request changes on this PR", "Submit a review on PR #7"],
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
        app_name="github",
        tool_name="list_pull_request_comments",
        description="List review comments on a pull request.",
        llm_description="Returns line-level and file-level review comments on a PR (id, body, path, line, user). Use for 'review comments on PR #N', 'code review discussion'. Distinct from issue/PR discussion comments (list_issue_comments). Comment ids here used by edit_pull_request_review_comment.",
        args_schema=ListPullRequestCommentsInput,
        returns="JSON with list of review comments (id, body, path, line, user, created_at)",
        primary_intent=ToolIntent.SEARCH,
        category=ToolCategory.CODE_MANAGEMENT,
        when_to_use=[
            "User wants to see review comments on a PR",
            "User asks for code review discussion on a PR",
        ],
        when_not_to_use=[
            "User wants issue comments (use list_issue_comments on the issue number)",
            "User wants to add a review comment (use create_pull_request_review_comment)",
        ],
        typical_queries=["List comments on PR #7", "Show review comments on pull request #12"],
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
        app_name="github",
        tool_name="create_pull_request_review_comment",
        description="Add a line-level or file-level review comment on a pull request.",
        llm_description="Adds a review comment on a specific file/line in a PR. You must call get_pull_request_commits first and use the returned last_commit_sha as commit_id (or placeholder {{github.get_pull_request_commits.last_commit_sha}}). Provide path (file path in repo) and body (comment text). Optional: line (line number), side ('LEFT' or 'RIGHT').",
        args_schema=CreatePullRequestReviewCommentInput,
        returns="JSON with the created comment details",
        primary_intent=ToolIntent.ACTION,
        category=ToolCategory.CODE_MANAGEMENT,
        when_to_use=[
            "User wants to add a review comment on a specific line or file in a PR",
        ],
        when_not_to_use=[
            "User wants to comment on the PR overall discussion (use create_issue_comment with the PR issue number)",
            "User wants to reply to an existing review comment; add a new comment with create_pull_request_review_comment)",
        ],
        typical_queries=["Comment on line 10 of src/main.py in PR #7", "Add review comment on projects.html line 18 in PR #5"],
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
        app_name="github",
        tool_name="search_repositories",
        description="Search for repositories on GitHub by keyword, language, or criteria.",
        llm_description="Searches GitHub repos. Query can include keywords; use 'in:name' for repo name, 'in:description', 'language:python', 'stars:>100', etc. Returns same trimmed fields as list_repositories.",
        args_schema=SearchRepositoriesInput,
        returns="JSON array of matching repositories (trimmed fields).",
        primary_intent=ToolIntent.SEARCH,
        category=ToolCategory.CODE_MANAGEMENT,
        when_to_use=[
            "User wants to find GitHub repositories by keyword, language, or other criteria",
            "User asks to search for repos on GitHub",
        ],
        when_not_to_use=[
            "User wants owner/user/org profile (use get_owner)",
            "User wants repos for a specific user (use list_repositories)",
            "User wants details of a known repo (use get_repository)",
        ],
        typical_queries=["Search GitHub for Python web frameworks", "Find repos with 'machine learning' in the name"],
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
