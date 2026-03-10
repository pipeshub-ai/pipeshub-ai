import json
import logging
from typing import List, Optional, Tuple

from app.agents.actions.utils import run_async
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
from app.sources.client.linear.linear import LinearClient
from app.sources.external.linear.linear import LinearDataSource

logger = logging.getLogger(__name__)

# Define tools
tools: List[ToolDefinition] = [
    ToolDefinition(
        name="get_viewer",
        description="Get current user information",
        parameters=[],
        tags=["users", "info"]
    ),
    ToolDefinition(
        name="get_user",
        description="Get user information",
        parameters=[
            {"name": "user_id", "type": "string", "description": "User ID", "required": True}
        ],
        tags=["users", "read"]
    ),
    ToolDefinition(
        name="get_teams",
        description="Get all teams",
        parameters=[],
        tags=["teams", "list"]
    ),
    ToolDefinition(
        name="get_team",
        description="Get team details",
        parameters=[
            {"name": "team_id", "type": "string", "description": "Team ID", "required": True}
        ],
        tags=["teams", "read"]
    ),
    ToolDefinition(
        name="get_issues",
        description="Get issues",
        parameters=[
            {"name": "team_id", "type": "string", "description": "Team ID", "required": False}
        ],
        tags=["issues", "list"]
    ),
    ToolDefinition(
        name="get_issue",
        description="Get issue details",
        parameters=[
            {"name": "issue_id", "type": "string", "description": "Issue ID", "required": True}
        ],
        tags=["issues", "read"]
    ),
    ToolDefinition(
        name="create_issue",
        description="Create a new issue",
        parameters=[
            {"name": "team_id", "type": "string", "description": "Team ID", "required": True},
            {"name": "title", "type": "string", "description": "Issue title", "required": True}
        ],
        tags=["issues", "create"]
    ),
    ToolDefinition(
        name="update_issue",
        description="Update an issue",
        parameters=[
            {"name": "issue_id", "type": "string", "description": "Issue ID", "required": True}
        ],
        tags=["issues", "update"]
    ),
    ToolDefinition(
        name="delete_issue",
        description="Delete an issue",
        parameters=[
            {"name": "issue_id", "type": "string", "description": "Issue ID", "required": True}
        ],
        tags=["issues", "delete"]
    ),
]


# Register Linear toolset
@ToolsetBuilder("Linear")\
    .in_group("Project Management")\
    .with_description("Linear integration for issue tracking and project management")\
    .with_category(ToolCategory.APP)\
    .with_auth([
        AuthBuilder.type(AuthType.OAUTH).oauth(
            connector_name="Linear",
            authorize_url="https://linear.app/oauth/authorize",
            token_url="https://api.linear.app/oauth/token",
            redirect_uri="toolsets/oauth/callback/linear",
            scopes=OAuthScopeConfig(
                personal_sync=[],
                team_sync=[],
                agent=[
                    "read",
                    "write"
                ]
            ),
            fields=[
                CommonFields.client_id("Linear OAuth App"),
                CommonFields.client_secret("Linear OAuth App")
            ],
            icon_path="/assets/icons/connectors/linear.svg",
            app_group="Project Management",
            app_description="Linear OAuth application for agent integration"
        ),
        AuthBuilder.type(AuthType.API_TOKEN).fields([
            CommonFields.api_token("Linear API Key", "lin_api_your-key-here")
        ])
    ])\
    .with_tools(tools)\
    .configure(lambda builder: builder.with_icon("/assets/icons/connectors/linear.svg"))\
    .build_decorator()
class Linear:
    """Linear tool exposed to the agents"""
    def __init__(self, client: LinearClient) -> None:
        """Initialize the Linear tool"""
        """
        Args:
            client: Linear client object
        Returns:
            None
        """
        self.client = LinearDataSource(client)

    @tool(
        app_name="linear",
        tool_name="get_viewer",
        description="Get current user information",
        parameters=[]
    )
    def get_viewer(self) -> Tuple[bool, str]:
        """Get current user information"""
        """
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use LinearDataSource method
            response = run_async(self.client.viewer())

            if response.success:
                return True, json.dumps({"data": response.data})
            else:
                return False, json.dumps({"error": response.message})
        except Exception as e:
            logger.error(f"Error getting viewer: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="linear",
        tool_name="get_user",
        description="Get user by ID",
        parameters=[
            ToolParameter(
                name="user_id",
                type=ParameterType.STRING,
                description="The ID of the user to get",
                required=True
            )
        ]
    )
    def get_user(self, user_id: str) -> Tuple[bool, str]:
        """Get user by ID"""
        """
        Args:
            user_id: The ID of the user to get
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use LinearDataSource method
            response = run_async(self.client.user(id=user_id))

            if response.success:
                return True, json.dumps({"data": response.data})
            else:
                return False, json.dumps({"error": response.message})
        except Exception as e:
            logger.error(f"Error getting user: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="linear",
        tool_name="get_teams",
        description="Get teams",
        parameters=[
            ToolParameter(
                name="first",
                type=ParameterType.INTEGER,
                description="Number of teams to return",
                required=False
            ),
            ToolParameter(
                name="after",
                type=ParameterType.STRING,
                description="Cursor for pagination",
                required=False
            )
        ]
    )
    def get_teams(
        self,
        first: Optional[int] = None,
        after: Optional[str] = None
    ) -> Tuple[bool, str]:
        """Get teams"""
        """
        Args:
            first: Number of teams to return
            after: Cursor for pagination
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use LinearDataSource method
            response = run_async(self.client.teams(first=first, after=after))

            if response.success:
                return True, json.dumps({"data": response.data})
            else:
                return False, json.dumps({"error": response.message})
        except Exception as e:
            logger.error(f"Error getting teams: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="linear",
        tool_name="get_team",
        description="Get team by ID",
        parameters=[
            ToolParameter(
                name="team_id",
                type=ParameterType.STRING,
                description="The ID of the team to get",
                required=True
            )
        ]
    )
    def get_team(self, team_id: str) -> Tuple[bool, str]:
        """Get team by ID"""
        """
        Args:
            team_id: The ID of the team to get
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use LinearDataSource method
            response = run_async(self.client.team(id=team_id))

            if response.success:
                return True, json.dumps({"data": response.data})
            else:
                return False, json.dumps({"error": response.message})
        except Exception as e:
            logger.error(f"Error getting team: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="linear",
        tool_name="get_issues",
        description="Get issues",
        parameters=[
            ToolParameter(
                name="first",
                type=ParameterType.INTEGER,
                description="Number of issues to return",
                required=False
            ),
            ToolParameter(
                name="after",
                type=ParameterType.STRING,
                description="Cursor for pagination",
                required=False
            ),
            ToolParameter(
                name="filter",
                type=ParameterType.STRING,
                description="Filter for issues",
                required=False
            )
        ]
    )
    def get_issues(
        self,
        first: Optional[int] = None,
        after: Optional[str] = None,
        filter: Optional[str] = None
    ) -> Tuple[bool, str]:
        """Get issues"""
        """
        Args:
            first: Number of issues to return
            after: Cursor for pagination
            filter: Filter for issues
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Convert string filter (if provided) to dict expected by data source
            filter_dict = None
            if filter:
                try:
                    parsed = json.loads(filter)
                    if isinstance(parsed, dict):
                        filter_dict = parsed
                except Exception:
                    filter_dict = None

            # Use LinearDataSource method
            response = run_async(self.client.issues(first=first, after=after, filter=filter_dict))

            if response.success:
                return True, json.dumps({"data": response.data})
            else:
                return False, json.dumps({"error": response.message})
        except Exception as e:
            logger.error(f"Error getting issues: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="linear",
        tool_name="get_issue",
        description="Get issue by ID",
        parameters=[
            ToolParameter(
                name="issue_id",
                type=ParameterType.STRING,
                description="The ID of the issue to get",
                required=True
            )
        ]
    )
    def get_issue(self, issue_id: str) -> Tuple[bool, str]:
        """Get issue by ID"""
        """
        Args:
            issue_id: The ID of the issue to get
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use LinearDataSource method
            response = run_async(self.client.issue(id=issue_id))

            if response.success:
                return True, json.dumps({"data": response.data})
            else:
                return False, json.dumps({"error": response.message})
        except Exception as e:
            logger.error(f"Error getting issue: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="linear",
        tool_name="create_issue",
        description="Create a new issue",
        parameters=[
            ToolParameter(
                name="team_id",
                type=ParameterType.STRING,
                description="The ID of the team",
                required=True
            ),
            ToolParameter(
                name="title",
                type=ParameterType.STRING,
                description="Title of the issue",
                required=True
            ),
            ToolParameter(
                name="description",
                type=ParameterType.STRING,
                description="Description of the issue",
                required=False
            ),
            ToolParameter(
                name="state_id",
                type=ParameterType.STRING,
                description="ID of the state",
                required=False
            ),
            ToolParameter(
                name="assignee_id",
                type=ParameterType.STRING,
                description="ID of the assignee",
                required=False
            ),
            ToolParameter(
                name="priority",
                type=ParameterType.INTEGER,
                description="Priority of the issue (0=No priority, 1=Urgent, 2=High, 3=Medium, 4=Low)",
                required=False
            )
        ]
    )
    def create_issue(
        self,
        team_id: str,
        title: str,
        description: Optional[str] = None,
        state_id: Optional[str] = None,
        assignee_id: Optional[str] = None,
        priority: Optional[int] = None
    ) -> Tuple[bool, str]:
        """Create a new issue"""
        """
        Args:
            team_id: The ID of the team
            title: Title of the issue
            description: Description of the issue
            state_id: ID of the state
            assignee_id: ID of the assignee
            priority: Priority of the issue (0=No priority, 1=Urgent, 2=High, 3=Medium, 4=Low)
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Build GraphQL input for issueCreate
            issue_input = {"title": title, "teamId": team_id}
            if description is not None:
                issue_input["description"] = description
            if state_id is not None:
                issue_input["stateId"] = state_id
            if assignee_id is not None:
                issue_input["assigneeId"] = assignee_id
            if priority is not None:
                issue_input["priority"] = priority

            # Call the correct LinearDataSource method
            response = run_async(self.client.issueCreate(input=issue_input))

            if response.success:
                return True, json.dumps({"data": response.data})
            else:
                return False, json.dumps({"error": response.message})
        except Exception as e:
            logger.error(f"Error creating issue: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="linear",
        tool_name="update_issue",
        description="Update an issue",
        parameters=[
            ToolParameter(
                name="issue_id",
                type=ParameterType.STRING,
                description="The ID of the issue to update",
                required=True
            ),
            ToolParameter(
                name="title",
                type=ParameterType.STRING,
                description="New title of the issue",
                required=False
            ),
            ToolParameter(
                name="description",
                type=ParameterType.STRING,
                description="New description of the issue",
                required=False
            ),
            ToolParameter(
                name="state_id",
                type=ParameterType.STRING,
                description="New state ID",
                required=False
            ),
            ToolParameter(
                name="assignee_id",
                type=ParameterType.STRING,
                description="New assignee ID",
                required=False
            )
        ]
    )
    def update_issue(
        self,
        issue_id: str,
        title: Optional[str] = None,
        description: Optional[str] = None,
        state_id: Optional[str] = None,
        assignee_id: Optional[str] = None
    ) -> Tuple[bool, str]:
        """Update an issue"""
        """
        Args:
            issue_id: The ID of the issue to update
            title: New title of the issue
            description: New description of the issue
            state_id: New state ID
            assignee_id: New assignee ID
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Build GraphQL input for issueUpdate
            update_input = {}
            if title is not None:
                update_input["title"] = title
            if description is not None:
                update_input["description"] = description
            if state_id is not None:
                update_input["stateId"] = state_id
            if assignee_id is not None:
                update_input["assigneeId"] = assignee_id

            # Call the correct LinearDataSource method
            response = run_async(self.client.issueUpdate(id=issue_id, input=update_input))

            if response.success:
                return True, json.dumps({"data": response.data})
            else:
                return False, json.dumps({"error": response.message})
        except Exception as e:
            logger.error(f"Error updating issue: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="linear",
        tool_name="delete_issue",
        description="Delete an issue",
        parameters=[
            ToolParameter(
                name="issue_id",
                type=ParameterType.STRING,
                description="The ID of the issue to delete",
                required=True
            )
        ]
    )
    def delete_issue(self, issue_id: str) -> Tuple[bool, str]:
        """Delete an issue"""
        """
        Args:
            issue_id: The ID of the issue to delete
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use LinearDataSource method with correct name
            response = run_async(self.client.issueDelete(id=issue_id))

            if response.success:
                return True, json.dumps({"data": response.data})
            else:
                return False, json.dumps({"error": response.message})
        except Exception as e:
            logger.error(f"Error deleting issue: {e}")
            return False, json.dumps({"error": str(e)})
