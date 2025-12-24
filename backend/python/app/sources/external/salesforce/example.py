# ruff: noqa
"""
Salesforce API Usage Examples

This example demonstrates how to use the Salesforce DataSource to interact with
the Salesforce REST API, covering:
- Authentication (OAuth2 or Access Token)
- SOQL Query & SOSL Search
- SObject CRUD Operations
- Metadata Discovery
- Composite API (Batch operations)
- Bulk API v2
- UI API (List Views, Favorites)
- System Limits & Recent Items

Prerequisites:
For OAuth2:
1. Create a Connected App in Salesforce Setup
2. Set SALESFORCE_CLIENT_ID and SALESFORCE_CLIENT_SECRET environment variables
3. Set SALESFORCE_INSTANCE_URL (e.g., https://your-domain.my.salesforce.com)
4. Set SALESFORCE_REDIRECT_URI (default: http://localhost:8080/services/oauth2/success)
   - This must match the redirect URI registered in your Salesforce Connected App
5. The OAuth flow will automatically open a browser for authorization

For Access Token (Bearer Token):
1. Set SALESFORCE_ACCESS_TOKEN environment variable
2. Set SALESFORCE_INSTANCE_URL environment variable
"""

import asyncio
import json
import os
from typing import Dict, Any

from app.sources.client.salesforce.salesforce import (
    SalesforceClient,
    SalesforceConfig,
    SalesforceResponse,
)
from app.sources.external.salesforce.salesforce_data_source import SalesforceDataSource
from app.sources.external.utils.oauth import perform_oauth_flow

# --- Configuration ---
CLIENT_ID = os.getenv("SALESFORCE_CLIENT_ID")
CLIENT_SECRET = os.getenv("SALESFORCE_CLIENT_SECRET")
ACCESS_TOKEN = os.getenv("SALESFORCE_ACCESS_TOKEN")
INSTANCE_URL = os.getenv("SALESFORCE_INSTANCE_URL")
API_VERSION = os.getenv("SALESFORCE_API_VERSION", "59.0")
REDIRECT_URI = os.getenv("SALESFORCE_REDIRECT_URI", "http://localhost:8080/services/oauth2/success")

def print_section(title: str):
    print(f"\n{'-'*80}")
    print(f"| {title}")
    print(f"{'-'*80}")

def print_result(name: str, response: SalesforceResponse, show_data: bool = True, max_records: int = 3):
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
                    print(f"   Showing first {min(len(records), max_records)} records:")
                    for i, record in enumerate(records[:max_records], 1):
                        print(f"   Record {i}: {json.dumps(record, indent=2)[:300]}...")
            # Handle list responses
            elif isinstance(data, dict) and "sobjects" in data:
                sobjects = data["sobjects"]
                print(f"   Total objects: {len(sobjects)}")
                print(f"   Sample objects: {[obj['name'] for obj in sobjects[:5]]}")
            # Handle creation/update responses
            elif isinstance(data, dict) and "id" in data:
                print(f"   ID: {data.get('id')}")
                print(f"   Success: {data.get('success', True)}")
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

    config = None
    
    if CLIENT_ID and CLIENT_SECRET and INSTANCE_URL:
        # OAuth2 authentication (highest priority)
        print("ℹ️  Using OAuth2 authentication")
        try:
            print("Starting OAuth flow...")
            
            # Salesforce OAuth endpoints
            # Note: Use your instance URL for the authorization endpoint
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
                auth_method="body",  # Salesforce uses POST body for token exchange
            )
            
            access_token = token_response.get("access_token")
            instance_url = token_response.get("instance_url", INSTANCE_URL)
            
            if not access_token:
                raise Exception("No access_token found in OAuth response")
            
            config = SalesforceConfig(
                instance_url=instance_url,
                access_token=access_token,
                api_version=API_VERSION
            )
            print("✅ OAuth authentication successful")
            
        except Exception as e:
            print(f"❌ OAuth flow failed: {e}")
            print("⚠️  Falling back to access token authentication...")
    
    if config is None and ACCESS_TOKEN and INSTANCE_URL:
        # Access Token authentication (fallback)
        print("ℹ️  Using Access Token authentication")
        config = SalesforceConfig(
            instance_url=INSTANCE_URL,
            access_token=ACCESS_TOKEN,
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
    print(f"Client initialized successfully. Base URL: {client.get_base_url()}")

    # 2. Describe Global - List all SObjects
    print_section("Describe Global - Available SObjects")
    describe_resp = await data_source.describe_global()
    print_result("Describe Global", describe_resp)

    # 3. Describe Specific SObject
    print_section("Describe SObject - Account Metadata")
    account_desc = await data_source.describe_sobject(sobject="Account")
    print_result("Describe Account", account_desc, show_data=False)
    if account_desc.success and account_desc.data:
        fields = account_desc.data.get("fields", [])
        print(f"   Total fields: {len(fields)}")
        print(f"   Sample fields: {[f['name'] for f in fields[:5]]}")

    # 4. SOQL Query - Get Accounts
    print_section("SOQL Query - Fetch Accounts")
    query_resp = await data_source.query(
        q="SELECT Id, Name, Industry, AnnualRevenue FROM Account LIMIT 5"
    )
    print_result("Query Accounts", query_resp)

    # 5. SOSL Search
    print_section("SOSL Search - Find Records")
    search_resp = await data_source.search(
        q="FIND {Test} IN ALL FIELDS RETURNING Account(Id, Name), Contact(Id, Name)"
    )
    print_result("Search Records", search_resp)

    # 6. Create a New Account
    print_section("Create New Account")
    new_account = {
        "Name": "Test Account from API",
        "Industry": "Technology",
        "AnnualRevenue": 1000000
    }
    create_resp = await data_source.create_record(
        data=new_account,
        sobject="Account"
    )
    print_result("Create Account", create_resp)
    
    created_account_id = None
    if create_resp.success and create_resp.data:
        created_account_id = create_resp.data.get("id")

    # 7. Get Record by ID
    if created_account_id:
        print_section(f"Get Account by ID - {created_account_id}")
        get_resp = await data_source.get_record(
            record_id=created_account_id,
            sobject="Account"
        )
        print_result("Get Account", get_resp)

        # 8. Update Record
        print_section(f"Update Account - {created_account_id}")
        update_data = {
            "Phone": "+1-555-0100",
            "Website": "https://testcompany.example.com"
        }
        update_resp = await data_source.update_record(
            data=update_data,
            record_id=created_account_id,
            sobject="Account"
        )
        print_result("Update Account", update_resp)

        # 9. Get Updated Record
        print_section(f"Verify Update - {created_account_id}")
        verify_resp = await data_source.get_record(
            record_id=created_account_id,
            sobject="Account"
        )
        print_result("Get Updated Account", verify_resp)

    # 10. Composite API - Batch Request
    print_section("Composite Batch - Multiple Operations")
    batch_requests = [
        {
            "method": "GET",
            "url": f"v{API_VERSION}/sobjects/Account/describe"
        },
        {
            "method": "GET",
            "url": f"v{API_VERSION}/limits"
        }
    ]
    batch_resp = await data_source.composite_batch(subrequests=batch_requests)
    print_result("Composite Batch", batch_resp)

    # 11. Get Organization Limits
    print_section("System Limits")
    limits_resp = await data_source.get_limits()
    print_result("Get Limits", limits_resp, show_data=False)
    if limits_resp.success and limits_resp.data:
        limits = limits_resp.data
        print(f"   API Requests: {limits.get('DailyApiRequests', {}).get('Remaining', 'N/A')}/{limits.get('DailyApiRequests', {}).get('Max', 'N/A')}")
        print(f"   Data Storage: {limits.get('DataStorageMB', {}).get('Remaining', 'N/A')}/{limits.get('DataStorageMB', {}).get('Max', 'N/A')} MB")

    # 12. Recent Items
    print_section("Recently Viewed Items")
    recent_resp = await data_source.recent_items(limit=10)
    print_result("Recent Items", recent_resp)

    # 13. Get List Views for Account
    print_section("List Views for Account")
    list_views_resp = await data_source.get_list_views(sobject="Account")
    print_result("Get List Views", list_views_resp, show_data=False)
    if list_views_resp.success and list_views_resp.data:
        views = list_views_resp.data.get("listViews", [])
        print(f"   Total list views: {len(views)}")
        if views:
            print(f"   Sample views: {[v['label'] for v in views[:3]]}")

    # 14. Get Favorites
    print_section("User Favorites")
    favorites_resp = await data_source.get_favorites()
    print_result("Get Favorites", favorites_resp)

    # 15. Query with Deleted Records
    print_section("Query All (Including Deleted)")
    query_all_resp = await data_source.query_all(
        q="SELECT Id, Name, IsDeleted FROM Account WHERE IsDeleted = true LIMIT 5"
    )
    print_result("Query All Records", query_all_resp)

    # 16. Composite Tree - Create Related Records
    print_section("Composite Tree - Create Account with Contacts")
    tree_data = {
        "records": [
            {
                "attributes": {"type": "Account", "referenceId": "ref1"},
                "Name": "Parent Account",
                "Contacts": {
                    "records": [
                        {
                            "attributes": {"type": "Contact", "referenceId": "ref2"},
                            "FirstName": "John",
                            "LastName": "Doe",
                            "Email": "john.doe@example.com"
                        },
                        {
                            "attributes": {"type": "Contact", "referenceId": "ref3"},
                            "FirstName": "Jane",
                            "LastName": "Smith",
                            "Email": "jane.smith@example.com"
                        }
                    ]
                }
            }
        ]
    }
    tree_resp = await data_source.composite_tree(sobject="Account", records=tree_data["records"])
    print_result("Composite Tree", tree_resp)

    # 17. Delete Record (Cleanup)
    if created_account_id:
        print_section(f"Delete Account - {created_account_id}")
        delete_resp = await data_source.delete_record(
            record_id=created_account_id,
            sobject="Account"
        )
        print_result("Delete Account", delete_resp)

    print("\n" + "=" * 80)
    print("✅ All examples completed successfully!")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())