# ruff: noqa

"""
Mindtickle API Usage Examples

This example demonstrates how to use the Mindtickle DataSource to interact with
the Mindtickle API, covering:
- Authentication (API Key / Bearer Token)
- Initializing the Client and DataSource
- Listing Users
- Listing Courses, Modules, Quizzes, Assessments
- Fetching Content and Leaderboard
- Analytics (Completion, Engagement)
- Listing Series

Prerequisites:
1. Obtain an API key from the Mindtickle admin panel
2. Set MINDTICKLE_API_KEY environment variable
"""

import asyncio
import json
import os

from app.sources.client.mindtickle.mindtickle import (
    MindtickleClient,
    MindtickleTokenConfig,
    MindtickleResponse,
)
from app.sources.external.mindtickle.mindtickle import MindtickleDataSource

# --- Configuration ---
API_KEY = os.getenv("MINDTICKLE_API_KEY")


def print_section(title: str):
    print(f"\n{'-'*80}")
    print(f"| {title}")
    print(f"{'-'*80}")


def print_result(name: str, response: MindtickleResponse, show_data: bool = True):
    if response.success:
        print(f"  {name}: Success")
        if show_data and response.data:
            data = response.data
            # Handle list-type responses
            for key in ("users", "courses", "modules", "quizzes", "assessments",
                        "content", "leaderboard", "series", "data"):
                if isinstance(data, dict) and key in data:
                    items = data[key]
                    if isinstance(items, list):
                        print(f"   Found {len(items)} {key}.")
                        if items:
                            print(f"   Sample: {json.dumps(items[0], indent=2)[:400]}...")
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
    print_section("Initializing Mindtickle Client")

    if not API_KEY:
        print("  No valid authentication method found.")
        print("   Please set MINDTICKLE_API_KEY environment variable")
        return

    print("  Using API Key (Bearer Token) authentication")
    config = MindtickleTokenConfig(token=API_KEY)
    client = MindtickleClient.build_with_config(config)
    data_source = MindtickleDataSource(client)
    print("Client initialized successfully.")

    try:
        # 2. Get Users
        print_section("Users")
        users_resp = await data_source.get_users(page_size=5)
        print_result("Get Users", users_resp)

        # Get a specific user if available
        if users_resp.success and users_resp.data:
            users = users_resp.data.get("users", [])
            if isinstance(users, list) and users:
                user_id = str(users[0].get("id", ""))
                if user_id:
                    print_section(f"User Details: {user_id}")
                    user_resp = await data_source.get_user(user_id=user_id)
                    print_result("Get User", user_resp)

        # 3. Get Courses
        print_section("Courses")
        courses_resp = await data_source.get_courses(page_size=5)
        print_result("Get Courses", courses_resp)

        # 4. Get Modules
        print_section("Modules")
        modules_resp = await data_source.get_modules(page_size=5)
        print_result("Get Modules", modules_resp)

        # 5. Get Quizzes
        print_section("Quizzes")
        quizzes_resp = await data_source.get_quizzes(page_size=5)
        print_result("Get Quizzes", quizzes_resp)

        # 6. Get Assessments
        print_section("Assessments")
        assessments_resp = await data_source.get_assessments(page_size=5)
        print_result("Get Assessments", assessments_resp)

        # 7. Get Content
        print_section("Content")
        content_resp = await data_source.get_content(page_size=5)
        print_result("Get Content", content_resp)

        # 8. Get Leaderboard
        print_section("Leaderboard")
        leaderboard_resp = await data_source.get_leaderboard(page_size=5)
        print_result("Get Leaderboard", leaderboard_resp)

        # 9. Get Completion Analytics
        print_section("Completion Analytics")
        completion_resp = await data_source.get_completion_analytics(page_size=5)
        print_result("Get Completion Analytics", completion_resp)

        # 10. Get Engagement Analytics
        print_section("Engagement Analytics")
        engagement_resp = await data_source.get_engagement_analytics(page_size=5)
        print_result("Get Engagement Analytics", engagement_resp)

        # 11. Get Series
        print_section("Series")
        series_resp = await data_source.get_series(page_size=5)
        print_result("Get Series", series_resp)

    finally:
        # Cleanup: Close the HTTP client session
        print("\nClosing client connection...")
        inner_client = client.get_client()
        if hasattr(inner_client, "close"):
            await inner_client.close()

    print("\n" + "=" * 80)
    print("  All Mindtickle API operations tested!")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
