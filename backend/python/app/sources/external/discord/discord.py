import logging

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

    def _serialize(self, obj) -> object:  # type: ignore[override]
        if isinstance(obj, (str, int, float, bool)) or obj is None:
            return obj
        if isinstance(obj, discord.Guild):
            return {"id": str(obj.id), "name": obj.name}
        if isinstance(obj, discord.TextChannel):
            return {"id": str(obj.id), "name": obj.name, "type": str(obj.type)}
        if isinstance(obj, discord.Member):
            return {"id": str(obj.id), "display_name": obj.display_name, "bot": obj.bot}
        if isinstance(obj, discord.User):
            return {"id": str(obj.id), "name": obj.name, "bot": obj.bot}
        if isinstance(obj, discord.Message):
            return {"id": str(obj.id), "content": obj.content, "author_id": str(obj.author.id) if obj.author else None, "author_name": obj.author.name if obj.author else None}
        if isinstance(obj, discord.Role):
            return {"id": str(obj.id), "name": obj.name}
        if isinstance(obj, (list, tuple)):
            return [self._serialize(x) for x in obj]
        if isinstance(obj, dict):
            return {k: self._serialize(v) for k, v in obj.items()}
        if hasattr(obj, "__iter__") and not isinstance(obj, (str, bytes)):
            return [self._serialize(x) for x in list(obj)]
        return str(obj)

    def _wrap(self, data: object) -> DiscordResponse:
        if isinstance(data, list):
            return DiscordResponse(
                success=True,
                data={
                    "items": self._serialize(data),
                    "count": len(data),
                },
            )
        ser = self._serialize(data)
        if isinstance(ser, dict):
            return DiscordResponse(success=True, data=ser)
        return DiscordResponse(success=True, data={"result": ser})


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
            await self.client.wait_until_ready()
            guilds = self.client.guilds
            # Convert to list explicitly (client.guilds returns a sequence-like object)
            guilds_list = list(guilds)
            return self._wrap(guilds_list)
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
            return self._wrap(guild)
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

            return self._wrap(channels)
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
            return self._wrap(channel)
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
            await self.client.wait_until_ready()
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
            history_kwargs: dict[str, object] = {"limit": min(limit, 100)}
            if before:
                history_kwargs["before"] = discord.Object(id=before)
            if after:
                history_kwargs["after"] = discord.Object(id=after)

            messages = []
            async for message in channel.history(**history_kwargs):
                messages.append(message)

            return self._wrap(messages)
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
            await self.client.wait_until_ready()
            guild = self.client.get_guild(guild_id)
            if guild is None:
                return DiscordResponse(
                    success=False, error=f"Guild with ID {guild_id} not found"
                )

            # Fetch members (limited by the limit parameter)
            members = []
            async for member in guild.fetch_members(limit=limit):
                members.append(member)

            return self._wrap(members)
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
            await self.client.wait_until_ready()
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

            return self._wrap(member)
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
            await self.client.wait_until_ready()
            user = await self.client.fetch_user(user_id)
            if user is None:
                return DiscordResponse(
                    success=False, error=f"User with ID {user_id} not found"
                )
            return self._wrap(user)
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

            return self._wrap(matching_messages)
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
            await self.client.wait_until_ready()
            guild = self.client.get_guild(guild_id)
            if guild is None:
                return DiscordResponse(
                    success=False, error=f"Guild with ID {guild_id} not found"
                )

            try:
                roles = await guild.fetch_roles()
            except Exception:
                roles = guild.roles  # fallback
            return self._wrap(list(roles))
        except Exception as e:
            return await self._handle_discord_error(e)


    async def send_message(self, channel_id: int, content: str) -> DiscordResponse:
        """POST /channels/{channel.id}/messages
        Send a message to a text channel.
        Args:
            channel_id: Target text channel ID
            content: Message content
        """
        try:
            await self.client.wait_until_ready()
            channel = self.client.get_channel(channel_id)
            if channel is None or not isinstance(channel, discord.TextChannel):
                return DiscordResponse(success=False, error="Channel not found or not a text channel")
            msg = await channel.send(content)
            return self._wrap(msg)
        except Exception as e:
            return await self._handle_discord_error(e)

    async def add_reaction(self, channel_id: int, message_id: int, emoji: str) -> DiscordResponse:
        """PUT /channels/{channel.id}/messages/{message.id}/reactions/{emoji}/@me
        Add a reaction to a message.
        Args:
            channel_id: Text channel ID
            message_id: Message ID
            emoji: Unicode emoji or custom emoji in name:id format
        """
        try:
            await self.client.wait_until_ready()
            channel = self.client.get_channel(channel_id)
            if channel is None or not isinstance(channel, discord.TextChannel):
                return DiscordResponse(success=False, error="Channel not found or not a text channel")
            msg = await channel.fetch_message(message_id)
            await msg.add_reaction(emoji)
            return DiscordResponse(success=True, data={"message_id": str(msg.id), "emoji": emoji})
        except Exception as e:
            return await self._handle_discord_error(e)

    async def remove_reaction(self, channel_id: int, message_id: int, emoji: str) -> DiscordResponse:
        """DELETE /channels/{channel.id}/messages/{message.id}/reactions/{emoji}/@me
        Remove the bot's reaction from a message.
        Args:
            channel_id: Text channel ID
            message_id: Message ID
            emoji: Emoji to remove
        """
        try:
            await self.client.wait_until_ready()
            channel = self.client.get_channel(channel_id)
            if channel is None or not isinstance(channel, discord.TextChannel):
                return DiscordResponse(success=False, error="Channel not found or not a text channel")
            msg = await channel.fetch_message(message_id)
            # Remove own reaction (discord.py lacks direct helper; iterate reactions)
            for reaction in msg.reactions:
                if str(reaction.emoji) == emoji:
                    async for user in reaction.users():
                        if user == self.client.user:
                            await reaction.remove(user)
                            break
            return DiscordResponse(success=True, data={"message_id": str(msg.id), "removed": emoji})
        except Exception as e:
            return await self._handle_discord_error(e)

    async def edit_message(self, channel_id: int, message_id: int, content: str) -> DiscordResponse:
        """PATCH /channels/{channel.id}/messages/{message.id}
        Edit a previously sent message by the bot.
        Args:
            channel_id: Text channel ID
            message_id: Message ID
            content: New message content
        """
        try:
            await self.client.wait_until_ready()
            channel = self.client.get_channel(channel_id)
            if channel is None or not isinstance(channel, discord.TextChannel):
                return DiscordResponse(success=False, error="Channel not found or not a text channel")
            msg = await channel.fetch_message(message_id)
            if msg.author != self.client.user:
                return DiscordResponse(success=False, error="Cannot edit a message not authored by the bot")
            edited = await msg.edit(content=content)
            return self._wrap(edited)
        except Exception as e:
            return await self._handle_discord_error(e)

    async def delete_message(self, channel_id: int, message_id: int) -> DiscordResponse:
        """DELETE /channels/{channel.id}/messages/{message.id}
        Delete a message authored by the bot.
        Args:
            channel_id: Text channel ID
            message_id: Message ID
        """
        try:
            await self.client.wait_until_ready()
            channel = self.client.get_channel(channel_id)
            if channel is None or not isinstance(channel, discord.TextChannel):
                return DiscordResponse(success=False, error="Channel not found or not a text channel")
            msg = await channel.fetch_message(message_id)
            if msg.author != self.client.user:
                return DiscordResponse(success=False, error="Cannot delete a message not authored by the bot")
            await msg.delete()
            return DiscordResponse(success=True, data={"deleted_message_id": str(message_id)})
        except Exception as e:
            return await self._handle_discord_error(e)

    async def create_role(self, guild_id: int, name: str) -> DiscordResponse:
        """POST /guilds/{guild.id}/roles
        Create a role in the guild.
        Args:
            guild_id: Guild ID
            name: Role name
        """
        try:
            await self.client.wait_until_ready()
            guild = self.client.get_guild(guild_id)
            if guild is None:
                return DiscordResponse(success=False, error="Guild not found")
            role = await guild.create_role(name=name)
            return self._wrap(role)
        except Exception as e:
            return await self._handle_discord_error(e)

    async def send_dm(self, user_id: int, content: str) -> DiscordResponse:
        """POST /users/@me/channels then POST /channels/{channel.id}/messages
        Send a direct message to a user.
        Args:
            user_id: Target user ID
            content: Message content
        """
        try:
            await self.client.wait_until_ready()
            user = await self.client.fetch_user(user_id)
            if user is None:
                return DiscordResponse(success=False, error="User not found")
            dm = await user.create_dm()
            msg = await dm.send(content)
            return self._wrap(msg)
        except Exception as e:
            return await self._handle_discord_error(e)
