# ruff: noqa

"""
JumpCloud API Usage Examples

This example demonstrates how to use the JumpCloud DataSource to interact
with the JumpCloud API, covering:
- Authentication (API Key)
- Initializing the Client and DataSource
- Listing users and user groups
- Listing systems and system groups
- Listing applications and directories
- Listing policies, organizations, and RADIUS servers

Prerequisites:
1. Log in to the JumpCloud Admin Console
2. Go to your user profile (top right) or API Settings
3. Generate or copy your API Key
4. Set JUMPCLOUD_API_KEY environment variable
"""

import asyncio
import json
import os

from app.sources.client.jumpcloud.jumpcloud import (
    JumpCloudApiKeyConfig,
    JumpCloudClient,
    JumpCloudResponse,
)
from app.sources.external.jumpcloud.jumpcloud import JumpCloudDataSource

# --- Configuration ---
API_KEY = os.getenv("JUMPCLOUD_API_KEY")


def print_section(title: str):
    print(f"\n{'-'*80}")
    print(f"| {title}")
    print(f"{'-'*80}")


def print_result(name: str, response: JumpCloudResponse, show_data: bool = True):
    if response.success:
        print(f"  {name}: Success")
        if show_data and response.data:
            data = response.data
            if isinstance(data, list):
                print(f"   Found {len(data)} items.")
                if data:
                    print(f"   Sample: {json.dumps(data[0], indent=2)[:400]}...")
            elif isinstance(data, dict):
                if "results" in data:
                    items = data["results"]
                    print(f"   Found {len(items)} items.")
                    if items:
                        print(f"   Sample: {json.dumps(items[0], indent=2)[:400]}...")
                else:
                    print(f"   Data: {json.dumps(data, indent=2)[:500]}...")
    else:
        print(f"  {name}: Failed")
        print(f"   Error: {response.error}")
        if response.message:
            print(f"   Message: {response.message}")


async def main() -> None:
    # 1. Initialize Client
    print_section("Initializing JumpCloud Client")

    if not API_KEY:
        print("  No valid authentication method found.")
        print("   Please set the following environment variable:")
        print("   - JUMPCLOUD_API_KEY")
        return

    print("  Using API Key authentication")
    config = JumpCloudApiKeyConfig(api_key=API_KEY)

    client = JumpCloudClient.build_with_config(config)
    data_source = JumpCloudDataSource(client)
    print("  Client initialized successfully.")

    try:
        # 2. List Users
        print_section("Users")
        users_resp = await data_source.list_users(limit=5)
        print_result("List Users", users_resp)

        # Get first user detail
        user_id = None
        if users_resp.success and users_resp.data:
            items = users_resp.data if isinstance(users_resp.data, list) else users_resp.data.get("results", []) if isinstance(users_resp.data, dict) else []
            if items:
                user_id = str(items[0].get("id", "") or items[0].get("_id", ""))
                if user_id:
                    print_section(f"User Details: {user_id}")
                    user_resp = await data_source.get_user(user_id=user_id)
                    print_result("Get User", user_resp)

        # 3. List User Groups
        print_section("User Groups")
        ugroups_resp = await data_source.list_user_groups(limit=5)
        print_result("List User Groups", ugroups_resp)

        # 4. List System Groups
        print_section("System Groups")
        sgroups_resp = await data_source.list_system_groups(limit=5)
        print_result("List System Groups", sgroups_resp)

        # 5. List Systems
        print_section("Systems")
        systems_resp = await data_source.list_systems(limit=5)
        print_result("List Systems", systems_resp)

        # 6. List Applications
        print_section("Applications")
        apps_resp = await data_source.list_applications(limit=5)
        print_result("List Applications", apps_resp)

        # 7. List Directories
        print_section("Directories")
        dirs_resp = await data_source.list_directories(limit=5)
        print_result("List Directories", dirs_resp)

        # 8. List Policies
        print_section("Policies")
        policies_resp = await data_source.list_policies(limit=5)
        print_result("List Policies", policies_resp)

        # 9. List Organizations
        print_section("Organizations")
        orgs_resp = await data_source.list_organizations(limit=5)
        print_result("List Organizations", orgs_resp)

        # 10. List RADIUS Servers
        print_section("RADIUS Servers")
        radius_resp = await data_source.list_radius_servers(limit=5)
        print_result("List RADIUS Servers", radius_resp)

    finally:
        # Cleanup: Close the HTTP client session
        print("\nClosing client connection...")
        inner_client = client.get_client()
        if hasattr(inner_client, "close"):
            await inner_client.close()

    print("\n" + "=" * 80)
    print("  All JumpCloud API operations tested!")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
