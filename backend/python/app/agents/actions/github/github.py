import asyncio
import json
import logging
import threading
from typing import List, Optional, Tuple

from app.agents.tools.decorator import tool
from app.agents.tools.enums import ParameterType
from app.agents.tools.models import ToolParameter
from app.sources.client.github.github import GitHubClient, GitHubResponse
from app.sources.external.github.github_ import GitHubDataSource

logger = logging.getLogger(__name__)


class GitHub:
    """GitHub tools exposed to the agents using GitHubDataSource"""

    def __init__(self, client: GitHubClient) -> None:
        """Initialize the GitHub tool with a data source wrapper.
        Args:
            client: An initialized `GitHubClient` instance
        """
        self.client = GitHubDataSource(client)
        self._bg_loop = asyncio.new_event_loop()
        self._bg_loop_thread = threading.Thread(
            target=self._start_background_loop, daemon=True
        )
        self._bg_loop_thread.start()

    def _start_background_loop(self) -> None:
        """Start the background event loop."""
        asyncio.set_event_loop(self._bg_loop)
        self._bg_loop.run_forever()

    def _run_async(self, coro) -> GitHubResponse:
        """Run a coroutine safely from sync context via a dedicated loop."""
        future = asyncio.run_coroutine_threadsafe(coro, self._bg_loop)
        return future.result()

    def shutdown(self) -> None:
        """Gracefully stop the background event loop and thread."""
        try:
            if getattr(self, "_bg_loop", None) is not None and self._bg_loop.is_running():
                self._bg_loop.call_soon_threadsafe(self._bg_loop.stop)
            if getattr(self, "_bg_loop_thread", None) is not None:
                self._bg_loop_thread.join()
            if getattr(self, "_bg_loop", None) is not None:
                self._bg_loop.close()
        except Exception as exc:
            logger.warning(f"GitHub shutdown encountered an issue: {exc}")

    def _handle_response(
        self, response: GitHubResponse, success_message: str
    ) -> Tuple[bool, str]:
        """Handle GitHubResponse and return standardized tuple."""
        if response.success:
            return True, json.dumps(
                {"message": success_message, "data": response.data or {}}
            )
        return False, json.dumps({"error": response.error or "Unknown error"})

    @tool(
        app_name="github",
        tool_name="create_repository",
        description="Create a new repository on GitHub",
        parameters=[
            ToolParameter(
                name="name",
                type=ParameterType.STRING,
                description="The name of the repository (required)",
            ),
            ToolParameter(
                name="private",
                type=ParameterType.BOOLEAN,
                description="Whether the repository should be private. Defaults to True.",
                required=False,
                default=True,
            ),
            ToolParameter(
                name="description",
                type=ParameterType.STRING,
                description="A short description of the repository",
                required=False,
            ),
            ToolParameter(
                name="auto_init",
                type=ParameterType.BOOLEAN,
                description="Whether to initialize the repository with a README. Defaults to True.",
                required=False,
                default=True,
            ),
        ],
        returns="JSON with the created repository details",
    )
    def create_repository(
        self,
        name: str,
        private: bool = True,
        description: Optional[str] = None,
        auto_init: bool = True,
    ) -> Tuple[bool, str]:
        """Create a new repository on GitHub."""
        try:
            response = self._run_async(
                self.client.create_repo(
                    name=name,
                    private=private,
                    description=description,
                    auto_init=auto_init,
                )
            )
            return self._handle_response(response, "Repository created successfully")
        except Exception as e:
            logger.error(f"Error creating repository: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="github",
        tool_name="get_repository",
        description="Get details of a specific repository from GitHub",
        parameters=[
            ToolParameter(
                name="owner",
                type=ParameterType.STRING,
                description="The owner of the repository (username or organization) (required)",
            ),
            ToolParameter(
                name="repo",
                type=ParameterType.STRING,
                description="The name of the repository (required)",
            ),
        ],
        returns="JSON with repository details",
    )
    def get_repository(self, owner: str, repo: str) -> Tuple[bool, str]:
        """Get details of a specific repository from GitHub."""
        try:
            response = self._run_async(self.client.get_repo(owner=owner, repo=repo))
            return self._handle_response(response, "Repository fetched successfully")
        except Exception as e:
            logger.error(f"Error getting repository: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="github",
        tool_name="update_repository",
        description="Update repository settings (Note: GitHub API doesn't support direct repo updates, this tool provides repository information)",
        parameters=[
            ToolParameter(
                name="owner",
                type=ParameterType.STRING,
                description="The owner of the repository (username or organization) (required)",
            ),
            ToolParameter(
                name="repo",
                type=ParameterType.STRING,
                description="The name of the repository (required)",
            ),
        ],
        returns="JSON with repository details",
    )
    def update_repository(self, owner: str, repo: str) -> Tuple[bool, str]:
        """Get repository details (GitHub API doesn't support direct repository updates)."""
        try:
            response = self._run_async(self.client.get_repo(owner=owner, repo=repo))
            return self._handle_response(response, "Repository details retrieved successfully")
        except Exception as e:
            logger.error(f"Error getting repository: {e}")
            return False, json.dumps({"error": str(e)})

    # delete_repository removed: non-functional and misleading

    @tool(
        app_name="github",
        tool_name="create_issue",
        description="Create a new issue in a GitHub repository",
        parameters=[
            ToolParameter(
                name="owner",
                type=ParameterType.STRING,
                description="The owner of the repository (username or organization) (required)",
            ),
            ToolParameter(
                name="repo",
                type=ParameterType.STRING,
                description="The name of the repository (required)",
            ),
            ToolParameter(
                name="title",
                type=ParameterType.STRING,
                description="The title of the issue (required)",
            ),
            ToolParameter(
                name="body",
                type=ParameterType.STRING,
                description="The body/description of the issue",
                required=False,
            ),
            ToolParameter(
                name="assignees",
                type=ParameterType.ARRAY,
                description="A list of usernames to assign to the issue",
                required=False,
            ),
            ToolParameter(
                name="labels",
                type=ParameterType.ARRAY,
                description="A list of label names to apply to the issue",
                required=False,
            ),
        ],
        returns="JSON with the created issue details",
    )
    def create_issue(
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
            response = self._run_async(
                self.client.create_issue(
                    owner=owner,
                    repo=repo,
                    title=title,
                    body=body,
                    assignees=assignees,
                    labels=labels,
                )
            )
            return self._handle_response(response, "Issue created successfully")
        except Exception as e:
            logger.error(f"Error creating issue: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="github",
        tool_name="get_issue",
        description="Get details of a specific issue from a GitHub repository",
        parameters=[
            ToolParameter(
                name="owner",
                type=ParameterType.STRING,
                description="The owner of the repository (username or organization) (required)",
            ),
            ToolParameter(
                name="repo",
                type=ParameterType.STRING,
                description="The name of the repository (required)",
            ),
            ToolParameter(
                name="number",
                type=ParameterType.NUMBER,
                description="The issue number (required)",
            ),
        ],
        returns="JSON with issue details",
    )
    def get_issue(self, owner: str, repo: str, number: int) -> Tuple[bool, str]:
        """Get details of a specific issue from a GitHub repository."""
        try:
            response = self._run_async(
                self.client.get_issue(owner=owner, repo=repo, number=number)
            )
            return self._handle_response(response, "Issue fetched successfully")
        except Exception as e:
            logger.error(f"Error getting issue: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="github",
        tool_name="close_issue",
        description="Close an issue in a GitHub repository",
        parameters=[
            ToolParameter(
                name="owner",
                type=ParameterType.STRING,
                description="The owner of the repository (username or organization) (required)",
            ),
            ToolParameter(
                name="repo",
                type=ParameterType.STRING,
                description="The name of the repository (required)",
            ),
            ToolParameter(
                name="number",
                type=ParameterType.NUMBER,
                description="The issue number (required)",
            ),
        ],
        returns="JSON with updated issue details",
    )
    def close_issue(self, owner: str, repo: str, number: int) -> Tuple[bool, str]:
        """Close an issue in a GitHub repository."""
        try:
            response = self._run_async(
                self.client.close_issue(owner=owner, repo=repo, number=number)
            )
            return self._handle_response(response, "Issue closed successfully")
        except Exception as e:
            logger.error(f"Error closing issue: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="github",
        tool_name="create_pull_request",
        description="Create a new pull request in a GitHub repository",
        parameters=[
            ToolParameter(
                name="owner",
                type=ParameterType.STRING,
                description="The owner of the repository (username or organization) (required)",
            ),
            ToolParameter(
                name="repo",
                type=ParameterType.STRING,
                description="The name of the repository (required)",
            ),
            ToolParameter(
                name="title",
                type=ParameterType.STRING,
                description="The title of the pull request (required)",
            ),
            ToolParameter(
                name="head",
                type=ParameterType.STRING,
                description="The branch to merge from (required)",
            ),
            ToolParameter(
                name="base",
                type=ParameterType.STRING,
                description="The branch to merge into (required)",
            ),
            ToolParameter(
                name="body",
                type=ParameterType.STRING,
                description="The body/description of the pull request",
                required=False,
            ),
            ToolParameter(
                name="draft",
                type=ParameterType.BOOLEAN,
                description="Whether the pull request should be a draft. Defaults to False.",
                required=False,
                default=False,
            ),
        ],
        returns="JSON with the created pull request details",
    )
    def create_pull_request(
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
            response = self._run_async(
                self.client.create_pull(
                    owner=owner,
                    repo=repo,
                    title=title,
                    head=head,
                    base=base,
                    body=body,
                    draft=draft,
                )
            )
            return self._handle_response(response, "Pull request created successfully")
        except Exception as e:
            logger.error(f"Error creating pull request: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="github",
        tool_name="get_pull_request",
        description="Get details of a specific pull request from a GitHub repository",
        parameters=[
            ToolParameter(
                name="owner",
                type=ParameterType.STRING,
                description="The owner of the repository (username or organization) (required)",
            ),
            ToolParameter(
                name="repo",
                type=ParameterType.STRING,
                description="The name of the repository (required)",
            ),
            ToolParameter(
                name="number",
                type=ParameterType.NUMBER,
                description="The pull request number (required)",
            ),
        ],
        returns="JSON with pull request details",
    )
    def get_pull_request(self, owner: str, repo: str, number: int) -> Tuple[bool, str]:
        """Get details of a specific pull request from a GitHub repository."""
        try:
            response = self._run_async(
                self.client.get_pull(owner=owner, repo=repo, number=number)
            )
            return self._handle_response(response, "Pull request fetched successfully")
        except Exception as e:
            logger.error(f"Error getting pull request: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="github",
        tool_name="merge_pull_request",
        description="Merge a pull request in a GitHub repository",
        parameters=[
            ToolParameter(
                name="owner",
                type=ParameterType.STRING,
                description="The owner of the repository (username or organization) (required)",
            ),
            ToolParameter(
                name="repo",
                type=ParameterType.STRING,
                description="The name of the repository (required)",
            ),
            ToolParameter(
                name="number",
                type=ParameterType.NUMBER,
                description="The pull request number (required)",
            ),
            ToolParameter(
                name="commit_message",
                type=ParameterType.STRING,
                description="The commit message for the merge",
                required=False,
            ),
            ToolParameter(
                name="merge_method",
                type=ParameterType.STRING,
                description="The merge method ('merge', 'squash', or 'rebase'). Defaults to 'merge'.",
                required=False,
                default="merge",
            ),
        ],
        returns="JSON with merge status",
    )
    def merge_pull_request(
        self,
        owner: str,
        repo: str,
        number: int,
        commit_message: Optional[str] = None,
        merge_method: str = "merge",
    ) -> Tuple[bool, str]:
        """Merge a pull request in a GitHub repository."""
        try:
            response = self._run_async(
                self.client.merge_pull(
                    owner=owner,
                    repo=repo,
                    number=number,
                    commit_message=commit_message,
                    merge_method=merge_method,
                )
            )
            return self._handle_response(response, "Pull request merged successfully")
        except Exception as e:
            logger.error(f"Error merging pull request: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="github",
        tool_name="search_repositories",
        description="Search for repositories on GitHub",
        parameters=[
            ToolParameter(
                name="query",
                type=ParameterType.STRING,
                description="The search query string (required)",
            ),
        ],
        returns="JSON with search results",
    )
    def search_repositories(self, query: str) -> Tuple[bool, str]:
        """Search for repositories on GitHub."""
        try:
            response = self._run_async(self.client.search_repositories(query=query))
            return self._handle_response(response, "Repository search completed successfully")
        except Exception as e:
            logger.error(f"Error searching repositories: {e}")
            return False, json.dumps({"error": str(e)})
