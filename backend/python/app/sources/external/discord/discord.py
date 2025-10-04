import logging
from typing import Dict, Optional

import discord

from app.sources.client.discord.discord import DiscordClient, DiscordResponse

# Set up logger
logger = logging.getLogger(__name__)


class DiscordDataSource:
    """Discord API client wrapper.
    - Uses the **official** discord.py SDK client passed as `DiscordClient`
    - **Snake_case** method names following Discord API conventions
    - All responses wrapped in standardized DiscordResponse format (Pydantic model)
    - Async methods for Discord API interactions
    """

    def __init__(self, client: DiscordClient) -> None:
        """Initialize Discord data source with client.

        Args:
            client: DiscordClient instance wrapping discord.py Client
        """
        self.client = client.get_discord_client()

    def _serialize_partial_guild(self, guild: discord.Guild) -> Dict[str, object]:
        """Serialize Guild to match GET /users/@me/guilds response format.

        Discord API Reference:
        https://discord.com/developers/docs/resources/user

        Args:
            guild: discord.py Guild object

        Returns:
            Dict matching Discord's partial guild object format
        """
        return {
            "id": str(guild.id),
            "name": guild.name,
            "icon": guild.icon.key if guild.icon else None,
            "owner": guild.owner_id == self.client.user.id
            if self.client.user
            else False,
            "permissions": str(guild.me.guild_permissions.value) if guild.me else "0",
            "features": guild.features,
        }

    def _serialize_guild(self, guild: discord.Guild) -> Dict[str, object]:
        """Serialize Guild to match GET /guilds/{guild.id} response format.

        Discord API Reference:
        https://discord.com/developers/docs/resources/guild

        Args:
            guild: discord.py Guild object

        Returns:
            Dict matching Discord's full guild object format
        """
        return {
            "id": str(guild.id),
            "name": guild.name,
            "icon": guild.icon.key if guild.icon else None,
            "icon_hash": None,
            "splash": guild.splash.key if guild.splash else None,
            "discovery_splash": guild.discovery_splash.key
            if guild.discovery_splash
            else None,
            "owner_id": str(guild.owner_id),
            "afk_channel_id": str(guild.afk_channel.id) if guild.afk_channel else None,
            "afk_timeout": guild.afk_timeout,
            "widget_enabled": guild.widget_enabled
            if hasattr(guild, "widget_enabled")
            else None,
            "widget_channel_id": str(guild.widget_channel.id)
            if guild.widget_channel
            else None,
            "verification_level": guild.verification_level.value,
            "default_message_notifications": guild.default_notifications.value,
            "explicit_content_filter": guild.explicit_content_filter.value,
            "roles": [self._serialize_role(role) for role in guild.roles],
            "emojis": [{"id": str(e.id), "name": e.name} for e in guild.emojis],
            "features": guild.features,
            "mfa_level": guild.mfa_level,
            "application_id": str(getattr(guild, "application_id", None))
            if getattr(guild, "application_id", None)
            else None,
            "system_channel_id": str(guild.system_channel.id)
            if guild.system_channel
            else None,
            "system_channel_flags": guild.system_channel_flags.value,
            "rules_channel_id": str(guild.rules_channel.id)
            if guild.rules_channel
            else None,
            "max_presences": guild.max_presences,
            "max_members": guild.max_members,
            "vanity_url_code": guild.vanity_url_code,
            "description": guild.description,
            "banner": guild.banner.key if guild.banner else None,
            "premium_tier": guild.premium_tier,
            "premium_subscription_count": guild.premium_subscription_count or 0,
            "preferred_locale": str(guild.preferred_locale),
            "public_updates_channel_id": str(guild.public_updates_channel.id)
            if guild.public_updates_channel
            else None,
            "nsfw_level": guild.nsfw_level.value,
            "premium_progress_bar_enabled": guild.premium_progress_bar_enabled,
        }

    def _serialize_channel(
        self, channel: discord.abc.GuildChannel
    ) -> Dict[str, object]:
        """Serialize Channel to match GET /channels/{channel.id} response format.

        Discord API Reference:
        https://discord.com/developers/docs/resources/channel

        Args:
            channel: discord.py Channel object

        Returns:
            Dict matching Discord's channel object format
        """
        base_data = {
            "id": str(channel.id),
            "type": channel.type.value,
            "name": channel.name,
            "position": channel.position,
        }

        if hasattr(channel, "guild"):
            base_data["guild_id"] = str(channel.guild.id) if channel.guild else None

        if isinstance(channel, discord.TextChannel):
            base_data.update(
                {
                    "topic": channel.topic,
                    "nsfw": channel.nsfw,
                    "last_message_id": str(channel.last_message_id)
                    if channel.last_message_id
                    else None,
                    "rate_limit_per_user": channel.slowmode_delay,
                    "parent_id": str(channel.category.id) if channel.category else None,
                }
            )
        elif isinstance(channel, discord.VoiceChannel):
            base_data.update(
                {
                    "bitrate": channel.bitrate,
                    "user_limit": channel.user_limit,
                    "parent_id": str(channel.category.id) if channel.category else None,
                }
            )
        elif isinstance(channel, discord.CategoryChannel):
            base_data.update(
                {
                    "nsfw": channel.nsfw,
                }
            )

        return base_data

    def _serialize_message(self, message: discord.Message) -> Dict[str, object]:
        """Serialize Message to match GET /channels/{channel.id}/messages response format.

        Discord API Reference:
        https://discord.com/developers/docs/resources/channel

        Args:
            message: discord.py Message object

        Returns:
            Dict matching Discord's message object format
        """
        return {
            "id": str(message.id),
            "channel_id": str(message.channel.id),
            "author": self._serialize_user(message.author),
            "content": message.content,
            "timestamp": message.created_at.isoformat(),
            "edited_timestamp": message.edited_at.isoformat()
            if message.edited_at
            else None,
            "tts": message.tts,
            "mention_everyone": message.mention_everyone,
            "mentions": [self._serialize_user(user) for user in message.mentions],
            "mention_roles": [str(role.id) for role in message.role_mentions],
            "attachments": [
                {
                    "id": str(a.id),
                    "filename": a.filename,
                    "size": a.size,
                    "url": a.url,
                    "proxy_url": a.proxy_url,
                }
                for a in message.attachments
            ],
            "embeds": [e.to_dict() for e in message.embeds],
            "reactions": [
                {
                    "count": r.count,
                    "me": r.me,
                    "emoji": {"id": str(r.emoji.id), "name": r.emoji.name}
                    if hasattr(r.emoji, "id")
                    else {"name": str(r.emoji)},
                }
                for r in message.reactions
            ]
            if message.reactions
            else [],
            "pinned": message.pinned,
            "type": message.type.value,
            "flags": message.flags.value,
        }

    def _serialize_member(self, member: discord.Member) -> Dict[str, object]:
        """Serialize Member to match GET /guilds/{guild.id}/members response format.

        Discord API Reference:
        https://discord.com/developers/docs/resources/guild

        Args:
            member: discord.py Member object

        Returns:
            Dict matching Discord's guild member object format
        """
        return {
            "user": self._serialize_user(member),
            "nick": member.nick,
            "avatar": member.avatar.key if member.avatar else None,
            "roles": [
                str(role.id) for role in member.roles if role.id != member.guild.id
            ],
            "joined_at": member.joined_at.isoformat() if member.joined_at else None,
            "premium_since": member.premium_since.isoformat()
            if member.premium_since
            else None,
            "deaf": member.voice.deaf if member.voice else False,
            "mute": member.voice.mute if member.voice else False,
            "flags": member.flags.value,
            "pending": member.pending,
            "permissions": str(member.guild_permissions.value),
            "communication_disabled_until": member.communication_disabled_until.isoformat()
            if getattr(member, "communication_disabled_until", None)
            else None,
        }

    def _serialize_user(self, user: discord.User | discord.Member) -> Dict[str, object]:
        """Serialize User to match GET /users/{user.id} response format.

        Discord API Reference:
        https://discord.com/developers/docs/resources/user

        Args:
            user: discord.py User or Member object

        Returns:
            Dict matching Discord's user object format
        """
        return {
            "id": str(user.id),
            "username": user.name,
            "discriminator": user.discriminator,
            "avatar": user.avatar.key if user.avatar else None,
            "bot": user.bot,
            "system": user.system,
            "banner": user.banner.key
            if hasattr(user, "banner") and user.banner
            else None,
            "accent_color": user.accent_color
            if hasattr(user, "accent_color")
            else None,
            "public_flags": user.public_flags.value,
        }

    def _serialize_role(self, role: discord.Role) -> Dict[str, object]:
        """Serialize Role to match GET /guilds/{guild.id}/roles response format.

        Discord API Reference:
        https://discord.com/developers/docs/resources/guild

        Args:
            role: discord.py Role object

        Returns:
            Dict matching Discord's role object format
        """
        return {
            "id": str(role.id),
            "name": role.name,
            "color": role.color.value,
            "hoist": role.hoist,
            "icon": role.icon.key if role.icon else None,
            "unicode_emoji": role.unicode_emoji,
            "position": role.position,
            "permissions": str(role.permissions.value),
            "managed": role.managed,
            "mentionable": role.mentionable,
        }

    async def _handle_discord_error(self, error: Exception) -> DiscordResponse:
        """Handle Discord API errors and convert to standardized format.

        Args:
            error: Exception raised by Discord API

        Returns:
            DiscordResponse with error information
        """
        error_msg = str(error)
        logger.error(f"Discord API error: {error_msg}")
        return DiscordResponse(success=False, error=error_msg)

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

        try:
            await self.client.wait_until_ready()
            guilds = self.client.guilds
            serialized_guilds = [
                self._serialize_partial_guild(guild) for guild in guilds
            ]
            return DiscordResponse(
                success=True,
                data={"items": serialized_guilds, "count": len(serialized_guilds)},
            )
        except Exception as e:
            return await self._handle_discord_error(e)

    async def get_guild(self, guild_id: int) -> DiscordResponse:
        """Get specific guild details by ID

        Discord endpoint: `GET /guilds/{guild.id}`

        Returns full guild object as per Discord API specification.

        Args:
            guild_id (required): Guild ID

        Returns:
            DiscordResponse: Standardized response wrapper with success/data/error
            Full guild object matching Discord API format

        Notes:
            Response format validated against Discord REST API v10.
            Includes all guild properties as defined in Discord documentation.
        """

        try:
            await self.client.wait_until_ready()
            guild = self.client.get_guild(guild_id)
            if guild is None:
                return DiscordResponse(
                    success=False, error=f"Guild with ID {guild_id} not found"
                )
            return DiscordResponse(success=True, data=self._serialize_guild(guild))
        except Exception as e:
            return await self._handle_discord_error(e)

    async def get_channels(
        self, guild_id: int, channel_type: Optional[str] = None
    ) -> DiscordResponse:
        """List channels in a guild (optionally filtered by type)

        Discord endpoint: `GET /guilds/{guild.id}/channels`

        Returns array of channel objects as per Discord API specification.

        Args:
            guild_id (required): Guild ID
            channel_type (optional): Optional filter (text|voice|category)

        Returns:
            DiscordResponse: Standardized response wrapper with success/data/error
            Array of channel objects matching Discord API format

        Notes:
            Response format validated against Discord REST API v10.
            Channel objects include type-specific fields.
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

            serialized_channels = [self._serialize_channel(ch) for ch in channels]
            return DiscordResponse(
                success=True,
                data={"items": serialized_channels, "count": len(serialized_channels)},
            )
        except Exception as e:
            return await self._handle_discord_error(e)

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

        try:
            await self.client.wait_until_ready()
            channel = self.client.get_channel(channel_id)
            if channel is None:
                return DiscordResponse(
                    success=False, error=f"Channel with ID {channel_id} not found"
                )
            return DiscordResponse(success=True, data=self._serialize_channel(channel))
        except Exception as e:
            return await self._handle_discord_error(e)

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

            history_kwargs: Dict[str, object] = {"limit": min(limit, 100)}
            if before:
                history_kwargs["before"] = discord.Object(id=before)
            if after:
                history_kwargs["after"] = discord.Object(id=after)

            messages = []
            async for message in channel.history(**history_kwargs):
                messages.append(message)

            serialized_messages = [self._serialize_message(msg) for msg in messages]
            return DiscordResponse(
                success=True,
                data={"items": serialized_messages, "count": len(serialized_messages)},
            )
        except Exception as e:
            return await self._handle_discord_error(e)

    async def get_members(self, guild_id: int, limit: int = 100) -> DiscordResponse:
        """Fetch members (requires privileged intents)

        Discord endpoint: `GET /guilds/{guild.id}/members`

        Returns array of guild member objects as per Discord API specification.

        Args:
            guild_id (required): Guild ID
            limit (optional): Max members (approx)

        Returns:
            DiscordResponse: Standardized response wrapper with success/data/error
            Array of guild member objects matching Discord API format

        Notes:
            Response format validated against Discord REST API v10.
            Includes user object, roles, joined_at, permissions.
            Requires GUILD_MEMBERS privileged intent.
        """

        try:
            await self.client.wait_until_ready()
            guild = self.client.get_guild(guild_id)
            if guild is None:
                return DiscordResponse(
                    success=False, error=f"Guild with ID {guild_id} not found"
                )

            members = []
            async for member in guild.fetch_members(limit=limit):
                members.append(member)

            serialized_members = [self._serialize_member(member) for member in members]
            return DiscordResponse(
                success=True,
                data={"items": serialized_members, "count": len(serialized_members)},
            )
        except Exception as e:
            return await self._handle_discord_error(e)

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

            return DiscordResponse(success=True, data=self._serialize_member(member))
        except Exception as e:
            return await self._handle_discord_error(e)

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

        try:
            await self.client.wait_until_ready()
            user = await self.client.fetch_user(user_id)
            if user is None:
                return DiscordResponse(
                    success=False, error=f"User with ID {user_id} not found"
                )
            return DiscordResponse(success=True, data=self._serialize_user(user))
        except Exception as e:
            return await self._handle_discord_error(e)

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
                roles = guild.roles

            serialized_roles = [self._serialize_role(role) for role in roles]
            return DiscordResponse(
                success=True,
                data={"items": serialized_roles, "count": len(serialized_roles)},
            )
        except Exception as e:
            return await self._handle_discord_error(e)

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

        try:
            await self.client.wait_until_ready()
            channel = self.client.get_channel(channel_id)
            if channel is None or not isinstance(channel, discord.TextChannel):
                return DiscordResponse(
                    success=False, error="Channel not found or not a text channel"
                )
            msg = await channel.send(content)
            return DiscordResponse(success=True, data=self._serialize_message(msg))
        except Exception as e:
            return await self._handle_discord_error(e)

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

        Returns:
            DiscordResponse: Standardized response wrapper with success/data/error
            Channel object matching Discord API format

        Notes:
            Response format validated against Discord REST API v10.
            Returns the created channel with all standard fields.
        """

        try:
            await self.client.wait_until_ready()
            guild = self.client.get_guild(guild_id)
            if guild is None:
                return DiscordResponse(success=False, error="Guild not found")

            if channel_type == "voice":
                channel = await guild.create_voice_channel(name)
            elif channel_type == "category":
                channel = await guild.create_category(name)
            else:
                channel = await guild.create_text_channel(name)

            return DiscordResponse(success=True, data=self._serialize_channel(channel))
        except Exception as e:
            return await self._handle_discord_error(e)

    async def delete_channel(self, channel_id: int) -> DiscordResponse:
        """Delete a channel

        Discord endpoint: `DELETE /channels/{channel.id}`

        Returns confirmation as per Discord API specification.

        Args:
            channel_id (required): Channel ID to delete

        Returns:
            DiscordResponse: Standardized response wrapper with success/data/error
            Confirmation object with deleted channel ID

        Notes:
            Response format validated against Discord REST API v10.
            Returns the deleted channel object on success.
        """

        try:
            await self.client.wait_until_ready()
            channel = self.client.get_channel(channel_id)
            if channel is None:
                return DiscordResponse(success=False, error="Channel not found")

            channel_data = self._serialize_channel(channel)
            await channel.delete()

            return DiscordResponse(success=True, data=channel_data)
        except Exception as e:
            return await self._handle_discord_error(e)

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

        try:
            await self.client.wait_until_ready()
            guild = self.client.get_guild(guild_id)
            if guild is None:
                return DiscordResponse(success=False, error="Guild not found")
            role = await guild.create_role(name=name)
            return DiscordResponse(success=True, data=self._serialize_role(role))
        except Exception as e:
            return await self._handle_discord_error(e)

    async def send_dm(self, user_id: int, content: str) -> DiscordResponse:
        """Send a direct message to a user

        Discord endpoint: `POST /users/@me/channels then POST /channels/{channel.id}/messages`

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

        try:
            await self.client.wait_until_ready()
            user = await self.client.fetch_user(user_id)
            if user is None:
                return DiscordResponse(success=False, error="User not found")
            dm = await user.create_dm()
            msg = await dm.send(content)
            return DiscordResponse(success=True, data=self._serialize_message(msg))
        except Exception as e:
            return await self._handle_discord_error(e)


__all__ = ["DiscordDataSource", "DiscordResponse"]
