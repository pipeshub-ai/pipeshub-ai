# ruff: noqa

"""
Redmine API Usage Examples

This example demonstrates how to use the Redmine DataSource to interact with
the Redmine REST API, covering:
- Authentication (API Key, Basic Auth)
- Initializing the Client and DataSource
- Fetching Projects, Issues, Users
- Time entries, News, Wiki pages
- Issue statuses, Trackers, Roles

Prerequisites:
For API Key:
1. Set REDMINE_INSTANCE_URL to your Redmine instance (e.g. "redmine.example.com")
2. Set REDMINE_API_KEY (found at My Account > API access key)

For Basic Auth:
1. Set REDMINE_INSTANCE_URL
2. Set REDMINE_USERNAME and REDMINE_PASSWORD
"""

import asyncio
import json
import os

from app.sources.client.redmine.redmine import (
    RedmineApiKeyConfig,
    RedmineBasicAuthConfig,
    RedmineClient,
    RedmineResponse,
)
from app.sources.external.redmine.redmine import RedmineDataSource

# --- Configuration ---
INSTANCE_URL = os.getenv("REDMINE_INSTANCE_URL", "")
API_KEY = os.getenv("REDMINE_API_KEY", "")
USERNAME = os.getenv("REDMINE_USERNAME", "")
PASSWORD = os.getenv("REDMINE_PASSWORD", "")


def print_section(title: str):
    print(f"\n{'-'*80}")
    print(f"| {title}")
    print(f"{'-'*80}")


def print_result(name: str, response: RedmineResponse, show_data: bool = True):
    if response.success:
        print(f"  {name}: Success")
        if show_data and response.data:
            data = response.data
            if isinstance(data, list):
                print(f"   Found {len(data)} items.")
                if data:
                    print(f"   Sample: {json.dumps(data[0], indent=2)[:400]}...")
            elif isinstance(data, dict):
                for key in ("projects", "issues", "users", "time_entries", "news", "wiki_pages"):
                    if key in data:
                        items = data[key]
                        print(f"   Found {len(items)} {key}.")
                        if items:
                            print(f"   Sample: {json.dumps(items[0], indent=2)[:400]}...")
                        return
                print(f"   Data: {json.dumps(data, indent=2)[:500]}...")
    else:
        print(f"  {name}: Failed")
        print(f"   Error: {response.error}")
        if response.message:
            print(f"   Message: {response.message}")


async def main() -> None:
    # 1. Initialize Client
    print_section("Initializing Redmine Client")

    if not INSTANCE_URL:
        print("  REDMINE_INSTANCE_URL is required.")
        return

    config = None

    # Priority 1: API Key
    if API_KEY:
        print("  Using API Key authentication")
        config = RedmineApiKeyConfig(api_key=API_KEY, instance_url=INSTANCE_URL)

    # Priority 2: Basic Auth
    elif USERNAME and PASSWORD:
        print("  Using Basic Auth authentication")
        config = RedmineBasicAuthConfig(
            username=USERNAME, password=PASSWORD, instance_url=INSTANCE_URL
        )

    if config is None:
        print("  No valid authentication found.")
        print("   Please set one of:")
        print("   - REDMINE_API_KEY (for API Key auth)")
        print("   - REDMINE_USERNAME and REDMINE_PASSWORD (for Basic Auth)")
        return

    client = RedmineClient.build_with_config(config)
    data_source = RedmineDataSource(client)
    print(f"Client initialized for instance: {INSTANCE_URL}")

    try:
        # 2. Get Projects
        print_section("Projects")
        projects_resp = await data_source.get_projects(limit=5)
        print_result("Get Projects", projects_resp)

        project_id = None
        if projects_resp.success and isinstance(projects_resp.data, dict):
            projects = projects_resp.data.get("projects", [])
            if projects:
                project_id = str(projects[0].get("id", ""))
                print(f"   Using Project: {projects[0].get('name')} (ID: {project_id})")

        # 3. Get Issues
        print_section("Issues")
        issues_resp = await data_source.get_issues(
            project_id=project_id, limit=5
        )
        print_result("Get Issues", issues_resp)

        # 4. Get Users
        print_section("Users")
        users_resp = await data_source.get_users(limit=5)
        print_result("Get Users", users_resp)

        # 5. Get Time Entries
        print_section("Time Entries")
        time_resp = await data_source.get_time_entries(limit=5)
        print_result("Get Time Entries", time_resp)

        # 6. Get News
        print_section("News")
        news_resp = await data_source.get_news(limit=5)
        print_result("Get News", news_resp)

        # 7. Get Issue Statuses
        print_section("Issue Statuses")
        statuses_resp = await data_source.get_issue_statuses()
        print_result("Get Issue Statuses", statuses_resp)

        # 8. Get Trackers
        print_section("Trackers")
        trackers_resp = await data_source.get_trackers()
        print_result("Get Trackers", trackers_resp)

        # 9. Get Roles
        print_section("Roles")
        roles_resp = await data_source.get_roles()
        print_result("Get Roles", roles_resp)

        # 10. Get Wiki Index (if project found)
        if project_id:
            print_section("Wiki Index")
            wiki_resp = await data_source.get_wiki_index(project_id)
            print_result("Get Wiki Index", wiki_resp)

    finally:
        # Cleanup
        print("\nClosing client connection...")
        inner_client = client.get_client()
        if hasattr(inner_client, "close"):
            await inner_client.close()

    print("\n" + "=" * 80)
    print("  All Redmine API operations tested!")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
