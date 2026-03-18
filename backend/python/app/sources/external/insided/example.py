# ruff: noqa

"""
InSided (Gainsight Customer Communities) API Usage Examples

This example demonstrates how to use the InSided DataSource to interact with
the InSided API v2, covering:
- Authentication (OAuth2 client_credentials, Bearer Token)
- Initializing the Client and DataSource
- Listing Communities, Categories, Topics, Posts
- Fetching Users and Groups
- Searching content

Prerequisites:
For OAuth2 client_credentials:
1. Obtain client_id and client_secret from InSided admin panel
2. Set INSIDED_CLIENT_ID and INSIDED_CLIENT_SECRET environment variables

For Bearer Token:
1. Obtain a valid API token from InSided
2. Set INSIDED_TOKEN environment variable
"""

import asyncio
import json
import os

from app.sources.client.insided.insided import (
    InSidedClient,
    InSidedClientCredentialsConfig,
    InSidedTokenConfig,
    InSidedResponse,
)
from app.sources.external.insided.insided import InSidedDataSource

# --- Configuration ---
CLIENT_ID = os.getenv("INSIDED_CLIENT_ID")
CLIENT_SECRET = os.getenv("INSIDED_CLIENT_SECRET")
TOKEN = os.getenv("INSIDED_TOKEN")


def print_section(title: str):
    print(f"\n{'-'*80}")
    print(f"| {title}")
    print(f"{'-'*80}")


def print_result(name: str, response: InSidedResponse, show_data: bool = True):
    if response.success:
        print(f"  {name}: Success")
        if show_data and response.data:
            data = response.data
            if isinstance(data, list):
                print(f"   Found {len(data)} items.")
                if data:
                    print(f"   Sample: {json.dumps(data[0], indent=2)[:400]}...")
            elif isinstance(data, dict):
                for key in ("communities", "categories", "topics", "posts",
                            "users", "groups", "results"):
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
    print_section("Initializing InSided Client")

    config = None

    # Priority 1: OAuth2 client_credentials
    if CLIENT_ID and CLIENT_SECRET:
        print("  Using OAuth2 client_credentials authentication")
        config = InSidedClientCredentialsConfig(
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
        )

    # Priority 2: Bearer Token
    if config is None and TOKEN:
        print("  Using Bearer Token authentication")
        config = InSidedTokenConfig(token=TOKEN)

    if config is None:
        print("  No valid authentication method found.")
        print("   Please set one of the following:")
        print("   - INSIDED_CLIENT_ID and INSIDED_CLIENT_SECRET (for OAuth2)")
        print("   - INSIDED_TOKEN (for Bearer Token)")
        return

    client = InSidedClient.build_with_config(config)
    data_source = InSidedDataSource(client)
    print("Client initialized successfully.")

    try:
        # 2. Get Communities
        print_section("Communities")
        communities_resp = await data_source.get_communities(limit=10)
        print_result("Get Communities", communities_resp)

        community_id = None
        if communities_resp.success and communities_resp.data:
            data = communities_resp.data
            items = data if isinstance(data, list) else data.get("communities", []) if isinstance(data, dict) else []
            if items:
                community_id = str(items[0].get("id"))
                print(f"   Using Community ID: {community_id}")

        if community_id:
            print_section("Community Details")
            community_resp = await data_source.get_community(community_id)
            print_result("Get Community", community_resp)

        # 3. Get Categories
        print_section("Categories")
        categories_resp = await data_source.get_categories(limit=10)
        print_result("Get Categories", categories_resp)

        # 4. Get Topics
        print_section("Topics")
        topics_resp = await data_source.get_topics(limit=10)
        print_result("Get Topics", topics_resp)

        # 5. Get Posts
        print_section("Posts")
        posts_resp = await data_source.get_posts(limit=10)
        print_result("Get Posts", posts_resp)

        # 6. Get Users
        print_section("Users")
        users_resp = await data_source.get_users(limit=10)
        print_result("Get Users", users_resp)

        # 7. Get Groups
        print_section("Groups")
        groups_resp = await data_source.get_groups(limit=10)
        print_result("Get Groups", groups_resp)

        # 8. Search
        print_section("Search")
        search_resp = await data_source.search(q="help", limit=10)
        print_result("Search", search_resp)

    finally:
        # Cleanup: Close the HTTP client session
        print("\nClosing client connection...")
        inner_client = client.get_client()
        if hasattr(inner_client, "close"):
            await inner_client.close()

    print("\n" + "=" * 80)
    print("  All InSided API operations tested!")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
