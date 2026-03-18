# ruff: noqa

"""
Freshservice API Usage Examples

This example demonstrates how to use the Freshservice DataSource to interact with
the Freshservice API v2, covering:
- Authentication (API Key via Basic Auth)
- Initializing the Client and DataSource
- Listing tickets, agents, requesters
- Ticket CRUD operations
- Listing assets, problems, changes, departments

Prerequisites:
1. Set FRESHSERVICE_DOMAIN environment variable (e.g., 'company.freshservice.com')
2. Set FRESHSERVICE_API_KEY environment variable with your API key

You can obtain an API key from:
Freshservice Admin > Profile Settings > API Key
"""

import asyncio
import json
import os

from app.sources.client.freshservice.freshservice import (
    FreshserviceApiKeyConfig,
    FreshserviceClient,
    FreshserviceResponse,
)
from app.sources.external.freshservice.freshservice import FreshserviceDataSource

# --- Configuration ---
DOMAIN = os.getenv("FRESHSERVICE_DOMAIN")
API_KEY = os.getenv("FRESHSERVICE_API_KEY")


def print_section(title: str):
    print(f"\n{'-'*80}")
    print(f"| {title}")
    print(f"{'-'*80}")


def print_result(name: str, response: FreshserviceResponse, show_data: bool = True):
    if response.success:
        print(f"  {name}: Success")
        if show_data and response.data:
            data = response.data
            if isinstance(data, dict):
                for key in ("tickets", "requesters", "agents", "assets", "problems",
                            "changes", "releases", "departments", "groups",
                            "service_items", "conversations"):
                    if key in data:
                        items = data[key]
                        print(f"   Found {len(items)} {key}.")
                        if items:
                            print(f"   Sample: {json.dumps(items[0], indent=2)[:400]}...")
                        return
                # Single item response
                print(f"   Data: {json.dumps(data, indent=2)[:500]}...")
            else:
                print(f"   Data: {json.dumps(data, indent=2)[:500]}...")
    else:
        print(f"  {name}: Failed")
        print(f"   Error: {response.error}")
        if response.message:
            print(f"   Message: {response.message}")


async def main() -> None:
    if not DOMAIN:
        print("FRESHSERVICE_DOMAIN is not set")
        return
    if not API_KEY:
        print("FRESHSERVICE_API_KEY is not set")
        return

    # 1. Initialize Client
    print_section("Initializing Freshservice Client")
    client = FreshserviceClient.build_with_api_key_config(
        FreshserviceApiKeyConfig(domain=DOMAIN, api_key=API_KEY)
    )
    print(f"  Connected to: {client.get_domain()}")
    print(f"  Base URL: {client.get_base_url()}")

    data_source = FreshserviceDataSource(client)

    try:
        # 2. List Tickets
        print_section("Tickets")
        tickets_resp = await data_source.list_tickets(per_page=5)
        print_result("List Tickets", tickets_resp)

        # 3. Get first ticket details
        if tickets_resp.success and tickets_resp.data:
            tickets = tickets_resp.data.get("tickets", [])
            if tickets:
                ticket_id = tickets[0].get("id")
                print_section(f"Ticket Details (ID: {ticket_id})")
                ticket_resp = await data_source.get_ticket(id=ticket_id)
                print_result("Get Ticket", ticket_resp)

                # 4. List ticket conversations
                print_section(f"Ticket Conversations (ID: {ticket_id})")
                convs_resp = await data_source.list_ticket_conversations(id=ticket_id)
                print_result("List Conversations", convs_resp)

        # 5. List Requesters
        print_section("Requesters")
        requesters_resp = await data_source.list_requesters(per_page=5)
        print_result("List Requesters", requesters_resp)

        # 6. List Agents
        print_section("Agents")
        agents_resp = await data_source.list_agents(per_page=5)
        print_result("List Agents", agents_resp)

        # 7. List Assets
        print_section("Assets")
        assets_resp = await data_source.list_assets(per_page=5)
        print_result("List Assets", assets_resp)

        # 8. List Problems
        print_section("Problems")
        problems_resp = await data_source.list_problems(per_page=5)
        print_result("List Problems", problems_resp)

        # 9. List Changes
        print_section("Changes")
        changes_resp = await data_source.list_changes(per_page=5)
        print_result("List Changes", changes_resp)

        # 10. List Releases
        print_section("Releases")
        releases_resp = await data_source.list_releases(per_page=5)
        print_result("List Releases", releases_resp)

        # 11. List Departments
        print_section("Departments")
        departments_resp = await data_source.list_departments(per_page=5)
        print_result("List Departments", departments_resp)

        # 12. List Groups
        print_section("Groups")
        groups_resp = await data_source.list_groups(per_page=5)
        print_result("List Groups", groups_resp)

        # 13. List Service Catalog Items
        print_section("Service Catalog Items")
        catalog_resp = await data_source.list_service_catalog_items(per_page=5)
        print_result("List Service Catalog Items", catalog_resp)

    finally:
        print("\nClosing client connection...")
        inner_client = client.get_client()
        if hasattr(inner_client, "close"):
            await inner_client.close()

    print("\n" + "=" * 80)
    print("  All Freshservice API operations tested!")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
