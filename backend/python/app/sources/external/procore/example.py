# ruff: noqa

"""
Procore API Usage Examples

This example demonstrates how to use the Procore DataSource to interact with
the Procore API (v1.0), covering:
- Authentication (OAuth2)
- Initializing the Client and DataSource
- Fetching current user and companies
- Listing projects
- Browsing RFIs, submittals, documents, drawings
- Checking daily logs, incidents
- Viewing users, tasks, budgets, change orders

Prerequisites:
For OAuth2 (required):
1. Create a Procore OAuth app at the Developer Portal
2. Set PROCORE_CLIENT_ID and PROCORE_CLIENT_SECRET environment variables
3. The OAuth flow will automatically open a browser for authorization

OAuth Endpoints:
- Authorization: https://login.procore.com/oauth/authorize
- Token: https://login.procore.com/oauth/token
"""

import asyncio
import json
import os

from app.sources.client.procore.procore import (
    ProcoreClient,
    ProcoreOAuthConfig,
    ProcoreResponse,
    ProcoreTokenConfig,
)
from app.sources.external.procore.procore import ProcoreDataSource
from app.sources.external.utils.oauth import perform_oauth_flow

# --- Configuration ---
# OAuth2 credentials (primary)
CLIENT_ID = os.getenv("PROCORE_CLIENT_ID")
CLIENT_SECRET = os.getenv("PROCORE_CLIENT_SECRET")

# Bearer token (fallback, for pre-obtained tokens)
BEARER_TOKEN = os.getenv("PROCORE_BEARER_TOKEN")

# OAuth redirect URI
REDIRECT_URI = os.getenv("PROCORE_REDIRECT_URI", "http://localhost:8080/callback")


def print_section(title: str):
    print(f"\n{'-'*80}")
    print(f"| {title}")
    print(f"{'-'*80}")


def print_result(name: str, response: ProcoreResponse, show_data: bool = True):
    if response.success:
        print(f"  {name}: Success")
        if show_data and response.data:
            data = response.data
            # Handle list responses (Procore often returns arrays)
            if isinstance(data, list):
                print(f"   Found {len(data)} items.")
                if data:
                    print(f"   Sample: {json.dumps(data[0], indent=2)[:400]}...")
                return
            # Handle dict responses with common list keys
            for key in ("companies", "projects", "rfis", "submittals", "documents",
                        "drawings", "daily_logs", "incidents", "users", "tasks",
                        "budgets", "change_orders"):
                if isinstance(data, dict) and key in data:
                    items = data[key]
                    print(f"   Found {len(items)} {key}.")
                    if items:
                        print(f"   Sample: {json.dumps(items[0], indent=2)[:400]}...")
                    return
            # Generic response
            print(f"   Data: {json.dumps(data, indent=2)[:500]}...")
    else:
        print(f"  {name}: Failed")
        print(f"   Error: {response.error}")
        if response.message:
            print(f"   Message: {response.message}")


async def main() -> None:
    # 1. Initialize Client
    print_section("Initializing Procore Client")

    config = None

    # Priority 1: OAuth2
    if CLIENT_ID and CLIENT_SECRET:
        print("  Using OAuth2 authentication")
        try:
            print("Starting OAuth flow...")
            token_response = perform_oauth_flow(
                client_id=CLIENT_ID,
                client_secret=CLIENT_SECRET,
                auth_endpoint="https://login.procore.com/oauth/authorize",
                token_endpoint="https://login.procore.com/oauth/token",
                redirect_uri=REDIRECT_URI,
                scopes=[],
                scope_delimiter=" ",
                auth_method="body",
            )

            access_token = token_response.get("access_token")
            if not access_token:
                raise Exception("No access_token found in OAuth response")

            config = ProcoreOAuthConfig(
                access_token=access_token,
                client_id=CLIENT_ID,
                client_secret=CLIENT_SECRET,
            )
            print("  OAuth authentication successful")
        except Exception as e:
            print(f"  OAuth flow failed: {e}")
            print("  Falling back to other authentication methods...")

    # Priority 2: Bearer Token
    if config is None and BEARER_TOKEN:
        print("  Using Bearer Token authentication")
        config = ProcoreTokenConfig(token=BEARER_TOKEN)

    if config is None:
        print("  No valid authentication method found.")
        print("   Please set one of the following:")
        print("   - PROCORE_CLIENT_ID and PROCORE_CLIENT_SECRET (for OAuth2)")
        print("   - PROCORE_BEARER_TOKEN (for pre-obtained Bearer token)")
        return

    client = ProcoreClient.build_with_config(config)
    data_source = ProcoreDataSource(client)
    print("Client initialized successfully.")

    try:
        # 2. Get Current User
        print_section("Current User")
        me_resp = await data_source.get_me()
        print_result("Get Me", me_resp)

        # 3. List Companies
        print_section("Companies")
        companies_resp = await data_source.list_companies()
        print_result("List Companies", companies_resp)

        company_id = None
        if companies_resp.success and companies_resp.data:
            data = companies_resp.data
            companies = data if isinstance(data, list) else []
            if companies:
                company_id = str(companies[0].get("id")) if isinstance(companies[0], dict) else None
                if company_id:
                    print(f"   Using Company ID: {company_id}")

        if not company_id:
            print("   No company found. Skipping further operations.")
            return

        # 4. Get Specific Company
        print_section(f"Company Details: {company_id}")
        company_resp = await data_source.get_company(company_id=company_id)
        print_result("Get Company", company_resp)

        # 5. List Projects
        print_section("Projects")
        projects_resp = await data_source.list_projects(company_id=company_id, page=1, per_page=10)
        print_result("List Projects", projects_resp)

        project_id = None
        if projects_resp.success and projects_resp.data:
            data = projects_resp.data
            projects = data if isinstance(data, list) else []
            if projects:
                project_id = str(projects[0].get("id")) if isinstance(projects[0], dict) else None
                if project_id:
                    print(f"   Using Project ID: {project_id}")

        if not project_id:
            print("   No project found. Skipping project-level operations.")
            return

        # 6. Get Specific Project
        print_section(f"Project Details: {project_id}")
        project_resp = await data_source.get_project(project_id=project_id)
        print_result("Get Project", project_resp)

        # 7. List RFIs
        print_section("RFIs")
        rfis_resp = await data_source.list_rfis(project_id=project_id, page=1, per_page=10)
        print_result("List RFIs", rfis_resp)

        # 8. List Submittals
        print_section("Submittals")
        submittals_resp = await data_source.list_submittals(project_id=project_id, page=1, per_page=10)
        print_result("List Submittals", submittals_resp)

        # 9. List Documents
        print_section("Documents")
        documents_resp = await data_source.list_documents(project_id=project_id, page=1, per_page=10)
        print_result("List Documents", documents_resp)

        # 10. List Drawings
        print_section("Drawings")
        drawings_resp = await data_source.list_drawings(project_id=project_id, page=1, per_page=10)
        print_result("List Drawings", drawings_resp)

        # 11. List Daily Logs
        print_section("Daily Logs")
        daily_logs_resp = await data_source.list_daily_logs(project_id=project_id, page=1, per_page=10)
        print_result("List Daily Logs", daily_logs_resp)

        # 12. List Incidents
        print_section("Incidents")
        incidents_resp = await data_source.list_incidents(project_id=project_id, page=1, per_page=10)
        print_result("List Incidents", incidents_resp)

        # 13. List Company Users
        print_section("Company Users")
        company_users_resp = await data_source.list_company_users(company_id=company_id, page=1, per_page=10)
        print_result("List Company Users", company_users_resp)

        # 14. List Project Users
        print_section("Project Users")
        project_users_resp = await data_source.list_project_users(project_id=project_id, page=1, per_page=10)
        print_result("List Project Users", project_users_resp)

        # 15. List Tasks
        print_section("Tasks")
        tasks_resp = await data_source.list_tasks(project_id=project_id, page=1, per_page=10)
        print_result("List Tasks", tasks_resp)

        # 16. List Budgets
        print_section("Budgets")
        budgets_resp = await data_source.list_budgets(project_id=project_id)
        print_result("List Budgets", budgets_resp)

        # 17. List Change Orders
        print_section("Change Orders")
        change_orders_resp = await data_source.list_change_orders(project_id=project_id, page=1, per_page=10)
        print_result("List Change Orders", change_orders_resp)

    finally:
        # Cleanup: Close the HTTP client session
        print("\nClosing client connection...")
        inner_client = client.get_client()
        if hasattr(inner_client, "close"):
            await inner_client.close()

    print("\n" + "=" * 80)
    print("  All Procore API operations tested!")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
