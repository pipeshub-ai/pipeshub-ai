# ruff: noqa

"""
Tableau SDK Usage Examples

This example demonstrates how to use the Tableau DataSource with the
official tableauserverclient SDK:
- Authentication via Personal Access Token (PAT)
- Initializing the Client and DataSource
- Listing Workbooks, Views, Data Sources
- Listing Projects, Users, Groups

Prerequisites:
1. Create a Personal Access Token in Tableau:
   - Go to My Account Settings > Personal Access Tokens
   - Create a new token and note the token name and secret
2. Set the following environment variables:
   - TABLEAU_SERVER_URL: Your Tableau Server/Cloud URL (e.g., "https://10ax.online.tableau.com")
   - TABLEAU_TOKEN_NAME: Personal Access Token name
   - TABLEAU_TOKEN_SECRET: Personal Access Token secret
   - TABLEAU_SITE_ID: Site content URL (empty string for default site)
"""

import json
import os

from app.sources.client.tableau.tableau import (
    TableauClient,
    TableauPATConfig,
    TableauResponse,
)
from app.sources.external.tableau.tableau import TableauDataSource

# --- Configuration ---
SERVER_URL = os.getenv("TABLEAU_SERVER_URL", "")
TOKEN_NAME = os.getenv("TABLEAU_TOKEN_NAME", "")
TOKEN_SECRET = os.getenv("TABLEAU_TOKEN_SECRET", "")
SITE_ID = os.getenv("TABLEAU_SITE_ID", "")


def print_section(title: str):
    print(f"\n{'-'*80}")
    print(f"| {title}")
    print(f"{'-'*80}")


def print_result(name: str, response: TableauResponse, show_data: bool = True):
    if response.success:
        print(f"  {name}: Success")
        if show_data and response.data:
            data = response.data
            if isinstance(data, list):
                print(f"   Found {len(data)} items.")
                if data:
                    print(f"   Sample: {json.dumps(data[0], indent=2, default=str)[:400]}...")
            elif isinstance(data, dict):
                print(f"   Data: {json.dumps(data, indent=2, default=str)[:500]}...")
            else:
                print(f"   Data: {data}")
    else:
        print(f"  {name}: Failed")
        print(f"   Error: {response.error}")
        if response.message:
            print(f"   Message: {response.message}")


def main() -> None:
    # 1. Validate Configuration
    print_section("Initializing Tableau Client")

    if not SERVER_URL:
        print("  TABLEAU_SERVER_URL is required.")
        print("  Example: export TABLEAU_SERVER_URL='https://10ax.online.tableau.com'")
        return

    if not (TOKEN_NAME and TOKEN_SECRET):
        print("  TABLEAU_TOKEN_NAME and TABLEAU_TOKEN_SECRET are required.")
        print("  Create a Personal Access Token in Tableau Settings.")
        return

    print(f"  Server: {SERVER_URL}")
    print(f"  Token Name: {TOKEN_NAME}")
    print(f"  Site ID: '{SITE_ID}'")

    # 2. Build Client with PAT Config
    config = TableauPATConfig(
        server_url=SERVER_URL,
        token_name=TOKEN_NAME,
        token_secret=TOKEN_SECRET,
        site_id=SITE_ID,
    )

    client = TableauClient.build_with_config(config)
    data_source = TableauDataSource(client)
    print("  Client initialized and authenticated via PAT.")

    try:
        # 3. List Workbooks
        print_section("Workbooks")
        workbooks_resp = data_source.list_workbooks()
        print_result("List Workbooks", workbooks_resp)

        # 4. List Views
        print_section("Views")
        views_resp = data_source.list_views()
        print_result("List Views", views_resp)

        # 5. List Data Sources
        print_section("Data Sources")
        datasources_resp = data_source.list_datasources()
        print_result("List Data Sources", datasources_resp)

        # 6. List Projects
        print_section("Projects")
        projects_resp = data_source.list_projects()
        print_result("List Projects", projects_resp)

        # 7. List Users
        print_section("Users")
        users_resp = data_source.list_users()
        print_result("List Users", users_resp)

        # 8. List Groups
        print_section("Groups")
        groups_resp = data_source.list_groups()
        print_result("List Groups", groups_resp)

        # 9. List Flows
        print_section("Flows")
        flows_resp = data_source.list_flows()
        print_result("List Flows", flows_resp)

    finally:
        # Cleanup: Sign out
        print("\nSigning out...")
        data_source.sign_out()

    print("\n" + "=" * 80)
    print("  All Tableau SDK operations tested!")
    print("=" * 80)


if __name__ == "__main__":
    main()
