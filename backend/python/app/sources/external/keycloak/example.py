# ruff: noqa

"""
Keycloak API Usage Examples

This example demonstrates how to use the Keycloak DataSource to interact with
the Keycloak Admin REST API, covering:
- Authentication (OAuth2 client_credentials, Bearer Token)
- Initializing the Client and DataSource
- Listing Users, Groups, Clients, Roles
- Fetching Events and Identity Providers

Prerequisites:
For OAuth2 (client_credentials):
1. Create a client in Keycloak with "Service accounts roles" enabled
2. Assign realm-management roles to the service account
3. Set KEYCLOAK_HOSTNAME, KEYCLOAK_REALM, KEYCLOAK_CLIENT_ID,
   and KEYCLOAK_CLIENT_SECRET environment variables

For Bearer Token:
1. Obtain a token via Keycloak token endpoint
2. Set KEYCLOAK_HOSTNAME, KEYCLOAK_REALM, and KEYCLOAK_TOKEN
   environment variables
"""

import asyncio
import json
import os

from app.sources.client.keycloak.keycloak import (
    KeycloakClient,
    KeycloakOAuthConfig,
    KeycloakResponse,
    KeycloakTokenConfig,
)
from app.sources.external.keycloak.keycloak import KeycloakDataSource

# --- Configuration ---
HOSTNAME = os.getenv("KEYCLOAK_HOSTNAME")
REALM = os.getenv("KEYCLOAK_REALM", "master")

# OAuth2 credentials
CLIENT_ID = os.getenv("KEYCLOAK_CLIENT_ID")
CLIENT_SECRET = os.getenv("KEYCLOAK_CLIENT_SECRET")

# Bearer Token
TOKEN = os.getenv("KEYCLOAK_TOKEN")


def print_section(title: str):
    print(f"\n{'-'*80}")
    print(f"| {title}")
    print(f"{'-'*80}")


def print_result(name: str, response: KeycloakResponse, show_data: bool = True):
    if response.success:
        print(f"  {name}: Success")
        if show_data and response.data:
            data = response.data
            if isinstance(data, list):
                print(f"   Found {len(data)} items.")
                if data:
                    print(f"   Sample: {json.dumps(data[0], indent=2)[:400]}...")
            elif isinstance(data, dict):
                print(f"   Data: {json.dumps(data, indent=2)[:500]}...")
    else:
        print(f"  {name}: Failed")
        print(f"   Error: {response.error}")
        if response.message:
            print(f"   Message: {response.message}")


async def main() -> None:
    # 1. Initialize Client
    print_section("Initializing Keycloak Client")

    if not HOSTNAME:
        print("  KEYCLOAK_HOSTNAME is required.")
        return

    config = None

    # Priority 1: OAuth2 client_credentials
    if CLIENT_ID and CLIENT_SECRET:
        print("  Using OAuth2 client_credentials authentication")
        config = KeycloakOAuthConfig(
            hostname=HOSTNAME,
            realm=REALM,
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
        )

    # Priority 2: Bearer Token
    if config is None and TOKEN:
        print("  Using Bearer Token authentication")
        config = KeycloakTokenConfig(
            token=TOKEN,
            hostname=HOSTNAME,
            realm=REALM,
        )

    if config is None:
        print("  No valid authentication method found.")
        print("   Please set one of the following:")
        print("   - KEYCLOAK_CLIENT_ID and KEYCLOAK_CLIENT_SECRET (for OAuth2)")
        print("   - KEYCLOAK_TOKEN (for Bearer Token)")
        return

    client = KeycloakClient.build_with_config(config)
    data_source = KeycloakDataSource(client)
    print("Client initialized successfully.")

    try:
        # 2. List Users
        print_section("Users")
        users_resp = await data_source.list_users(max=10)
        print_result("List Users", users_resp)

        # 3. Get Users Count
        print_section("Users Count")
        count_resp = await data_source.get_users_count()
        print_result("Users Count", count_resp)

        # 4. Get a specific user if available
        if users_resp.success and isinstance(users_resp.data, list) and users_resp.data:
            user_id = str(users_resp.data[0].get("id", ""))
            if user_id:
                print_section(f"User Details: {user_id}")
                user_resp = await data_source.get_user(user_id=user_id)
                print_result("Get User", user_resp)

        # 5. List Groups
        print_section("Groups")
        groups_resp = await data_source.list_groups(max=10)
        print_result("List Groups", groups_resp)

        # 6. List Clients
        print_section("Clients")
        clients_resp = await data_source.list_clients(max=10)
        print_result("List Clients", clients_resp)

        # 7. List Roles
        print_section("Roles")
        roles_resp = await data_source.list_roles(max=10)
        print_result("List Roles", roles_resp)

        # 8. List Events
        print_section("Events")
        events_resp = await data_source.list_events(max=10)
        print_result("List Events", events_resp)

        # 9. List Identity Providers
        print_section("Identity Providers")
        idp_resp = await data_source.list_identity_providers()
        print_result("List Identity Providers", idp_resp)

        # 10. List Authentication Flows
        print_section("Authentication Flows")
        flows_resp = await data_source.list_authentication_flows()
        print_result("List Authentication Flows", flows_resp)

    finally:
        # Cleanup: Close the HTTP client session
        print("\nClosing client connection...")
        inner_client = client.get_client()
        if hasattr(inner_client, "close"):
            await inner_client.close()

    print("\n" + "=" * 80)
    print("  All Keycloak API operations tested!")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
