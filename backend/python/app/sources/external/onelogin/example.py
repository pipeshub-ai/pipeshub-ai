# ruff: noqa

"""
OneLogin API Usage Examples (SDK-backed)

This example demonstrates how to use the OneLogin DataSource backed by the
official ``onelogin`` Python SDK, covering:
- Authentication (OAuth2 client_credentials)
- Initialising the Client and DataSource
- Listing Users, Groups, Roles, Apps
- Fetching Events and Privileges

Prerequisites:
1. Create an API credential pair in OneLogin Admin portal
   (Developers > API Credentials)
2. Set ONELOGIN_CLIENT_ID and ONELOGIN_CLIENT_SECRET environment variables
3. Optionally set ONELOGIN_REGION (default: "us", options: "us", "eu")
"""

import os

from app.sources.client.onelogin.onelogin import (
    OneLoginClient,
    OneLoginClientCredentialsConfig,
    OneLoginResponse,
)
from app.sources.external.onelogin.onelogin import OneLoginDataSource

# --- Configuration ---
CLIENT_ID = os.getenv("ONELOGIN_CLIENT_ID")
CLIENT_SECRET = os.getenv("ONELOGIN_CLIENT_SECRET")
REGION = os.getenv("ONELOGIN_REGION", "us")


def print_section(title: str):
    print(f"\n{'-'*80}")
    print(f"| {title}")
    print(f"{'-'*80}")


def print_result(name: str, response: OneLoginResponse):
    if response.success:
        print(f"  {name}: Success")
        if response.data:
            data = response.data
            if isinstance(data, list):
                print(f"   Found {len(data)} items.")
            else:
                print(f"   Data type: {type(data).__name__}")
    else:
        print(f"  {name}: Failed")
        print(f"   Error: {response.error}")
        if response.message:
            print(f"   Message: {response.message}")


def main() -> None:
    print_section("Initialising OneLogin Client (SDK-backed)")

    if not (CLIENT_ID and CLIENT_SECRET):
        print("  No valid authentication method found.")
        print("   Please set ONELOGIN_CLIENT_ID and ONELOGIN_CLIENT_SECRET")
        return

    print(f"  Using OAuth2 client_credentials authentication (region: {REGION})")
    config = OneLoginClientCredentialsConfig(
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        region=REGION,
    )

    client = OneLoginClient.build_with_config(config)
    data_source = OneLoginDataSource(client)
    print("  Client initialised successfully.")

    # 1. List Users
    print_section("Users")
    users_resp = data_source.list_users(limit=10)
    print_result("List Users", users_resp)

    # 2. List Groups
    print_section("Groups")
    groups_resp = data_source.list_groups()
    print_result("List Groups", groups_resp)

    # 3. List Roles
    print_section("Roles")
    roles_resp = data_source.list_roles()
    print_result("List Roles", roles_resp)

    # 4. List Apps
    print_section("Apps")
    apps_resp = data_source.list_apps(limit=10)
    print_result("List Apps", apps_resp)

    # 5. List Events
    print_section("Events")
    events_resp = data_source.list_events(limit=10)
    print_result("List Events", events_resp)

    # 6. List Privileges
    print_section("Privileges")
    privs_resp = data_source.list_privileges()
    print_result("List Privileges", privs_resp)

    # 7. List Mappings
    print_section("Mappings")
    mappings_resp = data_source.list_mappings()
    print_result("List Mappings", mappings_resp)

    # 8. List Brands
    print_section("Brands")
    brands_resp = data_source.list_brands()
    print_result("List Brands", brands_resp)

    print("\n" + "=" * 80)
    print("  All OneLogin API operations tested!")
    print("=" * 80)


if __name__ == "__main__":
    main()
