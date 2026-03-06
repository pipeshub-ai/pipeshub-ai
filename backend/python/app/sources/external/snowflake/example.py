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

# --- TEST CONFIGURATION ---
# Override these to test specific database/schema/stage instead of auto-discovery
# Set to None to use auto-discovery (first database/schema found)
TEST_DATABASE = os.getenv("SNOWFLAKE_TEST_DATABASE", "TEST_DB")  # e.g., "TEST_DB"
TEST_SCHEMA = os.getenv("SNOWFLAKE_TEST_SCHEMA", "TEST_SCHEMA1")  # e.g., "TEST_SCHEMA1"  
TEST_STAGE = os.getenv("SNOWFLAKE_TEST_STAGE", "TEST_STAGE1")  # e.g., "TEST_STAGE1"

# Role and Warehouse configuration
# The role determines what permissions you have - use a role with access to your test objects
TEST_ROLE = os.getenv("SNOWFLAKE_TEST_ROLE", "SNOWFLAKE_LEARNING_ROLE")  # e.g., "ACCOUNTADMIN", "SYSADMIN", "PUBLIC"
TEST_WAREHOUSE = os.getenv("SNOWFLAKE_TEST_WAREHOUSE", None)  # e.g., "COMPUTE_WH" - None = auto-discover

# Set to None to use auto-discovery:
# TEST_DATABASE = None
# TEST_SCHEMA = None
# TEST_STAGE = None
# TEST_ROLE = None  # Will use default role from OAuth scope



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

            # Build OAuth scope based on TEST_ROLE config
            oauth_role = TEST_ROLE if TEST_ROLE else "PUBLIC"
            oauth_scopes = [f"session:role:{oauth_role}"]
            print(f"ℹ️  Using role for OAuth: {oauth_role}")

            token_response = perform_oauth_flow(
                client_id=CLIENT_ID,
                client_secret=CLIENT_SECRET,
                auth_endpoint=endpoints["auth_endpoint"],
                token_endpoint=endpoints["token_endpoint"],
                redirect_uri=REDIRECT_URI,
                scopes=oauth_scopes,
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

    # Use configured database or auto-discover first one
    database_name = TEST_DATABASE  # Use config value first
    if database_name:
        print(f"ℹ️  Using configured TEST_DATABASE: {database_name}")
    elif databases_resp.success and databases_resp.data:
        # Auto-discover from first database
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
            print(f"ℹ️  Auto-discovered database: {database_name}")

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

        # Use configured schema or auto-discover first one
        schema_name = TEST_SCHEMA  # Use config value first
        if schema_name:
            print(f"ℹ️  Using configured TEST_SCHEMA: {schema_name}")
        elif schemas_resp.success and schemas_resp.data:
            # Auto-discover from first schema
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
                print(f"ℹ️  Auto-discovered schema: {schema_name}")

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
            sdk_role = TEST_ROLE if TEST_ROLE else "PUBLIC"
            sdk_warehouse = TEST_WAREHOUSE if TEST_WAREHOUSE else warehouse_name
            sdk_client = SnowflakeSDKClient(
                account_identifier=ACCOUNT_IDENTIFIER,
                oauth_token=oauth_token,
                warehouse=sdk_warehouse,
                role=sdk_role,
            )
            print(f"   Using role: {sdk_role}, warehouse: {sdk_warehouse}")

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
        print(f"⚠️  SQL SDK examples skipped (requires OAuth token)")

    # ========================================================================
    # NEW API TESTS - SQL Statements, Grants, Database Roles, Files, etc.
    # ========================================================================

    # 19. Execute SQL API - Run custom SQL queries
    print_section("SQL Statements API - execute_sql()")
    
    # Test 1: Simple query
    sql_resp = await data_source.execute_sql(
        statement="SELECT CURRENT_USER() AS current_user, CURRENT_ROLE() AS current_role",
        warehouse=warehouse_name
    )
    print_result("Execute SQL - Current User/Role", sql_resp)

    # Test 2: Show grants (useful for permission sync)
    if database_name:
        print("\n   Testing SHOW GRANTS on database...")
        grants_sql_resp = await data_source.execute_sql(
            statement=f"SHOW GRANTS ON DATABASE {database_name}",
            warehouse=warehouse_name
        )
        print_result(f"Execute SQL - SHOW GRANTS ON DATABASE {database_name}", grants_sql_resp)

    # Test 3: Describe table (useful for table metadata indexing)
    if database_name and schema_name:
        # First get a table name
        tables_resp = await data_source.list_tables(database=database_name, schema=schema_name)
        table_name = None
        if tables_resp.success and tables_resp.data:
            data = tables_resp.data
            tables = data.get("data", []) if isinstance(data, dict) else data
            if tables and len(tables) > 0:
                first_table = tables[0]
                table_name = first_table.get("name") if isinstance(first_table, dict) else None
        
        if table_name:
            print(f"\n   Testing DESCRIBE TABLE {database_name}.{schema_name}.{table_name}...")
            describe_resp = await data_source.execute_sql(
                statement=f"DESCRIBE TABLE {database_name}.{schema_name}.{table_name}",
                warehouse=warehouse_name
            )
            print_result(f"Execute SQL - DESCRIBE TABLE", describe_resp)

    # 20. Grants API - List grants to role
    print_section("Grants API - list_grants_to_role()")
    
    # Get first role name for testing
    role_name = None
    if roles_resp.success and roles_resp.data:
        data = roles_resp.data
        roles = data.get("data", []) if isinstance(data, dict) else data
        if roles and len(roles) > 0:
            first_role = roles[0]
            role_name = first_role.get("name") if isinstance(first_role, dict) else None

    if role_name:
        grants_to_role_resp = await data_source.list_grants_to_role(role_name=role_name)
        print_result(f"List Grants to Role '{role_name}'", grants_to_role_resp)
    else:
        print("   ⚠️  Skipped - No roles found to test")

    # 21. Grants API - List grants to user
    print_section("Grants API - list_grants_to_user()")
    
    # Get first user name for testing
    user_name = None
    if users_resp.success and users_resp.data:
        data = users_resp.data
        users = data.get("data", []) if isinstance(data, dict) else data
        if users and len(users) > 0:
            first_user = users[0]
            user_name = first_user.get("name") if isinstance(first_user, dict) else None

    if user_name:
        grants_to_user_resp = await data_source.list_grants_to_user(user_name=user_name)
        print_result(f"List Grants to User '{user_name}'", grants_to_user_resp)
    else:
        print("   ⚠️  Skipped - No users found to test")

    # 22. Grants API - List grants of role (who has this role)
    print_section("Grants API - list_grants_of_role()")
    
    if role_name:
        grants_of_role_resp = await data_source.list_grants_of_role(role_name=role_name)
        print_result(f"List Grants of Role '{role_name}' (members)", grants_of_role_resp)
    else:
        print("   ⚠️  Skipped - No roles found to test")

    # 23. Grants API - List grants on object
    print_section("Grants API - list_grants_on_object()")
    
    if database_name:
        grants_on_db_resp = await data_source.list_grants_on_object(
            object_type="DATABASE",
            object_name=database_name
        )
        print_result(f"List Grants on DATABASE {database_name}", grants_on_db_resp)
    
    if database_name and schema_name:
        grants_on_schema_resp = await data_source.list_grants_on_object(
            object_type="SCHEMA",
            object_name=f"{database_name}.{schema_name}"
        )
        print_result(f"List Grants on SCHEMA {database_name}.{schema_name}", grants_on_schema_resp)

    # 24. Database Roles API
    print_section("Database Roles API - list_database_roles()")
    
    if database_name:
        db_roles_resp = await data_source.list_database_roles(database=database_name)
        print_result(f"List Database Roles in '{database_name}'", db_roles_resp)
        
        # Try to get details of first database role
        if db_roles_resp.success and db_roles_resp.data:
            data = db_roles_resp.data
            db_roles = data.get("data", []) if isinstance(data, dict) else data
            if db_roles and len(db_roles) > 0:
                first_db_role = db_roles[0]
                db_role_name = first_db_role.get("name") if isinstance(first_db_role, dict) else None
                if db_role_name:
                    db_role_detail = await data_source.get_database_role(
                        database=database_name,
                        name=db_role_name
                    )
                    print_result(f"Get Database Role '{db_role_name}'", db_role_detail)
    else:
        print("   ⚠️  Skipped - No database found to test")

    # 25. Dynamic Tables API
    print_section("Dynamic Tables API - list_dynamic_tables()")
    
    if database_name and schema_name:
        dynamic_tables_resp = await data_source.list_dynamic_tables(
            database=database_name,
            schema=schema_name
        )
        print_result(f"List Dynamic Tables in {database_name}.{schema_name}", dynamic_tables_resp)
        
        # Try to get details of first dynamic table
        if dynamic_tables_resp.success and dynamic_tables_resp.data:
            data = dynamic_tables_resp.data
            dyn_tables = data.get("data", []) if isinstance(data, dict) else data
            if dyn_tables and len(dyn_tables) > 0:
                first_dyn_table = dyn_tables[0]
                dyn_table_name = first_dyn_table.get("name") if isinstance(first_dyn_table, dict) else None
                if dyn_table_name:
                    dyn_table_detail = await data_source.get_dynamic_table(
                        database=database_name,
                        schema=schema_name,
                        name=dyn_table_name
                    )
                    print_result(f"Get Dynamic Table '{dyn_table_name}'", dyn_table_detail)
    else:
        print("   ⚠️  Skipped - No database/schema found to test")

    # 26. Stage Files API - List files in a stage (Directory Table)
    print_section("Stage Files API - list_stage_files()")
    
    # Use configured stage or auto-discover first one
    stage_name = TEST_STAGE  # Use config value first
    if database_name and schema_name:
        # Re-fetch stages to ensure we have the latest data
        print(f"   Fetching stages from {database_name}.{schema_name}...")
        stages_for_files_resp = await data_source.list_stages(
            database=database_name,
            schema=schema_name
        )
        print_result(f"List Stages in {database_name}.{schema_name}", stages_for_files_resp)
        
        if stage_name:
            print(f"ℹ️  Using configured TEST_STAGE: {stage_name}")
        elif stages_for_files_resp.success and stages_for_files_resp.data:
            # Auto-discover from first stage
            data = stages_for_files_resp.data
            stages = data.get("data", []) if isinstance(data, dict) else data
            if stages and len(stages) > 0:
                first_stage = stages[0]
                stage_name = first_stage.get("name") if isinstance(first_stage, dict) else None
                print(f"ℹ️  Auto-discovered stage: {stage_name}")
        
        if stage_name:
            print(f"   Testing list_stage_files for stage: {stage_name}")
            print(f"   Using warehouse: {warehouse_name}")
            stage_files_resp = await data_source.list_stage_files(
                database=database_name,
                schema=schema_name,
                stage=stage_name,
                warehouse=warehouse_name  # Required for SQL execution
            )
            print_result(f"List Files in Stage '{stage_name}'", stage_files_resp)
            
            # 27. Generate Pre-signed URL for a file
            print_section("Files API - generate_presigned_url()")
            
            if stage_files_resp.success and stage_files_resp.data:
                # Check if there are files in the stage
                # SQL API returns results in "data" key, not "rowset"
                resp_data = stage_files_resp.data
                file_rows = resp_data.get("data", []) if isinstance(resp_data, dict) else []
                
                print(f"   Found {len(file_rows)} file(s) in stage")
                
                if file_rows and len(file_rows) > 0:
                    # Get first file - SQL API returns array of arrays
                    # Columns: RELATIVE_PATH, SIZE, LAST_MODIFIED, MD5, ETAG, FILE_URL
                    first_file = file_rows[0]
                    print(f"   First file data: {first_file}")
                    
                    # RELATIVE_PATH is first column (index 0)
                    file_path = first_file[0] if isinstance(first_file, list) else first_file.get("RELATIVE_PATH")
                    
                    if file_path:
                        print(f"   Generating pre-signed URL for: {file_path}")
                        presigned_resp = await data_source.generate_presigned_url(
                            database=database_name,
                            schema=schema_name,
                            stage=stage_name,
                            file_path=file_path,
                            expiration_seconds=3600,
                            warehouse=warehouse_name  # Required for SQL execution
                        )
                        print_result(f"Generate Pre-signed URL for '{file_path}'", presigned_resp)
                        
                        # Extract presigned URL from response
                        presigned_url = None
                        if presigned_resp.success and presigned_resp.data:
                            presigned_data = presigned_resp.data.get("data", [])
                            if presigned_data and len(presigned_data) > 0:
                                presigned_url = presigned_data[0][0] if isinstance(presigned_data[0], list) else presigned_data[0]
                        
                        # 28. Download File API test - Try presigned URL first (most reliable)
                        print_section("Files API - download_file_from_presigned_url()")
                        
                        if presigned_url:
                            print(f"   Pre-signed URL: {presigned_url[:100]}...")
                            print(f"   Attempting to download via presigned URL...")
                            download_resp = await data_source.download_file_from_presigned_url(presigned_url=presigned_url)
                            if download_resp.success:
                                file_content = download_resp.raw_content
                                file_size = len(file_content) if file_content else 0
                                print(f"   ✅ Downloaded file successfully!")
                                print(f"   📦 File size: {file_size} bytes")
                                
                                # Show content preview
                                if file_content and file_size > 0:
                                    # Check if it's a PDF (starts with %PDF)
                                    if file_content[:4] == b'%PDF':
                                        print(f"   📄 File type: PDF document")
                                        print(f"   📝 PDF Header: {file_content[:50].decode('latin-1', errors='replace')}")
                                    else:
                                        # Try to decode as text, fallback to hex
                                        try:
                                            text_preview = file_content[:200].decode('utf-8')
                                            print(f"   📝 Content (text): {text_preview[:100]}...")
                                        except UnicodeDecodeError:
                                            # Binary file - show hex
                                            hex_preview = file_content[:50].hex()
                                            print(f"   🔢 Content (hex): {hex_preview}...")
                                    
                                    # Show file magic bytes
                                    print(f"   🔍 First 20 bytes (hex): {file_content[:20].hex()}")
                            else:
                                print(f"   ⚠️  Presigned download failed: {download_resp.message}")
                                print(f"   Error: {download_resp.error}")
                        else:
                            print("   ⚠️  Could not extract presigned URL from response")
                        
                        # 28b. Also try the Files API download for comparison
                        print_section("Files API - download_file() [via Snowflake API]")
                        
                        # FILE_URL is the last column (index -1)
                        file_url = first_file[-1] if isinstance(first_file, list) else first_file.get("FILE_URL")
                        
                        if file_url:
                            print(f"   File URL: {file_url}")
                            print(f"   Attempting to download via Snowflake Files API...")
                            download_resp = await data_source.download_file(file_url=file_url)
                            if download_resp.success:
                                file_content = download_resp.raw_content
                                file_size = len(file_content) if file_content else 0
                                print(f"   ✅ Downloaded file successfully: {file_size} bytes")
                            else:
                                print(f"   ⚠️  Download failed: {download_resp.message}")
                                print(f"   Error: {download_resp.error}")
                        else:
                            print("   ⚠️  No FILE_URL available in directory table result")
                    else:
                        print("   ⚠️  No file path found in stage directory")
                else:
                    print("   ⚠️  Stage is empty - no files to test")
            else:
                print("   ⚠️  Could not list stage files")
        else:
            print("   ⚠️  No stages found to test")
    else:
        print("   ⚠️  Skipped - No database/schema found to test")

    # 29. Test async SQL execution
    print_section("SQL Statements API - Async Execution")
    
    async_resp = await data_source.execute_sql(
        statement="SELECT * FROM INFORMATION_SCHEMA.TABLES LIMIT 10",
        database=database_name if database_name else "SNOWFLAKE",
        warehouse=warehouse_name,
        async_exec=True  # Execute asynchronously
    )
    print_result("Execute SQL (async mode)", async_resp)
    
    if async_resp.success and async_resp.data:
        statement_handle = async_resp.data.get("statementHandle")
        if statement_handle:
            print(f"   Statement Handle: {statement_handle}")
            
            # Check status
            import asyncio as aio
            await aio.sleep(1)  # Wait a bit for the query to complete
            
            status_resp = await data_source.get_statement_status(statement_handle=statement_handle)
            print_result("Get Statement Status", status_resp)

    print("\n" + "=" * 80)
    print("✅ All examples completed successfully!")
    print("=" * 80)
    print("\n📝 NEW APIs Tested:")
    print("   - execute_sql() - Run custom SQL queries")
    print("   - get_statement_status() - Check async query status")
    print("   - list_grants_to_role() - Get role privileges")
    print("   - list_grants_to_user() - Get user privileges")
    print("   - list_grants_of_role() - Get role members")
    print("   - list_grants_on_object() - Get object permissions")
    print("   - list_database_roles() - List database-scoped roles")
    print("   - get_database_role() - Get database role details")
    print("   - list_dynamic_tables() - List dynamic tables")
    print("   - get_dynamic_table() - Get dynamic table details")
    print("   - list_stage_files() - List files in stage (Directory Table)")
    print("   - generate_presigned_url() - Create shareable file URLs")
    print("   - download_file() - Download file content for indexing")


if __name__ == "__main__":
    asyncio.run(main())

