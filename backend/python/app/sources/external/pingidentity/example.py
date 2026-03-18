# ruff: noqa

"""
Ping Identity (PingOne) API Usage Examples

This example demonstrates how to use the PingIdentity DataSource to interact
with the PingOne Platform API, covering:
- Authentication (OAuth2 client_credentials, Bearer Token)
- Initializing the Client and DataSource
- Listing Users, Groups, Populations, Applications
- Fetching Sign-On Policies, Schemas, Identity Providers

Prerequisites:
For OAuth2 (client_credentials):
1. Create a worker application in PingOne Admin console
2. Set PINGONE_ENVIRONMENT_ID, PINGONE_CLIENT_ID, and
   PINGONE_CLIENT_SECRET environment variables

For Bearer Token:
1. Obtain a token via PingOne token endpoint
2. Set PINGONE_ENVIRONMENT_ID and PINGONE_TOKEN environment variables
"""

import asyncio
import json
import os

from app.sources.client.pingidentity.pingidentity import (
    PingIdentityClient,
    PingIdentityOAuthConfig,
    PingIdentityResponse,
    PingIdentityTokenConfig,
)
from app.sources.external.pingidentity.pingidentity import PingIdentityDataSource

# --- Configuration ---
ENVIRONMENT_ID = os.getenv("PINGONE_ENVIRONMENT_ID")

# OAuth2 credentials
CLIENT_ID = os.getenv("PINGONE_CLIENT_ID")
CLIENT_SECRET = os.getenv("PINGONE_CLIENT_SECRET")

# Bearer Token
TOKEN = os.getenv("PINGONE_TOKEN")


def print_section(title: str):
    print(f"\n{'-'*80}")
    print(f"| {title}")
    print(f"{'-'*80}")


def print_result(name: str, response: PingIdentityResponse, show_data: bool = True):
    if response.success:
        print(f"  {name}: Success")
        if show_data and response.data:
            data = response.data
            if isinstance(data, dict):
                # PingOne uses _embedded for collections
                embedded = data.get("_embedded", {})
                for key in embedded:
                    items = embedded[key]
                    if isinstance(items, list):
                        print(f"   Found {len(items)} {key}.")
                        if items:
                            print(f"   Sample: {json.dumps(items[0], indent=2)[:400]}...")
                        return
                print(f"   Data: {json.dumps(data, indent=2)[:500]}...")
            elif isinstance(data, list):
                print(f"   Found {len(data)} items.")
                if data:
                    print(f"   Sample: {json.dumps(data[0], indent=2)[:400]}...")
    else:
        print(f"  {name}: Failed")
        print(f"   Error: {response.error}")
        if response.message:
            print(f"   Message: {response.message}")


async def main() -> None:
    # 1. Initialize Client
    print_section("Initializing PingIdentity (PingOne) Client")

    if not ENVIRONMENT_ID:
        print("  PINGONE_ENVIRONMENT_ID is required.")
        return

    config = None

    # Priority 1: OAuth2 client_credentials
    if CLIENT_ID and CLIENT_SECRET:
        print("  Using OAuth2 client_credentials authentication")
        config = PingIdentityOAuthConfig(
            environment_id=ENVIRONMENT_ID,
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
        )

    # Priority 2: Bearer Token
    if config is None and TOKEN:
        print("  Using Bearer Token authentication")
        config = PingIdentityTokenConfig(
            token=TOKEN,
            environment_id=ENVIRONMENT_ID,
        )

    if config is None:
        print("  No valid authentication method found.")
        print("   Please set one of the following:")
        print("   - PINGONE_CLIENT_ID and PINGONE_CLIENT_SECRET (for OAuth2)")
        print("   - PINGONE_TOKEN (for Bearer Token)")
        return

    client = PingIdentityClient.build_with_config(config)
    data_source = PingIdentityDataSource(client)
    print("Client initialized successfully.")

    try:
        # 2. List Users
        print_section("Users")
        users_resp = await data_source.list_users(limit=10)
        print_result("List Users", users_resp)

        # 3. Get a specific user if available
        if users_resp.success and isinstance(users_resp.data, dict):
            embedded = users_resp.data.get("_embedded", {})
            users = embedded.get("users", []) if isinstance(embedded, dict) else []
            if isinstance(users, list) and users:
                user_id = str(users[0].get("id", ""))
                if user_id:
                    print_section(f"User Details: {user_id}")
                    user_resp = await data_source.get_user(user_id=user_id)
                    print_result("Get User", user_resp)

        # 4. List Groups
        print_section("Groups")
        groups_resp = await data_source.list_groups(limit=10)
        print_result("List Groups", groups_resp)

        # 5. List Populations
        print_section("Populations")
        pop_resp = await data_source.list_populations(limit=10)
        print_result("List Populations", pop_resp)

        # 6. List Applications
        print_section("Applications")
        apps_resp = await data_source.list_applications(limit=10)
        print_result("List Applications", apps_resp)

        # 7. List Sign-On Policies
        print_section("Sign-On Policies")
        policies_resp = await data_source.list_sign_on_policies(limit=10)
        print_result("List Sign-On Policies", policies_resp)

        # 8. List Schemas
        print_section("Schemas")
        schemas_resp = await data_source.list_schemas()
        print_result("List Schemas", schemas_resp)

        # 9. List Password Policies
        print_section("Password Policies")
        pw_resp = await data_source.list_password_policies()
        print_result("List Password Policies", pw_resp)

        # 10. List Identity Providers
        print_section("Identity Providers")
        idp_resp = await data_source.list_identity_providers()
        print_result("List Identity Providers", idp_resp)

        # 11. List Gateways
        print_section("Gateways")
        gw_resp = await data_source.list_gateways()
        print_result("List Gateways", gw_resp)

    finally:
        # Cleanup: Close the HTTP client session
        print("\nClosing client connection...")
        inner_client = client.get_client()
        if hasattr(inner_client, "close"):
            await inner_client.close()

    print("\n" + "=" * 80)
    print("  All PingIdentity API operations tested!")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
