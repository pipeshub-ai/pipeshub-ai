# ruff: noqa
"""Example usage of LumosClient and LumosDataSource.

This demonstrates how to:
1. Create a Lumos client with API key authentication
2. Create a Lumos client with OAuth token authentication
3. Initialize the datasource with the client
4. Make API calls across all Lumos API categories:
   - Apps, Users, Accounts, Groups
   - AppStore (apps, permissions, pre-approval rules, access requests)
   - Access Policies, Vendor Agreements, Documents
   - System (health, activity logs, webhooks)
"""

import asyncio
import json
import os

from app.sources.client.http.http_response import HTTPResponse
from app.sources.client.lumos.lumos import (
    LumosApiKeyConfig,
    LumosClient,
    LumosTokenConfig,
)
from app.sources.external.lumos.lumos import LumosDataSource


def _print_response(title: str, response: HTTPResponse, max_items: int = 3) -> None:
    """Print an HTTPResponse in a clean, summarized format."""
    print(f"\n{title}")
    print(f"  Status: {response.status}")

    if response.status >= 400:
        print(f"  Error: {response.text()}")
        return

    try:
        data = response.json()
    except Exception:
        print(f"  Body: {response.text()[:200]}")
        return

    # Handle paginated list responses
    if isinstance(data, dict) and "items" in data:
        items = data["items"]
        total = data.get("total", len(items))
        print(f"  Total: {total} | Page: {data.get('page', '?')}/{data.get('total_pages', '?')}")
        for i, item in enumerate(items[:max_items], 1):
            name = item.get("name", item.get("email", item.get("id", "?")))
            status = item.get("status", "")
            print(f"    {i}. {name}" + (f" [{status}]" if status else ""))
        if len(items) > max_items:
            print(f"    ... and {len(items) - max_items} more")
    elif isinstance(data, list):
        print(f"  Items: {len(data)}")
        for i, item in enumerate(data[:max_items], 1):
            if isinstance(item, dict):
                print(f"    {i}. {item.get('name', item.get('id', json.dumps(item)[:80]))}")
            else:
                print(f"    {i}. {item}")
    elif isinstance(data, dict):
        print(f"  Keys: {', '.join(list(data.keys())[:8])}")
        if "id" in data:
            print(f"  ID: {data['id']}")
        if "name" in data:
            print(f"  Name: {data['name']}")
        if "email" in data:
            print(f"  Email: {data['email']}")
    else:
        print(f"  Body: {str(data)[:200]}")


# ============================================================================
# Example 1: API Key Authentication
# ============================================================================

async def example_with_api_key() -> None:
    """Demonstrate API Key authentication with Lumos."""
    api_key = os.getenv("LUMOS_API_KEY")
    if not api_key:
        raise Exception(
            "LUMOS_API_KEY is not set. "
            "Please set it in your environment: export LUMOS_API_KEY='your_api_key_here'"
        )

    # Create client with API key config
    lumos_client = LumosClient.build_with_config(
        LumosApiKeyConfig(api_key=api_key)
    )
    ds = LumosDataSource(lumos_client)

    try:
        # --- System ---
        print("\n--- Liveness Check ---")
        resp = await ds.lumos_liveness_check()
        _print_response("API Info:", resp)

        print("\n--- ASCII Art ---")
        resp = await ds.lumos_art()
        print(f"  Status: {resp.status}")
        print(f"  {resp.text()[:120]}...")

        # --- Apps ---
        print("\n--- List Apps (first page, 5 items) ---")
        apps_resp = await ds.list_apps(page=1, size=5)
        _print_response("Apps:", apps_resp)

        print("\n--- Get App Categories ---")
        resp = await ds.get_app_categories()
        _print_response("App Categories:", resp)

        # Get a specific app if available
        if apps_resp.status == 200:
            apps_data = apps_resp.json()
            items = apps_data.get("items", [])
            if items:
                app_id = items[0]["id"]
                print(f"\n--- Get App Details (ID: {app_id}) ---")
                resp = await ds.get_app(app_id=app_id, expand=["custom_attributes"])
                _print_response("App Detail:", resp)

                print(f"\n--- Get App Settings (ID: {app_id}) ---")
                resp = await ds.get_app_settings(app_id=app_id)
                _print_response("App Settings:", resp)

        # --- Users ---
        print("\n--- Get Current User ---")
        resp = await ds.current_user()
        _print_response("Current User:", resp)

        print("\n--- List Users (first 5) ---")
        users_resp = await ds.list_users(page=1, size=5, expand=["custom_attributes"])
        _print_response("Users:", users_resp)

        # Get a specific user's accounts and roles
        if users_resp.status == 200:
            users_data = users_resp.json()
            user_items = users_data.get("items", [])
            if user_items:
                user_id = user_items[0]["id"]
                print(f"\n--- Get User Accounts (User ID: {user_id}) ---")
                resp = await ds.get_user_accounts(
                    user_id=user_id, expand=["app"], page=1, size=5
                )
                _print_response("User Accounts:", resp)

                print(f"\n--- Get User Roles (User ID: {user_id}) ---")
                resp = await ds.get_user_roles_users_user_id_roles_get(user_id=user_id)
                _print_response("User Roles:", resp)

        # --- Groups ---
        print("\n--- List Groups (first 5) ---")
        groups_resp = await ds.get_groups(page=1, size=5)
        _print_response("Groups:", groups_resp)

        if groups_resp.status == 200:
            groups_data = groups_resp.json()
            group_items = groups_data.get("items", [])
            if group_items:
                group_id = group_items[0]["id"]
                print(f"\n--- Get Group Membership (Group ID: {group_id}) ---")
                resp = await ds.get_group_membership(
                    group_id=group_id, page=1, size=5
                )
                _print_response("Group Members:", resp)

        # --- Accounts ---
        print("\n--- List Accounts (first 5, active only) ---")
        resp = await ds.get_accounts(
            status=["ACTIVE"],
            expand=["app"],
            page=1,
            size=5,
        )
        _print_response("Accounts:", resp)

        # --- Activity Logs ---
        print("\n--- Get Activity Logs (last 5) ---")
        resp = await ds.get_activity_logs(limit=5, offset=0)
        _print_response("Activity Logs:", resp)

        # --- Identity Events ---
        print("\n--- Get Identity Events ---")
        resp = await ds.get_identity_events(limit=5)
        _print_response("Identity Events:", resp)

        # --- AppStore Apps ---
        print("\n--- List AppStore Apps (first 5) ---")
        appstore_resp = await ds.get_app_store_apps(page=1, size=5)
        _print_response("AppStore Apps:", appstore_resp)

        if appstore_resp.status == 200:
            appstore_data = appstore_resp.json()
            appstore_items = appstore_data.get("items", [])
            if appstore_items:
                appstore_app_id = appstore_items[0]["id"]
                print(f"\n--- Get AppStore App Settings (ID: {appstore_app_id}) ---")
                resp = await ds.get_app_store_app_settings(app_id=appstore_app_id)
                _print_response("AppStore App Settings:", resp)

                print(f"\n--- Get AppStore Permissions for App (ID: {appstore_app_id}) ---")
                resp = await ds.get_appstore_permissions_for_app_appstore_apps_app_id_requestable_permissions_get(
                    app_id=appstore_app_id, page=1, size=5
                )
                _print_response("AppStore Permissions:", resp)

        # --- AppStore Pre-Approval Rules ---
        print("\n--- List Pre-Approval Rules ---")
        resp = await ds.get_appstore_pre_approval_rules_for_app_appstore_pre_approval_rules_get(
            page=1, size=5
        )
        _print_response("Pre-Approval Rules:", resp)

        # --- Access Requests ---
        print("\n--- List Access Requests (recent, first 5) ---")
        resp = await ds.get_access_requests(
            statuses=["PENDING", "APPROVED", "PROVISIONED"],
            sort="DESC",
            page=1,
            size=5,
        )
        _print_response("Access Requests:", resp)

        # --- Access Policies ---
        print("\n--- List Access Policies ---")
        resp = await ds.get_access_policies(page=1, size=5)
        _print_response("Access Policies:", resp)

        # --- Vendor Agreements ---
        print("\n--- List Vendor Agreements ---")
        resp = await ds.list_vendor_agreements(page=1, size=5)
        _print_response("Vendor Agreements:", resp)

        # --- Inline Webhooks ---
        print("\n--- Get Inline Webhooks ---")
        resp = await ds.get_inline_webhooks_inline_webhooks_get()
        _print_response("Inline Webhooks:", resp)

    finally:
        await lumos_client.get_client().close()


# ============================================================================
# Example 2: OAuth Token Authentication
# ============================================================================

async def example_with_oauth_token() -> None:
    """Demonstrate OAuth Bearer token authentication with Lumos."""
    oauth_token = os.getenv("LUMOS_OAUTH_TOKEN")
    if not oauth_token:
        raise Exception(
            "LUMOS_OAUTH_TOKEN is not set. "
            "Please set it in your environment: export LUMOS_OAUTH_TOKEN='your_oauth_token_here'"
        )

    # Create client with OAuth token config
    lumos_client = LumosClient.build_with_config(
        LumosTokenConfig(token=oauth_token)
    )
    ds = LumosDataSource(lumos_client)

    try:
        # --- Current User (verify auth works) ---
        print("\n--- Get Current User (OAuth) ---")
        resp = await ds.current_user()
        _print_response("Current User:", resp)

        # --- List Apps with expanded custom attributes ---
        print("\n--- List Apps with Custom Attributes (OAuth) ---")
        resp = await ds.list_apps(
            expand=["custom_attributes"],
            page=1,
            size=3,
        )
        _print_response("Apps:", resp)

        # --- Search Users by name ---
        print("\n--- Search Users (OAuth) ---")
        resp = await ds.list_users(search_term="admin", exact_match=False, page=1, size=5)
        _print_response("User Search Results:", resp)

        # --- List Accounts filtered by discovery source ---
        print("\n--- List Accounts by Source (OAuth) ---")
        resp = await ds.get_accounts(
            sources=["OKTA", "GSUITE_OAUTH"],
            page=1,
            size=5,
        )
        _print_response("Accounts by Source:", resp)

        # --- AppStore: search permissions ---
        print("\n--- Search AppStore Permissions (OAuth) ---")
        resp = await ds.get_appstore_permissions_appstore_requestable_permissions_get(
            in_app_store=True,
            page=1,
            size=5,
        )
        _print_response("Visible AppStore Permissions:", resp)

        # --- Identity Events with field filter ---
        print("\n--- Identity Events (title changes) ---")
        resp = await ds.get_identity_events(
            changed_fields=["title", "team"],
            limit=5,
        )
        _print_response("Identity Events:", resp)

    finally:
        await lumos_client.get_client().close()


# ============================================================================
# Main
# ============================================================================

def main() -> None:
    """Run Lumos example demonstrations."""
    print("=" * 70)
    print("Lumos API Client Examples")
    print("=" * 70)

    # Run API key example if LUMOS_API_KEY is set
    if os.getenv("LUMOS_API_KEY"):
        print("\n[*] Example 1: API Key Authentication")
        print("-" * 70)
        asyncio.run(example_with_api_key())
    else:
        print("\n[SKIP] Example 1: LUMOS_API_KEY not set")

    # Run OAuth token example if LUMOS_OAUTH_TOKEN is set
    if os.getenv("LUMOS_OAUTH_TOKEN"):
        print("\n[*] Example 2: OAuth Token Authentication")
        print("-" * 70)
        asyncio.run(example_with_oauth_token())
    else:
        print("\n[SKIP] Example 2: LUMOS_OAUTH_TOKEN not set")

    if not os.getenv("LUMOS_API_KEY") and not os.getenv("LUMOS_OAUTH_TOKEN"):
        print("\nNo credentials found. Set one of:")
        print("  export LUMOS_API_KEY='your_api_key'")
        print("  export LUMOS_OAUTH_TOKEN='your_oauth_token'")

    print("\n" + "=" * 70)
    print("Examples completed")
    print("=" * 70)


if __name__ == "__main__":
    main()
