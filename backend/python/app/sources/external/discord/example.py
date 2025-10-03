import asyncio
import contextlib
import os

from app.sources.client.discord.discord import DiscordClient, DiscordTokenConfig
from app.sources.external.discord.discord import DiscordDataSource


async def main():
    """Example usage of Discord client and data source"""

    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        raise Exception("DISCORD_BOT_TOKEN environment variable is not set")

    print("=" * 80)
    print("Discord Data Source Example")
    print("=" * 80)
    print()

    print("Step 1: Creating Discord client with bot token...")
    discord_client = DiscordClient.build_with_config(DiscordTokenConfig(token=token))
    print("✓ Discord client created successfully")
    print()

    print("Step 2: Initializing Discord data source...")
    discord_data_source = DiscordDataSource(discord_client)
    print("✓ Discord data source initialized")
    print(f"  Data source client ID: {id(discord_data_source.client)}")
    print()

    print("Step 3: Starting Discord client (this may take a moment)...")
    client = discord_client.get_discord_client()
    print(f"  Main client ID: {id(client)}")
    print()

    done_event = asyncio.Event()

    @client.event
    async def on_ready():
        print("✓ Discord bot is ready!")
        print(f"  Bot User: {client.user}")
        print(f"  Bot ID: {client.user.id}")
        print()

        print("Step 4: Fetching all guilds (servers)...")
        print("-" * 80)
        guilds_response = await discord_data_source.get_guilds()
        if guilds_response.success:
            print(f"Success! Found {guilds_response.data.get('count', 0)} guilds")
            for i, guild in enumerate(guilds_response.data.get("items", [])[:3], 1):
                print(f"  {i}. {guild['name']} (ID: {guild['id']})")
            print()
        else:
            print(f"Error: {guilds_response.error}")
            print()

        if guilds_response.success and guilds_response.data.get("items"):
            first_guild = guilds_response.data["items"][0]
            guild_id = int(first_guild["id"])

            print(f"Step 5: Fetching channels from guild '{first_guild['name']}'...")
            print("-" * 80)
            channels_response = await discord_data_source.get_channels(
                guild_id, channel_type="text"
            )
            if channels_response.success:
                print(
                    f"Success! Found {channels_response.data.get('count', 0)} text channels"
                )
                for i, channel in enumerate(
                    channels_response.data.get("items", [])[:5], 1
                ):
                    print(f"  {i}. #{channel['name']} (ID: {channel['id']})")
                print()

                if channels_response.data.get("items"):
                    first_channel = channels_response.data["items"][0]
                    channel_id = int(first_channel["id"])

                    print(
                        f"Step 6: Fetching messages from channel '#{first_channel['name']}'..."
                    )
                    print("-" * 80)
                    messages_response = await discord_data_source.get_messages(
                        channel_id, limit=5
                    )
                    if messages_response.success:
                        print(
                            f"Success! Found {messages_response.data.get('count', 0)} messages"
                        )
                        for i, message in enumerate(
                            messages_response.data.get("items", [])[:3], 1
                        ):
                            content = message.get("content", "")[:50]
                            author_name = message.get("author_name") or message.get("author_id") or message.get("id") or "Unknown"
                            print(
                                f"  {i}. [{author_name}]: {content}..."
                            )
                        print()
                    else:
                        print(f"Error: {messages_response.error}")
                        print()
            else:
                print(f"Error: {channels_response.error}")
                print()

            print(f"Step 7: Fetching members from guild '{first_guild['name']}'...")
            print("-" * 80)
            members_response = await discord_data_source.get_members(guild_id, limit=5)
            if members_response.success:
                print(
                    f"Success! Found {members_response.data.get('count', 0)} members (limited to 5)"
                )
                for i, member in enumerate(members_response.data.get("items", []), 1):
                    dn = member.get("display_name") or member.get("name")
                    print(
                        f"  {i}. {dn} - Bot: {member.get('bot')}"
                    )
                print()
            else:
                print(f"Error: {members_response.error}")
                print()

            print(f"Step 8: Fetching roles from guild '{first_guild['name']}'...")
            print("-" * 80)
            roles_response = await discord_data_source.get_guild_roles(guild_id)
            if roles_response.success:
                print(f"Success! Found {roles_response.data.get('count', 0)} roles")
                for i, role in enumerate(roles_response.data.get("items", [])[:5], 1):
                    print(f"  {i}. {role.get('name')}")
                print()
            else:
                print(f"Error: {roles_response.error}")
                print()

            print("Step 9: Demonstrating write - sending a test message...")
            print("-" * 80)
            test_message_response = await discord_data_source.send_message(channel_id, "Hello from pipeshub DiscordDataSource example!")
            if test_message_response.success:
                sent_id = test_message_response.data.get("id") or test_message_response.data.get("result") or test_message_response.data.get("message_id")
                print(f"Sent message ID: {sent_id}")
                print("Adding reaction to the sent message...")
                reaction_resp = await discord_data_source.add_reaction(channel_id, int(sent_id), "👍")
                if reaction_resp.success:
                    print("✓ Reaction added")
                else:
                    print(f"Failed to add reaction: {reaction_resp.error}")
            else:
                print(f"Failed to send message: {test_message_response.error}")
            print()

        print("=" * 80)
        print("Example completed successfully!")
        print("=" * 80)

        done_event.set()

    start_task = asyncio.create_task(client.start(token, reconnect=False))

    try:
        await done_event.wait()
    finally:
        if not client.is_closed():
            await client.close()
        with contextlib.suppress(asyncio.CancelledError):
            await start_task


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nExample interrupted by user")
    except Exception as e:
        print(f"\nError running example: {e}")
        import traceback

        traceback.print_exc()
