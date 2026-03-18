# ruff: noqa

"""
Pipedrive API Usage Examples

This example demonstrates how to use the Pipedrive DataSource to interact with
the Pipedrive API, covering:
- Authentication (OAuth2, API Token)
- Initializing the Client and DataSource
- Getting Current User
- Listing Deals
- Listing Persons (Contacts)
- Listing Pipelines
- Listing Activities

Prerequisites:
For OAuth2:
1. Create a Pipedrive OAuth app at https://app.pipedrive.com/settings/marketplace
2. Set PIPEDRIVE_CLIENT_ID and PIPEDRIVE_CLIENT_SECRET environment variables
3. The OAuth flow will automatically open a browser for authorization

For API Token:
1. Log in to Pipedrive
2. Go to Settings > Personal preferences > API
3. Copy your personal API token
4. Set PIPEDRIVE_API_TOKEN environment variable
"""

import asyncio
import json
import os

from app.sources.client.pipedrive.pipedrive import (
    PipedriveClient,
    PipedriveOAuthConfig,
    PipedriveTokenConfig,
    PipedriveResponse,
)
from app.sources.external.pipedrive.pipedrive import PipedriveDataSource
from app.sources.external.utils.oauth import perform_oauth_flow

# --- Configuration ---
# OAuth2 credentials (highest priority)
CLIENT_ID = os.getenv("PIPEDRIVE_CLIENT_ID")
CLIENT_SECRET = os.getenv("PIPEDRIVE_CLIENT_SECRET")

# API Token (second priority)
API_TOKEN = os.getenv("PIPEDRIVE_API_TOKEN")

# OAuth redirect URI
REDIRECT_URI = os.getenv("PIPEDRIVE_REDIRECT_URI", "http://localhost:8080/callback")


def print_section(title: str):
    print(f"\n{'-'*80}")
    print(f"| {title}")
    print(f"{'-'*80}")


def print_result(name: str, response: PipedriveResponse, show_data: bool = True):
    if response.success:
        print(f"  {name}: Success")
        if show_data and response.data:
            data = response.data
            # Handle Pipedrive list-type responses (data is usually in a 'data' key)
            if isinstance(data, dict) and "data" in data:
                items = data["data"]
                if isinstance(items, list):
                    print(f"   Found {len(items)} items.")
                    if items:
                        print(f"   Sample: {json.dumps(items[0], indent=2)[:400]}...")
                else:
                    print(f"   Data: {json.dumps(items, indent=2)[:500]}...")
            else:
                # Generic response
                print(f"   Data: {json.dumps(data, indent=2)[:500]}...")
    else:
        print(f"  {name}: Failed")
        print(f"   Error: {response.error}")
        if response.message:
            print(f"   Message: {response.message}")


async def main() -> None:
    # 1. Initialize Client
    print_section("Initializing Pipedrive Client")

    config = None

    # Priority 1: OAuth2
    if CLIENT_ID and CLIENT_SECRET:
        print("  Using OAuth2 authentication")
        try:
            print("Starting OAuth flow...")
            # Pipedrive OAuth authorization URL: https://oauth.pipedrive.com/oauth/authorize
            # Pipedrive token endpoint: https://oauth.pipedrive.com/oauth/token
            token_response = perform_oauth_flow(
                client_id=CLIENT_ID,
                client_secret=CLIENT_SECRET,
                auth_endpoint="https://oauth.pipedrive.com/oauth/authorize",
                token_endpoint="https://oauth.pipedrive.com/oauth/token",
                redirect_uri=REDIRECT_URI,
                scopes=[],  # Pipedrive doesn't require specific scopes in the auth URL
                scope_delimiter=" ",
                auth_method="header",  # Basic Auth with client_id:client_secret
            )

            access_token = token_response.get("access_token")
            if not access_token:
                raise Exception("No access_token found in OAuth response")

            config = PipedriveOAuthConfig(
                access_token=access_token,
                client_id=CLIENT_ID,
                client_secret=CLIENT_SECRET,
            )
            print("  OAuth authentication successful")
        except Exception as e:
            print(f"  OAuth flow failed: {e}")
            print("  Falling back to other authentication methods...")

    # Priority 2: API Token
    if config is None and API_TOKEN:
        print("  Using API Token authentication")
        config = PipedriveTokenConfig(
            token=API_TOKEN,
        )

    if config is None:
        print("  No valid authentication method found.")
        print("   Please set one of the following:")
        print("   - PIPEDRIVE_CLIENT_ID and PIPEDRIVE_CLIENT_SECRET (for OAuth2)")
        print("   - PIPEDRIVE_API_TOKEN (for API Token)")
        return

    client = PipedriveClient.build_with_config(config)
    data_source = PipedriveDataSource(client)
    print("Client initialized successfully.")

    try:
        # 2. Get Current User
        print_section("Current User")
        user_resp = await data_source.get_current_user()
        print_result("Get Current User", user_resp)

        # 3. List Deals
        print_section("Deals")
        deals_resp = await data_source.list_deals(limit=5)
        print_result("List Deals", deals_resp)

        # 4. List Persons (Contacts)
        print_section("Persons (Contacts)")
        persons_resp = await data_source.list_persons(limit=5)
        print_result("List Persons", persons_resp)

        # 5. List Pipelines
        print_section("Pipelines")
        pipelines_resp = await data_source.list_pipelines()
        print_result("List Pipelines", pipelines_resp)

        # 6. List Activities
        print_section("Activities")
        activities_resp = await data_source.list_activities(limit=5)
        print_result("List Activities", activities_resp)

    finally:
        # Cleanup: Close the HTTP client session
        print("\nClosing client connection...")
        inner_client = client.get_client()
        if hasattr(inner_client, "close"):
            await inner_client.close()

    print("\n" + "=" * 80)
    print("  All Pipedrive API operations tested!")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
