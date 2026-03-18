# ruff: noqa

"""
Miro API Usage Examples (SDK-based)

This example demonstrates how to use the Miro DataSource backed by the
official ``miro_api`` SDK, covering:
- Authentication (OAuth2, Bearer Token)
- Initializing the Client and DataSource
- Listing Boards
- Getting Board Details
- Listing Board Items, Members, Tags, Connectors, Frames

Prerequisites:
For OAuth2:
1. Create a Miro app at https://miro.com/app/settings/user-profile/apps
2. Set MIRO_CLIENT_ID and MIRO_CLIENT_SECRET environment variables
3. The OAuth flow will automatically open a browser for authorization

For Bearer Token:
1. Create a Miro app and generate an access token
2. Set MIRO_ACCESS_TOKEN environment variable

SDK Reference: https://miroapp.github.io/api-clients/python/
"""

import asyncio
import os

from app.sources.client.miro.miro import (
    MiroClient,
    MiroOAuthConfig,
    MiroResponse,
    MiroTokenConfig,
)
from app.sources.external.miro.miro import MiroDataSource
from app.sources.external.utils.oauth import perform_oauth_flow

# --- Configuration ---
# OAuth2 credentials (highest priority)
CLIENT_ID = os.getenv("MIRO_CLIENT_ID")
CLIENT_SECRET = os.getenv("MIRO_CLIENT_SECRET")

# Bearer Token (second priority)
ACCESS_TOKEN = os.getenv("MIRO_ACCESS_TOKEN")

# OAuth redirect URI
REDIRECT_URI = os.getenv("MIRO_REDIRECT_URI", "http://localhost:8080/callback")


def print_section(title: str):
    print(f"\n{'-'*80}")
    print(f"| {title}")
    print(f"{'-'*80}")


def print_result(name: str, response: MiroResponse, show_data: bool = True):
    if response.success:
        print(f"  {name}: Success")
        if show_data and response.data is not None:
            data = response.data
            # SDK returns typed model objects; show their repr
            print(f"   Data: {repr(data)[:500]}...")
    else:
        print(f"  {name}: Failed")
        print(f"   Error: {response.error}")
        if response.message:
            print(f"   Message: {response.message}")


async def main() -> None:
    # 1. Initialize Client
    print_section("Initializing Miro Client")

    config = None

    # Priority 1: OAuth2
    if CLIENT_ID and CLIENT_SECRET:
        print("  Using OAuth2 authentication")
        try:
            print("Starting OAuth flow...")
            token_response = perform_oauth_flow(
                client_id=CLIENT_ID,
                client_secret=CLIENT_SECRET,
                auth_endpoint="https://miro.com/oauth/authorize",
                token_endpoint="https://api.miro.com/v1/oauth/token",
                redirect_uri=REDIRECT_URI,
                scopes=[],
                scope_delimiter=" ",
                auth_method="body",
            )

            access_token = token_response.get("access_token")
            if not access_token:
                raise Exception("No access_token found in OAuth response")

            config = MiroOAuthConfig(
                access_token=access_token,
                client_id=CLIENT_ID,
                client_secret=CLIENT_SECRET,
            )
            print("  OAuth authentication successful")
        except Exception as e:
            print(f"  OAuth flow failed: {e}")
            print("  Falling back to other authentication methods...")

    # Priority 2: Bearer Token
    if config is None and ACCESS_TOKEN:
        print("  Using Bearer Token authentication")
        config = MiroTokenConfig(
            token=ACCESS_TOKEN,
        )

    if config is None:
        print("  No valid authentication method found.")
        print("   Please set one of the following:")
        print("   - MIRO_CLIENT_ID and MIRO_CLIENT_SECRET (for OAuth2)")
        print("   - MIRO_ACCESS_TOKEN (for Bearer Token)")
        return

    client = MiroClient.build_with_config(config)
    # MiroDataSource accepts MiroClient (or raw MiroApi) and wraps SDK calls
    data_source = MiroDataSource(client)
    print("Client initialized successfully.")

    # 2. List Boards
    print_section("Boards")
    boards_resp = data_source.list_boards()
    print_result("List Boards", boards_resp)

    # Extract first board_id for further exploration
    board_id = None
    if boards_resp.success and boards_resp.data is not None:
        boards_data = boards_resp.data
        # SDK returns a BoardsPagedResponse with a .data attribute
        board_list = getattr(boards_data, "data", None) or []
        if board_list:
            first_board = board_list[0]
            board_id = getattr(first_board, "id", None)
            board_name = getattr(first_board, "name", "Unknown")
            print(f"   Using Board: {board_name} (ID: {board_id})")

    if not board_id:
        print("   No boards found. Skipping further operations.")
        return

    # 3. Get Board Details
    print_section("Board Details")
    board_resp = data_source.get_board(board_id=board_id)
    print_result("Get Board", board_resp)

    # 4. List Board Items
    print_section("Board Items")
    items_resp = data_source.list_board_items(board_id=board_id)
    print_result("List Board Items", items_resp)

    # 5. List Board Members
    print_section("Board Members")
    members_resp = data_source.list_board_members(board_id=board_id)
    print_result("List Board Members", members_resp)

    # 6. List Board Tags
    print_section("Board Tags")
    tags_resp = data_source.list_board_tags(board_id=board_id)
    print_result("List Board Tags", tags_resp)

    # 7. List Connectors
    print_section("Board Connectors")
    connectors_resp = data_source.list_connectors(board_id=board_id)
    print_result("List Connectors", connectors_resp)

    # 8. List Frames (using type filter)
    print_section("Board Frames")
    frames_resp = data_source.list_frames(board_id=board_id, type="frame")
    print_result("List Frames", frames_resp)

    print("\n" + "=" * 80)
    print("  All Miro API operations tested!")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
