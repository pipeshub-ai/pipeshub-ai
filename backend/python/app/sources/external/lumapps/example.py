# ruff: noqa

"""
LumApps API Usage Examples

This example demonstrates how to use the LumApps DataSource to interact with
the LumApps API via the official lumapps-sdk, covering:
- Authentication (Access Token, Service Account)
- Initializing the Client and DataSource
- Listing Users, Communities, Content, Feeds
- Searching content
- Getting directories and spaces

Prerequisites:
For Access Token:
1. Get your LumApps API token
2. Set LUMAPPS_TOKEN environment variable

For Service Account:
1. Register a service account with LumApps
2. Set LUMAPPS_CLIENT_ID and LUMAPPS_CLIENT_SECRET environment variables
"""

import json
import os

from app.sources.client.lumapps.lumapps import (
    LumAppsClient,
    LumAppsOAuthConfig,
    LumAppsResponse,
    LumAppsTokenConfig,
)
from app.sources.external.lumapps.lumapps import LumAppsDataSource

# --- Configuration ---
TOKEN = os.getenv("LUMAPPS_TOKEN")
CLIENT_ID = os.getenv("LUMAPPS_CLIENT_ID")
CLIENT_SECRET = os.getenv("LUMAPPS_CLIENT_SECRET")


def print_section(title: str):
    print(f"\n{'-'*80}")
    print(f"| {title}")
    print(f"{'-'*80}")


def print_result(name: str, response: LumAppsResponse, show_data: bool = True):
    if response.success:
        print(f"  {name}: Success")
        if show_data and response.data:
            data = response.data
            if isinstance(data, list):
                print(f"   Found {len(data)} items.")
                if data:
                    print(f"   Sample: {json.dumps(data[0], indent=2, default=str)[:400]}...")
            elif isinstance(data, dict):
                print(f"   Data: {json.dumps(data, indent=2, default=str)[:500]}...")
            else:
                print(f"   Data: {str(data)[:500]}...")
    else:
        print(f"  {name}: Failed")
        print(f"   Error: {response.error}")
        if response.message:
            print(f"   Message: {response.message}")


def main() -> None:
    # 1. Initialize Client
    print_section("Initializing LumApps Client")

    config = None

    # Priority 1: Service Account
    if CLIENT_ID and CLIENT_SECRET:
        print("  Using Service Account authentication")
        config = LumAppsOAuthConfig(
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
        )

    # Priority 2: Access Token
    if config is None and TOKEN:
        print("  Using Access Token authentication")
        config = LumAppsTokenConfig(token=TOKEN)

    if config is None:
        print("  No valid authentication method found.")
        print("   Please set one of the following:")
        print("   - LUMAPPS_CLIENT_ID and LUMAPPS_CLIENT_SECRET (for Service Account)")
        print("   - LUMAPPS_TOKEN (for Access Token)")
        return

    client = LumAppsClient.build_with_config(config)
    data_source = LumAppsDataSource(client)
    print("  Client initialized successfully.")

    # 2. List Users
    print_section("Users")
    users_resp = data_source.list_users()
    print_result("List Users", users_resp)

    # 3. List Communities
    print_section("Communities")
    communities_resp = data_source.list_communities()
    print_result("List Communities", communities_resp)

    # 4. List Content
    print_section("Content")
    content_resp = data_source.list_content()
    print_result("List Content", content_resp)

    # 5. List Feeds
    print_section("Feeds")
    feeds_resp = data_source.list_feeds()
    print_result("List Feeds", feeds_resp)

    # 6. Search
    print_section("Search")
    search_resp = data_source.search(query="getting started")
    print_result("Search", search_resp)

    # 7. List Directories
    print_section("Directories")
    dirs_resp = data_source.list_directories()
    print_result("List Directories", dirs_resp)

    # 8. List Spaces
    print_section("Spaces")
    spaces_resp = data_source.list_spaces()
    print_result("List Spaces", spaces_resp)

    print("\n" + "=" * 80)
    print("  All LumApps API operations tested!")
    print("=" * 80)


if __name__ == "__main__":
    main()
