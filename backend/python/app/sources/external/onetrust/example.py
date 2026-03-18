# ruff: noqa

"""
OneTrust API Usage Examples

This example demonstrates how to use the OneTrust DataSource to interact with
the OneTrust API, covering:
- Authentication (OAuth2 client_credentials, Bearer Token)
- Initializing the Client and DataSource
- Listing Data Subject Requests, Privacy Notices, Consent Receipts
- Getting Assessments, Data Elements, Risks, Vendors

Prerequisites:
For OAuth2:
1. Get your OneTrust API client_id and client_secret
2. Set ONETRUST_CLIENT_ID, ONETRUST_CLIENT_SECRET, ONETRUST_HOSTNAME

For Bearer Token:
1. Get your OneTrust API token
2. Set ONETRUST_TOKEN and ONETRUST_HOSTNAME
"""

import asyncio
import json
import os

from app.sources.client.onetrust.onetrust import (
    OneTrustClient,
    OneTrustOAuthConfig,
    OneTrustResponse,
    OneTrustTokenConfig,
)
from app.sources.external.onetrust.onetrust import OneTrustDataSource

# --- Configuration ---
# OAuth2 credentials
CLIENT_ID = os.getenv("ONETRUST_CLIENT_ID")
CLIENT_SECRET = os.getenv("ONETRUST_CLIENT_SECRET")

# Bearer Token
TOKEN = os.getenv("ONETRUST_TOKEN")

# Hostname (required for both auth methods)
HOSTNAME = os.getenv("ONETRUST_HOSTNAME")


def print_section(title: str):
    print(f"\n{'-'*80}")
    print(f"| {title}")
    print(f"{'-'*80}")


def print_result(name: str, response: OneTrustResponse, show_data: bool = True):
    if response.success:
        print(f"  {name}: Success")
        if show_data and response.data:
            data = response.data
            for key in ("requestQueues", "notices", "consentReceipts",
                        "assessments", "dataElements", "risks", "vendors",
                        "content", "results", "items"):
                if isinstance(data, dict) and key in data:
                    items = data[key]
                    print(f"   Found {len(items)} {key}.")
                    if items:
                        print(f"   Sample: {json.dumps(items[0], indent=2)[:400]}...")
                    return
            if isinstance(data, list):
                print(f"   Found {len(data)} items.")
                if data:
                    print(f"   Sample: {json.dumps(data[0], indent=2)[:400]}...")
                return
            print(f"   Data: {json.dumps(data, indent=2)[:500]}...")
    else:
        print(f"  {name}: Failed")
        print(f"   Error: {response.error}")
        if response.message:
            print(f"   Message: {response.message}")


async def main() -> None:
    # 1. Initialize Client
    print_section("Initializing OneTrust Client")

    if not HOSTNAME:
        print("  ONETRUST_HOSTNAME is required.")
        print("   Please set ONETRUST_HOSTNAME environment variable.")
        return

    config = None

    # Priority 1: OAuth2
    if CLIENT_ID and CLIENT_SECRET:
        print("  Using OAuth2 client_credentials authentication")
        config = OneTrustOAuthConfig(
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
            hostname=HOSTNAME,
        )

    # Priority 2: Bearer Token
    if config is None and TOKEN:
        print("  Using Bearer Token authentication")
        config = OneTrustTokenConfig(token=TOKEN, hostname=HOSTNAME)

    if config is None:
        print("  No valid authentication method found.")
        print("   Please set one of the following:")
        print("   - ONETRUST_CLIENT_ID and ONETRUST_CLIENT_SECRET (for OAuth2)")
        print("   - ONETRUST_TOKEN (for Bearer Token)")
        return

    client = OneTrustClient.build_with_config(config)
    data_source = OneTrustDataSource(client)
    print("Client initialized successfully.")

    try:
        # 2. List Data Subject Request Queues
        print_section("Data Subject Request Queues")
        requests_resp = await data_source.list_request_queues(limit=10)
        print_result("List Request Queues", requests_resp)

        # 3. List Privacy Notices
        print_section("Privacy Notices")
        notices_resp = await data_source.list_privacy_notices(limit=10)
        print_result("List Privacy Notices", notices_resp)

        # 4. List Consent Receipts
        print_section("Consent Receipts")
        receipts_resp = await data_source.list_consent_receipts(limit=10)
        print_result("List Consent Receipts", receipts_resp)

        # 5. List Assessments
        print_section("Assessments")
        assessments_resp = await data_source.list_assessments(limit=10)
        print_result("List Assessments", assessments_resp)

        # 6. List Data Elements
        print_section("Data Elements")
        elements_resp = await data_source.list_data_elements(limit=10)
        print_result("List Data Elements", elements_resp)

        # 7. List Risks
        print_section("Risks")
        risks_resp = await data_source.list_risks(limit=10)
        print_result("List Risks", risks_resp)

        # 8. List Vendors
        print_section("Vendors")
        vendors_resp = await data_source.list_vendors(limit=10)
        print_result("List Vendors", vendors_resp)

    finally:
        print("\nClosing client connection...")
        inner_client = client.get_client()
        if hasattr(inner_client, "close"):
            await inner_client.close()

    print("\n" + "=" * 80)
    print("  All OneTrust API operations tested!")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
