# ruff: noqa

"""
Simpplr API Usage Examples

This example demonstrates how to use the Simpplr DataSource to interact with
the Simpplr API, covering:
- Authentication (OAuth2, Bearer Token)
- Initializing the Client and DataSource
- Listing Sites, Content, Users, Pages
- Searching content
- Getting analytics

Prerequisites:
For OAuth2:
1. Register an OAuth app with Simpplr
2. Set SIMPPLR_CLIENT_ID and SIMPPLR_CLIENT_SECRET environment variables

For Bearer Token:
1. Get your Simpplr API token
2. Set SIMPPLR_TOKEN environment variable
"""

import asyncio
import json
import os

from app.sources.client.simpplr.simpplr import (
    SimpplrClient,
    SimpplrOAuthConfig,
    SimpplrResponse,
    SimpplrTokenConfig,
)
from app.sources.external.simpplr.simpplr import SimpplrDataSource
from app.sources.external.utils.oauth import perform_oauth_flow

# --- Configuration ---
# OAuth2 credentials (highest priority)
CLIENT_ID = os.getenv("SIMPPLR_CLIENT_ID")
CLIENT_SECRET = os.getenv("SIMPPLR_CLIENT_SECRET")

# Bearer Token (second priority)
TOKEN = os.getenv("SIMPPLR_TOKEN")

# OAuth redirect URI
REDIRECT_URI = os.getenv("SIMPPLR_REDIRECT_URI", "http://localhost:8080/callback")


def print_section(title: str):
    print(f"\n{'-'*80}")
    print(f"| {title}")
    print(f"{'-'*80}")


def print_result(name: str, response: SimpplrResponse, show_data: bool = True):
    if response.success:
        print(f"  {name}: Success")
        if show_data and response.data:
            data = response.data
            for key in ("sites", "content", "users", "pages", "events",
                        "newsletters", "results"):
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
    print_section("Initializing Simpplr Client")

    config = None

    # Priority 1: OAuth2
    if CLIENT_ID and CLIENT_SECRET:
        print("  Using OAuth2 authentication")
        try:
            token_response = perform_oauth_flow(
                client_id=CLIENT_ID,
                client_secret=CLIENT_SECRET,
                auth_endpoint="https://api.simpplr.com/oauth/authorize",
                token_endpoint="https://api.simpplr.com/oauth/token",
                redirect_uri=REDIRECT_URI,
                scopes=[],
                scope_delimiter=" ",
                auth_method="body",
            )
            access_token = token_response.get("access_token")
            if not access_token:
                raise Exception("No access_token found in OAuth response")

            config = SimpplrOAuthConfig(
                access_token=access_token,
                client_id=CLIENT_ID,
                client_secret=CLIENT_SECRET,
            )
            print("  OAuth authentication successful")
        except Exception as e:
            print(f"  OAuth flow failed: {e}")
            print("  Falling back to other authentication methods...")

    # Priority 2: Bearer Token
    if config is None and TOKEN:
        print("  Using Bearer Token authentication")
        config = SimpplrTokenConfig(token=TOKEN)

    if config is None:
        print("  No valid authentication method found.")
        print("   Please set one of the following:")
        print("   - SIMPPLR_CLIENT_ID and SIMPPLR_CLIENT_SECRET (for OAuth2)")
        print("   - SIMPPLR_TOKEN (for Bearer Token)")
        return

    client = SimpplrClient.build_with_config(config)
    data_source = SimpplrDataSource(client)
    print("Client initialized successfully.")

    try:
        # 2. List Sites
        print_section("Sites")
        sites_resp = await data_source.list_sites(limit=10)
        print_result("List Sites", sites_resp)

        # 3. List Content
        print_section("Content")
        content_resp = await data_source.list_content(limit=10)
        print_result("List Content", content_resp)

        # 4. List Users
        print_section("Users")
        users_resp = await data_source.list_users(limit=10)
        print_result("List Users", users_resp)

        # 5. List Pages
        print_section("Pages")
        pages_resp = await data_source.list_pages(limit=10)
        print_result("List Pages", pages_resp)

        # 6. List Events
        print_section("Events")
        events_resp = await data_source.list_events(limit=10)
        print_result("List Events", events_resp)

        # 7. Search
        print_section("Search")
        search_resp = await data_source.search(q="getting started", limit=10)
        print_result("Search", search_resp)

        # 8. Content Analytics
        print_section("Content Analytics")
        analytics_resp = await data_source.get_content_analytics()
        print_result("Content Analytics", analytics_resp)

    finally:
        print("\nClosing client connection...")
        inner_client = client.get_client()
        if hasattr(inner_client, "close"):
            await inner_client.close()

    print("\n" + "=" * 80)
    print("  All Simpplr API operations tested!")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
