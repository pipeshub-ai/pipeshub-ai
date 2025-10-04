"""Discord Data Source Example"""

import asyncio
import contextlib
import os

from app.sources.client.discord.discord import DiscordClient, DiscordTokenConfig
from app.sources.external.discord.discord import DiscordDataSource


async def main() -> None:
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        raise Exception("DISCORD_BOT_TOKEN is not set")

    discord_client = DiscordClient.build_with_config(DiscordTokenConfig(token=token))
    discord_data_source = DiscordDataSource(discord_client)
    client = discord_client.get_discord_client()

    done_event = asyncio.Event()

    @client.event
    async def on_ready() -> None:
        print(f"Bot: {client.user}")
        print()

        guilds = await discord_data_source.get_guilds()
        if guilds.success:
            print(f"Guilds: {guilds.data.get('count')}")
            for g in guilds.data.get("items", [])[:3]:
                print(f"  {g['name']} (ID: {g['id']})")
                print(f"    Owner: {g['owner']}, Features: {g['features']}")
        print()

        if guilds.success and guilds.data.get("items"):
            gid = int(guilds.data["items"][0]["id"])

            guild = await discord_data_source.get_guild(gid)
            if guild.success:
                gdata = guild.data
                print("Guild Details:")
                print(f"  Name: {gdata['name']}")
                print(f"  Owner ID: {gdata['owner_id']}")
                print(f"  Members: {gdata['max_members']}")
                print(f"  Premium Tier: {gdata['premium_tier']}")
                print(f"  Verification: {gdata['verification_level']}")
            print()

            channels = await discord_data_source.get_channels(gid, "text")
            if channels.success:
                print(f"Text Channels: {channels.data.get('count')}")
                for c in channels.data.get("items", [])[:3]:
                    print(f"  #{c['name']} (ID: {c['id']})")
            print()

            if channels.success and channels.data.get("items"):
                cid = int(channels.data["items"][0]["id"])

                messages = await discord_data_source.get_messages(cid, limit=3)
                if messages.success:
                    print(f"Messages: {messages.data.get('count')}")
                    for msg in messages.data.get("items", []):
                        author = msg["author"]["username"]
                        content = msg["content"][:50]
                        timestamp = msg["timestamp"][:19]
                        print(f"  [{timestamp}] {author}: {content}")
                print()

            members = await discord_data_source.get_members(gid, 5)
            if members.success:
                print(f"Members: {members.data.get('count')}")
                for m in members.data.get("items", []):
                    user = m["user"]
                    nick = m.get("nick", "")
                    name = nick if nick else user["username"]
                    bot = "(bot)" if user["bot"] else ""
                    print(f"  {name} {bot}")
            print()

            roles = await discord_data_source.get_guild_roles(gid)
            if roles.success:
                print(f"Roles: {roles.data.get('count')}")
                for r in roles.data.get("items", [])[:5]:
                    perms = r["permissions"]
                    print(f"  {r['name']} (perms: {perms[:10]}...)")
            print()

            user_response = await discord_data_source.get_user(client.user.id)
            if user_response.success:
                user = user_response.data
                print("Bot User Info:")
                print(f"  Username: {user['username']}")
                print(f"  ID: {user['id']}")
                print(f"  Bot: {user['bot']}")
                print(f"  Discriminator: {user['discriminator']}")
            print()

        print("Done")
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
    asyncio.run(main())
