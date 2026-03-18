# ruff: noqa

"""
Panopto API Usage Examples

This example demonstrates how to use the Panopto DataSource to interact with
the Panopto API, covering:
- Authentication (OAuth2, API Key / Bearer Token)
- Initializing the Client and DataSource
- Listing Sessions (Recordings)
- Listing Folders and Folder Sessions
- Listing Users and Groups
- Searching for Content
- Getting View Statistics

Prerequisites:
For OAuth2:
1. Create a Panopto OAuth app in your Panopto instance admin panel
2. Set PANOPTO_CLIENT_ID and PANOPTO_CLIENT_SECRET environment variables
3. Set PANOPTO_DOMAIN (e.g., "mycompany" for mycompany.hosted.panopto.com)

For Bearer Token:
1. Set PANOPTO_ACCESS_TOKEN environment variable with your API key / access token
2. Set PANOPTO_DOMAIN (e.g., "mycompany" for mycompany.hosted.panopto.com)
"""

import asyncio
import json
import os

from app.sources.client.panopto.panopto import (
    PanoptoClient,
    PanoptoOAuthConfig,
    PanoptoTokenConfig,
    PanoptoResponse,
)
from app.sources.external.panopto.panopto import PanoptoDataSource
from app.sources.external.utils.oauth import perform_oauth_flow

# --- Configuration ---
# OAuth2 credentials
CLIENT_ID = os.getenv("PANOPTO_CLIENT_ID")
CLIENT_SECRET = os.getenv("PANOPTO_CLIENT_SECRET")

# Bearer Token
ACCESS_TOKEN = os.getenv("PANOPTO_ACCESS_TOKEN")

# Domain (required for both auth methods)
DOMAIN = os.getenv("PANOPTO_DOMAIN", "")

# OAuth redirect URI
REDIRECT_URI = os.getenv("PANOPTO_REDIRECT_URI", "http://localhost:8080/callback")


def print_section(title: str):
    print(f"\n{'-'*80}")
    print(f"| {title}")
    print(f"{'-'*80}")


def print_result(name: str, response: PanoptoResponse, show_data: bool = True):
    if response.success:
        print(f"  {name}: Success")
        if show_data and response.data:
            data = response.data
            # Handle list-type responses
            for key in ("Results", "sessions", "folders", "users", "groups",
                        "viewers", "SearchResults"):
                if isinstance(data, dict) and key in data:
                    items = data[key]
                    if isinstance(items, list):
                        print(f"   Found {len(items)} {key}.")
                        if items:
                            print(f"   Sample: {json.dumps(items[0], indent=2)[:400]}...")
                    return
            # If data is a list itself
            if isinstance(data, list):
                print(f"   Found {len(data)} items.")
                if data:
                    print(f"   Sample: {json.dumps(data[0], indent=2)[:400]}...")
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
    print_section("Initializing Panopto Client")

    if not DOMAIN:
        print("  Error: PANOPTO_DOMAIN is required.")
        print("   Set PANOPTO_DOMAIN environment variable (e.g., 'mycompany')")
        return

    config = None

    # Priority 1: OAuth2
    if CLIENT_ID and CLIENT_SECRET:
        print(f"  Using OAuth2 authentication (domain: {DOMAIN})")
        try:
            print("Starting OAuth flow...")
            auth_endpoint = f"https://{DOMAIN}.hosted.panopto.com/Panopto/oauth2/connect/authorize"
            token_endpoint = f"https://{DOMAIN}.hosted.panopto.com/Panopto/oauth2/connect/token"

            token_response = perform_oauth_flow(
                client_id=CLIENT_ID,
                client_secret=CLIENT_SECRET,
                auth_endpoint=auth_endpoint,
                token_endpoint=token_endpoint,
                redirect_uri=REDIRECT_URI,
                scopes=["api"],
                scope_delimiter=" ",
                auth_method="body",
            )

            access_token = token_response.get("access_token")
            if not access_token:
                raise Exception("No access_token found in OAuth response")

            config = PanoptoOAuthConfig(
                access_token=access_token,
                domain=DOMAIN,
                client_id=CLIENT_ID,
                client_secret=CLIENT_SECRET,
            )
            print("  OAuth authentication successful")
        except Exception as e:
            print(f"  OAuth flow failed: {e}")
            print("  Falling back to other authentication methods...")

    # Priority 2: Bearer Token
    if config is None and ACCESS_TOKEN:
        print(f"  Using Bearer Token authentication (domain: {DOMAIN})")
        config = PanoptoTokenConfig(
            token=ACCESS_TOKEN,
            domain=DOMAIN,
        )

    if config is None:
        print("  No valid authentication method found.")
        print("   Please set one of the following:")
        print("   - PANOPTO_CLIENT_ID and PANOPTO_CLIENT_SECRET (for OAuth2)")
        print("   - PANOPTO_ACCESS_TOKEN (for Bearer Token)")
        return

    client = PanoptoClient.build_with_config(config)
    data_source = PanoptoDataSource(client)
    print("Client initialized successfully.")

    try:
        # 2. Get Sessions
        print_section("Sessions (Recordings)")
        sessions_resp = await data_source.get_sessions(max_number_results=5)
        print_result("Get Sessions", sessions_resp)

        # Get specific session if available
        session_id = None
        if sessions_resp.success and sessions_resp.data:
            results = sessions_resp.data.get("Results", [])
            if isinstance(results, list) and results:
                session_id = str(results[0].get("Id", ""))
                print(f"   Using Session: {results[0].get('Name', 'N/A')} (ID: {session_id})")

        if session_id:
            print_section(f"Session Details: {session_id}")
            session_resp = await data_source.get_session(session_id=session_id)
            print_result("Get Session", session_resp)

            # Get Session Viewers
            print_section("Session Viewers")
            viewers_resp = await data_source.get_session_viewers(
                session_id=session_id, max_number_results=5
            )
            print_result("Get Session Viewers", viewers_resp)

            # Get View Stats
            print_section("View Statistics")
            stats_resp = await data_source.get_view_stats(session_id=session_id)
            print_result("Get View Stats", stats_resp)

        # 3. Get Folders
        print_section("Folders")
        folders_resp = await data_source.get_folders(max_number_results=5)
        print_result("Get Folders", folders_resp)

        # Get folder sessions if a folder is available
        folder_id = None
        if folders_resp.success and folders_resp.data:
            results = folders_resp.data.get("Results", [])
            if isinstance(results, list) and results:
                folder_id = str(results[0].get("Id", ""))
                print(f"   Using Folder: {results[0].get('Name', 'N/A')} (ID: {folder_id})")

        if folder_id:
            print_section("Folder Sessions")
            folder_sessions_resp = await data_source.get_folder_sessions(
                folder_id=folder_id, max_number_results=5
            )
            print_result("Get Folder Sessions", folder_sessions_resp)

        # 4. Get Users
        print_section("Users")
        users_resp = await data_source.get_users(max_number_results=5)
        print_result("Get Users", users_resp)

        # 5. Get Groups
        print_section("Groups")
        groups_resp = await data_source.get_groups(max_number_results=5)
        print_result("Get Groups", groups_resp)

        # 6. Search
        print_section("Search")
        search_resp = await data_source.search(query="test", max_number_results=5)
        print_result("Search", search_resp)

    finally:
        # Cleanup: Close the HTTP client session
        print("\nClosing client connection...")
        inner_client = client.get_client()
        if hasattr(inner_client, "close"):
            await inner_client.close()

    print("\n" + "=" * 80)
    print("  All Panopto API operations tested!")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
