# ruff: noqa

"""
Loopio API Usage Examples

This example demonstrates how to use the Loopio DataSource to interact with
the Loopio API v1, covering:
- Authentication (API Key / Bearer Token)
- Initializing the Client and DataSource
- Listing Projects, Entries, Library Items
- Fetching Categories, Users, Groups, Tags

Prerequisites:
1. Obtain an API key from Loopio admin settings
2. Set LOOPIO_API_KEY environment variable
"""

import asyncio
import json
import os

from app.sources.client.loopio.loopio import (
    LoopioClient,
    LoopioTokenConfig,
    LoopioResponse,
)
from app.sources.external.loopio.loopio import LoopioDataSource

# --- Configuration ---
API_KEY = os.getenv("LOOPIO_API_KEY")


def print_section(title: str):
    print(f"\n{'-'*80}")
    print(f"| {title}")
    print(f"{'-'*80}")


def print_result(name: str, response: LoopioResponse, show_data: bool = True):
    if response.success:
        print(f"  {name}: Success")
        if show_data and response.data:
            data = response.data
            if isinstance(data, list):
                print(f"   Found {len(data)} items.")
                if data:
                    print(f"   Sample: {json.dumps(data[0], indent=2)[:400]}...")
            elif isinstance(data, dict):
                for key in ("projects", "entries", "library", "categories",
                            "users", "groups", "tags"):
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
    print_section("Initializing Loopio Client")

    if not API_KEY:
        print("  No valid authentication method found.")
        print("   Please set LOOPIO_API_KEY environment variable.")
        return

    print("  Using API Key authentication")
    config = LoopioTokenConfig(token=API_KEY)
    client = LoopioClient.build_with_config(config)
    data_source = LoopioDataSource(client)
    print("Client initialized successfully.")

    try:
        # 2. Get Projects
        print_section("Projects")
        projects_resp = await data_source.get_projects(limit=10)
        print_result("Get Projects", projects_resp)

        project_id = None
        if projects_resp.success and projects_resp.data:
            data = projects_resp.data
            items = data if isinstance(data, list) else data.get("projects", []) if isinstance(data, dict) else []
            if items:
                project_id = str(items[0].get("id"))
                print(f"   Using Project ID: {project_id}")

        if project_id:
            print_section("Project Details")
            project_resp = await data_source.get_project(project_id)
            print_result("Get Project", project_resp)

        # 3. Get Entries
        print_section("Entries")
        entries_resp = await data_source.get_entries(limit=10)
        print_result("Get Entries", entries_resp)

        # 4. Get Library Items
        print_section("Library Items")
        library_resp = await data_source.get_library_items(limit=10)
        print_result("Get Library Items", library_resp)

        # 5. Get Categories
        print_section("Categories")
        categories_resp = await data_source.get_categories(limit=10)
        print_result("Get Categories", categories_resp)

        # 6. Get Users
        print_section("Users")
        users_resp = await data_source.get_users(limit=10)
        print_result("Get Users", users_resp)

        # 7. Get Groups
        print_section("Groups")
        groups_resp = await data_source.get_groups(limit=10)
        print_result("Get Groups", groups_resp)

        # 8. Get Tags
        print_section("Tags")
        tags_resp = await data_source.get_tags(limit=10)
        print_result("Get Tags", tags_resp)

    finally:
        # Cleanup: Close the HTTP client session
        print("\nClosing client connection...")
        inner_client = client.get_client()
        if hasattr(inner_client, "close"):
            await inner_client.close()

    print("\n" + "=" * 80)
    print("  All Loopio API operations tested!")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
