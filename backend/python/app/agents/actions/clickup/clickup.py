import json
import logging
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, model_validator

from app.agent_loop_lib.tools.base import ParameterType, Tag, ToolParameter
from app.agent_loop_lib.tools.decorators import tool
from app.agents.actions.util.tool_summaries import (
    args_template,
    confirmation,
    entity_summary,
    list_summary,
)
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


def _clickup_task_label(task: dict) -> str:
    return task.get("name") or task.get("id") or "?"


def _clickup_comment_label(comment: dict) -> str:
    user = comment.get("user")
    username = user.get("username") if isinstance(user, dict) else None
    preview = (comment.get("comment_text") or "").strip().splitlines()[0][:60] if comment.get("comment_text") else ""
    if username and preview:
        return f"{username}: {preview}"
    return username or preview or "?"


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
        path="/tools/clickup/get_authorized_user",
        short_description="Get the authorized ClickUp user details",
        description="Returns the authenticated ClickUp user (id, username, email). Use to confirm who is logged in or get user context.",
        parameters=[],
        tags=[Tag(key="category", value="project_management"), Tag(key="type", value="read")],
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
        path="/tools/clickup/get_authorized_teams_workspaces",
        short_description="Get the authorized teams (workspaces)",
        description="Returns list of ClickUp workspaces (teams). Use the returned team id as team_id in get_spaces. Call this first when user asks for spaces, folders, or lists.",
        parameters=[],
        tags=[Tag(key="category", value="project_management"), Tag(key="type", value="read")],
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
        path="/tools/clickup/get_spaces",
        short_description="Get all spaces in a workspace",
        description="Returns spaces in a workspace. Need team_id from get_authorized_teams_workspaces.",
        parameters=[
            ToolParameter(name="team_id", type=ParameterType.STRING, description="Workspace (team) ID. Get from get_authorized_teams_workspaces.", required=True),
            ToolParameter(name="archived", type=ParameterType.BOOLEAN, description="Include archived spaces", required=False),
        ],
        tags=[Tag(key="category", value="project_management"), Tag(key="type", value="read")],
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
        path="/tools/clickup/get_folders",
        short_description="Get all folders in a space",
        description="Returns folders in a space. Need space_id from get_spaces. Use returned folder id for get_lists.",
        parameters=[
            ToolParameter(name="space_id", type=ParameterType.STRING, description="Space ID. Get from get_spaces.", required=True),
            ToolParameter(name="team_id", type=ParameterType.STRING, description="Workspace (team) ID. Get from get_authorized_teams_workspaces. Required for folder web_url.", required=True),
            ToolParameter(name="archived", type=ParameterType.BOOLEAN, description="Include archived folders", required=False),
        ],
        tags=[Tag(key="category", value="project_management"), Tag(key="type", value="read")],
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
        path="/tools/clickup/get_lists",
        short_description="Get all lists in a folder",
        description="Returns lists inside a folder. Need folder_id from get_folders. Use returned list id for get_tasks or create_task.",
        parameters=[
            ToolParameter(name="folder_id", type=ParameterType.STRING, description="Folder ID. Get from get_folders.", required=True),
            ToolParameter(name="team_id", type=ParameterType.STRING, description="Workspace (team) ID. Get from get_authorized_teams_workspaces. Required for list web_url.", required=True),
            ToolParameter(name="archived", type=ParameterType.BOOLEAN, description="Include archived lists", required=False),
        ],
        tags=[Tag(key="category", value="project_management"), Tag(key="type", value="read")],
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
        path="/tools/clickup/get_folderless_lists",
        short_description="Get folderless lists in a space",
        description="Returns lists that are not inside a folder. Need space_id from get_spaces. Use returned list id for get_tasks or create_task.",
        parameters=[
            ToolParameter(name="space_id", type=ParameterType.STRING, description="Space ID. Get from get_spaces.", required=True),
            ToolParameter(name="team_id", type=ParameterType.STRING, description="Workspace (team) ID. Get from get_authorized_teams_workspaces. Required for list web_url.", required=True),
            ToolParameter(name="archived", type=ParameterType.BOOLEAN, description="Include archived lists", required=False),
        ],
        tags=[Tag(key="category", value="project_management"), Tag(key="type", value="read")],
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
        path="/tools/clickup/create_space",
        short_description="Create a new space in a workspace",
        description="Creates a space in a workspace. Need team_id from get_authorized_teams_workspaces; name is required. Optional: multiple_assignees, features.",
        parameters=[
            ToolParameter(name="team_id", type=ParameterType.STRING, description="Workspace (team) ID. Get from get_authorized_teams_workspaces.", required=True),
            ToolParameter(name="name", type=ParameterType.STRING, description="Name of the new space.", required=True),
            ToolParameter(name="multiple_assignees", type=ParameterType.BOOLEAN, description="Enable multiple assignees in the space", required=False),
            ToolParameter(name="features", type=ParameterType.DICT, description="Space features configuration", required=False),
        ],
        tags=[Tag(key="category", value="project_management"), Tag(key="type", value="write")],
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
        path="/tools/clickup/create_folder",
        short_description="Create a new folder in a space",
        description="Creates a folder in a space. Need space_id from get_spaces; name is required.",
        parameters=[
            ToolParameter(name="space_id", type=ParameterType.STRING, description="Space ID. Get from get_spaces.", required=True),
            ToolParameter(name="name", type=ParameterType.STRING, description="Name of the new folder.", required=True),
            ToolParameter(name="team_id", type=ParameterType.STRING, description="Workspace (team) ID. Get from get_authorized_teams_workspaces. Used for web_url in response.", required=False),
        ],
        tags=[Tag(key="category", value="project_management"), Tag(key="type", value="write")],
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
        path="/tools/clickup/create_list",
        short_description="Create a list in a folder or a folderless list in a space",
        description=(
            "Creates a list in a folder (provide folder_id from get_folders) or a folderless list in a space "
            "(provide space_id from get_spaces). Name required. Exactly one of folder_id or space_id required. "
            "Optional: team_id for web_url, content, due_date, priority, assignee, status."
        ),
        parameters=[
            ToolParameter(name="folder_id", type=ParameterType.STRING, description="Folder ID from get_folders. Use to create a list inside a folder. Mutually exclusive with space_id.", required=False),
            ToolParameter(name="space_id", type=ParameterType.STRING, description="Space ID from get_spaces. Use to create a folderless list. Mutually exclusive with folder_id.", required=False),
            ToolParameter(name="name", type=ParameterType.STRING, description="Name of the new list.", required=True),
            ToolParameter(name="team_id", type=ParameterType.STRING, description="Workspace (team) ID. Get from get_authorized_teams_workspaces. Used for web_url in response.", required=False),
            ToolParameter(name="content", type=ParameterType.STRING, description="List description", required=False),
            ToolParameter(name="due_date", type=ParameterType.INTEGER, description="Due date as Unix timestamp (ms)", required=False),
            ToolParameter(name="due_date_time", type=ParameterType.BOOLEAN, description="Include time in due date", required=False),
            ToolParameter(name="priority", type=ParameterType.INTEGER, description="Priority: 1=Urgent, 2=High, 3=Normal, 4=Low", required=False),
            ToolParameter(name="assignee", type=ParameterType.INTEGER, description="Assignee user ID", required=False),
            ToolParameter(name="status", type=ParameterType.STRING, description="Status name", required=False),
        ],
        tags=[Tag(key="category", value="project_management"), Tag(key="type", value="write")],
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
        path="/tools/clickup/update_list",
        short_description="Update a list",
        description="Updates a list. Need list_id from get_lists or get_folderless_lists. Pass only fields to change; omit others to leave unchanged.",
        parameters=[
            ToolParameter(name="list_id", type=ParameterType.STRING, description="List ID. Get from get_lists or get_folderless_lists.", required=True),
            ToolParameter(name="name", type=ParameterType.STRING, description="New name (omit to leave unchanged)", required=False),
            ToolParameter(name="content", type=ParameterType.STRING, description="List description (omit to leave unchanged)", required=False),
            ToolParameter(name="due_date", type=ParameterType.INTEGER, description="Due date as Unix timestamp (ms)", required=False),
            ToolParameter(name="due_date_time", type=ParameterType.BOOLEAN, description="Include time in due date", required=False),
            ToolParameter(name="priority", type=ParameterType.INTEGER, description="Priority: 1=Urgent, 2=High, 3=Normal, 4=Low", required=False),
            ToolParameter(name="assignee_add", type=ParameterType.INTEGER, description="Add assignee by user ID", required=False),
            ToolParameter(name="assignee_rem", type=ParameterType.INTEGER, description="Remove assignee by user ID", required=False),
            ToolParameter(name="unset_status", type=ParameterType.BOOLEAN, description="Remove the status field", required=False),
        ],
        tags=[Tag(key="category", value="project_management"), Tag(key="type", value="write")],
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
        path="/tools/clickup/get_tasks",
        short_description="Search/filter tasks across the whole workspace",
        description=(
            "Returns tasks across a workspace matching filters. Need team_id from get_authorized_teams_workspaces. "
            "For 'assigned to me' or 'my tasks', call get_authorized_user first and pass assignees=[user_id]. "
            "Use for one workspace only (pick by name from get_authorized_teams_workspaces). 100 tasks per page; use page for more."
        ),
        parameters=[
            ToolParameter(name="team_id", type=ParameterType.STRING, description="Workspace (team) ID. Get from get_authorized_teams_workspaces.", required=True),
            ToolParameter(name="page", type=ParameterType.INTEGER, description="Page number (0-based). 0 = first page. 100 tasks per page.", required=False),
            ToolParameter(name="order_by", type=ParameterType.STRING, description="Order by: created, updated, due_date, start_date. Default: updated.", required=False),
            ToolParameter(name="reverse", type=ParameterType.BOOLEAN, description="Reverse sort order", required=False),
            ToolParameter(name="subtasks", type=ParameterType.BOOLEAN, description="Include subtasks", required=False),
            ToolParameter(name="statuses", type=ParameterType.LIST, description="Filter by status names", required=False),
            ToolParameter(name="include_closed", type=ParameterType.BOOLEAN, description="Include closed tasks", required=False),
            ToolParameter(name="assignees", type=ParameterType.LIST, description="Filter by assignee user IDs", required=False),
            ToolParameter(name="tags", type=ParameterType.LIST, description="Filter by tag names", required=False),
            ToolParameter(name="due_date_gt", type=ParameterType.INTEGER, description="Filter tasks due after (Unix ms)", required=False),
            ToolParameter(name="due_date_lt", type=ParameterType.INTEGER, description="Filter tasks due before (Unix ms)", required=False),
            ToolParameter(name="date_created_gt", type=ParameterType.INTEGER, description="Filter tasks created after (Unix ms)", required=False),
            ToolParameter(name="date_created_lt", type=ParameterType.INTEGER, description="Filter tasks created before (Unix ms)", required=False),
            ToolParameter(name="date_updated_gt", type=ParameterType.INTEGER, description="Filter tasks updated after (Unix ms)", required=False),
            ToolParameter(name="date_updated_lt", type=ParameterType.INTEGER, description="Filter tasks updated before (Unix ms)", required=False),
            ToolParameter(name="space_ids", type=ParameterType.LIST, description="Filter by space IDs", required=False),
            ToolParameter(name="project_ids", type=ParameterType.LIST, description="Filter by folder (project) IDs", required=False),
            ToolParameter(name="list_ids", type=ParameterType.LIST, description="Filter by list IDs", required=False),
            ToolParameter(name="custom_fields", type=ParameterType.LIST, description="Filter by custom field values", required=False),
        ],
        tags=[Tag(key="category", value="project_management"), Tag(key="type", value="read")],
        args_summary=args_template("Fetching ClickUp tasks for workspace {team_id}", "team_id"),
        result_summary=list_summary("tasks", _clickup_task_label, "task"),
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
        path="/tools/clickup/search_tasks",
        short_description="Search tasks by keyword or phrase",
        description=(
            "Returns tasks matching a keyword/phrase across the workspace. Creates a temporary view, fetches tasks, "
            "then deletes the view. Use for free-text search (e.g. 'login bug', 'invoice'). Get team_id from "
            "get_authorized_teams_workspaces. Prefer this over get_tasks when user asks for tasks containing specific text."
        ),
        parameters=[
            ToolParameter(name="team_id", type=ParameterType.STRING, description="Workspace (team) ID. Get from get_authorized_teams_workspaces.", required=True),
            ToolParameter(name="keyword", type=ParameterType.STRING, description="Search string for task name, description, and custom field text.", required=True),
            ToolParameter(name="show_closed", type=ParameterType.BOOLEAN, description="Include closed (completed) tasks in search results", required=False),
            ToolParameter(name="page", type=ParameterType.INTEGER, description="Page number (0-based). 100 tasks per page.", required=False),
        ],
        tags=[Tag(key="category", value="project_management"), Tag(key="type", value="read")],
        args_summary=args_template('Searching ClickUp tasks: "{keyword}"', "keyword"),
        result_summary=list_summary("tasks", _clickup_task_label, "task"),
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
        path="/tools/clickup/get_task",
        short_description="Get a specific task's details",
        description="Returns one task by task_id. Get task_id from get_tasks, create_task, or search_tasks. Use for 'show task X', 'details of task Y'.",
        parameters=[
            ToolParameter(name="task_id", type=ParameterType.STRING, description="Task ID. Get from get_tasks, create_task, or search_tasks.", required=True),
        ],
        tags=[Tag(key="category", value="project_management"), Tag(key="type", value="read")],
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
        path="/tools/clickup/create_task",
        short_description="Create a new task or subtask in a list",
        description=(
            "Creates a task. Need list_id from get_lists or get_folderless_lists; name is required. "
            "Optional: description, status, priority, assignees, parent (for subtasks). Returns the created task including task id."
        ),
        parameters=[
            ToolParameter(name="list_id", type=ParameterType.STRING, description="List ID. Get from get_lists or get_folderless_lists.", required=True),
            ToolParameter(name="name", type=ParameterType.STRING, description="Task name", required=True),
            ToolParameter(name="description", type=ParameterType.STRING, description="Task description", required=False),
            ToolParameter(name="status", type=ParameterType.STRING, description="Status name (e.g. to do, in progress)", required=False),
            ToolParameter(name="priority", type=ParameterType.INTEGER, description="Priority: 1=Urgent, 2=High, 3=Normal, 4=Low", required=False),
            ToolParameter(name="assignees", type=ParameterType.LIST, description="Assignee user IDs (e.g. from get_authorized_user or get_list_members)", required=False),
            ToolParameter(name="parent", type=ParameterType.STRING, description="Parent task ID to create this as a subtask", required=False),
        ],
        tags=[Tag(key="category", value="project_management"), Tag(key="type", value="write")],
        args_summary=args_template('Creating ClickUp task "{name}"', "name"),
        result_summary=entity_summary(lambda e: f"Created task: {_clickup_task_label(e)}"),
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
        path="/tools/clickup/update_task",
        short_description="Update an existing task",
        description=(
            "Updates a task. Need task_id from get_tasks, create_task, or search_tasks. "
            "Pass only fields to change (name, description, status, priority, due_date, start_date, "
            "assignees_add/assignees_rem, archived, etc.); omit others to leave unchanged."
        ),
        parameters=[
            ToolParameter(name="task_id", type=ParameterType.STRING, description="Task ID. Get from get_tasks, create_task, or search_tasks.", required=True),
            ToolParameter(name="name", type=ParameterType.STRING, description="New task name (omit to leave unchanged)", required=False),
            ToolParameter(name="description", type=ParameterType.STRING, description="New task description, plain text (omit to leave unchanged)", required=False),
            ToolParameter(name="markdown_description", type=ParameterType.STRING, description="New task description in markdown (omit to leave unchanged)", required=False),
            ToolParameter(name="status", type=ParameterType.STRING, description="New status name (omit to leave unchanged)", required=False),
            ToolParameter(name="priority", type=ParameterType.INTEGER, description="Priority: 1=Urgent, 2=High, 3=Normal, 4=Low (omit to leave unchanged)", required=False),
            ToolParameter(name="due_date", type=ParameterType.INTEGER, description="Due date as Unix timestamp in ms (omit to leave unchanged)", required=False),
            ToolParameter(name="due_date_time", type=ParameterType.BOOLEAN, description="Include time in due date (omit to leave unchanged)", required=False),
            ToolParameter(name="time_estimate", type=ParameterType.INTEGER, description="Time estimate in milliseconds (omit to leave unchanged)", required=False),
            ToolParameter(name="start_date", type=ParameterType.INTEGER, description="Start date as Unix timestamp in ms (omit to leave unchanged)", required=False),
            ToolParameter(name="start_date_time", type=ParameterType.BOOLEAN, description="Include time in start date (omit to leave unchanged)", required=False),
            ToolParameter(name="assignees_add", type=ParameterType.LIST, description="User IDs to add as assignees", required=False),
            ToolParameter(name="assignees_rem", type=ParameterType.LIST, description="User IDs to remove from assignees", required=False),
            ToolParameter(name="archived", type=ParameterType.BOOLEAN, description="Archive or unarchive the task", required=False),
            ToolParameter(name="custom_task_ids", type=ParameterType.BOOLEAN, description="Use custom task IDs; requires team_id if true", required=False),
            ToolParameter(name="team_id", type=ParameterType.STRING, description="Team ID (required when custom_task_ids is true)", required=False),
        ],
        tags=[Tag(key="category", value="project_management"), Tag(key="type", value="write")],
        args_summary=args_template("Updating ClickUp task {task_id}", "task_id"),
        result_summary=entity_summary(lambda e: f"Updated task: {_clickup_task_label(e)}"),
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
        path="/tools/clickup/get_comments",
        short_description="Get comments on a task or replies to a comment",
        description=(
            "Returns comments on a task (pass task_id) or replies to a comment (pass comment_id; optionally task_id for web_url). "
            "Get task_id from get_tasks, get_task, create_task, or search_tasks; comment_id from get_comments. "
            "For task comments: optional custom_task_ids, team_id, start, start_id."
        ),
        parameters=[
            ToolParameter(name="task_id", type=ParameterType.STRING, description="Task ID to list all comments on the task. Get from get_tasks, get_task, create_task, or search_tasks.", required=False),
            ToolParameter(name="comment_id", type=ParameterType.STRING, description="Comment ID to list replies (thread). Get from get_comments. When set, returns only replies; optionally pass task_id for web_url.", required=False),
            ToolParameter(name="custom_task_ids", type=ParameterType.BOOLEAN, description="Use custom task IDs (only when task_id is set). If true, team_id is required.", required=False),
            ToolParameter(name="team_id", type=ParameterType.STRING, description="Workspace (team) ID. Required when custom_task_ids is true. Optional when comment_id is set (for web_url).", required=False),
            ToolParameter(name="start", type=ParameterType.INTEGER, description="Start timestamp for pagination (only when task_id is set).", required=False),
            ToolParameter(name="start_id", type=ParameterType.STRING, description="Start comment ID for pagination (only when task_id is set).", required=False),
        ],
        tags=[Tag(key="category", value="project_management"), Tag(key="type", value="read")],
        args_summary=lambda args: (
            f"Fetching replies to ClickUp comment {args['comment_id']}"
            if args.get("comment_id")
            else f"Fetching comments on ClickUp task {args.get('task_id', '?')}"
        ),
        result_summary=list_summary("comments", _clickup_comment_label, "comment"),
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
        path="/tools/clickup/create_task_comment",
        short_description="Add a comment to a task or a reply to a comment",
        description=(
            "Creates a top-level comment on a task (provide task_id) or a reply to a comment "
            "(provide comment_id from get_comments). comment_text required. Exactly one of task_id or comment_id required. "
            "Optional: assignee, notify_all; for task_id only: custom_task_ids, team_id."
        ),
        parameters=[
            ToolParameter(name="task_id", type=ParameterType.STRING, description="Task ID for a new top-level comment, or for web_url when replying. Get from get_tasks, get_task, create_task, or search_tasks.", required=False),
            ToolParameter(name="comment_id", type=ParameterType.STRING, description="Comment ID for a reply (threaded comment). Get from get_comments. When set, creates a reply; optionally pass task_id for web_url in response.", required=False),
            ToolParameter(name="comment_text", type=ParameterType.STRING, description="The comment or reply text (plain text).", required=True),
            ToolParameter(name="assignee", type=ParameterType.INTEGER, description="Assign the comment/reply to a user ID.", required=False),
            ToolParameter(name="notify_all", type=ParameterType.BOOLEAN, description="Notify all assignees.", required=False),
            ToolParameter(name="custom_task_ids", type=ParameterType.BOOLEAN, description="Use custom task IDs (only when task_id is set for new comment). If true, team_id is required.", required=False),
            ToolParameter(name="team_id", type=ParameterType.STRING, description="Workspace (team) ID. Required when task_id is set and custom_task_ids is true.", required=False),
        ],
        tags=[Tag(key="category", value="project_management"), Tag(key="type", value="write")],
        args_summary=lambda args: (
            f"Replying to ClickUp comment {args['comment_id']}"
            if args.get("comment_id")
            else f"Commenting on ClickUp task {args.get('task_id', '?')}"
        ),
        result_summary=confirmation("Comment added"),
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
        path="/tools/clickup/create_checklist",
        short_description="Create a checklist on a task",
        description="Creates a checklist on a task. Need task_id from get_tasks or create_task; name required. Returns checklist id for create_checklist_item. Optional: custom_task_ids, team_id.",
        parameters=[
            ToolParameter(name="task_id", type=ParameterType.STRING, description="Task ID. Get from get_tasks, get_task, create_task, or search_tasks.", required=True),
            ToolParameter(name="name", type=ParameterType.STRING, description="Checklist name.", required=True),
            ToolParameter(name="custom_task_ids", type=ParameterType.BOOLEAN, description="Use custom task IDs. If true, team_id is required.", required=False),
            ToolParameter(name="team_id", type=ParameterType.STRING, description="Workspace (team) ID. Required when custom_task_ids is true.", required=False),
        ],
        tags=[Tag(key="category", value="project_management"), Tag(key="type", value="write")],
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
        path="/tools/clickup/create_checklist_item",
        short_description="Add an item to a checklist",
        description="Adds an item to a checklist. Need checklist_id from task checklists (get_task) or create_checklist; name required. Optional: assignee.",
        parameters=[
            ToolParameter(name="checklist_id", type=ParameterType.STRING, description="Checklist ID. Get from task checklists (get_task) or create_checklist response.", required=True),
            ToolParameter(name="name", type=ParameterType.STRING, description="Checklist item name.", required=True),
            ToolParameter(name="assignee", type=ParameterType.INTEGER, description="Assignee user ID.", required=False),
        ],
        tags=[Tag(key="category", value="project_management"), Tag(key="type", value="write")],
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
        path="/tools/clickup/update_checklist_item",
        short_description="Update or check/uncheck a checklist item",
        description="Updates a checklist item (name, assignee, resolved/checked, parent). Need checklist_id and checklist_item_id from task checklists or create_checklist_item. Pass only fields to change.",
        parameters=[
            ToolParameter(name="checklist_id", type=ParameterType.STRING, description="Checklist ID. Get from task checklists or create_checklist response.", required=True),
            ToolParameter(name="checklist_item_id", type=ParameterType.STRING, description="Checklist item ID. Get from checklist items or create_checklist_item response.", required=True),
            ToolParameter(name="name", type=ParameterType.STRING, description="New item name (omit to leave unchanged).", required=False),
            ToolParameter(name="assignee", type=ParameterType.INTEGER, description="Assignee user ID.", required=False),
            ToolParameter(name="resolved", type=ParameterType.BOOLEAN, description="Mark item resolved (checked) or unresolved.", required=False),
            ToolParameter(name="parent", type=ParameterType.STRING, description="Parent checklist item ID for nesting.", required=False),
        ],
        tags=[Tag(key="category", value="project_management"), Tag(key="type", value="write")],
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
        path="/tools/clickup/get_workspace_docs",
        short_description="List docs in a workspace",
        description="Returns docs in a workspace. Pass only workspace_id unless user asks to filter (e.g. my docs, first 10).",
        parameters=[
            ToolParameter(name="workspace_id", type=ParameterType.STRING, description="Workspace ID. Same as team id from get_authorized_teams_workspaces.", required=True),
            ToolParameter(name="creator", type=ParameterType.INTEGER, description="Only set when user asks 'my docs' (use get_authorized_user id). Leave unset for list all docs.", required=False),
            ToolParameter(name="parent_id", type=ParameterType.STRING, description="Only set when user asks docs under a specific parent. Leave unset for list all docs.", required=False),
            ToolParameter(name="parent_type", type=ParameterType.STRING, description="Only set when user explicitly filters by parent type. Leave unset for list all docs; do not use WORKSPACE.", required=False),
            ToolParameter(name="limit", type=ParameterType.INTEGER, description="Only set when user asks to limit (e.g. 'first 10 docs'); use 10 then. Leave unset for list all docs.", required=False),
            ToolParameter(name="cursor", type=ParameterType.STRING, description="Cursor for next page; only when paginating. Leave unset for first page.", required=False),
        ],
        tags=[Tag(key="category", value="project_management"), Tag(key="type", value="read")],
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
        path="/tools/clickup/get_doc_pages",
        short_description="List pages in a doc",
        description="Returns pages in a doc. Need workspace_id and doc_id from get_workspace_docs. Use returned page ids for get_doc_page.",
        parameters=[
            ToolParameter(name="workspace_id", type=ParameterType.STRING, description="Workspace ID. Same as team id from get_authorized_teams_workspaces.", required=True),
            ToolParameter(name="doc_id", type=ParameterType.STRING, description="Doc ID. Get from get_workspace_docs.", required=True),
        ],
        tags=[Tag(key="category", value="project_management"), Tag(key="type", value="read")],
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
        path="/tools/clickup/get_doc_page",
        short_description="Get details of a page",
        description="Returns full details of one page. Need workspace_id, doc_id from get_workspace_docs, and page_id from get_doc_pages.",
        parameters=[
            ToolParameter(name="workspace_id", type=ParameterType.STRING, description="Workspace ID. Same as team id from get_authorized_teams_workspaces.", required=True),
            ToolParameter(name="doc_id", type=ParameterType.STRING, description="Doc ID. Get from get_workspace_docs.", required=True),
            ToolParameter(name="page_id", type=ParameterType.STRING, description="Page ID. Get from get_doc_pages.", required=True),
        ],
        tags=[Tag(key="category", value="project_management"), Tag(key="type", value="read")],
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
        path="/tools/clickup/create_doc",
        short_description="Create a doc in a workspace",
        description=(
            "Creates a doc in a workspace. Need workspace_id and name. Optional: parent_id+parent_type "
            "(type 4=Space, 5=Folder, 6=List, 7=Everything, 12=Workspace), visibility (PUBLIC/PRIVATE)."
        ),
        parameters=[
            ToolParameter(name="workspace_id", type=ParameterType.STRING, description="Workspace ID. Same as team id from get_authorized_teams_workspaces.", required=True),
            ToolParameter(name="name", type=ParameterType.STRING, description="Name of the new doc.", required=True),
            ToolParameter(name="parent_id", type=ParameterType.STRING, description="Parent id (e.g. space_id, folder_id, list_id). Required if parent_type is set.", required=False),
            ToolParameter(name="parent_type", type=ParameterType.INTEGER, description="Parent type: 4=Space, 5=Folder, 6=List, 7=Everything, 12=Workspace. Use with parent_id.", required=False),
            ToolParameter(name="visibility", type=ParameterType.STRING, description="Visibility: PUBLIC or PRIVATE.", required=False),
        ],
        tags=[Tag(key="category", value="project_management"), Tag(key="type", value="write")],
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
        path="/tools/clickup/create_doc_page",
        short_description="Create a page in a ClickUp doc",
        description="Creates a page in a doc. Need workspace_id and doc_id from get_workspace_docs; optional parent_page_id, name, sub_title, content, content_format.",
        parameters=[
            ToolParameter(name="workspace_id", type=ParameterType.STRING, description="Workspace ID. Same as team id from get_authorized_teams_workspaces.", required=True),
            ToolParameter(name="doc_id", type=ParameterType.STRING, description="Doc ID. Get from get_workspace_docs.", required=True),
            ToolParameter(name="parent_page_id", type=ParameterType.STRING, description="Parent page ID. Omit for a root page in the doc.", required=False),
            ToolParameter(name="name", type=ParameterType.STRING, description="Name of the new page.", required=False),
            ToolParameter(name="sub_title", type=ParameterType.STRING, description="Subtitle of the new page.", required=False),
            ToolParameter(name="content", type=ParameterType.STRING, description="Content of the new page.", required=False),
            ToolParameter(name="content_format", type=ParameterType.STRING, description="Content format: text/md (markdown) or text/plain.", required=False),
        ],
        tags=[Tag(key="category", value="project_management"), Tag(key="type", value="write")],
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
        path="/tools/clickup/update_doc_page",
        short_description="Edit or update a page in a doc",
        description=(
            "Updates a doc page. Need workspace_id, doc_id from get_workspace_docs, page_id from get_doc_pages. "
            "Pass only fields to change (name, sub_title, content); content_edit_mode: replace, append, prepend."
        ),
        parameters=[
            ToolParameter(name="workspace_id", type=ParameterType.STRING, description="Workspace ID. Same as team id from get_authorized_teams_workspaces.", required=True),
            ToolParameter(name="doc_id", type=ParameterType.STRING, description="Doc ID. Get from get_workspace_docs.", required=True),
            ToolParameter(name="page_id", type=ParameterType.STRING, description="Page ID. Get from get_doc_pages.", required=True),
            ToolParameter(name="name", type=ParameterType.STRING, description="Updated name of the page (omit to leave unchanged).", required=False),
            ToolParameter(name="sub_title", type=ParameterType.STRING, description="Updated subtitle (omit to leave unchanged).", required=False),
            ToolParameter(name="content", type=ParameterType.STRING, description="Updated content (omit to leave unchanged).", required=False),
            ToolParameter(name="content_edit_mode", type=ParameterType.STRING, description="How to update content: replace, append, or prepend.", required=False),
            ToolParameter(name="content_format", type=ParameterType.STRING, description="Content format: text/md or text/plain.", required=False),
        ],
        tags=[Tag(key="category", value="project_management"), Tag(key="type", value="write")],
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
