# ruff: noqa

"""
Canva Connect API Usage Examples

This example demonstrates how to use the Canva DataSource to interact with
the Canva Connect API (v1), covering:
- Authentication (OAuth2 with PKCE, Access Token)
- Initializing the Client and DataSource
- Fetching User Profile
- Listing Designs, Folders, and Brand Templates

Prerequisites:
For OAuth2 (PKCE):
1. Create a Canva integration at https://www.canva.com/developers/
2. Set CANVA_CLIENT_ID environment variable
3. The OAuth flow will automatically open a browser for authorization
   (Canva uses PKCE - no client_secret required)

For Access Token:
1. Generate an access token from the Canva developer portal
2. Set CANVA_ACCESS_TOKEN environment variable

OAuth Scopes:
- design:content:read - Read design content
- design:meta:read - Read design metadata
- folder:read - Read folder information
- profile:read - Read user profile
"""

import asyncio
import json
import os

from app.sources.client.canva.canva import (
    CanvaClient,
    CanvaOAuthConfig,
    CanvaResponse,
    CanvaTokenConfig,
)
from app.sources.external.canva.canva import CanvaDataSource
from app.sources.external.utils.oauth import perform_oauth_flow

# --- Configuration ---
# OAuth2 credentials (highest priority)
CLIENT_ID = os.getenv("CANVA_CLIENT_ID")
CLIENT_SECRET = os.getenv("CANVA_CLIENT_SECRET")

# Pre-generated access token (second priority)
ACCESS_TOKEN = os.getenv("CANVA_ACCESS_TOKEN")

# OAuth redirect URI
REDIRECT_URI = os.getenv("CANVA_REDIRECT_URI", "http://localhost:8080/callback")


def print_section(title: str):
    print(f"\n{'-'*80}")
    print(f"| {title}")
    print(f"{'-'*80}")


def print_result(name: str, response: CanvaResponse, show_data: bool = True):
    if response.success:
        print(f"  {name}: Success")
        if show_data and response.data:
            data = response.data
            # Handle list-type responses (designs, folders, items, templates, assets, comments)
            for key in ("designs", "folders", "items", "brand_templates", "assets",
                        "comments", "exports"):
                if isinstance(data, dict) and key in data:
                    items = data[key]
                    print(f"   Found {len(items)} {key}.")
                    if items:
                        print(f"   Sample: {json.dumps(items[0], indent=2)[:400]}...")
                    return
            # Generic response
            print(f"   Data: {json.dumps(data, indent=2)[:500]}...")
    else:
        print(f"  {name}: Failed")
        print(f"   Error: {response.error}")
        if response.message:
            print(f"   Message: {response.message}")


async def main() -> None:
    # 1. Initialize Client
    print_section("Initializing Canva Client")

    config = None

    # Priority 1: OAuth2 (PKCE)
    if CLIENT_ID:
        print("  Using OAuth2 authentication (PKCE)")
        try:
            print("Starting OAuth flow...")
            # Canva OAuth uses PKCE (no client_secret needed)
            # auth_method="body" sends client_id in POST body
            token_response = perform_oauth_flow(
                client_id=CLIENT_ID,
                client_secret=CLIENT_SECRET,
                auth_endpoint="https://www.canva.com/api/oauth/authorize",
                token_endpoint="https://api.canva.com/rest/v1/oauth/token",
                redirect_uri=REDIRECT_URI,
                scopes=[
                    "design:content:read",
                    "design:meta:read",
                    "folder:read",
                    "profile:read",
                ],
                scope_delimiter=" ",
                auth_method="body",
            )

            access_token = token_response.get("access_token")
            if not access_token:
                raise Exception("No access_token found in OAuth response")

            config = CanvaOAuthConfig(
                access_token=access_token,
                client_id=CLIENT_ID,
            )
            print("  OAuth authentication successful")
        except Exception as e:
            print(f"  OAuth flow failed: {e}")
            print("  Falling back to other authentication methods...")

    # Priority 2: Pre-generated Access Token
    if config is None and ACCESS_TOKEN:
        print("  Using pre-generated access token")
        config = CanvaTokenConfig(
            token=ACCESS_TOKEN,
        )

    if config is None:
        print("  No valid authentication method found.")
        print("   Please set one of the following:")
        print("   - CANVA_CLIENT_ID (for OAuth2 with PKCE)")
        print("   - CANVA_ACCESS_TOKEN (for pre-generated token)")
        return

    client = CanvaClient.build_with_config(config)
    data_source = CanvaDataSource(client)
    print("Client initialized successfully.")

    try:
        # 2. Get Current User
        print_section("Current User Profile")
        user_resp = await data_source.get_current_user()
        print_result("Get Current User", user_resp)

        # 3. List Designs
        print_section("Designs")
        designs_resp = await data_source.list_designs(limit=5)
        print_result("List Designs", designs_resp)

        # Get a specific design if available
        if designs_resp.success and designs_resp.data:
            items = designs_resp.data.get("items", [])
            if items:
                design_id = str(items[0].get("id"))
                print_section(f"Design Details: {items[0].get('title', 'Untitled')}")
                design_resp = await data_source.get_design(design_id=design_id)
                print_result("Get Design", design_resp)

        # 4. List Folders
        print_section("Folders")
        folders_resp = await data_source.list_folders(limit=5)
        print_result("List Folders", folders_resp)

        # Get folder items if available
        if folders_resp.success and folders_resp.data:
            items = folders_resp.data.get("items", [])
            if items:
                folder_id = str(items[0].get("id"))
                print_section(f"Folder Items: {items[0].get('name', 'Unnamed')}")
                folder_items_resp = await data_source.list_folder_items(
                    folder_id=folder_id, limit=5
                )
                print_result("List Folder Items", folder_items_resp)

        # 5. List Brand Templates
        print_section("Brand Templates")
        templates_resp = await data_source.list_brand_templates(limit=5)
        print_result("List Brand Templates", templates_resp)

        # 6. List Assets
        print_section("Assets")
        assets_resp = await data_source.list_assets(limit=5)
        print_result("List Assets", assets_resp)

    finally:
        # Cleanup: Close the HTTP client session
        print("\nClosing client connection...")
        inner_client = client.get_client()
        if hasattr(inner_client, "close"):
            await inner_client.close()

    print("\n" + "=" * 80)
    print("  All Canva API operations tested!")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
