# ruff: noqa

"""
Interact (Interact Intranet) API Usage Examples

This example demonstrates how to use the Interact DataSource to interact with
the Interact API v1, covering:
- Authentication (OAuth2 authorization code, API Key / Bearer Token)
- Initializing the Client and DataSource
- Listing Users, Content, Pages, News
- Fetching Communities and Events
- Searching content

Prerequisites:
For OAuth2:
1. Register an OAuth2 application in Interact admin settings
2. Set INTERACT_CLIENT_ID and INTERACT_CLIENT_SECRET environment variables
3. The OAuth flow will automatically open a browser for authorization

For API Key:
1. Obtain an API key from Interact admin settings
2. Set INTERACT_API_KEY environment variable
"""

import asyncio
import json
import os

from app.sources.client.interact.interact import (
    InteractClient,
    InteractOAuthConfig,
    InteractTokenConfig,
    InteractResponse,
)
from app.sources.external.interact.interact import InteractDataSource
from app.sources.external.utils.oauth import perform_oauth_flow

# --- Configuration ---
CLIENT_ID = os.getenv("INTERACT_CLIENT_ID")
CLIENT_SECRET = os.getenv("INTERACT_CLIENT_SECRET")
API_KEY = os.getenv("INTERACT_API_KEY")
REDIRECT_URI = os.getenv("INTERACT_REDIRECT_URI", "http://localhost:8080/callback")


def print_section(title: str):
    print(f"\n{'-'*80}")
    print(f"| {title}")
    print(f"{'-'*80}")


def print_result(name: str, response: InteractResponse, show_data: bool = True):
    if response.success:
        print(f"  {name}: Success")
        if show_data and response.data:
            data = response.data
            if isinstance(data, list):
                print(f"   Found {len(data)} items.")
                if data:
                    print(f"   Sample: {json.dumps(data[0], indent=2)[:400]}...")
            elif isinstance(data, dict):
                for key in ("users", "content", "pages", "news",
                            "communities", "events", "results"):
                    if key in data:
                        items = data[key]
                        print(f"   Found {len(items)} {key}.")
                        if items:
                            print(f"   Sample: {json.dumps(items[0], indent=2)[:400]}...")
                        return
                print(f"   Data: {json.dumps(data, indent=2)[:500]}...")
    else:
        print(f"  {name}: Failed")
        print(f"   Error: {response.error}")
        if response.message:
            print(f"   Message: {response.message}")


async def main() -> None:
    # 1. Initialize Client
    print_section("Initializing Interact Client")

    config = None

    # Priority 1: OAuth2
    if CLIENT_ID and CLIENT_SECRET:
        print("  Using OAuth2 authentication")
        try:
            token_response = perform_oauth_flow(
                client_id=CLIENT_ID,
                client_secret=CLIENT_SECRET,
                auth_endpoint="https://app.interact-intranet.com/oauth/authorize",
                token_endpoint="https://app.interact-intranet.com/oauth/token",
                redirect_uri=REDIRECT_URI,
                scopes=[],
                scope_delimiter=" ",
                auth_method="body",
            )

            access_token = token_response.get("access_token")
            if not access_token:
                raise Exception("No access_token found in OAuth response")

            config = InteractOAuthConfig(
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
        config = InteractTokenConfig(token=API_KEY)

    if config is None:
        print("  No valid authentication method found.")
        print("   Please set one of the following:")
        print("   - INTERACT_CLIENT_ID and INTERACT_CLIENT_SECRET (for OAuth2)")
        print("   - INTERACT_API_KEY (for API Key)")
        return

    client = InteractClient.build_with_config(config)
    data_source = InteractDataSource(client)
    print("Client initialized successfully.")

    try:
        # 2. Get Users
        print_section("Users")
        users_resp = await data_source.get_users(limit=10)
        print_result("Get Users", users_resp)

        user_id = None
        if users_resp.success and users_resp.data:
            data = users_resp.data
            items = data if isinstance(data, list) else data.get("users", []) if isinstance(data, dict) else []
            if items:
                user_id = str(items[0].get("id"))
                print(f"   Using User ID: {user_id}")

        if user_id:
            print_section("User Details")
            user_resp = await data_source.get_user(user_id)
            print_result("Get User", user_resp)

        # 3. Get Content
        print_section("Content")
        content_resp = await data_source.get_content_list(limit=10)
        print_result("Get Content", content_resp)

        # 4. Get Pages
        print_section("Pages")
        pages_resp = await data_source.get_pages(limit=10)
        print_result("Get Pages", pages_resp)

        # 5. Get News
        print_section("News")
        news_resp = await data_source.get_news_list(limit=10)
        print_result("Get News", news_resp)

        # 6. Get Communities
        print_section("Communities")
        communities_resp = await data_source.get_communities(limit=10)
        print_result("Get Communities", communities_resp)

        # 7. Get Events
        print_section("Events")
        events_resp = await data_source.get_events(limit=10)
        print_result("Get Events", events_resp)

        # 8. Search
        print_section("Search")
        search_resp = await data_source.search(q="welcome", limit=10)
        print_result("Search", search_resp)

    finally:
        # Cleanup: Close the HTTP client session
        print("\nClosing client connection...")
        inner_client = client.get_client()
        if hasattr(inner_client, "close"):
            await inner_client.close()

    print("\n" + "=" * 80)
    print("  All Interact API operations tested!")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
