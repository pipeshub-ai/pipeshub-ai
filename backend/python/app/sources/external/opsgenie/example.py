# ruff: noqa

"""
Opsgenie API Usage Examples (SDK-backed)

This example demonstrates how to use the Opsgenie DataSource backed by the
official ``opsgenie-sdk`` Python package, covering:
- Authentication (API Key via GenieKey header)
- Initialising the Client and DataSource
- Listing alerts, incidents, schedules
- Teams, users, services, heartbeats

Prerequisites:
1. Set OPSGENIE_API_KEY environment variable with your API integration key

You can obtain an API key from:
Opsgenie > Settings > Integrations > API Integration
"""

import os

from app.sources.client.opsgenie.opsgenie import (
    OpsgenieApiKeyConfig,
    OpsgenieClient,
    OpsgenieResponse,
)
from app.sources.external.opsgenie.opsgenie import OpsgenieDataSource

# --- Configuration ---
API_KEY = os.getenv("OPSGENIE_API_KEY")


def print_section(title: str):
    print(f"\n{'-'*80}")
    print(f"| {title}")
    print(f"{'-'*80}")


def print_result(name: str, response: OpsgenieResponse):
    if response.success:
        print(f"  {name}: Success")
        if response.data:
            data = response.data
            print(f"   Data type: {type(data).__name__}")
    else:
        print(f"  {name}: Failed")
        print(f"   Error: {response.error}")
        if response.message:
            print(f"   Message: {response.message}")


def main() -> None:
    if not API_KEY:
        print("OPSGENIE_API_KEY is not set")
        print("  Set it with your Opsgenie API integration key")
        return

    # 1. Initialise Client
    print_section("Initialising Opsgenie Client (SDK-backed)")
    client = OpsgenieClient.build_with_config(
        OpsgenieApiKeyConfig(api_key=API_KEY)
    )
    print(f"  Base URL: {client.get_base_url()}")

    data_source = OpsgenieDataSource(client)

    # 2. List Alerts
    print_section("Alerts")
    alerts_resp = data_source.list_alerts(limit=5)
    print_result("List Alerts", alerts_resp)

    # 3. List Incidents
    print_section("Incidents")
    incidents_resp = data_source.list_incidents(limit=5)
    print_result("List Incidents", incidents_resp)

    # 4. List Schedules
    print_section("Schedules")
    schedules_resp = data_source.list_schedules()
    print_result("List Schedules", schedules_resp)

    # 5. List Teams
    print_section("Teams")
    teams_resp = data_source.list_teams()
    print_result("List Teams", teams_resp)

    # 6. List Users
    print_section("Users")
    users_resp = data_source.list_users(limit=5)
    print_result("List Users", users_resp)

    # 7. List Services
    print_section("Services")
    services_resp = data_source.list_services(limit=5)
    print_result("List Services", services_resp)

    # 8. List Heartbeats
    print_section("Heartbeats")
    heartbeats_resp = data_source.list_heartbeats()
    print_result("List Heartbeats", heartbeats_resp)

    print("\n" + "=" * 80)
    print("  All Opsgenie API operations tested!")
    print("=" * 80)


if __name__ == "__main__":
    main()
