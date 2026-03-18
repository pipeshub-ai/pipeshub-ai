# ruff: noqa

"""
Figma API Usage Examples

This example demonstrates how to use the Figma DataSource to interact with
the Figma API (v1), covering:
- Authentication (OAuth2, Personal Access Token)
- Initializing the Client and DataSource
- Fetching User Details
- Listing Team Projects and Project Files
- Getting File Details and Comments

Prerequisites:
For OAuth2:
1. Create a Figma OAuth app at https://www.figma.com/developers/apps
2. Set FIGMA_CLIENT_ID and FIGMA_CLIENT_SECRET environment variables
3. The OAuth flow will automatically open a browser for authorization

For Personal Access Token:
1. Log in to Figma
2. Go to Settings > Account > Personal access tokens
3. Generate a token and set FIGMA_PERSONAL_TOKEN environment variable

Scopes (OAuth2):
files:read, file_variables:read, file_variables:write,
file_comments:write, file_dev_resources:read, file_dev_resources:write,
webhooks:write
"""

import asyncio
import json
import os

from app.sources.client.figma.figma import (
    FigmaClient,
    FigmaOAuthConfig,
    FigmaResponse,
    FigmaTokenConfig,
)
from app.sources.external.figma.figma import FigmaDataSource
from app.sources.external.utils.oauth import perform_oauth_flow

# --- Configuration ---
# OAuth2 credentials (highest priority)
CLIENT_ID = os.getenv("FIGMA_CLIENT_ID")
CLIENT_SECRET = os.getenv("FIGMA_CLIENT_SECRET")

# Personal Access Token (second priority)
PERSONAL_TOKEN = os.getenv("FIGMA_PERSONAL_TOKEN")

# OAuth redirect URI
REDIRECT_URI = os.getenv("FIGMA_REDIRECT_URI", "http://localhost:8080/callback")

# Figma Team ID (for team-level operations)
TEAM_ID = os.getenv("FIGMA_TEAM_ID")


def print_section(title: str):
    print(f"\n{'-'*80}")
    print(f"| {title}")
    print(f"{'-'*80}")


def print_result(name: str, response: FigmaResponse, show_data: bool = True):
    if response.success:
        print(f"  {name}: Success")
        if show_data and response.data:
            data = response.data
            # Handle list-type responses
            for key in ("projects", "files", "comments", "versions",
                        "meta", "webhooks", "components", "styles"):
                if isinstance(data, dict) and key in data:
                    items = data[key]
                    if isinstance(items, list):
                        print(f"   Found {len(items)} {key}.")
                        if items:
                            print(f"   Sample: {json.dumps(items[0], indent=2)[:400]}...")
                    elif isinstance(items, dict):
                        print(f"   {key}: {json.dumps(items, indent=2)[:400]}...")
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
    print_section("Initializing Figma Client")

    config = None

    # Priority 1: OAuth2
    if CLIENT_ID and CLIENT_SECRET:
        print("  Using OAuth2 authentication")
        try:
            print("Starting OAuth flow...")
            # Figma OAuth authorization URL: https://www.figma.com/oauth
            # Figma token endpoint: https://www.figma.com/api/oauth/token
            token_response = perform_oauth_flow(
                client_id=CLIENT_ID,
                client_secret=CLIENT_SECRET,
                auth_endpoint="https://www.figma.com/oauth",
                token_endpoint="https://www.figma.com/api/oauth/token",
                redirect_uri=REDIRECT_URI,
                scopes=[
                    "files:read",
                    "file_variables:read",
                    "file_variables:write",
                    "file_comments:write",
                    "file_dev_resources:read",
                    "file_dev_resources:write",
                    "webhooks:write",
                ],
                scope_delimiter=",",
                auth_method="body",  # Figma sends client_id/client_secret in POST body
            )

            access_token = token_response.get("access_token")
            if not access_token:
                raise Exception("No access_token found in OAuth response")

            config = FigmaOAuthConfig(
                access_token=access_token,
                client_id=CLIENT_ID,
                client_secret=CLIENT_SECRET,
            )
            print("  OAuth authentication successful")
        except Exception as e:
            print(f"  OAuth flow failed: {e}")
            print("  Falling back to other authentication methods...")

    # Priority 2: Personal Access Token
    if config is None and PERSONAL_TOKEN:
        print("  Using Personal Access Token authentication")
        config = FigmaTokenConfig(token=PERSONAL_TOKEN)

    if config is None:
        print("  No valid authentication method found.")
        print("   Please set one of the following:")
        print("   - FIGMA_CLIENT_ID and FIGMA_CLIENT_SECRET (for OAuth2)")
        print("   - FIGMA_PERSONAL_TOKEN (for Personal Access Token)")
        return

    client = FigmaClient.build_with_config(config)
    data_source = FigmaDataSource(client)
    print("Client initialized successfully.")

    try:
        # 2. Get Current User
        print_section("Current User")
        user_resp = await data_source.get_current_user()
        print_result("Get Current User", user_resp)

        # 3. List Team Projects
        team_id = TEAM_ID
        if not team_id:
            print("\n  FIGMA_TEAM_ID not set. Skipping team-level operations.")
            print("   Set FIGMA_TEAM_ID to test team projects, components, styles, etc.")
        else:
            print_section("Team Projects")
            projects_resp = await data_source.list_team_projects(team_id=team_id)
            print_result("List Team Projects", projects_resp)

            # Extract first project for further exploration
            project_id = None
            if projects_resp.success and projects_resp.data:
                projects = projects_resp.data.get("projects", [])
                if projects:
                    project_id = str(projects[0].get("id"))
                    print(f"   Using Project: {projects[0].get('name')} (ID: {project_id})")

            if project_id:
                # 4. List Project Files
                print_section("Project Files")
                files_resp = await data_source.list_project_files(project_id=project_id)
                print_result("List Project Files", files_resp)

                # Extract first file for file-level operations
                file_key = None
                if files_resp.success and files_resp.data:
                    files = files_resp.data.get("files", [])
                    if files:
                        file_key = str(files[0].get("key"))
                        print(f"   Using File: {files[0].get('name')} (Key: {file_key})")

                if file_key:
                    # 5. Get File
                    print_section("File Details")
                    file_resp = await data_source.get_file(file_key=file_key, depth=1)
                    print_result("Get File", file_resp)

                    # 6. List Comments
                    print_section("File Comments")
                    comments_resp = await data_source.list_comments(file_key=file_key)
                    print_result("List Comments", comments_resp)

                    # 7. List File Versions
                    print_section("File Versions")
                    versions_resp = await data_source.list_file_versions(file_key=file_key)
                    print_result("List File Versions", versions_resp)

                    # 8. List File Components
                    print_section("File Components")
                    components_resp = await data_source.list_file_components(file_key=file_key)
                    print_result("List File Components", components_resp)

                    # 9. List File Styles
                    print_section("File Styles")
                    styles_resp = await data_source.list_file_styles(file_key=file_key)
                    print_result("List File Styles", styles_resp)

            # 10. List Team Components
            print_section("Team Components")
            team_components_resp = await data_source.list_team_components(team_id=team_id, page_size=5)
            print_result("List Team Components", team_components_resp)

            # 11. List Team Styles
            print_section("Team Styles")
            team_styles_resp = await data_source.list_team_styles(team_id=team_id, page_size=5)
            print_result("List Team Styles", team_styles_resp)

            # 12. List Team Webhooks
            print_section("Team Webhooks")
            webhooks_resp = await data_source.list_team_webhooks(team_id=team_id)
            print_result("List Team Webhooks", webhooks_resp)

    finally:
        # Cleanup: Close the HTTP client session
        print("\nClosing client connection...")
        inner_client = client.get_client()
        if hasattr(inner_client, "close"):
            await inner_client.close()

    print("\n" + "=" * 80)
    print("  All Figma API operations tested!")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
