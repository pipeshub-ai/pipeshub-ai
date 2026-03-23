import asyncio
import json
import logging
import threading
from typing import List, Optional, Tuple

from app.agents.tools.decorator import tool
from app.agents.tools.enums import ParameterType
from app.agents.tools.models import ToolParameter
from app.connectors.core.registry.auth_builder import (
    AuthBuilder,
    AuthType,
    OAuthScopeConfig,
)
from app.connectors.core.registry.connector_builder import CommonFields
from app.connectors.core.registry.tool_builder import (
    ToolCategory,
    ToolDefinition,
    ToolsetBuilder,
)
from app.sources.client.gitlab.gitlab import GitLabClient, GitLabResponse
from app.sources.external.gitlab.gitlab_ import GitLabDataSource

logger = logging.getLogger(__name__)

# Define tools
tools: List[ToolDefinition] = [
    ToolDefinition(
        name="create_project",
        description="Create a new GitLab project",
        parameters=[
            {"name": "name", "type": "string", "description": "Project name", "required": True},
            {"name": "visibility", "type": "string", "description": "Project visibility", "required": False}
        ],
        tags=["projects", "create"]
    ),
    ToolDefinition(
        name="get_project",
        description="Get details of a project",
        parameters=[
            {"name": "project_id", "type": "string", "description": "Project ID", "required": True}
        ],
        tags=["projects", "read"]
    ),
    ToolDefinition(
        name="update_project",
        description="Update project settings",
        parameters=[
            {"name": "project_id", "type": "string", "description": "Project ID", "required": True}
        ],
        tags=["projects", "update"]
    ),
    ToolDefinition(
        name="delete_project",
        description="Delete a project",
        parameters=[
            {"name": "project_id", "type": "string", "description": "Project ID", "required": True}
        ],
        tags=["projects", "delete"]
    ),
    ToolDefinition(
        name="create_issue",
        description="Create a new issue",
        parameters=[
            {"name": "project_id", "type": "string", "description": "Project ID", "required": True},
            {"name": "title", "type": "string", "description": "Issue title", "required": True}
        ],
        tags=["issues", "create"]
    ),
    ToolDefinition(
        name="get_issue",
        description="Get issue details",
        parameters=[
            {"name": "project_id", "type": "string", "description": "Project ID", "required": True},
            {"name": "issue_id", "type": "integer", "description": "Issue ID", "required": True}
        ],
        tags=["issues", "read"]
    ),
    ToolDefinition(
        name="update_issue",
        description="Update an issue",
        parameters=[
            {"name": "project_id", "type": "string", "description": "Project ID", "required": True},
            {"name": "issue_id", "type": "integer", "description": "Issue ID", "required": True}
        ],
        tags=["issues", "update"]
    ),
    ToolDefinition(
        name="delete_issue",
        description="Delete an issue",
        parameters=[
            {"name": "project_id", "type": "string", "description": "Project ID", "required": True},
            {"name": "issue_id", "type": "integer", "description": "Issue ID", "required": True}
        ],
        tags=["issues", "delete"]
    ),
    ToolDefinition(
        name="create_merge_request",
        description="Create a merge request",
        parameters=[
            {"name": "project_id", "type": "string", "description": "Project ID", "required": True},
            {"name": "source_branch", "type": "string", "description": "Source branch", "required": True},
            {"name": "target_branch", "type": "string", "description": "Target branch", "required": True},
            {"name": "title", "type": "string", "description": "MR title", "required": True}
        ],
        tags=["merge-requests", "create"]
    ),
    ToolDefinition(
        name="get_merge_request",
        description="Get merge request details",
        parameters=[
            {"name": "project_id", "type": "string", "description": "Project ID", "required": True},
            {"name": "merge_request_id", "type": "integer", "description": "MR ID", "required": True}
        ],
        tags=["merge-requests", "read"]
    ),
    ToolDefinition(
        name="merge_merge_request",
        description="Merge a merge request",
        parameters=[
            {"name": "project_id", "type": "string", "description": "Project ID", "required": True},
            {"name": "merge_request_id", "type": "integer", "description": "MR ID", "required": True}
        ],
        tags=["merge-requests", "merge"]
    ),
    ToolDefinition(
        name="search_projects",
        description="Search for projects",
        parameters=[
            {"name": "query", "type": "string", "description": "Search query", "required": True}
        ],
        tags=["projects", "search"]
    ),
]


# Register GitLab toolset
@ToolsetBuilder("GitLab")\
    .in_group("Development")\
    .with_description("GitLab integration for repository management, issues, and merge requests")\
    .with_category(ToolCategory.APP)\
    .with_auth([
        AuthBuilder.type(AuthType.OAUTH).oauth(
            connector_name="GitLab",
            authorize_url="https://gitlab.com/oauth/authorize",
            token_url="https://gitlab.com/oauth/token",
            redirect_uri="toolsets/oauth/callback/gitlab",
            scopes=OAuthScopeConfig(
                personal_sync=[],
                team_sync=[],
                agent=[
                    "api",
                    "read_user",
                    "read_repository",
                    "write_repository"
                ]
            ),
            fields=[
                CommonFields.client_id("GitLab Application Settings"),
                CommonFields.client_secret("GitLab Application Settings")
            ],
            icon_path="/assets/icons/connectors/gitlab.svg",
            app_group="Development",
            app_description="GitLab OAuth application for agent integration"
        ),
        AuthBuilder.type(AuthType.API_TOKEN).fields([
            CommonFields.api_token("GitLab Personal Access Token", "glpat-your-token-here")
        ])
    ])\
    .with_tools(tools)\
    .configure(lambda builder: builder.with_icon("/assets/icons/connectors/gitlab.svg"))\
    .build_decorator()
class GitLab:
    """GitLab tools exposed to the agents using GitLabDataSource"""

    def __init__(self, client: GitLabClient) -> None:
        """Initialize the GitLab tool with a data source wrapper.
        Args:
            client: An initialized `GitLabClient` instance
        """
        self.client = GitLabDataSource(client)
        self._bg_loop = asyncio.new_event_loop()
        self._bg_loop_thread = threading.Thread(
            target=self._start_background_loop, daemon=True
        )
        self._bg_loop_thread.start()

    def _start_background_loop(self) -> None:
        """Start the background event loop."""
        asyncio.set_event_loop(self._bg_loop)
        self._bg_loop.run_forever()

    def _run_async(self, coro) -> GitLabResponse:
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
            logger.warning(f"GitLab shutdown encountered an issue: {exc}")

    def _handle_response(
        self, response: GitLabResponse, success_message: str
    ) -> Tuple[bool, str]:
        """Handle GitLabResponse and return standardized tuple."""
        if response.success:
            return True, json.dumps(
                {"message": success_message, "data": response.data or {}}
            )
        return False, json.dumps({"error": response.error or "Unknown error"})

    @tool(
        app_name="gitlab",
        tool_name="create_project",
        description="Create a new project in GitLab",
        parameters=[
            ToolParameter(
                name="name",
                type=ParameterType.STRING,
                description="The name of the project (required)",
            ),
            ToolParameter(
                name="description",
                type=ParameterType.STRING,
                description="A short description of the project",
                required=False,
            ),
            ToolParameter(
                name="visibility",
                type=ParameterType.STRING,
                description="The visibility level ('private', 'internal', 'public'). Defaults to 'private'.",
                required=False,
                default="private",
            ),
            ToolParameter(
                name="namespace_id",
                type=ParameterType.NUMBER,
                description="The ID of the namespace (group) to create the project in",
                required=False,
            ),
            ToolParameter(
                name="initialize_with_readme",
                type=ParameterType.BOOLEAN,
                description="Whether to initialize the project with a README. Defaults to False.",
                required=False,
                default=False,
            ),
        ],
        returns="JSON with the created project details",
    )
    def create_project(
        self,
        name: str,
        description: Optional[str] = None,
        visibility: str = "private",
        namespace_id: Optional[int] = None,
        initialize_with_readme: bool = False,
    ) -> Tuple[bool, str]:
        """Create a new project in GitLab."""
        try:
            response = self._run_async(
                self.client.create_project(
                    name=name,
                    description=description,
                    visibility=visibility,
                    namespace_id=namespace_id,
                    initialize_with_readme=initialize_with_readme,
                )
            )
            return self._handle_response(response, "Project created successfully")
        except Exception as e:
            logger.error(f"Error creating project: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="gitlab",
        tool_name="get_project",
        description="Get details of a specific project from GitLab",
        parameters=[
            ToolParameter(
                name="project_id",
                type=ParameterType.STRING,
                description="The ID or path of the project (required)",
            ),
        ],
        returns="JSON with project details",
    )
    def get_project(self, project_id: str) -> Tuple[bool, str]:
        """Get details of a specific project from GitLab."""
        try:
            response = self._run_async(self.client.get_project(project_id=project_id))
            return self._handle_response(response, "Project fetched successfully")
        except Exception as e:
            logger.error(f"Error getting project: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="gitlab",
        tool_name="update_project",
        description="Update an existing project in GitLab",
        parameters=[
            ToolParameter(
                name="project_id",
                type=ParameterType.STRING,
                description="The ID or path of the project (required)",
            ),
            ToolParameter(
                name="name",
                type=ParameterType.STRING,
                description="The new name of the project",
                required=False,
            ),
            ToolParameter(
                name="description",
                type=ParameterType.STRING,
                description="The new description of the project",
                required=False,
            ),
            ToolParameter(
                name="visibility",
                type=ParameterType.STRING,
                description="The new visibility level ('private', 'internal', 'public')",
                required=False,
            ),
        ],
        returns="JSON with updated project details",
    )
    def update_project(
        self,
        project_id: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        visibility: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """Update an existing project in GitLab."""
        try:
            response = self._run_async(
                self.client.update_project(
                    project_id=project_id,
                    name=name,
                    description=description,
                    visibility=visibility,
                )
            )
            return self._handle_response(response, "Project updated successfully")
        except Exception as e:
            logger.error(f"Error updating project: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="gitlab",
        tool_name="delete_project",
        description="Delete a project from GitLab",
        parameters=[
            ToolParameter(
                name="project_id",
                type=ParameterType.STRING,
                description="The ID or path of the project (required)",
            ),
        ],
        returns="JSON with success status",
    )
    def delete_project(self, project_id: str) -> Tuple[bool, str]:
        """Delete a project from GitLab."""
        try:
            response = self._run_async(self.client.delete_project(project_id=project_id))
            return self._handle_response(response, "Project deleted successfully")
        except Exception as e:
            logger.error(f"Error deleting project: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="gitlab",
        tool_name="create_issue",
        description="Create a new issue in a GitLab project",
        parameters=[
            ToolParameter(
                name="project_id",
                type=ParameterType.STRING,
                description="The ID or path of the project (required)",
            ),
            ToolParameter(
                name="title",
                type=ParameterType.STRING,
                description="The title of the issue (required)",
            ),
            ToolParameter(
                name="description",
                type=ParameterType.STRING,
                description="The description of the issue",
                required=False,
            ),
            ToolParameter(
                name="assignee_ids",
                type=ParameterType.ARRAY,
                description="A list of user IDs to assign to the issue",
                required=False,
            ),
            ToolParameter(
                name="labels",
                type=ParameterType.ARRAY,
                description="A list of label names to apply to the issue",
                required=False,
            ),
            ToolParameter(
                name="milestone_id",
                type=ParameterType.NUMBER,
                description="The ID of the milestone to assign to the issue",
                required=False,
            ),
        ],
        returns="JSON with the created issue details",
    )
    def create_issue(
        self,
        project_id: str,
        title: str,
        description: Optional[str] = None,
        assignee_ids: Optional[List[int]] = None,
        labels: Optional[List[str]] = None,
        milestone_id: Optional[int] = None,
    ) -> Tuple[bool, str]:
        """Create a new issue in a GitLab project."""
        try:
            response = self._run_async(
                self.client.create_issue(
                    project_id=project_id,
                    title=title,
                    description=description,
                    assignee_ids=assignee_ids,
                    labels=labels,
                    milestone_id=milestone_id,
                )
            )
            return self._handle_response(response, "Issue created successfully")
        except Exception as e:
            logger.error(f"Error creating issue: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="gitlab",
        tool_name="get_issue",
        description="Get details of a specific issue from a GitLab project",
        parameters=[
            ToolParameter(
                name="project_id",
                type=ParameterType.STRING,
                description="The ID or path of the project (required)",
            ),
            ToolParameter(
                name="issue_iid",
                type=ParameterType.NUMBER,
                description="The internal ID of the issue (required)",
            ),
        ],
        returns="JSON with issue details",
    )
    def get_issue(self, project_id: str, issue_iid: int) -> Tuple[bool, str]:
        """Get details of a specific issue from a GitLab project."""
        try:
            response = self._run_async(
                self.client.get_issue(project_id=project_id, issue_iid=issue_iid)
            )
            return self._handle_response(response, "Issue fetched successfully")
        except Exception as e:
            logger.error(f"Error getting issue: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="gitlab",
        tool_name="update_issue",
        description="Update an existing issue in a GitLab project",
        parameters=[
            ToolParameter(
                name="project_id",
                type=ParameterType.STRING,
                description="The ID or path of the project (required)",
            ),
            ToolParameter(
                name="issue_iid",
                type=ParameterType.NUMBER,
                description="The internal ID of the issue (required)",
            ),
            ToolParameter(
                name="title",
                type=ParameterType.STRING,
                description="The new title of the issue",
                required=False,
            ),
            ToolParameter(
                name="description",
                type=ParameterType.STRING,
                description="The new description of the issue",
                required=False,
            ),
            ToolParameter(
                name="assignee_ids",
                type=ParameterType.ARRAY,
                description="A list of user IDs to assign to the issue",
                required=False,
            ),
            ToolParameter(
                name="labels",
                type=ParameterType.ARRAY,
                description="A list of label names to apply to the issue",
                required=False,
            ),
            ToolParameter(
                name="state_event",
                type=ParameterType.STRING,
                description="The new state of the issue ('close' or 'reopen')",
                required=False,
            ),
        ],
        returns="JSON with updated issue details",
    )
    def update_issue(
        self,
        project_id: str,
        issue_iid: int,
        title: Optional[str] = None,
        description: Optional[str] = None,
        labels: Optional[List[str]] = None,
        state_event: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """Update an existing issue in a GitLab project."""
        try:
            response = self._run_async(
                self.client.update_issue(
                    project_id=project_id,
                    issue_iid=issue_iid,
                    title=title,
                    description=description,
                    labels=labels,
                    state_event=state_event,
                )
            )
            return self._handle_response(response, "Issue updated successfully")
        except Exception as e:
            logger.error(f"Error updating issue: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="gitlab",
        tool_name="delete_issue",
        description="Delete an issue from a GitLab project",
        parameters=[
            ToolParameter(
                name="project_id",
                type=ParameterType.STRING,
                description="The ID or path of the project (required)",
            ),
            ToolParameter(
                name="issue_iid",
                type=ParameterType.NUMBER,
                description="The internal ID of the issue (required)",
            ),
        ],
        returns="JSON with success status",
    )
    def delete_issue(self, project_id: str, issue_iid: int) -> Tuple[bool, str]:
        """Delete an issue from a GitLab project."""
        try:
            response = self._run_async(
                self.client.delete_issue(project_id=project_id, issue_iid=issue_iid)
            )
            return self._handle_response(response, "Issue deleted successfully")
        except Exception as e:
            logger.error(f"Error deleting issue: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="gitlab",
        tool_name="create_merge_request",
        description="Create a new merge request in a GitLab project",
        parameters=[
            ToolParameter(
                name="project_id",
                type=ParameterType.STRING,
                description="The ID or path of the project (required)",
            ),
            ToolParameter(
                name="title",
                type=ParameterType.STRING,
                description="The title of the merge request (required)",
            ),
            ToolParameter(
                name="source_branch",
                type=ParameterType.STRING,
                description="The source branch name (required)",
            ),
            ToolParameter(
                name="target_branch",
                type=ParameterType.STRING,
                description="The target branch name (required)",
            ),
            ToolParameter(
                name="description",
                type=ParameterType.STRING,
                description="The description of the merge request",
                required=False,
            ),
            ToolParameter(
                name="assignee_id",
                type=ParameterType.NUMBER,
                description="The ID of the user to assign the merge request to",
                required=False,
            ),
            ToolParameter(
                name="labels",
                type=ParameterType.ARRAY,
                description="A list of label names to apply to the merge request",
                required=False,
            ),
        ],
        returns="JSON with the created merge request details",
    )
    def create_merge_request(
        self,
        project_id: str,
        title: str,
        source_branch: str,
        target_branch: str,
        description: Optional[str] = None,
        assignee_id: Optional[int] = None,
        labels: Optional[List[str]] = None,
    ) -> Tuple[bool, str]:
        """Create a new merge request in a GitLab project."""
        try:
            response = self._run_async(
                self.client.create_merge_request(
                    project_id=project_id,
                    title=title,
                    source_branch=source_branch,
                    target_branch=target_branch,
                    description=description,
                    assignee_id=assignee_id,
                    labels=labels,
                )
            )
            return self._handle_response(response, "Merge request created successfully")
        except Exception as e:
            logger.error(f"Error creating merge request: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="gitlab",
        tool_name="get_merge_request",
        description="Get details of a specific merge request from a GitLab project",
        parameters=[
            ToolParameter(
                name="project_id",
                type=ParameterType.STRING,
                description="The ID or path of the project (required)",
            ),
            ToolParameter(
                name="merge_request_iid",
                type=ParameterType.NUMBER,
                description="The internal ID of the merge request (required)",
            ),
        ],
        returns="JSON with merge request details",
    )
    def get_merge_request(self, project_id: str, merge_request_iid: int) -> Tuple[bool, str]:
        """Get details of a specific merge request from a GitLab project."""
        try:
            response = self._run_async(
                self.client.get_merge_request(project_id=project_id, mr_iid=merge_request_iid)
            )
            return self._handle_response(response, "Merge request fetched successfully")
        except Exception as e:
            logger.error(f"Error getting merge request: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="gitlab",
        tool_name="merge_merge_request",
        description="Merge a merge request in a GitLab project",
        parameters=[
            ToolParameter(
                name="project_id",
                type=ParameterType.STRING,
                description="The ID or path of the project (required)",
            ),
            ToolParameter(
                name="merge_request_iid",
                type=ParameterType.NUMBER,
                description="The internal ID of the merge request (required)",
            ),
            ToolParameter(
                name="merge_when_pipeline_succeeds",
                type=ParameterType.BOOLEAN,
                description="Merge when pipeline succeeds",
                required=False,
            ),
            ToolParameter(
                name="squash",
                type=ParameterType.BOOLEAN,
                description="Squash commits on merge",
                required=False,
            ),
        ],
        returns="JSON with merge status",
    )
    def merge_merge_request(
        self,
        project_id: str,
        merge_request_iid: int,
        merge_when_pipeline_succeeds: Optional[bool] = None,
        squash: Optional[bool] = None,
    ) -> Tuple[bool, str]:
        """Merge a merge request in a GitLab project."""
        try:
            response = self._run_async(
                self.client.merge_merge_request(
                    project_id=project_id,
                    mr_iid=merge_request_iid,
                    merge_when_pipeline_succeeds=merge_when_pipeline_succeeds,
                    squash=squash,
                )
            )
            return self._handle_response(response, "Merge request merged successfully")
        except Exception as e:
            logger.error(f"Error merging merge request: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="gitlab",
        tool_name="search_projects",
        description="Search for projects in GitLab",
        parameters=[
            ToolParameter(
                name="query",
                type=ParameterType.STRING,
                description="The search query string (required)",
            ),
        ],
        returns="JSON with search results",
    )
    def search_projects(self, query: str) -> Tuple[bool, str]:
        """Search for projects in GitLab."""
        try:
            # Note: GitLabDataSource doesn't have a direct search method, so we'll use list_projects with search parameter
            response = self._run_async(
                self.client.list_projects(search=query)
            )
            return self._handle_response(response, "Project search completed successfully")
        except Exception as e:
            logger.error(f"Error searching projects: {e}")
            return False, json.dumps({"error": str(e)})
