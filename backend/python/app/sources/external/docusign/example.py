# ruff: noqa: T201
"""Comprehensive example usage of DocuSign integration with PipesHub.

This script demonstrates all available DocuSign APIs including envelopes,
templates, accounts, users, groups, folders, and workspaces.

It supports both the SDK Wrapper methods (Synchronous) and the new
Manual HTTP methods (Asynchronous).
"""

import asyncio
import os
import sys

# Ensure 'app' is found in the python path
sys.path.append(os.getcwd())

from app.sources.client.docusign.docusign import (
    DocuSignClient,
    DocuSignClientError,
    DocuSignOAuthConfig,
)
from app.sources.external.docusign.docusign import DocuSignDataSource


def print_section(title: str) -> None:
    """Print a formatted section header."""
    print(f"\n{'=' * 70}")
    print(f"  {title}")
    print(f"{'=' * 70}\n")


def get_configured_base_path() -> str:
    """Get base path from env var, ensuring /restapi suffix exists."""
    base_uri = os.environ.get("DOCUSIGN_BASE_URI", "https://demo.docusign.net")
    # The SDK expects the full path including /restapi
    if not base_uri.endswith("/restapi"):
        return f"{base_uri}/restapi"
    return base_uri


async def authenticate_oauth() -> str:
    """Interactive OAuth flow to get a token if one isn't provided."""
    print_section("OAuth Verification")

    client_id = os.environ.get("DOCUSIGN_CLIENT_ID")
    client_secret = os.environ.get("DOCUSIGN_CLIENT_SECRET")
    redirect_uri = os.environ.get(
        "DOCUSIGN_REDIRECT_URI", "http://localhost:3000/callback"
    )
    account_id = os.environ.get("DOCUSIGN_ACCOUNT_ID")

    if not (client_id and client_secret and account_id):
        print("Error: Missing OAuth credentials.")
        print(
            "Please set: DOCUSIGN_CLIENT_ID, DOCUSIGN_CLIENT_SECRET, DOCUSIGN_ACCOUNT_ID"
        )
        sys.exit(1)

    # Use the configured base path
    base_path = get_configured_base_path()
    print(f"Using Base Path: {base_path}")

    config = DocuSignOAuthConfig(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        base_path=base_path,  # <--- Added config
    )

    # 1. Get Auth URL
    oauth_client = config.create_client()
    auth_url = oauth_client.get_authorization_url()
    print(f"1. Visit this URL to authorize the app:\n\n{auth_url}\n")

    # 2. Get Code
    code = input("2. Paste the 'code' parameter from the callback URL here: ").strip()
    if not code:
        print("No code provided.")
        sys.exit(1)

    # 3. Exchange for Token
    print("\nExchanging code for token...")
    response = await oauth_client.exchange_code_for_token(code)

    if response.success and response.data:
        token = response.data.get("access_token")
        print("✓ Authentication successful! Token generated.")
        return token
    else:
        print(f"✗ Authentication failed: {response.message}")
        sys.exit(1)


def initialize_client(
    access_token: str,
) -> tuple[DocuSignDataSource | None, str | None]:
    """Initialize DocuSign client and get inbox folder ID."""
    try:
        account_id = os.environ["DOCUSIGN_ACCOUNT_ID"]
        base_path = get_configured_base_path()

        # Build using OAuth config with existing token
        config = DocuSignOAuthConfig(
            client_id=os.environ.get("DOCUSIGN_CLIENT_ID", ""),
            client_secret=os.environ.get("DOCUSIGN_CLIENT_SECRET", ""),
            redirect_uri=os.environ.get("DOCUSIGN_REDIRECT_URI", ""),
            access_token=access_token,
            base_path=base_path,  # <--- Added config
        )

        # Build the Unified Client using the new Builder pattern
        client = DocuSignClient.build_with_config(config)
        client.set_account_id(account_id)

        # Initialize Data Source
        ds = DocuSignDataSource(client)
        print(f"✓ DocuSign client initialized successfully (Base: {base_path})")

        # Get inbox folder ID (Using Sync SDK Wrapper)
        inbox_folder_id = None
        try:
            folders = ds.list_folders()
            for folder in folders.get("folders", []):
                if folder.get("type") == "inbox":
                    inbox_folder_id = folder.get("folder_id")
                    break
        except Exception:
            pass

        return ds, inbox_folder_id
    except DocuSignClientError as e:
        print(f"✗ Failed to initialize: {e}")
        return None, None


# ========================================================================
# SDK Wrapper Operation Tests (Synchronous)
# ========================================================================


def example_account_operations(ds: DocuSignDataSource) -> None:
    """Demonstrate account operations."""
    print_section("Account Operations (SDK Sync)")

    try:
        # Get account information
        account_info = ds.get_account_information()
        print("Account Information:")
        print(f"  Name: {account_info.get('account_name', 'N/A')}")
        print(f"  ID: {account_info.get('account_id', 'N/A')}")
        print(f"  Status: {account_info.get('status', 'N/A')}")

        # Get account settings
        settings = ds.get_account_settings()
        settings_list = settings.get("account_settings", [])
        print(f"\n✓ Retrieved {len(settings_list)} account settings")

        # List brands (may fail if branding not enabled)
        try:
            brands = ds.list_brands()
            brand_list = brands.get("brands", []) or []
            print(f"✓ Found {len(brand_list)} brands")
        except DocuSignClientError as brand_error:
            if "ACCOUNT_LACKS_PERMISSIONS" in str(brand_error):
                print("ℹ Branding not enabled for this account (normal for demo)")
            else:
                print(f"✗ Error listing brands: {brand_error}")

    except DocuSignClientError as e:
        print(f"✗ Error: {e}")


def example_envelope_operations(
    ds: DocuSignDataSource,
    folder_id: str | None,
) -> list[dict]:
    """Demonstrate envelope operations."""
    print_section("Envelope Operations (SDK Sync)")

    try:
        # List envelopes
        envelopes = ds.list_envelopes(folder_ids=folder_id, count="10")
        envelope_list = envelopes.get("envelopes", [])
        print(f"Found {len(envelope_list)} envelopes\n")

        for idx, env in enumerate(envelope_list[:3], 1):
            print(f"{idx}. {env.get('email_subject', 'N/A')}")
            print(f"   Status: {env.get('status')}")
            print(f"   Created: {env.get('created_date_time', 'N/A')}\n")

        # Get envelope details if available
        if envelope_list:
            envelope_id = envelope_list[0].get("envelope_id")
            details = ds.get_envelope(envelope_id)
            print(f"Envelope Details for {envelope_id}:")
            print(f"  Subject: {details.get('email_subject')}")
            print(f"  Status: {details.get('status')}")

            # List documents
            docs = ds.get_envelope_documents(envelope_id)
            print(f"\n  Documents: {len(docs.get('envelope_documents', []))}")

            # List recipients
            recipients = ds.list_recipients(envelope_id)
            print(f"  Recipients: {len(recipients.get('signers', []))}")

            # Get audit events
            audit = ds.get_envelope_audit_events(envelope_id)
            print(f"  Audit Events: {len(audit.get('audit_events', []))}")

        return envelope_list
    except DocuSignClientError as e:
        print(f"✗ Error: {e}")
        return []


def example_template_operations(ds: DocuSignDataSource) -> None:
    """Demonstrate template operations."""
    print_section("Template Operations (SDK Sync)")

    try:
        templates = ds.list_templates(count="10")
        template_list = templates.get("envelope_templates", []) or []
        print(f"Found {len(template_list)} templates\n")

        for idx, tmpl in enumerate(template_list[:3], 1):
            print(f"{idx}. {tmpl.get('name')}")
            print(f"   Description: {tmpl.get('description', 'N/A')}\n")

        # Get template details if available
        if template_list:
            template_id = template_list[0].get("template_id")
            details = ds.get_template(template_id)
            print(f"Template Details for {template_id}:")
            print(f"  Name: {details.get('name')}")

    except DocuSignClientError as e:
        print(f"✗ Error: {e}")


def example_user_operations(ds: DocuSignDataSource) -> None:
    """Demonstrate user operations."""
    print_section("User Operations (SDK Sync)")

    try:
        users = ds.list_users(count="10")
        user_list = users.get("users", []) or []
        print(f"Found {len(user_list)} users\n")

        for idx, user in enumerate(user_list[:5], 1):
            print(f"{idx}. {user.get('user_name')}")
            print(f"   Email: {user.get('email')}")
            print(f"   Status: {user.get('user_status')}\n")

        # Get user details if available
        if user_list:
            user_id = user_list[0].get("user_id")
            details = ds.get_user(user_id)
            print(f"User Details for {user_id}:")
            print(f"  Name: {details.get('user_name')}")
            print(f"  Email: {details.get('email')}")

    except DocuSignClientError as e:
        print(f"✗ Error: {e}")


def example_group_operations(ds: DocuSignDataSource) -> None:
    """Demonstrate group operations."""
    print_section("Group Operations (SDK Sync)")

    try:
        groups = ds.list_groups(count="10")
        group_list = groups.get("groups", []) or []
        print(f"Found {len(group_list)} groups\n")

        for idx, group in enumerate(group_list[:3], 1):
            print(f"{idx}. {group.get('group_name')}")
            print(f"   Type: {group.get('group_type', 'N/A')}\n")

        # Get group details if available
        if group_list:
            group_id = group_list[0].get("group_id")
            details = ds.get_group(group_id)
            print(f"Group Details for {group_id}:")
            print(f"  Name: {details.get('group_name')}")

    except DocuSignClientError as e:
        print(f"✗ Error: {e}")


def example_folder_operations(ds: DocuSignDataSource) -> None:
    """Demonstrate folder operations."""
    print_section("Folder Operations (SDK Sync)")

    try:
        folders = ds.list_folders()
        folder_list = folders.get("folders", []) or []
        print(f"Found {len(folder_list)} folders\n")

        for folder in folder_list:
            name = folder.get("name")
            folder_id = folder.get("folder_id")
            item_count = int(folder.get("item_count", 0))
            print(f"  - {name} (ID: {folder_id})")
            print(f"    Items: {item_count}\n")

            # List items in folder if it has items
            if item_count and item_count > 0:
                try:
                    items = ds.list_folder_items(folder_id)
                    folder_items = items.get("folder_items") or []

                    if folder_items:
                        print(
                            f"    First item: {folder_items[0].get('subject', 'N/A')}"
                        )
                    else:
                        print("    First item: N/A")

                except DocuSignClientError:
                    pass

    except DocuSignClientError as e:
        print(f"✗ Error: {e}")


def example_workspace_operations(ds: DocuSignDataSource) -> None:
    """Demonstrate workspace operations."""
    print_section("Workspace Operations (SDK Sync)")

    try:
        workspaces = ds.list_workspaces()
        workspace_list = workspaces.get("workspaces", []) or []
        print(f"Found {len(workspace_list)} workspaces\n")

        for idx, workspace in enumerate(workspace_list[:3], 1):
            print(f"{idx}. {workspace.get('workspace_name')}")
            print(f"   ID: {workspace.get('workspace_id')}\n")

        # Get workspace details if available
        if workspace_list:
            workspace_id = workspace_list[0].get("workspace_id")
            details = ds.get_workspace(workspace_id)
            print(f"Workspace Details for {workspace_id}:")
            print(f"  Name: {details.get('workspace_name')}")

    except DocuSignClientError as e:
        print(f"✗ Error: {e}")


def example_batch_operations(ds: DocuSignDataSource) -> None:
    """Demonstrate batch operations with pagination."""
    print_section("Batch Operations (Pagination/Loops)")

    try:
        # Fetch all users
        print("Fetching all users...")
        all_users = ds.fetch_all_users()
        print(f"✓ Fetched {len(all_users)} users total")

        # Fetch all templates
        print("Fetching all templates...")
        all_templates = ds.fetch_all_templates()
        print(f"✓ Fetched {len(all_templates)} templates total")

        # Fetch all groups
        print("Fetching all groups...")
        all_groups = ds.fetch_all_groups()
        print(f"✓ Fetched {len(all_groups)} groups total")

    except DocuSignClientError as e:
        print(f"✗ Error: {e}")


# ========================================================================
# Async Manual HTTP Operation Tests
# ========================================================================


async def example_async_operations(ds: DocuSignDataSource) -> None:
    """Demonstrate the NEW async manual HTTP methods."""
    print_section("Async Manual HTTP Operations (New PR Features)")

    account_id = os.environ["DOCUSIGN_ACCOUNT_ID"]

    try:
        # Test 1: Async Account Info
        print("1. Testing accounts_get_account (Async)...")
        acc_resp = await ds.accounts_get_account(account_id)
        if acc_resp.success:
            print(f"   ✓ Success! Name: {acc_resp.data.get('accountName')}")
        else:
            print(f"   ✗ Failed: {acc_resp.error}")

        # Test 2: Async Users List
        print("\n2. Testing users_list (Async)...")
        users_resp = await ds.users_list(account_id, count=5)
        if users_resp.success:
            users = users_resp.data.get("users", [])
            print(f"   ✓ Success! Fetched {len(users)} users asynchronously.")
        else:
            print(f"   ✗ Failed: {users_resp.error}")

    except Exception as e:
        print(f"✗ Async Exception: {e}")


async def main() -> None:
    """Run comprehensive DocuSign API demonstrations."""

    # 1. Auth Flow (if needed)
    token = os.environ.get("DOCUSIGN_ACCESS_TOKEN")
    if not token:
        token = await authenticate_oauth()

    # 2. Initialize Client
    ds, folder_id = initialize_client(token)
    if not ds:
        return

    # 3. Run Sync SDK Examples (Original functionality)
    example_account_operations(ds)
    example_envelope_operations(ds, folder_id)
    example_template_operations(ds)
    example_user_operations(ds)
    example_group_operations(ds)
    example_folder_operations(ds)
    example_workspace_operations(ds)
    example_batch_operations(ds)

    # 4. Run New Async Examples
    await example_async_operations(ds)

    print_section("All Examples Complete")
    print("✓ Successfully demonstrated Sync SDK and Async HTTP methods.")


if __name__ == "__main__":
    asyncio.run(main())