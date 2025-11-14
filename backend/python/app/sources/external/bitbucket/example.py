# ruff: noqa
"""
Bitbucket Cloud API Usage Examples

This example demonstrates how to use the Bitbucket DataSource to interact with
the Bitbucket Cloud REST API 2.0, covering:
- Workspace operations (list, get workspace info, members, projects)
- Repository CRUD operations (create, read, update, delete)
- Pull Request management (list, create, comment, approve, merge)
- Commit operations (list, get commit details, statuses, comments)
- Branch and Tag management
- Issue tracking (create, update, comment)
- Pipeline operations (trigger, monitor)
- User and permissions management

Prerequisites:
- Set BITBUCKET_TOKEN environment variable (Personal Access Token)
- Set BITBUCKET_WORKSPACE environment variable (your workspace slug)

API Documentation: https://developer.atlassian.com/cloud/bitbucket/rest/
"""

import asyncio
import os
from typing import Optional

from app.sources.client.bitbucket.bitbucket import (
    BitbucketClient,
    BitbucketTokenConfig,
    BitbucketResponse,
)
from app.sources.external.bitbucket.bitbucket_data_source import BitbucketDataSource

# Environment variables
TOKEN = os.getenv("BITBUCKET_TOKEN")
WORKSPACE = os.getenv("BITBUCKET_WORKSPACE")


def print_separator(char: str = "=", length: int = 80) -> None:
    """Print a separator line"""
    print(char * length)


def print_header(title: str) -> None:
    """Print a section header"""
    print_separator()
    print(f"  {title}")
    print_separator()


def print_response(title: str, response: BitbucketResponse, max_items: int = 5) -> None:
    """Print a formatted Bitbucket response"""
    print(f"\n{title}")
    print("-" * 80)

    if not response.success:
        print(f"❌ Error: {response.error}")
        if response.message:
            print(f"   Message: {response.message}")
        return

    if not response.data:
        print("✓ Success (no data returned)")
        return

    print("✓ Success")

    # Handle paginated results
    if isinstance(response.data, dict):
        if "values" in response.data:
            values = response.data["values"]
            print(f"   Found {len(values)} item(s)")
            for i, item in enumerate(values[:max_items], 1):
                if "name" in item:
                    print(f"   {i}. {item['name']}")
                elif "title" in item:
                    print(f"   {i}. {item['title']}")
                elif "slug" in item:
                    print(f"   {i}. {item['slug']}")
                else:
                    print(f"   {i}. {item.get('uuid', 'Item')}")

            if len(values) > max_items:
                print(f"   ... and {len(values) - max_items} more item(s)")
        else:
            # Single object response
            if "name" in response.data:
                print(f"   Name: {response.data['name']}")
            if "slug" in response.data:
                print(f"   Slug: {response.data['slug']}")
            if "uuid" in response.data:
                print(f"   UUID: {response.data['uuid']}")


async def example_workspaces(datasource: BitbucketDataSource) -> Optional[str]:
    """Example: Working with workspaces"""
    print_header("WORKSPACE OPERATIONS")

    # List all workspaces
    print("\n1. Listing workspaces:")
    workspaces_response = await datasource.list_workspaces(pagelen=10)
    print_response("Workspaces", workspaces_response)

    workspace_slug = None
    if workspaces_response.success and workspaces_response.data:
        values = workspaces_response.data.get("values", [])
        if values:
            workspace_slug = values[0].get("slug")

    # Use provided workspace or first from list
    workspace_slug = WORKSPACE or workspace_slug

    if not workspace_slug:
        print(
            "\n❌ No workspace found. Please set BITBUCKET_WORKSPACE environment variable."
        )
        return None

    # Get workspace details
    print(f"\n2. Getting workspace details for '{workspace_slug}':")
    workspace_response = await datasource.get_workspace(workspace_slug)
    print_response("Workspace Details", workspace_response)

    # List workspace members
    print(f"\n3. Listing workspace members:")
    members_response = await datasource.list_workspace_members(
        workspace_slug, pagelen=5
    )
    print_response("Workspace Members", members_response)

    # List workspace projects
    print(f"\n4. Listing workspace projects:")
    projects_response = await datasource.list_workspace_projects(
        workspace_slug, pagelen=5
    )
    print_response("Workspace Projects", projects_response)

    return workspace_slug


async def example_repositories(
    datasource: BitbucketDataSource, workspace: str
) -> Optional[str]:
    """Example: Working with repositories"""
    print_header("REPOSITORY OPERATIONS")

    # List repositories
    print(f"\n1. Listing repositories in workspace '{workspace}':")
    repos_response = await datasource.list_repositories(workspace, pagelen=10)
    print_response("Repositories", repos_response)

    repo_slug = None
    if repos_response.success and repos_response.data:
        values = repos_response.data.get("values", [])
        if values:
            repo_slug = values[0].get("slug")

    if not repo_slug:
        print("\n   No repositories found. Creating a test repository...")

        # Create a new repository
        create_response = await datasource.create_repository(
            workspace=workspace,
            repo_slug="bitbucket-api-test-repo",
            body={
                "scm": "git",
                "is_private": True,
                "description": "Test repository created via Bitbucket API",
                "project": {"key": "TEST"},
            },
        )
        print_response("Create Repository", create_response)

        if create_response.success and create_response.data:
            repo_slug = create_response.data.get("slug")

    if repo_slug:
        # Get repository details
        print(f"\n2. Getting repository details for '{repo_slug}':")
        repo_response = await datasource.get_repository(workspace, repo_slug)
        print_response("Repository Details", repo_response)

        # List repository watchers
        print(f"\n3. Listing repository watchers:")
        watchers_response = await datasource.list_repository_watchers(
            workspace, repo_slug, pagelen=5
        )
        print_response("Repository Watchers", watchers_response)

        # List repository forks
        print(f"\n4. Listing repository forks:")
        forks_response = await datasource.list_repository_forks(
            workspace, repo_slug, pagelen=5
        )
        print_response("Repository Forks", forks_response)

    return repo_slug


async def example_commits(
    datasource: BitbucketDataSource, workspace: str, repo_slug: str
) -> None:
    """Example: Working with commits"""
    print_header("COMMIT OPERATIONS")

    # List commits
    print(f"\n1. Listing commits in '{repo_slug}':")
    commits_response = await datasource.list_commits(workspace, repo_slug, pagelen=10)
    print_response("Commits", commits_response)

    commit_hash = None
    if commits_response.success and commits_response.data:
        values = commits_response.data.get("values", [])
        if values:
            commit_hash = values[0].get("hash")

    if commit_hash:
        # Get commit details
        print(f"\n2. Getting commit details for '{commit_hash[:8]}':")
        commit_response = await datasource.get_commit(workspace, repo_slug, commit_hash)
        print_response("Commit Details", commit_response)

        # List commit comments
        print(f"\n3. Listing commit comments:")
        comments_response = await datasource.list_commit_comments(
            workspace, repo_slug, commit_hash
        )
        print_response("Commit Comments", comments_response)

        # List commit statuses
        print(f"\n4. Listing commit statuses:")
        statuses_response = await datasource.list_commit_statuses(
            workspace, repo_slug, commit_hash
        )
        print_response("Commit Statuses", statuses_response)

        # Create a commit build status (example)
        print(f"\n5. Creating commit build status:")
        status_response = await datasource.create_commit_build_status(
            workspace=workspace,
            repo_slug=repo_slug,
            commit=commit_hash,
            body={
                "key": "test-build",
                "state": "SUCCESSFUL",
                "description": "Test build completed successfully",
                "url": "https://example.com/build/123",
            },
        )
        print_response("Create Build Status", status_response)
    else:
        print("\n   No commits found in repository.")


async def example_branches_tags(
    datasource: BitbucketDataSource, workspace: str, repo_slug: str
) -> None:
    """Example: Working with branches and tags"""
    print_header("BRANCH & TAG OPERATIONS")

    # List branches
    print(f"\n1. Listing branches in '{repo_slug}':")
    branches_response = await datasource.list_branches(workspace, repo_slug, pagelen=10)
    print_response("Branches", branches_response)

    branch_name = None
    if branches_response.success and branches_response.data:
        values = branches_response.data.get("values", [])
        if values:
            branch_name = values[0].get("name")

    if branch_name:
        # Get branch details
        print(f"\n2. Getting branch details for '{branch_name}':")
        branch_response = await datasource.get_branch(workspace, repo_slug, branch_name)
        print_response("Branch Details", branch_response)

    # List tags
    print(f"\n3. Listing tags in '{repo_slug}':")
    tags_response = await datasource.list_tags(workspace, repo_slug, pagelen=10)
    print_response("Tags", tags_response)

    # List branch restrictions
    print(f"\n4. Listing branch restrictions:")
    restrictions_response = await datasource.list_branch_restrictions(
        workspace, repo_slug
    )
    print_response("Branch Restrictions", restrictions_response)


async def example_pull_requests(
    datasource: BitbucketDataSource, workspace: str, repo_slug: str
) -> None:
    """Example: Working with pull requests"""
    print_header("PULL REQUEST OPERATIONS")

    # List pull requests
    print(f"\n1. Listing pull requests in '{repo_slug}':")
    prs_response = await datasource.list_pull_requests(
        workspace, repo_slug, state="OPEN", pagelen=10
    )
    print_response("Pull Requests", prs_response)

    pr_id = None
    if prs_response.success and prs_response.data:
        values = prs_response.data.get("values", [])
        if values:
            pr_id = values[0].get("id")

    if pr_id:
        # Get pull request details
        print(f"\n2. Getting pull request details for PR #{pr_id}:")
        pr_response = await datasource.get_pull_request(workspace, repo_slug, pr_id)
        print_response("Pull Request Details", pr_response)

        # List pull request comments
        print(f"\n3. Listing pull request comments:")
        pr_comments_response = await datasource.list_pull_request_comments(
            workspace, repo_slug, pr_id
        )
        print_response("PR Comments", pr_comments_response)

        # List pull request commits
        print(f"\n4. Listing pull request commits:")
        pr_commits_response = await datasource.list_pull_request_commits(
            workspace, repo_slug, pr_id
        )
        print_response("PR Commits", pr_commits_response)

        # Get pull request activity
        print(f"\n5. Getting pull request activity:")
        pr_activity_response = await datasource.get_pull_request_activity(
            workspace, repo_slug, pr_id
        )
        print_response("PR Activity", pr_activity_response)
    else:
        print("\n   No open pull requests found.")


async def example_issues(
    datasource: BitbucketDataSource, workspace: str, repo_slug: str
) -> None:
    """Example: Working with issues"""
    print_header("ISSUE OPERATIONS")

    # List issues
    print(f"\n1. Listing issues in '{repo_slug}':")
    issues_response = await datasource.list_issues(workspace, repo_slug, pagelen=10)
    print_response("Issues", issues_response)

    issue_id = None
    if issues_response.success and issues_response.data:
        values = issues_response.data.get("values", [])
        if values:
            issue_id = values[0].get("id")

    # Create a new issue if none exist
    if not issue_id:
        print("\n2. Creating a new issue:")
        create_issue_response = await datasource.create_issue(
            workspace=workspace,
            repo_slug=repo_slug,
            body={
                "title": "Test issue from Bitbucket API",
                "content": {
                    "raw": "This is a test issue created via the Bitbucket REST API.",
                    "markup": "markdown",
                },
                "priority": "major",
                "kind": "bug",
            },
        )
        print_response("Create Issue", create_issue_response)

        if create_issue_response.success and create_issue_response.data:
            issue_id = create_issue_response.data.get("id")

    if issue_id:
        # Get issue details
        print(f"\n3. Getting issue details for issue #{issue_id}:")
        issue_response = await datasource.get_issue(workspace, repo_slug, issue_id)
        print_response("Issue Details", issue_response)

        # List issue comments
        print(f"\n4. Listing issue comments:")
        issue_comments_response = await datasource.list_issue_comments(
            workspace, repo_slug, issue_id
        )
        print_response("Issue Comments", issue_comments_response)


async def example_pipelines(
    datasource: BitbucketDataSource, workspace: str, repo_slug: str
) -> None:
    """Example: Working with pipelines"""
    print_header("PIPELINE OPERATIONS")

    # List pipelines
    print(f"\n1. Listing pipelines in '{repo_slug}':")
    pipelines_response = await datasource.list_pipelines(
        workspace, repo_slug, pagelen=10
    )
    print_response("Pipelines", pipelines_response)

    pipeline_uuid = None
    if pipelines_response.success and pipelines_response.data:
        values = pipelines_response.data.get("values", [])
        if values:
            pipeline_uuid = values[0].get("uuid")

    if pipeline_uuid:
        # Get pipeline details
        print(f"\n2. Getting pipeline details:")
        pipeline_response = await datasource.get_pipeline(
            workspace, repo_slug, pipeline_uuid
        )
        print_response("Pipeline Details", pipeline_response)

        # List pipeline steps
        print(f"\n3. Listing pipeline steps:")
        steps_response = await datasource.list_pipeline_steps(
            workspace, repo_slug, pipeline_uuid
        )
        print_response("Pipeline Steps", steps_response)
    else:
        print("\n   No pipelines found in repository.")

    # Get pipeline configuration
    print(f"\n4. Getting pipeline configuration:")
    config_response = await datasource.get_pipeline_config(workspace, repo_slug)
    print_response("Pipeline Config", config_response)

    # List pipeline variables
    print(f"\n5. Listing pipeline variables:")
    variables_response = await datasource.list_pipeline_variables(workspace, repo_slug)
    print_response("Pipeline Variables", variables_response)


async def example_user_operations(datasource: BitbucketDataSource) -> None:
    """Example: Working with user data"""
    print_header("USER OPERATIONS")

    # Get current user
    print("\n1. Getting current user:")
    user_response = await datasource.get_current_user()
    print_response("Current User", user_response)

    # List user emails
    print("\n2. Listing user emails:")
    emails_response = await datasource.list_user_emails()
    print_response("User Emails", emails_response)

    # List user SSH keys
    print("\n3. Listing user SSH keys:")
    ssh_keys_response = await datasource.list_user_ssh_keys()
    print_response("SSH Keys", ssh_keys_response)

    # List user workspace permissions
    print("\n4. Listing user workspace permissions:")
    workspace_perms_response = await datasource.list_user_workspace_permissions()
    print_response("Workspace Permissions", workspace_perms_response)

    # List user repository permissions
    print("\n5. Listing user repository permissions:")
    repo_perms_response = await datasource.list_user_repository_permissions()
    print_response("Repository Permissions", repo_perms_response)


async def example_with_token() -> None:
    """Example using Personal Access Token authentication"""

    if not TOKEN:
        raise ValueError(
            "BITBUCKET_TOKEN environment variable is required.\n"
            "Create a Personal Access Token at: https://bitbucket.org/account/settings/app-passwords/"
        )

    print_header("Bitbucket API Examples - Token Authentication")
    print(f"Token: {TOKEN[:10]}...")

    # Create Bitbucket client with token config
    config = BitbucketTokenConfig(token=TOKEN)
    client = BitbucketClient.build_with_config(config)

    # Create datasource
    datasource = BitbucketDataSource(client)

    # Run examples
    try:
        # User operations
        await example_user_operations(datasource)

        # Workspace operations
        workspace = await example_workspaces(datasource)

        if workspace:
            # Repository operations
            repo_slug = await example_repositories(datasource, workspace)

            if repo_slug:
                # Commit operations
                await example_commits(datasource, workspace, repo_slug)

                # Branch and tag operations
                await example_branches_tags(datasource, workspace, repo_slug)

                # Pull request operations
                await example_pull_requests(datasource, workspace, repo_slug)

                # Issue operations
                await example_issues(datasource, workspace, repo_slug)

                # Pipeline operations
                await example_pipelines(datasource, workspace, repo_slug)

    except Exception as e:
        print(f"\n❌ Error during examples: {e}")
        import traceback

        traceback.print_exc()


async def main() -> None:
    """Main entry point"""
    print("\n" + "=" * 80)
    print("Bitbucket Cloud REST API 2.0 - Python Examples")
    print("=" * 80)

    if not TOKEN:
        print("\n❌ BITBUCKET_TOKEN environment variable is required.")
        print(
            "Create a Personal Access Token at: https://bitbucket.org/account/settings/app-passwords/"
        )
        print("Then set the environment variable:")
        print("  export BITBUCKET_TOKEN=your_token_here")
        print("  export BITBUCKET_WORKSPACE=your_workspace_slug")
        return

    await example_with_token()

    print("\n" + "=" * 80)
    print("Examples completed!")
    print("=" * 80)
    print("\nAPI Documentation: https://developer.atlassian.com/cloud/bitbucket/rest/")
    print()


if __name__ == "__main__":
    asyncio.run(main())
