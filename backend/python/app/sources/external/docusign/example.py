# ruff: noqa

"""
DocuSign API Usage Examples (docusign-esign SDK)

This example demonstrates how to use the DocuSign DataSource backed by the
official ``docusign-esign`` Python SDK, covering:
- Authentication (OAuth2, Bearer Token)
- Initializing the Client and DataSource
- Listing Envelopes
- Listing Templates
- Listing Users
- Listing Folders

Prerequisites:
For OAuth2:
1. Create a DocuSign app at https://admindemo.docusign.com/apps-and-keys
2. Set DOCUSIGN_ACCESS_TOKEN environment variable with your OAuth access token
3. Set DOCUSIGN_ACCOUNT_ID environment variable

For Bearer Token:
1. Set DOCUSIGN_ACCESS_TOKEN environment variable with your access token
2. Set DOCUSIGN_ACCOUNT_ID environment variable

Base Path:
Set DOCUSIGN_BASE_PATH to override the default demo environment URL.
Default: https://demo.docusign.net/restapi
Production example: https://na1.docusign.net/restapi
"""

import json
import os

from app.sources.client.docusign.docusign import (
    DocuSignClient,
    DocuSignOAuthConfig,
    DocuSignResponse,
    DocuSignTokenConfig,
)
from app.sources.external.docusign.docusign import DocuSignDataSource

# --- Configuration ---
# Bearer / OAuth access token
ACCESS_TOKEN = os.getenv("DOCUSIGN_ACCESS_TOKEN")

# Account ID (required for all auth methods)
ACCOUNT_ID = os.getenv("DOCUSIGN_ACCOUNT_ID")

# Base path for the SDK ApiClient (default: demo environment)
BASE_PATH = os.getenv("DOCUSIGN_BASE_PATH", "https://demo.docusign.net/restapi")


def print_section(title: str):
    print(f"\n{'-'*80}")
    print(f"| {title}")
    print(f"{'-'*80}")


def print_result(name: str, response: DocuSignResponse, show_data: bool = True):
    if response.success:
        print(f"  {name}: Success")
        if show_data and response.data:
            data = response.data
            # The SDK returns model objects; convert to dict for display
            if hasattr(data, "to_dict"):
                data = data.to_dict()
            if isinstance(data, dict):
                for key in ("envelopes", "envelope_templates", "users", "folders",
                            "brands", "envelope_documents", "signers", "audit_events"):
                    if key in data:
                        items = data[key]
                        if isinstance(items, list):
                            print(f"   Found {len(items)} {key}.")
                            if items:
                                print(f"   Sample: {json.dumps(items[0], indent=2, default=str)[:400]}...")
                        return
            print(f"   Data: {json.dumps(data, indent=2, default=str)[:500]}...")
    else:
        print(f"  {name}: Failed")
        print(f"   Error: {response.error}")
        if response.message:
            print(f"   Message: {response.message}")


def main() -> None:
    # 1. Initialize Client
    print_section("Initializing DocuSign Client")

    if not ACCOUNT_ID:
        print("  DOCUSIGN_ACCOUNT_ID is required. Please set it and try again.")
        return

    if not ACCESS_TOKEN:
        print("  DOCUSIGN_ACCESS_TOKEN is required. Please set it and try again.")
        return

    # Build via OAuth config (using access token)
    config = DocuSignOAuthConfig(
        access_token=ACCESS_TOKEN,
        account_id=ACCOUNT_ID,
        base_path=BASE_PATH,
    )

    client = DocuSignClient.build_with_config(config)
    data_source = DocuSignDataSource(client)
    print("  Client initialized successfully (using docusign-esign SDK).")

    # 2. List Envelopes (from_date is required by the API)
    print_section("Envelopes")
    envelopes_resp = data_source.list_envelopes(
        from_date="2024-01-01T00:00:00Z",
        count="10",
    )
    print_result("List Envelopes", envelopes_resp)

    # 3. List Templates
    print_section("Templates")
    templates_resp = data_source.list_templates(count="10")
    print_result("List Templates", templates_resp)

    # 4. List Users
    print_section("Users")
    users_resp = data_source.list_users(count="10")
    print_result("List Users", users_resp)

    # 5. List Folders
    print_section("Folders")
    folders_resp = data_source.list_folders()
    print_result("List Folders", folders_resp)

    print("\n" + "=" * 80)
    print("  All DocuSign API operations tested!")
    print("=" * 80)


if __name__ == "__main__":
    main()
