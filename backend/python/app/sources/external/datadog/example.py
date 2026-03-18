# ruff: noqa
"""
Datadog API Usage Examples (SDK-based)

This example demonstrates how to use the Datadog DataSource (backed by the
official datadog-api-client SDK) covering:
- Authentication (API Key + Application Key)
- Initializing the Client and DataSource
- Listing Dashboards
- Listing Monitors
- Listing Users
- Listing Hosts
- Listing Active Metrics

Prerequisites:
1. Create API and Application keys at https://app.datadoghq.com/organization-settings/api-keys
2. Set DD_API_KEY and DD_APP_KEY environment variables
3. Optionally set DD_SITE for non-US1 sites (e.g., datadoghq.eu, us3.datadoghq.com)
"""

import json
import os

from app.sources.client.datadog.datadog import (
    DatadogApiKeyConfig,
    DatadogClient,
    DatadogResponse,
)
from app.sources.external.datadog.datadog import DatadogDataSource

# --- Configuration ---
API_KEY = os.getenv("DD_API_KEY")
APP_KEY = os.getenv("DD_APP_KEY")
SITE = os.getenv("DD_SITE", "datadoghq.com")


def print_section(title: str) -> None:
    print(f"\n{'-' * 80}")
    print(f"| {title}")
    print(f"{'-' * 80}")


def print_result(name: str, response: DatadogResponse, show_data: bool = True) -> None:
    if response.success:
        print(f"  {name}: Success")
        if show_data and response.data:
            data = response.data
            # Handle dict responses with common list keys
            for key in (
                "dashboards",
                "monitors",
                "data",
                "host_list",
                "tests",
                "series",
            ):
                if isinstance(data, dict) and key in data:
                    items = data[key]
                    if isinstance(items, list):
                        print(f"   Found {len(items)} {key}.")
                        if items:
                            print(
                                f"   Sample: {json.dumps(items[0], indent=2, default=str)[:400]}..."
                            )
                    return
            # Generic response
            print(f"   Data: {json.dumps(data, indent=2, default=str)[:500]}...")
    else:
        print(f"  {name}: Failed")
        print(f"   Error: {response.error}")
        if response.message:
            print(f"   Message: {response.message}")


def main() -> None:
    # 1. Initialize Client
    print_section("Initializing Datadog Client (SDK)")

    if not API_KEY or not APP_KEY:
        print("  No valid authentication found.")
        print("   Please set the following environment variables:")
        print("   - DD_API_KEY (Datadog API key)")
        print("   - DD_APP_KEY (Datadog Application key)")
        print("   - DD_SITE (optional, default: datadoghq.com)")
        return

    print(f"  Using API Key authentication (site: {SITE})")
    config = DatadogApiKeyConfig(
        api_key=API_KEY,
        app_key=APP_KEY,
        site=SITE,
    )

    client = DatadogClient.build_with_config(config)
    data_source = DatadogDataSource(client)
    print("  Client initialized successfully.")

    # 2. List Dashboards
    print_section("Dashboards")
    dashboards_resp = data_source.list_dashboards()
    print_result("List Dashboards", dashboards_resp)

    # 3. List Monitors
    print_section("Monitors")
    monitors_resp = data_source.list_monitors(page_size=5)
    print_result("List Monitors", monitors_resp)

    # 4. List Users
    print_section("Users")
    users_resp = data_source.list_users(page_size=10)
    print_result("List Users", users_resp)

    # 5. List Hosts
    print_section("Hosts")
    hosts_resp = data_source.list_hosts(count=10)
    print_result("List Hosts", hosts_resp)

    # 6. List Active Metrics
    print_section("Active Metrics")
    metrics_resp = data_source.list_active_metrics(page_size=10)
    print_result("List Active Metrics", metrics_resp)

    print("\n" + "=" * 80)
    print("  All Datadog API operations tested!")
    print("=" * 80)


if __name__ == "__main__":
    main()
