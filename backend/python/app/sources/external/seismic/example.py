# ruff: noqa

"""
Seismic API Usage Examples

This example demonstrates how to use the Seismic DataSource to interact with
the Seismic API, covering:
- Authentication (OAuth2 or Bearer Token)
- Initializing the Client and DataSource
- Listing Library Content, Folders, Teamsites, Users
- Fetching workspace documents, LiveSend links, analytics

Prerequisites:
For OAuth2:
1. Register an OAuth app with Seismic
2. Set SEISMIC_CLIENT_ID, SEISMIC_CLIENT_SECRET, and SEISMIC_TENANT_ID
3. OAuth uses "body" auth method (credentials in POST body)

For Bearer Token:
1. Set SEISMIC_ACCESS_TOKEN environment variable
"""

import asyncio
import json
import os

from app.sources.client.seismic.seismic import (
    SeismicClient,
    SeismicOAuthConfig,
    SeismicTokenConfig,
    SeismicResponse,
)
from app.sources.external.seismic.seismic import SeismicDataSource
from app.sources.external.utils.oauth import perform_oauth_flow

# --- Configuration ---
CLIENT_ID = os.getenv("SEISMIC_CLIENT_ID")
CLIENT_SECRET = os.getenv("SEISMIC_CLIENT_SECRET")
TENANT_ID = os.getenv("SEISMIC_TENANT_ID")
ACCESS_TOKEN = os.getenv("SEISMIC_ACCESS_TOKEN")
REDIRECT_URI = os.getenv("SEISMIC_REDIRECT_URI", "http://localhost:8080/callback")


def print_section(title: str):
    print(f"\n{'-'*80}")
    print(f"| {title}")
    print(f"{'-'*80}")


def print_result(name: str, response: SeismicResponse, show_data: bool = True):
    if response.success:
        print(f"  {name}: Success")
        if show_data and response.data:
            data = response.data
            for key in ("content", "folders", "teamsites", "users", "documents",
                        "links", "results"):
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
    print_section("Initializing Seismic Client")

    config = None

    # Priority 1: OAuth2
    if CLIENT_ID and CLIENT_SECRET and TENANT_ID:
        print("  Using OAuth2 authentication")
        try:
            auth_endpoint = f"https://auth.seismic.com/tenants/{TENANT_ID}/connect/authorize"
            token_endpoint = f"https://auth.seismic.com/tenants/{TENANT_ID}/connect/token"

            token_response = perform_oauth_flow(
                client_id=CLIENT_ID,
                client_secret=CLIENT_SECRET,
                auth_endpoint=auth_endpoint,
                token_endpoint=token_endpoint,
                redirect_uri=REDIRECT_URI,
                scopes=[],
                scope_delimiter=" ",
                auth_method="body",  # Seismic uses body method for token exchange
            )
            access_token = token_response.get("access_token")
            if not access_token:
                raise Exception("No access_token found in OAuth response")

            config = SeismicOAuthConfig(
                access_token=access_token,
                client_id=CLIENT_ID,
                client_secret=CLIENT_SECRET,
                tenant_id=TENANT_ID,
            )
            print("  OAuth authentication successful")
        except Exception as e:
            print(f"  OAuth flow failed: {e}")
            print("  Falling back to other authentication methods...")

    # Priority 2: Bearer Token
    if config is None and ACCESS_TOKEN:
        print("  Using Bearer Token authentication")
        config = SeismicTokenConfig(token=ACCESS_TOKEN)

    if config is None:
        print("  No valid authentication method found.")
        print("   Please set one of the following:")
        print("   - SEISMIC_CLIENT_ID, SEISMIC_CLIENT_SECRET, and SEISMIC_TENANT_ID (for OAuth2)")
        print("   - SEISMIC_ACCESS_TOKEN (for Bearer Token)")
        return

    client = SeismicClient.build_with_config(config)
    data_source = SeismicDataSource(client)
    print("Client initialized successfully.")

    try:
        # 2. List Library Content
        print_section("Library Content")
        content_resp = await data_source.list_library_content(page=1, per_page=10)
        print_result("List Library Content", content_resp)

        # 3. List Library Folders
        print_section("Library Folders")
        folders_resp = await data_source.list_library_folders(page=1, per_page=10)
        print_result("List Library Folders", folders_resp)

        # 4. List Teamsites
        print_section("Teamsites")
        teamsites_resp = await data_source.list_teamsites(page=1, per_page=10)
        print_result("List Teamsites", teamsites_resp)

        # 5. List Users
        print_section("Users")
        users_resp = await data_source.list_users(page=1, per_page=10)
        print_result("List Users", users_resp)

        # 6. List Workspace Documents
        print_section("Workspace Documents")
        docs_resp = await data_source.list_workspace_documents(page=1, per_page=10)
        print_result("List Workspace Documents", docs_resp)

        # 7. List LiveSend Links
        print_section("LiveSend Links")
        links_resp = await data_source.list_livesend_links(page=1, per_page=10)
        print_result("List LiveSend Links", links_resp)

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
    print("  All Seismic API operations tested!")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
