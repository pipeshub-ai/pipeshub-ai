# ruff: noqa

"""
Ironclad API Usage Examples

This example demonstrates how to use the Ironclad DataSource to interact with
the Ironclad API (v1), covering:
- Authentication (OAuth2, API Key)
- Initializing the Client and DataSource
- Listing workflows, records, templates
- Managing webhooks
- Fetching users and groups

Prerequisites:
For OAuth2:
1. Create an Ironclad OAuth app in the Developer Portal
2. Set IRONCLAD_CLIENT_ID and IRONCLAD_CLIENT_SECRET environment variables
3. The OAuth flow will automatically open a browser for authorization

For API Key:
1. Generate an API key in your Ironclad account settings
2. Set IRONCLAD_API_KEY environment variable

OAuth Endpoints:
- Authorization: https://ironcladapp.com/oauth/authorize
- Token: https://ironcladapp.com/oauth/token
"""

import asyncio
import json
import os

from app.sources.client.ironclad.ironclad import (
    IroncladClient,
    IroncladOAuthConfig,
    IroncladResponse,
    IroncladTokenConfig,
)
from app.sources.external.ironclad.ironclad import IroncladDataSource
from app.sources.external.utils.oauth import perform_oauth_flow

# --- Configuration ---
# OAuth2 credentials (highest priority)
CLIENT_ID = os.getenv("IRONCLAD_CLIENT_ID")
CLIENT_SECRET = os.getenv("IRONCLAD_CLIENT_SECRET")

# API Key (second priority)
API_KEY = os.getenv("IRONCLAD_API_KEY")

# OAuth redirect URI
REDIRECT_URI = os.getenv("IRONCLAD_REDIRECT_URI", "http://localhost:8080/callback")


def print_section(title: str):
    print(f"\n{'-'*80}")
    print(f"| {title}")
    print(f"{'-'*80}")


def print_result(name: str, response: IroncladResponse, show_data: bool = True):
    if response.success:
        print(f"  {name}: Success")
        if show_data and response.data:
            data = response.data
            # Handle list-type responses
            for key in ("workflows", "records", "templates", "webhooks", "users", "groups"):
                if isinstance(data, dict) and key in data:
                    items = data[key]
                    print(f"   Found {len(items)} {key}.")
                    if items:
                        print(f"   Sample: {json.dumps(items[0], indent=2)[:400]}...")
                    return
            # Handle list responses (some endpoints return arrays directly)
            if isinstance(data, list):
                print(f"   Found {len(data)} items.")
                if data:
                    print(f"   Sample: {json.dumps(data[0], indent=2)[:400]}...")
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
    print_section("Initializing Ironclad Client")

    config = None

    # Priority 1: OAuth2
    if CLIENT_ID and CLIENT_SECRET:
        print("  Using OAuth2 authentication")
        try:
            print("Starting OAuth flow...")
            token_response = perform_oauth_flow(
                client_id=CLIENT_ID,
                client_secret=CLIENT_SECRET,
                auth_endpoint="https://ironcladapp.com/oauth/authorize",
                token_endpoint="https://ironcladapp.com/oauth/token",
                redirect_uri=REDIRECT_URI,
                scopes=[],
                scope_delimiter=" ",
                auth_method="body",
            )

            access_token = token_response.get("access_token")
            if not access_token:
                raise Exception("No access_token found in OAuth response")

            config = IroncladOAuthConfig(
                access_token=access_token,
                client_id=CLIENT_ID,
                client_secret=CLIENT_SECRET,
            )
            print("  OAuth authentication successful")
        except Exception as e:
            print(f"  OAuth flow failed: {e}")
            print("  Falling back to other authentication methods...")

    # Priority 2: API Key
    if config is None and API_KEY:
        print("  Using API Key authentication")
        config = IroncladTokenConfig(token=API_KEY)

    if config is None:
        print("  No valid authentication method found.")
        print("   Please set one of the following:")
        print("   - IRONCLAD_CLIENT_ID and IRONCLAD_CLIENT_SECRET (for OAuth2)")
        print("   - IRONCLAD_API_KEY (for API Key)")
        return

    client = IroncladClient.build_with_config(config)
    data_source = IroncladDataSource(client)
    print("Client initialized successfully.")

    try:
        # 2. List Templates
        print_section("Templates")
        templates_resp = await data_source.list_templates()
        print_result("List Templates", templates_resp)

        template_id = None
        if templates_resp.success and templates_resp.data:
            data = templates_resp.data
            templates = data if isinstance(data, list) else data.get("list", []) if isinstance(data, dict) else []
            if templates:
                template_id = str(templates[0].get("id")) if isinstance(templates[0], dict) else None
                if template_id:
                    print(f"   Using Template ID: {template_id}")

        # 3. Get Specific Template
        if template_id:
            print_section(f"Template Details: {template_id}")
            template_resp = await data_source.get_template(template_id=template_id)
            print_result("Get Template", template_resp)

        # 4. List Workflows
        print_section("Workflows")
        workflows_resp = await data_source.list_workflows(page=0, page_size=10)
        print_result("List Workflows", workflows_resp)

        workflow_id = None
        if workflows_resp.success and workflows_resp.data:
            data = workflows_resp.data
            workflows = data if isinstance(data, list) else data.get("list", []) if isinstance(data, dict) else []
            if workflows:
                workflow_id = str(workflows[0].get("id")) if isinstance(workflows[0], dict) else None
                if workflow_id:
                    print(f"   Using Workflow ID: {workflow_id}")

        # 5. Get Specific Workflow
        if workflow_id:
            print_section(f"Workflow Details: {workflow_id}")
            workflow_resp = await data_source.get_workflow(workflow_id=workflow_id)
            print_result("Get Workflow", workflow_resp)

            # 6. List Workflow Approvals
            print_section("Workflow Approvals")
            approvals_resp = await data_source.list_workflow_approvals(workflow_id=workflow_id)
            print_result("List Approvals", approvals_resp)

        # 7. List Records
        print_section("Records")
        records_resp = await data_source.list_records(page=0, page_size=10)
        print_result("List Records", records_resp)

        record_id = None
        if records_resp.success and records_resp.data:
            data = records_resp.data
            records = data if isinstance(data, list) else data.get("list", []) if isinstance(data, dict) else []
            if records:
                record_id = str(records[0].get("id")) if isinstance(records[0], dict) else None
                if record_id:
                    print(f"   Using Record ID: {record_id}")

        # 8. Get Specific Record
        if record_id:
            print_section(f"Record Details: {record_id}")
            record_resp = await data_source.get_record(record_id=record_id)
            print_result("Get Record", record_resp)

        # 9. List Webhooks
        print_section("Webhooks")
        webhooks_resp = await data_source.list_webhooks()
        print_result("List Webhooks", webhooks_resp)

        # 10. List Users
        print_section("Users")
        users_resp = await data_source.list_users(page=0, page_size=10)
        print_result("List Users", users_resp)

        # 11. List Groups
        print_section("Groups")
        groups_resp = await data_source.list_groups()
        print_result("List Groups", groups_resp)

    finally:
        # Cleanup: Close the HTTP client session
        print("\nClosing client connection...")
        inner_client = client.get_client()
        if hasattr(inner_client, "close"):
            await inner_client.close()

    print("\n" + "=" * 80)
    print("  All Ironclad API operations tested!")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
