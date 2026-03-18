# ruff: noqa

"""
Highspot API Usage Examples

This example demonstrates how to use the Highspot DataSource to interact with
the Highspot API, covering:
- Authentication (OAuth2 or Bearer Token)
- Initializing the Client and DataSource
- Listing Spots, Items, Pitches, Groups, Users
- Fetching analytics

Prerequisites:
For OAuth2:
1. Register an OAuth app with Highspot
2. Set HIGHSPOT_CLIENT_ID and HIGHSPOT_CLIENT_SECRET environment variables

For Bearer Token:
1. Set HIGHSPOT_ACCESS_TOKEN environment variable
"""

import asyncio
import json
import os

from app.sources.client.highspot.highspot import (
    HighspotClient,
    HighspotOAuthConfig,
    HighspotTokenConfig,
    HighspotResponse,
)
from app.sources.external.highspot.highspot import HighspotDataSource
from app.sources.external.utils.oauth import perform_oauth_flow

# --- Configuration ---
CLIENT_ID = os.getenv("HIGHSPOT_CLIENT_ID")
CLIENT_SECRET = os.getenv("HIGHSPOT_CLIENT_SECRET")
ACCESS_TOKEN = os.getenv("HIGHSPOT_ACCESS_TOKEN")
REDIRECT_URI = os.getenv("HIGHSPOT_REDIRECT_URI", "http://localhost:8080/callback")


def print_section(title: str):
    print(f"\n{'-'*80}")
    print(f"| {title}")
    print(f"{'-'*80}")


def print_result(name: str, response: HighspotResponse, show_data: bool = True):
    if response.success:
        print(f"  {name}: Success")
        if show_data and response.data:
            data = response.data
            for key in ("spots", "items", "pitches", "groups", "users", "results"):
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
    print_section("Initializing Highspot Client")

    config = None

    # Priority 1: OAuth2
    if CLIENT_ID and CLIENT_SECRET:
        print("  Using OAuth2 authentication")
        try:
            token_response = perform_oauth_flow(
                client_id=CLIENT_ID,
                client_secret=CLIENT_SECRET,
                auth_endpoint="https://app.highspot.com/oauth2/authorize",
                token_endpoint="https://app.highspot.com/oauth2/token",
                redirect_uri=REDIRECT_URI,
                scopes=[],
                scope_delimiter=" ",
                auth_method="header",
            )
            access_token = token_response.get("access_token")
            if not access_token:
                raise Exception("No access_token found in OAuth response")

            config = HighspotOAuthConfig(
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
        config = HighspotTokenConfig(token=ACCESS_TOKEN)

    if config is None:
        print("  No valid authentication method found.")
        print("   Please set one of the following:")
        print("   - HIGHSPOT_CLIENT_ID and HIGHSPOT_CLIENT_SECRET (for OAuth2)")
        print("   - HIGHSPOT_ACCESS_TOKEN (for Bearer Token)")
        return

    client = HighspotClient.build_with_config(config)
    data_source = HighspotDataSource(client)
    print("Client initialized successfully.")

    try:
        # 2. List Spots
        print_section("Spots")
        spots_resp = await data_source.list_spots(page=1, per_page=10)
        print_result("List Spots", spots_resp)

        # 3. List Items
        print_section("Items")
        items_resp = await data_source.list_items(page=1, per_page=10)
        print_result("List Items", items_resp)

        # 4. List Pitches
        print_section("Pitches")
        pitches_resp = await data_source.list_pitches(page=1, per_page=10)
        print_result("List Pitches", pitches_resp)

        # 5. List Groups
        print_section("Groups")
        groups_resp = await data_source.list_groups(page=1, per_page=10)
        print_result("List Groups", groups_resp)

        # 6. List Users
        print_section("Users")
        users_resp = await data_source.list_users(page=1, per_page=10)
        print_result("List Users", users_resp)

        # 7. Content Analytics
        print_section("Content Analytics")
        content_analytics_resp = await data_source.get_content_analytics()
        print_result("Content Analytics", content_analytics_resp)

        # 8. Engagement Analytics
        print_section("Engagement Analytics")
        engagement_resp = await data_source.get_engagement_analytics()
        print_result("Engagement Analytics", engagement_resp)

    finally:
        print("\nClosing client connection...")
        inner_client = client.get_client()
        if hasattr(inner_client, "close"):
            await inner_client.close()

    print("\n" + "=" * 80)
    print("  All Highspot API operations tested!")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
