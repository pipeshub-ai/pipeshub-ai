# ruff: noqa

"""
Bitbucket API Usage Examples

This example demonstrates how to use the Bitbucket DataSource to interact with
the Bitbucket Cloud API (v2.0), covering:
- Authentication (Basic Auth with App Password)
- Initializing the Client and DataSource
- Fetching User Details
- Listing Workspaces and Repositories
- Fetching Commits, Pull Requests, and Issues

Prerequisites:
1. Log in to Bitbucket.
2. Go to Personal Settings -> App Passwords.
3. Create an App Password with permissions (Read: Account, Workspace, Repositories, Pull Requests, Issues).
4. Set the environment variables below (BITBUCKET_USERNAME, BITBUCKET_PASSWORD).
"""

import asyncio
import json
import os

from app.sources.client.bitbucket.bitbucket import (
    BitbucketBasicAuthConfig,
    BitbucketClient,
    BitbucketTokenConfig,
)
from app.sources.external.bitbucket.bitbucket import (
    BitbucketDataSource,
    BitbucketResponse,
)

# --- Configuration ---
# Variables integrated from your request
USERNAME = os.getenv("BITBUCKET_USERNAME")
PASSWORD = os.getenv("BITBUCKET_PASSWORD")
TOKEN = os.getenv("BITBUCKET_TOKEN")
BASE_URL = os.getenv("BITBUCKET_BASE_URL", "https://api.bitbucket.org/2.0")

def print_section(title: str):
    print(f"\n{'-'*80}")
    print(f"| {title}")
    print(f"{'-'*80}")

def print_result(name: str, response: BitbucketResponse, show_data: bool = True):
    if response.success:
        print(f"✅ {name}: Success")
        if show_data and response.data:
            # Handle pagination wrapper often returned by Bitbucket ('values' key)
            data_to_show = response.data
            if isinstance(data_to_show, dict) and "values" in data_to_show:
                items = data_to_show["values"]
                print(f"   Found {len(items)} item(s) in current page.")
                if len(items) > 0:
                    print(f"   Sample item: {json.dumps(items[0], indent=2)[:300]}...")
            else:
                print(f"   Data: {json.dumps(data_to_show, indent=2)[:500]}...")
    else:
        print(f"❌ {name}: Failed")
        print(f"   Error: {response.error}")
        print(f"   Message: {response.message}")


async def main() -> None:
    # 1. Initialize Client
    print_section("Initializing Bitbucket Client")

    # Logic to choose between Token or Username/Password
    if TOKEN:
        print("ℹ️  Using Bearer Token authentication")
        config = BitbucketTokenConfig(token=TOKEN, base_url=BASE_URL)
    elif USERNAME and PASSWORD:
        print("ℹ️  Using Basic Auth (Username/Password) authentication")
        config = BitbucketBasicAuthConfig(
            username=USERNAME,
            password=PASSWORD,
            base_url=BASE_URL
        )
    else:
        print("⚠️  Please set BITBUCKET_USERNAME and BITBUCKET_PASSWORD (or BITBUCKET_TOKEN) environment variables.")
        return

    client = BitbucketClient.build_with_config(config)
    data_source = BitbucketDataSource(client)
    print("Client initialized successfully.")

    # 2. Get Current User
    print_section("Current User")
    user_resp = await data_source.get_user()
    print_result("Get User", user_resp)

    # 3. List Workspaces
    print_section("Workspaces")
    workspaces_resp = await data_source.get_workspaces()
    print_result("List Workspaces", workspaces_resp)

    # 4. List Repositories in Workspace
    print_section("Repositories")
    repositories = []
    if workspaces_resp.success and workspaces_resp.data:
        workspaces = workspaces_resp.data.get("values", []) if isinstance(workspaces_resp.data, dict) else workspaces_resp.data
        for workspace in workspaces:
            if isinstance(workspace, dict):
                workspace_id = workspace.get("slug") or workspace.get("uuid", "").strip("{}")
            else:
                workspace_id = workspace
            print("workspace:", workspace)
            repos_resp = await data_source.get_repositories_workspace(
                workspace=workspace_id,
                sort="-updated_on" # Get recently updated repos
            )
            print_result("List Repositories", repos_resp)
            print("repos_resp:", repos_resp.data)

            # Extract repositories from response
            if repos_resp.success and repos_resp.data:
                repos_data = repos_resp.data.get("values", []) if isinstance(repos_resp.data, dict) else repos_resp.data
                for repo in repos_data:
                    if isinstance(repo, dict):
                        repo_workspace = repo.get("workspace", {})
                        if isinstance(repo_workspace, dict):
                            repo_workspace_slug = repo_workspace.get("slug")
                        else:
                            repo_workspace_slug = workspace_id
                        repo_slug = repo.get("slug")
                        if repo_workspace_slug and repo_slug:
                            repositories.append({
                                "workspace": repo_workspace_slug,
                                "repo_slug": repo_slug,
                                "full_name": repo.get("full_name", f"{repo_workspace_slug}/{repo_slug}")
                            })

    # Process each repository found
    for repo_info in repositories:
        workspace_slug = repo_info["workspace"]
        repo_slug = repo_info["repo_slug"]
        full_name = repo_info["full_name"]

        # 5. Get Specific Repository Details
        print_section(f"Details for {full_name}")
        repo_detail = await data_source.get_repositories_workspace_repo_slug(
            workspace=workspace_slug,
            repo_slug=repo_slug
        )
        print_result("Get Single Repository", repo_detail)

        # 6. List Commits
        print_section(f"Commits for {full_name}")
        commits_resp = await data_source.get_repositories_workspace_repo_slug_commits(
            workspace=workspace_slug,
            repo_slug=repo_slug
        )
        print_result("List Commits", commits_resp)

        # 7. List Pull Requests
        print_section(f"Pull Requests for {full_name}")
        prs_resp = await data_source.get_repositories_workspace_repo_slug_pullrequests(
            workspace=workspace_slug,
            repo_slug=repo_slug,
            state="OPEN"
        )
        print_result("List Open Pull Requests", prs_resp)

        # 8. List Issues (Deprecated - Bitbucket Cloud no longer supports this endpoint)
        print_section(f"Issues for {full_name}")
        print("⚠️  Note: Bitbucket Cloud API v2.0 no longer supports the issues endpoint.")
        print("   Issues are now managed through Jira integration.")
        # Skipping the API call as it will always fail

        # 9. List Branches (Refs)
        print_section(f"Branches for {full_name}")
        branches_resp = await data_source.get_repositories_workspace_repo_slug_refs_branches(
            workspace=workspace_slug,
            repo_slug=repo_slug
        )
        print_result("List Branches", branches_resp)


if __name__ == "__main__":
    asyncio.run(main())