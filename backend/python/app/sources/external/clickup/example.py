# ruff: noqa

"""
ClickUp API Usage Examples

This example demonstrates how to use the ClickUp DataSource to interact with
the ClickUp API (v2/v3), covering:
- Authentication (OAuth2, Personal Token)
- Initializing the Client and DataSource
- Fetching User Details and Workspaces
- Listing Spaces, Folders, Lists, and Tasks
- Creating and Managing Tasks

Prerequisites:
For OAuth2:
1. Create a ClickUp OAuth app at Settings > Apps > Create new app
2. Set CLICKUP_CLIENT_ID and CLICKUP_CLIENT_SECRET environment variables
3. The OAuth flow will automatically open a browser for authorization

For Personal Token:
1. Log in to ClickUp
2. Go to Settings > Apps > API Token > Generate
3. Set CLICKUP_PERSONAL_TOKEN environment variable (starts with pk_)

API Version:
Set CLICKUP_API_VERSION to "v2" or "v3" (default: "v2")
"""

import asyncio
import json
import os

from app.sources.client.clickup.clickup import (
    ClickUpClient,
    ClickUpOAuthConfig,
    ClickUpPersonalTokenConfig,
    ClickUpResponse,
)
from app.sources.external.clickup.clickup import ClickUpDataSource
from app.sources.external.utils.oauth import perform_oauth_flow

# --- Configuration ---
# OAuth2 credentials (highest priority)
CLIENT_ID = os.getenv("CLICKUP_CLIENT_ID")
CLIENT_SECRET = os.getenv("CLICKUP_CLIENT_SECRET")

# Personal Token (second priority)
PERSONAL_TOKEN = os.getenv("CLICKUP_PERSONAL_TOKEN")

# API Version
API_VERSION = os.getenv("CLICKUP_API_VERSION", "v2")

# OAuth redirect URI
REDIRECT_URI = os.getenv("CLICKUP_REDIRECT_URI", "http://localhost:8080/callback")


def print_section(title: str):
    print(f"\n{'-'*80}")
    print(f"| {title}")
    print(f"{'-'*80}")


def print_result(name: str, response: ClickUpResponse, show_data: bool = True):
    if response.success:
        print(f"  {name}: Success")
        if show_data and response.data:
            data = response.data
            # Handle list-type responses (teams, spaces, folders, lists, tasks, etc.)
            for key in ("teams", "spaces", "folders", "lists", "tasks", "comments",
                        "goals", "views", "tags", "webhooks", "time_entries", "members"):
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
    print_section("Initializing ClickUp Client")

    config = None

    # Priority 1: OAuth2
    if CLIENT_ID and CLIENT_SECRET:
        print("  Using OAuth2 authentication")
        try:
            print("Starting OAuth flow...")
            # ClickUp OAuth authorization URL format:
            # https://app.clickup.com/api?client_id={client_id}&redirect_uri={redirect_uri}
            # ClickUp token endpoint: https://api.clickup.com/api/v2/oauth/token
            token_response = perform_oauth_flow(
                client_id=CLIENT_ID,
                client_secret=CLIENT_SECRET,
                auth_endpoint="https://app.clickup.com/api",
                token_endpoint="https://api.clickup.com/api/v2/oauth/token",
                redirect_uri=REDIRECT_URI,
                scopes=[],  # ClickUp doesn't use scopes in the auth URL
                scope_delimiter=" ",
                auth_method="body",  # ClickUp sends credentials in POST body
            )

            access_token = token_response.get("access_token")
            if not access_token:
                raise Exception("No access_token found in OAuth response")

            config = ClickUpOAuthConfig(
                access_token=access_token,
                version=API_VERSION,
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
        config = ClickUpPersonalTokenConfig(
            token=PERSONAL_TOKEN,
            version=API_VERSION,
        )

    if config is None:
        print("  No valid authentication method found.")
        print("   Please set one of the following:")
        print("   - CLICKUP_CLIENT_ID and CLICKUP_CLIENT_SECRET (for OAuth2)")
        print("   - CLICKUP_PERSONAL_TOKEN (for Personal Token, starts with pk_)")
        return

    client = ClickUpClient.build_with_config(config)
    data_source = ClickUpDataSource(client)
    print(f"Client initialized successfully (API {API_VERSION}).")

    try:
        # 2. Get Authorized User
        print_section("Authorized User")
        user_resp = await data_source.get_authorized_user()
        print_result("Get Authorized User", user_resp)

        # 3. Get Workspaces (Teams)
        print_section("Workspaces (Teams)")
        teams_resp = await data_source.get_authorized_teams_workspaces()
        print_result("Get Workspaces", teams_resp)

        # Extract first team_id for further exploration
        team_id = None
        if teams_resp.success and teams_resp.data:
            teams = teams_resp.data.get("teams", [])
            if teams:
                team_id = str(teams[0].get("id"))
                print(f"   Using Workspace: {teams[0].get('name')} (ID: {team_id})")

        if not team_id:
            print("   No workspace found. Skipping further operations.")
            return

        # 4. Get Spaces in Workspace
        print_section("Spaces")
        spaces_resp = await data_source.get_spaces(team_id=team_id)
        print_result("Get Spaces", spaces_resp)

        space_id = None
        if spaces_resp.success and spaces_resp.data:
            spaces = spaces_resp.data.get("spaces", [])
            if spaces:
                space_id = str(spaces[0].get("id"))
                print(f"   Using Space: {spaces[0].get('name')} (ID: {space_id})")

        if not space_id:
            print("   No spaces found. Skipping folder/list/task operations.")
            return

        # 5. Get Folders in Space
        print_section("Folders")
        folders_resp = await data_source.get_folders(space_id=space_id)
        print_result("Get Folders", folders_resp)

        # 6. Get Folderless Lists in Space
        print_section("Folderless Lists")
        lists_resp = await data_source.get_folderless_lists(space_id=space_id)
        print_result("Get Folderless Lists", lists_resp)

        # Try to get a list_id from folders or folderless lists
        list_id = None

        # Check folderless lists first
        if lists_resp.success and lists_resp.data:
            lists = lists_resp.data.get("lists", [])
            if lists:
                list_id = str(lists[0].get("id"))
                print(f"   Using List: {lists[0].get('name')} (ID: {list_id})")

        # If no folderless lists, check folders for lists
        if not list_id and folders_resp.success and folders_resp.data:
            folders = folders_resp.data.get("folders", [])
            for folder in folders:
                folder_id = str(folder.get("id"))
                folder_lists_resp = await data_source.get_lists(folder_id=folder_id)
                if folder_lists_resp.success and folder_lists_resp.data:
                    folder_lists = folder_lists_resp.data.get("lists", [])
                    if folder_lists:
                        list_id = str(folder_lists[0].get("id"))
                        print(f"   Using List: {folder_lists[0].get('name')} (ID: {list_id}) from Folder: {folder.get('name')}")
                        break

        if not list_id:
            print("   No lists found. Skipping task operations.")
            return

        # 7. Get Tasks in List
        print_section("Tasks")
        tasks_resp = await data_source.get_tasks(list_id=list_id, page=0)
        print_result("Get Tasks", tasks_resp)

        # 8. Get a specific task if available
        if tasks_resp.success and tasks_resp.data:
            tasks = tasks_resp.data.get("tasks", [])
            if tasks:
                task_id = str(tasks[0].get("id"))
                print_section(f"Task Details: {tasks[0].get('name')}")
                task_resp = await data_source.get_task(task_id=task_id)
                print_result("Get Task", task_resp)

                # 9. Get Task Comments
                print_section("Task Comments")
                comments_resp = await data_source.get_task_comments(task_id=task_id)
                print_result("Get Task Comments", comments_resp)

        # 10. Get Tags in Space
        print_section("Space Tags")
        tags_resp = await data_source.get_space_tags(space_id=space_id)
        print_result("Get Space Tags", tags_resp)

        # 11. Get Goals
        print_section("Goals")
        goals_resp = await data_source.get_goals(team_id=team_id)
        print_result("Get Goals", goals_resp)

        # 12. Get Views at Workspace Level
        print_section("Workspace Views")
        views_resp = await data_source.get_team_views(team_id=team_id)
        print_result("Get Workspace Views", views_resp)

        # 13. Get Webhooks
        print_section("Webhooks")
        webhooks_resp = await data_source.get_webhooks(team_id=team_id)
        print_result("Get Webhooks", webhooks_resp)

    finally:
        # Cleanup: Close the HTTP client session
        print("\nClosing client connection...")
        inner_client = client.get_client()
        if hasattr(inner_client, "close"):
            await inner_client.close()

    print("\n" + "=" * 80)
    print("  All ClickUp API operations tested!")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
