# ruff: noqa

"""
Guru API Usage Examples

This example demonstrates how to use the Guru DataSource to interact with
the Guru API, covering:
- Authentication (Basic Auth or OAuth2)
- Initializing the Client and DataSource
- Listing Cards, Boards, Collections, Groups
- Searching cards
- Getting team info and analytics

Prerequisites:
For Basic Auth:
1. Get your Guru username (email) and API token
2. Set GURU_USERNAME and GURU_API_TOKEN environment variables

For OAuth2:
1. Register an OAuth app with Guru
2. Set GURU_CLIENT_ID and GURU_CLIENT_SECRET environment variables
"""

import asyncio
import json
import os

from app.sources.client.guru.guru import (
    GuruClient,
    GuruBasicAuthConfig,
    GuruOAuthConfig,
    GuruResponse,
)
from app.sources.external.guru.guru import GuruDataSource
from app.sources.external.utils.oauth import perform_oauth_flow

# --- Configuration ---
# Basic Auth credentials
GURU_USERNAME = os.getenv("GURU_USERNAME")
GURU_API_TOKEN = os.getenv("GURU_API_TOKEN")

# OAuth2 credentials
CLIENT_ID = os.getenv("GURU_CLIENT_ID")
CLIENT_SECRET = os.getenv("GURU_CLIENT_SECRET")

# OAuth redirect URI
REDIRECT_URI = os.getenv("GURU_REDIRECT_URI", "http://localhost:8080/callback")


def print_section(title: str):
    print(f"\n{'-'*80}")
    print(f"| {title}")
    print(f"{'-'*80}")


def print_result(name: str, response: GuruResponse, show_data: bool = True):
    if response.success:
        print(f"  {name}: Success")
        if show_data and response.data:
            data = response.data
            for key in ("cards", "boards", "collections", "groups", "members",
                        "results"):
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
    print_section("Initializing Guru Client")

    config = None

    # Priority 1: OAuth2
    if CLIENT_ID and CLIENT_SECRET:
        print("  Using OAuth2 authentication")
        try:
            token_response = perform_oauth_flow(
                client_id=CLIENT_ID,
                client_secret=CLIENT_SECRET,
                auth_endpoint="https://api.getguru.com/oauth/authorize",
                token_endpoint="https://api.getguru.com/oauth/token",
                redirect_uri=REDIRECT_URI,
                scopes=[],
                scope_delimiter=" ",
                auth_method="header",
            )
            access_token = token_response.get("access_token")
            if not access_token:
                raise Exception("No access_token found in OAuth response")

            config = GuruOAuthConfig(
                access_token=access_token,
                client_id=CLIENT_ID,
                client_secret=CLIENT_SECRET,
            )
            print("  OAuth authentication successful")
        except Exception as e:
            print(f"  OAuth flow failed: {e}")
            print("  Falling back to other authentication methods...")

    # Priority 2: Basic Auth
    if config is None and GURU_USERNAME and GURU_API_TOKEN:
        print("  Using Basic Auth authentication")
        config = GuruBasicAuthConfig(
            username=GURU_USERNAME,
            api_token=GURU_API_TOKEN,
        )

    if config is None:
        print("  No valid authentication method found.")
        print("   Please set one of the following:")
        print("   - GURU_CLIENT_ID and GURU_CLIENT_SECRET (for OAuth2)")
        print("   - GURU_USERNAME and GURU_API_TOKEN (for Basic Auth)")
        return

    client = GuruClient.build_with_config(config)
    data_source = GuruDataSource(client)
    print("Client initialized successfully.")

    try:
        # 2. Get Team Info
        print_section("Team Info")
        team_resp = await data_source.get_team_info()
        print_result("Get Team Info", team_resp)

        # 3. List Cards
        print_section("Cards")
        cards_resp = await data_source.list_cards(page=1, per_page=10)
        print_result("List Cards", cards_resp)

        # 4. List Boards
        print_section("Boards")
        boards_resp = await data_source.list_boards()
        print_result("List Boards", boards_resp)

        # 5. List Collections
        print_section("Collections")
        colls_resp = await data_source.list_collections()
        print_result("List Collections", colls_resp)

        # 6. List Groups
        print_section("Groups")
        groups_resp = await data_source.list_groups()
        print_result("List Groups", groups_resp)

        # 7. Search Cards
        print_section("Search Cards")
        search_resp = await data_source.search_cards(search_terms="getting started")
        print_result("Search Cards", search_resp)

        # 8. Card Analytics
        print_section("Card Analytics")
        analytics_resp = await data_source.get_card_analytics()
        print_result("Card Analytics", analytics_resp)

    finally:
        print("\nClosing client connection...")
        inner_client = client.get_client()
        if hasattr(inner_client, "close"):
            await inner_client.close()

    print("\n" + "=" * 80)
    print("  All Guru API operations tested!")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
