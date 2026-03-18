# ruff: noqa

"""
Looker Studio API Usage Examples

This example demonstrates how to use the Looker Studio DataSource to interact
with the Looker Studio (Google Data Studio) API, covering:
- Authentication (OAuth2, Service Account Token)
- Initializing the Client and DataSource
- Searching assets
- Listing reports and data sources
- Getting specific asset details and permissions

Prerequisites:
For OAuth2:
1. Create a Google Cloud project and enable the Looker Studio API
2. Create OAuth 2.0 credentials
3. Set LOOKERSTUDIO_CLIENT_ID and LOOKERSTUDIO_CLIENT_SECRET env vars

For Service Account Token:
1. Create a service account in Google Cloud
2. Generate and download a JSON key
3. Exchange for an access token
4. Set LOOKERSTUDIO_TOKEN environment variable

OAuth Scopes:
- https://www.googleapis.com/auth/datastudio
- https://www.googleapis.com/auth/datastudio.readonly
"""

import asyncio
import json
import os

from app.sources.client.lookerstudio.lookerstudio import (
    LookerStudioClient,
    LookerStudioOAuthConfig,
    LookerStudioResponse,
    LookerStudioTokenConfig,
)
from app.sources.external.lookerstudio.lookerstudio import LookerStudioDataSource
from app.sources.external.utils.oauth import perform_oauth_flow

# --- Configuration ---
# OAuth2 credentials (highest priority)
CLIENT_ID = os.getenv("LOOKERSTUDIO_CLIENT_ID")
CLIENT_SECRET = os.getenv("LOOKERSTUDIO_CLIENT_SECRET")

# Bearer Token (second priority)
TOKEN = os.getenv("LOOKERSTUDIO_TOKEN")

# OAuth redirect URI
REDIRECT_URI = os.getenv("LOOKERSTUDIO_REDIRECT_URI", "http://localhost:8080/callback")


def print_section(title: str):
    print(f"\n{'-'*80}")
    print(f"| {title}")
    print(f"{'-'*80}")


def print_result(name: str, response: LookerStudioResponse, show_data: bool = True):
    if response.success:
        print(f"  {name}: Success")
        if show_data and response.data:
            data = response.data
            if isinstance(data, dict):
                for key in ("assets", "reports", "dataSources"):
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
    print_section("Initializing Looker Studio Client")

    config = None

    # Priority 1: OAuth2
    if CLIENT_ID and CLIENT_SECRET:
        print("  Using OAuth2 authentication")
        try:
            token_response = perform_oauth_flow(
                client_id=CLIENT_ID,
                client_secret=CLIENT_SECRET,
                auth_endpoint="https://accounts.google.com/o/oauth2/v2/auth",
                token_endpoint="https://oauth2.googleapis.com/token",
                redirect_uri=REDIRECT_URI,
                scopes=[
                    "https://www.googleapis.com/auth/datastudio",
                    "https://www.googleapis.com/auth/datastudio.readonly",
                ],
                scope_delimiter=" ",
                auth_method="body",
            )

            access_token = token_response.get("access_token")
            if not access_token:
                raise Exception("No access_token found in OAuth response")

            config = LookerStudioOAuthConfig(
                access_token=access_token,
                client_id=CLIENT_ID,
                client_secret=CLIENT_SECRET,
                redirect_uri=REDIRECT_URI,
            )
            print("  OAuth authentication successful")
        except Exception as e:
            print(f"  OAuth flow failed: {e}")
            print("  Falling back to other authentication methods...")

    # Priority 2: Token
    if config is None and TOKEN:
        print("  Using Bearer Token authentication")
        config = LookerStudioTokenConfig(token=TOKEN)

    if config is None:
        print("  No valid authentication method found.")
        print("   Please set one of the following:")
        print("   - LOOKERSTUDIO_CLIENT_ID and LOOKERSTUDIO_CLIENT_SECRET (OAuth2)")
        print("   - LOOKERSTUDIO_TOKEN (Bearer Token)")
        return

    client = LookerStudioClient.build_with_config(config)
    data_source = LookerStudioDataSource(client)
    print("  Client initialized successfully.")

    try:
        # 2. Search Assets
        print_section("Search Assets")
        assets_resp = await data_source.search_assets(page_size=10)
        print_result("Search Assets", assets_resp)

        # 3. List Reports
        print_section("Reports")
        reports_resp = await data_source.list_reports()
        print_result("List Reports", reports_resp)

        # 4. List Data Sources
        print_section("Data Sources")
        ds_resp = await data_source.list_data_sources()
        print_result("List Data Sources", ds_resp)

        # 5. Get specific asset if available
        if assets_resp.success and assets_resp.data:
            data = assets_resp.data
            assets = data.get("assets", []) if isinstance(data, dict) else []
            if assets:
                asset_id = str(assets[0].get("name", "").split("/")[-1] or assets[0].get("assetId", ""))
                if asset_id:
                    print_section(f"Asset Details: {asset_id}")
                    detail_resp = await data_source.get_asset(asset_id=asset_id)
                    print_result("Get Asset", detail_resp)

                    print_section(f"Asset Permissions: {asset_id}")
                    perms_resp = await data_source.get_asset_permissions(asset_id=asset_id)
                    print_result("Get Permissions", perms_resp)

    finally:
        # Cleanup: Close the HTTP client session
        print("\nClosing client connection...")
        inner_client = client.get_client()
        if hasattr(inner_client, "close"):
            await inner_client.close()

    print("\n" + "=" * 80)
    print("  All Looker Studio API operations tested!")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
