"""Discord Data Source Example

This example demonstrates using the Discord REST API via HTTP calls.

Setup Requirements:
1. Create a Discord Bot at https://discord.com/developers/applications
2. Generate a bot token and set it as DISCORD_BOT_TOKEN environment variable
3. Invite the bot to your server with appropriate permissions

Optional - For full member list access:
- Enable SERVER MEMBERS INTENT in Discord Developer Portal Bot settings
- This allows get_members() to return all members up to the specified limit

Usage:
    export DISCORD_BOT_TOKEN="your_bot_token_here"
    python3 -m app.sources.external.discord.example
"""

import asyncio
import os

from app.sources.client.discord.discord import DiscordClient, DiscordTokenConfig
from app.sources.external.discord.discord import DiscordDataSource

# Constants
MESSAGE_DISPLAY_LIMIT = 5


async def main() -> None:
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        raise ValueError("DISCORD_BOT_TOKEN is not set")

    # Create client with HTTP-based implementation
    discord_client = DiscordClient.build_with_config(DiscordTokenConfig(token=token))
    discord_data_source = DiscordDataSource(discord_client)

    print("Fetching Discord data via REST API...")
    print()

    # Get all guilds
    guilds = await discord_data_source.get_guilds()
    if guilds.success:
        guild_list = guilds.data if isinstance(guilds.data, list) else []
        print(f"Guilds: {len(guild_list)}")
        for g in guild_list[:3]:
            print(f"  {g['name']} (ID: {g['id']})")
            print(
                f"    Owner: {g.get('owner', False)}, Features: {g.get('features', [])}"
            )
    else:
        print(f"Error fetching guilds: {guilds.error}")
    print()

    # If we have guilds, explore the first one
    if guilds.success and isinstance(guilds.data, list) and len(guilds.data) > 0:
        gid = int(guilds.data[0]["id"])

        # Get guild details
        guild = await discord_data_source.get_guild(gid)
        if guild.success:
            gdata = guild.data
            print("Guild Details:")
            print(f"  Name: {gdata.get('name')}")
            print(f"  Owner ID: {gdata.get('owner_id')}")
            print(f"  Member Count: {gdata.get('approximate_member_count', 'N/A')}")
            print(f"  Max Members: {gdata.get('max_members', 'N/A')}")
            print(f"  Premium Tier: {gdata.get('premium_tier', 0)}")
            print(f"  Verification: {gdata.get('verification_level', 0)}")
        else:
            print(f"Error fetching guild: {guild.error}")
        print()

        # Get text channels
        channels = await discord_data_source.get_channels(gid, "text")
        if channels.success:
            channel_list = channels.data if isinstance(channels.data, list) else []
            print(f"Text Channels: {len(channel_list)}")
            for c in channel_list[:3]:
                print(f"  #{c['name']} (ID: {c['id']})")
        else:
            print(f"Error fetching channels: {channels.error}")
        print()

        # Get messages from first text channel
        if (
            channels.success
            and isinstance(channels.data, list)
            and len(channels.data) > 0
        ):
            cid = int(channels.data[0]["id"])

            messages = await discord_data_source.get_messages(cid, limit=10)
            if messages.success:
                message_list = messages.data if isinstance(messages.data, list) else []
                print(f"Messages (up to 10): {len(message_list)}")
                for msg in message_list[:MESSAGE_DISPLAY_LIMIT]:  # Show first N for brevity
                    author = msg.get("author", {}).get("username", "Unknown")
                    content = msg.get("content", "")[:50]
                    timestamp = msg.get("timestamp", "")[:19]
                    print(f"  [{timestamp}] {author}: {content}")
                if len(message_list) > MESSAGE_DISPLAY_LIMIT:
                    print(
                        f"  ... and {len(message_list) - MESSAGE_DISPLAY_LIMIT} more messages"
                    )
            else:
                print(f"Error fetching messages: {messages.error}")
            print()

        # Get members
        print(f"Fetching members from guild '{guilds.data[0]['name']}'...")
        print("-" * 80)
        members = await discord_data_source.get_members(gid, 5)
        if members.success:
            member_list = members.data if isinstance(members.data, list) else []
            print(f"Success! Found {len(member_list)} members (limited to 5)")
            for idx, m in enumerate(member_list, 1):
                user = m.get("user", {})
                username = user.get("username", "Unknown")
                is_bot = user.get("bot", False)
                print(f"  {idx}. {username} - Bot: {is_bot}")
        else:
            print(f"Error fetching members: {members.error}")
        print()

        # Get roles
        roles = await discord_data_source.get_guild_roles(gid)
        if roles.success:
            role_list = roles.data if isinstance(roles.data, list) else []
            print(f"Roles: {len(role_list)}")
            for r in role_list[:5]:
                perms = r.get("permissions", "0")
                print(f"  {r.get('name', 'Unknown')} (perms: {perms[:10]}...)")
        else:
            print(f"Error fetching roles: {roles.error}")
        print()

    print("Done!")

    # Close the HTTP client
    await discord_client.get_client().close()


if __name__ == "__main__":
    asyncio.run(main())
