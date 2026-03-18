# ruff: noqa

"""
BambooHR API Usage Examples

This example demonstrates how to use the BambooHR DataSource to interact with
the BambooHR API, covering:
- Authentication (API Key via HTTP Basic Auth)
- Initializing the Client and DataSource
- Getting the Employee Directory
- Fetching Metadata Fields
- Getting Time Off Policies
- Getting Job Summaries

Prerequisites:
1. Create a BambooHR API key at BambooHR > Settings > API Keys > Add New Key
2. Set BAMBOOHR_API_KEY environment variable
3. Set BAMBOOHR_COMPANY_DOMAIN environment variable (your company subdomain)
"""

import asyncio
import json
import os

from app.sources.client.bamboohr.bamboohr import (
    BambooHRApiKeyConfig,
    BambooHRClient,
    BambooHRResponse,
)
from app.sources.external.bamboohr.bamboohr import BambooHRDataSource

# --- Configuration ---
API_KEY = os.getenv("BAMBOOHR_API_KEY")
COMPANY_DOMAIN = os.getenv("BAMBOOHR_COMPANY_DOMAIN")


def print_section(title: str):
    print(f"\n{'-'*80}")
    print(f"| {title}")
    print(f"{'-'*80}")


def print_result(name: str, response: BambooHRResponse, show_data: bool = True):
    if response.success:
        print(f"  {name}: Success")
        if show_data and response.data:
            data = response.data
            # Handle list-type responses (employees, fields, policies, etc.)
            for key in ("employees", "fields", "tables", "lists", "users",
                        "requests", "policies", "applications", "jobSummaries"):
                if isinstance(data, dict) and key in data:
                    items = data[key]
                    print(f"   Found {len(items)} {key}.")
                    if items:
                        print(f"   Sample: {json.dumps(items[0], indent=2)[:400]}...")
                    return
            # Generic response
            if isinstance(data, list):
                print(f"   Found {len(data)} items.")
                if data:
                    print(f"   Sample: {json.dumps(data[0], indent=2)[:400]}...")
            else:
                print(f"   Data: {json.dumps(data, indent=2)[:500]}...")
    else:
        print(f"  {name}: Failed")
        print(f"   Error: {response.error}")
        if response.message:
            print(f"   Message: {response.message}")


async def main() -> None:
    # 1. Initialize Client
    print_section("Initializing BambooHR Client")

    if not API_KEY:
        print("  No API key found.")
        print("   Please set BAMBOOHR_API_KEY environment variable")
        return

    if not COMPANY_DOMAIN:
        print("  No company domain found.")
        print("   Please set BAMBOOHR_COMPANY_DOMAIN environment variable")
        return

    print("  Using API Key authentication")
    config = BambooHRApiKeyConfig(
        company_domain=COMPANY_DOMAIN,
        api_key=API_KEY,
    )

    client = BambooHRClient.build_with_config(config)
    data_source = BambooHRDataSource(client)
    print("Client initialized successfully.")

    try:
        # 2. Get Employee Directory
        print_section("Employee Directory")
        directory_resp = await data_source.get_employee_directory()
        print_result("Get Employee Directory", directory_resp)

        # 3. Get Metadata Fields
        print_section("Metadata Fields")
        fields_resp = await data_source.get_metadata_fields()
        print_result("Get Metadata Fields", fields_resp)

        # 4. Get Time Off Policies
        print_section("Time Off Policies")
        policies_resp = await data_source.get_time_off_policies()
        print_result("Get Time Off Policies", policies_resp)

        # 5. Get Job Summaries
        print_section("Job Summaries")
        jobs_resp = await data_source.get_job_summaries()
        print_result("Get Job Summaries", jobs_resp)

    finally:
        # Cleanup: Close the HTTP client session
        print("\nClosing client connection...")
        inner_client = client.get_client()
        if hasattr(inner_client, "close"):
            await inner_client.close()

    print("\n" + "=" * 80)
    print("  All BambooHR API operations tested!")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
