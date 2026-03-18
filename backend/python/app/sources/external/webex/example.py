# ruff: noqa
"""
Webex API Usage Examples

This example demonstrates how to use the Webex DataSource to interact with
the Webex API, covering:
- Authentication (Token or OAuth)
- Initializing the Client and DataSource
- Getting current user info
- Listing people, rooms, teams, meetings
- Listing messages in a room
- Listing organizations, webhooks, recordings

Prerequisites:
1. Create a Webex integration at https://developer.webex.com/my-apps
2. Set environment variables:
   - WEBEX_ACCESS_TOKEN: A valid Webex access token
   OR for OAuth:
   - WEBEX_CLIENT_ID: OAuth client ID
   - WEBEX_CLIENT_SECRET: OAuth client secret
   - WEBEX_REDIRECT_URI: OAuth redirect URI

API Reference: https://developer.webex.com/docs/api/getting-started
"""

import json
import os

from app.sources.client.webex.webex import (
    WebexClient,
    WebexTokenConfig,
    WebexResponse,
)
from app.sources.external.webex.webex import WebexDataSource


def print_section(title: str):
    print(f"\n{'-'*80}")
    print(f"| {title}")
    print(f"{'-'*80}")


def print_result(name: str, response: WebexResponse, show_data: bool = True):
    if response.success:
        print(f"  {name}: Success")
        if show_data and response.data:
            if isinstance(response.data, list):
                print(f"   Found {len(response.data)} items")
                if response.data:
                    print(f"   Sample: {json.dumps(response.data[0], indent=2, default=str)[:400]}...")
            elif isinstance(response.data, dict):
                print(f"   Data: {json.dumps(response.data, indent=2, default=str)[:500]}...")
    else:
        print(f"  {name}: Failed")
        print(f"   Error: {response.error}")
        if response.message:
            print(f"   Message: {response.message}")


def main() -> None:
    """Example usage of Webex API."""
    ACCESS_TOKEN = os.getenv("WEBEX_ACCESS_TOKEN")

    if not ACCESS_TOKEN:
        print("Please set WEBEX_ACCESS_TOKEN environment variable")
        print("   Get a token from https://developer.webex.com/docs/getting-started")
        return

    # Initialize Webex client
    print_section("Initializing Webex Client")
    print("  Using token authentication")
    config = WebexTokenConfig(access_token=ACCESS_TOKEN)
    client = WebexClient.build_with_config(config)
    data_source = WebexDataSource(client)
    print("  Client initialized successfully.")

    # 1. Get current user
    print_section("Current User")
    me_resp = data_source.get_me()
    print_result("Get Me", me_resp)

    # 2. List people
    print_section("People")
    people_resp = data_source.list_people(max_results=5)
    print_result("List People", people_resp)

    # 3. List rooms
    print_section("Rooms / Spaces")
    rooms_resp = data_source.list_rooms(max_results=5)
    print_result("List Rooms", rooms_resp)

    # If we have rooms, list messages from the first one
    if rooms_resp.success and rooms_resp.data and isinstance(rooms_resp.data, list):
        if rooms_resp.data:
            first_room = rooms_resp.data[0]
            room_id = first_room.get("id", "") if isinstance(first_room, dict) else ""
            if room_id:
                print_section(f"Messages in Room: {room_id[:20]}...")
                messages_resp = data_source.list_messages(
                    room_id=room_id, max_results=5
                )
                print_result("List Messages", messages_resp)

    # 4. List teams
    print_section("Teams")
    teams_resp = data_source.list_teams(max_results=5)
    print_result("List Teams", teams_resp)

    # 5. List meetings
    print_section("Meetings")
    meetings_resp = data_source.list_meetings(max_results=5)
    print_result("List Meetings", meetings_resp)

    # 6. List organizations
    print_section("Organizations")
    orgs_resp = data_source.list_organizations()
    print_result("List Organizations", orgs_resp)

    # 7. List webhooks
    print_section("Webhooks")
    webhooks_resp = data_source.list_webhooks(max_results=5)
    print_result("List Webhooks", webhooks_resp)

    # 8. List recordings
    print_section("Recordings")
    recordings_resp = data_source.list_recordings(max_results=5)
    print_result("List Recordings", recordings_resp)

    print("\n" + "=" * 80)
    print("  All Webex API operations tested!")
    print("=" * 80)


if __name__ == "__main__":
    main()
