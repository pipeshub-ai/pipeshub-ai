# ruff: noqa

"""
Egnyte API Usage Examples

This example demonstrates how to use the Egnyte DataSource to interact with
the Egnyte API, covering:
- Authentication (OAuth2, Access Token)
- Initializing the Client and DataSource
- Fetching Current User Info
- Listing Files and Folders
- Managing Users and Groups
- Searching Files
- Auditing File Activity

Prerequisites:
For OAuth2:
1. Create an Egnyte API key at https://developers.egnyte.com
2. Set EGNYTE_CLIENT_ID, EGNYTE_CLIENT_SECRET, and EGNYTE_DOMAIN
3. The OAuth flow will automatically open a browser for authorization

For Access Token:
1. Generate a token in Egnyte developer portal
2. Set EGNYTE_ACCESS_TOKEN and EGNYTE_DOMAIN environment variables

API Reference: https://developers.egnyte.com/docs
"""

import asyncio
import json
import os

from app.sources.client.egnyte.egnyte import (
    EgnyteClient,
    EgnyteOAuthConfig,
    EgnyteResponse,
    EgnyteTokenConfig,
)
from app.sources.external.egnyte.egnyte import EgnyteDataSource
from app.sources.external.utils.oauth import perform_oauth_flow

# --- Configuration ---
# OAuth2 credentials (highest priority)
CLIENT_ID = os.getenv("EGNYTE_CLIENT_ID")
CLIENT_SECRET = os.getenv("EGNYTE_CLIENT_SECRET")

# Access Token (second priority)
ACCESS_TOKEN = os.getenv("EGNYTE_ACCESS_TOKEN")

# Domain (required for all auth types)
DOMAIN = os.getenv("EGNYTE_DOMAIN")  # e.g. 'mycompany' for mycompany.egnyte.com

# OAuth redirect URI
REDIRECT_URI = os.getenv("EGNYTE_REDIRECT_URI", "http://localhost:8080/callback")


def print_section(title: str):
    print(f"\n{'-'*80}")
    print(f"| {title}")
    print(f"{'-'*80}")


def print_result(name: str, response: EgnyteResponse, show_data: bool = True):
    if response.success:
        print(f"  {name}: Success")
        if show_data and response.data:
            data = response.data
            print(f"   Data: {json.dumps(data, indent=2, default=str)[:500]}...")
    else:
        print(f"  {name}: Failed")
        print(f"   Error: {response.error}")
        if response.message:
            print(f"   Message: {response.message}")


async def main() -> None:
    if not DOMAIN:
        print("  Please set EGNYTE_DOMAIN environment variable")
        print("   e.g. 'mycompany' for mycompany.egnyte.com")
        return

    # 1. Initialize Client
    print_section("Initializing Egnyte Client")

    config = None

    # Priority 1: OAuth2
    if CLIENT_ID and CLIENT_SECRET:
        print("  Using OAuth2 authentication")
        try:
            print("Starting OAuth flow...")
            token_response = perform_oauth_flow(
                client_id=CLIENT_ID,
                client_secret=CLIENT_SECRET,
                auth_endpoint=f"https://{DOMAIN}.egnyte.com/puboauth/token",
                token_endpoint=f"https://{DOMAIN}.egnyte.com/puboauth/token",
                redirect_uri=REDIRECT_URI,
                scopes=[],
                scope_delimiter=" ",
                auth_method="body",
            )

            access_token = token_response.get("access_token")
            if not access_token:
                raise Exception("No access_token found in OAuth response")

            config = EgnyteOAuthConfig(
                access_token=access_token,
                domain=DOMAIN,
                client_id=CLIENT_ID,
                client_secret=CLIENT_SECRET,
            )
            print("  OAuth authentication successful")
        except Exception as e:
            print(f"  OAuth flow failed: {e}")
            print("  Falling back to other authentication methods...")

    # Priority 2: Access Token
    if config is None and ACCESS_TOKEN:
        print("  Using Access Token authentication")
        config = EgnyteTokenConfig(
            token=ACCESS_TOKEN,
            domain=DOMAIN,
        )

    if config is None:
        print("  No valid authentication method found.")
        print("   Please set one of the following:")
        print("   - EGNYTE_CLIENT_ID and EGNYTE_CLIENT_SECRET (for OAuth2)")
        print("   - EGNYTE_ACCESS_TOKEN (for Access Token)")
        return

    client = EgnyteClient.build_with_config(config)
    data_source = EgnyteDataSource(client)
    print(f"Client initialized successfully for domain: {DOMAIN}")

    try:
        # 2. Get Current User Info
        print_section("Current User Info")
        user_resp = await data_source.get_current_user()
        print_result("Get Current User", user_resp)

        # 3. List Root Folder
        print_section("Root Folder Contents")
        root_resp = await data_source.get_file_or_folder_metadata(
            path="Shared",
            list_content=True,
            count=10,
        )
        print_result("Root Folder", root_resp)

        # 4. List Users
        print_section("Users")
        users_resp = await data_source.list_users(count=5)
        print_result("List Users", users_resp)

        # 5. List Groups
        print_section("Groups")
        groups_resp = await data_source.list_groups()
        print_result("List Groups", groups_resp)

        # 6. List Links
        print_section("Shared Links")
        links_resp = await data_source.list_links(count=5)
        print_result("List Links", links_resp)

        # 7. Search
        print_section("Search Files")
        search_resp = await data_source.search(query="report")
        print_result("Search 'report'", search_resp)

    finally:
        # Cleanup: Close the HTTP client session
        print("\nClosing client connection...")
        inner_client = client.get_client()
        if hasattr(inner_client, "close"):
            await inner_client.close()

    print("\n" + "=" * 80)
    print("  All Egnyte API operations tested!")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
