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
- Actions
- Session recordings
- Plugins
- Annotations

Prerequisites:
- Set POSTHOG_API_KEY environment variable (Personal API Key)
- Set POSTHOG_HOST environment variable (optional, defaults to https://app.posthog.com)
"""

import asyncio
import os
import traceback
from datetime import datetime, timedelta

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
        results = events_response.data.get('events', {}).get('results', [])
        print(f"   Found {len(results)} events")
        for event in results[:3]:
            print(f"   - Event: {event.get('event')}, Time: {event.get('timestamp')}")
    
    # Query events by distinct_id
    print("\n3. Querying events for a specific user:")
    user_events = await data_source.events(
        distinct_id="user_12345",
        limit=5
    )
    print(f"   Success: {user_events.success}")
    if user_events.success and user_events.data:
        results = user_events.data.get('events', {}).get('results', [])
        print(f"   Found {len(results)} events for user")


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
        results = persons_response.data.get('persons', {}).get('results', [])
        print(f"   Found {len(results)} persons")
        for person in results[:3]:
            distinct_ids = person.get('distinct_ids', ['Unknown'])
            name = person.get('name', 'Unnamed')
            print(f"   - {name} ({distinct_ids[0] if distinct_ids else 'No ID'})")
    
    # Query persons with property filters
    print("\n2. Querying persons with filters:")
    filtered_persons = await data_source.persons(
        limit=10,
        properties={"email": {"operator": "is_set"}}
    )
    print(f"   Success: {filtered_persons.success}")
    if filtered_persons.success:
        print(f"   Filtered persons query executed")


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
    if trend_response.success and trend_response.data:
        result = trend_response.data.get('trend', {}).get('result', {})
        print(f"   Trend data retrieved for {date_from} to {date_to}")
        if result.get('labels'):
            print(f"   Days: {len(result.get('labels', []))}")
    else:
        print(f"   Error: {trend_response.error}")
    
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
    if funnel_response.success and funnel_response.data:
        result = funnel_response.data.get('funnel', {}).get('result', {})
        steps = result.get('steps', [])
        print(f"   Funnel calculated with {len(steps)} steps")
        for step in steps:
            print(f"   - {step.get('name')}: {step.get('count')} users")
    else:
        print(f"   Error: {funnel_response.error}")


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
        results = dashboards_response.data.get('dashboards', {}).get('results', [])
        print(f"   Found {len(results)} dashboards")
        for dashboard in results[:3]:
            print(f"   - {dashboard.get('name', 'Unnamed')} (ID: {dashboard.get('id')})")
    
    # Create a new dashboard
    print("\n2. Creating a new dashboard:")
    create_dashboard_response = await data_source.dashboard_create(
        name=f"Weekly Metrics Dashboard {datetime.now().strftime('%Y%m%d_%H%M%S')}",
        description="Key metrics for weekly review",
        pinned=True
    )
    print(f"   Success: {create_dashboard_response.success}")
    if create_dashboard_response.success and create_dashboard_response.data:
        dashboard = create_dashboard_response.data.get('dashboardCreate', {}).get('dashboard', {})
        print(f"   Dashboard created: {dashboard.get('name')} (ID: {dashboard.get('id')})")
        created_dashboard_id = dashboard.get('id')
        
        # Update the dashboard
        if created_dashboard_id:
            print("\n3. Updating the dashboard:")
            update_response = await data_source.dashboard_update(
                id=created_dashboard_id,
                description="Updated description for weekly metrics"
            )
            print(f"   Update success: {update_response.success}")
    else:
        print(f"   Error: {create_dashboard_response.error}")
    
    # List insights
    print("\n4. Listing insights:")
    insights_response = await data_source.insights(
        saved=True,
        limit=5
    )
    print(f"   Success: {insights_response.success}")
    if insights_response.success and insights_response.data:
        results = insights_response.data.get('insights', {}).get('results', [])
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
        results = cohorts_response.data.get('cohorts', {}).get('results', [])
        print(f"   Found {len(results)} cohorts")
        for cohort in results[:3]:
            print(f"   - {cohort.get('name', 'Unnamed')} (ID: {cohort.get('id')}, Count: {cohort.get('count', 0)})")
    
    # Create a cohort
    print("\n2. Creating a new cohort:")
    create_cohort_response = await data_source.cohort_create(
        name=f"Active Users - Last 7 Days {datetime.now().strftime('%Y%m%d_%H%M%S')}",
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
    if create_cohort_response.success and create_cohort_response.data:
        cohort = create_cohort_response.data.get('cohortCreate', {}).get('cohort', {})
        print(f"   Cohort created: {cohort.get('name')} (ID: {cohort.get('id')})")
    else:
        print(f"   Error: {create_cohort_response.error}")


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
        results = flags_response.data.get('featureFlags', {}).get('results', [])
        print(f"   Found {len(results)} feature flags")
        for flag in results[:3]:
            active = flag.get('active', False)
            status = "Active" if active else "Inactive"
            print(f"   - {flag.get('key', 'unknown')} ({status})")
    
    # Create a feature flag
    print("\n2. Creating a new feature flag:")
    flag_key = f"new_ui_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    create_flag_response = await data_source.feature_flag_create(
        key=flag_key,
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
    if create_flag_response.success and create_flag_response.data:
        flag = create_flag_response.data.get('featureFlagCreate', {}).get('featureFlag', {})
        print(f"   Feature flag created: {flag.get('key')} (ID: {flag.get('id')})")
    else:
        print(f"   Error: {create_flag_response.error}")


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
        results = experiments_response.data.get('experiments', {}).get('results', [])
        print(f"   Found {len(results)} experiments")
        for exp in results[:3]:
            print(f"   - {exp.get('name', 'Unnamed')} (ID: {exp.get('id')})")
            print(f"     Feature Flag: {exp.get('feature_flag')}")
    else:
        print(f"   Note: {experiments_response.error or 'No experiments found'}")


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
        results = actions_response.data.get('actions', {}).get('results', [])
        print(f"   Found {len(results)} actions")
        for action in results[:3]:
            print(f"   - {action.get('name', 'Unnamed')} (ID: {action.get('id')})")
            steps = action.get('steps', [])
            print(f"     Steps: {len(steps)}")
    
    # Create an action
    print("\n2. Creating a new action:")
    create_action_response = await data_source.action_create(
        name=f"Signup Completed {datetime.now().strftime('%Y%m%d_%H%M%S')}",
        steps=[
            {
                "event": "account_created",
                "url": "/signup/complete"
            }
        ],
        description="User successfully completed signup flow"
    )
    print(f"   Success: {create_action_response.success}")
    if create_action_response.success and create_action_response.data:
        action = create_action_response.data.get('actionCreate', {}).get('action', {})
        print(f"   Action created: {action.get('name')} (ID: {action.get('id')})")
    else:
        print(f"   Error: {create_action_response.error}")


async def example_session_recordings(data_source: PostHogDataSource) -> None:
    """Example: Working with session recordings."""
    print("\n" + "="*80)
    print("SESSION RECORDINGS EXAMPLES")
    print("="*80)
    
    # List session recordings
    print("\n1. Listing session recordings:")
    date_from = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    date_to = datetime.now().strftime("%Y-%m-%d")
    
    recordings_response = await data_source.session_recordings(
        date_from=date_from,
        date_to=date_to,
        limit=5
    )
    print(f"   Success: {recordings_response.success}")
    if recordings_response.success and recordings_response.data:
        results = recordings_response.data.get('sessionRecordings', {}).get('results', [])
        print(f"   Found {len(results)} session recordings")
        for recording in results[:3]:
            duration = recording.get('recording_duration', 0)
            print(f"   - ID: {recording.get('id')}, Duration: {duration}s")
    else:
        print(f"   Note: {recordings_response.error or 'No recordings found'}")


async def example_plugins(data_source: PostHogDataSource) -> None:
    """Example: Working with plugins."""
    print("\n" + "="*80)
    print("PLUGINS EXAMPLES")
    print("="*80)
    
    # List plugins
    print("\n1. Listing plugins:")
    plugins_response = await data_source.plugins(limit=5)
    print(f"   Success: {plugins_response.success}")
    if plugins_response.success and plugins_response.data:
        results = plugins_response.data.get('plugins', {}).get('results', [])
        print(f"   Found {len(results)} plugins")
        for plugin in results[:3]:
            enabled = plugin.get('enabled', False)
            status = "Enabled" if enabled else "Disabled"
            print(f"   - {plugin.get('name', 'Unnamed')} ({status})")
    else:
        print(f"   Note: {plugins_response.error or 'No plugins found'}")


async def example_annotations(data_source: PostHogDataSource) -> None:
    """Example: Working with annotations."""
    print("\n" + "="*80)
    print("ANNOTATIONS EXAMPLES")
    print("="*80)
    
    # Create an annotation
    print("\n1. Creating a new annotation:")
    create_annotation_response = await data_source.annotation_create(
        content="Product launch milestone",
        date_marker=datetime.now().strftime("%Y-%m-%d")
    )
    print(f"   Success: {create_annotation_response.success}")
    if create_annotation_response.success and create_annotation_response.data:
        annotation = create_annotation_response.data.get('annotationCreate', {}).get('annotation', {})
        print(f"   Annotation created: {annotation.get('content')} (ID: {annotation.get('id')})")
    else:
        print(f"   Error: {create_annotation_response.error}")


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
        org = org_response.data.get('organization', {})
        print(f"   Organization: {org.get('name', 'Unknown')}")
        print(f"   ID: {org.get('id')}")
        print(f"   Membership Level: {org.get('membership_level', 'N/A')}")
    else:
        print(f"   Error: {org_response.error}")
    
    # Get team
    print("\n2. Getting team info:")
    team_response = await data_source.team()
    print(f"   Success: {team_response.success}")
    if team_response.success and team_response.data:
        team = team_response.data.get('team', {})
        print(f"   Team: {team.get('name', 'Unknown')}")
        print(f"   ID: {team.get('id')}")
    else:
        print(f"   Error: {team_response.error}")


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
        endpoint=f"{POSTHOG_HOST}/api/graphql",
        timeout=30,
        use_header_auth=True
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
        
        # Session Recordings
        await example_session_recordings(data_source)
        
        # Plugins
        await example_plugins(data_source)
        
        # Annotations
        await example_annotations(data_source)
        
    except Exception as e:
        print(f"\n❌ Error during examples: {e}")
        traceback.print_exc()
    finally:
        # Always close the client
        print("\n" + "="*80)
        print("Closing client connection...")
        await client.close()
        print("="*80)
    
    print("\n" + "="*80)
    print("Examples completed!")
    print("="*80)


if __name__ == "__main__":
    asyncio.run(main())