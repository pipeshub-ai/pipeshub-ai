# ruff: noqa

"""
Mattermost API Usage Examples

This example demonstrates how to use the Mattermost DataSource to interact with
the Mattermost API v4, covering:
- Authentication (Personal Access Token)
- Initializing the Client and DataSource
- Fetching User Details
- Listing Teams, Channels, Posts
- System health check

Prerequisites:
1. Set MATTERMOST_SERVER to your Mattermost server domain (e.g. "mattermost.example.com")
2. Set MATTERMOST_TOKEN to a personal access token
   (Generated via Account Settings > Security > Personal Access Tokens)
"""

import asyncio
import json
import os

from app.sources.client.mattermost.mattermost import (
    MattermostClient,
    MattermostResponse,
    MattermostTokenConfig,
)
from app.sources.external.mattermost.mattermost import MattermostDataSource

# --- Configuration ---
SERVER = os.getenv("MATTERMOST_SERVER", "")
TOKEN = os.getenv("MATTERMOST_TOKEN", "")


def print_section(title: str):
    print(f"\n{'-'*80}")
    print(f"| {title}")
    print(f"{'-'*80}")


def print_result(name: str, response: MattermostResponse, show_data: bool = True):
    if response.success:
        print(f"  {name}: Success")
        if show_data and response.data:
            data = response.data
            if isinstance(data, list):
                print(f"   Found {len(data)} items.")
                if data:
                    print(f"   Sample: {json.dumps(data[0], indent=2)[:400]}...")
            elif isinstance(data, dict):
                print(f"   Data: {json.dumps(data, indent=2)[:500]}...")
    else:
        print(f"  {name}: Failed")
        print(f"   Error: {response.error}")
        if response.message:
            print(f"   Message: {response.message}")


async def main() -> None:
    # 1. Initialize Client
    print_section("Initializing Mattermost Client")

    if not SERVER or not TOKEN:
        print("  No valid authentication found.")
        print("   Please set:")
        print("   - MATTERMOST_SERVER (e.g. mattermost.example.com)")
        print("   - MATTERMOST_TOKEN (personal access token)")
        return

    config = MattermostTokenConfig(token=TOKEN, server=SERVER)
    client = MattermostClient.build_with_config(config)
    data_source = MattermostDataSource(client)
    print(f"Client initialized for server: {SERVER}")

    try:
        # 2. System Health
        print_section("System Health")
        ping_resp = await data_source.ping()
        print_result("Ping", ping_resp)

        # 3. Get Current User
        print_section("Current User")
        me_resp = await data_source.get_me()
        print_result("Get Me", me_resp)

        user_id = None
        if me_resp.success and isinstance(me_resp.data, dict):
            user_id = str(me_resp.data.get("id", ""))
            print(f"   User: {me_resp.data.get('username')} (ID: {user_id})")

        # 4. Get Teams
        print_section("Teams")
        teams_resp = await data_source.get_teams()
        print_result("Get Teams", teams_resp)

        team_id = None
        if teams_resp.success and isinstance(teams_resp.data, list) and teams_resp.data:
            team_id = str(teams_resp.data[0].get("id", ""))
            print(f"   Using Team: {teams_resp.data[0].get('display_name')} (ID: {team_id})")

        if not team_id:
            print("   No teams found. Skipping further operations.")
            return

        # 5. Get Team Channels
        print_section("Team Channels")
        channels_resp = await data_source.get_team_channels(team_id)
        print_result("Get Team Channels", channels_resp)

        channel_id = None
        if channels_resp.success and isinstance(channels_resp.data, list) and channels_resp.data:
            channel_id = str(channels_resp.data[0].get("id", ""))
            print(f"   Using Channel: {channels_resp.data[0].get('display_name')} (ID: {channel_id})")

        # 6. Get Channel Posts
        if channel_id:
            print_section("Channel Posts")
            posts_resp = await data_source.get_channel_posts(channel_id, per_page=5)
            print_result("Get Channel Posts", posts_resp)

        # 7. Get Team Members
        print_section("Team Members")
        members_resp = await data_source.get_team_members(team_id, per_page=5)
        print_result("Get Team Members", members_resp)

        # 8. Get User Teams
        if user_id:
            print_section("User Teams")
            user_teams_resp = await data_source.get_user_teams(user_id)
            print_result("Get User Teams", user_teams_resp)

        # 9. Get Emoji
        print_section("Custom Emoji")
        emoji_resp = await data_source.get_emoji_list(per_page=5)
        print_result("Get Emoji List", emoji_resp)

    finally:
        # Cleanup
        print("\nClosing client connection...")
        inner_client = client.get_client()
        if hasattr(inner_client, "close"):
            await inner_client.close()

    print("\n" + "=" * 80)
    print("  All Mattermost API operations tested!")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
