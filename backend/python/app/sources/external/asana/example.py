"""
Example usage of AsanaClient and AsanaDataSource

This demonstrates how to:
1. Create an Asana client with token authentication
2. Initialize the datasource with the client
3. Make API calls using the datasource methods
"""

import asyncio
import json
import os

from app.sources.client.asana.asana import AsanaClient, AsanaResponse, AsanaTokenConfig
from app.sources.external.asana.asana_ import AsanaDataSource


def _print_response(title: str, response: AsanaResponse, max_items: int = 25) -> None:
    """Print an AsanaResponse in a simple format."""
    print(title)

    if not response.success:
        print(f"✗ Error: {response.error}")
        return

    data = response.data
    if not data:
        print("(no data)")
        return

    # Handle generator (convert to list for display)
    if hasattr(data, '__iter__') and not isinstance(data, (dict, str)):
        # This handles both generators and lists
        try:
            data_list = list(data) if not isinstance(data, list) else data
            print(f"✓ Found {len(data_list)} items:")
            for i, item in enumerate(data_list[:max_items], 1):
                print(f"  {i}. {json.dumps(item, indent=4, default=str)}")
            if len(data_list) > max_items:
                print(f"  ... and {len(data_list) - max_items} more items")
            return
        except Exception as e:
            print(f"✗ Error converting data to list: {e}")
            return

    # Handle single object
    print("✓ Result:")
    print(json.dumps(data, indent=2, default=str))


async def example_with_token() -> None:
    """Example using Personal Access Token authentication"""

    # Get token from environment
    token = os.getenv("ASANA_TOKEN")
    workspace_gid = os.getenv("ASANA_WORKSPACE_GID")
    project_gid = os.getenv("ASANA_PROJECT_GID")
    if not token:
        raise Exception("ASANA_TOKEN is not set")
    if not workspace_gid:
        raise Exception("ASANA_WORKSPACE_GID is not set")
    if not project_gid:
        raise Exception("ASANA_PROJECT_GID is not set")

    # Create Asana client with token config
    asana_client = AsanaClient.build_with_config(
        AsanaTokenConfig(
            access_token=token,
            return_page_iterator=True,
            max_retries=5,
        ),
    )

    print(f"✓ Created Asana client: {asana_client}")
    try:
        pagination_mode = (
            "iterator"
            if asana_client.get_client().return_page_iterator
            else "single-page"
        )
        print(f"✓ Pagination mode: {pagination_mode}")
    except Exception as e:
        print(e)
        print("✗ Error: Failed to get pagination mode")

    # Create datasource with the client
    asana_datasource = AsanaDataSource(asana_client)
    print(f"✓ Created Asana datasource: {asana_datasource}")

    # Example 1: Get current user
    print("\n--- Getting current user ---")
    user_response = await asana_datasource.get_user(user_gid="me")
    _print_response("Current user:", user_response)

    # Example 2: Get workspaces
    print("\n--- Getting workspaces ---")
    workspaces_response = await asana_datasource.get_workspaces(
        opts={"opt_fields": "gid,name,resource_type"}
    )
    _print_response("Workspaces:", workspaces_response)

    # Example 3: Get tasks for a project
    # Replace with your actual project GID
    if not project_gid:
        raise Exception("ASANA_PROJECT_GID is not set")
    print(f"\n--- Getting tasks for project {project_gid} ---")
    tasks_response = await asana_datasource.get_tasks_for_project(
        project_gid=project_gid,
        opts={"limit": 25, "opt_fields": "gid,name,resource_type,created_at"},
    )
    _print_response("Project tasks (first page or iterator sample):", tasks_response)

    # Example 4: Create a task
    print("\n--- Creating a task ---")
    task_data = {
        "data": {
            "name": "Test task from API",
            "notes": "This task was created via the Asana API",
            "workspace": workspace_gid,
        }
    }
    create_response = await asana_datasource.create_task(
        body=task_data, opts={"opt_fields": "gid,name,created_at,resource_type"}
    )
    _print_response("Created task:", create_response)
    if getattr(create_response, "success", False) and isinstance(
        create_response.data, dict
    ):
        task_gid = create_response.data.get("gid")

        # Example 5: Update the task
        print(f"\n--- Updating task {task_gid} ---")
        update_data = {"data": {"completed": True}}
        update_response = await asana_datasource.update_task(
            body=update_data, task_gid=task_gid
        )
        _print_response("Updated task:", update_response)

    # Example 6: Search for tasks
    print("\n--- Searching for tasks ---")
    search_response = await asana_datasource.search_tasks_for_workspace(
        workspace_gid=workspace_gid,
        opts={
            "text": "test",
            "limit": 10,
            "opt_fields": "gid,name,resource_type,created_at",
        },
    )
    _print_response("Search results:", search_response)


def main() -> None:
    """Main entry point"""
    print("=" * 70)
    print("Asana API Client Examples")
    print("=" * 70)

    # Run token authentication example
    print("\n🔐 Example 1: Token Authentication")
    print("-" * 70)
    asyncio.run(example_with_token())

    print("\n" + "=" * 70)
    print("✓ Examples completed")
    print("=" * 70)


if __name__ == "__main__":
    main()
