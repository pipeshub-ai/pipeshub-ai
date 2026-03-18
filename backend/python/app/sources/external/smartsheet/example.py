# ruff: noqa

"""
Smartsheet SDK Usage Examples

This example demonstrates how to use the Smartsheet DataSource (backed by the
official smartsheet-python-sdk) to interact with the Smartsheet API, covering:
- Authentication (OAuth2 or API Access Token)
- Initializing the Client and DataSource
- Getting Current User
- Listing Sheets
- Getting Home
- Searching
- Listing Workspaces

Prerequisites:
For OAuth2:
1. Create a Smartsheet Developer App at https://app.smartsheet.com/b/home
2. Set SMARTSHEET_CLIENT_ID and SMARTSHEET_CLIENT_SECRET environment variables
3. The OAuth flow will automatically open a browser for authorization

For API Access Token:
1. Log in to Smartsheet
2. Go to Account > Apps & Integrations > API Access > Generate new access token
3. Set SMARTSHEET_ACCESS_TOKEN environment variable
"""

import json
import os

from app.sources.client.smartsheet.smartsheet import (
    SmartsheetClient,
    SmartsheetOAuthConfig,
    SmartsheetResponse,
    SmartsheetTokenConfig,
)
from app.sources.external.smartsheet.smartsheet import SmartsheetDataSource
from app.sources.external.utils.oauth import perform_oauth_flow

# --- Configuration ---
# OAuth2 credentials (highest priority)
CLIENT_ID = os.getenv("SMARTSHEET_CLIENT_ID")
CLIENT_SECRET = os.getenv("SMARTSHEET_CLIENT_SECRET")

# API Access Token (second priority)
ACCESS_TOKEN = os.getenv("SMARTSHEET_ACCESS_TOKEN")

# OAuth redirect URI
REDIRECT_URI = os.getenv("SMARTSHEET_REDIRECT_URI", "http://localhost:8080/callback")


def print_section(title: str):
    print(f"\n{'-'*80}")
    print(f"| {title}")
    print(f"{'-'*80}")


def print_result(name: str, response: SmartsheetResponse, show_data: bool = True):
    if response.success:
        print(f"  {name}: Success")
        if show_data and response.data:
            data = response.data
            # SDK returns model objects; try to convert to dict for display
            if hasattr(data, "to_dict"):
                data = data.to_dict()
            if isinstance(data, dict):
                for key in ("data", "sheets", "workspaces", "folders", "reports",
                            "results", "users", "columns", "rows", "discussions",
                            "attachments"):
                    if key in data:
                        items = data[key]
                        if isinstance(items, list):
                            print(f"   Found {len(items)} {key}.")
                            if items:
                                item = items[0]
                                if hasattr(item, "to_dict"):
                                    item = item.to_dict()
                                print(f"   Sample: {json.dumps(item, indent=2, default=str)[:400]}...")
                        else:
                            print(f"   {key}: {json.dumps(items, indent=2, default=str)[:400]}...")
                        return
            print(f"   Data: {str(data)[:500]}...")
    else:
        print(f"  {name}: Failed")
        print(f"   Error: {response.error}")
        if response.message:
            print(f"   Message: {response.message}")


def main() -> None:
    # 1. Initialize Client
    print_section("Initializing Smartsheet Client")

    config = None

    # Priority 1: OAuth2
    if CLIENT_ID and CLIENT_SECRET:
        print("  Using OAuth2 authentication")
        try:
            print("Starting OAuth flow...")
            # Smartsheet OAuth authorization URL: https://app.smartsheet.com/b/authorize
            # Smartsheet token endpoint: https://api.smartsheet.com/2.0/token
            token_response = perform_oauth_flow(
                client_id=CLIENT_ID,
                client_secret=CLIENT_SECRET,
                auth_endpoint="https://app.smartsheet.com/b/authorize",
                token_endpoint="https://api.smartsheet.com/2.0/token",
                redirect_uri=REDIRECT_URI,
                scopes=["READ_SHEETS", "READ_USERS"],
                scope_delimiter=" ",
                auth_method="header",
            )

            access_token = token_response.get("access_token")
            if not access_token:
                raise Exception("No access_token found in OAuth response")

            config = SmartsheetOAuthConfig(
                access_token=access_token,
                client_id=CLIENT_ID,
                client_secret=CLIENT_SECRET,
            )
            print("  OAuth authentication successful")
        except Exception as e:
            print(f"  OAuth flow failed: {e}")
            print("  Falling back to other authentication methods...")

    # Priority 2: API Access Token
    if config is None and ACCESS_TOKEN:
        print("  Using API Access Token authentication")
        config = SmartsheetTokenConfig(
            token=ACCESS_TOKEN,
        )

    if config is None:
        print("  No valid authentication method found.")
        print("   Please set one of the following:")
        print("   - SMARTSHEET_CLIENT_ID and SMARTSHEET_CLIENT_SECRET (for OAuth2)")
        print("   - SMARTSHEET_ACCESS_TOKEN (for API Access Token)")
        return

    client = SmartsheetClient.build_with_config(config)
    data_source = SmartsheetDataSource(client)
    print("Client initialized successfully.")

    # 2. Get Current User
    print_section("Current User")
    user_resp = data_source.get_current_user()
    print_result("Get Current User", user_resp)

    # 3. List Sheets
    print_section("Sheets")
    sheets_resp = data_source.list_sheets(page_size=10)
    print_result("List Sheets", sheets_resp)

    # 4. Get Home
    print_section("Home")
    home_resp = data_source.get_home()
    print_result("Get Home", home_resp)

    # 5. Search
    print_section("Search")
    search_resp = data_source.search(query="test")
    print_result("Search", search_resp)

    # 6. List Workspaces
    print_section("Workspaces")
    workspaces_resp = data_source.list_workspaces()
    print_result("List Workspaces", workspaces_resp)

    # 7. List Reports
    print_section("Reports")
    reports_resp = data_source.list_reports()
    print_result("List Reports", reports_resp)

    # 8. List Folders
    print_section("Folders")
    folders_resp = data_source.list_folders()
    print_result("List Folders", folders_resp)

    # 9. Get a specific sheet if available
    if sheets_resp.success and sheets_resp.data:
        sheets_data = sheets_resp.data
        sheets_list = []
        if hasattr(sheets_data, "data"):
            sheets_list = sheets_data.data or []
        elif hasattr(sheets_data, "to_dict"):
            d = sheets_data.to_dict()
            if isinstance(d, dict):
                sheets_list = d.get("data", [])

        if sheets_list and isinstance(sheets_list, list) and len(sheets_list) > 0:
            first_sheet = sheets_list[0]
            sheet_id = getattr(first_sheet, "id", None)
            if sheet_id is None and isinstance(first_sheet, dict):
                sheet_id = first_sheet.get("id")
            sheet_name = getattr(first_sheet, "name", "N/A")
            if sheet_name == "N/A" and isinstance(first_sheet, dict):
                sheet_name = first_sheet.get("name", "N/A")

            if sheet_id:
                print_section(f"Sheet Details: {sheet_name}")
                sheet_resp = data_source.get_sheet(sheet_id=int(sheet_id))
                print_result("Get Sheet", sheet_resp)

                # 10. List Columns in Sheet
                print_section("Sheet Columns")
                columns_resp = data_source.list_columns(sheet_id=int(sheet_id))
                print_result("List Columns", columns_resp)

    print("\n" + "=" * 80)
    print("  All Smartsheet API operations tested!")
    print("=" * 80)


if __name__ == "__main__":
    main()
