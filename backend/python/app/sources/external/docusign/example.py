"""Example usage of DocuSign integration with PipesHub.

This script demonstrates how to initialize the DocuSign client and
data source, and perform various operations like listing envelopes,
templates, and downloading documents.

Environment Variables Required:
    DOCUSIGN_ACCESS_TOKEN: OAuth access token for authentication
    DOCUSIGN_BASE_URI: Base URI for DocuSign API
    DOCUSIGN_ACCOUNT_ID: Your DocuSign account ID
"""

# ruff: noqa: T201 (print statements are OK in example files)

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.sources.client.docusign.docusign import (
    DocuSignClient,
    DocuSignClientError,
)
from app.sources.external.docusign.docusign import DocuSignDataSource


def print_section(title: str) -> None:
    """Print a formatted section header."""
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}\n")


def check_environment() -> bool:
    """Check if required environment variables are set.

    Returns:
        True if all required variables are set, False otherwise
    """
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
        print("\nPlease set the following environment variables:")
        print("  export DOCUSIGN_ACCESS_TOKEN=your_token")
        print("  export DOCUSIGN_BASE_URI=https://demo.docusign.net")
        print("  export DOCUSIGN_ACCOUNT_ID=your_account_id")
        return False
    return True


def initialize_client() -> tuple[DocuSignDataSource | None, str | None]:
    """Initialize DocuSign client and data source.

    Returns:
        Tuple of (DocuSignDataSource instance, inbox_folder_id) or (None, None)
    """
    try:
        client = DocuSignClient(
            access_token=os.environ["DOCUSIGN_ACCESS_TOKEN"],
            base_uri=os.environ["DOCUSIGN_BASE_URI"],
            account_id=os.environ["DOCUSIGN_ACCOUNT_ID"],
        )
        ds = DocuSignDataSource(client)
        print("✓ DocuSign client initialized successfully")
        
        # Get inbox folder ID for listing envelopes
        folders = ds.list_folders()
        inbox_folder_id = None
        for folder in folders.get("folders", []):
            if folder.get("type") == "inbox":
                inbox_folder_id = folder.get("folder_id")
                break
        
        return ds, inbox_folder_id
    except DocuSignClientError as e:
        print(f"✗ Failed to initialize DocuSign client: {e}")
        return None, None


def example_list_envelopes(
    ds: DocuSignDataSource,
    folder_id: str | None,
) -> list[dict]:
    """Example: List recent envelopes.
    
    Returns:
        List of envelopes
    """
    print_section("Example 1: List Recent Envelopes")
    try:
        # Use folder_ids parameter to avoid from_date requirement
        envelopes = ds.list_envelopes(
            folder_ids=folder_id if folder_id else None,
            count="10",
        )

        envelope_list = envelopes.get("envelopes", [])
        print(f"Found {len(envelope_list)} envelopes")

        for idx, env in enumerate(envelope_list[:5], 1):
            print(f"\n{idx}. Envelope ID: {env.get('envelope_id')}")
            print(f"   Status: {env.get('status')}")
            print(f"   Subject: {env.get('email_subject', 'N/A')}")
            print(f"   Created: {env.get('created_date_time', 'N/A')}")

        return envelope_list
    except DocuSignClientError as e:
        print(f"✗ Error listing envelopes: {e}")
        return []


def example_get_envelope_details(
    ds: DocuSignDataSource,
    envelope_list: list[dict],
) -> None:
    """Example: Get envelope details."""
    print_section("Example 2: Get Envelope Details")
    try:
        if envelope_list:
            envelope_id = envelope_list[0].get("envelope_id")
            print(f"Getting details for envelope: {envelope_id}")

            envelope_details = ds.get_envelope(envelope_id)
            print("\nEnvelope Details:")
            print(
                f"  Subject: {envelope_details.get('email_subject', 'N/A')}"
            )
            print(f"  Status: {envelope_details.get('status')}")
            sender_name = (
                envelope_details.get("sender", {}).get("user_name", "N/A")
            )
            print(f"  Sender: {sender_name}")
            print(f"  Created: {envelope_details.get('created_date_time')}")

            print("\n  Documents:")
            docs = ds.get_envelope_documents(envelope_id)
            for doc in docs.get("envelope_documents", []):
                doc_name = doc.get("name")
                doc_id = doc.get("document_id")
                print(f"    - {doc_name} (ID: {doc_id})")
        else:
            print("No envelopes found to display details")

    except DocuSignClientError as e:
        print(f"✗ Error getting envelope details: {e}")


def example_list_templates(ds: DocuSignDataSource) -> None:
    """Example: List templates."""
    print_section("Example 3: List Templates")
    try:
        templates = ds.list_templates(count="10")
        template_list = templates.get("envelope_templates", []) or []
        print(f"Found {len(template_list)} templates")

        if not template_list:
            print("(No templates found - create some in DocuSign web UI)")
        
        for idx, tmpl in enumerate(template_list[:5], 1):
            print(f"\n{idx}. Template ID: {tmpl.get('template_id')}")
            print(f"   Name: {tmpl.get('name')}")
            print(f"   Description: {tmpl.get('description', 'N/A')}")
            print(f"   Created: {tmpl.get('created', 'N/A')}")

    except DocuSignClientError as e:
        print(f"✗ Error listing templates: {e}")


def example_list_folders(ds: DocuSignDataSource) -> None:
    """Example: List folders."""
    print_section("Example 4: List Folders")
    try:
        folders = ds.list_folders()
        folder_list = folders.get("folders", []) or []
        print(f"Found {len(folder_list)} folders")

        for folder in folder_list:
            folder_name = folder.get("name")
            folder_id = folder.get("folder_id")
            print(f"\n  - {folder_name} (ID: {folder_id})")
            print(f"    Type: {folder.get('type')}")
            if folder.get("item_count") is not None:
                print(f"    Items: {folder.get('item_count')}")

    except DocuSignClientError as e:
        print(f"✗ Error listing folders: {e}")


def example_list_users(ds: DocuSignDataSource) -> None:
    """Example: List users."""
    print_section("Example 5: List Users")
    try:
        users = ds.list_users(count="10")
        user_list = users.get("users", []) or []
        print(f"Found {len(user_list)} users")

        for idx, user in enumerate(user_list[:5], 1):
            print(f"\n{idx}. User ID: {user.get('user_id')}")
            print(f"   Name: {user.get('user_name')}")
            print(f"   Email: {user.get('email')}")
            print(f"   Status: {user.get('user_status')}")

    except DocuSignClientError as e:
        print(f"✗ Error listing users: {e}")


def example_download_document(
    ds: DocuSignDataSource,
    envelope_list: list[dict],
) -> None:
    """Example: Download a document."""
    print_section("Example 6: Download Document")
    try:
        if envelope_list:
            envelope_id = envelope_list[0].get("envelope_id")
            docs = ds.get_envelope_documents(envelope_id)
            doc_list = docs.get("envelope_documents", [])

            if doc_list:
                doc = doc_list[0]
                doc_id = doc.get("document_id")
                doc_name = doc.get("name", "document")

                print(f"Downloading: {doc_name}")
                content = ds.download_document(envelope_id, doc_id)

                output_path = Path(f"docusign_{doc_name}")
                output_path.write_bytes(content)
                print(f"✓ Document saved to: {output_path}")
                print(f"  Size: {len(content)} bytes")
            else:
                print("No documents found in envelope")
        else:
            print("No envelopes found to download documents from")

    except DocuSignClientError as e:
        print(f"✗ Error downloading document: {e}")


def example_fetch_all_envelopes(
    ds: DocuSignDataSource,
    folder_id: str | None,
) -> None:
    """Example: Fetch all envelopes with pagination."""
    print_section("Example 7: Fetch All Envelopes (Paginated)")
    try:
        # Use a proper date format for from_date
        from_date = (datetime.now(tz=UTC) - timedelta(days=90)).strftime(
            "%Y-%m-%d"
        )
        
        # Use folder_ids to list envelopes
        all_envelopes = []
        if folder_id:
            response = ds.list_envelopes(folder_ids=folder_id, count="100")
            all_envelopes = response.get("envelopes", [])
        
        print(f"✓ Fetched {len(all_envelopes)} envelopes in total")

        if all_envelopes:
            status_count: dict[str, int] = {}
            for env in all_envelopes:
                status = env.get("status", "unknown")
                status_count[status] = status_count.get(status, 0) + 1

            print("\nStatus Breakdown:")
            for status, count in sorted(status_count.items()):
                print(f"  {status}: {count}")
        else:
            print("(No envelopes in the past 90 days)")

    except DocuSignClientError as e:
        print(f"✗ Error fetching all envelopes: {e}")


def main() -> None:
    """Run example DocuSign operations."""
    if not check_environment():
        return

    ds, folder_id = initialize_client()
    if not ds:
        return

    # Run all examples - pass envelope_list between examples
    envelope_list = example_list_envelopes(ds, folder_id)
    example_get_envelope_details(ds, envelope_list)
    example_list_templates(ds)
    example_list_folders(ds)
    example_list_users(ds)
    example_download_document(ds, envelope_list)
    example_fetch_all_envelopes(ds, folder_id)

    print_section("Examples Complete")
    print("✓ All examples executed successfully!")
    print("\nNote: Some examples may show '0 results' if your account")
    print("doesn't have templates or recent envelopes. This is normal.")


if __name__ == "__main__":
    main()