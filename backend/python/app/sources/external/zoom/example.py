# ruff: noqa

"""
Zoom API Usage Examples

This example demonstrates how to use the Zoom DataSource to interact with
the Zoom API, covering:
- Authentication (OAuth2, Bearer Token, or Server-to-Server)
- Initializing the Client and DataSource
- Listing Users
- Getting User Info
- Listing Meetings
- Listing Groups

Prerequisites:
For OAuth2:
1. Create a Zoom OAuth app at https://marketplace.zoom.us/
2. Set ZOOM_CLIENT_ID and ZOOM_CLIENT_SECRET environment variables
3. The OAuth flow will automatically open a browser for authorization

For Bearer Token:
1. Set ZOOM_ACCESS_TOKEN environment variable with your access token

For Server-to-Server:
1. Create a Server-to-Server OAuth app at https://marketplace.zoom.us/
2. Set ZOOM_CLIENT_ID, ZOOM_CLIENT_SECRET, and ZOOM_ACCOUNT_ID environment variables
"""

import asyncio
import json
import os

from app.sources.client.zoom.zoom import (
    ZoomClient,
    ZoomOAuthConfig,
    ZoomTokenConfig,
    ZoomServerToServerConfig,
    ZoomResponse,
)
from app.sources.external.zoom.zoom import ZoomDataSource
from app.sources.external.utils.oauth import perform_oauth_flow

# --- Configuration ---
# OAuth2 credentials (highest priority)
CLIENT_ID = os.getenv("ZOOM_CLIENT_ID")
CLIENT_SECRET = os.getenv("ZOOM_CLIENT_SECRET")

# Bearer Token (second priority)
ACCESS_TOKEN = os.getenv("ZOOM_ACCESS_TOKEN")

# Server-to-Server Account ID (lowest priority, used with CLIENT_ID/SECRET)
ACCOUNT_ID = os.getenv("ZOOM_ACCOUNT_ID")

# OAuth redirect URI
REDIRECT_URI = os.getenv("ZOOM_REDIRECT_URI", "http://localhost:8080/callback")


def print_section(title: str):
    print(f"\n{'-'*80}")
    print(f"| {title}")
    print(f"{'-'*80}")


def print_result(name: str, response: ZoomResponse, show_data: bool = True):
    if response.success:
        print(f"  {name}: Success")
        if show_data and response.data:
            data = response.data
            # Handle list-type responses (users, meetings, groups, etc.)
            for key in ("users", "meetings", "groups", "registrants",
                        "participants", "recordings", "channels", "members"):
                if isinstance(data, dict) and key in data:
                    items = data[key]
                    print(f"   Found {len(items)} {key}.")
                    if items:
                        print(f"   Sample: {json.dumps(items[0], indent=2)[:400]}...")
                    return
            # Generic response
            print(f"   Data: {json.dumps(data, indent=2)[:500]}...")
    else:
        print(f"  {name}: Failed")
        print(f"   Error: {response.error}")
        if response.message:
            print(f"   Message: {response.message}")


async def main() -> None:
    # 1. Initialize Client
    print_section("Initializing Zoom Client")

    config = None

    # Priority 1: OAuth2
    if CLIENT_ID and CLIENT_SECRET and not ACCOUNT_ID:
        print("  Using OAuth2 authentication")
        try:
            print("Starting OAuth flow...")
            # Zoom OAuth authorization URL: https://zoom.us/oauth/authorize
            # Zoom token endpoint: https://zoom.us/oauth/token
            token_response = perform_oauth_flow(
                client_id=CLIENT_ID,
                client_secret=CLIENT_SECRET,
                auth_endpoint="https://zoom.us/oauth/authorize",
                token_endpoint="https://zoom.us/oauth/token",
                redirect_uri=REDIRECT_URI,
                scopes=[],  # Zoom doesn't require specific scopes in the auth URL
                scope_delimiter=" ",
                auth_method="header",  # Basic Auth with client_id:client_secret
            )

            access_token = token_response.get("access_token")
            if not access_token:
                raise Exception("No access_token found in OAuth response")

            config = ZoomOAuthConfig(
                access_token=access_token,
                client_id=CLIENT_ID,
                client_secret=CLIENT_SECRET,
            )
            print("  OAuth authentication successful")
        except Exception as e:
            print(f"  OAuth flow failed: {e}")
            print("  Falling back to other authentication methods...")

    # Priority 2: Bearer Token
    if config is None and ACCESS_TOKEN:
        print("  Using Bearer Token authentication")
        config = ZoomTokenConfig(
            access_token=ACCESS_TOKEN,
        )

    # Priority 3: Server-to-Server (uses CLIENT_ID, CLIENT_SECRET, and ACCOUNT_ID)
    if config is None and CLIENT_ID and CLIENT_SECRET and ACCOUNT_ID:
        print("  Using Server-to-Server authentication")
        config = ZoomServerToServerConfig(
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
            account_id=ACCOUNT_ID,
        )

    if config is None:
        print("  No valid authentication method found.")
        print("   Please set one of the following:")
        print("   - ZOOM_CLIENT_ID and ZOOM_CLIENT_SECRET (for OAuth2)")
        print("   - ZOOM_ACCESS_TOKEN (for Bearer Token)")
        print("   - ZOOM_CLIENT_ID, ZOOM_CLIENT_SECRET, and ZOOM_ACCOUNT_ID (for Server-to-Server)")
        return

    client = ZoomClient.build_with_config(config)
    data_source = ZoomDataSource(client)
    print("Client initialized successfully.")

    try:
        # 2. List Users
        print_section("Users")
        users_resp = await data_source.list_users()
        print_result("List Users", users_resp)

        # 3. Get User Info (use first user from list, or 'me')
        print_section("User Info")
        user_id = "me"
        if users_resp.success and users_resp.data:
            users = users_resp.data.get("users", [])
            if users:
                user_id = str(users[0].get("id", "me"))
                print(f"   Using User: {users[0].get('email', 'N/A')} (ID: {user_id})")

        user_info_resp = await data_source.get_user(user_id=user_id)
        print_result("Get User Info", user_info_resp)

        # 4. List Meetings
        print_section("Meetings")
        meetings_resp = await data_source.list_meetings(user_id=user_id)
        print_result("List Meetings", meetings_resp)

        # 5. List Groups
        print_section("Groups")
        groups_resp = await data_source.list_groups()
        print_result("List Groups", groups_resp)

    finally:
        # Cleanup: Close the HTTP client session
        print("\nClosing client connection...")
        inner_client = client.get_client()
        if hasattr(inner_client, "close"):
            await inner_client.close()

    print("\n" + "=" * 80)
    print("  All Zoom API operations tested!")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
