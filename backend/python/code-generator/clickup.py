# ruff: noqa
"""
ClickUp REST API Code Generator

Generates ClickUpDataSource class covering ClickUp API v2 and v3:
- Workspace / Team operations
- Space, Folder, List management
- Task CRUD and management
- Comments, Members, Tags
- Goals, Time tracking
- Views, Webhooks, Custom Fields

The generated DataSource accepts a ClickUpClient and uses the client's
configured version (v2 or v3) to construct the base URL. Methods are
generated for both API versions, with each method tagged to its version.

All methods have explicit parameter signatures with no **kwargs usage.
"""

import sys
from pathlib import Path
from typing import Dict, List, Optional

# ================================================================================
# ClickUp API Endpoints - organized by version and resource
#
# Each endpoint defines:
#   method: HTTP verb
#   path: URL path (appended to base_url which already includes /api/v2 or /api/v3)
#   description: Human-readable description
#   parameters: Dict of param_name -> {type, location (path/query/body), description}
#   required: List of required parameter names
#   version: Which API version this endpoint belongs to ("v2", "v3", or "both")
# ================================================================================

CLICKUP_API_ENDPOINTS = {
    # ================================================================================
    # AUTHORIZATION / USER (v2)
    # ================================================================================
    "get_authorized_user": {
        "method": "GET",
        "path": "/user",
        "description": "Get the authorized user details",
        "parameters": {},
        "required": [],
        "version": "v2",
    },
    "get_authorized_teams_workspaces": {
        "method": "GET",
        "path": "/team",
        "description": "Get the authorized teams (Workspaces) for the authenticated user",
        "parameters": {},
        "required": [],
        "version": "v2",
    },

    # ================================================================================
    # SPACES (v2)
    # ================================================================================
    "get_spaces": {
        "method": "GET",
        "path": "/team/{team_id}/space",
        "description": "Get all Spaces in a Workspace",
        "parameters": {
            "team_id": {"type": "str", "location": "path", "description": "The Workspace (Team) ID"},
            "archived": {"type": "Optional[bool]", "location": "query", "description": "Include archived spaces"},
        },
        "required": ["team_id"],
        "version": "v2",
    },
    "create_space": {
        "method": "POST",
        "path": "/team/{team_id}/space",
        "description": "Create a new Space in a Workspace",
        "parameters": {
            "team_id": {"type": "str", "location": "path", "description": "The Workspace (Team) ID"},
            "name": {"type": "str", "location": "body", "description": "The name of the Space"},
            "multiple_assignees": {"type": "Optional[bool]", "location": "body", "description": "Enable multiple assignees"},
            "features": {"type": "Optional[Dict[str, Any]]", "location": "body", "description": "Space features configuration"},
        },
        "required": ["team_id", "name"],
        "version": "v2",
    },
    "get_space": {
        "method": "GET",
        "path": "/space/{space_id}",
        "description": "Get a specific Space",
        "parameters": {
            "space_id": {"type": "str", "location": "path", "description": "The Space ID"},
        },
        "required": ["space_id"],
        "version": "v2",
    },
    "update_space": {
        "method": "PUT",
        "path": "/space/{space_id}",
        "description": "Update a Space",
        "parameters": {
            "space_id": {"type": "str", "location": "path", "description": "The Space ID"},
            "name": {"type": "Optional[str]", "location": "body", "description": "New name for the Space"},
            "color": {"type": "Optional[str]", "location": "body", "description": "Space color hex code"},
            "private": {"type": "Optional[bool]", "location": "body", "description": "Make Space private"},
            "admin_can_manage": {"type": "Optional[bool]", "location": "body", "description": "Allow admin to manage"},
            "multiple_assignees": {"type": "Optional[bool]", "location": "body", "description": "Enable multiple assignees"},
            "features": {"type": "Optional[Dict[str, Any]]", "location": "body", "description": "Space features configuration"},
        },
        "required": ["space_id"],
        "version": "v2",
    },
    "delete_space": {
        "method": "DELETE",
        "path": "/space/{space_id}",
        "description": "Delete a Space",
        "parameters": {
            "space_id": {"type": "str", "location": "path", "description": "The Space ID"},
        },
        "required": ["space_id"],
        "version": "v2",
    },

    # ================================================================================
    # FOLDERS (v2)
    # ================================================================================
    "get_folders": {
        "method": "GET",
        "path": "/space/{space_id}/folder",
        "description": "Get all Folders in a Space",
        "parameters": {
            "space_id": {"type": "str", "location": "path", "description": "The Space ID"},
            "archived": {"type": "Optional[bool]", "location": "query", "description": "Include archived folders"},
        },
        "required": ["space_id"],
        "version": "v2",
    },
    "create_folder": {
        "method": "POST",
        "path": "/space/{space_id}/folder",
        "description": "Create a Folder in a Space",
        "parameters": {
            "space_id": {"type": "str", "location": "path", "description": "The Space ID"},
            "name": {"type": "str", "location": "body", "description": "The name of the Folder"},
        },
        "required": ["space_id", "name"],
        "version": "v2",
    },
    "get_folder": {
        "method": "GET",
        "path": "/folder/{folder_id}",
        "description": "Get a specific Folder",
        "parameters": {
            "folder_id": {"type": "str", "location": "path", "description": "The Folder ID"},
        },
        "required": ["folder_id"],
        "version": "v2",
    },
    "update_folder": {
        "method": "PUT",
        "path": "/folder/{folder_id}",
        "description": "Update a Folder",
        "parameters": {
            "folder_id": {"type": "str", "location": "path", "description": "The Folder ID"},
            "name": {"type": "str", "location": "body", "description": "New name for the Folder"},
        },
        "required": ["folder_id", "name"],
        "version": "v2",
    },
    "delete_folder": {
        "method": "DELETE",
        "path": "/folder/{folder_id}",
        "description": "Delete a Folder",
        "parameters": {
            "folder_id": {"type": "str", "location": "path", "description": "The Folder ID"},
        },
        "required": ["folder_id"],
        "version": "v2",
    },

    # ================================================================================
    # LISTS (v2)
    # ================================================================================
    "get_lists": {
        "method": "GET",
        "path": "/folder/{folder_id}/list",
        "description": "Get all Lists in a Folder",
        "parameters": {
            "folder_id": {"type": "str", "location": "path", "description": "The Folder ID"},
            "archived": {"type": "Optional[bool]", "location": "query", "description": "Include archived lists"},
        },
        "required": ["folder_id"],
        "version": "v2",
    },
    "create_list": {
        "method": "POST",
        "path": "/folder/{folder_id}/list",
        "description": "Create a List in a Folder",
        "parameters": {
            "folder_id": {"type": "str", "location": "path", "description": "The Folder ID"},
            "name": {"type": "str", "location": "body", "description": "The name of the List"},
            "content": {"type": "Optional[str]", "location": "body", "description": "List description"},
            "due_date": {"type": "Optional[int]", "location": "body", "description": "Due date as Unix timestamp (ms)"},
            "due_date_time": {"type": "Optional[bool]", "location": "body", "description": "Include time in due date"},
            "priority": {"type": "Optional[int]", "location": "body", "description": "Priority level (1=Urgent, 2=High, 3=Normal, 4=Low)"},
            "assignee": {"type": "Optional[int]", "location": "body", "description": "Assignee user ID"},
            "status": {"type": "Optional[str]", "location": "body", "description": "Status name"},
        },
        "required": ["folder_id", "name"],
        "version": "v2",
    },
    "get_folderless_lists": {
        "method": "GET",
        "path": "/space/{space_id}/list",
        "description": "Get Lists that are not in a Folder (folderless Lists)",
        "parameters": {
            "space_id": {"type": "str", "location": "path", "description": "The Space ID"},
            "archived": {"type": "Optional[bool]", "location": "query", "description": "Include archived lists"},
        },
        "required": ["space_id"],
        "version": "v2",
    },
    "create_folderless_list": {
        "method": "POST",
        "path": "/space/{space_id}/list",
        "description": "Create a folderless List in a Space",
        "parameters": {
            "space_id": {"type": "str", "location": "path", "description": "The Space ID"},
            "name": {"type": "str", "location": "body", "description": "The name of the List"},
            "content": {"type": "Optional[str]", "location": "body", "description": "List description"},
            "due_date": {"type": "Optional[int]", "location": "body", "description": "Due date as Unix timestamp (ms)"},
            "due_date_time": {"type": "Optional[bool]", "location": "body", "description": "Include time in due date"},
            "priority": {"type": "Optional[int]", "location": "body", "description": "Priority level"},
            "assignee": {"type": "Optional[int]", "location": "body", "description": "Assignee user ID"},
            "status": {"type": "Optional[str]", "location": "body", "description": "Status name"},
        },
        "required": ["space_id", "name"],
        "version": "v2",
    },
    "get_list": {
        "method": "GET",
        "path": "/list/{list_id}",
        "description": "Get a specific List",
        "parameters": {
            "list_id": {"type": "str", "location": "path", "description": "The List ID"},
        },
        "required": ["list_id"],
        "version": "v2",
    },
    "update_list": {
        "method": "PUT",
        "path": "/list/{list_id}",
        "description": "Update a List",
        "parameters": {
            "list_id": {"type": "str", "location": "path", "description": "The List ID"},
            "name": {"type": "Optional[str]", "location": "body", "description": "New name for the List"},
            "content": {"type": "Optional[str]", "location": "body", "description": "List description"},
            "due_date": {"type": "Optional[int]", "location": "body", "description": "Due date as Unix timestamp (ms)"},
            "due_date_time": {"type": "Optional[bool]", "location": "body", "description": "Include time in due date"},
            "priority": {"type": "Optional[int]", "location": "body", "description": "Priority level"},
            "assignee_add": {"type": "Optional[int]", "location": "body", "description": "Add assignee by user ID"},
            "assignee_rem": {"type": "Optional[int]", "location": "body", "description": "Remove assignee by user ID"},
            "unset_status": {"type": "Optional[bool]", "location": "body", "description": "Remove the status field"},
        },
        "required": ["list_id"],
        "version": "v2",
    },
    "delete_list": {
        "method": "DELETE",
        "path": "/list/{list_id}",
        "description": "Delete a List",
        "parameters": {
            "list_id": {"type": "str", "location": "path", "description": "The List ID"},
        },
        "required": ["list_id"],
        "version": "v2",
    },

    # ================================================================================
    # TASKS (v2)
    # ================================================================================
    "get_tasks": {
        "method": "GET",
        "path": "/list/{list_id}/task",
        "description": "Get Tasks in a List",
        "parameters": {
            "list_id": {"type": "str", "location": "path", "description": "The List ID"},
            "archived": {"type": "Optional[bool]", "location": "query", "description": "Include archived tasks"},
            "include_markdown_description": {"type": "Optional[bool]", "location": "query", "description": "Return description in markdown"},
            "page": {"type": "Optional[int]", "location": "query", "description": "Page number (starts at 0)"},
            "order_by": {"type": "Optional[str]", "location": "query", "description": "Order by field (id, created, updated, due_date)"},
            "reverse": {"type": "Optional[bool]", "location": "query", "description": "Reverse sort order"},
            "subtasks": {"type": "Optional[bool]", "location": "query", "description": "Include subtasks"},
            "statuses": {"type": "Optional[List[str]]", "location": "query", "description": "Filter by status names"},
            "include_closed": {"type": "Optional[bool]", "location": "query", "description": "Include closed tasks"},
            "assignees": {"type": "Optional[List[str]]", "location": "query", "description": "Filter by assignee IDs"},
            "tags": {"type": "Optional[List[str]]", "location": "query", "description": "Filter by tag names"},
            "due_date_gt": {"type": "Optional[int]", "location": "query", "description": "Filter tasks due after timestamp (ms)"},
            "due_date_lt": {"type": "Optional[int]", "location": "query", "description": "Filter tasks due before timestamp (ms)"},
            "date_created_gt": {"type": "Optional[int]", "location": "query", "description": "Filter tasks created after timestamp (ms)"},
            "date_created_lt": {"type": "Optional[int]", "location": "query", "description": "Filter tasks created before timestamp (ms)"},
            "date_updated_gt": {"type": "Optional[int]", "location": "query", "description": "Filter tasks updated after timestamp (ms)"},
            "date_updated_lt": {"type": "Optional[int]", "location": "query", "description": "Filter tasks updated before timestamp (ms)"},
            "custom_fields": {"type": "Optional[List[Dict[str, Any]]]", "location": "query", "description": "Filter by custom field values"},
        },
        "required": ["list_id"],
        "version": "v2",
    },
    "create_task": {
        "method": "POST",
        "path": "/list/{list_id}/task",
        "description": "Create a Task in a List",
        "parameters": {
            "list_id": {"type": "str", "location": "path", "description": "The List ID"},
            "name": {"type": "str", "location": "body", "description": "The name of the Task"},
            "description": {"type": "Optional[str]", "location": "body", "description": "Task description (plain text)"},
            "markdown_description": {"type": "Optional[str]", "location": "body", "description": "Task description (markdown)"},
            "assignees": {"type": "Optional[List[int]]", "location": "body", "description": "Assignee user IDs"},
            "tags": {"type": "Optional[List[str]]", "location": "body", "description": "Tag names"},
            "status": {"type": "Optional[str]", "location": "body", "description": "Status name"},
            "priority": {"type": "Optional[int]", "location": "body", "description": "Priority (1=Urgent, 2=High, 3=Normal, 4=Low)"},
            "due_date": {"type": "Optional[int]", "location": "body", "description": "Due date as Unix timestamp (ms)"},
            "due_date_time": {"type": "Optional[bool]", "location": "body", "description": "Include time in due date"},
            "time_estimate": {"type": "Optional[int]", "location": "body", "description": "Time estimate in milliseconds"},
            "start_date": {"type": "Optional[int]", "location": "body", "description": "Start date as Unix timestamp (ms)"},
            "start_date_time": {"type": "Optional[bool]", "location": "body", "description": "Include time in start date"},
            "notify_all": {"type": "Optional[bool]", "location": "body", "description": "Notify all assignees"},
            "parent": {"type": "Optional[str]", "location": "body", "description": "Parent task ID for subtasks"},
            "links_to": {"type": "Optional[str]", "location": "body", "description": "Task ID to link to"},
            "check_required_custom_fields": {"type": "Optional[bool]", "location": "body", "description": "Validate required custom fields"},
            "custom_fields": {"type": "Optional[List[Dict[str, Any]]]", "location": "body", "description": "Custom field values"},
        },
        "required": ["list_id", "name"],
        "version": "v2",
    },
    "get_task": {
        "method": "GET",
        "path": "/task/{task_id}",
        "description": "Get a specific Task",
        "parameters": {
            "task_id": {"type": "str", "location": "path", "description": "The Task ID"},
            "custom_task_ids": {"type": "Optional[bool]", "location": "query", "description": "Use custom task IDs"},
            "team_id": {"type": "Optional[str]", "location": "query", "description": "Team ID (required with custom_task_ids)"},
            "include_subtasks": {"type": "Optional[bool]", "location": "query", "description": "Include subtasks"},
            "include_markdown_description": {"type": "Optional[bool]", "location": "query", "description": "Return description in markdown"},
        },
        "required": ["task_id"],
        "version": "v2",
    },
    "update_task": {
        "method": "PUT",
        "path": "/task/{task_id}",
        "description": "Update a Task",
        "parameters": {
            "task_id": {"type": "str", "location": "path", "description": "The Task ID"},
            "custom_task_ids": {"type": "Optional[bool]", "location": "query", "description": "Use custom task IDs"},
            "team_id": {"type": "Optional[str]", "location": "query", "description": "Team ID (required with custom_task_ids)"},
            "name": {"type": "Optional[str]", "location": "body", "description": "New task name"},
            "description": {"type": "Optional[str]", "location": "body", "description": "Task description (plain text)"},
            "markdown_description": {"type": "Optional[str]", "location": "body", "description": "Task description (markdown)"},
            "status": {"type": "Optional[str]", "location": "body", "description": "Status name"},
            "priority": {"type": "Optional[int]", "location": "body", "description": "Priority (1=Urgent, 2=High, 3=Normal, 4=Low)"},
            "due_date": {"type": "Optional[int]", "location": "body", "description": "Due date as Unix timestamp (ms)"},
            "due_date_time": {"type": "Optional[bool]", "location": "body", "description": "Include time in due date"},
            "time_estimate": {"type": "Optional[int]", "location": "body", "description": "Time estimate in milliseconds"},
            "start_date": {"type": "Optional[int]", "location": "body", "description": "Start date as Unix timestamp (ms)"},
            "start_date_time": {"type": "Optional[bool]", "location": "body", "description": "Include time in start date"},
            "assignees_add": {"type": "Optional[List[int]]", "location": "body", "description": "Add assignees by user ID"},
            "assignees_rem": {"type": "Optional[List[int]]", "location": "body", "description": "Remove assignees by user ID"},
            "archived": {"type": "Optional[bool]", "location": "body", "description": "Archive or unarchive the task"},
        },
        "required": ["task_id"],
        "version": "v2",
    },
    "delete_task": {
        "method": "DELETE",
        "path": "/task/{task_id}",
        "description": "Delete a Task",
        "parameters": {
            "task_id": {"type": "str", "location": "path", "description": "The Task ID"},
            "custom_task_ids": {"type": "Optional[bool]", "location": "query", "description": "Use custom task IDs"},
            "team_id": {"type": "Optional[str]", "location": "query", "description": "Team ID (required with custom_task_ids)"},
        },
        "required": ["task_id"],
        "version": "v2",
    },
    "get_filtered_team_tasks": {
        "method": "GET",
        "path": "/team/{team_id}/task",
        "description": "Get filtered Tasks across an entire Workspace",
        "parameters": {
            "team_id": {"type": "str", "location": "path", "description": "The Workspace (Team) ID"},
            "page": {"type": "Optional[int]", "location": "query", "description": "Page number (starts at 0)"},
            "order_by": {"type": "Optional[str]", "location": "query", "description": "Order by field"},
            "reverse": {"type": "Optional[bool]", "location": "query", "description": "Reverse sort order"},
            "subtasks": {"type": "Optional[bool]", "location": "query", "description": "Include subtasks"},
            "statuses": {"type": "Optional[List[str]]", "location": "query", "description": "Filter by status names"},
            "include_closed": {"type": "Optional[bool]", "location": "query", "description": "Include closed tasks"},
            "assignees": {"type": "Optional[List[str]]", "location": "query", "description": "Filter by assignee IDs"},
            "tags": {"type": "Optional[List[str]]", "location": "query", "description": "Filter by tag names"},
            "due_date_gt": {"type": "Optional[int]", "location": "query", "description": "Filter tasks due after timestamp (ms)"},
            "due_date_lt": {"type": "Optional[int]", "location": "query", "description": "Filter tasks due before timestamp (ms)"},
            "date_created_gt": {"type": "Optional[int]", "location": "query", "description": "Filter tasks created after timestamp"},
            "date_created_lt": {"type": "Optional[int]", "location": "query", "description": "Filter tasks created before timestamp"},
            "date_updated_gt": {"type": "Optional[int]", "location": "query", "description": "Filter tasks updated after timestamp"},
            "date_updated_lt": {"type": "Optional[int]", "location": "query", "description": "Filter tasks updated before timestamp"},
            "space_ids": {"type": "Optional[List[str]]", "location": "query", "description": "Filter by Space IDs"},
            "project_ids": {"type": "Optional[List[str]]", "location": "query", "description": "Filter by project (Folder) IDs"},
            "list_ids": {"type": "Optional[List[str]]", "location": "query", "description": "Filter by List IDs"},
        },
        "required": ["team_id"],
        "version": "v2",
    },

    # ================================================================================
    # TASK COMMENTS (v2)
    # ================================================================================
    "get_task_comments": {
        "method": "GET",
        "path": "/task/{task_id}/comment",
        "description": "Get comments on a Task",
        "parameters": {
            "task_id": {"type": "str", "location": "path", "description": "The Task ID"},
            "custom_task_ids": {"type": "Optional[bool]", "location": "query", "description": "Use custom task IDs"},
            "team_id": {"type": "Optional[str]", "location": "query", "description": "Team ID (required with custom_task_ids)"},
            "start": {"type": "Optional[int]", "location": "query", "description": "Start timestamp for comments"},
            "start_id": {"type": "Optional[str]", "location": "query", "description": "Start comment ID for pagination"},
        },
        "required": ["task_id"],
        "version": "v2",
    },
    "create_task_comment": {
        "method": "POST",
        "path": "/task/{task_id}/comment",
        "description": "Create a comment on a Task",
        "parameters": {
            "task_id": {"type": "str", "location": "path", "description": "The Task ID"},
            "comment_text": {"type": "str", "location": "body", "description": "The comment text (plain text)"},
            "assignee": {"type": "Optional[int]", "location": "body", "description": "Assign the comment to a user ID"},
            "notify_all": {"type": "Optional[bool]", "location": "body", "description": "Notify all assignees"},
            "custom_task_ids": {"type": "Optional[bool]", "location": "query", "description": "Use custom task IDs"},
            "team_id": {"type": "Optional[str]", "location": "query", "description": "Team ID (required with custom_task_ids)"},
        },
        "required": ["task_id", "comment_text"],
        "version": "v2",
    },
    "get_list_comments": {
        "method": "GET",
        "path": "/list/{list_id}/comment",
        "description": "Get comments on a List",
        "parameters": {
            "list_id": {"type": "str", "location": "path", "description": "The List ID"},
            "start": {"type": "Optional[int]", "location": "query", "description": "Start timestamp for comments"},
            "start_id": {"type": "Optional[str]", "location": "query", "description": "Start comment ID for pagination"},
        },
        "required": ["list_id"],
        "version": "v2",
    },
    "create_list_comment": {
        "method": "POST",
        "path": "/list/{list_id}/comment",
        "description": "Create a comment on a List",
        "parameters": {
            "list_id": {"type": "str", "location": "path", "description": "The List ID"},
            "comment_text": {"type": "str", "location": "body", "description": "The comment text (plain text)"},
            "assignee": {"type": "Optional[int]", "location": "body", "description": "Assign the comment to a user ID"},
            "notify_all": {"type": "Optional[bool]", "location": "body", "description": "Notify all assignees"},
        },
        "required": ["list_id", "comment_text"],
        "version": "v2",
    },
    "update_comment": {
        "method": "PUT",
        "path": "/comment/{comment_id}",
        "description": "Update a comment",
        "parameters": {
            "comment_id": {"type": "str", "location": "path", "description": "The Comment ID"},
            "comment_text": {"type": "str", "location": "body", "description": "Updated comment text"},
            "assignee": {"type": "Optional[int]", "location": "body", "description": "Reassign the comment"},
            "resolved": {"type": "Optional[bool]", "location": "body", "description": "Resolve or unresolve the comment"},
        },
        "required": ["comment_id", "comment_text"],
        "version": "v2",
    },
    "delete_comment": {
        "method": "DELETE",
        "path": "/comment/{comment_id}",
        "description": "Delete a comment",
        "parameters": {
            "comment_id": {"type": "str", "location": "path", "description": "The Comment ID"},
        },
        "required": ["comment_id"],
        "version": "v2",
    },

    # ================================================================================
    # MEMBERS (v2)
    # ================================================================================
    "get_task_members": {
        "method": "GET",
        "path": "/task/{task_id}/member",
        "description": "Get members assigned to a Task",
        "parameters": {
            "task_id": {"type": "str", "location": "path", "description": "The Task ID"},
        },
        "required": ["task_id"],
        "version": "v2",
    },
    "get_list_members": {
        "method": "GET",
        "path": "/list/{list_id}/member",
        "description": "Get members of a List",
        "parameters": {
            "list_id": {"type": "str", "location": "path", "description": "The List ID"},
        },
        "required": ["list_id"],
        "version": "v2",
    },

    # ================================================================================
    # TAGS (v2)
    # ================================================================================
    "get_space_tags": {
        "method": "GET",
        "path": "/space/{space_id}/tag",
        "description": "Get all Tags in a Space",
        "parameters": {
            "space_id": {"type": "str", "location": "path", "description": "The Space ID"},
        },
        "required": ["space_id"],
        "version": "v2",
    },
    "create_space_tag": {
        "method": "POST",
        "path": "/space/{space_id}/tag",
        "description": "Create a Tag in a Space",
        "parameters": {
            "space_id": {"type": "str", "location": "path", "description": "The Space ID"},
            "name": {"type": "str", "location": "body", "description": "Tag name"},
            "tag_fg": {"type": "Optional[str]", "location": "body", "description": "Foreground color hex"},
            "tag_bg": {"type": "Optional[str]", "location": "body", "description": "Background color hex"},
        },
        "required": ["space_id", "name"],
        "version": "v2",
    },
    "add_tag_to_task": {
        "method": "POST",
        "path": "/task/{task_id}/tag/{tag_name}",
        "description": "Add a Tag to a Task",
        "parameters": {
            "task_id": {"type": "str", "location": "path", "description": "The Task ID"},
            "tag_name": {"type": "str", "location": "path", "description": "The Tag name"},
            "custom_task_ids": {"type": "Optional[bool]", "location": "query", "description": "Use custom task IDs"},
            "team_id": {"type": "Optional[str]", "location": "query", "description": "Team ID (required with custom_task_ids)"},
        },
        "required": ["task_id", "tag_name"],
        "version": "v2",
    },
    "remove_tag_from_task": {
        "method": "DELETE",
        "path": "/task/{task_id}/tag/{tag_name}",
        "description": "Remove a Tag from a Task",
        "parameters": {
            "task_id": {"type": "str", "location": "path", "description": "The Task ID"},
            "tag_name": {"type": "str", "location": "path", "description": "The Tag name"},
            "custom_task_ids": {"type": "Optional[bool]", "location": "query", "description": "Use custom task IDs"},
            "team_id": {"type": "Optional[str]", "location": "query", "description": "Team ID (required with custom_task_ids)"},
        },
        "required": ["task_id", "tag_name"],
        "version": "v2",
    },

    # ================================================================================
    # GOALS (v2)
    # ================================================================================
    "get_goals": {
        "method": "GET",
        "path": "/team/{team_id}/goal",
        "description": "Get all Goals in a Workspace",
        "parameters": {
            "team_id": {"type": "str", "location": "path", "description": "The Workspace (Team) ID"},
        },
        "required": ["team_id"],
        "version": "v2",
    },
    "create_goal": {
        "method": "POST",
        "path": "/team/{team_id}/goal",
        "description": "Create a Goal in a Workspace",
        "parameters": {
            "team_id": {"type": "str", "location": "path", "description": "The Workspace (Team) ID"},
            "name": {"type": "str", "location": "body", "description": "Goal name"},
            "due_date": {"type": "Optional[int]", "location": "body", "description": "Due date as Unix timestamp (ms)"},
            "description": {"type": "Optional[str]", "location": "body", "description": "Goal description"},
            "multiple_owners": {"type": "Optional[bool]", "location": "body", "description": "Allow multiple owners"},
            "owners": {"type": "Optional[List[int]]", "location": "body", "description": "Owner user IDs"},
            "color": {"type": "Optional[str]", "location": "body", "description": "Goal color hex code"},
        },
        "required": ["team_id", "name"],
        "version": "v2",
    },
    "get_goal": {
        "method": "GET",
        "path": "/goal/{goal_id}",
        "description": "Get a specific Goal",
        "parameters": {
            "goal_id": {"type": "str", "location": "path", "description": "The Goal ID"},
        },
        "required": ["goal_id"],
        "version": "v2",
    },
    "update_goal": {
        "method": "PUT",
        "path": "/goal/{goal_id}",
        "description": "Update a Goal",
        "parameters": {
            "goal_id": {"type": "str", "location": "path", "description": "The Goal ID"},
            "name": {"type": "Optional[str]", "location": "body", "description": "Goal name"},
            "due_date": {"type": "Optional[int]", "location": "body", "description": "Due date as Unix timestamp (ms)"},
            "description": {"type": "Optional[str]", "location": "body", "description": "Goal description"},
            "color": {"type": "Optional[str]", "location": "body", "description": "Goal color hex code"},
            "add_owners": {"type": "Optional[List[int]]", "location": "body", "description": "Add owner user IDs"},
            "rem_owners": {"type": "Optional[List[int]]", "location": "body", "description": "Remove owner user IDs"},
        },
        "required": ["goal_id"],
        "version": "v2",
    },
    "delete_goal": {
        "method": "DELETE",
        "path": "/goal/{goal_id}",
        "description": "Delete a Goal",
        "parameters": {
            "goal_id": {"type": "str", "location": "path", "description": "The Goal ID"},
        },
        "required": ["goal_id"],
        "version": "v2",
    },

    # ================================================================================
    # TIME TRACKING (v2)
    # ================================================================================
    "get_time_entries_in_range": {
        "method": "GET",
        "path": "/team/{team_id}/time_entries",
        "description": "Get time entries within a date range for a Workspace",
        "parameters": {
            "team_id": {"type": "str", "location": "path", "description": "The Workspace (Team) ID"},
            "start_date": {"type": "Optional[int]", "location": "query", "description": "Start date as Unix timestamp (ms)"},
            "end_date": {"type": "Optional[int]", "location": "query", "description": "End date as Unix timestamp (ms)"},
            "assignee": {"type": "Optional[str]", "location": "query", "description": "Filter by user ID"},
            "include_task_tags": {"type": "Optional[bool]", "location": "query", "description": "Include task tag info"},
            "include_location_names": {"type": "Optional[bool]", "location": "query", "description": "Include Space, Folder, List names"},
        },
        "required": ["team_id"],
        "version": "v2",
    },
    "get_task_time_entries": {
        "method": "GET",
        "path": "/task/{task_id}/time",
        "description": "Get tracked time entries for a Task",
        "parameters": {
            "task_id": {"type": "str", "location": "path", "description": "The Task ID"},
            "custom_task_ids": {"type": "Optional[bool]", "location": "query", "description": "Use custom task IDs"},
            "team_id": {"type": "Optional[str]", "location": "query", "description": "Team ID (required with custom_task_ids)"},
        },
        "required": ["task_id"],
        "version": "v2",
    },
    "create_time_entry": {
        "method": "POST",
        "path": "/team/{team_id}/time_entries",
        "description": "Create a time entry",
        "parameters": {
            "team_id": {"type": "str", "location": "path", "description": "The Workspace (Team) ID"},
            "description": {"type": "Optional[str]", "location": "body", "description": "Time entry description"},
            "start": {"type": "int", "location": "body", "description": "Start time as Unix timestamp (ms)"},
            "end": {"type": "Optional[int]", "location": "body", "description": "End time as Unix timestamp (ms)"},
            "duration": {"type": "int", "location": "body", "description": "Duration in milliseconds"},
            "assignee": {"type": "Optional[int]", "location": "body", "description": "User ID to assign the time entry"},
            "tid": {"type": "Optional[str]", "location": "body", "description": "Task ID to associate with"},
            "billable": {"type": "Optional[bool]", "location": "body", "description": "Mark as billable"},
            "tags": {"type": "Optional[List[Dict[str, str]]]", "location": "body", "description": "Tags for the time entry"},
        },
        "required": ["team_id", "start", "duration"],
        "version": "v2",
    },
    "delete_time_entry": {
        "method": "DELETE",
        "path": "/team/{team_id}/time_entries/{timer_id}",
        "description": "Delete a time entry",
        "parameters": {
            "team_id": {"type": "str", "location": "path", "description": "The Workspace (Team) ID"},
            "timer_id": {"type": "str", "location": "path", "description": "The time entry ID"},
        },
        "required": ["team_id", "timer_id"],
        "version": "v2",
    },

    # ================================================================================
    # VIEWS (v2)
    # ================================================================================
    "get_team_views": {
        "method": "GET",
        "path": "/team/{team_id}/view",
        "description": "Get all Views at Workspace level",
        "parameters": {
            "team_id": {"type": "str", "location": "path", "description": "The Workspace (Team) ID"},
        },
        "required": ["team_id"],
        "version": "v2",
    },
    "get_space_views": {
        "method": "GET",
        "path": "/space/{space_id}/view",
        "description": "Get all Views in a Space",
        "parameters": {
            "space_id": {"type": "str", "location": "path", "description": "The Space ID"},
        },
        "required": ["space_id"],
        "version": "v2",
    },
    "get_folder_views": {
        "method": "GET",
        "path": "/folder/{folder_id}/view",
        "description": "Get all Views in a Folder",
        "parameters": {
            "folder_id": {"type": "str", "location": "path", "description": "The Folder ID"},
        },
        "required": ["folder_id"],
        "version": "v2",
    },
    "get_list_views": {
        "method": "GET",
        "path": "/list/{list_id}/view",
        "description": "Get all Views for a List",
        "parameters": {
            "list_id": {"type": "str", "location": "path", "description": "The List ID"},
        },
        "required": ["list_id"],
        "version": "v2",
    },
    "get_view": {
        "method": "GET",
        "path": "/view/{view_id}",
        "description": "Get a specific View",
        "parameters": {
            "view_id": {"type": "str", "location": "path", "description": "The View ID"},
        },
        "required": ["view_id"],
        "version": "v2",
    },
    "get_view_tasks": {
        "method": "GET",
        "path": "/view/{view_id}/task",
        "description": "Get Tasks from a View",
        "parameters": {
            "view_id": {"type": "str", "location": "path", "description": "The View ID"},
            "page": {"type": "Optional[int]", "location": "query", "description": "Page number (starts at 0)"},
        },
        "required": ["view_id"],
        "version": "v2",
    },

    # ================================================================================
    # CUSTOM FIELDS (v2)
    # ================================================================================
    "get_accessible_custom_fields": {
        "method": "GET",
        "path": "/list/{list_id}/field",
        "description": "Get all accessible Custom Fields for a List",
        "parameters": {
            "list_id": {"type": "str", "location": "path", "description": "The List ID"},
        },
        "required": ["list_id"],
        "version": "v2",
    },
    "set_custom_field_value": {
        "method": "POST",
        "path": "/task/{task_id}/field/{field_id}",
        "description": "Set a Custom Field value on a Task",
        "parameters": {
            "task_id": {"type": "str", "location": "path", "description": "The Task ID"},
            "field_id": {"type": "str", "location": "path", "description": "The Custom Field ID"},
            "value": {"type": "str | int | float | bool | list[Any] | dict[str, Any]", "location": "body", "description": "The value to set"},
            "custom_task_ids": {"type": "Optional[bool]", "location": "query", "description": "Use custom task IDs"},
            "team_id": {"type": "Optional[str]", "location": "query", "description": "Team ID (required with custom_task_ids)"},
        },
        "required": ["task_id", "field_id", "value"],
        "version": "v2",
    },
    "remove_custom_field_value": {
        "method": "DELETE",
        "path": "/task/{task_id}/field/{field_id}",
        "description": "Remove a Custom Field value from a Task",
        "parameters": {
            "task_id": {"type": "str", "location": "path", "description": "The Task ID"},
            "field_id": {"type": "str", "location": "path", "description": "The Custom Field ID"},
            "custom_task_ids": {"type": "Optional[bool]", "location": "query", "description": "Use custom task IDs"},
            "team_id": {"type": "Optional[str]", "location": "query", "description": "Team ID (required with custom_task_ids)"},
        },
        "required": ["task_id", "field_id"],
        "version": "v2",
    },

    # ================================================================================
    # WEBHOOKS (v2)
    # ================================================================================
    "get_webhooks": {
        "method": "GET",
        "path": "/team/{team_id}/webhook",
        "description": "Get all Webhooks in a Workspace",
        "parameters": {
            "team_id": {"type": "str", "location": "path", "description": "The Workspace (Team) ID"},
        },
        "required": ["team_id"],
        "version": "v2",
    },
    "create_webhook": {
        "method": "POST",
        "path": "/team/{team_id}/webhook",
        "description": "Create a Webhook in a Workspace",
        "parameters": {
            "team_id": {"type": "str", "location": "path", "description": "The Workspace (Team) ID"},
            "endpoint": {"type": "str", "location": "body", "description": "Webhook endpoint URL"},
            "events": {"type": "List[str]", "location": "body", "description": "List of event types to subscribe to"},
            "space_id": {"type": "Optional[str]", "location": "body", "description": "Filter to a specific Space"},
            "folder_id": {"type": "Optional[str]", "location": "body", "description": "Filter to a specific Folder"},
            "list_id": {"type": "Optional[str]", "location": "body", "description": "Filter to a specific List"},
            "task_id": {"type": "Optional[str]", "location": "body", "description": "Filter to a specific Task"},
        },
        "required": ["team_id", "endpoint", "events"],
        "version": "v2",
    },
    "update_webhook": {
        "method": "PUT",
        "path": "/webhook/{webhook_id}",
        "description": "Update a Webhook",
        "parameters": {
            "webhook_id": {"type": "str", "location": "path", "description": "The Webhook ID"},
            "endpoint": {"type": "Optional[str]", "location": "body", "description": "New endpoint URL"},
            "events": {"type": "Optional[List[str]]", "location": "body", "description": "Updated event types"},
            "status": {"type": "Optional[str]", "location": "body", "description": "Webhook status (active/inactive)"},
        },
        "required": ["webhook_id"],
        "version": "v2",
    },
    "delete_webhook": {
        "method": "DELETE",
        "path": "/webhook/{webhook_id}",
        "description": "Delete a Webhook",
        "parameters": {
            "webhook_id": {"type": "str", "location": "path", "description": "The Webhook ID"},
        },
        "required": ["webhook_id"],
        "version": "v2",
    },

    # ================================================================================
    # CHECKLISTS (v2)
    # ================================================================================
    "create_checklist": {
        "method": "POST",
        "path": "/task/{task_id}/checklist",
        "description": "Create a Checklist in a Task",
        "parameters": {
            "task_id": {"type": "str", "location": "path", "description": "The Task ID"},
            "name": {"type": "str", "location": "body", "description": "Checklist name"},
            "custom_task_ids": {"type": "Optional[bool]", "location": "query", "description": "Use custom task IDs"},
            "team_id": {"type": "Optional[str]", "location": "query", "description": "Team ID (required with custom_task_ids)"},
        },
        "required": ["task_id", "name"],
        "version": "v2",
    },
    "update_checklist": {
        "method": "PUT",
        "path": "/checklist/{checklist_id}",
        "description": "Update a Checklist",
        "parameters": {
            "checklist_id": {"type": "str", "location": "path", "description": "The Checklist ID"},
            "name": {"type": "Optional[str]", "location": "body", "description": "New checklist name"},
            "position": {"type": "Optional[int]", "location": "body", "description": "Position of the checklist"},
        },
        "required": ["checklist_id"],
        "version": "v2",
    },
    "delete_checklist": {
        "method": "DELETE",
        "path": "/checklist/{checklist_id}",
        "description": "Delete a Checklist",
        "parameters": {
            "checklist_id": {"type": "str", "location": "path", "description": "The Checklist ID"},
        },
        "required": ["checklist_id"],
        "version": "v2",
    },
    "create_checklist_item": {
        "method": "POST",
        "path": "/checklist/{checklist_id}/checklist_item",
        "description": "Create a Checklist Item",
        "parameters": {
            "checklist_id": {"type": "str", "location": "path", "description": "The Checklist ID"},
            "name": {"type": "str", "location": "body", "description": "Checklist item name"},
            "assignee": {"type": "Optional[int]", "location": "body", "description": "Assignee user ID"},
        },
        "required": ["checklist_id", "name"],
        "version": "v2",
    },
    "update_checklist_item": {
        "method": "PUT",
        "path": "/checklist/{checklist_id}/checklist_item/{checklist_item_id}",
        "description": "Update a Checklist Item",
        "parameters": {
            "checklist_id": {"type": "str", "location": "path", "description": "The Checklist ID"},
            "checklist_item_id": {"type": "str", "location": "path", "description": "The Checklist Item ID"},
            "name": {"type": "Optional[str]", "location": "body", "description": "New item name"},
            "assignee": {"type": "Optional[int]", "location": "body", "description": "Assignee user ID"},
            "resolved": {"type": "Optional[bool]", "location": "body", "description": "Mark resolved/unresolved"},
            "parent": {"type": "Optional[str]", "location": "body", "description": "Parent checklist item ID (for nesting)"},
        },
        "required": ["checklist_id", "checklist_item_id"],
        "version": "v2",
    },
    "delete_checklist_item": {
        "method": "DELETE",
        "path": "/checklist/{checklist_id}/checklist_item/{checklist_item_id}",
        "description": "Delete a Checklist Item",
        "parameters": {
            "checklist_id": {"type": "str", "location": "path", "description": "The Checklist ID"},
            "checklist_item_id": {"type": "str", "location": "path", "description": "The Checklist Item ID"},
        },
        "required": ["checklist_id", "checklist_item_id"],
        "version": "v2",
    },

    # ================================================================================
    # SHARED HIERARCHY (v2)
    # ================================================================================
    "get_shared_hierarchy": {
        "method": "GET",
        "path": "/team/{team_id}/shared",
        "description": "Get the shared hierarchy for a Workspace (items shared with the authenticated user)",
        "parameters": {
            "team_id": {"type": "str", "location": "path", "description": "The Workspace (Team) ID"},
        },
        "required": ["team_id"],
        "version": "v2",
    },

    # ================================================================================
    # TASK DEPENDENCIES (v2)
    # ================================================================================
    "add_task_dependency": {
        "method": "POST",
        "path": "/task/{task_id}/dependency",
        "description": "Add a dependency relationship between Tasks",
        "parameters": {
            "task_id": {"type": "str", "location": "path", "description": "The Task ID"},
            "depends_on": {"type": "Optional[str]", "location": "body", "description": "Task ID this task depends on (waiting on)"},
            "dependency_of": {"type": "Optional[str]", "location": "body", "description": "Task ID that depends on this task (blocking)"},
            "custom_task_ids": {"type": "Optional[bool]", "location": "query", "description": "Use custom task IDs"},
            "team_id": {"type": "Optional[str]", "location": "query", "description": "Team ID (required with custom_task_ids)"},
        },
        "required": ["task_id"],
        "version": "v2",
    },
    "delete_task_dependency": {
        "method": "DELETE",
        "path": "/task/{task_id}/dependency",
        "description": "Remove a dependency from a Task",
        "parameters": {
            "task_id": {"type": "str", "location": "path", "description": "The Task ID"},
            "depends_on": {"type": "Optional[str]", "location": "query", "description": "Task ID to remove as depends_on"},
            "dependency_of": {"type": "Optional[str]", "location": "query", "description": "Task ID to remove as dependency_of"},
            "custom_task_ids": {"type": "Optional[bool]", "location": "query", "description": "Use custom task IDs"},
            "team_id": {"type": "Optional[str]", "location": "query", "description": "Team ID (required with custom_task_ids)"},
        },
        "required": ["task_id"],
        "version": "v2",
    },

    # ================================================================================
    # GUESTS (v2)
    # ================================================================================
    "invite_guest_to_workspace": {
        "method": "POST",
        "path": "/team/{team_id}/guest",
        "description": "Invite a guest to a Workspace",
        "parameters": {
            "team_id": {"type": "str", "location": "path", "description": "The Workspace (Team) ID"},
            "email": {"type": "str", "location": "body", "description": "Guest email address"},
            "can_edit_tags": {"type": "Optional[bool]", "location": "body", "description": "Allow guest to edit tags"},
            "can_see_time_spent": {"type": "Optional[bool]", "location": "body", "description": "Allow guest to see time spent"},
            "can_see_time_estimated": {"type": "Optional[bool]", "location": "body", "description": "Allow guest to see time estimates"},
        },
        "required": ["team_id", "email"],
        "version": "v2",
    },
    "get_guest": {
        "method": "GET",
        "path": "/team/{team_id}/guest/{guest_id}",
        "description": "Get a guest in a Workspace",
        "parameters": {
            "team_id": {"type": "str", "location": "path", "description": "The Workspace (Team) ID"},
            "guest_id": {"type": "str", "location": "path", "description": "The Guest ID"},
        },
        "required": ["team_id", "guest_id"],
        "version": "v2",
    },
    "remove_guest_from_workspace": {
        "method": "DELETE",
        "path": "/team/{team_id}/guest/{guest_id}",
        "description": "Remove a guest from a Workspace",
        "parameters": {
            "team_id": {"type": "str", "location": "path", "description": "The Workspace (Team) ID"},
            "guest_id": {"type": "str", "location": "path", "description": "The Guest ID"},
        },
        "required": ["team_id", "guest_id"],
        "version": "v2",
    },
}


class ClickUpDataSourceGenerator:
    """Generator for comprehensive ClickUp REST API datasource class.

    Generates methods for both v2 and v3 ClickUp API endpoints.
    The generated DataSource class accepts a ClickUpClient whose version
    setting determines the base URL.
    """

    def __init__(self):
        self.generated_methods: List[Dict[str, str]] = []

    def _sanitize_parameter_name(self, name: str) -> str:
        """Sanitize parameter names to be valid Python identifiers."""
        sanitized = name.replace("-", "_").replace(".", "_").replace("/", "_")
        if sanitized and not (sanitized[0].isalpha() or sanitized[0] == "_"):
            sanitized = f"param_{sanitized}"
        return sanitized

    def _build_query_params(self, endpoint_info: Dict) -> List[str]:
        """Build query parameter handling code."""
        lines = ["        query_params: dict[str, Any] = {}"]

        for param_name, param_info in endpoint_info["parameters"].items():
            if param_info["location"] == "query":
                sanitized_name = self._sanitize_parameter_name(param_name)

                if "Optional[bool]" in param_info["type"]:
                    lines.extend([
                        f"        if {sanitized_name} is not None:",
                        f"            query_params['{param_name}'] = str({sanitized_name}).lower()",
                    ])
                elif "Optional[int]" in param_info["type"]:
                    lines.extend([
                        f"        if {sanitized_name} is not None:",
                        f"            query_params['{param_name}'] = str({sanitized_name})",
                    ])
                elif "List[" in param_info["type"]:
                    lines.extend([
                        f"        if {sanitized_name} is not None:",
                        f"            query_params['{param_name}[]'] = {sanitized_name}",
                    ])
                else:
                    lines.extend([
                        f"        if {sanitized_name} is not None:",
                        f"            query_params['{param_name}'] = {sanitized_name}",
                    ])

        return lines

    def _build_path_formatting(self, path: str, endpoint_info: Dict) -> str:
        """Build URL path with parameter substitution."""
        path_params = [
            name
            for name, info in endpoint_info["parameters"].items()
            if info["location"] == "path"
        ]

        if path_params:
            format_dict = ", ".join(
                f"{param}={self._sanitize_parameter_name(param)}"
                for param in path_params
            )
            return f'        url = self.base_url + "{path}".format({format_dict})'
        else:
            return f'        url = self.base_url + "{path}"'

    def _build_request_body(self, endpoint_info: Dict) -> List[str]:
        """Build request body handling."""
        body_params = {
            name: info
            for name, info in endpoint_info["parameters"].items()
            if info["location"] == "body"
        }

        if not body_params:
            return []

        lines = ["        body: dict[str, Any] = {}"]

        for param_name, param_info in body_params.items():
            sanitized_name = self._sanitize_parameter_name(param_name)

            if param_name in endpoint_info["required"]:
                lines.append(f"        body['{param_name}'] = {sanitized_name}")
            else:
                lines.extend([
                    f"        if {sanitized_name} is not None:",
                    f"            body['{param_name}'] = {sanitized_name}",
                ])

        return lines

    @staticmethod
    def _modernize_type(type_str: str) -> str:
        """Convert typing-style annotations to modern Python 3.10+ syntax.

        Optional[str] -> str | None, Dict[str, Any] -> dict[str, Any],
        List[str] -> list[str], etc.
        """
        if type_str.startswith("Optional[") and type_str.endswith("]"):
            inner = type_str[len("Optional["):-1]
            inner = ClickUpDataSourceGenerator._modernize_type(inner)
            return f"{inner} | None"
        if type_str.startswith("Dict["):
            inner = type_str[len("Dict["):-1]
            parts = ClickUpDataSourceGenerator._split_type_args(inner)
            modernized = ", ".join(
                ClickUpDataSourceGenerator._modernize_type(p.strip()) for p in parts
            )
            return f"dict[{modernized}]"
        if type_str == "Dict":
            return "dict"
        if type_str.startswith("List["):
            inner = type_str[len("List["):-1]
            parts = ClickUpDataSourceGenerator._split_type_args(inner)
            modernized = ", ".join(
                ClickUpDataSourceGenerator._modernize_type(p.strip()) for p in parts
            )
            return f"list[{modernized}]"
        if type_str == "List":
            return "list"
        return type_str

    @staticmethod
    def _split_type_args(s: str) -> List[str]:
        """Split type arguments respecting nested brackets."""
        parts = []
        depth = 0
        current = ""
        for ch in s:
            if ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
            if ch == "," and depth == 0:
                parts.append(current.strip())
                current = ""
            else:
                current += ch
        if current.strip():
            parts.append(current.strip())
        return parts

    def _generate_method_signature(self, method_name: str, endpoint_info: Dict) -> str:
        """Generate method signature with explicit parameters."""
        params = ["self"]

        # Add required parameters first
        for param_name in endpoint_info["required"]:
            if param_name in endpoint_info["parameters"]:
                param_info = endpoint_info["parameters"][param_name]
                sanitized_name = self._sanitize_parameter_name(param_name)
                modern_type = self._modernize_type(param_info["type"])
                params.append(f"{sanitized_name}: {modern_type}")

        # Collect optional parameters, check if any are bool
        optional_params: List[str] = []
        has_bool_optional = False
        for param_name, param_info in endpoint_info["parameters"].items():
            if param_name not in endpoint_info["required"]:
                sanitized_name = self._sanitize_parameter_name(param_name)
                modern_type = self._modernize_type(param_info["type"])
                if "| None" not in modern_type:
                    modern_type = f"{modern_type} | None"
                optional_params.append(f"{sanitized_name}: {modern_type} = None")
                if "bool" in param_info.get("type", ""):
                    has_bool_optional = True

        # Force keyword-only if any optional param is bool (FBT001)
        if has_bool_optional and optional_params:
            params.append("*")

        params.extend(optional_params)

        signature_params = ",\n        ".join(params)
        return f"    async def {method_name}(\n        {signature_params}\n    ) -> ClickUpResponse:"

    def _generate_method_docstring(self, endpoint_info: Dict) -> List[str]:
        """Generate method docstring."""
        version = endpoint_info.get("version", "v2")
        lines = [f'        """{endpoint_info["description"]} (API {version})', ""]

        if endpoint_info["parameters"]:
            lines.append("        Args:")
            for param_name, param_info in endpoint_info["parameters"].items():
                sanitized_name = self._sanitize_parameter_name(param_name)
                lines.append(
                    f"            {sanitized_name}: {param_info['description']}"
                )
            lines.append("")

        lines.extend([
            "        Returns:",
            "            ClickUpResponse with operation result",
            '        """',
        ])

        return lines

    def _generate_method(self, method_name: str, endpoint_info: Dict) -> str:
        """Generate a complete method for an API endpoint."""
        lines = []

        # Method signature
        lines.append(self._generate_method_signature(method_name, endpoint_info))

        # Docstring
        lines.extend(self._generate_method_docstring(endpoint_info))

        # Query parameters
        has_query = any(
            info["location"] == "query"
            for info in endpoint_info["parameters"].values()
        )
        if has_query:
            query_lines = self._build_query_params(endpoint_info)
            lines.extend(query_lines)
            lines.append("")

        # URL construction
        lines.append(self._build_path_formatting(endpoint_info["path"], endpoint_info))

        # Request body
        body_lines = self._build_request_body(endpoint_info)
        if body_lines:
            lines.append("")
            lines.extend(body_lines)

        # Request construction and execution
        lines.append("")
        lines.append("        try:")
        lines.append("            request = HTTPRequest(")
        lines.append(f'                method="{endpoint_info["method"]}",')
        lines.append("                url=url,")
        lines.append('                headers={"Content-Type": "application/json"},')
        if has_query:
            lines.append("                query=query_params,")
        if body_lines:
            lines.append("                body=body,")
        lines.append("            )")
        lines.extend([
            "            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]",
            "            response_data = response.json() if response.text() else None",
            "            return ClickUpResponse(",
            "                success=response.status < HTTP_ERROR_THRESHOLD,",
            "                data=response_data,",
            f'                message="Successfully executed {method_name}" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {{response.status}}"',
            "            )",
            "        except Exception as e:",
            f'            return ClickUpResponse(success=False, error=str(e), message="Failed to execute {method_name}")',
        ])

        self.generated_methods.append({
            "name": method_name,
            "endpoint": endpoint_info["path"],
            "method": endpoint_info["method"],
            "description": endpoint_info["description"],
            "version": endpoint_info.get("version", "v2"),
        })

        return "\n".join(lines)

    def generate_clickup_datasource(self) -> str:
        """Generate the complete ClickUp datasource class."""

        class_lines = [
            "# ruff: noqa: FBT001",
            '"""',
            "ClickUp REST API DataSource - Auto-generated API wrapper",
            "",
            "Generated from ClickUp REST API v2/v3 documentation.",
            "Uses HTTP client for direct REST API interactions.",
            "All methods have explicit parameter signatures.",
            '"""',
            "",
            "from __future__ import annotations",
            "",
            "from typing import Any",
            "",
            "from app.sources.client.clickup.clickup import ClickUpClient, ClickUpResponse",
            "from app.sources.client.http.http_request import HTTPRequest",
            "",
            "# HTTP status code constant",
            "HTTP_ERROR_THRESHOLD = 400",
            "",
            "",
            "class ClickUpDataSource:",
            '    """ClickUp REST API DataSource',
            "",
            "    Provides async wrapper methods for ClickUp REST API operations:",
            "    - Workspace / Team management",
            "    - Space, Folder, List CRUD",
            "    - Task CRUD and management",
            "    - Comments, Members, Tags",
            "    - Goals, Time tracking",
            "    - Views, Webhooks, Custom Fields",
            "    - Checklists, Dependencies, Guests",
            "",
            "    The base URL is determined by the ClickUpClient's configured version",
            "    (v2 or v3). Create a client with the desired version and pass it here.",
            "",
            "    All methods return ClickUpResponse objects.",
            '    """',
            "",
            "    def __init__(self, client: ClickUpClient) -> None:",
            '        """Initialize with ClickUpClient.',
            "",
            "        Args:",
            "            client: ClickUpClient instance with configured authentication and version",
            '        """',
            "        self._client = client",
            "        self.http = client.get_client()",
            "        try:",
            "            self.base_url = self.http.get_base_url().rstrip('/')",
            "        except AttributeError as exc:",
            "            raise ValueError('HTTP client does not have get_base_url method') from exc",
            "",
            "    def get_data_source(self) -> 'ClickUpDataSource':",
            '        """Return the data source instance."""',
            "        return self",
            "",
            "    def get_client(self) -> ClickUpClient:",
            '        """Return the underlying ClickUpClient."""',
            "        return self._client",
            "",
        ]

        # Generate all API methods
        for method_name, endpoint_info in CLICKUP_API_ENDPOINTS.items():
            class_lines.append(self._generate_method(method_name, endpoint_info))
            class_lines.append("")

        return "\n".join(class_lines)

    def save_to_file(self, filename: Optional[str] = None) -> None:
        """Generate and save the ClickUp datasource to a file."""
        if filename is None:
            filename = "clickup.py"

        script_dir = Path(__file__).parent if __file__ else Path(".")
        clickup_dir = script_dir.parent / "app" / "sources" / "external" / "clickup"
        clickup_dir.mkdir(parents=True, exist_ok=True)

        full_path = clickup_dir / filename

        class_code = self.generate_clickup_datasource()

        full_path.write_text(class_code, encoding="utf-8")

        print(f"Generated ClickUp data source with {len(self.generated_methods)} methods")
        print(f"Saved to: {full_path}")

        # Print summary by category
        categories = {}
        for method in self.generated_methods:
            version = method["version"]
            key = f"API {version}"
            categories[key] = categories.get(key, 0) + 1

        print(f"\nMethods by API version:")
        for category, count in sorted(categories.items()):
            print(f"  - {category}: {count}")

        # Print resource summary
        resource_categories = {
            "User/Auth": 0,
            "Space": 0,
            "Folder": 0,
            "List": 0,
            "Task": 0,
            "Comment": 0,
            "Member": 0,
            "Tag": 0,
            "Goal": 0,
            "Time Tracking": 0,
            "View": 0,
            "Custom Field": 0,
            "Webhook": 0,
            "Checklist": 0,
            "Dependency": 0,
            "Guest": 0,
            "Shared": 0,
        }

        for method in self.generated_methods:
            name = method["name"]
            if "authorized" in name or "user" in name:
                resource_categories["User/Auth"] += 1
            elif "space" in name and "tag" not in name and "view" not in name:
                resource_categories["Space"] += 1
            elif "folder" in name and "view" not in name and "list" not in name:
                resource_categories["Folder"] += 1
            elif "list" in name and "checklist" not in name and "comment" not in name and "member" not in name and "view" not in name and "field" not in name:
                resource_categories["List"] += 1
            elif ("task" in name and "comment" not in name and "member" not in name
                  and "tag" not in name and "time" not in name and "field" not in name
                  and "dependency" not in name and "checklist" not in name and "view" not in name):
                resource_categories["Task"] += 1
            elif "comment" in name:
                resource_categories["Comment"] += 1
            elif "member" in name:
                resource_categories["Member"] += 1
            elif "tag" in name:
                resource_categories["Tag"] += 1
            elif "goal" in name:
                resource_categories["Goal"] += 1
            elif "time" in name:
                resource_categories["Time Tracking"] += 1
            elif "view" in name:
                resource_categories["View"] += 1
            elif "field" in name:
                resource_categories["Custom Field"] += 1
            elif "webhook" in name:
                resource_categories["Webhook"] += 1
            elif "checklist" in name:
                resource_categories["Checklist"] += 1
            elif "dependency" in name:
                resource_categories["Dependency"] += 1
            elif "guest" in name:
                resource_categories["Guest"] += 1
            elif "shared" in name:
                resource_categories["Shared"] += 1

        print(f"\nMethods by Resource:")
        for category, count in resource_categories.items():
            if count > 0:
                print(f"  - {category}: {count}")


def main():
    """Main function for ClickUp data source generator."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Generate ClickUp REST API data source"
    )
    parser.add_argument("--filename", "-f", help="Output filename (optional)")

    args = parser.parse_args()

    try:
        generator = ClickUpDataSourceGenerator()
        generator.save_to_file(args.filename)
        return 0
    except Exception as e:
        print(f"Failed to generate ClickUp data source: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
