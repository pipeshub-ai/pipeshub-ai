# ruff: noqa
"""
Asana API Usage Example v2 (Updated)

Demonstrates using the updated AsanaDataSource from asana_datasource.py across multiple entities:
- Projects CRUD
- Tasks CRUD
- Tags CRUD (and attach to task)
- Sections CRUD
- Custom Fields CRUD
- Stories (comments) CRUD
- Attachments CRUD

Prerequisites:
- Set ASANA_ACCESS_TOKEN environment variable
- Set ASANA_WORKSPACE_GID environment variable
- Set ASANA_ASSIGNEE_GID environment variable (for tasks)
"""

import asyncio
import os
from pprint import pprint

from app.sources.client.asana.asana import AsanaTokenConfig, AsanaClient
from app.sources.external.asana.asana_ import AsanaDataSource


TOKEN = os.getenv("ASANA_ACCESS_TOKEN")
WORKSPACE = os.getenv("ASANA_WORKSPACE_GID")
ASSIGNEE = os.getenv("ASANA_ASSIGNEE_GID")


async def main() -> None:
    if not TOKEN:
        raise Exception("ASANA_ACCESS_TOKEN is not set")
    if not WORKSPACE:
        raise Exception("ASANA_WORKSPACE_GID is not set")
    if not ASSIGNEE:
        raise Exception("ASANA_ASSIGNEE_GID is not set")

    # Create client using the new v2 pattern
    config = AsanaTokenConfig(token=TOKEN)
    client = AsanaClient.build_with_config(config)
    api_client = client.get_api_client()

    # Create data source with the new AsanaDataSource
    data_source = AsanaDataSource(api_client, return_iterators=False)

    print(f"Workspace: {WORKSPACE}")
    print(f"Assignee: {ASSIGNEE}")
    print()

    # --- Project ---
    print("Creating project...")
    project = data_source.create_project(
        body={"data": {"name": "Demo Project v2", "workspace": WORKSPACE}}, opts={}
    )
    pprint(project)
    project_gid = project["gid"]

    print("Reading project...")
    pprint(data_source.get_project(project_gid=project_gid, opts={}))

    print("Updating project...")
    pprint(
        data_source.update_project(
            body={"data": {"notes": "Updated project v2"}},
            project_gid=project_gid,
            opts={},
        )
    )

    # --- Task ---
    print("Creating task...")
    task = data_source.create_task(
        body={
            "data": {
                "name": "Demo Task v2",
                "projects": [project_gid],
                "assignee": ASSIGNEE,
                "workspace": WORKSPACE,
            }
        },
        opts={},
    )
    pprint(task)
    task_gid = task["gid"]

    print("Reading task...")
    pprint(data_source.get_task(task_gid=task_gid, opts={}))

    print("Updating task...")
    pprint(
        data_source.update_task(
            body={"data": {"completed": True}}, task_gid=task_gid, opts={}
        )
    )

    # --- Tag ---
    print("Creating tag...")
    tag = data_source.create_tag(
        body={"data": {"name": "Demo Tag v2", "workspace": WORKSPACE}}, opts={}
    )
    pprint(tag)
    tag_gid = tag["gid"]

    print("Attaching tag to task...")
    pprint(
        data_source.add_tag_for_task(body={"data": {"tag": tag_gid}}, task_gid=task_gid)
    )

    # --- Section ---
    print("Creating section...")
    section = data_source.create_section_for_project(
        project_gid=project_gid, opts={"body": {"data": {"name": "Demo Section v2"}}}
    )
    pprint(section)
    section_gid = section["gid"]

    print("Updating section...")
    pprint(
        data_source.update_section(
            section_gid=section_gid,
            opts={"body": {"data": {"name": "Updated Section v2"}}},
        )
    )

    # --- Custom Field (Read existing) ---
    print("Getting custom field settings for project...")
    pprint(
        data_source.get_custom_field_settings_for_project(
            project_gid=project_gid, opts={}
        )
    )

    # --- Story (Comment) ---
    print("Creating story (comment)...")
    story = data_source.create_story_for_task(
        task_gid=task_gid, body={"data": {"text": "This is a demo comment v2"}}, opts={}
    )
    pprint(story)
    story_gid = story["gid"]

    print("Reading stories for task...")
    pprint(data_source.get_stories_for_task(task_gid=task_gid, opts={}))

    print("Updating story...")
    pprint(
        data_source.update_story(
            story_gid=story_gid,
            body={"data": {"text": "Updated comment v2"}},
            opts={}
        )
    )

    # --- Workspace and Users ---
    print("Getting workspaces...")
    pprint(data_source.get_workspaces(
        opts={}
    ))

    print("Getting users for workspace...")
    pprint(data_source.get_users_for_workspace(workspace_gid=WORKSPACE, opts={}))

    # --- Cleanup ---
    print("Deleting story...")
    pprint(data_source.delete_story(story_gid=story_gid))

    print("Deleting section...")
    pprint(data_source.delete_section(section_gid=section_gid))

    print("Deleting tag...")
    pprint(data_source.delete_tag(tag_gid=tag_gid))

    print("Deleting task...")
    pprint(data_source.delete_task(task_gid=task_gid))

    print("Deleting project...")
    pprint(data_source.delete_project(project_gid=project_gid))

    print("=== Example completed successfully! ===")


if __name__ == "__main__":
    asyncio.run(main())
