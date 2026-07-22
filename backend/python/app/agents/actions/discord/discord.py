import asyncio
import json
import logging
import threading
from typing import Optional, Tuple, List

from app.agent_loop_lib.tools.base import ParameterType, Tag, ToolParameter
from app.agent_loop_lib.tools.decorators import tool
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
from app.sources.client.discord.discord import DiscordClient, DiscordResponse
from app.sources.external.discord.discord import DiscordDataSource

logger = logging.getLogger(__name__)

# Define tools
tools: List[ToolDefinition] = [
    ToolDefinition(
        name="send_message",
        description="Send a message to a Discord channel",
        parameters=[
            {"name": "channel_id", "type": "string", "description": "Channel ID", "required": True},
            {"name": "content", "type": "string", "description": "Message content", "required": True}
        ],
        tags=["messaging", "send"]
    ),
    ToolDefinition(
        name="get_channel",
        description="Get channel details",
        parameters=[
            {"name": "channel_id", "type": "string", "description": "Channel ID", "required": True}
        ],
        tags=["channels", "read"]
    ),
    ToolDefinition(
        name="create_channel",
        description="Create a new channel",
        parameters=[
            {"name": "guild_id", "type": "string", "description": "Guild ID", "required": True},
            {"name": "name", "type": "string", "description": "Channel name", "required": True}
        ],
        tags=["channels", "create"]
    ),
    ToolDefinition(
        name="delete_channel",
        description="Delete a channel",
        parameters=[
            {"name": "channel_id", "type": "string", "description": "Channel ID", "required": True}
        ],
        tags=["channels", "delete"]
    ),
    ToolDefinition(
        name="get_messages",
        description="Get messages from a channel",
        parameters=[
            {"name": "channel_id", "type": "string", "description": "Channel ID", "required": True},
            {"name": "limit", "type": "integer", "description": "Max messages", "required": False}
        ],
        tags=["messages", "list"]
    ),
    ToolDefinition(
        name="get_guilds",
        description="Get all guilds (servers)",
        parameters=[],
        tags=["guilds", "list"]
    ),
    ToolDefinition(
        name="get_guild_channels",
        description="Get channels in a guild",
        parameters=[
            {"name": "guild_id", "type": "string", "description": "Guild ID", "required": True}
        ],
        tags=["channels", "list"]
    ),
    ToolDefinition(
        name="send_direct_message",
        description="Send a direct message",
        parameters=[
            {"name": "user_id", "type": "string", "description": "User ID", "required": True},
            {"name": "content", "type": "string", "description": "Message content", "required": True}
        ],
        tags=["messaging", "dm"]
    ),
    ToolDefinition(
        name="get_guild_members",
        description="Get members in a guild",
        parameters=[
            {"name": "guild_id", "type": "string", "description": "Guild ID", "required": True}
        ],
        tags=["members", "list"]
    ),
]


# Register Discord toolset
@ToolsetBuilder("Discord")\
    .in_group("Communication")\
    .with_description("Discord integration for messaging and server management")\
    .with_category(ToolCategory.APP)\
    .with_auth([
        AuthBuilder.type(AuthType.OAUTH).oauth(
            connector_name="Discord",
            authorize_url="https://discord.com/api/oauth2/authorize",
            token_url="https://discord.com/api/oauth2/token",
            redirect_uri="toolsets/oauth/callback/discord",
            scopes=OAuthScopeConfig(
                personal_sync=[],
                team_sync=[],
                agent=[
                    "bot",
                    "messages.read",
                    "messages.write",
                    "channels.read",
                    "guilds.read"
                ]
            ),
            fields=[
                CommonFields.client_id("Discord Developer Portal"),
                CommonFields.client_secret("Discord Developer Portal")
            ],
            icon_path="/assets/icons/connectors/discord.svg",
            app_group="Communication",
            app_description="Discord OAuth application for agent integration"
        ),
        AuthBuilder.type(AuthType.API_TOKEN).fields([
            CommonFields.api_token("Discord Bot Token", "your-bot-token")
        ])
    ])\
    .with_tools(tools)\
    .configure(lambda builder: builder.with_icon("/assets/icons/connectors/discord.svg"))\
    .build_decorator()
class Discord:
    """Discord tools exposed to the agents using DiscordDataSource"""

    def __init__(self, client: DiscordClient) -> None:
        """Initialize the Discord tool with a data source wrapper.
        Args:
            client: An initialized `DiscordClient` instance
        """
        self.client = DiscordDataSource(client)
        self._bg_loop = asyncio.new_event_loop()
        self._bg_loop_thread = threading.Thread(
            target=self._start_background_loop,
            daemon=True
        )
        self._bg_loop_thread.start()

    def _start_background_loop(self) -> None:
        """Start the background event loop."""
        asyncio.set_event_loop(self._bg_loop)
        self._bg_loop.run_forever()

    def _run_async(self, coro) -> DiscordResponse:
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
            logger.warning(f"Discord shutdown encountered an issue: {exc}")

    def _handle_response(
        self,
        response: DiscordResponse,
        success_message: str
    ) -> Tuple[bool, str]:
        """Handle DiscordResponse and return standardized tuple."""
        if response.success:
            return True, json.dumps({
                "message": success_message,
                "data": response.data or {}
            })
        return False, json.dumps({
            "error": response.error or "Unknown error"
        })

    @tool(
        path="/tools/discord/send_message",
        short_description="Send a message to a Discord text channel",
        description="Send a message to a Discord text channel by channel ID.",
        parameters=[
            ToolParameter(
                name="channel_id",
                type=ParameterType.INTEGER,
                description="The ID of the channel to send the message to (required)"
            ),
            ToolParameter(
                name="content",
                type=ParameterType.STRING,
                description="The content of the message to send (required)"
            ),
        ],
        tags=[Tag(key="category", value="communication"), Tag(key="type", value="create")],
    )
    async def send_message(self, channel_id: int, content: str) -> Tuple[bool, str]:
        try:
            response = self._run_async(
                self.client.send_message(channel_id=channel_id, content=content)
            )
            return self._handle_response(response, "Message sent successfully")
        except Exception as e:
            logger.error(f"Error sending message: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        path="/tools/discord/get_channel",
        short_description="Get information about a Discord channel",
        description="Get details about a Discord channel by its ID.",
        parameters=[
            ToolParameter(
                name="channel_id",
                type=ParameterType.INTEGER,
                description="The ID of the channel to retrieve (required)"
            ),
        ],
        tags=[Tag(key="category", value="communication"), Tag(key="type", value="read")],
    )
    async def get_channel(self, channel_id: int) -> Tuple[bool, str]:
        try:
            response = self._run_async(self.client.get_channel(channel_id=channel_id))
            return self._handle_response(response, "Channel information retrieved successfully")
        except Exception as e:
            logger.error(f"Error getting channel: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        path="/tools/discord/create_channel",
        short_description="Create a new channel in a Discord guild",
        description="Create a new channel in a Discord guild with an optional channel type.",
        parameters=[
            ToolParameter(
                name="guild_id",
                type=ParameterType.INTEGER,
                description="The ID of the guild to create the channel in (required)"
            ),
            ToolParameter(
                name="name",
                type=ParameterType.STRING,
                description="The name of the channel to create (required)"
            ),
            ToolParameter(
                name="channel_type",
                type=ParameterType.STRING,
                description="The type of channel (text, voice, category)",
                required=False,
            ),
        ],
        tags=[Tag(key="category", value="communication"), Tag(key="type", value="create")],
    )
    async def create_channel(
        self,
        guild_id: int,
        name: str,
        channel_type: Optional[str] = None
    ) -> Tuple[bool, str]:
        try:
            response = self._run_async(
                self.client.create_channel(
                    guild_id=guild_id,
                    name=name,
                    channel_type=channel_type
                )
            )
            return self._handle_response(response, "Channel created successfully")
        except Exception as e:
            logger.error(f"Error creating channel: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        path="/tools/discord/delete_channel",
        short_description="Delete a Discord channel",
        description="Delete a Discord channel by its ID.",
        parameters=[
            ToolParameter(
                name="channel_id",
                type=ParameterType.INTEGER,
                description="The ID of the channel to delete (required)"
            ),
        ],
        tags=[Tag(key="category", value="communication"), Tag(key="type", value="delete")],
    )
    async def delete_channel(self, channel_id: int) -> Tuple[bool, str]:
        try:
            response = self._run_async(self.client.delete_channel(channel_id=channel_id))
            return self._handle_response(response, "Channel deleted successfully")
        except Exception as e:
            logger.error(f"Error deleting channel: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        path="/tools/discord/get_messages",
        short_description="Get messages from a Discord text channel",
        description="Get messages from a Discord text channel with optional pagination by message ID.",
        parameters=[
            ToolParameter(
                name="channel_id",
                type=ParameterType.INTEGER,
                description="The ID of the channel to get messages from (required)"
            ),
            ToolParameter(
                name="limit",
                type=ParameterType.INTEGER,
                description="Maximum number of messages to retrieve (default: 100, max: 100)",
                required=False,
            ),
            ToolParameter(
                name="before",
                type=ParameterType.INTEGER,
                description="Message ID to fetch messages before this ID",
                required=False,
            ),
            ToolParameter(
                name="after",
                type=ParameterType.INTEGER,
                description="Message ID to fetch messages after this ID",
                required=False,
            ),
        ],
        tags=[Tag(key="category", value="communication"), Tag(key="type", value="read")],
    )
    async def get_messages(
        self,
        channel_id: int,
        limit: Optional[int] = None,
        before: Optional[int] = None,
        after: Optional[int] = None
    ) -> Tuple[bool, str]:
        try:
            response = self._run_async(
                self.client.get_messages(
                    channel_id=channel_id,
                    limit=limit,
                    before=before,
                    after=after
                )
            )
            return self._handle_response(response, "Messages retrieved successfully")
        except Exception as e:
            logger.error(f"Error getting messages: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        path="/tools/discord/get_guilds",
        short_description="Get all guilds (servers) the bot has access to",
        description="Get all guilds (servers) that the bot currently has access to.",
        parameters=[],
        tags=[Tag(key="category", value="communication"), Tag(key="type", value="read")],
    )
    async def get_guilds(self) -> Tuple[bool, str]:
        try:
            response = self._run_async(self.client.get_guilds())
            return self._handle_response(response, "Guilds retrieved successfully")
        except Exception as e:
            logger.error(f"Error getting guilds: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        path="/tools/discord/get_guild_channels",
        short_description="Get all channels in a Discord guild",
        description="Get all channels in a Discord guild, optionally filtered by channel type.",
        parameters=[
            ToolParameter(
                name="guild_id",
                type=ParameterType.INTEGER,
                description="The ID of the guild to get channels from (required)"
            ),
            ToolParameter(
                name="channel_type",
                type=ParameterType.STRING,
                description="Filter by channel type (text, voice, category)",
                required=False,
            ),
        ],
        tags=[Tag(key="category", value="communication"), Tag(key="type", value="read")],
    )
    async def get_guild_channels(
        self,
        guild_id: int,
        channel_type: Optional[str] = None
    ) -> Tuple[bool, str]:
        try:
            response = self._run_async(
                self.client.get_channels(
                    guild_id=guild_id,
                    channel_type=channel_type
                )
            )
            return self._handle_response(response, "Guild channels retrieved successfully")
        except Exception as e:
            logger.error(f"Error getting guild channels: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        path="/tools/discord/send_direct_message",
        short_description="Send a direct message to a Discord user",
        description="Send a direct message to a Discord user by their user ID.",
        parameters=[
            ToolParameter(
                name="user_id",
                type=ParameterType.INTEGER,
                description="The ID of the user to send a DM to (required)"
            ),
            ToolParameter(
                name="content",
                type=ParameterType.STRING,
                description="The content of the direct message (required)"
            ),
        ],
        tags=[Tag(key="category", value="communication"), Tag(key="type", value="create")],
    )
    async def send_direct_message(self, user_id: int, content: str) -> Tuple[bool, str]:
        try:
            response = self._run_async(
                self.client.send_dm(user_id=user_id, content=content)
            )
            return self._handle_response(response, "Direct message sent successfully")
        except Exception as e:
            logger.error(f"Error sending direct message: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        path="/tools/discord/get_guild_members",
        short_description="Get members of a Discord guild",
        description="Get members of a Discord guild (requires privileged intents).",
        parameters=[
            ToolParameter(
                name="guild_id",
                type=ParameterType.INTEGER,
                description="The ID of the guild to get members from (required)"
            ),
            ToolParameter(
                name="limit",
                type=ParameterType.INTEGER,
                description="Maximum number of members to retrieve (default: 100, max: 1000)",
                required=False,
            ),
        ],
        tags=[Tag(key="category", value="communication"), Tag(key="type", value="read")],
    )
    async def get_guild_members(
        self,
        guild_id: int,
        limit: Optional[int] = None
    ) -> Tuple[bool, str]:
        try:
            response = self._run_async(
                self.client.get_members(guild_id=guild_id, limit=limit)
            )
            return self._handle_response(response, "Guild members retrieved successfully")
        except Exception as e:
            logger.error(f"Error getting guild members: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        path="/tools/discord/create_role",
        short_description="Create a new role in a Discord guild",
        description="Create a new role in a Discord guild by name.",
        parameters=[
            ToolParameter(
                name="guild_id",
                type=ParameterType.INTEGER,
                description="The ID of the guild to create the role in (required)"
            ),
            ToolParameter(
                name="name",
                type=ParameterType.STRING,
                description="The name of the role to create (required)"
            ),
        ],
        tags=[Tag(key="category", value="communication"), Tag(key="type", value="create")],
    )
    async def create_role(self, guild_id: int, name: str) -> Tuple[bool, str]:
        try:
            response = self._run_async(
                self.client.create_role(guild_id=guild_id, name=name)
            )
            return self._handle_response(response, "Role created successfully")
        except Exception as e:
            logger.error(f"Error creating role: {e}")
            return False, json.dumps({"error": str(e)})
