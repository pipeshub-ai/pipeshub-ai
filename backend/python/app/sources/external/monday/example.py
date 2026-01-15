# ruff: noqa
"""
Monday.com API Usage Examples

This example demonstrates how to use the Monday.com DataSource to interact with
the Monday.com GraphQL API, covering:
- Authentication (OAuth2 or API Token)
- Board Operations (CRUD)
- Item Operations (CRUD)
- Column Operations
- Group Operations
- Update/Comment Operations
- Workspace Operations
- Team & User Operations

Prerequisites:
For OAuth2:
1. Create a Monday.com app in the Developer Center (https://monday.com/developers/apps)
2. Set MONDAY_CLIENT_ID and MONDAY_CLIENT_SECRET environment variables
3. Set MONDAY_REDIRECT_URI (default: http://localhost:8080/oauth/callback)
   - This must match the redirect URI registered in your Monday.com app
4. The OAuth flow will automatically open a browser for authorization

For API Token:
1. Get your API Token from Monday.com (Profile > Admin > API)
2. Set MONDAY_API_TOKEN environment variable
"""

import asyncio
import json
import os
from typing import Dict, Any

from app.sources.client.monday.monday import (
    MondayClient,
    MondayTokenConfig,
    MondayOAuthConfig,
)
from app.sources.external.monday.monday_data_source import MondayDataSource
from app.sources.external.utils.oauth import perform_oauth_flow
from app.sources.client.graphql.response import GraphQLResponse

# --- Configuration ---
CLIENT_ID = os.getenv("MONDAY_CLIENT_ID")
CLIENT_SECRET = os.getenv("MONDAY_CLIENT_SECRET")
API_TOKEN = os.getenv("MONDAY_API_TOKEN")
API_VERSION = os.getenv("MONDAY_API_VERSION")  # e.g., "2025-04"
REDIRECT_URI = os.getenv("MONDAY_REDIRECT_URI", "http://localhost:8080/callback")

# Monday.com OAuth endpoints
MONDAY_AUTH_ENDPOINT = "https://auth.monday.com/oauth2/authorize"
MONDAY_TOKEN_ENDPOINT = "https://auth.monday.com/oauth2/token"


def print_section(title: str):
    print(f"\n{'-'*80}")
    print(f"| {title}")
    print(f"{'-'*80}")


def print_result(name: str, response: GraphQLResponse, show_data: bool = True, max_items: int = 3):
    if response.success:
        print(f"✅ {name}: Success")
        if show_data and response.data:
            data = response.data

            # Handle board results
            if "boards" in data:
                boards = data["boards"]
                print(f"   Total boards: {len(boards)}")
                if boards:
                    print(f"   Showing first {min(len(boards), max_items)} boards:")
                    for i, board in enumerate(boards[:max_items], 1):
                        print(f"   Board {i}: {board.get('name', 'N/A')} (ID: {board.get('id', 'N/A')})")
            # Handle item results
            elif "items" in data:
                items = data["items"]
                print(f"   Total items: {len(items)}")
                if items:
                    print(f"   Showing first {min(len(items), max_items)} items:")
                    for i, item in enumerate(items[:max_items], 1):
                        print(f"   Item {i}: {item.get('name', 'N/A')} (ID: {item.get('id', 'N/A')})")
            # Handle user results
            elif "me" in data:
                user = data["me"]
                print(f"   User: {user.get('name', 'N/A')} ({user.get('email', 'N/A')})")
                print(f"   ID: {user.get('id', 'N/A')}")
            elif "users" in data:
                users = data["users"]
                print(f"   Total users: {len(users)}")
                if users:
                    print(f"   Showing first {min(len(users), max_items)} users:")
                    for i, user in enumerate(users[:max_items], 1):
                        print(f"   User {i}: {user.get('name', 'N/A')} ({user.get('email', 'N/A')})")
            # Handle workspace results
            elif "workspaces" in data:
                workspaces = data["workspaces"]
                print(f"   Total workspaces: {len(workspaces)}")
                if workspaces:
                    print(f"   Showing first {min(len(workspaces), max_items)} workspaces:")
                    for i, ws in enumerate(workspaces[:max_items], 1):
                        print(f"   Workspace {i}: {ws.get('name', 'N/A')} (ID: {ws.get('id', 'N/A')})")
            # Handle create/mutation results
            elif "create_board" in data:
                board = data["create_board"]
                print(f"   Created Board: {board.get('name', 'N/A')} (ID: {board.get('id', 'N/A')})")
            elif "create_item" in data:
                item = data["create_item"]
                print(f"   Created Item: {item.get('name', 'N/A')} (ID: {item.get('id', 'N/A')})")
            elif "create_group" in data:
                group = data["create_group"]
                print(f"   Created Group: {group.get('title', 'N/A')} (ID: {group.get('id', 'N/A')})")
            elif "create_column" in data:
                column = data["create_column"]
                print(f"   Created Column: {column.get('title', 'N/A')} (ID: {column.get('id', 'N/A')})")
            elif "create_update" in data:
                update = data["create_update"]
                print(f"   Created Update ID: {update.get('id', 'N/A')}")
            # Handle account info
            elif "account" in data:
                account = data["account"]
                print(f"   Account: {account.get('name', 'N/A')}")
                plan = account.get('plan', {})
                if isinstance(plan, dict):
                    print(f"   Plan: {plan.get('tier', 'N/A')}")
            # Handle tags
            elif "tags" in data:
                tags = data["tags"]
                print(f"   Total tags: {len(tags)}")
                if tags:
                    print(f"   Sample tags: {[tag.get('name', 'N/A') for tag in tags[:5]]}")
            # Handle teams
            elif "teams" in data:
                teams = data["teams"]
                print(f"   Total teams: {len(teams)}")
                if teams:
                    print(f"   Sample teams: {[team.get('name', 'N/A') for team in teams[:5]]}")
            # Handle delete results
            elif "delete_board" in data:
                print(f"   Board deleted: {data['delete_board'].get('id', 'N/A')}")
            elif "archive_item" in data:
                print(f"   Item archived: {data['archive_item'].get('id', 'N/A')}")
            # Generic response
            else:
                print(f"   Data preview: {json.dumps(data, indent=2)[:500]}...")
    else:
        print(f"❌ {name}: Failed")
        if response.message:
            print(f"   Message: {response.message}")
        if hasattr(response, 'errors') and response.errors:
            print(f"   Errors: {response.errors}")


async def main() -> None:
    # 1. Initialize Client
    print_section("Initializing Monday.com Client")

    config = None

    if CLIENT_ID and CLIENT_SECRET:
        # OAuth2 authentication (highest priority)
        print("ℹ️  Using OAuth2 authentication")
        try:
            print("Starting OAuth flow...")

            token_response = perform_oauth_flow(
                client_id=CLIENT_ID,
                client_secret=CLIENT_SECRET,
                auth_endpoint=MONDAY_AUTH_ENDPOINT,
                token_endpoint=MONDAY_TOKEN_ENDPOINT,
                redirect_uri=REDIRECT_URI,
                scopes=[
                    "me:read",
                    "boards:read",
                    "boards:write",
                    "docs:read",
                    "docs:write",
                    "workspaces:read",
                    "workspaces:write",
                    "users:read",
                    "users:write",
                    "account:read",
                    "notifications:write",
                    "updates:read",
                    "updates:write",
                    "assets:read",
                    "tags:read",
                    "teams:read",
                    "teams:write",
                    "webhooks:read",
                    "webhooks:write",
                ],
                scope_delimiter=" ",
                auth_method="body",  # Monday.com uses POST body for token exchange
            )

            access_token = token_response.get("access_token")

            if not access_token:
                raise Exception("No access_token found in OAuth response")

            config = MondayOAuthConfig(
                oauth_token=access_token,
                api_version=API_VERSION
            )
            print("✅ OAuth authentication successful")

        except Exception as e:
            print(f"❌ OAuth flow failed: {e}")
            print("⚠️  Falling back to API Token authentication...")

    if config is None and API_TOKEN:
        # API Token authentication (fallback)
        print("ℹ️  Using API Token authentication")
        config = MondayTokenConfig(
            token=API_TOKEN,
            api_version=API_VERSION
        )

    if config is None:
        print("⚠️  No valid authentication method found.")
        print("   Please set one of the following:")
        print("   - MONDAY_CLIENT_ID and MONDAY_CLIENT_SECRET (for OAuth2)")
        print("   - MONDAY_API_TOKEN (for API Token)")
        return

    client = MondayClient.build_with_config(config)
    data_source = MondayDataSource(client)
    print(f"✅ Client initialized successfully!")
    print(f"   Official Monday SDK available: {client.is_sdk_available()}")

    # 2. Get Current User
    print_section("Get Current User (me)")
    me_resp = await data_source.me()
    print_result("Current User", me_resp)

    # 3. Get Account Information
    print_section("Get Account Information")
    account_resp = await data_source.account()
    print_result("Account", account_resp)

    # 4. Get All Users
    print_section("Get Users")
    users_resp = await data_source.users(limit=10)
    print_result("Users", users_resp)

    # 5. Get All Boards
    print_section("Get Boards")
    boards_resp = await data_source.boards(limit=10)
    print_result("Boards", boards_resp)

    # Store a board ID for later operations
    test_board_id = None
    if boards_resp.success and boards_resp.data:
        boards = boards_resp.data.get("boards", [])
        if boards:
            test_board_id = boards[0].get("id")
            print(f"   Using board ID for tests: {test_board_id}")

    # 6. Get Board Details with Columns and Groups
    if test_board_id:
        print_section(f"Get Board Details - {test_board_id}")
        board_detail_resp = await data_source.boards(ids=[test_board_id], limit=1)
        print_result("Board Details", board_detail_resp, show_data=False)

        if board_detail_resp.success and board_detail_resp.data:
            board_data = board_detail_resp.data.get("boards", [{}])[0]

            # Show columns
            columns = board_data.get("columns", [])
            print(f"   Columns ({len(columns)}):")
            for col in columns[:5]:
                print(f"     - {col.get('title', 'N/A')} ({col.get('id', 'N/A')}, type: {col.get('type', 'N/A')})")

            # Show groups
            groups = board_data.get("groups", [])
            print(f"   Groups ({len(groups)}):")
            for grp in groups[:5]:
                print(f"     - {grp.get('title', 'N/A')} ({grp.get('id', 'N/A')})")

    # 7. Get Workspaces
    print_section("Get Workspaces")
    workspaces_resp = await data_source.workspaces(limit=10)
    print_result("Workspaces", workspaces_resp)

    # 8. Get Tags
    print_section("Get Tags")
    tags_resp = await data_source.tags()
    print_result("Tags", tags_resp)

    # 9. Get Teams
    print_section("Get Teams")
    teams_resp = await data_source.teams()
    print_result("Teams", teams_resp)

    # 10. Create a Test Board
    print_section("Create Test Board")
    create_board_resp = await data_source.create_board(
        board_name="API Test Board",
        board_kind="public",
        description="Board created via API for testing"
    )
    print_result("Create Board", create_board_resp)

    created_board_id = None
    if create_board_resp.success and create_board_resp.data:
        created_board_id = create_board_resp.data.get("create_board", {}).get("id")
        print(f"   Created board ID: {created_board_id}")

    if created_board_id:
        # 11. Create a Group on the board
        print_section(f"Create Group on Board {created_board_id}")
        create_group_resp = await data_source.create_group(
            board_id=created_board_id,
            group_name="Test Group",
            group_color="#0086c0"
        )
        print_result("Create Group", create_group_resp)

        created_group_id = None
        if create_group_resp.success and create_group_resp.data:
            created_group_id = create_group_resp.data.get("create_group", {}).get("id")
            print(f"   Created group ID: {created_group_id}")

        # 12. Create a Column on the board
        print_section(f"Create Column on Board {created_board_id}")
        create_column_resp = await data_source.create_column(
            board_id=created_board_id,
            title="Priority",
            column_type="status",
            description="Priority level for the item"
        )
        print_result("Create Column", create_column_resp)

        # 13. Create an Item on the board
        print_section(f"Create Item on Board {created_board_id}")
        create_item_resp = await data_source.create_item(
            board_id=created_board_id,
            item_name="Test Item from API",
            group_id=created_group_id
        )
        print_result("Create Item", create_item_resp)

        created_item_id = None
        if create_item_resp.success and create_item_resp.data:
            created_item_id = create_item_resp.data.get("create_item", {}).get("id")
            print(f"   Created item ID: {created_item_id}")

        # 14. Add an Update/Comment to the Item
        if created_item_id:
            print_section(f"Add Update to Item {created_item_id}")
            create_update_resp = await data_source.create_update(
                item_id=created_item_id,
                body="This is a test comment added via the API!"
            )
            print_result("Create Update", create_update_resp)

            # 15. Duplicate the Item
            print_section(f"Duplicate Item {created_item_id}")
            duplicate_resp = await data_source.duplicate_item(
                board_id=created_board_id,
                item_id=created_item_id,
                with_updates=True
            )
            print_result("Duplicate Item", duplicate_resp)

            # 16. Archive the Item
            print_section(f"Archive Item {created_item_id}")
            archive_item_resp = await data_source.archive_item(item_id=created_item_id)
            print_result("Archive Item", archive_item_resp)

        # 17. Delete the test board (cleanup)
        print_section(f"Delete Test Board {created_board_id}")
        delete_board_resp = await data_source.delete_board(board_id=created_board_id)
        print_result("Delete Board", delete_board_resp)

    # 18. Get API Version
    print_section("Get API Version")
    version_resp = await data_source.version()
    print_result("Version", version_resp)

    # 19. Get Complexity/Rate Limit Info
    print_section("Get Complexity Information")
    complexity_resp = await data_source.complexity()
    print_result("Complexity", complexity_resp)

    # 20. Validate Connection
    print_section("Validate Connection")
    is_valid = await data_source.validate_connection()
    print(f"✅ Connection Valid: {is_valid}" if is_valid else f"❌ Connection Valid: {is_valid}")

    # 21. Get Operation Info
    print_section("Data Source Operation Info")
    op_info = data_source.get_operation_info()
    print(f"   Total Methods: {op_info['total_methods']}")
    print(f"   Queries: {op_info['queries']}")
    print(f"   Mutations: {op_info['mutations']}")
    print(f"   Coverage:")
    for entity, coverage in list(op_info['coverage'].items())[:5]:
        print(f"     - {entity}: {coverage}")

    print("\n" + "=" * 80)
    print("✅ All examples completed successfully!")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
