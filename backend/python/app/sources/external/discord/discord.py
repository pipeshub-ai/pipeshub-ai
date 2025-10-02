import logging
from typing import Any

import discord
from app.sources.client.discord.discord import DiscordClient, DiscordResponse

# Set up logger
logger = logging.getLogger(__name__)


class DiscordDataSource:
    """Auto-generated Discord API client wrapper.
    - Uses the **official** discord.py SDK client passed as `DiscordClient`
    - **Snake_case** method names following Discord API conventions
    - All responses wrapped in standardized DiscordResponse format
    - Async methods for Discord API interactions
    """

    def __init__(self, client: DiscordClient) -> None:
        self.client = client.get_discord_client()

    async def _handle_discord_response(self, data: Any) -> DiscordResponse:  # noqa: ANN401
        """Handle Discord API response and convert to standardized format"""
        try:
            if data is None:
                return DiscordResponse(
                    success=False, error="Empty response from Discord API"
                )

            # Convert Discord objects to dict for serialization
            if isinstance(
                data,
                (
                    discord.Guild,
                    discord.TextChannel,
                    discord.Member,
                    discord.User,
                    discord.Message,
                ),
            ):
                # Convert Discord object to dict
                data_dict = self._discord_object_to_dict(data)
                return DiscordResponse(success=True, data=data_dict)
            elif (
                isinstance(data, (list, tuple))
                or hasattr(data, "__iter__")
                and not isinstance(data, (str, dict))
            ):
                # Convert list/iterable of Discord objects
                data_list = [
                    self._discord_object_to_dict(item) if hasattr(item, "id") else item
                    for item in data
                ]
                return DiscordResponse(
                    success=True, data={"items": data_list, "count": len(data_list)}
                )
            elif isinstance(data, dict):
                return DiscordResponse(success=True, data=data)
            return DiscordResponse(success=True, data={"result": str(data)})

        except Exception as e:
            logger.error(f"Error handling Discord response: {e}")
            return DiscordResponse(success=False, error=str(e))

    def _discord_object_to_dict(self, obj: Any) -> dict[str, Any]:  # noqa: ANN401
        """Convert Discord object to dictionary"""
        if isinstance(obj, discord.Guild):
            return {
                "id": str(obj.id),
                "name": obj.name,
                "description": obj.description,
                "member_count": obj.member_count,
                "created_at": obj.created_at.isoformat() if obj.created_at else None,
                "icon": str(obj.icon.url) if obj.icon else None,
                "owner_id": str(obj.owner_id) if obj.owner_id else None,
            }
        if isinstance(obj, discord.TextChannel):
            return {
                "id": str(obj.id),
                "name": obj.name,
                "type": str(obj.type),
                "position": obj.position,
                "topic": obj.topic,
                "category_id": str(obj.category_id) if obj.category_id else None,
                "created_at": obj.created_at.isoformat() if obj.created_at else None,
            }
        if isinstance(obj, discord.Member):
            return {
                "id": str(obj.id),
                "name": obj.name,
                "display_name": obj.display_name,
                "discriminator": obj.discriminator,
                "bot": obj.bot,
                "avatar": str(obj.avatar.url) if obj.avatar else None,
                "joined_at": obj.joined_at.isoformat() if obj.joined_at else None,
                "roles": [str(role.id) for role in obj.roles] if obj.roles else [],
            }
        if isinstance(obj, discord.User):
            return {
                "id": str(obj.id),
                "name": obj.name,
                "discriminator": obj.discriminator,
                "bot": obj.bot,
                "avatar": str(obj.avatar.url) if obj.avatar else None,
                "created_at": obj.created_at.isoformat() if obj.created_at else None,
            }
        if isinstance(obj, discord.Message):
            return {
                "id": str(obj.id),
                "content": obj.content,
                "author": {
                    "id": str(obj.author.id),
                    "name": obj.author.name,
                    "discriminator": obj.author.discriminator,
                }
                if obj.author
                else None,
                "channel_id": str(obj.channel.id) if obj.channel else None,
                "guild_id": str(obj.guild.id) if obj.guild else None,
                "created_at": obj.created_at.isoformat() if obj.created_at else None,
                "edited_at": obj.edited_at.isoformat() if obj.edited_at else None,
                "attachments": [
                    {"url": att.url, "filename": att.filename}
                    for att in obj.attachments
                ]
                if obj.attachments
                else [],
                "embeds": [
                    {"title": emb.title, "description": emb.description}
                    for emb in obj.embeds
                ]
                if obj.embeds
                else [],
            }
        # Fallback for unknown types
        return (
            {"id": str(obj.id), "type": type(obj).__name__}
            if hasattr(obj, "id")
            else {"data": str(obj)}
        )

    async def _handle_discord_error(self, error: Exception) -> DiscordResponse:
        """Handle Discord API errors and convert to standardized format"""
        error_msg = str(error)
        logger.error(f"Discord API error: {error_msg}")
        return DiscordResponse(success=False, error=error_msg)

    async def get_guilds(self) -> DiscordResponse:
        """Get all guilds (servers) the bot has access to

        Returns:
            DiscordResponse: Standardized response wrapper with success/data/error

        Notes:
            Fetches all guilds the bot is a member of using discord.py SDK.

        """
        try:
            # Client is already ready when using async context manager
            guilds = self.client.guilds
            # Convert to list explicitly (client.guilds returns a sequence-like object)
            guilds_list = list(guilds)
            return await self._handle_discord_response(guilds_list)
        except Exception as e:
            return await self._handle_discord_error(e)

    async def get_guild(self, guild_id: int) -> DiscordResponse:
        """Get specific guild details

        Args:
            guild_id: The Discord guild (server) ID

        Returns:
            DiscordResponse: Standardized response wrapper with success/data/error

        Notes:
            Fetches details of a specific guild by ID.

        """
        try:
            await self.client.wait_until_ready()
            guild = self.client.get_guild(guild_id)
            if guild is None:
                return DiscordResponse(
                    success=False, error=f"Guild with ID {guild_id} not found"
                )
            return await self._handle_discord_response(guild)
        except Exception as e:
            return await self._handle_discord_error(e)

    async def get_channels(
        self, guild_id: int, channel_type: str | None = None
    ) -> DiscordResponse:
        """Get all channels in a guild

        Args:
            guild_id: The Discord guild (server) ID
            channel_type: Optional filter by channel type (text, voice, category)

        Returns:
            DiscordResponse: Standardized response wrapper with success/data/error

        Notes:
            Fetches all channels in the specified guild, optionally filtered by type.

        """
        try:
            await self.client.wait_until_ready()
            guild = self.client.get_guild(guild_id)
            if guild is None:
                return DiscordResponse(
                    success=False, error=f"Guild with ID {guild_id} not found"
                )

            channels = guild.channels
            if channel_type:
                if channel_type == "text":
                    channels = [
                        ch for ch in channels if isinstance(ch, discord.TextChannel)
                    ]
                elif channel_type == "voice":
                    channels = [
                        ch for ch in channels if isinstance(ch, discord.VoiceChannel)
                    ]
                elif channel_type == "category":
                    channels = [
                        ch for ch in channels if isinstance(ch, discord.CategoryChannel)
                    ]

            return await self._handle_discord_response(channels)
        except Exception as e:
            return await self._handle_discord_error(e)

    async def get_channel(self, channel_id: int) -> DiscordResponse:
        """Get specific channel details

        Args:
            channel_id: The Discord channel ID

        Returns:
            DiscordResponse: Standardized response wrapper with success/data/error

        Notes:
            Fetches details of a specific channel by ID.

        """
        try:
            await self.client.wait_until_ready()
            channel = self.client.get_channel(channel_id)
            if channel is None:
                return DiscordResponse(
                    success=False, error=f"Channel with ID {channel_id} not found"
                )
            return await self._handle_discord_response(channel)
        except Exception as e:
            return await self._handle_discord_error(e)

    async def get_messages(
        self,
        channel_id: int,
        limit: int = 100,
        before: int | None = None,
        after: int | None = None,
    ) -> DiscordResponse:
        """Get messages from a channel

        Args:
            channel_id: The Discord channel ID
            limit: Maximum number of messages to fetch (default: 100, max: 100)
            before: Get messages before this message ID
            after: Get messages after this message ID

        Returns:
            DiscordResponse: Standardized response wrapper with success/data/error

        Notes:
            Fetches messages from the specified channel with pagination support.

        """
        try:
            channel = self.client.get_channel(channel_id)
            if channel is None:
                return DiscordResponse(
                    success=False, error=f"Channel with ID {channel_id} not found"
                )

            if not isinstance(channel, discord.TextChannel):
                return DiscordResponse(
                    success=False, error="Channel is not a text channel"
                )

            # Prepare parameters for history
            history_kwargs: dict[str, Any] = {"limit": min(limit, 100)}
            if before:
                history_kwargs["before"] = discord.Object(id=before)
            if after:
                history_kwargs["after"] = discord.Object(id=after)

            messages = []
            async for message in channel.history(**history_kwargs):
                messages.append(message)

            return await self._handle_discord_response(messages)
        except Exception as e:
            return await self._handle_discord_error(e)

    async def get_members(self, guild_id: int, limit: int = 100) -> DiscordResponse:
        """Get members of a guild

        Args:
            guild_id: The Discord guild (server) ID
            limit: Maximum number of members to fetch (default: 100)

        Returns:
            DiscordResponse: Standardized response wrapper with success/data/error

        Notes:
            Fetches members from the specified guild. Requires members intent.

        """
        try:
            guild = self.client.get_guild(guild_id)
            if guild is None:
                return DiscordResponse(
                    success=False, error=f"Guild with ID {guild_id} not found"
                )

            # Fetch members (limited by the limit parameter)
            members = []
            async for member in guild.fetch_members(limit=limit):
                members.append(member)

            return await self._handle_discord_response(members)
        except Exception as e:
            return await self._handle_discord_error(e)

    async def get_member(self, guild_id: int, user_id: int) -> DiscordResponse:
        """Get specific member details from a guild

        Args:
            guild_id: The Discord guild (server) ID
            user_id: The Discord user ID

        Returns:
            DiscordResponse: Standardized response wrapper with success/data/error

        Notes:
            Fetches a specific member from the guild.

        """
        try:
            guild = self.client.get_guild(guild_id)
            if guild is None:
                return DiscordResponse(
                    success=False, error=f"Guild with ID {guild_id} not found"
                )

            member = await guild.fetch_member(user_id)
            if member is None:
                return DiscordResponse(
                    success=False, error=f"Member with ID {user_id} not found in guild"
                )

            return await self._handle_discord_response(member)
        except Exception as e:
            return await self._handle_discord_error(e)

    async def get_user(self, user_id: int) -> DiscordResponse:
        """Get user information

        Args:
            user_id: The Discord user ID

        Returns:
            DiscordResponse: Standardized response wrapper with success/data/error

        Notes:
            Fetches user information by ID.

        """
        try:
            user = await self.client.fetch_user(user_id)
            if user is None:
                return DiscordResponse(
                    success=False, error=f"User with ID {user_id} not found"
                )
            return await self._handle_discord_response(user)
        except Exception as e:
            return await self._handle_discord_error(e)

    async def search_messages(
        self,
        guild_id: int,
        query: str,
        channel_id: int | None = None,
        limit: int = 100,
    ) -> DiscordResponse:
        """Search for messages in a guild

        Args:
            guild_id: The Discord guild (server) ID
            query: Search query string
            channel_id: Optional channel ID to limit search to specific channel
            limit: Maximum number of messages to search through (default: 100)

        Returns:
            DiscordResponse: Standardized response wrapper with success/data/error

        Notes:
            Searches through messages in the guild. This is a client-side search
            as Discord API doesn't provide native message search for bots.

        """
        try:
            await self.client.wait_until_ready()
            guild = self.client.get_guild(guild_id)
            if guild is None:
                return DiscordResponse(
                    success=False, error=f"Guild with ID {guild_id} not found"
                )

            matching_messages: list[discord.Message] = []
            query_lower = query.lower()

            # If channel_id specified, search only that channel
            if channel_id:
                channel = self.client.get_channel(channel_id)
                if channel and isinstance(channel, discord.TextChannel):
                    async for message in channel.history(limit=limit):
                        if query_lower in message.content.lower():
                            matching_messages.append(message)
            else:
                # Search all text channels in guild
                for channel in guild.text_channels:
                    try:
                        async for message in channel.history(
                            limit=limit // len(guild.text_channels) or 10
                        ):
                            if query_lower in message.content.lower():
                                matching_messages.append(message)
                    except discord.Forbidden:
                        # Skip channels bot doesn't have access to
                        continue

            return await self._handle_discord_response(matching_messages)
        except Exception as e:
            return await self._handle_discord_error(e)

    async def get_guild_roles(self, guild_id: int) -> DiscordResponse:
        """Get all roles in a guild

        Args:
            guild_id: The Discord guild (server) ID

        Returns:
            DiscordResponse: Standardized response wrapper with success/data/error

        Notes:
            Fetches all roles from the specified guild.

        """
        try:
            guild = self.client.get_guild(guild_id)
            if guild is None:
                return DiscordResponse(
                    success=False, error=f"Guild with ID {guild_id} not found"
                )

            roles = guild.roles
            roles_data = [
                {
                    "id": str(role.id),
                    "name": role.name,
                    "color": str(role.color),
                    "position": role.position,
                    "permissions": role.permissions.value,
                    "mentionable": role.mentionable,
                }
                for role in roles
            ]

            return DiscordResponse(
                success=True, data={"items": roles_data, "count": len(roles_data)}
            )
        except Exception as e:
            return await self._handle_discord_error(e)
