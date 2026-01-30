# ruff: noqa

"""
Salesforce API Usage Examples

This example demonstrates how to use the Salesforce DataSource to interact with
the Salesforce REST API, covering:
- Authentication (OAuth2 or Access Token)
- Initializing the Client and DataSource
- GET methods for Accounts, Users, Groups, Roles, and Contacts

Prerequisites:
For OAuth2:
1. Create a Salesforce Connected App at Setup > App Manager > New Connected App
2. Set SALESFORCE_CLIENT_ID and SALESFORCE_CLIENT_SECRET environment variables
3. Set SALESFORCE_INSTANCE_URL environment variable (e.g., https://login.salesforce.com or https://test.salesforce.com)
4. The OAuth flow will automatically open a browser for authorization

For Access Token:
1. Set SALESFORCE_ACCESS_TOKEN and SALESFORCE_INSTANCE_URL environment variables
"""

import asyncio
import json
import os

from app.sources.client.salesforce.salesforce import (
    SalesforceClient,
    SalesforceConfig,
    SalesforceResponse,
)
from app.sources.external.salesforce.salesforce_data_source import SalesforceDataSource
from app.sources.external.utils.oauth import perform_oauth_flow

# --- Configuration ---
# OAuth2 credentials (highest priority)
CLIENT_ID = os.getenv("SALESFORCE_CLIENT_ID")
CLIENT_SECRET = os.getenv("SALESFORCE_CLIENT_SECRET")
INSTANCE_URL = os.getenv("SALESFORCE_INSTANCE_URL")

# Access Token (second priority)
ACCESS_TOKEN = os.getenv("SALESFORCE_ACCESS_TOKEN")

API_VERSION = os.getenv("SALESFORCE_API_VERSION", "59.0")
REDIRECT_URI = os.getenv("SALESFORCE_REDIRECT_URI", "http://localhost:8080/services/oauth2/success")


def print_section(title: str):
    print(f"\n{'-'*80}")
    print(f"| {title}")
    print(f"{'-'*80}")


def print_result(name: str, response: SalesforceResponse, show_data: bool = True):
    if response.success:
        print(f"✅ {name}: Success")
        if show_data and response.data:
            data = response.data
            
            # Handle SOQL query results
            if isinstance(data, dict) and "records" in data:
                records = data["records"]
                total = data.get("totalSize", len(records))
                print(f"   Total records: {total}")
                if records:
                    print(f"   Showing first {min(len(records), 3)} record(s):")
                    for i, record in enumerate(records[:3], 1):
                        # Clean up attributes for display
                        record_copy = record.copy()
                        if "attributes" in record_copy:
                            del record_copy["attributes"]
                        print(f"   Record {i}: {json.dumps(record_copy, indent=2)[:300]}...")
            # Handle list responses (like Groups)
            elif isinstance(data, dict) and "groups" in data:
                groups = data["groups"]
                print(f"   Total groups: {len(groups)}")
                if groups:
                    print(f"   Showing first {min(len(groups), 3)} group(s):")
                    for i, group in enumerate(groups[:3], 1):
                        print(f"   Group {i}: {json.dumps(group, indent=2)[:300]}...")
            else:
                # Generic response
                print(f"   Data preview: {json.dumps(data, indent=2)[:500]}...")
    else:
        print(f"❌ {name}: Failed")
        print(f"   Error: {response.error}")
        if response.message:
            print(f"   Message: {response.message}")


async def main() -> None:
    # 1. Initialize Client
    print_section("Initializing Salesforce Client")

    # Determine authentication method (priority: OAuth > Access Token)
    config = None
    
    if CLIENT_ID and CLIENT_SECRET and INSTANCE_URL:
        # OAuth2 authentication (highest priority)
        print("ℹ️  Using OAuth2 authentication")
        try:
            print("Starting OAuth flow...")
            auth_endpoint = f"{INSTANCE_URL}/services/oauth2/authorize"
            token_endpoint = f"{INSTANCE_URL}/services/oauth2/token"
            
            token_response = perform_oauth_flow(
                client_id=CLIENT_ID,
                client_secret=CLIENT_SECRET,
                auth_endpoint=auth_endpoint,
                token_endpoint=token_endpoint,
                redirect_uri=REDIRECT_URI,
                scopes=["api", "refresh_token", "offline_access"],
                scope_delimiter=" ",
                auth_method="body",
            )
            
            # Extract access token from response
            access_token = token_response.get("access_token")
            if not access_token:
                raise Exception("No access_token found in OAuth response")
            
            instance_url = token_response.get("instance_url", INSTANCE_URL)
            
            config = SalesforceConfig(
                access_token=access_token,
                instance_url=instance_url,
                api_version=API_VERSION
            )
            print("✅ OAuth authentication successful")
        except Exception as e:
            print(f"❌ OAuth flow failed: {e}")
            print("⚠️  Falling back to other authentication methods...")
            # Continue to check other auth methods
    
    if config is None and ACCESS_TOKEN and INSTANCE_URL:
        # Access Token authentication (second priority)
        print("ℹ️  Using Access Token authentication")
        config = SalesforceConfig(
            access_token=ACCESS_TOKEN,
            instance_url=INSTANCE_URL,
            api_version=API_VERSION
        )
    
    if config is None:
        print("⚠️  No valid authentication method found.")
        print("   Please set one of the following:")
        print("   - SALESFORCE_CLIENT_ID, SALESFORCE_CLIENT_SECRET, and SALESFORCE_INSTANCE_URL (for OAuth2)")
        print("   - SALESFORCE_ACCESS_TOKEN and SALESFORCE_INSTANCE_URL (for Access Token)")
        return

    client = SalesforceClient.build_with_config(config)
    data_source = SalesforceDataSource(client)
    print("Client initialized successfully.")

    try:
        # 2. Get Accounts
        print_section("Accounts")
        accounts_query = "SELECT Id, Name, Type, Industry, Phone FROM Account LIMIT 5"
        print(f"Executing: {accounts_query}")
        accounts_resp = await data_source.soql_query(api_version=API_VERSION, q=accounts_query)
        print_result("Get Accounts", accounts_resp)

        # 3. Get Users
        print_section("Users")
        users_query = "SELECT Id, Username, Email, FirstName, LastName, IsActive FROM User LIMIT 5"
        print(f"Executing: {users_query}")
        users_resp = await data_source.soql_query(api_version=API_VERSION, q=users_query)
        print_result("Get Users", users_resp)

        # 4. Get Groups
        print_section("Groups")
        print("Executing: list_of_groups API call")
        groups_resp = await data_source.list_of_groups(version=API_VERSION)
        print_result("Get Groups", groups_resp)

        # 5. Get Roles
        print_section("Roles")
        roles_query = "SELECT Id, Name, DeveloperName FROM UserRole LIMIT 5"
        print(f"Executing: {roles_query}")
        roles_resp = await data_source.soql_query(api_version=API_VERSION, q=roles_query)
        print_result("Get Roles", roles_resp)

        # 6. Get Contacts
        print_section("Contacts")
        contacts_query = "SELECT Id, FirstName, LastName, Email, Phone, AccountId FROM Contact LIMIT 5"
        print(f"Executing: {contacts_query}")
        contacts_resp = await data_source.soql_query(api_version=API_VERSION, q=contacts_query)
        print_result("Get Contacts", contacts_resp)

    finally:
        # Cleanup: Close the HTTP client session to prevent Event Loop errors
        print("\nClosing client connection...")
        inner_client = client.get_client()
        if hasattr(inner_client, "close"):
            await inner_client.close()

    print("\n" + "=" * 80)
    print("✅ All get methods tested!")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
