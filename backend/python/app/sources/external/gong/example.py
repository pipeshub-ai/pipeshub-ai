"""Gong API Integration Examples

This module provides examples of how to use the Gong client and service
for common operations.
"""

import asyncio
import json
import os
from datetime import datetime

from app.sources.client.gong.gong import (
    test_gong_credentials,
)
from app.sources.external.gong.gong import create_gong_service


async def example_basic_connection():
    """Example: Test basic connection to Gong API."""
    print("🔗 Testing Gong API Connection...")

    # Get credentials from environment variables
    access_key = os.getenv("GONG_ACCESS_KEY")
    access_key_secret = os.getenv("GONG_ACCESS_KEY_SECRET")

    if not access_key or not access_key_secret:
        print("❌ Please set GONG_ACCESS_KEY and GONG_ACCESS_KEY_SECRET environment variables")
        return

    # Test credentials
    is_valid = await test_gong_credentials(access_key, access_key_secret)

    if is_valid:
        print("✅ Connection successful!")
    else:
        print("❌ Connection failed. Please check your credentials.")


async def example_get_workspace_info():
    """Example: Get workspace information."""
    print("\n📊 Getting Workspace Information...")

    access_key = os.getenv("GONG_ACCESS_KEY")
    access_key_secret = os.getenv("GONG_ACCESS_KEY_SECRET")

    if not access_key or not access_key_secret:
        print("❌ Please set environment variables")
        return

    service = await create_gong_service(access_key, access_key_secret)

    try:
        workspace_info = await service.get_workspace_info()

        print(f"📈 Total Workspaces: {workspace_info['total_workspaces']}")
        print(f"👥 Total Users: {workspace_info['total_users']}")

        if workspace_info["primary_workspace"]:
            workspace = workspace_info["primary_workspace"]
            print(f"🏢 Primary Workspace: {workspace.get('name', 'N/A')}")
            print(f"🆔 Workspace ID: {workspace.get('id', 'N/A')}")

        print("\n📋 All Workspaces:")
        for i, workspace in enumerate(workspace_info["workspaces"], 1):
            print(f"  {i}. {workspace.get('name', 'N/A')} (ID: {workspace.get('id', 'N/A')})")

    finally:
        await service.client.close()


async def example_get_recent_calls():
    """Example: Get recent calls."""
    print("\n📞 Getting Recent Calls...")

    access_key = os.getenv("GONG_ACCESS_KEY")
    access_key_secret = os.getenv("GONG_ACCESS_KEY_SECRET")

    if not access_key or not access_key_secret:
        print("❌ Please set environment variables")
        return

    service = await create_gong_service(access_key, access_key_secret)

    try:
        # Get calls from last 7 days
        calls = await service.get_recent_calls(days_back=7, limit=10)

        print(f"📊 Found {len(calls)} recent calls")

        for i, call in enumerate(calls, 1):
            title = call.get("title", "Untitled Call")
            started = call.get("started", "Unknown time")
            duration = call.get("duration", 0)
            participants = len(call.get("participants", []))

            # Convert duration to minutes
            duration_minutes = duration // 60 if duration else 0

            print(f"\n  {i}. {title}")
            print(f"     🕐 Started: {started}")
            print(f"     ⏱️  Duration: {duration_minutes} minutes")
            print(f"     👥 Participants: {participants}")
            print(f"     🆔 Call ID: {call.get('id', 'N/A')}")

    finally:
        await service.client.close()


async def example_get_call_transcript():
    """Example: Get call transcript."""
    print("\n📝 Getting Call Transcript...")

    access_key = os.getenv("GONG_ACCESS_KEY")
    access_key_secret = os.getenv("GONG_ACCESS_KEY_SECRET")

    if not access_key or not access_key_secret:
        print("❌ Please set environment variables")
        return

    service = await create_gong_service(access_key, access_key_secret)

    try:
        # First get a recent call
        calls = await service.get_recent_calls(days_back=30, limit=1)

        if not calls:
            print("❌ No recent calls found")
            return

        call_id = calls[0].get("id")
        if not call_id:
            print("❌ Call ID not found")
            return

        print(f"🔍 Getting transcript for call: {calls[0].get('title', 'Untitled')}")

        # Get call with transcript
        call_data = await service.get_call_with_transcript(call_id)

        if call_data.get("transcript"):
            transcript = call_data["transcript"]
            entries = transcript.get("entries", [])

            print(f"📄 Transcript has {len(entries)} entries")

            # Show first few entries
            for i, entry in enumerate(entries[:5]):
                speaker = entry.get("speakerId", "Unknown")
                text = entry.get("text", "")
                start_time = entry.get("start", 0) // 1000  # Convert to seconds

                print(f"  [{start_time}s] {speaker}: {text[:100]}{'...' if len(text) > 100 else ''}")

            if len(entries) > 5:
                print(f"  ... and {len(entries) - 5} more entries")
        else:
            print("❌ No transcript available for this call")
            if call_data.get("transcript_error"):
                print(f"Error: {call_data['transcript_error']}")

    finally:
        await service.client.close()


async def example_user_activity_summary():
    """Example: Get user activity summary."""
    print("\n👤 Getting User Activity Summary...")

    access_key = os.getenv("GONG_ACCESS_KEY")
    access_key_secret = os.getenv("GONG_ACCESS_KEY_SECRET")

    if not access_key or not access_key_secret:
        print("❌ Please set environment variables")
        return

    service = await create_gong_service(access_key, access_key_secret)

    try:
        # Get activity summary for all users
        summary = await service.get_user_activity_summary(days_back=30)

        print(f"📊 Activity Summary (Last {summary['period_days']} days)")
        print(f"📞 Total Calls: {summary['total_calls']}")
        print(f"⏱️  Total Duration: {summary['total_duration_seconds'] // 60} minutes")

        if summary["total_calls"] > 0:
            avg_duration = summary["average_call_duration"] // 60
            print(f"📈 Average Call Duration: {avg_duration} minutes")

        if summary.get("calls_by_date"):
            print(f"\n📅 Most Active Date: {summary['most_active_date'][0]} ({summary['most_active_date'][1]} calls)")

    finally:
        await service.client.close()


async def example_team_performance():
    """Example: Get team performance metrics."""
    print("\n🏆 Getting Team Performance Metrics...")

    access_key = os.getenv("GONG_ACCESS_KEY")
    access_key_secret = os.getenv("GONG_ACCESS_KEY_SECRET")

    if not access_key or not access_key_secret:
        print("❌ Please set environment variables")
        return

    service = await create_gong_service(access_key, access_key_secret)

    try:
        metrics = await service.get_team_performance_metrics(days_back=30)

        if metrics.get("error"):
            print(f"❌ Error: {metrics['error']}")
            return

        team_totals = metrics["team_totals"]
        print(f"🏢 Team Performance (Last {metrics['period_days']} days)")
        print(f"📞 Total Team Calls: {team_totals['total_calls']}")
        print(f"👥 Active Users: {team_totals['active_users']}/{team_totals['total_users']}")
        print(f"⏱️  Total Duration: {team_totals['total_duration_seconds'] // 60} minutes")

        print("\n🏆 Top Performers:")
        for i, (user_id, user_data) in enumerate(metrics["top_performers"], 1):
            name = user_data["name"] or "Unknown User"
            calls = user_data["total_calls"]
            duration = user_data["total_duration_seconds"] // 60

            print(f"  {i}. {name}: {calls} calls, {duration} minutes")

    finally:
        await service.client.close()


async def example_search_calls():
    """Example: Search calls by keywords."""
    print("\n🔍 Searching Calls by Keywords...")

    access_key = os.getenv("GONG_ACCESS_KEY")
    access_key_secret = os.getenv("GONG_ACCESS_KEY_SECRET")

    if not access_key or not access_key_secret:
        print("❌ Please set environment variables")
        return

    service = await create_gong_service(access_key, access_key_secret)

    try:
        # Search for calls containing specific keywords
        keywords = ["demo", "pricing", "contract", "proposal"]
        matching_calls = await service.search_calls_by_keywords(
            keywords=keywords,
            days_back=30,
        )

        print(f"🔍 Found {len(matching_calls)} calls matching keywords: {', '.join(keywords)}")

        for i, call in enumerate(matching_calls[:5], 1):  # Show top 5
            title = call.get("title", "Untitled Call")
            matched_keywords = call.get("matched_keywords", [])
            match_score = call.get("match_score", 0)
            started = call.get("started", "Unknown time")

            print(f"\n  {i}. {title}")
            print(f"     🎯 Matched Keywords: {', '.join(matched_keywords)}")
            print(f"     📊 Match Score: {match_score}")
            print(f"     🕐 Started: {started}")

    finally:
        await service.client.close()


async def example_export_calls():
    """Example: Export calls data."""
    print("\n📤 Exporting Calls Data...")

    access_key = os.getenv("GONG_ACCESS_KEY")
    access_key_secret = os.getenv("GONG_ACCESS_KEY_SECRET")

    if not access_key or not access_key_secret:
        print("❌ Please set environment variables")
        return

    service = await create_gong_service(access_key, access_key_secret)

    try:
        # Export calls from last 3 days (without transcripts for speed)
        calls_data = await service.export_calls_to_dict(
            days_back=3,
            include_transcripts=False,
        )

        print(f"📊 Exported {len(calls_data)} calls")

        if calls_data:
            # Save to JSON file
            filename = f"gong_calls_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"

            # Create a simplified version for export
            export_data = []
            for call in calls_data:
                export_call = {
                    "id": call.get("id"),
                    "title": call.get("title"),
                    "started": call.get("started"),
                    "duration_seconds": call.get("duration"),
                    "participants": [
                        {
                            "name": p.get("name"),
                            "email": p.get("emailAddress"),
                            "role": p.get("role"),
                        }
                        for p in call.get("participants", [])
                    ],
                    "workspace_id": call.get("workspaceId"),
                }
                export_data.append(export_call)

            with open(filename, "w", encoding="utf-8") as f:
                json.dump(export_data, f, indent=2, ensure_ascii=False)

            print(f"💾 Data exported to: {filename}")

            # Show summary
            total_duration = sum(call.get("duration", 0) for call in calls_data)
            total_participants = sum(len(call.get("participants", [])) for call in calls_data)

            print("📈 Summary:")
            print(f"   Total Duration: {total_duration // 60} minutes")
            print(f"   Total Participants: {total_participants}")
            print(f"   Average Duration: {(total_duration // len(calls_data)) // 60} minutes per call")

    finally:
        await service.client.close()


async def run_all_examples():
    """Run all examples in sequence."""
    print("🚀 Running Gong API Integration Examples")
    print("=" * 50)

    examples = [
        example_basic_connection,
        example_get_workspace_info,
        example_get_recent_calls,
        example_user_activity_summary,
        example_team_performance,
        example_search_calls,
        example_get_call_transcript,
        example_export_calls,
    ]

    for example in examples:
        try:
            await example()
            print("\n" + "-" * 50)
        except Exception as e:
            print(f"❌ Error in {example.__name__}: {e}")
            print("-" * 50)

        # Small delay between examples
        await asyncio.sleep(1)

    print("\n✅ All examples completed!")


if __name__ == "__main__":
    # Set up example credentials (replace with your actual credentials)
    # You can also set these as environment variables
    if not os.getenv("GONG_ACCESS_KEY"):
        print("💡 To run examples, set environment variables:")
        print("   export GONG_ACCESS_KEY='your_access_key'")
        print("   export GONG_ACCESS_KEY_SECRET='your_access_key_secret'")
        print("\nOr modify this script to set them directly (not recommended for production)")

        # Uncomment and set your credentials here for testing
        # os.environ["GONG_ACCESS_KEY"] = "your_access_key_here"
        # os.environ["GONG_ACCESS_KEY_SECRET"] = "your_access_key_secret_here"

    # Run examples
    asyncio.run(run_all_examples())
