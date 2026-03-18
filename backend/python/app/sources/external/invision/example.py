# ruff: noqa

"""
InVision API Usage Examples

This example demonstrates how to use the InVision DataSource to interact with
the InVision API (v2), covering:
- Authentication (API Key as Bearer Token)
- Initializing the Client and DataSource
- Fetching User Details
- Listing and Managing Projects
- Working with Screens and Comments
- Team and Space Operations

Prerequisites:
1. Obtain an InVision API key from the InVision developer portal
2. Set INVISION_API_KEY environment variable

API Reference: https://developers.invisionapp.com/
"""

import asyncio
import json
import os

from app.sources.client.invision.invision import (
    InVisionClient,
    InVisionTokenConfig,
    InVisionResponse,
)
from app.sources.external.invision.invision import InVisionDataSource

# --- Configuration ---
API_KEY = os.getenv("INVISION_API_KEY")


def print_section(title: str):
    print(f"\n{'-'*80}")
    print(f"| {title}")
    print(f"{'-'*80}")


def print_result(name: str, response: InVisionResponse, show_data: bool = True):
    if response.success:
        print(f"  {name}: Success")
        if show_data and response.data:
            data = response.data
            # Handle list-type responses
            for key in ("projects", "screens", "comments", "teams", "members", "spaces"):
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
    print_section("Initializing InVision Client")

    if not API_KEY:
        print("  No valid authentication method found.")
        print("   Please set INVISION_API_KEY environment variable.")
        return

    print("  Using API Key authentication")
    config = InVisionTokenConfig(api_key=API_KEY)
    client = InVisionClient.build_with_config(config)
    data_source = InVisionDataSource(client)
    print("Client initialized successfully.")

    try:
        # 2. Get Current User
        print_section("Current User")
        user_resp = await data_source.get_current_user()
        print_result("Get Current User", user_resp)

        # 3. List Projects
        print_section("Projects")
        projects_resp = await data_source.list_projects(limit=10)
        print_result("List Projects", projects_resp)

        # Extract first project ID for further exploration
        project_id = None
        if projects_resp.success and projects_resp.data:
            projects = projects_resp.data.get("projects", [])
            if not projects and isinstance(projects_resp.data, list):
                projects = projects_resp.data
            if projects:
                project_id = str(projects[0].get("id") or projects[0].get("projectId", ""))
                print(f"   Using Project: {projects[0].get('name', 'Unknown')} (ID: {project_id})")

        if not project_id:
            print("   No projects found. Skipping project-specific operations.")
        else:
            # 4. Get Specific Project
            print_section("Project Details")
            project_resp = await data_source.get_project(projectId=project_id)
            print_result("Get Project", project_resp)

            # 5. List Project Screens
            print_section("Project Screens")
            screens_resp = await data_source.list_project_screens(projectId=project_id, limit=10)
            print_result("List Project Screens", screens_resp)

            # Get a specific screen if available
            if screens_resp.success and screens_resp.data:
                screens = screens_resp.data.get("screens", [])
                if not screens and isinstance(screens_resp.data, list):
                    screens = screens_resp.data
                if screens:
                    screen_id = str(screens[0].get("id") or screens[0].get("screenId", ""))
                    print_section("Screen Details")
                    screen_resp = await data_source.get_screen(screenId=screen_id)
                    print_result("Get Screen", screen_resp)

            # 6. List Project Comments
            print_section("Project Comments")
            comments_resp = await data_source.list_project_comments(projectId=project_id, limit=10)
            print_result("List Project Comments", comments_resp)

        # 7. List Teams
        print_section("Teams")
        teams_resp = await data_source.list_teams()
        print_result("List Teams", teams_resp)

        # Extract first team ID
        team_id = None
        if teams_resp.success and teams_resp.data:
            teams = teams_resp.data.get("teams", [])
            if not teams and isinstance(teams_resp.data, list):
                teams = teams_resp.data
            if teams:
                team_id = str(teams[0].get("id") or teams[0].get("teamId", ""))
                print(f"   Using Team ID: {team_id}")

        if team_id:
            # 8. Get Team Details
            print_section("Team Details")
            team_resp = await data_source.get_team(teamId=team_id)
            print_result("Get Team", team_resp)

            # 9. List Team Members
            print_section("Team Members")
            members_resp = await data_source.list_team_members(teamId=team_id)
            print_result("List Team Members", members_resp)

        # 10. List Spaces
        print_section("Spaces")
        spaces_resp = await data_source.list_spaces(limit=10)
        print_result("List Spaces", spaces_resp)

    finally:
        # Cleanup: Close the HTTP client session
        print("\nClosing client connection...")
        inner_client = client.get_client()
        if hasattr(inner_client, "close"):
            await inner_client.close()

    print("\n" + "=" * 80)
    print("  All InVision API operations tested!")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
