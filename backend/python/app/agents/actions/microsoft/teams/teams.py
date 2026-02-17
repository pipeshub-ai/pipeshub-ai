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
from app.sources.client.microsoft.microsoft import MSGraphClient
from app.sources.external.microsoft.teams.teams import TeamsDataSource

logger = logging.getLogger(__name__)

# Define tools
tools: List[ToolDefinition] = [
    ToolDefinition(
        name="send_message",
        description="Send a message to a Teams channel",
        parameters=[
            {"name": "team_id", "type": "string", "description": "Team ID", "required": True},
            {"name": "channel_id", "type": "string", "description": "Channel ID", "required": True},
            {"name": "message", "type": "string", "description": "Message content", "required": True}
        ],
        tags=["messaging", "send"]
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
        name="get_channels",
        description="Get channels in a team",
        parameters=[
            {"name": "team_id", "type": "string", "description": "Team ID", "required": True}
        ],
        tags=["channels", "list"]
    ),
    ToolDefinition(
        name="get_channel_messages",
        description="Get messages from a channel",
        parameters=[
            {"name": "team_id", "type": "string", "description": "Team ID", "required": True},
            {"name": "channel_id", "type": "string", "description": "Channel ID", "required": True}
        ],
        tags=["messages", "list"]
    ),
    ToolDefinition(
        name="create_team",
        description="Create a new team",
        parameters=[
            {"name": "display_name", "type": "string", "description": "Team name", "required": True}
        ],
        tags=["teams", "create"]
    ),
    ToolDefinition(
        name="add_member",
        description="Add a member to a team",
        parameters=[
            {"name": "team_id", "type": "string", "description": "Team ID", "required": True},
            {"name": "user_id", "type": "string", "description": "User ID", "required": True}
        ],
        tags=["teams", "members"]
    ),
]


# Register Microsoft Teams toolset
@ToolsetBuilder("Microsoft Teams")\
    .in_group("Microsoft 365")\
    .with_description("Microsoft Teams integration for messaging and collaboration")\
    .with_category(ToolCategory.APP)\
    .with_auth([
        AuthBuilder.type(AuthType.OAUTH).oauth(
            connector_name="Teams",
            authorize_url="https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
            token_url="https://login.microsoftonline.com/common/oauth2/v2.0/token",
            redirect_uri="toolsets/oauth/callback/teams",
            scopes=OAuthScopeConfig(
                personal_sync=[],
                team_sync=[],
                agent=[
                    "Chat.ReadWrite",
                    "ChannelMessage.ReadWrite",
                    "Team.ReadBasic.All"
                ]
            ),
            fields=[
                CommonFields.client_id("Azure App Registration"),
                CommonFields.client_secret("Azure App Registration")
            ],
            icon_path="/assets/icons/connectors/teams.svg",
            app_group="Microsoft 365",
            app_description="Microsoft Teams OAuth application for agent integration"
        )
    ])\
    .with_tools(tools)\
    .configure(lambda builder: builder.with_icon("/assets/icons/connectors/teams.svg"))\
    .build_decorator()
class Teams:
    """Microsoft Teams tool exposed to the agents"""
    def __init__(self, client: MSGraphClient) -> None:
        """Initialize the Teams tool"""
        """
        Args:
            client: Microsoft Graph client object
        Returns:
            None
        """
        self.client = TeamsDataSource(client)

    @tool(
        app_name="teams",
        tool_name="send_message",
        description="Send a message to a Microsoft Teams channel",
        parameters=[
            ToolParameter(
                name="team_id",
                type=ParameterType.STRING,
                description="ID of the team",
                required=True
            ),
            ToolParameter(
                name="channel_id",
                type=ParameterType.STRING,
                description="ID of the channel",
                required=True
            ),
            ToolParameter(
                name="message",
                type=ParameterType.STRING,
                description="Message content",
                required=True
            )
        ]
    )
    def send_message(
        self,
        team_id: str,
        channel_id: str,
        message: str
    ) -> Tuple[bool, str]:
        """Send a message to a Microsoft Teams channel"""
        """
        Args:
            team_id: ID of the team
            channel_id: ID of the channel
            message: Message content
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Note: Microsoft Teams Graph API doesn't have a simple "send_message" method
            # The API requires using specific endpoints for channel messages, chat messages, etc.
            # This would need to be implemented using the Microsoft Graph SDK's chat or channel message endpoints
            # For now, this is a placeholder that shows the required approach
            logger.error("Direct message sending not implemented - Microsoft Teams API requires specific message endpoints")
            return False, json.dumps({
                "error": "Direct message sending not implemented",
                "details": "Microsoft Teams API requires using specific endpoints like chats or channel messages",
                "suggestion": "Use Microsoft Graph SDK's chat or channel message endpoints directly"
            })
        except Exception as e:
            logger.error(f"Error in send_message: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="teams",
        tool_name="get_teams",
        description="Get Microsoft Teams",
        parameters=[
            ToolParameter(
                name="top",
                type=ParameterType.INTEGER,
                description="Number of teams to retrieve",
                required=False
            )
        ]
    )
    def get_teams(self, top: Optional[int] = None) -> Tuple[bool, str]:
        """Get Microsoft Teams"""
        """
        Args:
            top: Number of teams to retrieve
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Note: Microsoft Teams Graph API doesn't have a simple list all teams method
            # Available methods require specific team IDs (me_get_joined_teams) or are for creation
            # To list teams, you would need to use Microsoft Graph SDK's teams endpoint directly
            # The TeamsDataSource doesn't expose a generic list method
            logger.error("Listing all teams not implemented - Microsoft Teams API requires specific team access patterns")
            return False, json.dumps({
                "error": "Listing all teams not implemented",
                "details": "Microsoft Teams API requires specific team IDs or uses Microsoft Graph SDK directly",
                "available_methods": ["me_get_joined_teams(team_id)", "me_create_joined_teams()"],
                "suggestion": "Use Microsoft Graph SDK's /me/joinedTeams endpoint directly"
            })
        except Exception as e:
            logger.error(f"Error in get_teams: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="teams",
        tool_name="get_team",
        description="Get a specific Microsoft Team",
        parameters=[
            ToolParameter(
                name="team_id",
                type=ParameterType.STRING,
                description="ID of the team",
                required=True
            )
        ]
    )
    def get_team(self, team_id: str) -> Tuple[bool, str]:
        """Get a specific Microsoft Team"""
        """
        Args:
            team_id: ID of the team
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use TeamsDataSource method
            response = run_async(self.client.teams_team_get_team(
                team_id=team_id
            ))

            if response.success:
                return True, response.to_json()
            else:
                return False, response.to_json()
        except Exception as e:
            logger.error(f"Error in get_team: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="teams",
        tool_name="get_channels",
        description="Get channels for a Microsoft Team",
        parameters=[
            ToolParameter(
                name="team_id",
                type=ParameterType.STRING,
                description="ID of the team",
                required=True
            )
        ]
    )
    def get_channels(self, team_id: str) -> Tuple[bool, str]:
        """Get channels for a Microsoft Team"""
        """
        Args:
            team_id: ID of the team
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Note: Microsoft Teams Graph API doesn't have a simple list channels method
            # Available methods require specific channel IDs or are for specific operations
            # The TeamsDataSource has methods like teams_channels_get_files_folder, teams_channels_get_members
            # but these require both team_id and channel_id
            logger.error("Listing all channels not implemented - Microsoft Teams API requires specific channel IDs")
            return False, json.dumps({
                "error": "Listing all channels not implemented",
                "details": "Microsoft Teams API requires specific channel IDs for channel operations",
                "available_methods": ["teams_channels_get_files_folder(team_id, channel_id)", "teams_channels_get_members(team_id, channel_id)"],
                "suggestion": "Use Microsoft Graph SDK's /teams/{team-id}/channels endpoint directly"
            })
        except Exception as e:
            logger.error(f"Error in get_channels: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="teams",
        tool_name="get_channel_messages",
        description="Get messages from a Microsoft Teams channel",
        parameters=[
            ToolParameter(
                name="team_id",
                type=ParameterType.STRING,
                description="ID of the team",
                required=True
            ),
            ToolParameter(
                name="channel_id",
                type=ParameterType.STRING,
                description="ID of the channel",
                required=True
            ),
            ToolParameter(
                name="top",
                type=ParameterType.INTEGER,
                description="Number of messages to retrieve",
                required=False
            )
        ]
    )
    def get_channel_messages(
        self,
        team_id: str,
        channel_id: str,
        top: Optional[int] = None
    ) -> Tuple[bool, str]:
        """Get messages from a Microsoft Teams channel"""
        """
        Args:
            team_id: ID of the team
            channel_id: ID of the channel
            top: Number of messages to retrieve
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Note: Microsoft Teams Graph API doesn't have simple channel message retrieval methods
            # The TeamsDataSource doesn't expose channel message operations directly
            # Channel messages require using Microsoft Graph SDK's channel messages endpoints
            logger.error("Getting channel messages not implemented - Microsoft Teams API requires Graph SDK message endpoints")
            return False, json.dumps({
                "error": "Getting channel messages not implemented",
                "details": "Microsoft Teams API requires Graph SDK for channel message operations",
                "suggestion": "Use Microsoft Graph SDK's /teams/{team-id}/channels/{channel-id}/messages endpoint directly"
            })
        except Exception as e:
            logger.error(f"Error in get_channel_messages: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="teams",
        tool_name="create_team",
        description="Create a new Microsoft Team",
        parameters=[
            ToolParameter(
                name="display_name",
                type=ParameterType.STRING,
                description="Display name of the team",
                required=True
            ),
            ToolParameter(
                name="description",
                type=ParameterType.STRING,
                description="Description of the team",
                required=False
            )
        ]
    )
    def create_team(
        self,
        display_name: str,
        description: Optional[str] = None
    ) -> Tuple[bool, str]:
        """Create a new Microsoft Team"""
        """
        Args:
            display_name: Display name of the team
            description: Description of the team
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Note: Microsoft Teams Graph API doesn't have a simple create_team method in TeamsDataSource
            # Team creation in Microsoft Graph requires creating a group first, then enabling Teams
            # The TeamsDataSource has me_create_joined_teams but this is for joining existing teams
            logger.error("Creating teams not implemented - Microsoft Teams API requires complex group and team setup")
            return False, json.dumps({
                "error": "Creating teams not implemented",
                "details": "Microsoft Teams creation requires group creation followed by team enablement",
                "available_methods": ["me_create_joined_teams()"],
                "suggestion": "Use Microsoft Graph SDK's groups endpoint to create group, then enable Teams"
            })
        except Exception as e:
            logger.error(f"Error in create_team: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="teams",
        tool_name="add_member",
        description="Add a member to a Microsoft Team",
        parameters=[
            ToolParameter(
                name="team_id",
                type=ParameterType.STRING,
                description="ID of the team",
                required=True
            ),
            ToolParameter(
                name="user_id",
                type=ParameterType.STRING,
                description="ID of the user to add",
                required=True
            ),
            ToolParameter(
                name="role",
                type=ParameterType.STRING,
                description="Role of the member (owner or member)",
                required=False
            )
        ]
    )
    def add_member(
        self,
        team_id: str,
        user_id: str,
        role: Optional[str] = None
    ) -> Tuple[bool, str]:
        """Add a member to a Microsoft Team"""
        """
        Args:
            team_id: ID of the team
            user_id: ID of the user to add
            role: Role of the member (owner or member)
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Note: Microsoft Teams Graph API doesn't have a simple add_member method in TeamsDataSource
            # Member management requires using specific endpoints and member objects
            # Available methods in TeamsDataSource are for chat members or specific operations
            logger.error("Adding team members not implemented - Microsoft Teams API requires specific member management")
            return False, json.dumps({
                "error": "Adding team members not implemented",
                "details": "Microsoft Teams API requires specific member management operations",
                "available_methods": ["teams_channels_get_members(team_id, channel_id, member_id)"],
                "suggestion": "Use Microsoft Graph SDK's /teams/{team-id}/members endpoint directly"
            })
        except Exception as e:
            logger.error(f"Error in add_member: {e}")
            return False, json.dumps({"error": str(e)})
