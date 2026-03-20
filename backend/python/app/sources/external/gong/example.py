# ruff: noqa

"""
Gong API Usage Examples

This example demonstrates how to use the Gong DataSource to interact with
the Gong REST API (v2), covering:
- Authentication (OAuth2, Basic Auth)
- Initializing the Client and DataSource
- Fetching User Details
- Listing Calls and Call Details
- Getting Activity Stats

Prerequisites:
For OAuth2:
1. Create a Gong integration at Admin > Settings > Ecosystem > API
2. Set GONG_CLIENT_ID and GONG_CLIENT_SECRET environment variables
3. The OAuth flow will automatically open a browser for authorization

For Basic Auth:
1. Generate API credentials in Admin > Settings > Ecosystem > API
2. Set GONG_ACCESS_KEY and GONG_ACCESS_KEY_SECRET environment variables

OAuth2 Scopes (space-delimited):
api:calls:read:basic api:calls:read:extensive api:users:read
api:stats:user-actions:read api:crm:read api:settings:read
api:meetings:user:read api:library:read

Note: Gong OAuth token response includes `api_base_url_for_customer`
which is the per-customer API base URL. This must be used for all
subsequent API requests.
"""

import asyncio
import json
import os

from app.sources.client.gong.gong import (
    GongBasicAuthConfig,
    GongClient,
    GongOAuthConfig,
    GongResponse,
)
from app.sources.external.gong.gong import GongDataSource
from app.sources.external.utils.oauth import perform_oauth_flow

# --- Configuration ---
# OAuth2 credentials (highest priority)
CLIENT_ID = os.getenv("GONG_CLIENT_ID")
CLIENT_SECRET = os.getenv("GONG_CLIENT_SECRET")

# Basic Auth credentials (second priority)
ACCESS_KEY = os.getenv("GONG_ACCESS_KEY")
ACCESS_KEY_SECRET = os.getenv("GONG_ACCESS_KEY_SECRET")

# OAuth redirect URI
REDIRECT_URI = os.getenv("GONG_REDIRECT_URI", "http://localhost:8080/callback")

# Custom base URL (per-customer, set after OAuth or via env)
BASE_URL = os.getenv("GONG_BASE_URL", "https://api.gong.io/v2")


def print_section(title: str):
    print(f"\n{'-'*80}")
    print(f"| {title}")
    print(f"{'-'*80}")


def print_result(name: str, response: GongResponse, show_data: bool = True):
    if response.success:
        print(f"  {name}: Success")
        if show_data and response.data:
            data = response.data
            # Handle common Gong response patterns
            for key in ("users", "calls", "records", "scorecards",
                        "trackers", "workspaces", "meetings", "flows"):
                if isinstance(data, dict) and key in data:
                    items = data[key]
                    if isinstance(items, list):
                        print(f"   Found {len(items)} {key}.")
                        if items:
                            print(f"   Sample: {json.dumps(items[0], indent=2, default=str)[:400]}...")
                    return
            # Generic response
            print(f"   Data: {json.dumps(data, indent=2, default=str)[:500]}...")
    else:
        print(f"  {name}: Failed")
        print(f"   Error: {response.error}")
        if response.message:
            print(f"   Message: {response.message}")


async def main() -> None:
    # 1. Initialize Client
    print_section("Initializing Gong Client")

    config = None
    base_url = BASE_URL

    # Priority 1: OAuth2
    if CLIENT_ID and CLIENT_SECRET:
        print("  Using OAuth2 authentication")
        try:
            print("Starting OAuth flow...")
            # Gong uses Basic Auth (header method) for token exchange
            # Auth URL: https://app.gong.io/oauth2/authorize
            # Token URL: https://app.gong.io/oauth2/generate-customer-token
            token_response = perform_oauth_flow(
                client_id=CLIENT_ID,
                client_secret=CLIENT_SECRET,
                auth_endpoint="https://app.gong.io/oauth2/authorize",
                token_endpoint="https://app.gong.io/oauth2/generate-customer-token",
                redirect_uri=REDIRECT_URI,
                scopes=[
                    "api:calls:read:basic",
                    "api:calls:read:extensive",
                    "api:users:read",
                    "api:stats:user-actions:read",
                    "api:crm:read",
                    "api:settings:read",
                    "api:meetings:user:read",
                    "api:library:read",
                ],
                scope_delimiter=" ",
                auth_method="header",
            )

            access_token = token_response.get("access_token")
            if not access_token:
                raise Exception("No access_token found in OAuth response")

            # Gong returns a per-customer base URL
            customer_base = token_response.get("api_base_url_for_customer")
            if customer_base:
                base_url = customer_base.rstrip("/") + "/v2"
                print(f"  Using customer API base: {base_url}")

            config = GongOAuthConfig(
                access_token=access_token,
                client_id=CLIENT_ID,
                client_secret=CLIENT_SECRET,
                base_url=base_url,
            )
            print("  OAuth authentication successful")
        except Exception as e:
            print(f"  OAuth flow failed: {e}")
            print("  Falling back to other authentication methods...")

    # Priority 2: Basic Auth
    if config is None and ACCESS_KEY and ACCESS_KEY_SECRET:
        print("  Using Basic Auth authentication")
        config = GongBasicAuthConfig(
            access_key=ACCESS_KEY,
            access_key_secret=ACCESS_KEY_SECRET,
            base_url=base_url,
        )

    if config is None:
        print("  No valid authentication method found.")
        print("   Please set one of the following:")
        print("   - GONG_CLIENT_ID and GONG_CLIENT_SECRET (for OAuth2)")
        print("   - GONG_ACCESS_KEY and GONG_ACCESS_KEY_SECRET (for Basic Auth)")
        return

    client = GongClient.build_with_config(config)
    data_source = GongDataSource(client)
    print("Client initialized successfully.")

    try:
        # 2. List Users
        print_section("Users")
        users_resp = await data_source.list_users()
        print_result("List Users", users_resp)

        # Extract first user for further operations
        user_id = None
        if users_resp.success and users_resp.data:
            users = users_resp.data.get("users", [])
            if users:
                user_id = str(users[0].get("id"))
                print(f"   Using User: {users[0].get('firstName', '')} "
                      f"{users[0].get('lastName', '')} (ID: {user_id})")

        if user_id:
            # 3. Get User Details
            print_section("User Details")
            user_resp = await data_source.get_user(id=user_id)
            print_result("Get User", user_resp)

        # 4. List Calls
        print_section("Calls")
        calls_resp = await data_source.list_calls()
        print_result("List Calls", calls_resp)

        # Extract first call for details
        call_id = None
        if calls_resp.success and calls_resp.data:
            calls = calls_resp.data.get("calls", [])
            if calls:
                call_id = str(calls[0].get("id"))
                print(f"   Using Call ID: {call_id}")

        if call_id:
            # 5. Get Call Details
            print_section("Call Details")
            call_resp = await data_source.get_call(id=call_id)
            print_result("Get Call", call_resp)

        # 6. List Scorecards
        print_section("Scorecards")
        sc_resp = await data_source.list_scorecards()
        print_result("List Scorecards", sc_resp)

        # 7. List Workspaces
        print_section("Workspaces")
        ws_resp = await data_source.list_workspaces()
        print_result("List Workspaces", ws_resp)

        # 8. Get Aggregate Activity Stats
        if user_id:
            print_section("Activity Stats")
            stats_resp = await data_source.get_aggregate_activity(
                filter={"fromDate": "2024-01-01", "toDate": "2024-12-31"}
            )
            print_result("Get Aggregate Activity", stats_resp)

    finally:
        # Cleanup: Close the HTTP client session
        print("\nClosing client connection...")
        inner_client = client.get_client()
        if hasattr(inner_client, "close"):
            await inner_client.close()

    print("\n" + "=" * 80)
    print("  All Gong API operations tested!")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
