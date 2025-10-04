import json
import logging
from typing import Dict, Optional

from app.sources.client.discord.discord import DiscordClient, DiscordResponse
from app.sources.client.http.http_request import HTTPRequest

# Set up logger
logger = logging.getLogger(__name__)


class DiscordDataSource:
    """Discord API client wrapper using direct HTTP REST API calls.
    - Uses HTTPClient for direct Discord REST API v10 calls
    - **Snake_case** method names following Discord API conventions
    - All responses wrapped in standardized DiscordResponse format (Pydantic model)
    - Async methods for Discord API interactions
    - No serialization needed - returns raw JSON responses from Discord API
    """

    def __init__(self, client: DiscordClient) -> None:
        """Initialize Discord data source with client.

        Args:
            client: DiscordClient instance wrapping HTTPClient
        """
        self._client = client
        self.http = client.get_client()
        if self.http is None:
            raise ValueError("HTTP client is not initialized")
        try:
            self.base_url = self.http.get_base_url().rstrip("/")
        except AttributeError as exc:
            raise ValueError("HTTP client does not have get_base_url method") from exc

    def get_data_source(self) -> "DiscordDataSource":
        """Return the data source instance."""
        return self

    async def get_guilds(self) -> DiscordResponse:
        """Get all guilds (servers) the bot has access to

        Discord endpoint: `GET /users/@me/guilds`

        Returns partial guild objects as per Discord API specification.

        Args:
            (no parameters)

        Returns:
            DiscordResponse: Standardized response wrapper with success/data/error
            Array of partial guild objects matching Discord API format

        Notes:
            Response format validated against Discord REST API v10.
            Returns: id (string), name, icon, owner (boolean), permissions (string), features
        """
        url = self.base_url + "/users/@me/guilds"
        request = HTTPRequest(method="GET", url=url, headers=self.http.headers.copy())

        try:
            response = await self.http.execute(request)
            return DiscordResponse(success=True, data=response.json())
        except Exception as e:
            logger.error(f"Discord API error: {str(e)}")
            return DiscordResponse(success=False, error=str(e))

    async def get_guild(
        self, guild_id: int, with_counts: bool = True
    ) -> DiscordResponse:
        """Get specific guild details by ID

        Discord endpoint: `GET /guilds/{guild.id}`

        Returns full guild object as per Discord API specification.

        Args:
            guild_id (required): Guild ID
            with_counts (optional): Include approximate_member_count and approximate_presence_count (default: True)

        Returns:
            DiscordResponse: Standardized response wrapper with success/data/error
            Full guild object matching Discord API format

        Notes:
            Response format validated against Discord REST API v10.
            Includes all guild properties as defined in Discord documentation.
            with_counts=true adds approximate_member_count and approximate_presence_count fields.
        """
        url = self.base_url + f"/guilds/{guild_id}"

        query_params = {}
        if with_counts:
            query_params["with_counts"] = "true"

        request = HTTPRequest(
            method="GET", url=url, headers=self.http.headers.copy(), query=query_params
        )

        try:
            response = await self.http.execute(request)
            return DiscordResponse(success=True, data=response.json())
        except Exception as e:
            logger.error(f"Discord API error: {str(e)}")
            return DiscordResponse(success=False, error=str(e))

    async def get_channels(
        self, guild_id: int, channel_type: Optional[str] = None
    ) -> DiscordResponse:
        """List channels in a guild (optionally filtered by type)

        Discord endpoint: `GET /guilds/{guild.id}/channels`

        Returns array of channel objects as per Discord API specification.

        Args:
            guild_id (required): Guild ID
            channel_type (optional): Optional filter (text|voice|category)
                - text: Type 0 (GUILD_TEXT)
                - voice: Type 2 (GUILD_VOICE)
                - category: Type 4 (GUILD_CATEGORY)

        Returns:
            DiscordResponse: Standardized response wrapper with success/data/error
            Array of channel objects matching Discord API format

        Notes:
            Response format validated against Discord REST API v10.
            Channel objects include type-specific fields.
        """
        url = self.base_url + f"/guilds/{guild_id}/channels"
        request = HTTPRequest(method="GET", url=url, headers=self.http.headers.copy())

        try:
            response = await self.http.execute(request)
            channels = response.json()

            # Filter by channel type if specified
            if channel_type:
                channel_type_map = {
                    "text": 0,  # GUILD_TEXT
                    "voice": 2,  # GUILD_VOICE
                    "category": 4,  # GUILD_CATEGORY
                }
                type_value = channel_type_map.get(channel_type)
                if type_value is not None:
                    channels = [ch for ch in channels if ch.get("type") == type_value]

            return DiscordResponse(success=True, data=channels)
        except Exception as e:
            logger.error(f"Discord API error: {str(e)}")
            return DiscordResponse(success=False, error=str(e))

    async def get_channel(self, channel_id: int) -> DiscordResponse:
        """Get a channel by ID

        Discord endpoint: `GET /channels/{channel.id}`

        Returns channel object as per Discord API specification.

        Args:
            channel_id (required): Channel ID

        Returns:
            DiscordResponse: Standardized response wrapper with success/data/error
            Channel object matching Discord API format

        Notes:
            Response format validated against Discord REST API v10.
            Includes channel type-specific fields.
        """
        url = self.base_url + f"/channels/{channel_id}"
        request = HTTPRequest(method="GET", url=url, headers=self.http.headers.copy())

        try:
            response = await self.http.execute(request)
            return DiscordResponse(success=True, data=response.json())
        except Exception as e:
            logger.error(f"Discord API error: {str(e)}")
            return DiscordResponse(success=False, error=str(e))

    async def get_messages(
        self,
        channel_id: int,
        limit: int = 100,
        before: Optional[int] = None,
        after: Optional[int] = None,
    ) -> DiscordResponse:
        """Fetch messages from a text channel

        Discord endpoint: `GET /channels/{channel.id}/messages`

        Returns array of message objects as per Discord API specification.

        Args:
            channel_id (required): Channel ID
            limit (optional): Max messages (<=100)
            before (optional): Message ID to fetch before
            after (optional): Message ID to fetch after

        Returns:
            DiscordResponse: Standardized response wrapper with success/data/error
            Array of message objects matching Discord API format

        Notes:
            Response format validated against Discord REST API v10.
            Includes author, content, embeds, attachments, reactions.
            IDs are strings, timestamps are ISO 8601 format.
        """
        url = self.base_url + f"/channels/{channel_id}/messages"

        # Build query parameters
        query_params: Dict[str, str] = {"limit": str(min(limit, 100))}
        if before is not None:
            query_params["before"] = str(before)
        if after is not None:
            query_params["after"] = str(after)

        request = HTTPRequest(
            method="GET", url=url, headers=self.http.headers.copy(), query=query_params
        )

        try:
            response = await self.http.execute(request)
            return DiscordResponse(success=True, data=response.json())
        except Exception as e:
            logger.error(f"Discord API error: {str(e)}")
            return DiscordResponse(success=False, error=str(e))

    async def get_members(self, guild_id: int, limit: int = 100) -> DiscordResponse:
        """Fetch members (requires privileged intents)

        Discord endpoint: `GET /guilds/{guild.id}/members`

        Returns array of guild member objects as per Discord API specification.

        Args:
            guild_id (required): Guild ID
            limit (optional): Max members (1-1000, default 100)

        Returns:
            DiscordResponse: Standardized response wrapper with success/data/error
            Array of guild member objects matching Discord API format

        Notes:
            Response format validated against Discord REST API v10.
            Includes user object, roles, joined_at, permissions.
            May return fewer members if GUILD_MEMBERS intent is not enabled.
        """
        url = self.base_url + f"/guilds/{guild_id}/members"

        # Ensure limit is between 1 and 1000
        actual_limit = min(max(limit, 1), 1000)
        query_params: Dict[str, str] = {"limit": str(actual_limit)}

        request = HTTPRequest(
            method="GET", url=url, headers=self.http.headers.copy(), query=query_params
        )

        try:
            response = await self.http.execute(request)
            return DiscordResponse(success=True, data=response.json())
        except Exception as e:
            logger.error(f"Discord API error: {str(e)}")
            return DiscordResponse(success=False, error=str(e))

    async def get_member(self, guild_id: int, user_id: int) -> DiscordResponse:
        """Fetch a specific member

        Discord endpoint: `GET /guilds/{guild.id}/members/{user.id}`

        Returns guild member object as per Discord API specification.

        Args:
            guild_id (required): Guild ID
            user_id (required): User ID

        Returns:
            DiscordResponse: Standardized response wrapper with success/data/error
            Guild member object matching Discord API format

        Notes:
            Response format validated against Discord REST API v10.
            Includes user, roles, joined_at, permissions.
        """
        url = self.base_url + f"/guilds/{guild_id}/members/{user_id}"
        request = HTTPRequest(method="GET", url=url, headers=self.http.headers.copy())

        try:
            response = await self.http.execute(request)
            return DiscordResponse(success=True, data=response.json())
        except Exception as e:
            logger.error(f"Discord API error: {str(e)}")
            return DiscordResponse(success=False, error=str(e))

    async def get_user(self, user_id: int) -> DiscordResponse:
        """Fetch a user profile

        Discord endpoint: `GET /users/{user.id}`

        Returns user object as per Discord API specification.

        Args:
            user_id (required): User ID

        Returns:
            DiscordResponse: Standardized response wrapper with success/data/error
            User object matching Discord API format

        Notes:
            Response format validated against Discord REST API v10.
            IDs are strings, includes username, discriminator, avatar, flags.
        """
        url = self.base_url + f"/users/{user_id}"
        request = HTTPRequest(method="GET", url=url, headers=self.http.headers.copy())

        try:
            response = await self.http.execute(request)
            return DiscordResponse(success=True, data=response.json())
        except Exception as e:
            logger.error(f"Discord API error: {str(e)}")
            return DiscordResponse(success=False, error=str(e))

    async def get_guild_roles(self, guild_id: int) -> DiscordResponse:
        """Fetch all roles in a guild

        Discord endpoint: `GET /guilds/{guild.id}/roles`

        Returns array of role objects as per Discord API specification.

        Args:
            guild_id (required): Guild ID

        Returns:
            DiscordResponse: Standardized response wrapper with success/data/error
            Array of role objects matching Discord API format

        Notes:
            Response format validated against Discord REST API v10.
            Includes id (string), name, color, permissions (string), position.
        """
        url = self.base_url + f"/guilds/{guild_id}/roles"
        request = HTTPRequest(method="GET", url=url, headers=self.http.headers.copy())

        try:
            response = await self.http.execute(request)
            return DiscordResponse(success=True, data=response.json())
        except Exception as e:
            logger.error(f"Discord API error: {str(e)}")
            return DiscordResponse(success=False, error=str(e))

    async def send_message(self, channel_id: int, content: str) -> DiscordResponse:
        """Send a message to a text channel

        Discord endpoint: `POST /channels/{channel.id}/messages`

        Returns message object as per Discord API specification.

        Args:
            channel_id (required): Target text channel ID
            content (required): Message content

        Returns:
            DiscordResponse: Standardized response wrapper with success/data/error
            Message object matching Discord API format

        Notes:
            Response format validated against Discord REST API v10.
            Returns the created message with all standard fields.
        """
        url = self.base_url + f"/channels/{channel_id}/messages"

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        body = {"content": content}

        request = HTTPRequest(
            method="POST", url=url, headers=headers, body=json.dumps(body)
        )

        try:
            response = await self.http.execute(request)
            return DiscordResponse(success=True, data=response.json())
        except Exception as e:
            logger.error(f"Discord API error: {str(e)}")
            return DiscordResponse(success=False, error=str(e))

    async def create_channel(
        self, guild_id: int, name: str, channel_type: Optional[str] = None
    ) -> DiscordResponse:
        """Create a new channel in a guild

        Discord endpoint: `POST /guilds/{guild.id}/channels`

        Returns channel object as per Discord API specification.

        Args:
            guild_id (required): Guild ID
            name (required): Channel name
            channel_type (optional): Channel type (text|voice|category)
                - text: Type 0 (GUILD_TEXT)
                - voice: Type 2 (GUILD_VOICE)
                - category: Type 4 (GUILD_CATEGORY)

        Returns:
            DiscordResponse: Standardized response wrapper with success/data/error
            Channel object matching Discord API format

        Notes:
            Response format validated against Discord REST API v10.
            Returns the created channel with all standard fields.
        """
        url = self.base_url + f"/guilds/{guild_id}/channels"

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        # Map channel type to Discord API type value
        channel_type_map = {
            "voice": 2,  # GUILD_VOICE
            "category": 4,  # GUILD_CATEGORY
            "text": 0,  # GUILD_TEXT (default)
        }
        type_value = channel_type_map.get(channel_type or "text", 0)

        body = {"name": name, "type": type_value}

        request = HTTPRequest(
            method="POST", url=url, headers=headers, body=json.dumps(body)
        )

        try:
            response = await self.http.execute(request)
            return DiscordResponse(success=True, data=response.json())
        except Exception as e:
            logger.error(f"Discord API error: {str(e)}")
            return DiscordResponse(success=False, error=str(e))

    async def delete_channel(self, channel_id: int) -> DiscordResponse:
        """Delete a channel

        Discord endpoint: `DELETE /channels/{channel.id}`

        Returns confirmation as per Discord API specification.

        Args:
            channel_id (required): Channel ID to delete

        Returns:
            DiscordResponse: Standardized response wrapper with success/data/error
            Confirmation object with deleted channel data

        Notes:
            Response format validated against Discord REST API v10.
            Returns the deleted channel object on success.
        """
        url = self.base_url + f"/channels/{channel_id}"
        request = HTTPRequest(
            method="DELETE", url=url, headers=self.http.headers.copy()
        )

        try:
            response = await self.http.execute(request)
            return DiscordResponse(success=True, data=response.json())
        except Exception as e:
            logger.error(f"Discord API error: {str(e)}")
            return DiscordResponse(success=False, error=str(e))

    async def create_role(self, guild_id: int, name: str) -> DiscordResponse:
        """Create a role in the guild

        Discord endpoint: `POST /guilds/{guild.id}/roles`

        Returns role object as per Discord API specification.

        Args:
            guild_id (required): Guild ID
            name (required): Role name

        Returns:
            DiscordResponse: Standardized response wrapper with success/data/error
            Role object matching Discord API format

        Notes:
            Response format validated against Discord REST API v10.
            Returns the created role with all standard fields.
        """
        url = self.base_url + f"/guilds/{guild_id}/roles"

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        body = {"name": name}

        request = HTTPRequest(
            method="POST", url=url, headers=headers, body=json.dumps(body)
        )

        try:
            response = await self.http.execute(request)
            return DiscordResponse(success=True, data=response.json())
        except Exception as e:
            logger.error(f"Discord API error: {str(e)}")
            return DiscordResponse(success=False, error=str(e))

    async def send_dm(self, user_id: int, content: str) -> DiscordResponse:
        """Send a direct message to a user

        Discord endpoint: `POST /users/@me/channels` then `POST /channels/{channel.id}/messages`

        Returns message object as per Discord API specification.

        Args:
            user_id (required): Target user ID
            content (required): Message content

        Returns:
            DiscordResponse: Standardized response wrapper with success/data/error
            Message object matching Discord API format

        Notes:
            Response format validated against Discord REST API v10.
            Creates DM channel then sends message, returns message object.
        """
        # First, create or get the DM channel
        create_dm_url = self.base_url + "/users/@me/channels"

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        create_dm_body = {"recipient_id": str(user_id)}

        create_dm_request = HTTPRequest(
            method="POST",
            url=create_dm_url,
            headers=headers,
            body=json.dumps(create_dm_body),
        )

        try:
            # Create DM channel
            dm_response = await self.http.execute(create_dm_request)
            dm_channel = dm_response.json()
            channel_id = dm_channel.get("id")

            if not channel_id:
                return DiscordResponse(
                    success=False, error="Failed to create DM channel"
                )

            # Send message to DM channel
            send_message_url = self.base_url + f"/channels/{channel_id}/messages"
            send_message_body = {"content": content}

            send_message_request = HTTPRequest(
                method="POST",
                url=send_message_url,
                headers=headers,
                body=json.dumps(send_message_body),
            )

            message_response = await self.http.execute(send_message_request)
            return DiscordResponse(success=True, data=message_response.json())
        except Exception as e:
            logger.error(f"Discord API error: {str(e)}")
            return DiscordResponse(success=False, error=str(e))
