"""Example usage of PagerDutyClient and PagerDutyDataSource.

This demonstrates how to:
1. Create a PagerDuty client with token authentication
2. Initialize the datasource with the client
3. Make API calls using the datasource methods
"""

import asyncio
import os

from app.sources.client.pagerduty.pagerduty import (
    PagerDutyClient,
    PagerDutyResponse,
    PagerDutyTokenConfig,
)
from app.sources.external.pagerduty.pagerduty import PagerDutyDataSource


def _print_response(title: str, response: PagerDutyResponse, max_items: int = 3) -> None:
    """Print a PagerDutyResponse in a clean, summarized format."""
    print(title)

    if not response.success:
        print(f"  ✗ Error: {response.error}")
        return

    data = response.data
    if not data:
        print("  (no data)")
        return

    # Handle list responses (incidents, services, users, etc.)
    for key in ["incidents", "services", "users", "schedules", "oncalls", "escalation_policies", "teams", "contact_methods"]:
        if key in data:
            items = data[key]
            print(f"  [OK] Found {len(items)} {key}")
            for i, item in enumerate(items[:max_items], 1):
                # Print only key fields for each item
                if key == "incidents":
                    print(f"    {i}. [{item.get('incident_number')}] {item.get('title')} - Status: {item.get('status')} (ID: {item.get('id')})")
                elif key == "services":
                    print(f"    {i}. {item.get('name')} - Status: {item.get('status')} (ID: {item.get('id')})")
                elif key == "users":
                    print(f"    {i}. {item.get('name')} ({item.get('email')}) - Role: {item.get('role')} (ID: {item.get('id')})")
                elif key == "schedules":
                    print(f"    {i}. {item.get('name')} (ID: {item.get('id')})")
                elif key == "oncalls":
                    user = item.get("user", {})
                    policy = item.get("escalation_policy", {})
                    print(f"    {i}. {user.get('summary')} on {policy.get('summary')} - Level: {item.get('escalation_level')}")
                elif key == "escalation_policies":
                    rules_count = len(item.get("escalation_rules", []))
                    print(f"    {i}. {item.get('name')} - {rules_count} rules (ID: {item.get('id')})")
                elif key == "teams":
                    print(f"    {i}. {item.get('name')} (ID: {item.get('id')})")
                elif key == "contact_methods":
                    print(f"    {i}. {item.get('type')} - {item.get('summary')} (ID: {item.get('id')})")
                else:
                    print(f"    {i}. {item.get('name', item.get('summary', item.get('id')))}")

            if len(items) > max_items:
                print(f"    ... and {len(items) - max_items} more")
            return

    # Handle single object responses (incident, service, user, etc.)
    for key in ["incident", "service", "user", "schedule", "escalation_policy", "team"]:
        if key in data:
            item = data[key]
            print(f"  [OK] {key.replace('_', ' ').title()}:")

            if key == "incident":
                print(f"    • Title: {item.get('title')}")
                print(f"    • Status: {item.get('status')} | Urgency: {item.get('urgency')}")
                print(f"    • Created: {item.get('created_at')}")
                print(f"    • ID: {item.get('id')}")
                if item.get("assignments"):
                    assignee = item["assignments"][0].get("assignee")
                    if assignee:
                        print(f"    • Assigned to: {assignee.get('summary')}")
            elif key == "service":
                print(f"    • Name: {item.get('name')}")
                print(f"    • Status: {item.get('status')}")
                print(f"    • ID: {item.get('id')}")
                policy = item.get("escalation_policy", {})
                if policy:
                    print(f"    • Escalation Policy: {policy.get('name', policy.get('summary'))}")
                integrations = item.get("integrations", [])
                print(f"    • Integrations: {len(integrations)}")
            elif key == "user":
                print(f"    • Name: {item.get('name')}")
                print(f"    • Email: {item.get('email')}")
                print(f"    • Role: {item.get('role')}")
                print(f"    • ID: {item.get('id')}")
                contact_methods = item.get("contact_methods", [])
                print(f"    • Contact Methods: {len(contact_methods)}")
            elif key == "schedule":
                print(f"    • Name: {item.get('name')}")
                print(f"    • ID: {item.get('id')}")
                print(f"    • Time Zone: {item.get('time_zone')}")
            elif key == "escalation_policy":
                print(f"    • Name: {item.get('name')}")
                print(f"    • ID: {item.get('id')}")
                rules = item.get("escalation_rules", [])
                print(f"    • Rules: {len(rules)}")
                print(f"    • Loops: {item.get('num_loops', 0)}")
            elif key == "team":
                print(f"    • Name: {item.get('name')}")
                print(f"    • ID: {item.get('id')}")
            return

    # Handle notes response
    if "notes" in data:
        notes = data["notes"]
        print(f"  [OK] Found {len(notes)} notes")
        for i, note in enumerate(notes[:max_items], 1):
            print(f"    {i}. {note.get('content', 'No content')[:100]}...")
        if len(notes) > max_items:
            print(f"    ... and {len(notes) - max_items} more")
        return

    # Handle error responses
    if "error" in data:
        print(f"  [!]  API Message: {data['error']}")
        return

    # Default: show summary
    print(f"  [OK] Response received with {len(data)} fields")
    print(f"  • Keys: {', '.join(list(data.keys())[:5])}")


async def example_with_token() -> None:
    """Demonstrate API Token authentication."""
    # Get token from environment
    api_token = os.getenv("PAGERDUTY_API_KEY")
    if not api_token:
        raise Exception(
            "PAGERDUTY_API_KEY is not set. "
            "Please set it in your environment: export PAGERDUTY_API_KEY='u+your_token_here'",
        )

    # Create PagerDuty client with token config
    pagerduty_client = await PagerDutyClient.build_with_config(
        PagerDutyTokenConfig(
            api_token=api_token,
        ),
    )

    print(f"[OK] Created PagerDuty client: {pagerduty_client}")

    # Create datasource with the client
    pagerduty_datasource = PagerDutyDataSource(pagerduty_client)
    print(f"[OK] Created PagerDuty datasource: {pagerduty_datasource}")

    # Example 1: Get current user
    print("\n--- Getting current user ---")
    user_response = pagerduty_datasource.get_current_user(
        include=["contact_methods", "notification_rules"],
    )
    _print_response("Current user:", user_response)

    # Example 2: List services
    print("\n--- Getting services ---")
    services_response = pagerduty_datasource.get_services(
        limit=5,
        include=["escalation_policies", "teams"],
    )
    _print_response("Services:", services_response)

    # Example 3: List incidents
    print("\n--- Getting open incidents ---")
    incidents_response = pagerduty_datasource.get_incidents(
        statuses=["triggered", "acknowledged"],
        limit=5,
    )
    _print_response("Open incidents:", incidents_response)

    # Example 4: List users
    print("\n--- Getting users ---")
    users_response = pagerduty_datasource.get_users(
        limit=5,
    )
    _print_response("Users:", users_response)

    # Example 5: List schedules
    print("\n--- Getting schedules ---")
    schedules_response = pagerduty_datasource.get_schedules(
        limit=5,
    )
    _print_response("Schedules:", schedules_response)

    # Example 6: Get on-call users
    print("\n--- Getting on-call users ---")
    oncalls_response = pagerduty_datasource.get_oncalls(
        limit=5,
    )
    _print_response("On-call entries:", oncalls_response)

    # Example 7: List escalation policies
    print("\n--- Getting escalation policies ---")
    policies_response = pagerduty_datasource.get_escalation_policies(
        limit=5,
        include=["services", "teams"],
    )
    _print_response("Escalation policies:", policies_response)

    # Example 8: List teams
    print("\n--- Getting teams ---")
    teams_response = pagerduty_datasource.get_teams(
        limit=5,
    )
    _print_response("Teams:", teams_response)

    # Example 9: Get specific incident (if any exist)
    if incidents_response.success and incidents_response.data and incidents_response.data.get("incidents"):
        first_incident = incidents_response.data["incidents"][0]
        incident_id = first_incident["id"]

        print(f"\n--- Getting details for incident {incident_id} ---")
        incident_detail = pagerduty_datasource.get_incident(incident_id)
        _print_response("Incident details:", incident_detail)

        # Example 10: Get incident notes
        print(f"\n--- Getting notes for incident {incident_id} ---")
        notes_response = pagerduty_datasource.get_incident_notes(incident_id)
        _print_response("Incident notes:", notes_response)

    # Example 11: Get specific service (if any exist)
    if services_response.success and services_response.data and services_response.data.get("services"):
        first_service = services_response.data["services"][0]
        service_id = first_service["id"]

        print(f"\n--- Getting details for service {service_id} ---")
        service_detail = pagerduty_datasource.get_service(
            service_id,
            include=["escalation_policies", "teams", "integrations"],
        )
        _print_response("Service details:", service_detail)


def main() -> None:
    """Run PagerDuty example demonstrations."""
    print("=" * 70)
    print("PagerDuty API Client Examples")
    print("=" * 70)

    # Run token authentication example
    print("\n[*] Example: Token Authentication")
    print("-" * 70)
    asyncio.run(example_with_token())

    print("\n" + "=" * 70)
    print("[OK] Examples completed")
    print("=" * 70)


if __name__ == "__main__":
    main()
