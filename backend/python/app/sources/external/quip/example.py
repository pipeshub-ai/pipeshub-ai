# ruff: noqa

"""
Quip API Usage Examples

This example demonstrates how to use the Quip DataSource to interact with
the Quip Automation API, covering:
- Authentication (OAuth2, Personal Access Token)
- Initializing the Client and DataSource
- Fetching Current User Info and Contacts
- Listing Recent Threads (Documents)
- Getting Thread Details
- Searching Threads
- Working with Folders
- Getting Thread Messages

Prerequisites:
For OAuth2:
1. Create a Quip API app at https://quip.com/dev/automation
2. Set QUIP_CLIENT_ID and QUIP_CLIENT_SECRET environment variables

For Personal Token:
1. Generate a token at https://quip.com/dev/token
2. Set QUIP_PERSONAL_TOKEN environment variable

API Reference: https://quip.com/dev/automation/documentation
"""

import asyncio
import json
import os

from app.sources.client.quip.quip import (
    QuipClient,
    QuipOAuthConfig,
    QuipResponse,
    QuipTokenConfig,
)
from app.sources.external.quip.quip import QuipDataSource
from app.sources.external.utils.oauth import perform_oauth_flow

# --- Configuration ---
# OAuth2 credentials (highest priority)
CLIENT_ID = os.getenv("QUIP_CLIENT_ID")
CLIENT_SECRET = os.getenv("QUIP_CLIENT_SECRET")

# Personal Token (second priority)
PERSONAL_TOKEN = os.getenv("QUIP_PERSONAL_TOKEN")

# OAuth redirect URI
REDIRECT_URI = os.getenv("QUIP_REDIRECT_URI", "http://localhost:8080/callback")


def print_section(title: str):
    print(f"\n{'-'*80}")
    print(f"| {title}")
    print(f"{'-'*80}")


def print_result(name: str, response: QuipResponse, show_data: bool = True):
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
    # 1. Initialize Client
    print_section("Initializing Quip Client")

    config = None

    # Priority 1: OAuth2
    if CLIENT_ID and CLIENT_SECRET:
        print("  Using OAuth2 authentication")
        try:
            print("Starting OAuth flow...")
            token_response = perform_oauth_flow(
                client_id=CLIENT_ID,
                client_secret=CLIENT_SECRET,
                auth_endpoint="https://platform.quip.com/1/oauth/login",
                token_endpoint="https://platform.quip.com/1/oauth/access_token",
                redirect_uri=REDIRECT_URI,
                scopes=[],
                scope_delimiter=" ",
                auth_method="body",
            )

            access_token = token_response.get("access_token")
            if not access_token:
                raise Exception("No access_token found in OAuth response")

            config = QuipOAuthConfig(
                access_token=access_token,
                client_id=CLIENT_ID,
                client_secret=CLIENT_SECRET,
            )
            print("  OAuth authentication successful")
        except Exception as e:
            print(f"  OAuth flow failed: {e}")
            print("  Falling back to other authentication methods...")

    # Priority 2: Personal Token
    if config is None and PERSONAL_TOKEN:
        print("  Using Personal Token authentication")
        config = QuipTokenConfig(token=PERSONAL_TOKEN)

    if config is None:
        print("  No valid authentication method found.")
        print("   Please set one of the following:")
        print("   - QUIP_CLIENT_ID and QUIP_CLIENT_SECRET (for OAuth2)")
        print("   - QUIP_PERSONAL_TOKEN (for Personal Access Token)")
        return

    client = QuipClient.build_with_config(config)
    data_source = QuipDataSource(client)
    print("Client initialized successfully.")

    try:
        # 2. Get Current User
        print_section("Current User")
        user_resp = await data_source.get_current_user()
        print_result("Get Current User", user_resp)

        # 3. Get Contacts
        print_section("Contacts")
        contacts_resp = await data_source.get_contacts()
        print_result("Get Contacts", contacts_resp)

        # 4. Get Recent Threads
        print_section("Recent Threads")
        recent_resp = await data_source.get_recent_threads(count=5)
        print_result("Get Recent Threads", recent_resp)

        # Extract first thread ID for further exploration
        thread_id = None
        if recent_resp.success and recent_resp.data:
            # Quip returns threads as a list of objects
            if isinstance(recent_resp.data, list) and len(recent_resp.data) > 0:
                first_thread = recent_resp.data[0]
                if isinstance(first_thread, dict):
                    thread = first_thread.get("thread", {})
                    thread_id = thread.get("id") if isinstance(thread, dict) else None
            elif isinstance(recent_resp.data, dict):
                # Try to get first thread from the dict response
                for key, value in recent_resp.data.items():
                    if isinstance(value, dict) and "thread" in value:
                        thread_id = value["thread"].get("id")
                        break

        if thread_id:
            # 5. Get Thread Details
            print_section(f"Thread Details: {thread_id}")
            thread_resp = await data_source.get_thread(thread_id=thread_id)
            print_result("Get Thread", thread_resp)

            # 6. Get Thread Messages
            print_section("Thread Messages")
            messages_resp = await data_source.get_thread_messages(
                thread_id=thread_id, count=5
            )
            print_result("Get Thread Messages", messages_resp)
        else:
            print("\n   No threads found. Skipping thread detail operations.")

        # 7. Search Threads
        print_section("Search Threads")
        search_resp = await data_source.search_threads(query="meeting notes")
        print_result("Search 'meeting notes'", search_resp)

        # 8. Get a Folder (using current user's private folder if available)
        if user_resp.success and user_resp.data:
            user_data = user_resp.data
            if isinstance(user_data, dict):
                private_folder = user_data.get("private_folder_id")
                if private_folder:
                    print_section(f"Private Folder: {private_folder}")
                    folder_resp = await data_source.get_folder(
                        folder_id=str(private_folder)
                    )
                    print_result("Get Private Folder", folder_resp)

    finally:
        # Cleanup: Close the HTTP client session
        print("\nClosing client connection...")
        inner_client = client.get_client()
        if hasattr(inner_client, "close"):
            await inner_client.close()

    print("\n" + "=" * 80)
    print("  All Quip API operations tested!")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
