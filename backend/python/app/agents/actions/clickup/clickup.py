import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from app.agents.tools.config import ToolCategory
from app.agents.tools.decorator import tool
from app.agents.tools.models import ToolIntent
from app.connectors.core.registry.auth_builder import (
    AuthBuilder,
    AuthType,
    OAuthScopeConfig,
)
from app.connectors.core.registry.connector_builder import CommonFields
from app.connectors.core.registry.tool_builder import (
    ToolsetBuilder,
    ToolsetCategory,
)
from app.sources.client.clickup.clickup import ClickUpClient
from app.sources.external.clickup.clickup import ClickUpDataSource

logger = logging.getLogger(__name__)

CLICKUP_APP_BASE = "https://app.clickup.com"


def _build_clickup_web_url(entity: str, team_id: str, **kwargs: str) -> str:
    """Build ClickUp app web URL for an entity. All IDs as strings."""
    if entity == "workspace":
        return f"{CLICKUP_APP_BASE}/{team_id}/home"
    if entity == "space":
        return f"{CLICKUP_APP_BASE}/{team_id}/v/o/s/{kwargs.get('space_id', '')}"
    if entity == "folder":
        return f"{CLICKUP_APP_BASE}/{team_id}/v/o/f/{kwargs.get('folder_id', '')}?pr={kwargs.get('space_id', '')}"
    if entity == "list":
        return f"{CLICKUP_APP_BASE}/{team_id}/v/l/li/{kwargs.get('list_id', '')}?pr={kwargs.get('folder_id', '')}"
    if entity == "doc":
        return f"{CLICKUP_APP_BASE}/{team_id}/v/dc/{kwargs.get('doc_id', '')}"
    if entity == "page":
        return f"{CLICKUP_APP_BASE}/{team_id}/v/dc/{kwargs.get('doc_id', '')}/{kwargs.get('page_id', '')}"
    return ""


# ---------------------------------------------------------------------------
# Pydantic input schemas
# ---------------------------------------------------------------------------

class GetSpacesInput(BaseModel):
    """Schema for getting spaces in a workspace."""
    team_id: str = Field(description="Workspace (team) ID. Get from get_authorized_teams_workspaces.")
    archived: Optional[bool] = Field(default=None, description="Include archived spaces")


class GetFoldersInput(BaseModel):
    """Schema for getting folders in a space."""
    space_id: str = Field(description="Space ID. Get from get_spaces.")
    team_id: str = Field(description="Workspace (team) ID. Get from get_authorized_teams_workspaces. Required for folder web_url.")
    archived: Optional[bool] = Field(default=None, description="Include archived folders")


class GetListsInput(BaseModel):
    """Schema for getting lists in a folder."""
    folder_id: str = Field(description="Folder ID. Get from get_folders.")
    team_id: str = Field(description="Workspace (team) ID. Get from get_authorized_teams_workspaces. Required for list web_url.")
    archived: Optional[bool] = Field(default=None, description="Include archived lists")


class GetFolderlessListsInput(BaseModel):
    """Schema for getting folderless lists in a space."""
    space_id: str = Field(description="Space ID. Get from get_spaces.")
    team_id: str = Field(description="Workspace (team) ID. Get from get_authorized_teams_workspaces. Required for list web_url.")
    archived: Optional[bool] = Field(default=None, description="Include archived lists")


class GetTasksInput(BaseModel):
    """Schema for getting tasks in a list."""
    list_id: str = Field(description="List ID. Get from get_lists or get_folderless_lists.")
    archived: Optional[bool] = Field(default=None, description="Include archived tasks")
    page: Optional[int] = Field(default=None, description="Page number (0-based). 100 tasks per page.")
    order_by: Optional[str] = Field(default=None, description="Order by: id, created, updated, due_date")
    reverse: Optional[bool] = Field(default=None, description="Reverse sort order")
    subtasks: Optional[bool] = Field(default=None, description="Include subtasks")
    statuses: Optional[List[str]] = Field(default=None, description="Filter by status names")
    include_closed: Optional[bool] = Field(default=None, description="Include closed tasks")
    assignees: Optional[List[str]] = Field(default=None, description="Filter by assignee user IDs")
    tags: Optional[List[str]] = Field(default=None, description="Filter by tag names")
    due_date_gt: Optional[int] = Field(default=None, description="Filter tasks due after (Unix ms)")
    due_date_lt: Optional[int] = Field(default=None, description="Filter tasks due before (Unix ms)")
    date_created_gt: Optional[int] = Field(default=None, description="Filter tasks created after (Unix ms)")
    date_created_lt: Optional[int] = Field(default=None, description="Filter tasks created before (Unix ms)")
    date_updated_gt: Optional[int] = Field(default=None, description="Filter tasks updated after (Unix ms)")
    date_updated_lt: Optional[int] = Field(default=None, description="Filter tasks updated before (Unix ms)")
    custom_fields: Optional[List[Dict[str, Any]]] = Field(default=None, description="Filter by custom field values")


class GetTaskInput(BaseModel):
    """Schema for getting a specific task."""
    task_id: str = Field(description="Task ID. Get from get_tasks or create_task.")


class CreateTaskInput(BaseModel):
    """Schema for creating a task."""
    list_id: str = Field(description="List ID. Get from get_lists or get_folderless_lists.")
    name: str = Field(description="Task name")
    description: Optional[str] = Field(default=None, description="Task description")
    status: Optional[str] = Field(default=None, description="Status name (optional)")


class UpdateTaskInput(BaseModel):
    """Schema for updating a task."""
    task_id: str = Field(description="Task ID. Get from get_tasks or create_task.")
    name: Optional[str] = Field(default=None, description="New task name (omit to leave unchanged)")
    description: Optional[str] = Field(default=None, description="New task description, plain text (omit to leave unchanged)")
    markdown_description: Optional[str] = Field(default=None, description="New task description in markdown (omit to leave unchanged)")
    status: Optional[str] = Field(default=None, description="New status name (omit to leave unchanged)")
    priority: Optional[int] = Field(default=None, description="Priority: 1=Urgent, 2=High, 3=Normal, 4=Low (omit to leave unchanged)")
    due_date: Optional[int] = Field(default=None, description="Due date as Unix timestamp in ms (omit to leave unchanged)")
    due_date_time: Optional[bool] = Field(default=None, description="Include time in due date (omit to leave unchanged)")
    time_estimate: Optional[int] = Field(default=None, description="Time estimate in milliseconds (omit to leave unchanged)")
    start_date: Optional[int] = Field(default=None, description="Start date as Unix timestamp in ms (omit to leave unchanged)")
    start_date_time: Optional[bool] = Field(default=None, description="Include time in start date (omit to leave unchanged)")
    assignees_add: Optional[List[int]] = Field(default=None, description="User IDs to add as assignees")
    assignees_rem: Optional[List[int]] = Field(default=None, description="User IDs to remove from assignees")
    archived: Optional[bool] = Field(default=None, description="Archive or unarchive the task")
    custom_task_ids: Optional[bool] = Field(default=None, description="Use custom task IDs; requires team_id if true")
    team_id: Optional[str] = Field(default=None, description="Team ID (required when custom_task_ids is true)")


class GetWorkspaceDocsInput(BaseModel):
    """Schema for listing docs in a workspace (ClickUp Docs API v3)."""
    workspace_id: str = Field(description="Workspace ID. Same as team id from get_authorized_teams_workspaces.")


class GetDocPagesInput(BaseModel):
    """Schema for listing pages in a doc (ClickUp Docs API v3)."""
    workspace_id: str = Field(description="Workspace ID. Same as team id from get_authorized_teams_workspaces.")
    doc_id: str = Field(description="Doc ID. Get from get_workspace_docs.")


class GetDocPageInput(BaseModel):
    """Schema for getting one page details (ClickUp Docs API v3)."""
    workspace_id: str = Field(description="Workspace ID. Same as team id from get_authorized_teams_workspaces.")
    doc_id: str = Field(description="Doc ID. Get from get_workspace_docs.")
    page_id: str = Field(description="Page ID. Get from get_doc_pages.")


class GetWorkspaceTasksInput(BaseModel):
    """Schema for searching/filtering tasks across a workspace (team)."""
    team_id: str = Field(description="Workspace (team) ID. Get from get_authorized_teams_workspaces.")
    page: Optional[int] = Field(default=None, description="Page number (0-based). 100 tasks per page.")
    order_by: Optional[str] = Field(default=None, description="Order by: created, updated, due_date, start_date")
    reverse: Optional[bool] = Field(default=None, description="Reverse sort order")
    subtasks: Optional[bool] = Field(default=None, description="Include subtasks")
    statuses: Optional[List[str]] = Field(default=None, description="Filter by status names")
    include_closed: Optional[bool] = Field(default=None, description="Include closed tasks")
    assignees: Optional[List[str]] = Field(default=None, description="Filter by assignee user IDs")
    tags: Optional[List[str]] = Field(default=None, description="Filter by tag names")
    due_date_gt: Optional[int] = Field(default=None, description="Filter tasks due after (Unix ms)")
    due_date_lt: Optional[int] = Field(default=None, description="Filter tasks due before (Unix ms)")
    date_created_gt: Optional[int] = Field(default=None, description="Filter tasks created after (Unix ms)")
    date_created_lt: Optional[int] = Field(default=None, description="Filter tasks created before (Unix ms)")
    date_updated_gt: Optional[int] = Field(default=None, description="Filter tasks updated after (Unix ms)")
    date_updated_lt: Optional[int] = Field(default=None, description="Filter tasks updated before (Unix ms)")
    space_ids: Optional[List[str]] = Field(default=None, description="Filter by space IDs")
    project_ids: Optional[List[str]] = Field(default=None, description="Filter by folder (project) IDs")
    list_ids: Optional[List[str]] = Field(default=None, description="Filter by list IDs")
    custom_fields: Optional[List[Dict[str, Any]]] = Field(default=None, description="Filter by custom field values")


class SearchTasksInput(BaseModel):
    """Schema for keyword search across workspace tasks (creates temporary view, returns tasks, deletes view)."""
    team_id: str = Field(description="Workspace (team) ID. Get from get_authorized_teams_workspaces.")
    keyword: str = Field(description="Search string for task name, description, and custom field text.")
    show_closed: bool = Field(default=False, description="Include closed tasks in search results")
    page: Optional[int] = Field(default=None, description="Page number (0-based). 100 tasks per page.")


# Register ClickUp toolset (OAuth only); tools are auto-discovered from @tool decorators
@ToolsetBuilder("ClickUp") \
    .in_group("Project Management") \
    .with_description("ClickUp integration for tasks, lists, spaces, and workspaces") \
    .with_category(ToolsetCategory.APP) \
    .with_auth([
        AuthBuilder.type(AuthType.OAUTH).oauth(
            connector_name="ClickUp",
            authorize_url="https://app.clickup.com/api",
            token_url="https://api.clickup.com/api/v2/oauth/token",
            redirect_uri="toolsets/oauth/callback/clickup",
            scopes=OAuthScopeConfig(
                personal_sync=[],
                team_sync=[],
                agent=[],
            ),
            fields=[
                CommonFields.client_id("ClickUp OAuth App"),
                CommonFields.client_secret("ClickUp OAuth App"),
            ],
            icon_path="/assets/icons/connectors/clickup.svg",
            app_group="Project Management",
            app_description="ClickUp OAuth application for agent integration",
        )
    ]) \
    .configure(lambda builder: builder.with_icon("/assets/icons/connectors/clickup.svg")) \
    .build_decorator()
class ClickUp:
    """ClickUp tool exposed to the agents using ClickUpDataSource."""

    def __init__(self, client: ClickUpClient) -> None:
        """Initialize the ClickUp tool.

        Args:
            client: ClickUp client object
        """
        self.client = ClickUpDataSource(client)

    def _handle_response(self, response, data_override: Any = None) -> Tuple[bool, str]:
        """Return (success, json_string). If data_override is set, serialize with it instead of response.data."""
        if data_override is not None:
            payload = {
                "success": response.success,
                "data": data_override,
                "message": response.message,
            }
            if getattr(response, "error", None) is not None:
                payload["error"] = response.error
            return (response.success, json.dumps(payload))
        if response.success:
            return True, response.to_json()
        return False, response.to_json()

    @tool(
        app_name="clickup",
        tool_name="get_authorized_user",
        description="Get the authorized ClickUp user details.",
        llm_description="Returns the authenticated ClickUp user (id, username, email). Use to confirm who is logged in or get user context.",
        parameters=[],
        returns="JSON with the authenticated user details (id, username, email, etc.)",
        primary_intent=ToolIntent.SEARCH,
        category=ToolCategory.PROJECT_MANAGEMENT,
        when_to_use=[
            "User wants to know who is logged in to ClickUp",
            "User asks for their ClickUp profile or account details",
        ],
        when_not_to_use=[
            "User wants workspaces/teams (use get_authorized_teams_workspaces)",
            "User wants spaces, lists, or tasks (use get_spaces, get_lists, get_tasks)",
        ],
        typical_queries=["Who am I in ClickUp?", "Get my ClickUp profile", "Which account is connected?"],
    )
    async def get_authorized_user(self) -> Tuple[bool, str]:
        """Get the authorized ClickUp user details."""
        try:
            response = await self.client.get_authorized_user()
            return self._handle_response(response)
        except Exception as e:
            logger.error(f"Error in get_authorized_user: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="clickup",
        tool_name="get_authorized_teams_workspaces",
        description="Get the authorized teams (workspaces) for the authenticated user.",
        llm_description="Returns list of ClickUp workspaces (teams). Use the returned team id as team_id in get_spaces. Call this first when user asks for spaces, folders, or lists.",
        parameters=[],
        returns="JSON with list of workspaces (teams); use team id for get_spaces.",
        primary_intent=ToolIntent.SEARCH,
        category=ToolCategory.PROJECT_MANAGEMENT,
        when_to_use=[
            "User wants to list ClickUp workspaces or teams",
            "User needs team_id to list spaces (call this first, then get_spaces)",
        ],
        when_not_to_use=[
            "User wants user profile only (use get_authorized_user)",
            "User already has team_id and wants spaces (use get_spaces)",
        ],
        typical_queries=["List my ClickUp workspaces", "Show teams", "What workspaces do I have?"],
    )
    async def get_authorized_teams_workspaces(self) -> Tuple[bool, str]:
        """Get the authorized teams (workspaces)."""
        try:
            response = await self.client.get_authorized_teams_workspaces()
            if not response.success:
                return self._handle_response(response)
            data = response.data if response.data is not None else {}
            if isinstance(data, dict):
                for item in data.get("teams") or []:
                    if isinstance(item, dict) and item.get("id") is not None:
                        item["web_url"] = _build_clickup_web_url("workspace", team_id=str(item["id"]))
            return self._handle_response(response, data_override=data)
        except Exception as e:
            logger.error(f"Error in get_authorized_teams_workspaces: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="clickup",
        tool_name="get_spaces",
        description="Get all spaces in a workspace (team).",
        llm_description="Returns spaces in a workspace. Need team_id from get_authorized_teams_workspaces. Use returned space id for get_folders or get_folderless_lists.",
        args_schema=GetSpacesInput,
        returns="JSON with list of spaces in the workspace",
        primary_intent=ToolIntent.SEARCH,
        category=ToolCategory.PROJECT_MANAGEMENT,
        when_to_use=[
            "User wants to list spaces in a ClickUp workspace",
            "User needs space_id for folders or lists (call get_authorized_teams_workspaces first for team_id)",
        ],
        when_not_to_use=[
            "User wants workspaces/teams list (use get_authorized_teams_workspaces)",
            "User wants folders in a space (use get_folders with space_id)",
        ],
        typical_queries=["List spaces in my workspace", "Show ClickUp spaces", "What spaces do I have?"],
    )
    async def get_spaces(
        self,
        team_id: str,
        archived: Optional[bool] = None,
    ) -> Tuple[bool, str]:
        """Get all spaces in a workspace."""
        try:
            response = await self.client.get_spaces(team_id, archived=archived)
            if not response.success:
                return self._handle_response(response)
            data = response.data if response.data is not None else {}
            if isinstance(data, dict) and team_id:
                for item in data.get("spaces") or []:
                    if isinstance(item, dict) and item.get("id") is not None:
                        item["web_url"] = _build_clickup_web_url("space", team_id=team_id, space_id=str(item["id"]))
            return self._handle_response(response, data_override=data)
        except Exception as e:
            logger.error(f"Error in get_spaces: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="clickup",
        tool_name="get_folders",
        description="Get all folders in a space.",
        llm_description="Returns folders in a space. Need space_id from get_spaces. Use returned folder id for get_lists.",
        args_schema=GetFoldersInput,
        returns="JSON with list of folders in the space",
        primary_intent=ToolIntent.SEARCH,
        category=ToolCategory.PROJECT_MANAGEMENT,
        when_to_use=[
            "User wants to list folders in a ClickUp space",
            "User needs folder_id for get_lists (call get_spaces first for space_id)",
        ],
        when_not_to_use=[
            "User wants folderless lists (use get_folderless_lists with space_id)",
            "User wants lists inside a folder (use get_lists with folder_id)",
        ],
        typical_queries=["List folders in this space", "Show folders", "What folders are in the space?"],
    )
    async def get_folders(
        self,
        space_id: str,
        team_id: str,
        archived: Optional[bool] = None,
    ) -> Tuple[bool, str]:
        """Get all folders in a space."""
        try:
            response = await self.client.get_folders(space_id, archived=archived)
            if not response.success:
                return self._handle_response(response)
            data = response.data if response.data is not None else {}
            if isinstance(data, dict) and team_id and space_id:
                for item in data.get("folders") or []:
                    if isinstance(item, dict) and item.get("id") is not None:
                        item["web_url"] = _build_clickup_web_url(
                            "folder", team_id=team_id, space_id=space_id, folder_id=str(item["id"])
                        )
            return self._handle_response(response, data_override=data)
        except Exception as e:
            logger.error(f"Error in get_folders: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="clickup",
        tool_name="get_lists",
        description="Get all lists in a folder.",
        llm_description="Returns lists inside a folder. Need folder_id from get_folders. Use returned list id for get_tasks or create_task.",
        args_schema=GetListsInput,
        returns="JSON with list of lists in the folder",
        primary_intent=ToolIntent.SEARCH,
        category=ToolCategory.PROJECT_MANAGEMENT,
        when_to_use=[
            "User wants to list lists in a ClickUp folder",
            "User needs list_id for get_tasks or create_task (call get_folders first for folder_id)",
        ],
        when_not_to_use=[
            "User wants lists not in a folder (use get_folderless_lists with space_id)",
            "User wants tasks in a list (use get_tasks with list_id)",
        ],
        typical_queries=["List lists in this folder", "Show lists", "What lists are in the folder?"],
    )
    async def get_lists(
        self,
        folder_id: str,
        team_id: str,
        archived: Optional[bool] = None,
    ) -> Tuple[bool, str]:
        """Get all lists in a folder."""
        try:
            response = await self.client.get_lists(folder_id, archived=archived)
            if not response.success:
                return self._handle_response(response)
            data = response.data if response.data is not None else {}
            if isinstance(data, dict) and team_id and folder_id:
                for item in data.get("lists") or []:
                    if isinstance(item, dict) and item.get("id") is not None:
                        item["web_url"] = _build_clickup_web_url(
                            "list", team_id=team_id, list_id=str(item["id"]), folder_id=folder_id
                        )
            return self._handle_response(response, data_override=data)
        except Exception as e:
            logger.error(f"Error in get_lists: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="clickup",
        tool_name="get_folderless_lists",
        description="Get folderless lists in a space.",
        llm_description="Returns lists that are not inside a folder. Need space_id from get_spaces. Use returned list id for get_tasks or create_task.",
        args_schema=GetFolderlessListsInput,
        returns="JSON with list of folderless lists in the space",
        primary_intent=ToolIntent.SEARCH,
        category=ToolCategory.PROJECT_MANAGEMENT,
        when_to_use=[
            "User wants to list lists that are not inside a folder",
            "User needs list_id and space has no folders (call get_spaces first for space_id)",
        ],
        when_not_to_use=[
            "User wants lists inside a folder (use get_lists with folder_id)",
            "User wants folders (use get_folders)",
        ],
        typical_queries=["List folderless lists", "Show lists without folder", "Lists at space level"],
    )
    async def get_folderless_lists(
        self,
        space_id: str,
        team_id: str,
        archived: Optional[bool] = None,
    ) -> Tuple[bool, str]:
        """Get folderless lists in a space."""
        try:
            response = await self.client.get_folderless_lists(space_id, archived=archived)
            if not response.success:
                return self._handle_response(response)
            data = response.data if response.data is not None else {}
            if isinstance(data, dict) and team_id and space_id:
                for item in data.get("lists") or []:
                    if isinstance(item, dict) and item.get("id") is not None:
                        item["web_url"] = _build_clickup_web_url(
                            "list", team_id=team_id, list_id=str(item["id"]), folder_id=space_id
                        )
            return self._handle_response(response, data_override=data)
        except Exception as e:
            logger.error(f"Error in get_folderless_lists: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="clickup",
        tool_name="get_tasks",
        description="Get tasks in a list, with optional filters by status, assignee, tags, dates.",
        llm_description="Returns tasks in a list. Need list_id from get_lists or get_folderless_lists. Supports optional filters (statuses, assignees, tags, dates). Use returned task id for get_task or update_task.",
        args_schema=GetTasksInput,
        returns="JSON with list of tasks in the list",
        primary_intent=ToolIntent.SEARCH,
        category=ToolCategory.PROJECT_MANAGEMENT,
        when_to_use=[
            "User wants to list tasks in a ClickUp list",
            "User wants tasks in a specific list with optional filters (status, assignee, tags, dates)",
            "User needs task_id for get_task or update_task (call get_lists or get_folderless_lists first for list_id)",
        ],
        when_not_to_use=[
            "User wants tasks across the whole workspace (use get_workspace_tasks)",
            "User wants a single task by id (use get_task)",
            "User wants to create a task (use create_task)",
        ],
        typical_queries=["List tasks in this list", "Show my tasks", "What tasks are in the list?", "Tasks in list X with status To Do"],
    )
    async def get_tasks(
        self,
        list_id: str,
        archived: Optional[bool] = None,
        page: Optional[int] = None,
        order_by: Optional[str] = None,
        reverse: Optional[bool] = None,
        subtasks: Optional[bool] = None,
        statuses: Optional[List[str]] = None,
        include_closed: Optional[bool] = None,
        assignees: Optional[List[str]] = None,
        tags: Optional[List[str]] = None,
        due_date_gt: Optional[int] = None,
        due_date_lt: Optional[int] = None,
        date_created_gt: Optional[int] = None,
        date_created_lt: Optional[int] = None,
        date_updated_gt: Optional[int] = None,
        date_updated_lt: Optional[int] = None,
        custom_fields: Optional[List[Dict[str, Any]]] = None,
    ) -> Tuple[bool, str]:
        """Get tasks in a list."""
        try:
            response = await self.client.get_tasks(
                list_id,
                archived=archived,
                page=page,
                order_by=order_by,
                reverse=reverse,
                subtasks=subtasks,
                statuses=statuses,
                include_closed=include_closed,
                assignees=assignees,
                tags=tags,
                due_date_gt=due_date_gt,
                due_date_lt=due_date_lt,
                date_created_gt=date_created_gt,
                date_created_lt=date_created_lt,
                date_updated_gt=date_updated_gt,
                date_updated_lt=date_updated_lt,
                custom_fields=custom_fields,
            )
            return self._handle_response(response)
        except Exception as e:
            logger.error(f"Error in get_tasks: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="clickup",
        tool_name="get_workspace_tasks",
        description="Search/filter tasks across the whole workspace (team) by status, assignee, tags, dates, or scope.",
        llm_description="Returns tasks across a workspace matching filters. Need team_id from get_authorized_teams_workspaces. For 'assigned to me' or 'my tasks', call get_authorized_user first and pass assignees=[user_id]. Use for one workspace only (pick by name from get_authorized_teams_workspaces). 100 tasks per page; use page for more.",
        args_schema=GetWorkspaceTasksInput,
        returns="JSON with list of tasks in the workspace matching filters",
        primary_intent=ToolIntent.SEARCH,
        category=ToolCategory.PROJECT_MANAGEMENT,
        when_to_use=[
            "User wants tasks across the whole workspace matching criteria",
            "User asks for tasks in workspace by status, assignee, tags, or dates without specifying a single list",
            "User wants all tasks assigned to someone or with a tag in the workspace",
            "User asks for 'my tasks' or 'tasks assigned to me' in a workspace (use get_authorized_user for user id, then this with assignees=[user_id])",
        ],
        when_not_to_use=[
            "User wants tasks in a specific list (use get_tasks with list_id)",
            "User wants a single task by id (use get_task)",
        ],
        typical_queries=["Tasks in workspace with status In Progress", "All tasks assigned to me", "Tasks with tag urgent in workspace"],
    )
    async def get_workspace_tasks(
        self,
        team_id: str,
        page: Optional[int] = None,
        order_by: Optional[str] = None,
        reverse: Optional[bool] = None,
        subtasks: Optional[bool] = None,
        statuses: Optional[List[str]] = None,
        include_closed: Optional[bool] = None,
        assignees: Optional[List[str]] = None,
        tags: Optional[List[str]] = None,
        due_date_gt: Optional[int] = None,
        due_date_lt: Optional[int] = None,
        date_created_gt: Optional[int] = None,
        date_created_lt: Optional[int] = None,
        date_updated_gt: Optional[int] = None,
        date_updated_lt: Optional[int] = None,
        space_ids: Optional[List[str]] = None,
        project_ids: Optional[List[str]] = None,
        list_ids: Optional[List[str]] = None,
        custom_fields: Optional[List[Dict[str, Any]]] = None,
    ) -> Tuple[bool, str]:
        """Search/filter tasks across the whole workspace."""
        try:
            # API expects assignees as list of strings; normalize in case caller passes ints
            assignees_out = None
            if assignees:
                assignees_out = [str(a) for a in assignees]
            response = await self.client.get_filtered_team_tasks(
                team_id,
                page=page,
                order_by=order_by,
                reverse=reverse,
                subtasks=subtasks,
                statuses=statuses,
                include_closed=include_closed,
                assignees=assignees_out,
                tags=tags,
                due_date_gt=due_date_gt,
                due_date_lt=due_date_lt,
                date_created_gt=date_created_gt,
                date_created_lt=date_created_lt,
                date_updated_gt=date_updated_gt,
                date_updated_lt=date_updated_lt,
                space_ids=space_ids,
                project_ids=project_ids,
                list_ids=list_ids,
                custom_fields=custom_fields,
            )
            return self._handle_response(response)
        except Exception as e:
            logger.error(f"Error in get_workspace_tasks: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="clickup",
        tool_name="search_tasks",
        description="Search tasks by keyword or phrase in name, description, and custom field text.",
        llm_description="Returns tasks matching a keyword/phrase across the workspace. Creates a temporary view, fetches tasks, then deletes the view. Use for free-text search (e.g. 'login bug', 'invoice'). Get team_id from get_authorized_teams_workspaces. Prefer this over get_workspace_tasks when user asks for tasks containing specific text.",
        args_schema=SearchTasksInput,
        returns="JSON with list of tasks matching the keyword",
        primary_intent=ToolIntent.SEARCH,
        category=ToolCategory.PROJECT_MANAGEMENT,
        when_to_use=[
            "User wants tasks containing specific text or keyword",
            "User asks for tasks with a word or phrase in name/description (e.g. 'login bug', 'invoice')",
        ],
        when_not_to_use=[
            "User wants to filter by status/assignee/tags/dates only (use get_workspace_tasks)",
            "User wants tasks in a specific list (use get_tasks)",
        ],
        typical_queries=["Tasks containing login bug", "Find tasks with invoice", "Search for tasks named X"],
    )
    async def search_tasks(
        self,
        team_id: str,
        keyword: str,
        show_closed: bool = False,
        page: Optional[int] = None,
    ) -> Tuple[bool, str]:
        """Search tasks by keyword via temporary workspace view."""
        view_id = None
        try:
            create_resp = await self.client.create_team_view(
                team_id,
                name=f"Search: {keyword[:50]}" if len(keyword) > 50 else f"Search: {keyword}",
                search=keyword,
                show_closed=show_closed,
            )
            if not create_resp.success or not create_resp.data:
                return self._handle_response(create_resp)
            data = create_resp.data if isinstance(create_resp.data, dict) else {}
            view = data.get("view") or {}
            view_id = view.get("id")
            if view_id is None:
                return False, json.dumps({"error": "Create view did not return view id", "data": data})
            view_id = str(view_id)
            tasks_resp = await self.client.get_view_tasks(view_id, page=page)
            result = self._handle_response(tasks_resp)
        except Exception as e:
            logger.error(f"Error in search_tasks: {e}")
            result = False, json.dumps({"error": str(e)})
        finally:
            if view_id:
                try:
                    await self.client.delete_view(view_id)
                except Exception as cleanup_e:
                    logger.warning(f"search_tasks: failed to delete temporary view {view_id}: {cleanup_e}")
        return result

    @tool(
        app_name="clickup",
        tool_name="get_task",
        description="Get a specific task by ID.",
        llm_description="Returns one task by task_id. Get task_id from get_tasks or create_task. Use for 'show task X', 'details of task Y'.",
        args_schema=GetTaskInput,
        returns="JSON with task details",
        primary_intent=ToolIntent.SEARCH,
        category=ToolCategory.PROJECT_MANAGEMENT,
        when_to_use=[
            "User wants details of a specific ClickUp task",
            "User asks for a task by ID (get task_id from get_tasks or create_task)",
        ],
        when_not_to_use=[
            "User wants all tasks in a list (use get_tasks)",
            "User wants to create a task (use create_task)",
        ],
        typical_queries=["Get task abc123", "Show task details", "What is the status of this task?"],
    )
    async def get_task(self, task_id: str) -> Tuple[bool, str]:
        """Get a specific task."""
        try:
            response = await self.client.get_task(task_id)
            return self._handle_response(response)
        except Exception as e:
            logger.error(f"Error in get_task: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="clickup",
        tool_name="create_task",
        description="Create a new task in a list.",
        llm_description="Creates a task. Need list_id from get_lists or get_folderless_lists; name is required. Optional: description, status. Returns the created task including task id.",
        args_schema=CreateTaskInput,
        returns="JSON with the created task details including task id",
        primary_intent=ToolIntent.ACTION,
        category=ToolCategory.PROJECT_MANAGEMENT,
        when_to_use=[
            "User wants to create a new ClickUp task",
            "User asks to add a task to a list (need list_id from get_lists or get_folderless_lists)",
        ],
        when_not_to_use=[
            "User wants to list or get tasks (use get_tasks or get_task)",
            "User wants to update a task (use update_task)",
        ],
        typical_queries=["Create a task", "Add a task to the list", "New task: Fix login bug"],
    )
    async def create_task(
        self,
        list_id: str,
        name: str,
        description: Optional[str] = None,
        status: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """Create a new task in a list."""
        try:
            response = await self.client.create_task(
                list_id,
                name,
                description=description,
                status=status,
            )
            return self._handle_response(response)
        except Exception as e:
            logger.error(f"Error in create_task: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="clickup",
        tool_name="update_task",
        description="Update an existing task (name, description, status, priority, dates, assignees, archived).",
        llm_description="Updates a task. Need task_id from get_tasks or create_task. Pass only fields to change (name, description, status, priority, due_date, start_date, assignees_add/assignees_rem, archived, etc.); omit others to leave unchanged.",
        args_schema=UpdateTaskInput,
        returns="JSON with the updated task details",
        primary_intent=ToolIntent.ACTION,
        category=ToolCategory.PROJECT_MANAGEMENT,
        when_to_use=[
            "User wants to edit or update a ClickUp task",
            "User asks to change task name, description, status, priority, due date, assignees, or archive (need task_id from get_tasks or create_task)",
        ],
        when_not_to_use=[
            "User wants to create a task (use create_task)",
            "User wants to read task details (use get_task)",
        ],
        typical_queries=["Update task abc123", "Change task status to Done", "Edit task name", "Set due date", "Add assignee"],
    )
    async def update_task(
        self,
        task_id: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        markdown_description: Optional[str] = None,
        status: Optional[str] = None,
        priority: Optional[int] = None,
        due_date: Optional[int] = None,
        due_date_time: Optional[bool] = None,
        time_estimate: Optional[int] = None,
        start_date: Optional[int] = None,
        start_date_time: Optional[bool] = None,
        assignees_add: Optional[List[int]] = None,
        assignees_rem: Optional[List[int]] = None,
        archived: Optional[bool] = None,
        custom_task_ids: Optional[bool] = None,
        team_id: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """Update an existing task."""
        logger.info(
            "clickup update_task: task_id=%s assignees_add=%s assignees_rem=%s (name=%s status=%s priority=%s)",
            task_id, assignees_add, assignees_rem, name, status, priority,
        )
        try:
            response = await self.client.update_task(
                task_id,
                name=name,
                description=description,
                markdown_description=markdown_description,
                status=status,
                priority=priority,
                due_date=due_date,
                due_date_time=due_date_time,
                time_estimate=time_estimate,
                start_date=start_date,
                start_date_time=start_date_time,
                assignees_add=assignees_add,
                assignees_rem=assignees_rem,
                archived=archived,
                custom_task_ids=custom_task_ids,
                team_id=team_id,
            )
            return self._handle_response(response)
        except Exception as e:
            logger.error(f"Error in update_task: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="clickup",
        tool_name="get_workspace_docs",
        description="List all docs in a ClickUp workspace (Docs API v3).",
        llm_description="Returns all docs in a workspace. Use workspace_id (same as team id from get_authorized_teams_workspaces). Use returned doc ids for get_doc_pages.",
        args_schema=GetWorkspaceDocsInput,
        returns="JSON with list of docs in the workspace",
        primary_intent=ToolIntent.SEARCH,
        category=ToolCategory.PROJECT_MANAGEMENT,
        when_to_use=[
            "User wants to list ClickUp docs in a workspace",
            "User needs doc_id to list pages or get page details (call get_authorized_teams_workspaces first for workspace_id)",
        ],
        when_not_to_use=[
            "User wants tasks or lists (use get_tasks, get_lists, etc.)",
            "User wants pages inside a doc (use get_doc_pages with doc_id)",
        ],
        typical_queries=["List docs in workspace", "Show all docs", "What docs do we have?"],
    )
    async def get_workspace_docs(self, workspace_id: str) -> Tuple[bool, str]:
        """List all docs in a workspace."""
        try:
            response = await self.client.get_workspace_docs(workspace_id)
            if not response.success:
                return self._handle_response(response)
            data = response.data if response.data is not None else {}
            if isinstance(data, dict) and workspace_id:
                docs_list = data.get("docs") or data.get("data") or []
                if isinstance(docs_list, list):
                    for item in docs_list:
                        if isinstance(item, dict) and item.get("id") is not None:
                            item["web_url"] = _build_clickup_web_url(
                                "doc", team_id=workspace_id, doc_id=str(item["id"])
                            )
            return self._handle_response(response, data_override=data)
        except Exception as e:
            logger.error(f"Error in get_workspace_docs: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="clickup",
        tool_name="get_doc_pages",
        description="List pages in a ClickUp doc (Docs API v3).",
        llm_description="Returns pages in a doc. Need workspace_id and doc_id from get_workspace_docs. Use returned page ids for get_doc_page.",
        args_schema=GetDocPagesInput,
        returns="JSON with list or tree of pages in the doc",
        primary_intent=ToolIntent.SEARCH,
        category=ToolCategory.PROJECT_MANAGEMENT,
        when_to_use=[
            "User wants to list pages in a ClickUp doc",
            "User needs page_id to get page details (call get_workspace_docs first for doc_id)",
        ],
        when_not_to_use=[
            "User wants to list docs (use get_workspace_docs)",
            "User wants details of one page (use get_doc_page with page_id)",
        ],
        typical_queries=["List pages in this doc", "Show doc outline", "Pages in doc X"],
    )
    async def get_doc_pages(
        self,
        workspace_id: str,
        doc_id: str,
    ) -> Tuple[bool, str]:
        """List pages in a doc."""
        try:
            response = await self.client.get_doc_pages(workspace_id, doc_id)
            if not response.success:
                return self._handle_response(response)
            data = response.data if response.data is not None else {}
            if isinstance(data, dict) and workspace_id and doc_id:
                pages_list = data.get("pages") or data.get("data") or []
                if isinstance(pages_list, list):
                    for item in pages_list:
                        if isinstance(item, dict) and item.get("id") is not None:
                            item["web_url"] = _build_clickup_web_url(
                                "page", team_id=workspace_id, doc_id=doc_id, page_id=str(item["id"])
                            )
            return self._handle_response(response, data_override=data)
        except Exception as e:
            logger.error(f"Error in get_doc_pages: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="clickup",
        tool_name="get_doc_page",
        description="Get full details of a single page in a ClickUp doc (Docs API v3).",
        llm_description="Returns full details of one page. Need workspace_id, doc_id from get_workspace_docs, and page_id from get_doc_pages.",
        args_schema=GetDocPageInput,
        returns="JSON with page details and content",
        primary_intent=ToolIntent.SEARCH,
        category=ToolCategory.PROJECT_MANAGEMENT,
        when_to_use=[
            "User wants details or content of a specific page in a doc",
            "User asks for one page by id (get page_id from get_doc_pages)",
        ],
        when_not_to_use=[
            "User wants to list pages (use get_doc_pages)",
            "User wants to list docs (use get_workspace_docs)",
        ],
        typical_queries=["Get page details", "Show page content", "Details of page X"],
    )
    async def get_doc_page(
        self,
        workspace_id: str,
        doc_id: str,
        page_id: str,
    ) -> Tuple[bool, str]:
        """Get full details of a single page in a doc."""
        try:
            response = await self.client.get_doc_page(workspace_id, doc_id, page_id)
            if not response.success:
                return self._handle_response(response)
            data = response.data if response.data is not None else {}
            if isinstance(data, dict) and workspace_id and doc_id and page_id:
                data["web_url"] = _build_clickup_web_url(
                    "page", team_id=workspace_id, doc_id=doc_id, page_id=page_id
                )
            return self._handle_response(response, data_override=data)
        except Exception as e:
            logger.error(f"Error in get_doc_page: {e}")
            return False, json.dumps({"error": str(e)})
