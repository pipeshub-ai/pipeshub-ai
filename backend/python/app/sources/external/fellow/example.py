# ruff: noqa

"""
Fellow API Usage Examples

This example demonstrates how to use the Fellow DataSource to interact with
the Fellow API, covering:
- Authentication (OAuth2, API Key)
- Initializing the Client and DataSource
- Listing Meetings, Users, Streams
- Getting meeting notes and action items
- Getting feedback and objectives

Prerequisites:
For OAuth2:
1. Register an OAuth app with Fellow
2. Set FELLOW_CLIENT_ID and FELLOW_CLIENT_SECRET environment variables

For API Key:
1. Get your Fellow API key
2. Set FELLOW_API_KEY environment variable
"""

import asyncio
import json
import os

from app.sources.client.fellow.fellow import (
    FellowClient,
    FellowOAuthConfig,
    FellowResponse,
    FellowTokenConfig,
)
from app.sources.external.fellow.fellow import FellowDataSource
from app.sources.external.utils.oauth import perform_oauth_flow

# --- Configuration ---
# OAuth2 credentials (highest priority)
CLIENT_ID = os.getenv("FELLOW_CLIENT_ID")
CLIENT_SECRET = os.getenv("FELLOW_CLIENT_SECRET")

# API Key (second priority)
API_KEY = os.getenv("FELLOW_API_KEY")

# OAuth redirect URI
REDIRECT_URI = os.getenv("FELLOW_REDIRECT_URI", "http://localhost:8080/callback")


def print_section(title: str):
    print(f"\n{'-'*80}")
    print(f"| {title}")
    print(f"{'-'*80}")


def print_result(name: str, response: FellowResponse, show_data: bool = True):
    if response.success:
        print(f"  {name}: Success")
        if show_data and response.data:
            data = response.data
            for key in ("meetings", "users", "streams", "feedback",
                        "objectives", "one_on_ones", "notes", "action_items",
                        "results", "items"):
                if isinstance(data, dict) and key in data:
                    items = data[key]
                    print(f"   Found {len(items)} {key}.")
                    if items:
                        print(f"   Sample: {json.dumps(items[0], indent=2)[:400]}...")
                    return
            if isinstance(data, list):
                print(f"   Found {len(data)} items.")
                if data:
                    print(f"   Sample: {json.dumps(data[0], indent=2)[:400]}...")
                return
            print(f"   Data: {json.dumps(data, indent=2)[:500]}...")
    else:
        print(f"  {name}: Failed")
        print(f"   Error: {response.error}")
        if response.message:
            print(f"   Message: {response.message}")


async def main() -> None:
    # 1. Initialize Client
    print_section("Initializing Fellow Client")

    config = None

    # Priority 1: OAuth2
    if CLIENT_ID and CLIENT_SECRET:
        print("  Using OAuth2 authentication")
        try:
            token_response = perform_oauth_flow(
                client_id=CLIENT_ID,
                client_secret=CLIENT_SECRET,
                auth_endpoint="https://fellow.app/oauth/authorize",
                token_endpoint="https://fellow.app/oauth/token",
                redirect_uri=REDIRECT_URI,
                scopes=[],
                scope_delimiter=" ",
                auth_method="body",
            )
            access_token = token_response.get("access_token")
            if not access_token:
                raise Exception("No access_token found in OAuth response")

            config = FellowOAuthConfig(
                access_token=access_token,
                client_id=CLIENT_ID,
                client_secret=CLIENT_SECRET,
            )
            print("  OAuth authentication successful")
        except Exception as e:
            print(f"  OAuth flow failed: {e}")
            print("  Falling back to other authentication methods...")

    # Priority 2: API Key
    if config is None and API_KEY:
        print("  Using API Key authentication")
        config = FellowTokenConfig(token=API_KEY)

    if config is None:
        print("  No valid authentication method found.")
        print("   Please set one of the following:")
        print("   - FELLOW_CLIENT_ID and FELLOW_CLIENT_SECRET (for OAuth2)")
        print("   - FELLOW_API_KEY (for API Key)")
        return

    client = FellowClient.build_with_config(config)
    data_source = FellowDataSource(client)
    print("Client initialized successfully.")

    try:
        # 2. List Meetings
        print_section("Meetings")
        meetings_resp = await data_source.list_meetings(limit=10)
        print_result("List Meetings", meetings_resp)

        # Get meeting details if available
        meeting_id = None
        if meetings_resp.success and meetings_resp.data:
            meetings = (meetings_resp.data.get("meetings", [])
                       if isinstance(meetings_resp.data, dict) else meetings_resp.data)
            if isinstance(meetings, list) and meetings:
                meeting_id = str(meetings[0].get("id") if isinstance(meetings[0], dict) else meetings[0])

        if meeting_id:
            # 3. Get Meeting Notes
            print_section("Meeting Notes")
            notes_resp = await data_source.get_meeting_notes(meeting_id=meeting_id)
            print_result("Get Meeting Notes", notes_resp)

            # 4. Get Meeting Action Items
            print_section("Meeting Action Items")
            actions_resp = await data_source.get_meeting_action_items(meeting_id=meeting_id)
            print_result("Get Meeting Action Items", actions_resp)

        # 5. List Users
        print_section("Users")
        users_resp = await data_source.list_users(limit=10)
        print_result("List Users", users_resp)

        # 6. List Streams
        print_section("Streams")
        streams_resp = await data_source.list_streams(limit=10)
        print_result("List Streams", streams_resp)

        # 7. List Feedback
        print_section("Feedback")
        feedback_resp = await data_source.list_feedback(limit=10)
        print_result("List Feedback", feedback_resp)

        # 8. List Objectives
        print_section("Objectives")
        objectives_resp = await data_source.list_objectives(limit=10)
        print_result("List Objectives", objectives_resp)

        # 9. List One-on-Ones
        print_section("One-on-Ones")
        one_on_ones_resp = await data_source.list_one_on_ones(limit=10)
        print_result("List One-on-Ones", one_on_ones_resp)

    finally:
        print("\nClosing client connection...")
        inner_client = client.get_client()
        if hasattr(inner_client, "close"):
            await inner_client.close()

    print("\n" + "=" * 80)
    print("  All Fellow API operations tested!")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
