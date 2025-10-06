# ruff: noqa
"""
PostHog API Usage Examples

This example demonstrates how to use the PostHog DataSource to interact with
the PostHog GraphQL API, covering:
- Event tracking and querying
- Person management
- Analytics (trends, funnels)
- Dashboard and insight management
- Feature flags and experiments
- Cohort management

Prerequisites:
- Set POSTHOG_API_KEY environment variable (Personal API Key)
- Set POSTHOG_HOST environment variable (optional, defaults to https://app.posthog.com)
"""

import asyncio
import os
import traceback
from datetime import datetime, timedelta
from typing import Optional

from app.sources.client.posthog.posthog import (
    PostHogTokenConfig,
    PostHogClient
)
from app.sources.external.posthog.posthog import PostHogDataSource

# Environment variables
API_KEY = os.getenv("POSTHOG_API_KEY")
POSTHOG_HOST = os.getenv("POSTHOG_HOST", "https://app.posthog.com")


async def example_events(data_source: PostHogDataSource) -> None:
    """Example: Working with events."""
    print("\n" + "="*80)
    print("EVENTS EXAMPLES")
    print("="*80)
    
    # Capture a new event
    print("\n1. Capturing a new event:")
    capture_response = await data_source.capture_event(
        event="button_clicked",
        distinct_id="user_12345",
        properties={
            "button_name": "signup",
            "page": "/landing",
            "campaign": "summer_2024"
        },
        timestamp=datetime.now().isoformat()
    )
    print(f"   Success: {capture_response.success}")
    if capture_response.success:
        print(f"   Event captured successfully")
    else:
        print(f"   Error: {capture_response.error}")
    
    # Query recent events
    print("\n2. Querying recent events:")
    events_response = await data_source.events(
        limit=10,
        event="button_clicked"
    )
    print(f"   Success: {events_response.success}")
    if events_response.success and events_response.data:
        print(f"   Found events: {len(events_response.data.get('results', []))}")
    
    # Get specific event
    print("\n3. Getting a specific event:")
    # Note: You would need an actual event ID here
    # event_response = await data_source.event(id="event-uuid-here")
    # print(f"   Success: {event_response.success}")


async def example_persons(data_source: PostHogDataSource) -> None:
    """Example: Working with persons."""
    print("\n" + "="*80)
    print("PERSONS EXAMPLES")
    print("="*80)
    
    # Query persons
    print("\n1. Querying persons:")
    persons_response = await data_source.persons(
        limit=5,
        search="user"
    )
    print(f"   Success: {persons_response.success}")
    if persons_response.success and persons_response.data:
        results = persons_response.data.get('results', [])
        print(f"   Found {len(results)} persons")
        for person in results[:3]:
            print(f"   - {person.get('distinct_ids', ['Unknown'])[0]}")
    
    # Update person properties
    print("\n2. Updating person properties:")
    # Note: You would need an actual person ID here
    # update_response = await data_source.person_update(
    #     id="person-id-here",
    #     properties={
    #         "plan": "premium",
    #         "signup_date": "2024-01-15"
    #     }
    # )
    # print(f"   Success: {update_response.success}")


async def example_analytics(data_source: PostHogDataSource) -> None:
    """Example: Analytics queries (trends and funnels)."""
    print("\n" + "="*80)
    print("ANALYTICS EXAMPLES")
    print("="*80)
    
    # Calculate trends
    print("\n1. Calculating event trends:")
    date_from = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    date_to = datetime.now().strftime("%Y-%m-%d")
    
    trend_response = await data_source.trend(
        events=[
            {
                "id": "pageview",
                "type": "events",
                "order": 0
            }
        ],
        date_from=date_from,
        date_to=date_to,
        interval="day"
    )
    print(f"   Success: {trend_response.success}")
    if trend_response.success:
        print(f"   Trend calculated for date range: {date_from} to {date_to}")
    
    # Calculate funnel
    print("\n2. Calculating funnel conversion:")
    funnel_response = await data_source.funnel(
        events=[
            {"id": "page_view", "type": "events", "order": 0},
            {"id": "signup_clicked", "type": "events", "order": 1},
            {"id": "account_created", "type": "events", "order": 2}
        ],
        date_from=date_from,
        date_to=date_to,
        funnel_window_interval=7,
        funnel_window_interval_unit="day"
    )
    print(f"   Success: {funnel_response.success}")
    if funnel_response.success:
        print(f"   Funnel calculated successfully")


async def example_dashboards_insights(data_source: PostHogDataSource) -> None:
    """Example: Working with dashboards and insights."""
    print("\n" + "="*80)
    print("DASHBOARDS & INSIGHTS EXAMPLES")
    print("="*80)
    
    # List dashboards
    print("\n1. Listing dashboards:")
    dashboards_response = await data_source.dashboards(limit=5)
    print(f"   Success: {dashboards_response.success}")
    if dashboards_response.success and dashboards_response.data:
        results = dashboards_response.data.get('results', [])
        print(f"   Found {len(results)} dashboards")
        for dashboard in results[:3]:
            print(f"   - {dashboard.get('name', 'Unnamed')} (ID: {dashboard.get('id')})")
    
    # Create a new dashboard
    print("\n2. Creating a new dashboard:")
    create_dashboard_response = await data_source.dashboard_create(
        name="Weekly Metrics Dashboard",
        description="Key metrics for weekly review",
        pinned=True
    )
    print(f"   Success: {create_dashboard_response.success}")
    if create_dashboard_response.success:
        print(f"   Dashboard created successfully")
    
    # List insights
    print("\n3. Listing insights:")
    insights_response = await data_source.insights(
        saved=True,
        limit=5
    )
    print(f"   Success: {insights_response.success}")
    if insights_response.success and insights_response.data:
        results = insights_response.data.get('results', [])
        print(f"   Found {len(results)} saved insights")


async def example_cohorts(data_source: PostHogDataSource) -> None:
    """Example: Working with cohorts."""
    print("\n" + "="*80)
    print("COHORTS EXAMPLES")
    print("="*80)
    
    # List cohorts
    print("\n1. Listing cohorts:")
    cohorts_response = await data_source.cohorts(limit=5)
    print(f"   Success: {cohorts_response.success}")
    if cohorts_response.success and cohorts_response.data:
        results = cohorts_response.data.get('results', [])
        print(f"   Found {len(results)} cohorts")
        for cohort in results[:3]:
            print(f"   - {cohort.get('name', 'Unnamed')} (ID: {cohort.get('id')})")
    
    # Create a cohort
    print("\n2. Creating a new cohort:")
    create_cohort_response = await data_source.cohort_create(
        name="Active Users - Last 7 Days",
        filters={
            "properties": {
                "type": "OR",
                "values": [
                    {
                        "type": "behavioral",
                        "key": "pageview",
                        "value": 1,
                        "operator": "gte",
                        "time_value": 7,
                        "time_interval": "day"
                    }
                ]
            }
        },
        is_static=False
    )
    print(f"   Success: {create_cohort_response.success}")
    if create_cohort_response.success:
        print(f"   Cohort created successfully")


async def example_feature_flags(data_source: PostHogDataSource) -> None:
    """Example: Working with feature flags."""
    print("\n" + "="*80)
    print("FEATURE FLAGS EXAMPLES")
    print("="*80)
    
    # List feature flags
    print("\n1. Listing feature flags:")
    flags_response = await data_source.feature_flags(limit=5)
    print(f"   Success: {flags_response.success}")
    if flags_response.success and flags_response.data:
        results = flags_response.data.get('results', [])
        print(f"   Found {len(results)} feature flags")
        for flag in results[:3]:
            active = flag.get('active', False)
            status = "Active" if active else "Inactive"
            print(f"   - {flag.get('key', 'unknown')} ({status})")
    
    # Create a feature flag
    print("\n2. Creating a new feature flag:")
    create_flag_response = await data_source.feature_flag_create(
        key="new_dashboard_ui",
        name="New Dashboard UI",
        filters={
            "groups": [
                {
                    "properties": [],
                    "rollout_percentage": 10
                }
            ]
        },
        active=True
    )
    print(f"   Success: {create_flag_response.success}")
    if create_flag_response.success:
        print(f"   Feature flag created successfully")


async def example_experiments(data_source: PostHogDataSource) -> None:
    """Example: Working with experiments."""
    print("\n" + "="*80)
    print("EXPERIMENTS EXAMPLES")
    print("="*80)
    
    # List experiments
    print("\n1. Listing experiments:")
    experiments_response = await data_source.experiments(limit=5)
    print(f"   Success: {experiments_response.success}")
    if experiments_response.success and experiments_response.data:
        results = experiments_response.data.get('results', [])
        print(f"   Found {len(results)} experiments")
        for exp in results[:3]:
            print(f"   - {exp.get('name', 'Unnamed')} (ID: {exp.get('id')})")


async def example_actions(data_source: PostHogDataSource) -> None:
    """Example: Working with actions."""
    print("\n" + "="*80)
    print("ACTIONS EXAMPLES")
    print("="*80)
    
    # List actions
    print("\n1. Listing actions:")
    actions_response = await data_source.actions(limit=5)
    print(f"   Success: {actions_response.success}")
    if actions_response.success and actions_response.data:
        results = actions_response.data.get('results', [])
        print(f"   Found {len(results)} actions")
        for action in results[:3]:
            print(f"   - {action.get('name', 'Unnamed')} (ID: {action.get('id')})")
    
    # Create an action
    print("\n2. Creating a new action:")
    create_action_response = await data_source.action_create(
        name="Signup Completed",
        steps=[
            {
                "event": "account_created",
                "url": "/signup/complete"
            }
        ],
        description="User successfully completed signup flow"
    )
    print(f"   Success: {create_action_response.success}")
    if create_action_response.success:
        print(f"   Action created successfully")


async def example_organization_team(data_source: PostHogDataSource) -> None:
    """Example: Get organization and team info."""
    print("\n" + "="*80)
    print("ORGANIZATION & TEAM EXAMPLES")
    print("="*80)
    
    # Get organization
    print("\n1. Getting organization info:")
    org_response = await data_source.organization()
    print(f"   Success: {org_response.success}")
    if org_response.success and org_response.data:
        print(f"   Organization: {org_response.data.get('name', 'Unknown')}")
    
    # Get team
    print("\n2. Getting team info:")
    team_response = await data_source.team()
    print(f"   Success: {team_response.success}")
    if team_response.success and team_response.data:
        print(f"   Team: {team_response.data.get('name', 'Unknown')}")


async def main() -> None:
    """Main example runner."""
    if not API_KEY:
        raise ValueError(
            "POSTHOG_API_KEY environment variable is required.\n"
            "Get your Personal API Key from: https://app.posthog.com/settings/user-api-keys"
        )
    
    print("="*80)
    print("PostHog API Examples")
    print("="*80)
    print(f"Host: {POSTHOG_HOST}")
    
    # Configure and build the PostHog client
    config = PostHogTokenConfig(
        api_key=API_KEY,
        endpoint=f"{POSTHOG_HOST}/api/graphql"
    )
    client = PostHogClient.build_with_config(config)
    
    # Create the data source
    data_source = PostHogDataSource(client)
    
    # Run examples
    try:
        # Organization & Team (lightweight check)
        await example_organization_team(data_source)
        
        # Events (core functionality)
        await example_events(data_source)
        
        # Persons
        await example_persons(data_source)
        
        # Analytics
        await example_analytics(data_source)
        
        # Dashboards & Insights
        await example_dashboards_insights(data_source)
        
        # Cohorts
        await example_cohorts(data_source)
        
        # Feature Flags
        await example_feature_flags(data_source)
        
        # Experiments
        await example_experiments(data_source)
        
        # Actions
        await example_actions(data_source)
        
    except Exception as e:
        print(f"\n❌ Error during examples: {e}")
        traceback.print_exc()
    
    print("\n" + "="*80)
    print("Examples completed!")
    print("="*80)
    
    # Close the client
    await client.close()


if __name__ == "__main__":
    asyncio.run(main())