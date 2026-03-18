# ruff: noqa

"""
Phabricator API Usage Examples

This example demonstrates how to use the Phabricator DataSource to interact
with the Phabricator Conduit API, covering:
- Authentication (API Token / Conduit Token)
- Initializing the Client and DataSource
- Searching Maniphest tasks
- Searching Differential revisions
- Searching projects and users
- Looking up PHIDs
- Querying the activity feed

Prerequisites:
1. Have access to a Phabricator instance
2. Generate an API token at https://{instance}/settings/user/{username}/page/apitokens/
3. Set PHABRICATOR_API_TOKEN and PHABRICATOR_INSTANCE environment variables
"""

import asyncio
import json
import os

from app.sources.client.phabricator.phabricator import (
    PhabricatorClient,
    PhabricatorResponse,
    PhabricatorTokenConfig,
)
from app.sources.external.phabricator.phabricator import PhabricatorDataSource

# --- Configuration ---
API_TOKEN = os.getenv("PHABRICATOR_API_TOKEN")
INSTANCE = os.getenv("PHABRICATOR_INSTANCE")  # e.g. "phabricator.example.com"


def print_section(title: str):
    print(f"\n{'-'*80}")
    print(f"| {title}")
    print(f"{'-'*80}")


def print_result(name: str, response: PhabricatorResponse, show_data: bool = True):
    if response.success:
        print(f"  {name}: Success")
        if show_data and response.data:
            data = response.data
            if isinstance(data, dict) and "result" in data:
                result = data["result"]
                if isinstance(result, dict) and "data" in result:
                    items = result["data"]
                    print(f"   Found {len(items)} items.")
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
    print_section("Initializing Phabricator Client")

    if not API_TOKEN or not INSTANCE:
        print("  No valid authentication method found.")
        print("   Please set the following environment variables:")
        print("   - PHABRICATOR_API_TOKEN (Conduit API token)")
        print("   - PHABRICATOR_INSTANCE (hostname, e.g. phabricator.example.com)")
        return

    print("  Using API Token authentication")
    config = PhabricatorTokenConfig(
        token=API_TOKEN,
        instance=INSTANCE,
    )

    client = PhabricatorClient.build_with_config(config)
    data_source = PhabricatorDataSource(client)
    print(f"  Client initialized for {INSTANCE}")

    try:
        # 2. Search Maniphest Tasks
        print_section("Maniphest Tasks (Open)")
        tasks_resp = await data_source.search_maniphest_tasks(
            constraints={"statuses": ["open"]},
            limit=5,
        )
        print_result("Search Open Tasks", tasks_resp)

        # 3. Search Differential Revisions
        print_section("Differential Revisions")
        revisions_resp = await data_source.search_differential_revisions(
            limit=5,
        )
        print_result("Search Revisions", revisions_resp)

        # 4. Search Projects
        print_section("Projects")
        projects_resp = await data_source.search_projects(
            limit=5,
        )
        print_result("Search Projects", projects_resp)

        # 5. Search Users
        print_section("Users")
        users_resp = await data_source.search_users(
            limit=5,
        )
        print_result("Search Users", users_resp)

        # 6. Search Pastes
        print_section("Pastes")
        pastes_resp = await data_source.search_pastes(
            limit=5,
        )
        print_result("Search Pastes", pastes_resp)

        # 7. Search Repositories
        print_section("Diffusion Repositories")
        repos_resp = await data_source.search_repositories(
            limit=5,
        )
        print_result("Search Repositories", repos_resp)

        # 8. PHID Lookup (if tasks found)
        if tasks_resp.success and tasks_resp.data:
            result_data = tasks_resp.data.get("result", {})
            if isinstance(result_data, dict):
                items = result_data.get("data", [])
                if items:
                    phid = items[0].get("phid", "")
                    if phid:
                        print_section(f"PHID Lookup: {phid}")
                        phid_resp = await data_source.lookup_phids(names=[phid])
                        print_result("Lookup PHID", phid_resp)

        # 9. Query Feed
        print_section("Activity Feed")
        feed_resp = await data_source.query_feed(limit=5)
        print_result("Query Feed", feed_resp)

    finally:
        # Cleanup: Close the HTTP client session
        print("\nClosing client connection...")
        inner_client = client.get_client()
        if hasattr(inner_client, "close"):
            await inner_client.close()

    print("\n" + "=" * 80)
    print("  All Phabricator API operations tested!")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
