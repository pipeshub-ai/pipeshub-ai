# ruff: noqa

"""
Databricks SDK DataSource Usage Examples

This example demonstrates how to use the DatabricksDataSource to interact with
a Databricks workspace via the official databricks-sdk, covering:
- Authentication (OAuth2 U2M via PKCE, or Personal Access Token)
- Initializing the Client and DataSource
- Current user identity
- Workspace operations (listing objects)
- Cluster management
- SQL Warehouses & SQL statement execution
- User and group management, permission levels
- Job listing
- Unity Catalog (catalogs, schemas, tables)
- Secret scopes
- Model serving endpoints
- Repos and DBFS browsing

Prerequisites:
For OAuth2 (U2M — browser-based login, no client secret needed):
1. Set DATABRICKS_WORKSPACE_URL environment variable
   (e.g., https://dbc-7a41.cloud.databricks.com)
2. The OAuth PKCE flow will automatically open a browser for authorization
   using the built-in "databricks-cli" public client.

For Personal Access Token (PAT):
1. Log in to Databricks workspace
2. Go to User Settings -> Developer -> Access Tokens
3. Generate a new token
4. Set DATABRICKS_PAT_TOKEN environment variable
5. Set DATABRICKS_WORKSPACE_URL environment variable

For pre-obtained OAuth token:
1. Set DATABRICKS_OAUTH_TOKEN environment variable
2. Set DATABRICKS_WORKSPACE_URL environment variable

Databricks Authentication Documentation:
- PAT: https://docs.databricks.com/en/dev-tools/auth/pat.html
- OAuth U2M: https://docs.databricks.com/en/dev-tools/auth/oauth-u2m.html
- SDK Auth: https://docs.databricks.com/en/dev-tools/auth/index.html

Run from backend/python with the project venv (required for databricks-sdk):
  cd backend/python
  source venv/bin/activate   # or use venv Python explicitly:
  python app/sources/external/databricks/example.py
  # Or: ./venv/bin/python app/sources/external/databricks/example.py
"""

import asyncio
import json
import os
from typing import Dict

from app.sources.client.databricks.databricks import (
    DatabricksClient,
    DatabricksOAuthConfig,
    DatabricksPATConfig,
    DatabricksResponse,
)
from app.sources.external.databricks.databricks import DatabricksDataSource
from app.sources.external.utils.oauth import perform_oauth_flow

# --- Configuration from environment variables ---
WORKSPACE_URL = os.getenv("DATABRICKS_WORKSPACE_URL")

# OAuth2 U2M (uses PKCE with the built-in public "databricks-cli" client)
OAUTH_CLIENT_ID = os.getenv("DATABRICKS_OAUTH_CLIENT_ID", "databricks-cli")
OAUTH_SCOPES = os.getenv("DATABRICKS_OAUTH_SCOPES", "all-apis offline_access")
REDIRECT_PORT = int(os.getenv("DATABRICKS_REDIRECT_PORT", "8020"))
REDIRECT_URI = os.getenv("DATABRICKS_REDIRECT_URI", f"http://localhost:{REDIRECT_PORT}")

# Personal Access Token (alternative)
PAT_TOKEN = os.getenv("DATABRICKS_PAT_TOKEN")

# Pre-obtained OAuth token (alternative)
OAUTH_TOKEN = os.getenv("DATABRICKS_OAUTH_TOKEN")


def print_section(title: str):
    print(f"\n{'-'*80}")
    print(f"| {title}")
    print(f"{'-'*80}")


def print_result(
    name: str,
    response: DatabricksResponse,
    show_data: bool = True,
    max_records: int = 3,
):
    if response.success:
        print(f"  {name}: Success")
        if show_data and response.data:
            data = response.data
            if isinstance(data, list):
                print(f"   Found {len(data)} item(s).")
                for i, item in enumerate(data[:max_records], 1):
                    print(f"   Item {i}: {json.dumps(item, indent=2, default=str)[:300]}...")
            elif isinstance(data, dict):
                print(f"   Data: {json.dumps(data, indent=2, default=str)[:500]}...")
    else:
        print(f"  {name}: Failed")
        print(f"   Error: {response.error}")
        if response.message:
            print(f"   Message: {response.message[:200]}")


def get_databricks_oauth_endpoints(workspace_url: str) -> Dict[str, str]:
    """Build Databricks OIDC endpoints from workspace URL.

    Databricks uses OpenID Connect endpoints for OAuth2:
    - Authorization: {workspace}/oidc/v1/authorize
    - Token: {workspace}/oidc/v1/token

    Args:
        workspace_url: Databricks workspace URL

    Returns:
        Dictionary with auth_endpoint and token_endpoint URLs
    """
    base = workspace_url.rstrip("/")
    return {
        "auth_endpoint": f"{base}/oidc/v1/authorize",
        "token_endpoint": f"{base}/oidc/v1/token",
    }


async def main() -> None:
    # =========================================================================
    # 1. Initialize Client
    # =========================================================================
    print_section("Initializing Databricks Client")

    if not WORKSPACE_URL:
        print("  DATABRICKS_WORKSPACE_URL environment variable is required.")
        print("   Example: https://dbc-7a41.cloud.databricks.com")
        return

    config = None

    # Priority 1: OAuth2 U2M via PKCE (browser-based, no client secret)
    if not PAT_TOKEN and not OAUTH_TOKEN:
        print("  Using OAuth2 U2M (PKCE) authentication")
        try:
            print("Starting OAuth flow...")

            endpoints = get_databricks_oauth_endpoints(WORKSPACE_URL)
            scopes = OAUTH_SCOPES.split()

            token_response = perform_oauth_flow(
                client_id=OAUTH_CLIENT_ID,
                auth_endpoint=endpoints["auth_endpoint"],
                token_endpoint=endpoints["token_endpoint"],
                redirect_uri=REDIRECT_URI,
                scopes=scopes,
                scope_delimiter=" ",
                auth_method="pkce",
                port=REDIRECT_PORT,
                timeout=300,  # 5 minutes for user to authenticate
            )
            access_token = token_response.get("access_token")
            if not access_token:
                raise Exception("No access_token found in OAuth response")

            config = DatabricksOAuthConfig(
                workspace_url=WORKSPACE_URL,
                oauth_token=access_token,
            )
            print("  OAuth PKCE authentication successful")

            refresh_tok = token_response.get("refresh_token")
            if refresh_tok:
                print("   Refresh token received (save for token renewal)")
            print(f"token expired")

        except Exception as e:
            print(f"  OAuth flow failed: {e}")
            print("  Falling back to other authentication methods...")

    # Priority 2: Pre-obtained OAuth token
    if config is None and OAUTH_TOKEN:
        print("  Using pre-obtained OAuth token")
        config = DatabricksOAuthConfig(
            workspace_url=WORKSPACE_URL,
            oauth_token=OAUTH_TOKEN,
        )

    # Priority 3: Personal Access Token
    if config is None and PAT_TOKEN:
        print("  Using Personal Access Token (PAT) authentication")
        config = DatabricksPATConfig(
            workspace_url=WORKSPACE_URL,
            pat_token=PAT_TOKEN,
        )

    if config is None:
        print("  No valid authentication method found.")
        print("   Set one of the following:")
        print("   - (nothing extra) for OAuth2 U2M with PKCE (browser-based)")
        print("   - DATABRICKS_OAUTH_TOKEN for pre-obtained OAuth token")
        print("   - DATABRICKS_PAT_TOKEN for Personal Access Token")
        return

    # Build client and data source
    client = DatabricksClient.build_with_config(config)
    sdk_client = client.get_client()  # DatabricksSDKClient — holds the socket
    ds = DatabricksDataSource(client)
    print(f"Client initialized for workspace: {WORKSPACE_URL}")

    try:
        await _run_examples(ds)
    finally:
        # Close the SDK client to release the connection / socket
        sdk_client.close()
        print("\nDatabricks SDK client closed.")


async def _run_examples(ds: DatabricksDataSource) -> None:
    """Run all example API calls against the workspace."""
    # =========================================================================
    # 2. Current User
    # =========================================================================
    print_section("Current User (Who am I?)")
    me_resp = await ds.current_user_me()
    print_result("Current User", me_resp)

    # =========================================================================
    # 3. Workspace Operations
    # =========================================================================
    print_section("Workspace — List Root (/)")
    ws_resp = await ds.workspace_list(path="/")
    print_result("Workspace Root", ws_resp)

    # List user's home directory
    if me_resp.success and me_resp.data:
        username = None
        if isinstance(me_resp.data, dict):
            username = me_resp.data.get("user_name") or me_resp.data.get("userName")
        if username:
            home_path = f"/Users/{username}"
            print_section(f"Workspace — User Home ({home_path})")
            home_resp = await ds.workspace_list(path=home_path)
            print_result(f"User Home", home_resp)

    # =========================================================================
    # 4. Clusters
    # =========================================================================
    print_section("Clusters")
    clusters_resp = await ds.clusters_list()
    print_result("List Clusters", clusters_resp)

    # Get first cluster details and permissions
    cluster_id = None
    if clusters_resp.success and clusters_resp.data:
        if isinstance(clusters_resp.data, list) and len(clusters_resp.data) > 0:
            first_cluster = clusters_resp.data[0]
            if isinstance(first_cluster, dict):
                cluster_id = first_cluster.get("cluster_id")
                cluster_name = first_cluster.get("cluster_name", "Unknown")
                print(f"\n   First cluster: {cluster_name} ({cluster_id})")

    if cluster_id:
        print_section(f"Cluster Details: {cluster_id}")
        detail_resp = await ds.clusters_get(cluster_id=cluster_id)
        print_result("Get Cluster", detail_resp, max_records=1)

        print_section(f"Cluster Permissions: {cluster_id}")
        perm_resp = await ds.clusters_get_permissions(cluster_id=cluster_id)
        print_result("Cluster Permissions", perm_resp)

        perm_levels_resp = await ds.clusters_get_permission_levels(cluster_id=cluster_id)
        print_result("Cluster Permission Levels", perm_levels_resp)

    # =========================================================================
    # 5. SQL Warehouses
    # =========================================================================
    print_section("SQL Warehouses")
    wh_resp = await ds.warehouses_list()
    print_result("List SQL Warehouses", wh_resp)

    warehouse_id = None
    if wh_resp.success and wh_resp.data:
        if isinstance(wh_resp.data, list) and len(wh_resp.data) > 0:
            first_wh = wh_resp.data[0]
            if isinstance(first_wh, dict):
                warehouse_id = first_wh.get("id")
                wh_name = first_wh.get("name", "Unknown")
                print(f"\n   First warehouse: {wh_name} ({warehouse_id})")

    # =========================================================================
    # 6. SQL Statement Execution
    # =========================================================================
    if warehouse_id:
        print_section("SQL Statement Execution")

        # Query 1: SELECT 1 (connectivity test)
        print("\n   Query 1: SELECT 1")
        sql_resp = await ds.statement_execution_execute_statement(
            statement="SELECT 1 AS test_value",
            warehouse_id=warehouse_id,
        )
        print_result("SELECT 1", sql_resp)

        # Query 2: Show current user via SQL
        print("\n   Query 2: SELECT current_user()")
        user_sql_resp = await ds.statement_execution_execute_statement(
            statement="SELECT current_user() AS current_user",
            warehouse_id=warehouse_id,
        )
        print_result("Current User (SQL)", user_sql_resp)

        # Query 3: Show databases/catalogs
        print("\n   Query 3: SHOW CATALOGS")
        catalogs_sql_resp = await ds.statement_execution_execute_statement(
            statement="SHOW CATALOGS",
            warehouse_id=warehouse_id,
        )
        print_result("SHOW CATALOGS", catalogs_sql_resp)

        # Query 4: Show schemas in first catalog
        print("\n   Query 4: SHOW SCHEMAS IN default catalog")
        schemas_sql_resp = await ds.statement_execution_execute_statement(
            statement="SHOW SCHEMAS",
            warehouse_id=warehouse_id,
        )
        print_result("SHOW SCHEMAS", schemas_sql_resp)
    else:
        print_section("SQL Statement Execution")
        print("   Skipped — no SQL warehouse available")

    # =========================================================================
    # 7. Users
    # =========================================================================
    print_section("Users")
    users_resp = await ds.users_list(count=5)
    print_result("List Users (first 5)", users_resp)

    # User permission levels
    print_section("User Permission Levels")
    user_perm_levels = await ds.users_get_permission_levels()
    print_result("User Permission Levels", user_perm_levels)

    # User permissions
    print_section("User Permissions")
    user_perms = await ds.users_get_permissions()
    print_result("User Permissions", user_perms)

    # =========================================================================
    # 8. Groups
    # =========================================================================
    print_section("Groups")
    groups_resp = await ds.groups_list(count=5)
    print_result("List Groups (first 5)", groups_resp)

    # =========================================================================
    # 9. Service Principals
    # =========================================================================
    print_section("Service Principals")
    sp_resp = await ds.service_principals_list(count=5)
    print_result("List Service Principals (first 5)", sp_resp)

    # =========================================================================
    # 10. Jobs
    # =========================================================================
    print_section("Jobs")
    jobs_resp = await ds.jobs_list(limit=5)
    print_result("List Jobs (first 5)", jobs_resp)

    # =========================================================================
    # 11. Unity Catalog — Catalogs, Schemas, Tables
    # =========================================================================
    print_section("Unity Catalog — Catalogs")
    catalogs_resp = await ds.catalogs_list()
    print_result("List Catalogs", catalogs_resp)

    catalog_name = None
    if catalogs_resp.success and catalogs_resp.data:
        if isinstance(catalogs_resp.data, list) and len(catalogs_resp.data) > 0:
            first_cat = catalogs_resp.data[0]
            if isinstance(first_cat, dict):
                catalog_name = first_cat.get("name")

    if catalog_name:
        print_section(f"Unity Catalog — Schemas in '{catalog_name}'")
        schemas_resp = await ds.schemas_list(catalog_name=catalog_name)
        print_result("List Schemas", schemas_resp)

        schema_name = None
        if schemas_resp.success and schemas_resp.data:
            if isinstance(schemas_resp.data, list) and len(schemas_resp.data) > 0:
                first_schema = schemas_resp.data[0]
                if isinstance(first_schema, dict):
                    schema_name = first_schema.get("name")

        if schema_name:
            print_section(
                f"Unity Catalog — Tables in '{catalog_name}.{schema_name}'"
            )
            tables_resp = await ds.tables_list(
                catalog_name=catalog_name,
                schema_name=schema_name,
                max_results=5,
            )
            print_result("List Tables (first 5)", tables_resp)

    # =========================================================================
    # 12. Secret Scopes
    # =========================================================================
    print_section("Secret Scopes")
    scopes_resp = await ds.secrets_list_scopes()
    print_result("List Secret Scopes", scopes_resp)

    # List secrets in first scope
    if scopes_resp.success and scopes_resp.data:
        if isinstance(scopes_resp.data, list) and len(scopes_resp.data) > 0:
            first_scope = scopes_resp.data[0]
            if isinstance(first_scope, dict):
                scope_name = first_scope.get("name")
                if scope_name:
                    print_section(f"Secrets in scope '{scope_name}'")
                    secrets_resp = await ds.secrets_list_secrets(scope=scope_name)
                    print_result("List Secrets", secrets_resp)

    # =========================================================================
    # 13. Model Serving Endpoints
    # =========================================================================
    print_section("Model Serving Endpoints")
    endpoints_resp = await ds.serving_endpoints_list()
    print_result("List Serving Endpoints", endpoints_resp)

    # =========================================================================
    # 14. Repos
    # =========================================================================
    print_section("Repos")
    repos_resp = await ds.repos_list()
    print_result("List Repos", repos_resp)

    # =========================================================================
    # 15. DBFS
    # =========================================================================
    print_section("DBFS — List Root (/)")
    dbfs_resp = await ds.dbfs_list(path="/")
    print_result("DBFS Root", dbfs_resp)

    # =========================================================================
    # 16. Pipelines (Delta Live Tables)
    # =========================================================================
    print_section("Pipelines (Delta Live Tables)")
    pipelines_resp = await ds.pipelines_list_pipelines()
    print_result("List Pipelines", pipelines_resp)

    # =========================================================================
    # Done
    # =========================================================================
    print("\n" + "=" * 80)
    print("  All examples completed!")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
