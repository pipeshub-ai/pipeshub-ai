# ruff: noqa

"""
DarwinBox API Usage Examples

This example demonstrates how to use the DarwinBox DataSource to interact with
the DarwinBox API, covering:
- Authentication (Basic Auth, OAuth2)
- Initializing the Client and DataSource
- Fetching Employees
- Listing Departments, Designations, Locations
- Attendance and Leave management
- Payroll and Recruitment

Prerequisites:
For Basic Auth:
1. Set DARWINBOX_DOMAIN to your DarwinBox domain (e.g. "yourcompany")
2. Set DARWINBOX_API_KEY and DARWINBOX_API_SECRET

For OAuth2:
1. Set DARWINBOX_DOMAIN, DARWINBOX_CLIENT_ID, DARWINBOX_CLIENT_SECRET
2. Complete OAuth flow to get access_token
3. Set DARWINBOX_ACCESS_TOKEN
"""

import asyncio
import json
import os

from app.sources.client.darwinbox.darwinbox import (
    DarwinBoxBasicAuthConfig,
    DarwinBoxClient,
    DarwinBoxOAuthConfig,
    DarwinBoxResponse,
)
from app.sources.external.darwinbox.darwinbox import DarwinBoxDataSource

# --- Configuration ---
DOMAIN = os.getenv("DARWINBOX_DOMAIN", "")
API_KEY = os.getenv("DARWINBOX_API_KEY", "")
API_SECRET = os.getenv("DARWINBOX_API_SECRET", "")
ACCESS_TOKEN = os.getenv("DARWINBOX_ACCESS_TOKEN", "")


def print_section(title: str):
    print(f"\n{'-'*80}")
    print(f"| {title}")
    print(f"{'-'*80}")


def print_result(name: str, response: DarwinBoxResponse, show_data: bool = True):
    if response.success:
        print(f"  {name}: Success")
        if show_data and response.data:
            data = response.data
            if isinstance(data, list):
                print(f"   Found {len(data)} items.")
                if data:
                    print(f"   Sample: {json.dumps(data[0], indent=2)[:400]}...")
            elif isinstance(data, dict):
                print(f"   Data: {json.dumps(data, indent=2)[:500]}...")
    else:
        print(f"  {name}: Failed")
        print(f"   Error: {response.error}")
        if response.message:
            print(f"   Message: {response.message}")


async def main() -> None:
    # 1. Initialize Client
    print_section("Initializing DarwinBox Client")

    config = None

    if not DOMAIN:
        print("  DARWINBOX_DOMAIN is required.")
        return

    # Priority 1: OAuth2
    if ACCESS_TOKEN:
        print("  Using OAuth2 authentication")
        config = DarwinBoxOAuthConfig(
            access_token=ACCESS_TOKEN,
            domain=DOMAIN,
        )

    # Priority 2: Basic Auth
    elif API_KEY and API_SECRET:
        print("  Using Basic Auth authentication")
        config = DarwinBoxBasicAuthConfig(
            api_key=API_KEY,
            api_secret=API_SECRET,
            domain=DOMAIN,
        )

    if config is None:
        print("  No valid authentication found.")
        print("   Please set one of:")
        print("   - DARWINBOX_ACCESS_TOKEN (for OAuth2)")
        print("   - DARWINBOX_API_KEY and DARWINBOX_API_SECRET (for Basic Auth)")
        return

    client = DarwinBoxClient.build_with_config(config)
    data_source = DarwinBoxDataSource(client)
    print(f"Client initialized for domain: {DOMAIN}")

    try:
        # 2. Get Employees
        print_section("Employees")
        employees_resp = await data_source.get_employees(per_page=5)
        print_result("Get Employees", employees_resp)

        employee_id = None
        if employees_resp.success and isinstance(employees_resp.data, list) and employees_resp.data:
            employee_id = str(employees_resp.data[0].get("id", ""))
            print(f"   Using Employee ID: {employee_id}")

        # 3. Get Specific Employee
        if employee_id:
            print_section("Employee Details")
            emp_resp = await data_source.get_employee(employee_id)
            print_result("Get Employee", emp_resp)

        # 4. Get Departments
        print_section("Departments")
        depts_resp = await data_source.get_departments()
        print_result("Get Departments", depts_resp)

        # 5. Get Designations
        print_section("Designations")
        desig_resp = await data_source.get_designations()
        print_result("Get Designations", desig_resp)

        # 6. Get Locations
        print_section("Locations")
        loc_resp = await data_source.get_locations()
        print_result("Get Locations", loc_resp)

        # 7. Get Attendance
        print_section("Attendance")
        att_resp = await data_source.get_attendance(per_page=5)
        print_result("Get Attendance", att_resp)

        # 8. Get Leave Requests
        print_section("Leave Requests")
        leave_resp = await data_source.get_leave_requests(per_page=5)
        print_result("Get Leave Requests", leave_resp)

        # 9. Get Recruitment Openings
        print_section("Recruitment Openings")
        openings_resp = await data_source.get_recruitment_openings(per_page=5)
        print_result("Get Recruitment Openings", openings_resp)

        # 10. Get Recruitment Applications
        print_section("Recruitment Applications")
        apps_resp = await data_source.get_recruitment_applications(per_page=5)
        print_result("Get Recruitment Applications", apps_resp)

    finally:
        # Cleanup
        print("\nClosing client connection...")
        inner_client = client.get_client()
        if hasattr(inner_client, "close"):
            await inner_client.close()

    print("\n" + "=" * 80)
    print("  All DarwinBox API operations tested!")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
