import json
import logging
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, model_validator

from app.agents.tools.config import ToolCategory
from app.agents.tools.decorator import tool
from app.agents.tools.models import ToolIntent
from app.connectors.core.registry.auth_builder import (
    AuthBuilder,
    AuthType,
    OAuthScopeConfig,
)
from app.connectors.core.constants import IconPaths
from app.connectors.core.registry.connector_builder import CommonFields
from app.connectors.core.registry.tool_builder import (
    ToolsetBuilder,
    ToolsetCategory,
)
from app.connectors.core.registry.types import DocumentationLink
from app.sources.client.clickup.clickup import ClickUpClient, ClickUpResponse
from app.sources.external.clickup.clickup import ClickUpDataSource

logger = logging.getLogger(__name__)

CLICKUP_APP_BASE = "https://app.clickup.com"


class ClickUpEntityType(str, Enum):
    """Entity types for building ClickUp app web URLs."""

    WORKSPACE = "workspace"
    SPACE = "space"
    FOLDER = "folder"
    LIST = "list"
    DOC = "doc"
    PAGE = "page"
    COMMENT = "comment"
    COMMENT_REPLY = "comment_reply"


def _build_clickup_web_url(
    entity: ClickUpEntityType, team_id: Optional[str] = None, **kwargs: str
) -> str:
    """Build ClickUp app web URL for an entity. All IDs as strings. team_id optional for comment/comment_reply."""
    if team_id is None and entity in (
        ClickUpEntityType.WORKSPACE,
        ClickUpEntityType.SPACE,
        ClickUpEntityType.FOLDER,
        ClickUpEntityType.LIST,
        ClickUpEntityType.DOC,
        ClickUpEntityType.PAGE,
    ):
        logger.warning(
            "Attempted to build ClickUp web URL for entity '%s' without a team_id.",
            entity.value,
        )
        return ""

    if entity == ClickUpEntityType.WORKSPACE:
        return f"{CLICKUP_APP_BASE}/{team_id}/home"
    if entity == ClickUpEntityType.SPACE:
        space_id = kwargs.get("space_id", "")
        if not space_id:
            return ""
        return f"{CLICKUP_APP_BASE}/{team_id}/v/o/s/{space_id}"
    if entity == ClickUpEntityType.FOLDER:
        folder_id = kwargs.get("folder_id", "")
        space_id = kwargs.get("space_id", "")
        if not folder_id or not space_id:
            return ""
        return f"{CLICKUP_APP_BASE}/{team_id}/v/o/f/{folder_id}?pr={space_id}"
    if entity == ClickUpEntityType.LIST:
        list_id = kwargs.get("list_id", "")
        folder_id = kwargs.get("folder_id", "")
        if not list_id or not folder_id:
            return ""
        return f"{CLICKUP_APP_BASE}/{team_id}/v/l/li/{list_id}?pr={folder_id}"
    if entity == ClickUpEntityType.DOC:
        doc_id = kwargs.get("doc_id", "")
        if not doc_id:
            return ""
        return f"{CLICKUP_APP_BASE}/{team_id}/v/dc/{doc_id}"
    if entity == ClickUpEntityType.PAGE:
        doc_id = kwargs.get("doc_id", "")
        page_id = kwargs.get("page_id", "")
        if not doc_id or not page_id:
            return ""
        return f"{CLICKUP_APP_BASE}/{team_id}/v/dc/{doc_id}/{page_id}"
    if entity == ClickUpEntityType.COMMENT:
        task_id = kwargs.get("task_id", "")
        comment_id = kwargs.get("comment_id", "")
        if task_id and comment_id:
            return f"{CLICKUP_APP_BASE}/t/{task_id}?comment={comment_id}"
        return ""
    if entity == ClickUpEntityType.COMMENT_REPLY:
        task_id = kwargs.get("task_id", "")
        comment_id = kwargs.get("comment_id", "")
        threaded_comment_id = kwargs.get("threaded_comment_id", "")
        if task_id and comment_id and threaded_comment_id:
            return f"{CLICKUP_APP_BASE}/t/{task_id}?comment={comment_id}&threadedComment={threaded_comment_id}"
        return ""
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


class GetTaskInput(BaseModel):
    """Schema for getting a specific task."""
    task_id: str = Field(description="Task ID. Get from get_tasks, create_task, or search_tasks.")


class CreateTaskInput(BaseModel):
    """Schema for creating a task."""
    list_id: str = Field(description="List ID. Get from get_lists or get_folderless_lists.")
    name: str = Field(description="Task name")
    description: Optional[str] = Field(default=None, description="Task description")
    status: Optional[str] = Field(default=None, description="Status name (e.g. to do, in progress)")
    priority: Optional[int] = Field(default=None, description="Priority: 1=Urgent, 2=High, 3=Normal, 4=Low")
    assignees: Optional[list[int]] = Field(default=None, description="Assignee user IDs (e.g. from get_authorized_user or get_list_members)")
    parent: Optional[str] = Field(default=None, description="Parent task ID to create this as a subtask")


class UpdateTaskInput(BaseModel):
    """Schema for updating a task."""
    task_id: str = Field(description="Task ID. Get from get_tasks, create_task, or search_tasks.")
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
    assignees_add: Optional[list[int]] = Field(default=None, description="User IDs to add as assignees")
    assignees_rem: Optional[list[int]] = Field(default=None, description="User IDs to remove from assignees")
    archived: Optional[bool] = Field(default=None, description="Archive or unarchive the task")
    custom_task_ids: Optional[bool] = Field(default=None, description="Use custom task IDs; requires team_id if true")
    team_id: Optional[str] = Field(default=None, description="Team ID (required when custom_task_ids is true)")


class GetWorkspaceDocsInput(BaseModel):
    """Schema for listing docs in a workspace (ClickUp Docs API v3). For 'list all docs' pass only workspace_id; leave all optional fields unset."""
    workspace_id: str = Field(description="Workspace ID. Same as team id from get_authorized_teams_workspaces.")
    creator: Optional[int] = Field(default=None, description="Only set when user asks 'my docs' (use get_authorized_user id). Leave unset for list all docs.")
    parent_id: Optional[str] = Field(default=None, description="Only set when user asks docs under a specific parent. Leave unset for list all docs.")
    parent_type: Optional[str] = Field(default=None, description="Only set when user explicitly filters by parent type. Leave unset for list all docs; do not use WORKSPACE.")
    limit: Optional[int] = Field(default=None, description="Only set when user asks to limit (e.g. 'first 10 docs'); use 10 then. Leave unset for list all docs.")
    cursor: Optional[str] = Field(default=None, description="Cursor for next page; only when paginating. Leave unset for first page.")


class GetDocPagesInput(BaseModel):
    """Schema for listing pages in a doc (ClickUp Docs API v3)."""
    workspace_id: str = Field(description="Workspace ID. Same as team id from get_authorized_teams_workspaces.")
    doc_id: str = Field(description="Doc ID. Get from get_workspace_docs.")


class GetDocPageInput(BaseModel):
    """Schema for getting one page details (ClickUp Docs API v3)."""
    workspace_id: str = Field(description="Workspace ID. Same as team id from get_authorized_teams_workspaces.")
    doc_id: str = Field(description="Doc ID. Get from get_workspace_docs.")
    page_id: str = Field(description="Page ID. Get from get_doc_pages.")


class CreateDocInput(BaseModel):
    """Schema for creating a doc in a workspace (ClickUp Docs API v3)."""
    workspace_id: str = Field(description="Workspace ID. Same as team id from get_authorized_teams_workspaces.")
    name: str = Field(description="Name of the new doc.")
    parent_id: Optional[str] = Field(default=None, description="Parent id (e.g. space_id, folder_id, list_id). Required if parent_type is set.")
    parent_type: Optional[int] = Field(default=None, description="Parent type: 4=Space, 5=Folder, 6=List, 7=Everything, 12=Workspace. Use with parent_id.")
    visibility: Optional[str] = Field(default=None, description="Visibility: PUBLIC or PRIVATE.")


class CreateDocPageInput(BaseModel):
    """Schema for creating a page in a doc (ClickUp Docs API v3)."""
    workspace_id: str = Field(description="Workspace ID. Same as team id from get_authorized_teams_workspaces.")
    doc_id: str = Field(description="Doc ID. Get from get_workspace_docs.")
    parent_page_id: Optional[str] = Field(default=None, description="Parent page ID. Omit for a root page in the doc.")
    name: str = Field(default="", description="Name of the new page.")
    sub_title: Optional[str] = Field(default=None, description="Subtitle of the new page.")
    content: str = Field(default="", description="Content of the new page.")
    content_format: str = Field(default="text/md", description="Content format: text/md (markdown) or text/plain.")


class UpdateDocPageInput(BaseModel):
    """Schema for updating a doc page (ClickUp Docs API v3)."""
    workspace_id: str = Field(description="Workspace ID. Same as team id from get_authorized_teams_workspaces.")
    doc_id: str = Field(description="Doc ID. Get from get_workspace_docs.")
    page_id: str = Field(description="Page ID. Get from get_doc_pages.")
    name: Optional[str] = Field(default=None, description="Updated name of the page (omit to leave unchanged).")
    sub_title: Optional[str] = Field(default=None, description="Updated subtitle (omit to leave unchanged).")
    content: Optional[str] = Field(default=None, description="Updated content (omit to leave unchanged).")
    content_edit_mode: str = Field(default="replace", description="How to update content: replace, append, or prepend.")
    content_format: str = Field(default="text/md", description="Content format: text/md or text/plain.")


class GetWorkspaceTasksInput(BaseModel):
    """Schema for searching/filtering tasks across a workspace (team)."""
    team_id: str = Field(description="Workspace (team) ID. Get from get_authorized_teams_workspaces.")
    page: Optional[int] = Field(default=0, description="Page number (0-based). 0 = first page. 100 tasks per page.")
    order_by: Optional[str] = Field(default="updated", description="Order by: created, updated, due_date, start_date. Default: updated.")
    reverse: Optional[bool] = Field(default=None, description="Reverse sort order")
    subtasks: Optional[bool] = Field(default=None, description="Include subtasks")
    statuses: Optional[list[str]] = Field(default=None, description="Filter by status names")
    include_closed: Optional[bool] = Field(default=False, description="Include closed tasks")
    assignees: Optional[list[str]] = Field(default=None, description="Filter by assignee user IDs")
    tags: Optional[list[str]] = Field(default=None, description="Filter by tag names")
    due_date_gt: Optional[int] = Field(default=None, description="Filter tasks due after (Unix ms)")
    due_date_lt: Optional[int] = Field(default=None, description="Filter tasks due before (Unix ms)")
    date_created_gt: Optional[int] = Field(default=None, description="Filter tasks created after (Unix ms)")
    date_created_lt: Optional[int] = Field(default=None, description="Filter tasks created before (Unix ms)")
    date_updated_gt: Optional[int] = Field(default=None, description="Filter tasks updated after (Unix ms)")
    date_updated_lt: Optional[int] = Field(default=None, description="Filter tasks updated before (Unix ms)")
    space_ids: Optional[list[str]] = Field(default=None, description="Filter by space IDs")
    project_ids: Optional[list[str]] = Field(default=None, description="Filter by folder (project) IDs")
    list_ids: Optional[list[str]] = Field(default=None, description="Filter by list IDs")
    custom_fields: Optional[list[dict[str, Any]]] = Field(default=None, description="Filter by custom field values")


class SearchTasksInput(BaseModel):
    """Schema for keyword search across workspace tasks (creates temporary view, returns tasks, deletes view)."""
    team_id: str = Field(description="Workspace (team) ID. Get from get_authorized_teams_workspaces.")
    keyword: str = Field(description="Search string for task name, description, and custom field text.")
    show_closed: bool = Field(default=True, description="Include closed (completed) tasks in search results")
    page: Optional[int] = Field(default=None, description="Page number (0-based). 100 tasks per page.")


class CreateSpaceInput(BaseModel):
    """Schema for creating a space in a workspace."""
    team_id: str = Field(description="Workspace (team) ID. Get from get_authorized_teams_workspaces.")
    name: str = Field(description="Name of the new space.")
    multiple_assignees: Optional[bool] = Field(default=None, description="Enable multiple assignees in the space")
    features: Optional[dict[str, Any]] = Field(default=None, description="Space features configuration")


class CreateFolderInput(BaseModel):
    """Schema for creating a folder in a space."""
    space_id: str = Field(description="Space ID. Get from get_spaces.")
    name: str = Field(description="Name of the new folder.")
    team_id: Optional[str] = Field(default=None, description="Workspace (team) ID. Get from get_authorized_teams_workspaces. Used for web_url in response.")


class CreateListInput(BaseModel):
    """Schema for creating a list in a folder or a folderless list in a space. Exactly one of folder_id or space_id required."""
    folder_id: Optional[str] = Field(default=None, description="Folder ID from get_folders. Use to create a list inside a folder. Mutually exclusive with space_id.")
    space_id: Optional[str] = Field(default=None, description="Space ID from get_spaces. Use to create a folderless list. Mutually exclusive with folder_id.")
    name: str = Field(description="Name of the new list.")
    team_id: Optional[str] = Field(default=None, description="Workspace (team) ID. Get from get_authorized_teams_workspaces. Used for web_url in response.")
    content: Optional[str] = Field(default=None, description="List description")
    due_date: Optional[int] = Field(default=None, description="Due date as Unix timestamp (ms)")
    due_date_time: Optional[bool] = Field(default=None, description="Include time in due date")
    priority: Optional[int] = Field(default=None, description="Priority: 1=Urgent, 2=High, 3=Normal, 4=Low")
    assignee: Optional[int] = Field(default=None, description="Assignee user ID")
    status: Optional[str] = Field(default=None, description="Status name")

    @model_validator(mode="after")
    def require_folder_or_space(self) -> "CreateListInput":
        if not self.folder_id and not self.space_id:
            raise ValueError("At least one of folder_id or space_id is required.")
        return self


class UpdateListInput(BaseModel):
    """Schema for updating a list."""
    list_id: str = Field(description="List ID. Get from get_lists or get_folderless_lists.")
    name: Optional[str] = Field(default=None, description="New name (omit to leave unchanged)")
    content: Optional[str] = Field(default=None, description="List description (omit to leave unchanged)")
    due_date: Optional[int] = Field(default=None, description="Due date as Unix timestamp (ms)")
    due_date_time: Optional[bool] = Field(default=None, description="Include time in due date")
    priority: Optional[int] = Field(default=None, description="Priority: 1=Urgent, 2=High, 3=Normal, 4=Low")
    assignee_add: Optional[int] = Field(default=None, description="Add assignee by user ID")
    assignee_rem: Optional[int] = Field(default=None, description="Remove assignee by user ID")
    unset_status: Optional[bool] = Field(default=None, description="Remove the status field")


class GetCommentsInput(BaseModel):
    """Schema for getting comments: either all comments on a task (task_id) or replies to a comment (comment_id). Exactly one of task_id or comment_id required."""
    task_id: Optional[str] = Field(default=None, description="Task ID to list all comments on the task. Get from get_tasks, get_task, create_task, or search_tasks.")
    comment_id: Optional[str] = Field(default=None, description="Comment ID to list replies (thread). Get from get_comments. When set, returns only replies; optionally pass task_id for web_url.")
    custom_task_ids: Optional[bool] = Field(default=None, description="Use custom task IDs (only when task_id is set). If true, team_id is required.")
    team_id: Optional[str] = Field(default=None, description="Workspace (team) ID. Required when custom_task_ids is true. Optional when comment_id is set (for web_url).")
    start: Optional[int] = Field(default=None, description="Start timestamp for pagination (only when task_id is set).")
    start_id: Optional[str] = Field(default=None, description="Start comment ID for pagination (only when task_id is set).")

    @model_validator(mode="after")
    def require_task_or_comment(self) -> "GetCommentsInput":
        if not self.task_id and not self.comment_id:
            raise ValueError("At least one of task_id or comment_id is required. Use task_id for comments on a task, comment_id for replies to a comment (optionally task_id for web_url).")
        return self


class CreateTaskCommentInput(BaseModel):
    """Schema for creating a comment on a task or a reply to a comment. For new comment use task_id. For reply use comment_id (optionally task_id for web_url)."""
    task_id: Optional[str] = Field(default=None, description="Task ID for a new top-level comment, or for web_url when replying. Get from get_tasks, get_task, create_task, or search_tasks.")
    comment_id: Optional[str] = Field(default=None, description="Comment ID for a reply (threaded comment). Get from get_comments. When set, creates a reply; optionally pass task_id for web_url in response.")
    comment_text: str = Field(description="The comment or reply text (plain text).")
    assignee: Optional[int] = Field(default=None, description="Assign the comment/reply to a user ID.")
    notify_all: Optional[bool] = Field(default=None, description="Notify all assignees.")
    custom_task_ids: Optional[bool] = Field(default=None, description="Use custom task IDs (only when task_id is set for new comment). If true, team_id is required.")
    team_id: Optional[str] = Field(default=None, description="Workspace (team) ID. Required when task_id is set and custom_task_ids is true.")

    @model_validator(mode="after")
    def require_task_or_comment(self) -> "CreateTaskCommentInput":
        if not self.task_id and not self.comment_id:
            raise ValueError("At least one of task_id or comment_id is required. Use task_id for a new comment, comment_id for a reply (optionally task_id too for reply web_url).")
        return self


class CreateChecklistInput(BaseModel):
    """Schema for creating a checklist on a task."""
    task_id: str = Field(description="Task ID. Get from get_tasks, get_task, create_task, or search_tasks.")
    name: str = Field(description="Checklist name.")
    custom_task_ids: Optional[bool] = Field(default=None, description="Use custom task IDs. If true, team_id is required.")
    team_id: Optional[str] = Field(default=None, description="Workspace (team) ID. Required when custom_task_ids is true.")


class CreateChecklistItemInput(BaseModel):
    """Schema for creating a checklist item."""
    checklist_id: str = Field(description="Checklist ID. Get from task checklists (get_task) or create_checklist response.")
    name: str = Field(description="Checklist item name.")
    assignee: Optional[int] = Field(default=None, description="Assignee user ID.")


class UpdateChecklistItemInput(BaseModel):
    """Schema for updating a checklist item (name, assignee, resolved, parent)."""
    checklist_id: str = Field(description="Checklist ID. Get from task checklists or create_checklist response.")
    checklist_item_id: str = Field(description="Checklist item ID. Get from checklist items or create_checklist_item response.")
    name: Optional[str] = Field(default=None, description="New item name (omit to leave unchanged).")
    assignee: Optional[int] = Field(default=None, description="Assignee user ID.")
    resolved: Optional[bool] = Field(default=None, description="Mark item resolved (checked) or unresolved.")
    parent: Optional[str] = Field(default=None, description="Parent checklist item ID for nesting.")


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
            icon_path=IconPaths.connector_icon("clickup"),
            app_group="Project Management",
            app_description="ClickUp OAuth application for agent integration",
        )
    ]) \
    .configure(lambda builder: builder
        .with_icon(IconPaths.connector_icon("clickup"))
        .add_documentation_link(DocumentationLink(
            "ClickUp API Setup",
            "https://developer.clickup.com/docs/authentication",
            "setup",
        ))
        .add_documentation_link(DocumentationLink(
            "Pipeshub Documentation",
            "https://docs.pipeshub.com/toolsets/clickup/clickup",
            "pipeshub",
        ))) \
    .build_decorator()
class ClickUp:
    """ClickUp tool exposed to the agents using ClickUpDataSource."""

    def __init__(self, client: ClickUpClient) -> None:
        """Initialize the ClickUp tool.

        Args:
            client: ClickUp client object
        """
        self.client = ClickUpDataSource(client)

    def _handle_response(
        self,
        response: ClickUpResponse,
        data_override: dict[str, object] | list[object] | None = None,
    ) -> tuple[bool, str]:
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
    async def get_authorized_user(self) -> tuple[bool, str]:
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
        description="Get the authorized teams (workspaces).",
        llm_description="Returns list of ClickUp workspaces (teams). Use the returned team id as team_id in get_spaces. Call this first when user asks for spaces, folders, or lists.",
        parameters=[],
        returns="JSON with list of workspaces (teams)",
        primary_intent=ToolIntent.SEARCH,
        category=ToolCategory.PROJECT_MANAGEMENT,
        when_to_use=[
            "User wants to list ClickUp workspaces or teams",
            "User needs team_id to list spaces",
        ],
        when_not_to_use=[
            "User wants user profile only (use get_authorized_user)",
            "User already has team_id and wants spaces (use get_spaces)",
        ],
        typical_queries=["List my ClickUp workspaces", "Show teams", "What workspaces do I have?"],
    )
    async def get_authorized_teams_workspaces(self) -> tuple[bool, str]:
        """Get the authorized teams (workspaces)."""
        try:
            response = await self.client.get_authorized_teams_workspaces()
            if not response.success:
                return self._handle_response(response)
            data = response.data if response.data is not None else {}
            if isinstance(data, dict):
                for item in data.get("teams") or []:
                    if isinstance(item, dict) and item.get("id") is not None:
                        item["web_url"] = _build_clickup_web_url(ClickUpEntityType.WORKSPACE, team_id=str(item["id"]))
            return self._handle_response(response, data_override=data)
        except Exception as e:
            logger.error(f"Error in get_authorized_teams_workspaces: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="clickup",
        tool_name="get_spaces",
        description="Get all spaces in a workspace.",
        llm_description="Returns spaces in a workspace. Need team_id from get_authorized_teams_workspaces.",
        args_schema=GetSpacesInput,
        returns="JSON with list of spaces in the workspace",
        primary_intent=ToolIntent.SEARCH,
        category=ToolCategory.PROJECT_MANAGEMENT,
        when_to_use=[
            "User wants to list spaces in a ClickUp workspace",
            "User needs space_id for folders or lists (call get_authorized_teams_workspaces first for team_id)",
        ],
        when_not_to_use=[
            "User wants workspaces/teams list",
            "User wants folders in a space",
        ],
        typical_queries=["List spaces in my workspace", "Show ClickUp spaces", "What spaces do I have?"],
    )
    async def get_spaces(
        self,
        team_id: str,
        *,
        archived: Optional[bool] = None,
    ) -> tuple[bool, str]:
        """Get all spaces in a workspace."""
        try:
            response = await self.client.get_spaces(team_id, archived=archived)
            if not response.success:
                return self._handle_response(response)
            data = response.data if response.data is not None else {}
            if isinstance(data, dict) and team_id:
                for item in data.get("spaces") or []:
                    if isinstance(item, dict) and item.get("id") is not None:
                        item["web_url"] = _build_clickup_web_url(ClickUpEntityType.SPACE, team_id=team_id, space_id=str(item["id"]))
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
            "User wants folderless lists",
            "User wants lists inside a folder",
        ],
        typical_queries=["List folders in this space", "Show folders", "What folders are in the space?"],
    )
    async def get_folders(
        self,
        space_id: str,
        team_id: str,
        *,
        archived: Optional[bool] = None,
    ) -> tuple[bool, str]:
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
                            ClickUpEntityType.FOLDER,
                            team_id=team_id,
                            space_id=space_id,
                            folder_id=str(item["id"]),
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
            "User wants tasks in a list (use get_tasks with list_ids=[list_id])",
        ],
        typical_queries=["List lists in this folder", "Show lists", "What lists are in the folder?"],
    )
    async def get_lists(
        self,
        folder_id: str,
        team_id: str,
        *,
        archived: Optional[bool] = None,
    ) -> tuple[bool, str]:
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
                            ClickUpEntityType.LIST,
                            team_id=team_id,
                            list_id=str(item["id"]),
                            folder_id=folder_id,
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
        *,
        archived: Optional[bool] = None,
    ) -> tuple[bool, str]:
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
                            ClickUpEntityType.LIST,
                            team_id=team_id,
                            list_id=str(item["id"]),
                            folder_id=space_id,
                        )
            return self._handle_response(response, data_override=data)
        except Exception as e:
            logger.error(f"Error in get_folderless_lists: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="clickup",
        tool_name="create_space",
        description="Create a new space in a workspace.",
        llm_description="Creates a space in a workspace. Need team_id from get_authorized_teams_workspaces; name is required. Optional: multiple_assignees, features.",
        args_schema=CreateSpaceInput,
        returns="JSON with the created space details",
        primary_intent=ToolIntent.ACTION,
        category=ToolCategory.PROJECT_MANAGEMENT,
        when_to_use=[
            "User wants to create a new space in a ClickUp workspace",
            "User asks to add a space (need team_id from get_authorized_teams_workspaces)",
        ],
        when_not_to_use=[
            "User wants to list spaces (use get_spaces)",
            "User wants to create a folder or list",
        ],
        typical_queries=["Create a space", "Add a space to workspace", "New space: Engineering"],
    )
    async def create_space(
        self,
        team_id: str,
        name: str,
        *,
        multiple_assignees: Optional[bool] = None,
        features: Optional[dict[str, Any]] = None,
    ) -> tuple[bool, str]:
        """Create a new space in a workspace."""
        try:
            response = await self.client.create_space(
                team_id,
                name,
                multiple_assignees=multiple_assignees,
                features=features,
            )
            if not response.success:
                return self._handle_response(response)
            data = dict(response.data) if isinstance(response.data, dict) else {}
            if data.get("id") and team_id:
                data["web_url"] = _build_clickup_web_url(
                ClickUpEntityType.SPACE, team_id=team_id, space_id=str(data["id"])
            )
            return self._handle_response(response, data_override=data)
        except Exception as e:
            logger.error(f"Error in create_space: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="clickup",
        tool_name="create_folder",
        description="Create a new folder in a space.",
        llm_description="Creates a folder in a space. Need space_id from get_spaces; name is required.",
        args_schema=CreateFolderInput,
        returns="JSON with the created folder details",
        primary_intent=ToolIntent.ACTION,
        category=ToolCategory.PROJECT_MANAGEMENT,
        when_to_use=[
            "User wants to create a new folder in a ClickUp space",
            "User asks to add a folder (need space_id from get_spaces)",
        ],
        when_not_to_use=[
            "User wants to list folders (use get_folders)",
            "User wants to create a list",
        ],
        typical_queries=["Create a folder", "Add a folder to space", "New folder: Sprint 1"],
    )
    async def create_folder(
        self,
        space_id: str,
        name: str,
        team_id: Optional[str] = None,
    ) -> tuple[bool, str]:
        """Create a new folder in a space."""
        try:
            response = await self.client.create_folder(space_id, name)
            if not response.success:
                return self._handle_response(response)
            data = dict(response.data) if isinstance(response.data, dict) else {}
            if data.get("id") and team_id and space_id:
                data["web_url"] = _build_clickup_web_url(
                    ClickUpEntityType.FOLDER,
                    team_id=team_id,
                    space_id=space_id,
                    folder_id=str(data["id"]),
                )
            return self._handle_response(response, data_override=data)
        except Exception as e:
            logger.error(f"Error in create_folder: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="clickup",
        tool_name="create_list",
        description="Create a list in a folder or a folderless list in a space.",
        llm_description="Creates a list in a folder (provide folder_id from get_folders) or a folderless list in a space (provide space_id from get_spaces). Name required. Exactly one of folder_id or space_id required. Optional: team_id for web_url, content, due_date, priority, assignee, status.",
        args_schema=CreateListInput,
        returns="JSON with the created list details",
        primary_intent=ToolIntent.ACTION,
        category=ToolCategory.PROJECT_MANAGEMENT,
        when_to_use=[
            "User wants to create a new list in a ClickUp folder (use folder_id from get_folders)",
            "User wants to create a folderless list in a space (use space_id from get_spaces)",
            "User asks to add a list",
        ],
        when_not_to_use=[
            "User wants to list lists (use get_lists or get_folderless_lists)",
            "User wants to create a task (use create_task)",
        ],
        typical_queries=["Create a list", "Add a list to folder", "New list: Backlog", "Create folderless list"],
    )
    async def create_list(
        self,
        name: str,
        folder_id: Optional[str] = None,
        space_id: Optional[str] = None,
        team_id: Optional[str] = None,
        content: Optional[str] = None,
        due_date: Optional[int] = None,
        *,
        due_date_time: Optional[bool] = None,
        priority: Optional[int] = None,
        assignee: Optional[int] = None,
        status: Optional[str] = None,
    ) -> tuple[bool, str]:
        """Create a list in a folder or a folderless list in a space."""
        try:
            if folder_id:
                response = await self.client.create_list(
                    folder_id,
                    name,
                    content=content,
                    due_date=due_date,
                    due_date_time=due_date_time,
                    priority=priority,
                    assignee=assignee,
                    status=status,
                )
                pr_id = folder_id
            else:
                response = await self.client.create_folderless_list(
                    space_id,
                    name,
                    content=content,
                    due_date=due_date,
                    due_date_time=due_date_time,
                    priority=priority,
                    assignee=assignee,
                    status=status,
                )
                pr_id = space_id
            if not response.success:
                return self._handle_response(response)
            data = dict(response.data) if isinstance(response.data, dict) else {}
            if data.get("id") and team_id and pr_id:
                data["web_url"] = _build_clickup_web_url(
                    ClickUpEntityType.LIST,
                    team_id=team_id,
                    list_id=str(data["id"]),
                    folder_id=pr_id,
                )
            return self._handle_response(response, data_override=data)
        except Exception as e:
            logger.error(f"Error in create_list: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="clickup",
        tool_name="update_list",
        description="Update a list.",
        llm_description="Updates a list. Need list_id from get_lists or get_folderless_lists. Pass only fields to change; omit others to leave unchanged.",
        args_schema=UpdateListInput,
        returns="JSON with the updated list details",
        primary_intent=ToolIntent.ACTION,
        category=ToolCategory.PROJECT_MANAGEMENT,
        when_to_use=[
            "User wants to edit or update a ClickUp list",
            "User asks to rename a list or change list settings",
        ],
        when_not_to_use=[
            "User wants to create a list (use create_list)",
            "User wants to list lists (use get_lists)",
        ],
        typical_queries=["Update list", "Rename list", "Change list due date", "Edit list settings"],
    )
    async def update_list(
        self,
        list_id: str,
        name: Optional[str] = None,
        content: Optional[str] = None,
        due_date: Optional[int] = None,
        *,
        due_date_time: Optional[bool] = None,
        priority: Optional[int] = None,
        assignee_add: Optional[int] = None,
        assignee_rem: Optional[int] = None,
        unset_status: Optional[bool] = None,
    ) -> tuple[bool, str]:
        """Update a list."""
        try:
            response = await self.client.update_list(
                list_id,
                name=name,
                content=content,
                due_date=due_date,
                due_date_time=due_date_time,
                priority=priority,
                assignee_add=assignee_add,
                assignee_rem=assignee_rem,
                unset_status=unset_status,
            )
            return self._handle_response(response)
        except Exception as e:
            logger.error(f"Error in update_list: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="clickup",
        tool_name="get_tasks",
        description="Search/filter tasks across the whole workspace.",
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
            "User wants tasks in a specific list (use get_tasks with list_ids=[list_id])",
            "User wants a single task by id (use get_task)",
        ],
        typical_queries=["Tasks in workspace with status In Progress", "All tasks assigned to me", "Tasks with tag urgent in workspace"],
    )
    async def get_tasks(
        self,
        team_id: str,
        page: Optional[int] = 0,
        order_by: Optional[str] = "updated",
        *,
        reverse: Optional[bool] = None,
        subtasks: Optional[bool] = None,
        statuses: Optional[list[str]] = None,
        include_closed: Optional[bool] = False,
        assignees: Optional[list[str]] = None,
        tags: Optional[list[str]] = None,
        due_date_gt: Optional[int] = None,
        due_date_lt: Optional[int] = None,
        date_created_gt: Optional[int] = None,
        date_created_lt: Optional[int] = None,
        date_updated_gt: Optional[int] = None,
        date_updated_lt: Optional[int] = None,
        space_ids: Optional[list[str]] = None,
        project_ids: Optional[list[str]] = None,
        list_ids: Optional[list[str]] = None,
        custom_fields: Optional[list[dict[str, Any]]] = None,
    ) -> tuple[bool, str]:
        """Search/filter tasks across the whole workspace."""
        logger.info(
            "clickup get_tasks: team_id=%s page=%s order_by=%s reverse=%s subtasks=%s statuses=%s include_closed=%s assignees=%s tags=%s space_ids=%s project_ids=%s list_ids=%s",
            team_id, page, order_by, reverse, subtasks, statuses, include_closed, assignees, tags, space_ids, project_ids, list_ids,
        )
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
            logger.error(f"Error in get_tasks: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="clickup",
        tool_name="search_tasks",
        description="Search tasks by keyword or phrase.",
        llm_description="Returns tasks matching a keyword/phrase across the workspace. Creates a temporary view, fetches tasks, then deletes the view. Use for free-text search (e.g. 'login bug', 'invoice'). Get team_id from get_authorized_teams_workspaces. Prefer this over get_tasks when user asks for tasks containing specific text.",
        args_schema=SearchTasksInput,
        returns="JSON with list of tasks matching the keyword",
        primary_intent=ToolIntent.SEARCH,
        category=ToolCategory.PROJECT_MANAGEMENT,
        when_to_use=[
            "User wants tasks containing specific text or keyword",
            "User asks for tasks with a word or phrase in name/description (e.g. 'login bug', 'invoice')",
        ],
        when_not_to_use=[
            "User wants to filter by status/assignee/tags/dates only (use get_tasks)",
            "User wants tasks in a specific list (use get_tasks with list_ids)",
        ],
        typical_queries=["Tasks containing login bug", "Find tasks with invoice", "Search for tasks named X"],
    )
    async def search_tasks(
        self,
        team_id: str,
        keyword: str,
        *,
        show_closed: bool = True,
        page: Optional[int] = None,
    ) -> tuple[bool, str]:
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
        description="Get a specific task details.",
        llm_description="Returns one task by task_id. Get task_id from get_tasks, create_task, or search_tasks. Use for 'show task X', 'details of task Y'.",
        args_schema=GetTaskInput,
        returns="JSON with task details",
        primary_intent=ToolIntent.SEARCH,
        category=ToolCategory.PROJECT_MANAGEMENT,
        when_to_use=[
            "User wants details of a specific ClickUp task",
            "User asks for a task by ID (get task_id from get_tasks, create_task, or search_tasks)",
        ],
        when_not_to_use=[
            "User wants all tasks in a list (use get_tasks with list_ids)",
            "User wants to create a task (use create_task)",
        ],
        typical_queries=["Get task abc123", "Show task details", "What is the status of this task?"],
    )
    async def get_task(self, task_id: str) -> tuple[bool, str]:
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
        description="Create a new task or subtask in a list.",
        llm_description="Creates a task. Need list_id from get_lists or get_folderless_lists; name is required. Optional: description, status, priority, assignees, parent (for subtasks). Returns the created task including task id.",
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
        priority: Optional[int] = None,
        assignees: Optional[list[int]] = None,
        parent: Optional[str] = None,
    ) -> tuple[bool, str]:
        """Create a new task in a list."""
        try:
            response = await self.client.create_task(
                list_id,
                name,
                description=description,
                status=status,
                priority=priority,
                assignees=assignees,
                parent=parent,
            )
            return self._handle_response(response)
        except Exception as e:
            logger.error(f"Error in create_task: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="clickup",
        tool_name="update_task",
        description="Update an existing task.",
        llm_description="Updates a task. Need task_id from get_tasks, create_task, or search_tasks. Pass only fields to change (name, description, status, priority, due_date, start_date, assignees_add/assignees_rem, archived, etc.); omit others to leave unchanged.",
        args_schema=UpdateTaskInput,
        returns="JSON with the updated task details",
        primary_intent=ToolIntent.ACTION,
        category=ToolCategory.PROJECT_MANAGEMENT,
        when_to_use=[
            "User wants to edit or update a ClickUp task",
            "User asks to change task name, description, status, priority, due date, assignees, or archive (need task_id from get_tasks, create_task, or search_tasks)",
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
        *,
        due_date_time: Optional[bool] = None,
        time_estimate: Optional[int] = None,
        start_date: Optional[int] = None,
        start_date_time: Optional[bool] = None,
        assignees_add: Optional[list[int]] = None,
        assignees_rem: Optional[list[int]] = None,
        archived: Optional[bool] = None,
        custom_task_ids: Optional[bool] = None,
        team_id: Optional[str] = None,
    ) -> tuple[bool, str]:
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
        tool_name="get_comments",
        description="Get comments on a task or replies to a comment.",
        llm_description="Returns comments on a task (pass task_id) or replies to a comment (pass comment_id; optionally task_id for web_url). Get task_id from get_tasks, get_task, create_task, or search_tasks; comment_id from get_comments. For task comments: optional custom_task_ids, team_id, start, start_id.",
        args_schema=GetCommentsInput,
        returns="JSON with list of comments or comment replies",
        primary_intent=ToolIntent.SEARCH,
        category=ToolCategory.PROJECT_MANAGEMENT,
        when_to_use=[
            "User wants to see comments on a task (pass task_id)",
            "User wants to see replies to a comment / comment thread (pass comment_id from get_comments)",
        ],
        when_not_to_use=[
            "User wants to add a comment (use create_task_comment)",
        ],
        typical_queries=["Comments on task", "List task comments", "Replies to comment", "Comment thread"],
    )
    async def get_comments(
        self,
        task_id: Optional[str] = None,
        comment_id: Optional[str] = None,
        *,
        custom_task_ids: Optional[bool] = None,
        team_id: Optional[str] = None,
        start: Optional[int] = None,
        start_id: Optional[str] = None,
    ) -> tuple[bool, str]:
        """Get comments on a task or replies to a comment."""
        try:
            if comment_id:
                response = await self.client.get_comment_replies(comment_id)
                if not response.success:
                    return self._handle_response(response)
                data = response.data if response.data is not None else {}
                if isinstance(data, dict) and task_id and comment_id:
                    for item in data.get("comments") or []:
                        if isinstance(item, dict) and item.get("id") is not None:
                            item["web_url"] = _build_clickup_web_url(
                                ClickUpEntityType.COMMENT_REPLY,
                                task_id=task_id,
                                comment_id=comment_id,
                                threaded_comment_id=str(item["id"]),
                            )
                return self._handle_response(response, data_override=data)
            else:
                response = await self.client.get_task_comments(
                    task_id,
                    custom_task_ids=custom_task_ids,
                    team_id=team_id,
                    start=start,
                    start_id=start_id,
                )
                if not response.success:
                    return self._handle_response(response)
                data = response.data if response.data is not None else {}
                if isinstance(data, dict) and task_id:
                    for item in data.get("comments") or []:
                        if isinstance(item, dict) and item.get("id") is not None:
                            item["web_url"] = _build_clickup_web_url(
                                ClickUpEntityType.COMMENT,
                                task_id=task_id,
                                comment_id=str(item["id"]),
                            )
                return self._handle_response(response, data_override=data)
        except Exception as e:
            logger.error(f"Error in get_comments: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="clickup",
        tool_name="create_task_comment",
        description="Add a comment to a task or a reply to a comment.",
        llm_description="Creates a top-level comment on a task (provide task_id) or a reply to a comment (provide comment_id from get_comments). comment_text required. Exactly one of task_id or comment_id required. Optional: assignee, notify_all; for task_id only: custom_task_ids, team_id.",
        args_schema=CreateTaskCommentInput,
        returns="JSON with the created comment or reply",
        primary_intent=ToolIntent.ACTION,
        category=ToolCategory.PROJECT_MANAGEMENT,
        when_to_use=[
            "User wants to add a comment to a task (use task_id)",
            "User wants to reply to a comment (use comment_id from get_comments)",
        ],
        when_not_to_use=[
            "User wants to read comments (use get_comments)",
        ],
        typical_queries=["Add comment to task", "Reply on task", "Reply to comment", "Comment on task X"],
    )
    async def create_task_comment(
        self,
        comment_text: str,
        task_id: Optional[str] = None,
        comment_id: Optional[str] = None,
        assignee: Optional[int] = None,
        *,
        notify_all: Optional[bool] = None,
        custom_task_ids: Optional[bool] = None,
        team_id: Optional[str] = None,
    ) -> tuple[bool, str]:
        """Add a comment to a task or a reply to a comment."""
        try:
            if comment_id:
                response = await self.client.create_task_comment_reply(
                    comment_id,
                    comment_text,
                    assignee=assignee,
                    notify_all=notify_all,
                )
            else:
                response = await self.client.create_task_comment(
                    task_id,
                    comment_text,
                    assignee=assignee,
                    notify_all=notify_all,
                    custom_task_ids=custom_task_ids,
                    team_id=team_id,
                )
            if not response.success:
                return self._handle_response(response)
            data = dict(response.data) if isinstance(response.data, dict) else {}
            if data.get("id") and task_id:
                if comment_id:
                    data["web_url"] = _build_clickup_web_url(
                        ClickUpEntityType.COMMENT_REPLY,
                        task_id=task_id,
                        comment_id=comment_id,
                        threaded_comment_id=str(data["id"]),
                    )
                else:
                    data["web_url"] = _build_clickup_web_url(
                        ClickUpEntityType.COMMENT,
                        task_id=task_id,
                        comment_id=str(data["id"]),
                    )
            return self._handle_response(response, data_override=data)
        except Exception as e:
            logger.error(f"Error in create_task_comment: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="clickup",
        tool_name="create_checklist",
        description="Create a checklist on a task.",
        llm_description="Creates a checklist on a task. Need task_id from get_tasks or create_task; name required. Returns checklist id for create_checklist_item. Optional: custom_task_ids, team_id.",
        args_schema=CreateChecklistInput,
        returns="JSON with the created checklist (includes checklist id for create_checklist_item)",
        primary_intent=ToolIntent.ACTION,
        category=ToolCategory.PROJECT_MANAGEMENT,
        when_to_use=[
            "User wants to add a checklist to a task",
            "User asks to create a checklist on a task (need task_id)",
        ],
        when_not_to_use=[
            "User wants to add checklist items (use create_checklist_item after create_checklist)",
        ],
        typical_queries=["Add checklist to task", "Create checklist on task", "New checklist"],
    )
    async def create_checklist(
        self,
        task_id: str,
        name: str,
        *,
        custom_task_ids: Optional[bool] = None,
        team_id: Optional[str] = None,
    ) -> tuple[bool, str]:
        """Create a checklist on a task."""
        try:
            response = await self.client.create_checklist(
                task_id,
                name,
                custom_task_ids=custom_task_ids,
                team_id=team_id,
            )
            return self._handle_response(response)
        except Exception as e:
            logger.error(f"Error in create_checklist: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="clickup",
        tool_name="create_checklist_item",
        description="Add an item to a checklist.",
        llm_description="Adds an item to a checklist. Need checklist_id from task checklists (get_task) or create_checklist; name required. Optional: assignee.",
        args_schema=CreateChecklistItemInput,
        returns="JSON with the created checklist item",
        primary_intent=ToolIntent.ACTION,
        category=ToolCategory.PROJECT_MANAGEMENT,
        when_to_use=[
            "User wants to add an item to a checklist",
            "User asks to add a checklist item (need checklist_id from create_checklist or task checklists)",
        ],
        when_not_to_use=[
            "User wants to create the checklist (use create_checklist)",
        ],
        typical_queries=["Add item to checklist", "Add checklist item", "New checklist item"],
    )
    async def create_checklist_item(
        self,
        checklist_id: str,
        name: str,
        assignee: Optional[int] = None,
    ) -> tuple[bool, str]:
        """Add an item to a checklist."""
        try:
            response = await self.client.create_checklist_item(
                checklist_id,
                name,
                assignee=assignee,
            )
            return self._handle_response(response)
        except Exception as e:
            logger.error(f"Error in create_checklist_item: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="clickup",
        tool_name="update_checklist_item",
        description="Update or check/uncheck a checklist item.",
        llm_description="Updates a checklist item (name, assignee, resolved/checked, parent). Need checklist_id and checklist_item_id from task checklists or create_checklist_item. Pass only fields to change.",
        args_schema=UpdateChecklistItemInput,
        returns="JSON with the updated checklist item",
        primary_intent=ToolIntent.ACTION,
        category=ToolCategory.PROJECT_MANAGEMENT,
        when_to_use=[
            "User wants to check/uncheck or edit a checklist item",
            "User asks to rename item, set assignee, or mark resolved (need checklist_id and checklist_item_id)",
        ],
        when_not_to_use=[
            "User wants to add an item (use create_checklist_item)",
        ],
        typical_queries=["Check checklist item", "Uncheck item", "Rename checklist item", "Mark checklist item done"],
    )
    async def update_checklist_item(
        self,
        checklist_id: str,
        checklist_item_id: str,
        name: Optional[str] = None,
        assignee: Optional[int] = None,
        *,
        resolved: Optional[bool] = None,
        parent: Optional[str] = None,
    ) -> tuple[bool, str]:
        """Update or check/uncheck a checklist item."""
        try:
            response = await self.client.update_checklist_item(
                checklist_id,
                checklist_item_id,
                name=name,
                assignee=assignee,
                resolved=resolved,
                parent=parent,
            )
            return self._handle_response(response)
        except Exception as e:
            logger.error(f"Error in update_checklist_item: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="clickup",
        tool_name="get_workspace_docs",
        description="List docs in a workspace.",
        llm_description="Returns docs in a workspace. Pass only workspace_id unless user asks to filter (e.g. my docs, first 10).",
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
    async def get_workspace_docs(
        self,
        workspace_id: str,
        creator: Optional[int] = None,
        parent_id: Optional[str] = None,
        parent_type: Optional[str] = None,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
    ) -> tuple[bool, str]:
        """List docs in a workspace."""
        try:
            response = await self.client.get_workspace_docs(
                workspace_id,
                creator=creator,
                parent_id=parent_id,
                parent_type=parent_type,
                limit=limit,
                cursor=cursor,
            )
            if not response.success:
                return self._handle_response(response)
            data = response.data if response.data is not None else {}
            if isinstance(data, dict) and workspace_id:
                docs_list = data.get("docs") or data.get("data") or []
                if isinstance(docs_list, list):
                    for item in docs_list:
                        if isinstance(item, dict) and item.get("id") is not None:
                            item["web_url"] = _build_clickup_web_url(
                                ClickUpEntityType.DOC,
                                team_id=workspace_id,
                                doc_id=str(item["id"]),
                            )
            return self._handle_response(response, data_override=data)
        except Exception as e:
            logger.error(f"Error in get_workspace_docs: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="clickup",
        tool_name="get_doc_pages",
        description="List pages in a doc.",
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
    ) -> tuple[bool, str]:
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
                                ClickUpEntityType.PAGE,
                                team_id=workspace_id,
                                doc_id=doc_id,
                                page_id=str(item["id"]),
                            )
            return self._handle_response(response, data_override=data)
        except Exception as e:
            logger.error(f"Error in get_doc_pages: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="clickup",
        tool_name="get_doc_page",
        description="Get details of a page.",
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
    ) -> tuple[bool, str]:
        """Get full details of a single page in a doc."""
        try:
            response = await self.client.get_doc_page(workspace_id, doc_id, page_id)
            if not response.success:
                return self._handle_response(response)
            data = response.data if response.data is not None else {}
            if isinstance(data, dict) and workspace_id and doc_id and page_id:
                data["web_url"] = _build_clickup_web_url(
                    ClickUpEntityType.PAGE,
                    team_id=workspace_id,
                    doc_id=doc_id,
                    page_id=page_id,
                )
            return self._handle_response(response, data_override=data)
        except Exception as e:
            logger.error(f"Error in get_doc_page: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="clickup",
        tool_name="create_doc",
        description="Create a doc in a workspace.",
        llm_description="Creates a doc in a workspace. Need workspace_id and name. Optional: parent_id+parent_type (type 4=Space, 5=Folder, 6=List, 7=Everything, 12=Workspace), visibility (PUBLIC/PRIVATE).",
        args_schema=CreateDocInput,
        returns="JSON with the created doc (includes doc id for create_doc_page)",
        primary_intent=ToolIntent.ACTION,
        category=ToolCategory.PROJECT_MANAGEMENT,
        when_to_use=[
            "User wants to create a new doc in a workspace",
            "User asks to add a doc (need workspace_id from get_authorized_teams_workspaces)",
        ],
        when_not_to_use=[
            "User wants to list docs (use get_workspace_docs)",
            "User wants to create a page in a doc (use create_doc_page)",
        ],
        typical_queries=["Create a doc", "New doc in workspace", "Add a doc"],
    )
    async def create_doc(
        self,
        workspace_id: str,
        name: str,
        parent_id: Optional[str] = None,
        parent_type: Optional[int] = None,
        visibility: Optional[str] = None,
    ) -> tuple[bool, str]:
        """Create a doc in a workspace."""
        parent = None
        if parent_id is not None and parent_type is not None:
            parent = {"id": parent_id, "type": parent_type}
        try:
            response = await self.client.create_doc(
                workspace_id,
                name=name,
                parent=parent,
                visibility=visibility,
                create_page=False,
            )
            if not response.success:
                return self._handle_response(response)
            data = dict(response.data) if isinstance(response.data, dict) else {}
            if data.get("id") and workspace_id:
                data["web_url"] = _build_clickup_web_url(
                    ClickUpEntityType.DOC, team_id=workspace_id, doc_id=str(data["id"])
                )
            return self._handle_response(response, data_override=data)
        except Exception as e:
            logger.error(f"Error in create_doc: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="clickup",
        tool_name="create_doc_page",
        description="Create a page in a ClickUp doc",
        llm_description="Creates a page in a doc. Need workspace_id and doc_id from get_workspace_docs; optional parent_page_id, name, sub_title, content, content_format.",
        args_schema=CreateDocPageInput,
        returns="JSON with the created page (includes page id for get_doc_page or update_doc_page)",
        primary_intent=ToolIntent.ACTION,
        category=ToolCategory.PROJECT_MANAGEMENT,
        when_to_use=[
            "User wants to add a page to a doc",
            "User asks to create a page in a doc (need doc_id from get_workspace_docs)",
        ],
        when_not_to_use=[
            "User wants to create a doc (use create_doc)",
            "User wants to list pages (use get_doc_pages)",
        ],
        typical_queries=["Add page to doc", "Create doc page", "New page in doc"],
    )
    async def create_doc_page(
        self,
        workspace_id: str,
        doc_id: str,
        parent_page_id: Optional[str] = None,
        name: str = "",
        sub_title: Optional[str] = None,
        content: str = "",
        content_format: str = "text/md",
    ) -> tuple[bool, str]:
        """Create a page in a doc."""
        try:
            response = await self.client.create_doc_page(
                workspace_id,
                doc_id,
                parent_page_id=parent_page_id,
                name=name,
                sub_title=sub_title,
                content=content,
                content_format=content_format,
            )
            if not response.success:
                return self._handle_response(response)
            data = dict(response.data) if isinstance(response.data, dict) else {}
            if data.get("id") and workspace_id and doc_id:
                data["web_url"] = _build_clickup_web_url(
                    ClickUpEntityType.PAGE,
                    team_id=workspace_id,
                    doc_id=doc_id,
                    page_id=str(data["id"]),
                )
            return self._handle_response(response, data_override=data)
        except Exception as e:
            logger.error(f"Error in create_doc_page: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="clickup",
        tool_name="update_doc_page",
        description="Edit or update a page in a doc",
        llm_description="Updates a doc page. Need workspace_id, doc_id from get_workspace_docs, page_id from get_doc_pages. Pass only fields to change (name, sub_title, content); content_edit_mode: replace, append, prepend.",
        args_schema=UpdateDocPageInput,
        returns="JSON with the updated page details",
        primary_intent=ToolIntent.ACTION,
        category=ToolCategory.PROJECT_MANAGEMENT,
        when_to_use=[
            "User wants to edit or update a doc page",
            "User asks to change page content or name (need page_id from get_doc_pages)",
        ],
        when_not_to_use=[
            "User wants to create a page (use create_doc_page)",
            "User wants to read a page (use get_doc_page)",
        ],
        typical_queries=["Edit doc page", "Update page content", "Change page name"],
    )
    async def update_doc_page(
        self,
        workspace_id: str,
        doc_id: str,
        page_id: str,
        name: Optional[str] = None,
        sub_title: Optional[str] = None,
        content: Optional[str] = None,
        content_edit_mode: str = "replace",
        content_format: str = "text/md",
    ) -> tuple[bool, str]:
        """Edit or update a doc page."""
        try:
            response = await self.client.update_doc_page(
                workspace_id,
                doc_id,
                page_id,
                name=name,
                sub_title=sub_title,
                content=content,
                content_edit_mode=content_edit_mode,
                content_format=content_format,
            )
            if not response.success:
                return self._handle_response(response)
            data = dict(response.data) if isinstance(response.data, dict) else {}
            if workspace_id and doc_id and page_id:
                data["web_url"] = _build_clickup_web_url(
                    ClickUpEntityType.PAGE,
                    team_id=workspace_id,
                    doc_id=doc_id,
                    page_id=page_id,
                )
            return self._handle_response(response, data_override=data)
        except Exception as e:
            logger.error(f"Error in update_doc_page: {e}")
            return False, json.dumps({"error": str(e)})
