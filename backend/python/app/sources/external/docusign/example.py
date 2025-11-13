"""Comprehensive example usage of DocuSign integration with PipesHub.

This script demonstrates all available DocuSign APIs including envelopes,
templates, accounts, users, groups, folders, and workspaces.

Environment Variables Required:
    DOCUSIGN_ACCESS_TOKEN: OAuth access token
    DOCUSIGN_BASE_URI: Base URI (e.g., https://demo.docusign.net)
    DOCUSIGN_ACCOUNT_ID: Your DocuSign account ID
"""

# ruff: noqa: T201

import os

from app.sources.client.docusign.docusign import (
    DocuSignClient,
    DocuSignClientError,
)
from app.sources.external.docusign.docusign import DocuSignDataSource


def print_section(title: str) -> None:
    """Print a formatted section header."""
    print(f"\n{'=' * 70}")
    print(f"  {title}")
    print(f"{'=' * 70}\n")


def check_environment() -> bool:
    """Check if required environment variables are set."""
    required_vars = [
        "DOCUSIGN_ACCESS_TOKEN",
        "DOCUSIGN_BASE_URI",
        "DOCUSIGN_ACCOUNT_ID",
    ]
    missing_vars = [var for var in required_vars if not os.environ.get(var)]

    if missing_vars:
        print(
            "Error: Missing required environment variables: "
            f"{', '.join(missing_vars)}"
        )
        print("\nPlease set:")
        print("  export DOCUSIGN_ACCESS_TOKEN=your_token")
        print("  export DOCUSIGN_BASE_URI=https://demo.docusign.net")
        print("  export DOCUSIGN_ACCOUNT_ID=your_account_id")
        return False
    return True


def initialize_client() -> tuple[DocuSignDataSource | None, str | None]:
    """Initialize DocuSign client and get inbox folder ID."""
    try:
        client = DocuSignClient(
            access_token=os.environ["DOCUSIGN_ACCESS_TOKEN"],
            base_uri=os.environ["DOCUSIGN_BASE_URI"],
            account_id=os.environ["DOCUSIGN_ACCOUNT_ID"],
        )
        ds = DocuSignDataSource(client)
        print("✓ DocuSign client initialized successfully")

        # Get inbox folder ID
        folders = ds.list_folders()
        inbox_folder_id = None
        for folder in folders.get("folders", []):
            if folder.get("type") == "inbox":
                inbox_folder_id = folder.get("folder_id")
                break

        return ds, inbox_folder_id
    except DocuSignClientError as e:
        print(f"✗ Failed to initialize: {e}")
        return None, None


def example_account_operations(ds: DocuSignDataSource) -> None:
    """Demonstrate account operations."""
    print_section("Account Operations")

    try:
        # Get account information
        account_info = ds.get_account_information()
        print("Account Information:")
        print(f"  Name: {account_info.get('account_name', 'N/A')}")
        print(f"  ID: {account_info.get('account_id', 'N/A')}")
        print(f"  Status: {account_info.get('status', 'N/A')}")

        # Get account settings
        settings = ds.get_account_settings()
        settings_list = settings.get('account_settings', [])
        print(f"\n✓ Retrieved {len(settings_list)} account settings")

        # List brands (may fail if branding not enabled)
        try:
            brands = ds.list_brands()
            brand_list = brands.get("brands", []) or []
            print(f"✓ Found {len(brand_list)} brands")
        except DocuSignClientError as brand_error:
            if "ACCOUNT_LACKS_PERMISSIONS" in str(brand_error):
                print("ℹ Branding not enabled for this account (normal for demo accounts)")
            else:
                print(f"✗ Error listing brands: {brand_error}")

    except DocuSignClientError as e:
        print(f"✗ Error: {e}")


def example_envelope_operations(
    ds: DocuSignDataSource,
    folder_id: str | None,
) -> list[dict]:
    """Demonstrate envelope operations."""
    print_section("Envelope Operations")

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
    print_section("Template Operations")

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
    print_section("User Operations")

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
    print_section("Group Operations")

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
    print_section("Folder Operations")

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
                        print(f"    First item: {folder_items[0].get('subject', 'N/A')}")
                    else:
                        print("    First item: N/A")

                except DocuSignClientError:
                    pass

    except DocuSignClientError as e:
        print(f"✗ Error: {e}")


def example_workspace_operations(ds: DocuSignDataSource) -> None:
    """Demonstrate workspace operations."""
    print_section("Workspace Operations")

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
    print_section("Batch Operations (Pagination)")

    try:
        # Fetch all users
        all_users = ds.fetch_all_users()
        print(f"✓ Fetched {len(all_users)} users total")

        # Fetch all templates
        all_templates = ds.fetch_all_templates()
        print(f"✓ Fetched {len(all_templates)} templates total")

        # Fetch all groups
        all_groups = ds.fetch_all_groups()
        print(f"✓ Fetched {len(all_groups)} groups total")

    except DocuSignClientError as e:
        print(f"✗ Error: {e}")


def main() -> None:
    """Run comprehensive DocuSign API demonstrations."""
    if not check_environment():
        return

    ds, folder_id = initialize_client()
    if not ds:
        return

    # Run all examples
    example_account_operations(ds)
    example_envelope_operations(ds, folder_id)
    example_template_operations(ds)
    example_user_operations(ds)
    example_group_operations(ds)
    example_folder_operations(ds)
    example_workspace_operations(ds)
    example_batch_operations(ds)

    print_section("All Examples Complete")
    print("✓ Successfully demonstrated all DocuSign APIs!")
    print("\nNote: Some operations may show '0 results' if your")
    print("account doesn't have that data. This is normal.")


if __name__ == "__main__":
    main()
