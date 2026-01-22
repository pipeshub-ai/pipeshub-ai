# ruff: noqa

"""
Snowflake REST API Usage Examples

This example demonstrates how to use the Snowflake DataSource to interact with
the Snowflake REST API (v2), covering:
- Authentication (OAuth2 or Personal Access Token)
- Initializing the Client and DataSource
- Listing Databases, Schemas, Tables
- Managing Warehouses
- User and Role Operations
- Task and Stream Management

Prerequisites:
For OAuth2:
1. Create a Snowflake Security Integration for OAuth
   - In Snowflake: CREATE SECURITY INTEGRATION ...
2. Set SNOWFLAKE_CLIENT_ID environment variable (OAuth Client ID)
3. Set SNOWFLAKE_CLIENT_SECRET environment variable (OAuth Client Secret)
4. Set SNOWFLAKE_ACCOUNT_IDENTIFIER environment variable (e.g., myaccount-xy12345)
5. Set SNOWFLAKE_REDIRECT_URI (default: http://localhost:8080/oauth/callback)
   - This must match the redirect URI registered in your Snowflake OAuth integration
6. The OAuth flow will automatically open a browser for authorization

For Personal Access Token (PAT):
1. Log in to Snowflake
2. Go to Admin -> Security -> Programmatic Access Tokens
3. Create a PAT with appropriate permissions
4. Set SNOWFLAKE_PAT_TOKEN environment variable
5. Set SNOWFLAKE_ACCOUNT_IDENTIFIER environment variable

Snowflake OAuth Documentation:
- OAuth Overview: https://docs.snowflake.com/en/user-guide/oauth-intro
- OAuth Custom Clients: https://docs.snowflake.com/en/user-guide/oauth-custom
- REST API Authentication: https://docs.snowflake.com/en/developer-guide/sql-api/authenticating
"""

import asyncio
import json
import os
from typing import Dict

from app.sources.client.snowflake.snowflake import (
    SnowflakeClient,
    SnowflakeOAuthConfig,
    SnowflakePATConfig,
    SnowflakeResponse,
    SnowflakeSDKClient,
)
from app.sources.external.snowflake.snowflake_ import SnowflakeDataSource
from app.sources.external.utils.oauth import perform_oauth_flow

# --- Configuration ---
# Account identifier (required for all auth methods)
ACCOUNT_IDENTIFIER = os.getenv("SNOWFLAKE_ACCOUNT_IDENTIFIER")

# OAuth credentials (highest priority)
CLIENT_ID = os.getenv("SNOWFLAKE_CLIENT_ID")
CLIENT_SECRET = os.getenv("SNOWFLAKE_CLIENT_SECRET")
REDIRECT_URI = os.getenv("SNOWFLAKE_REDIRECT_URI", "http://localhost:8080/oauth/callback")

# Personal Access Token (second priority)
PAT_TOKEN = os.getenv("SNOWFLAKE_PAT_TOKEN")

# Optional: Pre-obtained OAuth access token (third priority)
OAUTH_TOKEN = os.getenv("SNOWFLAKE_OAUTH_TOKEN")


def print_section(title: str):
    print(f"\n{'-'*80}")
    print(f"| {title}")
    print(f"{'-'*80}")


def print_result(name: str, response: SnowflakeResponse, show_data: bool = True, max_records: int = 3):
    if response.success:
        print(f"✅ {name}: Success")
        if show_data and response.data:
            data_to_show = response.data
            # Handle array responses
            if isinstance(data_to_show, list):
                print(f"   Found {len(data_to_show)} item(s).")
                if len(data_to_show) > 0:
                    for i, item in enumerate(data_to_show[:max_records], 1):
                        print(f"   Item {i}: {json.dumps(item, indent=2)[:300]}...")
            # Handle dict with data array (common Snowflake pattern)
            elif isinstance(data_to_show, dict):
                if "data" in data_to_show:
                    items = data_to_show["data"]
                    if isinstance(items, list):
                        print(f"   Found {len(items)} item(s).")
                        if len(items) > 0:
                            for i, item in enumerate(items[:max_records], 1):
                                print(f"   Item {i}: {json.dumps(item, indent=2)[:300]}...")
                    else:
                        print(f"   Data: {json.dumps(data_to_show, indent=2)[:500]}...")
                elif "rowset" in data_to_show:
                    # Handle SQL API response format
                    rowset = data_to_show["rowset"]
                    print(f"   Found {len(rowset)} row(s).")
                    if rowset:
                        for i, row in enumerate(rowset[:max_records], 1):
                            print(f"   Row {i}: {json.dumps(row, indent=2)[:300]}...")
                else:
                    print(f"   Data: {json.dumps(data_to_show, indent=2)[:500]}...")
    else:
        print(f"❌ {name}: Failed")
        print(f"   Error: {response.error}")
        if response.message:
            print(f"   Message: {response.message}")


def get_snowflake_oauth_endpoints(account_identifier: str) -> Dict[str, str]:
    """Build Snowflake OAuth endpoints from account identifier.

    Args:
        account_identifier: Snowflake account identifier (e.g., myaccount-xy12345)

    Returns:
        Dictionary with auth_endpoint and token_endpoint URLs
    """
    # Clean account identifier
    account = account_identifier.replace("https://", "").replace(".snowflakecomputing.com", "")
    base_url = f"https://{account}.snowflakecomputing.com"

    return {
        "auth_endpoint": f"{base_url}/oauth/authorize",
        "token_endpoint": f"{base_url}/oauth/token-request",
    }


async def main() -> None:
    # 1. Initialize Client
    print_section("Initializing Snowflake Client")

    if not ACCOUNT_IDENTIFIER:
        print("⚠️  SNOWFLAKE_ACCOUNT_IDENTIFIER environment variable is required.")
        print("   Example: myaccount-xy12345 or myaccount.us-east-1")
        return

    config = None

    if CLIENT_ID and CLIENT_SECRET:
        # OAuth2 authentication (highest priority)
        print("ℹ️  Using OAuth2 authentication")
        try:
            print("Starting OAuth flow...")

            # Get Snowflake OAuth endpoints
            endpoints = get_snowflake_oauth_endpoints(ACCOUNT_IDENTIFIER)

            token_response = perform_oauth_flow(
                client_id=CLIENT_ID,
                client_secret=CLIENT_SECRET,
                auth_endpoint=endpoints["auth_endpoint"],
                token_endpoint=endpoints["token_endpoint"],
                redirect_uri=REDIRECT_URI,
                scopes=["session:role:PUBLIC"],  # Adjust scopes as needed
                scope_delimiter=" ",
                auth_method="body",  # Snowflake uses POST body for token exchange
            )

            access_token = token_response.get("access_token")

            if not access_token:
                raise Exception("No access_token found in OAuth response")

            config = SnowflakeOAuthConfig(
                account_identifier=ACCOUNT_IDENTIFIER,
                oauth_token=access_token
            )
            print("✅ OAuth authentication successful")

        except Exception as e:
            print(f"❌ OAuth flow failed: {e}")
            print("⚠️  Falling back to other authentication methods...")

    if config is None and OAUTH_TOKEN:
        # Pre-obtained OAuth token (second priority)
        print("ℹ️  Using pre-obtained OAuth token")
        config = SnowflakeOAuthConfig(
            account_identifier=ACCOUNT_IDENTIFIER,
            oauth_token=OAUTH_TOKEN
        )
    elif config is None and PAT_TOKEN:
        # Personal Access Token authentication (third priority)
        print("ℹ️  Using Personal Access Token (PAT) authentication")
        config = SnowflakePATConfig(
            account_identifier=ACCOUNT_IDENTIFIER,
            pat_token=PAT_TOKEN
        )

    if config is None:
        print("⚠️  No valid authentication method found.")
        print("   Please set one of the following:")
        print("   - SNOWFLAKE_CLIENT_ID and SNOWFLAKE_CLIENT_SECRET (for OAuth2)")
        print("   - SNOWFLAKE_OAUTH_TOKEN (for pre-obtained OAuth token)")
        print("   - SNOWFLAKE_PAT_TOKEN (for Personal Access Token)")
        return

    client = SnowflakeClient.build_with_config(config)
    data_source = SnowflakeDataSource(client)
    print(f"Client initialized successfully.")
    print(f"Base URL: {data_source.base_url}")

    # 2. List Databases
    print_section("Databases")
    databases_resp = await data_source.list_databases()
    print_result("List Databases", databases_resp)

    # Get first database name for subsequent operations
    database_name = None
    if databases_resp.success and databases_resp.data:
        data = databases_resp.data
        if isinstance(data, dict) and "data" in data:
            databases = data["data"]
        elif isinstance(data, dict) and "rowset" in data:
            databases = data["rowset"]
        elif isinstance(data, list):
            databases = data
        else:
            databases = []

        if databases and len(databases) > 0:
            # Snowflake returns database info as arrays or objects
            first_db = databases[0]
            if isinstance(first_db, dict):
                database_name = first_db.get("name") or first_db.get("database_name")
            elif isinstance(first_db, list):
                database_name = first_db[0] if first_db else None

    # 3. Get Database Details
    schema_name = None
    if database_name:
        print_section(f"Database Details: {database_name}")
        db_detail = await data_source.get_database(name=database_name) # not working due to lesser scopes, will fix while integration.
        print_result(f"Get Database '{database_name}'", db_detail)

        # 4. List Schemas in Database
        print_section(f"Schemas in {database_name}")
        schemas_resp = await data_source.list_schemas(database=database_name)
        print_result("List Schemas", schemas_resp)

        # Get first schema name
        if schemas_resp.success and schemas_resp.data:
            data = schemas_resp.data
            if isinstance(data, dict) and "data" in data:
                schemas = data["data"]
            elif isinstance(data, dict) and "rowset" in data:
                schemas = data["rowset"]
            elif isinstance(data, list):
                schemas = data
            else:
                schemas = []

            if schemas and len(schemas) > 0:
                first_schema = schemas[0]
                if isinstance(first_schema, dict):
                    schema_name = first_schema.get("name") or first_schema.get("schema_name")
                elif isinstance(first_schema, list):
                    schema_name = first_schema[0] if first_schema else None

        # 5. List Tables in Schema
        if schema_name:
            print_section(f"Tables in {database_name}.{schema_name}")
            tables_resp = await data_source.list_tables(
                database=database_name,
                schema=schema_name
            )
            print_result("List Tables", tables_resp)

            # 6. List Views in Schema
            print_section(f"Views in {database_name}.{schema_name}")
            views_resp = await data_source.list_views(
                database=database_name,
                schema=schema_name
            )
            print_result("List Views", views_resp)

    # 7. List Warehouses
    print_section("Warehouses")
    warehouses_resp = await data_source.list_warehouses()
    print_result("List Warehouses", warehouses_resp)

    # Get first warehouse for details
    warehouse_name = None
    if warehouses_resp.success and warehouses_resp.data:
        data = warehouses_resp.data
        if isinstance(data, dict) and "data" in data:
            warehouses = data["data"]
        elif isinstance(data, dict) and "rowset" in data:
            warehouses = data["rowset"]
        elif isinstance(data, list):
            warehouses = data
        else:
            warehouses = []

        if warehouses and len(warehouses) > 0:
            first_wh = warehouses[0]
            if isinstance(first_wh, dict):
                warehouse_name = first_wh.get("name") or first_wh.get("warehouse_name")
            elif isinstance(first_wh, list):
                warehouse_name = first_wh[0] if first_wh else None

    if warehouse_name:
        print_section(f"Warehouse Details: {warehouse_name}")
        wh_detail = await data_source.get_warehouse(name=warehouse_name)
        print_result(f"Get Warehouse '{warehouse_name}'", wh_detail)

    # 8. List Users
    print_section("Users")
    users_resp = await data_source.list_users()
    print_result("List Users", users_resp)

    # 9. List Roles
    print_section("Roles")
    roles_resp = await data_source.list_roles()
    print_result("List Roles", roles_resp)

    # 10. List Tasks (if database and schema available)
    if database_name and schema_name:
        print_section(f"Tasks in {database_name}.{schema_name}")
        tasks_resp = await data_source.list_tasks(
            database=database_name,
            schema=schema_name
        )
        print_result("List Tasks", tasks_resp)

        # 11. List Streams
        print_section(f"Streams in {database_name}.{schema_name}")
        streams_resp = await data_source.list_streams(
            database=database_name,
            schema=schema_name
        )
        print_result("List Streams", streams_resp)

        # 12. List Stages
        print_section(f"Stages in {database_name}.{schema_name}")
        stages_resp = await data_source.list_stages(
            database=database_name,
            schema=schema_name
        )
        print_result("List Stages", stages_resp)

        # 13. List Pipes
        print_section(f"Pipes in {database_name}.{schema_name}")
        pipes_resp = await data_source.list_pipes(
            database=database_name,
            schema=schema_name
        )
        print_result("List Pipes", pipes_resp)

        # 14. List Alerts
        print_section(f"Alerts in {database_name}.{schema_name}")
        alerts_resp = await data_source.list_alerts(
            database=database_name,
            schema=schema_name
        )
        print_result("List Alerts", alerts_resp)

    # 15. List Network Policies
    print_section("Network Policies")
    policies_resp = await data_source.list_network_policies()
    print_result("List Network Policies", policies_resp)

    # 16. List Compute Pools
    print_section("Compute Pools")
    pools_resp = await data_source.list_compute_pools()
    print_result("List Compute Pools", pools_resp)

    # 17. List Notebooks (if database and schema available)
    if database_name and schema_name:
        print_section(f"Notebooks in {database_name}.{schema_name}")
        notebooks_resp = await data_source.list_notebooks(
            database=database_name,
            schema=schema_name
        )
        print_result("List Notebooks", notebooks_resp)

    # 18. SQL SDK Examples (Direct SQL Query Execution)
    print_section("SQL SDK Examples")

    # Get the OAuth token from the config for SDK client
    oauth_token = None
    if isinstance(config, SnowflakeOAuthConfig):
        oauth_token = config.oauth_token

    if oauth_token:
        print("ℹ️  Testing SQL SDK with OAuth token...")
        try:
            # Initialize SDK client with OAuth
            sdk_client = SnowflakeSDKClient(
                account_identifier=ACCOUNT_IDENTIFIER,
                oauth_token=oauth_token,
                warehouse=warehouse_name,  # Use the warehouse we found earlier
                role="PUBLIC",  # Match the OAuth scope role
            )

            # Use context manager for automatic connection handling
            with sdk_client:
                print("✅ SDK Client connected successfully")

                # Example 1: Show current user
                print("\n   Query 1: SELECT CURRENT_USER()")
                result = sdk_client.execute_query("SELECT CURRENT_USER() as current_user")
                print(f"   Result: {json.dumps(result, indent=2)}")

                # Example 2: Show current role
                print("\n   Query 2: SELECT CURRENT_ROLE()")
                result = sdk_client.execute_query("SELECT CURRENT_ROLE() as current_role")
                print(f"   Result: {json.dumps(result, indent=2)}")

                # Example 3: Show current warehouse
                print("\n   Query 3: SELECT CURRENT_WAREHOUSE()")
                result = sdk_client.execute_query("SELECT CURRENT_WAREHOUSE() as current_warehouse")
                print(f"   Result: {json.dumps(result, indent=2)}")

                # Example 4: List databases via SQL
                print("\n   Query 4: SHOW DATABASES")
                result = sdk_client.execute_query("SHOW DATABASES")
                print(f"   Found {len(result)} database(s)")
                for i, db in enumerate(result[:3], 1):
                    db_name = db.get("name", "Unknown")
                    print(f"   Database {i}: {db_name}")

                # Example 5: Sample data query (if SNOWFLAKE_SAMPLE_DATA exists)
                print("\n   Query 5: Sample data from TPCH")
                try:
                    result = sdk_client.execute_query(
                        "SELECT * FROM SNOWFLAKE_SAMPLE_DATA.TPCH_SF1.NATION LIMIT 5"
                    )
                    print(f"   Found {len(result)} row(s)")
                    for i, row in enumerate(result[:3], 1):
                        print(f"   Row {i}: {json.dumps(row, default=str)[:200]}...")
                except Exception as e:
                    print(f"   ⚠️  Sample data query skipped: {e}")

            print("\n✅ SQL SDK examples completed successfully")

        except ImportError as e:
            print(f"⚠️  SQL SDK not available: {e}")
            print("   Install with: pip install snowflake-connector-python")
        except Exception as e:
            print(f"❌ SQL SDK error: {e}")
    else:
        print("⚠️  SQL SDK examples skipped (requires OAuth token)")

    print("\n" + "=" * 80)
    print("✅ All examples completed successfully!")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
