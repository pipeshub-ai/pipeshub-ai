# ruff: noqa

"""
DocuSign Unified API Usage Examples

This example demonstrates how to use the DocuSign DataSource covering:
- eSignature (SDK): Envelopes, Templates, Users, Folders
- Admin (HTTP): Organizations, Users
- Rooms (HTTP): Rooms, Roles
- Click (HTTP): Clickwraps, Service Info
- Monitor (HTTP): Audit stream
- WebForms (HTTP): Forms

Prerequisites:
1. Set DOCUSIGN_ACCESS_TOKEN environment variable
2. Set DOCUSIGN_ACCOUNT_ID environment variable
3. Optionally set DOCUSIGN_BASE_PATH (default: https://demo.docusign.net/restapi)
"""

import asyncio
import json
import os

from app.sources.client.docusign.docusign import (
    DocuSignClient,
    DocuSignOAuthConfig,
    DocuSignResponse,
)
from app.sources.external.docusign.docusign import DocuSignDataSource

# --- Configuration ---
ACCESS_TOKEN = os.getenv("DOCUSIGN_ACCESS_TOKEN")
ACCOUNT_ID = os.getenv("DOCUSIGN_ACCOUNT_ID")
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
                            "brands", "envelope_documents", "signers", "audit_events",
                            "organizations", "rooms", "clickwraps", "forms"):
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


async def main() -> None:
    # 1. Initialize Client
    print_section("Initializing DocuSign Client")

    if not ACCOUNT_ID:
        print("  DOCUSIGN_ACCOUNT_ID is required. Please set it and try again.")
        return

    if not ACCESS_TOKEN:
        print("  DOCUSIGN_ACCESS_TOKEN is required. Please set it and try again.")
        return

    config = DocuSignOAuthConfig(
        access_token=ACCESS_TOKEN,
        account_id=ACCOUNT_ID,
        base_path=BASE_PATH,
    )

    client = DocuSignClient.build_with_config(config)
    ds = DocuSignDataSource(client)
    print("  Client initialized successfully (unified: SDK + HTTP).")

    # ===== eSign SDK methods (synchronous) =====

    # 2. List Envelopes
    print_section("eSign: Envelopes")
    envelopes_resp = ds.list_envelopes(
        from_date="2024-01-01T00:00:00Z",
        count="10",
    )
    print_result("List Envelopes", envelopes_resp)

    # 3. List Templates
    print_section("eSign: Templates")
    templates_resp = ds.list_templates(count="10")
    print_result("List Templates", templates_resp)

    # 4. List Users
    print_section("eSign: Users")
    users_resp = ds.list_users(count="10")
    print_result("List Users", users_resp)

    # 5. List Folders
    print_section("eSign: Folders")
    folders_resp = ds.list_folders()
    print_result("List Folders", folders_resp)

    # ===== Admin HTTP methods (async) =====

    print_section("Admin: Organizations")
    orgs_resp = await ds.admin_get_organizations()
    print_result("Get Organizations", orgs_resp)

    # If we got organizations, try to list users
    if orgs_resp.success and orgs_resp.data:
        orgs = orgs_resp.data if isinstance(orgs_resp.data, dict) else {}
        org_list = orgs.get("organizations", [])
        if org_list:
            org_id = str(org_list[0].get("id", ""))
            if org_id:
                print_section("Admin: Organization Users")
                admin_users_resp = await ds.admin_get_users(
                    org_id=org_id,
                    take=5,
                )
                print_result("Get Admin Users", admin_users_resp)

    # ===== Rooms HTTP methods (async) =====

    print_section("Rooms: List Rooms")
    rooms_resp = await ds.rooms_get_rooms(
        account_id=ACCOUNT_ID,
        count=5,
    )
    print_result("Get Rooms", rooms_resp)

    print_section("Rooms: Roles")
    roles_resp = await ds.rooms_get_roles(account_id=ACCOUNT_ID)
    print_result("Get Roles", roles_resp)

    # ===== Click HTTP methods (async) =====

    print_section("Click: Clickwraps")
    clickwraps_resp = await ds.click_get_clickwraps(account_id=ACCOUNT_ID)
    print_result("Get Clickwraps", clickwraps_resp)

    print_section("Click: Service Info")
    svc_resp = await ds.click_get_service_info(account_id=ACCOUNT_ID)
    print_result("Get Service Info", svc_resp)

    # ===== Monitor HTTP methods (async) =====

    print_section("Monitor: Audit Stream")
    stream_resp = await ds.monitor_get_stream(limit=5)
    print_result("Get Stream", stream_resp)

    # ===== WebForms HTTP methods (async) =====

    print_section("WebForms: List Forms")
    forms_resp = await ds.webforms_list_forms(account_id=ACCOUNT_ID)
    print_result("List Forms", forms_resp)

    # ===== Cleanup =====

    # Close HTTP clients
    inner = client.get_client()
    if hasattr(inner, "_http_clients"):
        for http_client in inner._http_clients.values():
            if hasattr(http_client, "close"):
                await http_client.close()

    print("\n" + "=" * 80)
    print("  All DocuSign API operations tested!")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
