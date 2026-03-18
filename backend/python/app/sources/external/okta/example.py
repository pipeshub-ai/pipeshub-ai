# ruff: noqa

"""
Okta API Usage Examples (SDK-backed)

This example demonstrates how to use the Okta DataSource backed by the
official ``okta`` Python SDK, covering:
- Authentication (API Token)
- Initialising the Client and DataSource
- Listing users, groups, and applications
- Getting system logs
- Listing authorization servers and policies

Prerequisites:
For API Token:
1. Log in to Okta Admin Console
2. Go to Security > API > Tokens > Create Token
3. Set OKTA_API_TOKEN and OKTA_DOMAIN environment variables

OKTA_DOMAIN should be the full org URL (e.g. "https://dev-123456.okta.com")
or just the subdomain portion (e.g. "dev-123456").
"""

import asyncio
import os

from app.sources.client.okta.okta import (
    OktaApiTokenConfig,
    OktaClient,
    OktaResponse,
)
from app.sources.external.okta.okta import OktaDataSource

# --- Configuration ---
API_TOKEN = os.getenv("OKTA_API_TOKEN")
DOMAIN = os.getenv("OKTA_DOMAIN")  # e.g. "dev-123456" or "https://dev-123456.okta.com"


def print_section(title: str):
    print(f"\n{'-'*80}")
    print(f"| {title}")
    print(f"{'-'*80}")


def print_result(name: str, response: OktaResponse):
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


async def main() -> None:
    print_section("Initialising Okta Client (SDK-backed)")

    if not DOMAIN:
        print("  OKTA_DOMAIN is required.")
        return

    if not API_TOKEN:
        print("  OKTA_API_TOKEN is required.")
        return

    print("  Using API Token authentication")
    config = OktaApiTokenConfig(api_token=API_TOKEN, domain=DOMAIN)
    client = OktaClient.build_with_config(config)
    data_source = OktaDataSource(client)
    print(f"  Client initialised for {DOMAIN}")

    # 1. List Users
    print_section("Users")
    users_resp = await data_source.list_users(limit=5)
    print_result("List Users", users_resp)

    # 2. Get Current User
    print_section("Current User")
    me_resp = await data_source.get_current_user()
    print_result("Get Current User", me_resp)

    # 3. List Groups
    print_section("Groups")
    groups_resp = await data_source.list_groups(limit=5)
    print_result("List Groups", groups_resp)

    # 4. List Applications
    print_section("Applications")
    apps_resp = await data_source.list_applications(limit=5)
    print_result("List Applications", apps_resp)

    # 5. System Logs
    print_section("System Logs (Recent)")
    logs_resp = await data_source.get_system_logs(limit=5)
    print_result("Get System Logs", logs_resp)

    # 6. Authorization Servers
    print_section("Authorization Servers")
    auth_servers_resp = await data_source.list_authorization_servers()
    print_result("List Authorization Servers", auth_servers_resp)

    # 7. Policies
    print_section("Policies")
    policies_resp = await data_source.list_policies()
    print_result("List Policies", policies_resp)

    print("\n" + "=" * 80)
    print("  All Okta API operations tested!")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
