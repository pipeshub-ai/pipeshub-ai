import difflib
import json
import logging
import re
import traceback
from typing import Any, Optional

from pydantic import BaseModel, Field, model_validator

from app.agents.actions.response_transformer import ResponseTransformer
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
from app.connectors.core.registry.types import AuthField, DocumentationLink
from app.connectors.sources.atlassian.core.oauth import AtlassianScope
from app.sources.client.http.exception.exception import HttpStatusCode
from app.sources.client.http.http_response import HTTPResponse
from app.sources.client.jira.jira import JiraClient
from app.sources.external.jira.jira import JiraDataSource

logger = logging.getLogger(__name__)

# Pydantic schemas for Jira tools
class CreateIssueInput(BaseModel):
    """Schema for creating JIRA issues"""
    project_key: str = Field(description="JIRA project key (e.g., 'PA')")
    summary: str = Field(description="Issue summary")
    issue_type_name: str = Field(description="Issue type (e.g., 'Task', 'Bug', 'Story')")
    description: Optional[str] = Field(default=None, description="Issue description")
    assignee_account_id: Optional[str] = Field(default=None, description="Assignee account ID")
    assignee_query: Optional[str] = Field(default=None, description="Name or email to resolve assignee")
    priority_name: Optional[str] = Field(default=None, description="Priority")
    labels: list[str] | None = Field(default=None, description="List of labels")
    components: list[str] | None = Field(default=None, description="List of component names")
    parent_key: str | None = Field(default=None, description="Parent issue key")
    custom_fields: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Required or optional project-specific fields as {field_id: value}. "
            "Call get_create_issue_fields first to discover required field IDs and value formats. "
            "Example: {'customfield_10016': 5, 'duedate': '2026-06-30'}"
        )
    )

    @model_validator(mode='before')
    @classmethod
    def extract_nested_values(cls, data: dict) -> dict:
        """Extract values from nested structures that LLMs might use"""
        if isinstance(data, dict):
            normalized = dict(data)

            # Handle project_key variations
            for key in ['project', 'projectKey', 'project_key']:
                if key in normalized and 'project_key' not in normalized:
                    normalized['project_key'] = normalized[key]
                    if key != 'project_key':
                        normalized.pop(key, None)

            # Handle issue_type_name variations
            if 'issuetype' in normalized and isinstance(normalized['issuetype'], dict):
                # Extract from nested structure like {"issuetype": {"name": "Task"}}
                if 'name' in normalized['issuetype']:
                    normalized['issue_type_name'] = normalized['issuetype']['name']
                elif 'issue_type_name' not in normalized:
                    normalized['issue_type_name'] = str(normalized['issuetype'])
                normalized.pop('issuetype', None)
            elif 'issue_type' in normalized and 'issue_type_name' not in normalized:
                normalized['issue_type_name'] = normalized['issue_type']
                normalized.pop('issue_type', None)

            return normalized
        return data

    class Config:
        populate_by_name = True
        extra = 'ignore'


class GetIssuesInput(BaseModel):
    """Schema for getting issues from a project"""
    project_key: str = Field(description="Project key (e.g., 'PA')")
    days: Optional[int] = Field(default=None, description="Days to look back")
    max_results: Optional[int] = Field(default=None, description="Max results")

    @model_validator(mode='before')
    @classmethod
    def extract_project_key(cls, data: dict) -> dict:
        """Extract project_key from various field names that LLMs might use"""
        if isinstance(data, dict):
            normalized = dict(data)
            for key in ['project', 'projectKey', 'project_key']:
                if key in normalized and 'project_key' not in normalized:
                    normalized['project_key'] = normalized[key]
                    if key != 'project_key':
                        normalized.pop(key, None)
            return normalized
        return data

    class Config:
        populate_by_name = True
        extra = 'ignore'


class GetIssueInput(BaseModel):
    """Schema for getting a specific issue"""
    issue_key: str = Field(description="Issue key (e.g., 'PA-123')")

    @model_validator(mode='before')
    @classmethod
    def extract_issue_key(cls, data: dict) -> dict:
        """Extract issue_key from various field names that LLMs might use"""
        if isinstance(data, dict):
            normalized = dict(data)
            for key in ['issueId', 'issueIdOrKey', 'issue_id', 'issue_key', 'issueKey']:
                if key in normalized and 'issue_key' not in normalized:
                    normalized['issue_key'] = normalized[key]
                    if key != 'issue_key':
                        normalized.pop(key, None)
            return normalized
        return data

    class Config:
        populate_by_name = True
        extra = 'ignore'


class SearchIssuesInput(BaseModel):
    """Schema for searching issues using JQL"""
    jql: str = Field(description="JQL query with time filter")
    maxResults: Optional[int] = Field(default=50, description="Max results")


class AddCommentInput(BaseModel):
    """Schema for adding a comment"""
    issue_key: str = Field(description="Issue key")
    comment: str = Field(description="Comment text")

    @model_validator(mode='before')
    @classmethod
    def extract_issue_key(cls, data: dict) -> dict:
        """Extract issue_key from various field names that LLMs might use"""
        if isinstance(data, dict):
            # Create a new dict to avoid modification during iteration
            normalized = dict(data)

            # Check all possible variations and normalize to issue_key
            for key in ['issueId', 'issueIdOrKey', 'issue_id', 'issue_key', 'issueKey']:
                if key in normalized and 'issue_key' not in normalized:
                    normalized['issue_key'] = normalized[key]
                    # Remove the alternate key to avoid confusion
                    if key != 'issue_key':
                        normalized.pop(key, None)

            return normalized
        return data

    class Config:
        populate_by_name = True
        # Allow extra fields to be ignored (LLM might send extra params)
        extra = 'ignore'


class SearchUsersInput(BaseModel):
    """Schema for searching users"""
    query: str = Field(description="Search query (name or email)")
    max_results: Optional[int] = Field(default=50, description="Max results")


class UpdateIssueInput(BaseModel):
    """Schema for updating a JIRA issue"""
    issue_key: str = Field(description="Issue key (e.g., 'PA-123')")
    summary: Optional[str] = Field(default=None, description="Issue summary")
    description: Optional[str] = Field(default=None, description="Issue description")
    issue_type_name: Optional[str] = Field(default=None, description="New issue type (e.g., 'Story', 'Bug') — call get_create_issue_fields first to discover required fields for the target type")
    assignee_account_id: Optional[str] = Field(default=None, description="Assignee account ID")
    assignee_query: Optional[str] = Field(default=None, description="Name or email to resolve assignee")
    reporter_account_id: Optional[str] = Field(default=None, description="Reporter account ID")
    reporter_query: Optional[str] = Field(default=None, description="Name or email to resolve reporter")
    priority_name: Optional[str] = Field(default=None, description="Priority")
    labels: list[str] | None = Field(default=None, description="List of labels")
    components: list[str] | None = Field(default=None, description="List of component names")
    status: str | None = Field(default=None, description="Issue status (e.g., 'In Progress', 'Done')")
    custom_fields: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Required or optional fields as {field_id: value}. "
            "Needed when changing issue type — call get_create_issue_fields first to discover "
            "which fields are required for the target type. "
            "Example: {'customfield_10016': 5, 'duedate': '2026-06-30'}"
        )
    )

    @model_validator(mode='before')
    @classmethod
    def extract_nested_values(cls, data: dict) -> dict:
        """Extract values from nested structures that LLMs might use"""
        if isinstance(data, dict):
            # Create normalized dict
            normalized = dict(data)

            # Handle issue_key variations FIRST (before processing update wrapper)
            for key in ['issueId', 'issueIdOrKey', 'issue_id', 'issue_key', 'issueKey']:
                if key in normalized and 'issue_key' not in normalized:
                    normalized['issue_key'] = normalized[key]
                    if key != 'issue_key':
                        normalized.pop(key, None)

            # Fields that map directly to top-level schema fields
            _top_level = [
                'assignee_account_id', 'assignee_query', 'reporter_account_id',
                'reporter_query', 'priority_name', 'labels', 'components',
                'status', 'issue_type_name', 'custom_fields',
            ]

            # Handle issue_type_name variations
            for key in ['issuetype', 'issue_type', 'issueType']:
                if key in normalized:
                    if 'issue_type_name' not in normalized:
                        val = normalized[key]
                        if isinstance(val, dict):
                            normalized['issue_type_name'] = val.get('name', str(val))
                        else:
                            normalized['issue_type_name'] = str(val)
                    normalized.pop(key, None)

            # Handle direct 'fields' key (LLM sometimes sends this directly, not nested in update/updateData)
            if 'fields' in normalized and isinstance(normalized['fields'], dict):
                fields = normalized['fields']
                if 'summary' in fields:
                    normalized['summary'] = fields['summary']
                if 'description' in fields:
                    normalized['description'] = fields['description']
                for field in _top_level:
                    if field in fields:
                        normalized[field] = fields[field]
                normalized.pop('fields', None)

            # Handle update/updateData wrapper
            update_wrapper = normalized.get('update') or normalized.get('updateData')
            if update_wrapper and isinstance(update_wrapper, dict):
                if 'fields' in update_wrapper:
                    fields = update_wrapper['fields']
                    if isinstance(fields, dict):
                        if 'summary' in fields:
                            normalized['summary'] = fields['summary']
                        if 'description' in fields:
                            normalized['description'] = fields['description']
                        for field in _top_level:
                            if field in fields:
                                normalized[field] = fields[field]
                if 'issue_key' not in normalized:
                    for key in ['issueId', 'issueIdOrKey', 'issue_id', 'issue_key', 'issueKey']:
                        if key in update_wrapper:
                            normalized['issue_key'] = update_wrapper[key]
                            break
                normalized.pop('update', None)
                normalized.pop('updateData', None)

            return normalized
        return data

    class Config:
        populate_by_name = True
        extra = 'ignore'  # Allow both field name and alias

class GetProjectMetadataInput(BaseModel):
    """Schema for getting project metadata"""
    project_key: str = Field(description="Project key (e.g., 'PA')")

    @model_validator(mode='before')
    @classmethod
    def extract_project_key(cls, data: dict) -> dict:
        """Extract project_key from various field names that LLMs might use"""
        if isinstance(data, dict):
            normalized = dict(data)
            for key in ['project', 'projectKey', 'project_key']:
                if key in normalized and 'project_key' not in normalized:
                    normalized['project_key'] = normalized[key]
                    if key != 'project_key':
                        normalized.pop(key, None)
            return normalized
        return data

    class Config:
        populate_by_name = True
        extra = 'ignore'

class GetProjectInput(BaseModel):
    """Schema for getting a specific JIRA project"""
    project_key: str = Field(description="Project key (e.g., 'PA')")

class ConvertTextToAdfInput(BaseModel):
    """Schema for converting plain text to ADF"""
    text: str = Field(description="Plain text to convert")

class GetCommentsInput(BaseModel):
    """Schema for getting a specific JIRA comment"""
    issue_key: str = Field(description="Issue key (e.g., 'PA-123')")


class GetCreateIssueFieldsInput(BaseModel):
    """Schema for fetching create-issue field metadata for a project and issue type"""
    project_key: str = Field(description="Project key (e.g., 'PA')")
    issue_type_name: str = Field(description="Issue type name (e.g., 'Task', 'Bug', 'Story')")

    @model_validator(mode='before')
    @classmethod
    def normalize(cls, data: dict) -> dict:
        if isinstance(data, dict):
            normalized = dict(data)
            for key in ['project', 'projectKey', 'project_key']:
                if key in normalized:
                    if 'project_key' not in normalized:
                        normalized['project_key'] = normalized[key]
                    if key != 'project_key':
                        normalized.pop(key, None)
            for key in ['issueType', 'issue_type', 'issuetype']:
                if key in normalized:
                    if 'issue_type_name' not in normalized:
                        normalized['issue_type_name'] = normalized[key]
                    if key != 'issue_type_name':
                        normalized.pop(key, None)
            return normalized
        return data

    class Config:
        populate_by_name = True
        extra = 'ignore'


# ---------------------------------------------------------------------------
# Agent-activity summary labels — shared by the `args_summary`/`result_
# summary` declarations on the `@tool`s below. Every success envelope here
# is `{"message": ..., "data": ...}` (see `Jira._handle_response`), so the
# `list_summary`/`entity_summary`/`confirmation` factories from
# `app/agents/actions/util/tool_summaries.py` cover the parsing; these are
# just the per-item/per-entity label functions specific to Jira's shape.
# ---------------------------------------------------------------------------


def _jira_issue_label(issue: dict[str, Any]) -> str:
    key = issue.get("key") or "?"
    fields = issue.get("fields")
    summary = fields.get("summary") if isinstance(fields, dict) else None
    return f"{key}: {summary}" if summary else key


def _jira_project_label(project: dict[str, Any]) -> str:
    key = project.get("key") or "?"
    name = project.get("name")
    return f"{key}: {name}" if name else key


def _jira_user_label(user: dict[str, Any]) -> str:
    return user.get("displayName") or user.get("accountId") or "?"


def _jira_comment_label(comment: dict[str, Any]) -> str:
    author = comment.get("author")
    name = author.get("displayName") if isinstance(author, dict) else None
    return name or "comment"


# Register JIRA toolset
@ToolsetBuilder("Jira")\
    .in_group("Atlassian")\
    .with_description("JIRA integration for issue tracking, project management, and team collaboration")\
    .with_category(ToolsetCategory.APP)\
    .with_auth([
        AuthBuilder.type(AuthType.OAUTH).oauth(
            connector_name="JIRA",
            authorize_url="https://auth.atlassian.com/authorize",
            token_url="https://auth.atlassian.com/oauth/token",
            redirect_uri="toolsets/oauth/callback/jira",
            scopes=OAuthScopeConfig(
                personal_sync=[],
                team_sync=[],
                agent=AtlassianScope.get_jira_read_access() + [
                    # Write scopes for creating/updating issues and comments
                    AtlassianScope.JIRA_WORK_WRITE.value,  # For create_issue and add_comment
                ]
            ),
            fields=[
                CommonFields.client_id("Atlassian Developer Console"),
                CommonFields.client_secret("Atlassian Developer Console"),
            ],
            icon_path=IconPaths.connector_icon("jira"),
            app_group="Project Management",
            app_description="JIRA OAuth application for agent integration"
        ),
        AuthBuilder.type(AuthType.API_TOKEN).fields([
            AuthField(
                name="baseUrl",
                display_name="Base URL",
                placeholder="https://yourcompany.atlassian.net",
                description="The base URL of your Atlassian instance",
                field_type="URL",
                required=True,
                usage="CONFIGURE",
                max_length=2000,
                is_secret=False,
            ),
            AuthField(
                name="email",
                display_name="Email",
                placeholder="your-email@company.com",
                description="Your Atlassian account email",
                field_type="TEXT",
                required=True,
                usage="AUTHENTICATE",
                max_length=500,
                is_secret=False,
            ),
            AuthField(
                name="apiToken",
                display_name="API Token",
                placeholder="your-api-token",
                description="API token from Atlassian account settings",
                field_type="PASSWORD",
                required=True,
                usage="AUTHENTICATE",
                max_length=2000,
                is_secret=True,
            ),
        ])
    ])\
    .configure(lambda builder: builder.with_icon(IconPaths.connector_icon("jira"))
        .add_documentation_link(DocumentationLink(
            "Jira Cloud API Setup",
            "https://developer.atlassian.com/cloud/jira/platform/rest/v3/intro/",
            "setup",
        ))
        .add_documentation_link(DocumentationLink(
            "Pipeshub Documentation",
            "https://docs.pipeshub.com/toolsets/jira/jira",
            "pipeshub",
        )))\
    .build_decorator()
class Jira:
    """JIRA tool exposed to the agents using JiraDataSource"""

    def __init__(self, client: JiraClient) -> None:
        """Initialize the JIRA tool

        Args:
            client: JIRA client object
        """
        self.client = JiraDataSource(client)
        self._site_url = None  # Cache for site URL
        self._field_schema_cache: Optional[dict[str, dict[str, str]]] = None  # Cache for field schema mapping
        self._create_fields_cache: dict[str, list[dict[str, Any]]] = {}  # Cache for create-field metadata per project+issue type

    def _handle_response(
        self,
        response: HTTPResponse,
        success_message: str,
        include_guidance: bool = False
    ) -> tuple[bool, str]:
        """Handle HTTP response and return standardized tuple.

        Args:
            response: HTTP response object
            success_message: Message to return on success
            include_guidance: Whether to include error guidance

        Returns:
            Tuple of (success_flag, json_string)
        """
        if response.status in [HttpStatusCode.SUCCESS.value, HttpStatusCode.CREATED.value, HttpStatusCode.NO_CONTENT.value]:
            try:
                data = response.json() if response.status != HttpStatusCode.NO_CONTENT else {}
                return True, json.dumps({
                    "message": success_message,
                    "data": data
                })
            except Exception as e:
                logger.error(f"Error parsing response: {e}")
                return True, json.dumps({
                    "message": success_message,
                    "data": {}
                })
        else:
            # Extract error information from response
            error_text = ""
            error_message = None
            error_details = None

            try:
                # Try to parse JSON error response
                if response.is_json:
                    error_data = response.json()
                    # JIRA API error responses can have different structures
                    if isinstance(error_data, dict):
                        # Common JIRA error fields
                        error_message = (
                            error_data.get("error") or
                            error_data.get("message") or
                            error_data.get("errorMessages", [None])[0] if isinstance(error_data.get("errorMessages"), list) else None
                        )
                        error_details = (
                            error_data.get("errors") or
                            error_data.get("errorMessages") or
                            error_data.get("details")
                        )
                        # If we found a structured error, use it
                        if error_message:
                            error_text = str(error_message)
                            if error_details and error_details != error_message:
                                error_text += f" - {error_details}"
                        else:
                            # Fallback to string representation of the error dict
                            error_text = json.dumps(error_data)
                    else:
                        error_text = str(error_data)
                else:
                    # Not JSON, get raw text
                    error_text = response.text() if hasattr(response, 'text') else str(response)
            except Exception as e:
                # If parsing fails, fall back to text extraction
                logger.debug(f"Error parsing error response: {e}")
                error_text = response.text() if hasattr(response, 'text') else str(response)

            # Build error response
            error_response: dict[str, object] = {
                "error": error_message or f"HTTP {response.status}",
                "status_code": response.status,
                "details": error_text
            }

            if include_guidance:
                guidance = self._get_error_guidance(response.status)
                if guidance:
                    error_response["guidance"] = guidance

            logger.error(f"HTTP error {response.status}: {error_text}")
            return False, json.dumps(error_response)

    def _get_error_guidance(self, status_code: int) -> Optional[str]:
        """Provide specific guidance for common JIRA API errors.

        Args:
            status_code: HTTP status code

        Returns:
            Guidance message or None
        """
        guidance_map = {
            HttpStatusCode.GONE.value: (
                "JIRA instance is no longer available. This usually means: "
                "1) The JIRA instance has been deleted or moved, "
                "2) The cloud ID is incorrect, "
                "3) The authentication token is expired or invalid."
            ),
            HttpStatusCode.UNAUTHORIZED.value: (
                "Authentication failed. Please check: "
                "1) The authentication token is valid and not expired, "
                "2) The token has the necessary permissions for JIRA access."
            ),
            HttpStatusCode.FORBIDDEN.value: (
                "Access forbidden. Please check: "
                "1) The token has the required permissions, "
                "2) The user has access to the requested JIRA instance."
            ),
            HttpStatusCode.NOT_FOUND.value: (
                "Resource not found. Please check: "
                "1) The project key exists, "
                "2) The JIRA instance URL is correct."
            ),
            HttpStatusCode.BAD_REQUEST.value: (
                "Bad request. This usually means: "
                "1) Invalid JQL query syntax (check field names and operators), "
                "2) Invalid field values or formats, "
                "3) Invalid account IDs, incorrect field types, or missing required fields. "
                "For JQL queries, common issues: using '=' instead of 'IS EMPTY' for empty fields, "
                "invalid field names, or incorrect operator usage."
            )
        }
        return guidance_map.get(status_code)

    def _convert_text_to_adf(self, text: str) -> Optional[dict[str, object]]:
        """Convert plain text to Atlassian Document Format (ADF).

        Args:
            text: Plain text to convert

        Returns:
            ADF document structure or None if text is empty
        """
        if not text:
            return None

        return {
            "type": "doc",
            "version": 1,
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        {
                            "type": "text",
                            "text": text
                        }
                    ]
                }
            ]
        }

    def _validate_issue_fields(self, fields: dict[str, object]) -> tuple[bool, str]:
        """Validate issue fields before creating the issue.

        Args:
            fields: Issue fields dictionary

        Returns:
            Tuple of (is_valid, validation_message)
        """
        try:
            # Check required fields
            if not fields.get("project", {}).get("key"):
                return False, "Project key is required"

            if not fields.get("summary"):
                return False, "Summary is required"

            if not fields.get("issuetype", {}).get("name"):
                return False, "Issue type name is required"

            # Convert description to ADF if it's plain text
            if fields.get("description"):
                description = fields["description"]
                if isinstance(description, str):
                    fields["description"] = self._convert_text_to_adf(description)
                elif not isinstance(description, dict):
                    return False, "Description must be a string or ADF document"

            # Validate assignee format if provided
            if fields.get("assignee"):
                assignee = fields["assignee"]
                if not isinstance(assignee, dict) or not assignee.get("accountId"):
                    return False, "Assignee must be a dictionary with 'accountId' field"

            # Validate reporter format if provided
            if fields.get("reporter"):
                reporter = fields["reporter"]
                if not isinstance(reporter, dict) or not reporter.get("accountId"):
                    return False, "Reporter must be a dictionary with 'accountId' field"

            # Validate priority format if provided
            if fields.get("priority"):
                priority = fields["priority"]
                if not isinstance(priority, dict) or not priority.get("name"):
                    return False, "Priority must be a dictionary with 'name' field"

            # Validate components format if provided
            if fields.get("components"):
                components = fields["components"]
                if not isinstance(components, list):
                    return False, "Components must be a list"
                for comp in components:
                    if not isinstance(comp, dict) or not comp.get("name"):
                        return False, "Each component must be a dictionary with 'name' field"

            return True, "Fields validation passed"
        except Exception as e:
            return False, f"Validation error: {e}"

    async def _resolve_user_to_account_id(
        self,
        project_key: str,
        query: str
    ) -> Optional[str]:
        """Resolve a user query to a JIRA account ID.

        Args:
            project_key: Project key for assignable user search
            query: User query (name, email, or ID)

        Returns:
            Account ID or None if not found
        """
        try:
            # First try assignable users for the project
            response = await self.client.find_assignable_users(
                project=project_key,
                query=query,
                maxResults=1
            )

            if response.status == HttpStatusCode.SUCCESS.value:
                data = response.json()
                if data and isinstance(data, list) and len(data) > 0:
                    return data[0].get('accountId')

            # Fallback: global user search
            response = await self.client.find_users_by_query(
                query=query,
                maxResults=1
            )

            if response.status == HttpStatusCode.SUCCESS.value:
                data = response.json()
                if data and isinstance(data, list) and len(data) > 0:
                    return data[0].get('accountId')

            return None
        except Exception as e:
            logger.warning(f"Error resolving user to account ID: {e}")
            return None

    def _normalize_description(self, description: str) -> str:
        """Normalize description by removing Slack mention markup.

        Args:
            description: Original description text

        Returns:
            Normalized description
        """
        try:
            mention_pattern = re.compile(r"<@([A-Z0-9]+)>")
            return mention_pattern.sub(r"@\1", description)
        except Exception:
            return description

    async def _get_site_url(self) -> Optional[str]:
        """Get the site URL (web URL) from accessible resources.

        Returns:
            Site URL (e.g., 'https://example.atlassian.net') or None if unavailable
        """
        if self._site_url:
            return self._site_url

        try:
            client_obj = self.client._client

            # OAuth: get_base_url() is the API gateway (api.atlassian.com/ex/jira/{cloud_id}).
            # Browse URLs need the site host from accessible-resources (*.atlassian.net),
            # and we must match the cloud_id to the correct site (the token can access many).
            if hasattr(client_obj, 'get_token'):
                token = client_obj.get_token()
                if token:
                    cloud_id = None
                    if hasattr(client_obj, 'get_base_url'):
                        gateway = (client_obj.get_base_url() or "").rstrip('/')
                        match = re.search(r"/ex/jira/([^/]+)", gateway)
                        if match:
                            cloud_id = match.group(1)

                    resources = await JiraClient.get_accessible_resources(token)
                    if resources:
                        if cloud_id:
                            picked = next((r for r in resources if r.id == cloud_id), None)
                            if picked is None:
                                logger.warning(
                                    "Jira _get_site_url: cloud_id %s not found in accessible resources (%s); "
                                    "refusing to fall back to a different site.",
                                    cloud_id, [r.id for r in resources],
                                )
                                return None
                            self._site_url = picked.url.rstrip('/')
                            return self._site_url
                        # Could not extract cloud_id from the gateway URL — only safe
                        # when the token has exactly one accessible site.
                        self._site_url = resources[0].url.rstrip('/')
                        return self._site_url

            # API token / basic: configured instance base_url is the site URL
            if hasattr(client_obj, 'get_base_url'):
                base_url = client_obj.get_base_url()
                if base_url:
                    self._site_url = base_url.rstrip('/')
                    return self._site_url
        except Exception as e:
            logger.warning("Could not get site URL: %s", e)

        return None

    def _normalize_field_name(self, field_name: str) -> str:
        """Normalize field name to a semantic identifier.

        Converts field names like "Story Points" to "story_points" for deterministic access.

        Args:
            field_name: Original field name (e.g., "Story Points", "Issue Type")

        Returns:
            Normalized field name (e.g., "story_points", "issue_type")
        """
        if not field_name:
            return field_name

        # Convert to lowercase and replace spaces/special chars with underscores
        normalized = re.sub(r'[^\w\s-]', '', field_name)  # Remove special chars except word chars, spaces, hyphens
        normalized = re.sub(r'[\s-]+', '_', normalized)  # Replace spaces and hyphens with underscores

        return normalized.lower().strip('_')

    async def _fetch_and_cache_field_schema(self) -> dict[str, dict[str, str]]:
        """Fetch and cache JIRA field schema mapping.

        Returns a mapping of:
        - field_id -> field_name (e.g., "customfield_10063" -> "Story Points")
        - field_id -> normalized_name (e.g., "customfield_10063" -> "story_points")

        Returns:
            Dictionary mapping field_id to {"name": field_name, "normalized": normalized_name}
        """
        if self._field_schema_cache is not None:
            return self._field_schema_cache

        try:
            response = await self.client.get_fields()

            if response.status == HttpStatusCode.SUCCESS.value:
                fields_data = response.json()
                if not isinstance(fields_data, list):
                    logger.warning(f"Expected list of fields, got {type(fields_data)}")
                    self._field_schema_cache = {}
                    return self._field_schema_cache

                # Build mapping: field_id -> {name, normalized}
                field_map: dict[str, dict[str, str]] = {}
                for field in fields_data:
                    field_id = field.get("id")
                    field_name = field.get("name", "")
                    if field_id and field_name:
                        normalized_name = self._normalize_field_name(field_name)
                        field_map[field_id] = {
                            "name": field_name,
                            "normalized": normalized_name
                        }

                self._field_schema_cache = field_map
                logger.info(f"Cached {len(field_map)} JIRA field mappings")
                return self._field_schema_cache
            else:
                logger.warning(f"Failed to fetch field schema: HTTP {response.status}")
                self._field_schema_cache = {}
                return self._field_schema_cache

        except Exception as e:
            logger.error(f"Error fetching field schema: {e}")
            self._field_schema_cache = {}
            return self._field_schema_cache

    def _parse_allowed_values(self, raw: list[Any]) -> list[dict[str, Any]]:
        """Normalise the allowedValues list from a createmeta field response."""
        result: list[dict[str, Any]] = []
        for av in raw[:20]:
            if not isinstance(av, dict):
                continue
            entry: dict[str, Any] = {}
            if av.get("id"):
                entry["id"] = str(av["id"])
            label = av.get("name") or av.get("value")
            if label:
                entry["name"] = str(label)
            if entry:
                result.append(entry)
        return result

    async def _fetch_create_fields(
        self,
        project_key: str,
        issue_type_name: str,
    ) -> tuple[list[dict[str, Any]], Optional[str]]:
        """Fetch and cache create-field metadata for a specific project + issue type.

        Two targeted calls — no instance-wide fetches:

        1. GET /rest/api/3/issue/createmeta/{project}/issuetypes
           Resolves the issue type name to its ID and collects available names for
           error messages, all in a single request.

        2. GET /rest/api/3/issue/createmeta/{project}/issuetypes/{id}  (paginated)
           Returns all fields for this exact project+issue type combination with
           accurate required/optional flags and allowed values.  Pagination is driven
           by the 'total' field so every field is fetched regardless of page size.

        Returns:
            (fields, error_message) — error_message is None on success.
        """
        cache_key = f"{project_key.upper()}:{issue_type_name.lower()}"
        if cache_key in self._create_fields_cache:
            return self._create_fields_cache[cache_key], None

        # Step 1: resolve issue type name -> ID for this project.
        # Tries exact case-insensitive match first, then fuzzy fallback so that
        # LLM-supplied names like "story", "user-story", or "bug report" map to
        # the correct Jira issue type without requiring exact spelling.
        issue_type_id: Optional[str] = None
        available_types: list[str] = []
        issue_types_raw: list[dict[str, Any]] = []
        try:
            r = await self.client.get_create_issue_meta_issue_types(projectIdOrKey=project_key)
            if r.status == HttpStatusCode.SUCCESS.value:
                issue_types_raw = r.json().get("issueTypes", [])
                for it in issue_types_raw:
                    name = it.get("name", "")
                    if name:
                        available_types.append(name)
                    if name.lower() == issue_type_name.lower():
                        issue_type_id = it.get("id")
        except Exception as e:
            logger.warning(f"Error fetching issue types for {project_key}: {e}")

        # Fuzzy fallback: LLM may pass an approximate name (e.g. "story" for "User Story").
        # difflib.get_close_matches returns the best match above the cutoff threshold.
        if not issue_type_id and available_types:
            fuzzy = difflib.get_close_matches(
                issue_type_name.lower(),
                [n.lower() for n in available_types],
                n=1,
                cutoff=0.6,
            )
            if fuzzy:
                for it in issue_types_raw:
                    if it.get("name", "").lower() == fuzzy[0]:
                        issue_type_id = it.get("id")
                        logger.info(
                            f"Fuzzy matched issue type '{issue_type_name}' -> "
                            f"'{it.get('name')}' in project '{project_key}'"
                        )
                        break

        if not issue_type_id:
            msg = f"Issue type '{issue_type_name}' not found in project '{project_key}'."
            if available_types:
                msg += f" Available: {', '.join(available_types)}"
            return [], msg

        # Step 2: paginate through all fields for this project+issue type.
        # The 'total' field in each response gives the authoritative count so we stop
        # exactly when all fields have been fetched — no extra request needed.
        fields_by_id: dict[str, dict[str, Any]] = {}
        start_at = 0
        page_size = 50

        while True:
            try:
                response = await self.client.get_create_issue_meta_issue_type_id(
                    projectIdOrKey=project_key,
                    issueTypeId=issue_type_id,
                    startAt=start_at,
                    maxResults=page_size,
                )
            except Exception as e:
                logger.error(f"createmeta fields error for {project_key}/{issue_type_name}: {e}")
                break

            if response.status != HttpStatusCode.SUCCESS.value:
                logger.warning(
                    f"createmeta HTTP {response.status} for {project_key}/{issue_type_name}"
                )
                break

            try:
                data = response.json()
            except Exception:
                break

            page_fields = data.get("fields", [])
            if not isinstance(page_fields, list):
                break

            for f in page_fields:
                field_id = f.get("fieldId") or f.get("key") or f.get("id")
                if not field_id:
                    continue
                meta_schema = f.get("schema") or {}
                fields_by_id[field_id] = {
                    "field_id": field_id,
                    "name": f.get("name", field_id),
                    "required": bool(f.get("required", False)),
                    "schema_type": meta_schema.get("type", "string"),
                    "allowed_values": self._parse_allowed_values(f.get("allowedValues") or []),
                    "has_default_value": bool(f.get("hasDefaultValue", False)),
                    "operations": f.get("operations") or [],
                }

            fetched = start_at + len(page_fields)
            total = data.get("total", 0)
            if not page_fields or fetched >= total:
                break
            start_at = fetched

        fields = list(fields_by_id.values())
        logger.info(
            f"create fields for {project_key}/{issue_type_name}: "
            f"{sum(1 for f in fields if f['required'])} required, "
            f"{sum(1 for f in fields if not f['required'])} optional "
            f"({len(fields)} total)"
        )
        self._create_fields_cache[cache_key] = fields
        return fields, None
    def _clean_issue_fields(self, issue: dict[str, Any]) -> dict[str, Any]:
        """Clean issue fields by removing unnecessary data and simplifying nested structures.

        This aggressively removes bloat while preserving user-actionable and business-relevant fields.

        Args:
            issue: Issue dictionary to clean

        Returns:
            New issue dictionary with cleaned fields
        """
        if not isinstance(issue, dict) or "fields" not in issue:
            return issue

        fields = issue["fields"]
        if not isinstance(fields, dict):
            return issue

        # Create a copy to avoid modifying the original
        cleaned_issue = dict(issue)
        cleaned_fields = dict(fields)

        # Fields to always remove (system metadata, empty values, redundant data)
        fields_to_remove = []

        # Fields to simplify (extract only essential info from nested objects)
        fields_to_simplify = {}

        for field_key, field_value in cleaned_fields.items():
            # Remove None customfield_* fields (biggest bloat source)
            if field_key.startswith("customfield_") and field_value is None:
                fields_to_remove.append(field_key)
            # Remove empty arrays
            elif isinstance(field_value, list) and len(field_value) == 0:
                fields_to_remove.append(field_key)
            # Remove empty comment/worklog objects
            elif field_key in ["comment", "worklog"] and isinstance(field_value, dict):
                if field_key == "comment" and field_value.get("comments") == []:
                    fields_to_remove.append(field_key)
                elif field_key == "worklog" and field_value.get("worklogs") == []:
                    fields_to_remove.append(field_key)
            # Remove empty strings
            elif field_value == "":
                fields_to_remove.append(field_key)
            # Remove redundant metadata fields (only if empty/null)
            elif field_key in [
                "statuscategorychangedate", "status_category_changed",
                "aggregatetimeoriginalestimate", "aggregatetimeestimate",
                "aggregatetimespent", "timeestimate", "timeoriginalestimate",
                "timespent", "workratio", "progress", "aggregateprogress",
                "rank", "environment", "security", "lastViewed",
                "organizations", "request_participants", "responders"
            ]:
                # Only remove if empty/null
                if field_value is None or (isinstance(field_value, list) and len(field_value) == 0):
                    fields_to_remove.append(field_key)
            # Remove empty arrays for these fields
            elif field_key in ["fixVersions", "versions", "issuelinks", "subtasks"]:
                if isinstance(field_value, list) and len(field_value) == 0:
                    fields_to_remove.append(field_key)
            # Simplify nested objects to essential fields only
            elif field_key == "project" and isinstance(field_value, dict):
                fields_to_simplify[field_key] = {
                    "key": field_value.get("key"),
                    "name": field_value.get("name")
                }
            elif field_key == "status" and isinstance(field_value, dict):
                fields_to_simplify[field_key] = {
                    "name": field_value.get("name"),
                    "id": field_value.get("id")
                }
            elif field_key == "priority" and isinstance(field_value, dict):
                fields_to_simplify[field_key] = {
                    "name": field_value.get("name"),
                    "id": field_value.get("id")
                }
            elif field_key == "issuetype" and isinstance(field_value, dict):
                fields_to_simplify[field_key] = {
                    "name": field_value.get("name"),
                    "id": field_value.get("id")
                }
            elif field_key in ["assignee", "reporter", "creator"] and isinstance(field_value, dict):
                # Keep only essential user info
                simplified = {}
                if field_value.get("accountId"):
                    simplified["accountId"] = field_value.get("accountId")
                if field_value.get("displayName"):
                    simplified["displayName"] = field_value.get("displayName")
                if field_value.get("emailAddress"):
                    simplified["emailAddress"] = field_value.get("emailAddress")
                if simplified:
                    fields_to_simplify[field_key] = simplified
                else:
                    fields_to_remove.append(field_key)
            elif field_key == "parent" and isinstance(field_value, dict):
                # Simplify parent issue to just key and summary
                parent_fields = field_value.get("fields", {})
                fields_to_simplify[field_key] = {
                    "key": field_value.get("key"),
                    "id": field_value.get("id"),
                    "summary": parent_fields.get("summary") if isinstance(parent_fields, dict) else None,
                    "status": {
                        "name": parent_fields.get("status", {}).get("name") if isinstance(parent_fields.get("status"), dict) else None
                    } if parent_fields.get("status") else None
                }
            elif field_key == "attachment" and isinstance(field_value, list):
                # Simplify attachments to just essential info
                if field_value:
                    simplified_attachments = []
                    for att in field_value:
                        if isinstance(att, dict):
                            simplified_attachments.append({
                                "id": att.get("id"),
                                "filename": att.get("filename"),
                                "size": att.get("size"),
                                "mimeType": att.get("mimeType"),
                                "created": att.get("created")
                            })
                    fields_to_simplify[field_key] = simplified_attachments
                else:
                    fields_to_remove.append(field_key)
            elif field_key == "comment" and isinstance(field_value, dict) and field_value.get("comments"):
                # Simplify comments - keep only recent ones or essential info
                comments = field_value.get("comments", [])
                if comments:
                    # Keep only last 3 comments to reduce size
                    simplified_comments = []
                    for comment in comments[-3:]:
                        if isinstance(comment, dict):
                            simplified_comments.append({
                                "id": comment.get("id"),
                                "body": comment.get("body"),
                                "author": {
                                    "displayName": comment.get("author", {}).get("displayName"),
                                    "emailAddress": comment.get("author", {}).get("emailAddress")
                                } if comment.get("author") else None,
                                "created": comment.get("created")
                            })
                    fields_to_simplify[field_key] = {"comments": simplified_comments}
                else:
                    fields_to_remove.append(field_key)

        # Remove fields
        for field_key in fields_to_remove:
            cleaned_fields.pop(field_key, None)

        # Simplify fields
        for field_key, simplified_value in fields_to_simplify.items():
            cleaned_fields[field_key] = simplified_value

        cleaned_issue["fields"] = cleaned_fields
        return cleaned_issue

    async def _normalize_issues_in_response(
        self,
        response_data: dict[str, Any],
        field_schema: dict[str, dict[str, str]]
    ) -> dict[str, Any]:
        """Normalize custom fields in a response containing issues.

        Only normalizes fields that have values (not None) to avoid adding back removed fields.

        Args:
            response_data: Response data (may contain "issues" list or single issue)
            field_schema: Field schema mapping

        Returns:
            Response data with normalized fields
        """
        if not isinstance(response_data, dict):
            return response_data

        normalized = dict(response_data)

        # Handle list of issues (from search_issues, get_issues)
        if "issues" in normalized and isinstance(normalized["issues"], list):
            for issue in normalized["issues"]:
                if "fields" in issue and isinstance(issue["fields"], dict):
                    fields = issue["fields"]
                    # Only normalize customfield_* fields that exist and are not None
                    for field_id, field_info in field_schema.items():
                        if field_id in fields and fields[field_id] is not None:
                            normalized_name = field_info["normalized"]
                            fields[normalized_name] = fields[field_id]

        # Handle single issue (from get_issue)
        elif "fields" in normalized and isinstance(normalized["fields"], dict):
            fields = normalized["fields"]
            for field_id, field_info in field_schema.items():
                if field_id in fields and fields[field_id] is not None:
                    normalized_name = field_info["normalized"]
                    fields[normalized_name] = fields[field_id]

        return normalized

    def _add_urls_to_issue_references(
        self,
        issue: dict[str, Any],
        site_url: Optional[str]
    ) -> None:
        """Add URLs to issue references in custom fields (like Epic Links) and parent field.

        This makes Epic Links and other issue-referencing custom fields clickable,
        similar to how regular Jira ticket links are handled.

        Args:
            issue: Issue dictionary to process (modified in place)
            site_url: Base site URL (e.g., 'https://example.atlassian.net')
        """
        if not site_url or not isinstance(issue, dict):
            return

        fields = issue.get("fields", {})
        if not isinstance(fields, dict):
            return

        # Helper to add URL to an issue reference object
        def add_url_to_issue_ref(issue_ref: object) -> None:
            """Add URL to an issue reference if it has a key."""
            if isinstance(issue_ref, dict) and issue_ref.get("key"):
                issue_key = issue_ref["key"]
                issue_ref["url"] = f"{site_url}/browse/{issue_key}"

        # Add URL to parent field if it exists and has a key
        parent = fields.get("parent")
        if parent:
            add_url_to_issue_ref(parent)

        # Check all fields for issue references
        # Epic Links and other issue-referencing custom fields typically contain
        # an issue object with a "key" field
        # We check both original custom field IDs (customfield_*) and normalized names
        for field_key, field_value in fields.items():
            # Skip standard fields that we've already handled (parent) or that aren't issue references
            if field_key in ["parent", "key", "id", "self", "url"]:
                continue

            # Check if this field contains an issue reference
            # Issue references are dicts with a "key" field (like Epic Links)
            if field_value is not None:
                if isinstance(field_value, dict):
                    # Check if it's an issue reference (has a "key" field)
                    # This catches Epic Links and other issue-referencing custom fields
                    if field_value.get("key") and isinstance(field_value.get("key"), str):
                        add_url_to_issue_ref(field_value)
                elif isinstance(field_value, list):
                    # Some custom fields might be arrays of issue references
                    for item in field_value:
                        if isinstance(item, dict) and item.get("key") and isinstance(item.get("key"), str):
                            add_url_to_issue_ref(item)


    def _validate_and_fix_jql(self, jql: str) -> tuple[str, str | None]:
        """Validate and fix common JQL syntax errors.

        Args:
            jql: Original JQL query string

        Returns:
            Tuple of (fixed_jql, warning_message)
        """
        if not jql:
            return jql, None

        original_jql = jql
        fixed_jql = jql
        warnings = []

        # Fix common JQL syntax errors
        # 1. Fix resolution = Unresolved -> resolution IS EMPTY
        # Pattern: resolution = Unresolved (case insensitive)
        resolution_pattern = re.compile(
            r'\bresolution\s*=\s*["\']?unresolved["\']?',
            re.IGNORECASE
        )
        if resolution_pattern.search(fixed_jql):
            fixed_jql = resolution_pattern.sub('resolution IS EMPTY', fixed_jql)
            warnings.append("Fixed 'resolution = Unresolved' to 'resolution IS EMPTY'")

        # 2. Fix status = Open -> status = "Open" (add quotes if missing)
        # This is more complex, so we'll be conservative
        # Only fix if it's clearly a status field without quotes
        status_unquoted_pattern = re.compile(
            r'\bstatus\s*=\s*([a-zA-Z][a-zA-Z0-9\s]+?)(?:\s+AND|\s+OR|\s+ORDER|\s*$)',
            re.IGNORECASE
        )
        def quote_status(match: re.Match[str]) -> str:
            status_value = match.group(1).strip()
            # Don't quote if it already has quotes or is a function call
            if '"' in status_value or "'" in status_value or '(' in status_value:
                return match.group(0)
            return f'status = "{status_value}"'

        # Check if we need to fix status
        # Note: We don't auto-fix status quotes as it might be intentional
        # The API will handle validation and return appropriate errors
        if 'status' in fixed_jql and status_unquoted_pattern.search(fixed_jql):
            try:
                # Check if the status value is already quoted
                # This is a bit brittle, but we're just checking, not fixing
                parts = fixed_jql.split('status', 1)[1].split('=', 1)[1].split()
                if parts and not (parts[0].startswith('"') or parts[0].startswith("'")):
                    # It's likely an unquoted status, but we'll let the API handle it
                    # The API will return an error if the JQL is invalid
                    pass
            except (IndexError, ValueError):
                # This can happen if the JQL is malformed
                # The API call will fail and return an appropriate error
                pass

        # 3. Fix common typos: assignee = currentUser -> assignee = currentUser()
        current_user_pattern = re.compile(
            r'\bassignee\s*=\s*currentUser\b(?!\()',
            re.IGNORECASE
        )
        if current_user_pattern.search(fixed_jql):
            fixed_jql = current_user_pattern.sub('assignee = currentUser()', fixed_jql)
            warnings.append("Fixed 'currentUser' to 'currentUser()'")

        warning_msg = "; ".join(warnings) if warnings else None

        if fixed_jql != original_jql:
            logger.info(f"JQL auto-fixed: '{original_jql}' -> '{fixed_jql}'")

        return fixed_jql, warning_msg

    @tool(
        path="/tools/jira/validate_connection",
        short_description="Validate JIRA connection and provide diagnostics",
        description="Validate the JIRA connection by checking authentication status and returning diagnostics.",
        parameters=[],
        tags=[Tag(key="category", value="project_management"), Tag(key="type", value="utility")],
    )
    async def validate_connection(self) -> tuple[bool, str]:
        """Validate JIRA connection and provide diagnostics"""
        try:
            # Simply try to fetch the current user to validate the connection
            # This is more reliable than trying to access the underlying client
            response = await self.client.get_current_user()

            if response.status == HttpStatusCode.SUCCESS.value:
                user_data = response.json()
                # Clean user data
                cleaned_user = (
                    ResponseTransformer(user_data)
                    .remove("self", "*.self", "*.avatarUrls", "*.expand", "*.iconUrl",
                            "*.active", "*.timeZone", "*.locale", "*.accountType",
                            "*.properties", "*._links")
                    .clean()
                )

                return True, json.dumps({
                    "message": "JIRA connection is valid",
                    "user": {
                        "accountId": cleaned_user.get("accountId"),
                        "emailAddress": cleaned_user.get("emailAddress"),
                        "displayName": cleaned_user.get("displayName")
                    }
                })
            else:
                return self._handle_response(
                    response,
                    "Connection validated",
                    include_guidance=True
                )

        except Exception as e:
            logger.error(f"Error validating JIRA connection: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        path="/tools/jira/get_current_user",
        short_description="Get the current authenticated JIRA user's details",
        description=(
            "Get the current authenticated JIRA user's details. "
            "Returns the accountId, displayName, and emailAddress of the user making the request."
        ),
        parameters=[],
        tags=[Tag(key="category", value="project_management"), Tag(key="type", value="read")],
    )
    async def get_current_user(self) -> tuple[bool, str]:
        """Get the current authenticated JIRA user's details"""
        try:
            response = await self.client.get_current_user()

            if response.status == HttpStatusCode.SUCCESS.value:
                user_data = response.json()
                # Clean user data
                cleaned_user = (
                    ResponseTransformer(user_data)
                    .remove("self", "*.self", "*.avatarUrls", "*.expand", "*.iconUrl",
                            "*.active", "*.timeZone", "*.locale", "*.accountType",
                            "*.properties", "*._links")
                    .clean()
                )

                return True, json.dumps({
                    "message": "Current user fetched successfully",
                    "data": {
                        "accountId": cleaned_user.get("accountId"),
                        "displayName": cleaned_user.get("displayName"),
                        "emailAddress": cleaned_user.get("emailAddress")
                    }
                })
            else:
                return self._handle_response(
                    response,
                    "Current user fetched",
                    include_guidance=True
                )

        except Exception as e:
            logger.error(f"Error getting current user: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        path="/tools/jira/convert_text_to_adf",
        short_description="Convert plain text to Atlassian Document Format (ADF)",
        description="Convert plain text to Atlassian Document Format (ADF).",
        parameters=[ToolParameter(name="text", type=ParameterType.STRING, description="The text to convert to ADF", required=True)],
        tags=[Tag(key="category", value="project_management"), Tag(key="type", value="utility")],
    )
    async def convert_text_to_adf(self, text: str) -> tuple[bool, str]:
        """Convert plain text to Atlassian Document Format"""
        try:
            adf_document = self._convert_text_to_adf(text)
            return True, json.dumps({
                "message": "Text converted to ADF successfully",
                "adf_document": adf_document,
                "usage_note": "Use this ADF document in the 'description' field when creating JIRA issues"
            })
        except Exception as e:
            logger.error(f"Error converting text to ADF: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        path="/tools/jira/get_create_issue_fields",
        short_description="Get all fields required to create a Jira issue for a project and issue type",
        description=(
            "Returns all fields (required and optional) for a specific issue type in a Jira project, "
            "including field IDs, types, allowed values, and whether each field has a default value.\n"
            "\n"
            "Call this before create_issue and before update_issue when changing the issue type. "
            "Projects often have required custom fields (Story Points, Sprint, Due Date, etc.) "
            "that are not standard parameters — submitting without them causes HTTP 400 failures.\n"
            "\n"
            "For every required field where has_default_value=false: check whether the user already "
            "stated a value in the conversation. If they did not, ask them before proceeding. "
            "Never assume or invent values for required fields — guessing creates incorrect data in Jira.\n"
            "\n"
            "Standard fields (summary, description, etc) "
            "are passed as named parameters to create_issue or update_issue. All other fields — "
            "especially required custom fields — are passed via custom_fields using the field_id as key "
            "and value_format from this response as a guide."
        ),
        parameters=[
            ToolParameter(name="project_key", type=ParameterType.STRING, description="The key of the Jira project", required=True),
            ToolParameter(name="issue_type_name", type=ParameterType.STRING, description="The name of the Jira issue type", required=True),
        ],
        tags=[Tag(key="category", value="project_management"), Tag(key="type", value="write")],
    )
    async def get_create_issue_fields(
        self, project_key: str, issue_type_name: str
    ) -> tuple[bool, str]:
        """Fetch field metadata for creating an issue — mandatory pre-step before create_issue."""
        try:
            fields, error_msg = await self._fetch_create_fields(project_key, issue_type_name)

            if error_msg:
                return False, json.dumps({
                    "error": error_msg,
                    "guidance": (
                        "Verify the project key and issue type name. "
                        "Use get_projects to list available projects and get_project_metadata "
                        "to list available issue types for a project."
                    ),
                })

            # Fields always handled internally by create_issue — never surface to the LLM
            internally_managed = {"issuetype", "project"}

            # Standard fields exposed through named parameters, not custom_fields
            standard_field_ids = {
                "summary", "description", "assignee", "reporter", "priority",
                "labels", "components", "issuetype", "project", "parent",
            }

            # Fields that add noise without being actionable by the LLM
            always_skip = {
                "attachment", "worklog", "issuelinks", "subtasks", "watches", "votes",
            }

            # Required fields the LLM must fill:
            # - exclude internally managed ones (issuetype, project — always set by create_issue)
            # - exclude reporter when Jira will auto-fill it (has_default_value=True means current
            #   user is the default, which create_issue does not override anyway)
            required_fields = [
                f for f in fields
                if f["required"]
                and f["field_id"] not in internally_managed
                and f["field_id"] not in always_skip
                and not (f["field_id"] == "reporter" and f.get("has_default_value"))
            ]
            optional_fields = [
                f for f in fields
                if not f["required"]
                and f["field_id"] not in internally_managed
                and f["field_id"] not in always_skip
            ]

            def format_hint(f: dict[str, Any]) -> str:
                t = f["schema_type"]
                avs = f.get("allowed_values") or []
                if avs:
                    first_id = avs[0].get("id", "") if avs else ""
                    return (
                        f"object with 'id' from the allowed_values list, "
                        f"e.g. {{'id': '{first_id}'}}"
                    )
                if t == "number":
                    return "numeric value, e.g. 5"
                if t == "date":
                    return "ISO date string, e.g. '2026-06-30'"
                if t == "datetime":
                    return "ISO datetime string, e.g. '2026-06-30T10:00:00.000+0000'"
                if t == "array":
                    return (
                        "list — e.g. [{'id': '...'}] for option arrays "
                        "or ['tag1', 'tag2'] for string arrays"
                    )
                if t == "user":
                    return "use assignee_account_id named parameter; or {'accountId': '<id>'}"
                return "string value"

            def clean_field(f: dict[str, Any]) -> dict[str, Any]:
                out: dict[str, Any] = {
                    "field_id": f["field_id"],
                    "name": f["name"],
                    "schema_type": f["schema_type"],
                    "value_format": format_hint(f),
                    "pass_via": (
                        "named_parameter"
                        if f["field_id"] in standard_field_ids
                        else "custom_fields"
                    ),
                }
                avs = f.get("allowed_values") or []
                if avs:
                    out["allowed_values"] = avs
                if f.get("has_default_value"):
                    out["has_default_value"] = True
                    out["note"] = "Jira will auto-fill this field if not provided"
                return out

            optional_out = [clean_field(f) for f in optional_fields]

            return True, json.dumps({
                "message": "Field metadata fetched successfully",
                "project_key": project_key,
                "issue_type_name": issue_type_name,
                "instructions": (
                    "Pass standard fields (summary, description, assignee_account_id, priority_name, "
                    "labels, components, parent_key) as named parameters to create_issue. "
                    "Pass ALL other fields — especially required ones — via the custom_fields dict "
                    "using the field_id as the key and value_format as a guide for the value."
                ),
                "required_fields": [clean_field(f) for f in required_fields],
                "optional_fields": optional_out,
            })

        except Exception as e:
            logger.error(f"Error getting create issue fields: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        path="/tools/jira/create_issue",
        short_description="Create a new issue in JIRA.",
        description=(
            "Create a new JIRA issue. Call get_create_issue_fields first to discover "
            "required fields for the target project and issue type — projects often have mandatory "
            "custom fields (Story Points, Sprint, Due Date, etc.) that are not standard parameters, "
            "and omitting them causes HTTP 400 failures.\n"
            "\n"
            "For every required field where has_default_value=false: check whether the user stated "
            "a value in the conversation. If not, ask them before calling this tool. "
            "Never assume or guess required field values.\n"
            "\n"
            "Pass standard fields (summary, description, assignee, priority, labels, components, "
            "parent_key) as named parameters. Pass all other fields via custom_fields using field IDs "
            "and value formats from get_create_issue_fields.\n"
            "\n"
            "Do not use this tool when the user wants to modify an existing issue — use update_issue instead."
        ),
        parameters=[
            ToolParameter(name="project_key", type=ParameterType.STRING, description="The key of the Jira project", required=True),
            ToolParameter(name="summary", type=ParameterType.STRING, description="The summary of the Jira issue", required=True),
            ToolParameter(name="issue_type_name", type=ParameterType.STRING, description="The name of the Jira issue type", required=True),
            ToolParameter(name="description", type=ParameterType.STRING, description="The description of the Jira issue", required=False),
            ToolParameter(name="assignee_account_id", type=ParameterType.STRING, description="The account ID of the Jira assignee", required=False),
            ToolParameter(name="assignee_query", type=ParameterType.STRING, description="The query to resolve the Jira assignee", required=False),
            ToolParameter(name="priority_name", type=ParameterType.STRING, description="The name of the Jira priority", required=False),
            ToolParameter(name="labels", type=ParameterType.LIST, description="The labels of the Jira issue", required=False),
            ToolParameter(name="components", type=ParameterType.LIST, description="The components of the Jira issue", required=False),
            ToolParameter(name="parent_key", type=ParameterType.STRING, description="The key of the Jira parent issue", required=False),
            ToolParameter(name="custom_fields", type=ParameterType.DICT, description="The custom fields of the Jira issue", required=False),
        ],
        tags=[Tag(key="category", value="project_management"), Tag(key="type", value="destructive")],
        args_summary=args_template('Creating Jira issue in {project_key}: "{summary}"', "project_key", "summary"),
        result_summary=entity_summary(lambda e: f"Created {e.get('key', '?')}"),
    )
    async def create_issue(
        self,
        project_key: str,
        summary: str,
        issue_type_name: str,
        description: Optional[str] = None,
        assignee_account_id: Optional[str] = None,
        assignee_query: Optional[str] = None,
        priority_name: str | None = None,
        labels: list[str] | None = None,
        components: list[str] | None = None,
        parent_key: str | None = None,
        custom_fields: dict[str, Any] | None = None,
    ) -> tuple[bool, str]:
        """Create a new JIRA issue"""
        try:
            # Build issue fields
            fields: dict[str, object] = {
                "project": {"key": project_key},
                "summary": summary,
                "issuetype": {"name": issue_type_name},
            }

            # Resolve assignee
            if assignee_query and not assignee_account_id:
                assignee_account_id = await self._resolve_user_to_account_id(
                    project_key,
                    assignee_query
                )

            if description:
                fields["description"] = self._normalize_description(description)

            if assignee_account_id:
                fields["assignee"] = {"accountId": assignee_account_id}

            if priority_name:
                fields["priority"] = {"name": priority_name}

            if labels:
                fields["labels"] = labels

            if components:
                fields["components"] = [{"name": comp} for comp in components]

            if parent_key:
                fields["parent"] = {"key": parent_key}

            # Merge project-specific required/optional custom fields
            if custom_fields:
                for field_id, field_value in custom_fields.items():
                    if field_id not in fields:
                        fields[field_id] = field_value
                    else:
                        logger.debug(f"custom_fields skipped override of standard field: {field_id}")

            # Validate fields
            is_valid, validation_msg = self._validate_issue_fields(fields)
            if not is_valid:
                return False, json.dumps({
                    "error": "Field validation failed",
                    "validation_error": validation_msg,
                    "fields": fields
                })

            # Create issue
            response = await self.client.create_issue(fields=fields)

            # Handle reporter field errors by retrying without it
            if response.status == HttpStatusCode.BAD_REQUEST.value:
                try:
                    error_body = response.json()
                    errors = error_body.get('errors', {})

                    if 'reporter' in errors and 'reporter' in fields:
                        logger.info("Retrying without reporter field")
                        del fields['reporter']
                        response = await self.client.create_issue(fields=fields)

                    elif 'assignee' in errors and 'assignee' in fields:
                        logger.info("Retrying without assignee field")
                        del fields['assignee']
                        response = await self.client.create_issue(fields=fields)
                except Exception:
                    pass

            if response.status == HttpStatusCode.SUCCESS.value or response.status == HttpStatusCode.CREATED.value:
                data = response.json()
                # Clean response: remove redundant fields
                cleaned_data = (
                    ResponseTransformer(data)
                    .remove("self", "*.self", "*.avatarUrls", "*.expand", "*.iconUrl",
                            "*.description", "*.subtask", "*.avatarId", "*.hierarchyLevel",
                            "*.statusCategory", "*.active", "*.timeZone", "*.locale", "*.accountType",
                            "*.properties", "*._links")

                    .clean()
                )

                # Add web URL if available
                issue_key = cleaned_data.get("key")
                site_url = await self._get_site_url()
                if issue_key and site_url:
                    cleaned_data["url"] = f"{site_url}/browse/{issue_key}"
                # Add URLs to Epic Links and other issue references in custom fields
                if site_url:
                    self._add_urls_to_issue_references(cleaned_data, site_url)

                return True, json.dumps({
                    "message": "Issue created successfully",
                    "data": cleaned_data
                })
            elif response.status == HttpStatusCode.BAD_REQUEST.value:
                # Translate field IDs to human-readable names and guide LLM to retry correctly
                try:
                    error_body = response.json()
                    field_errors = error_body.get("errors", {})
                    if field_errors:
                        field_schema = await self._fetch_and_cache_field_schema()
                        readable_errors: dict[str, str] = {}
                        for fid, emsg in field_errors.items():
                            fname = field_schema.get(fid, {}).get("name", fid)
                            readable_errors[f"{fname} ({fid})"] = emsg
                        return False, json.dumps({
                            "error": "Issue creation failed — required fields are missing or have invalid values",
                            "field_errors": readable_errors,
                            "guidance": (
                                f"Call get_create_issue_fields(project_key='{project_key}', "
                                f"issue_type_name='{issue_type_name}') to see all required fields "
                                "and their expected value formats, then retry create_issue with the "
                                "missing fields supplied via the custom_fields parameter."
                            ),
                        })
                except Exception:
                    pass
                return self._handle_response(
                    response,
                    "Issue created successfully",
                    include_guidance=True
                )
            else:
                return self._handle_response(
                    response,
                    "Issue created successfully",
                    include_guidance=True
                )

        except Exception as e:
            logger.error(f"Error creating issue: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        path="/tools/jira/update_issue",
        short_description="Update an existing issue in JIRA.",
        description=(
            "Modify one or more fields on an existing Jira issue. Supports any combination of: "
            "summary, description, issue type, assignee, reporter, priority, labels, components, "
            "status, and project-specific custom fields.\n"
            "\n"
            "issue_type_name changes the type of the issue (e.g. Bug → Story). "
            "The target type may have required custom fields — use get_create_issue_fields to discover "
            "them, collect any missing values from the user, then pass them via custom_fields. "
            "Do not assume values for required fields.\n"
            "\n"
            "custom_fields accepts any field the user explicitly provides that is not covered by "
            "the named parameters. Use get_create_issue_fields for the correct field IDs and value formats.\n"
            "\n"
            "Status changes are resolved through Jira workflow transitions automatically.\n"
            "\n"
            "Always include at least one field to change alongside issue_key."
        ),
        parameters=[
            ToolParameter(name="issue_key", type=ParameterType.STRING, description="The key of the Jira issue", required=True),
            ToolParameter(name="summary", type=ParameterType.STRING, description="The summary of the Jira issue", required=False),
            ToolParameter(name="description", type=ParameterType.STRING, description="The description of the Jira issue", required=False),
            ToolParameter(name="issue_type_name", type=ParameterType.STRING, description="The name of the Jira issue type", required=False),
            ToolParameter(name="assignee_account_id", type=ParameterType.STRING, description="The account ID of the Jira assignee", required=False),
            ToolParameter(name="assignee_query", type=ParameterType.STRING, description="The query to resolve the Jira assignee", required=False),
            ToolParameter(name="reporter_account_id", type=ParameterType.STRING, description="The account ID of the Jira reporter", required=False),
            ToolParameter(name="reporter_query", type=ParameterType.STRING, description="The query to resolve the Jira reporter", required=False),
            ToolParameter(name="priority_name", type=ParameterType.STRING, description="The name of the Jira priority", required=False),
            ToolParameter(name="labels", type=ParameterType.LIST, description="The labels of the Jira issue", required=False),
            ToolParameter(name="components", type=ParameterType.LIST, description="The components of the Jira issue", required=False),
            ToolParameter(name="status", type=ParameterType.STRING, description="The status of the Jira issue", required=False),
            ToolParameter(name="custom_fields", type=ParameterType.DICT, description="The custom fields of the Jira issue", required=False),
        ],
        tags=[Tag(key="category", value="project_management"), Tag(key="type", value="destructive")],
        args_summary=args_template("Updating Jira issue {issue_key}", "issue_key"),
        result_summary=entity_summary(lambda e: f"Updated {e.get('key', '?')}"),
    )
    async def update_issue(
        self,
        issue_key: str,
        summary: Optional[str] = None,
        description: Optional[str] = None,
        issue_type_name: Optional[str] = None,
        assignee_account_id: Optional[str] = None,
        assignee_query: Optional[str] = None,
        reporter_account_id: Optional[str] = None,
        reporter_query: Optional[str] = None,
        priority_name: str | None = None,
        labels: list[str] | None = None,
        components: list[str] | None = None,
        status: str | None = None,
        custom_fields: dict[str, Any] | None = None,
    ) -> tuple[bool, str]:
        """Update an existing JIRA issue"""
        try:
            # Fetch the issue first to get the authoritative project key from the data
            project_key = ""
            try:
                existing = await self.client.get_issue(issueIdOrKey=issue_key)
                if existing.status == HttpStatusCode.SUCCESS.value:
                    project_key = (
                        existing.json().get("fields", {}).get("project", {}).get("key", "")
                    )
            except Exception as e:
                logger.warning(f"Could not fetch issue {issue_key} for project key: {e}")

            fields: dict[str, object] = {}

            if summary:
                fields["summary"] = summary

            if description:
                if isinstance(description, str):
                    fields["description"] = self._convert_text_to_adf(description)
                elif isinstance(description, dict):
                    fields["description"] = description
                else:
                    return False, json.dumps({
                        "error": "Description must be a string or ADF document",
                        "guidance": "Provide description as plain text (string) or ADF format (dict)"
                    })

            if issue_type_name:
                resolved_type = issue_type_name
                if project_key:
                    try:
                        r = await self.client.get_create_issue_meta_issue_types(
                            projectIdOrKey=project_key
                        )
                        if r.status == HttpStatusCode.SUCCESS.value:
                            available_names = [
                                it.get("name", "")
                                for it in r.json().get("issueTypes", [])
                                if it.get("name")
                            ]
                            exact = next(
                                (n for n in available_names if n.lower() == issue_type_name.lower()),
                                None,
                            )
                            if exact:
                                resolved_type = exact
                            else:
                                fuzzy = difflib.get_close_matches(
                                    issue_type_name.lower(),
                                    [n.lower() for n in available_names],
                                    n=1,
                                    cutoff=0.6,
                                )
                                if fuzzy:
                                    for n in available_names:
                                        if n.lower() == fuzzy[0]:
                                            resolved_type = n
                                            logger.info(
                                                f"Fuzzy matched issue type '{issue_type_name}' -> "
                                                f"'{resolved_type}' for update on {issue_key}"
                                            )
                                            break
                    except Exception as e:
                        logger.warning(
                            f"Could not resolve issue type for update on {issue_key}: {e}"
                        )
                fields["issuetype"] = {"name": resolved_type}

            if assignee_query and not assignee_account_id:
                if project_key:
                    assignee_account_id = await self._resolve_user_to_account_id(
                        project_key, assignee_query
                    )

            if assignee_account_id:
                fields["assignee"] = {"accountId": assignee_account_id}

            if reporter_query and not reporter_account_id:
                if project_key:
                    reporter_account_id = await self._resolve_user_to_account_id(
                        project_key, reporter_query
                    )

            if reporter_account_id:
                fields["reporter"] = {"accountId": reporter_account_id}

            if priority_name:
                fields["priority"] = {"name": priority_name}

            if labels:
                fields["labels"] = labels

            if components:
                fields["components"] = [{"name": comp} for comp in components]

            if custom_fields:
                for field_id, field_value in custom_fields.items():
                    if field_id not in fields:
                        fields[field_id] = field_value

            transition = None
            if status:
                try:
                    transitions_response = await self.client.get_transitions(issueIdOrKey=issue_key)
                    if transitions_response.status == HttpStatusCode.SUCCESS.value:
                        transitions = transitions_response.json().get("transitions", [])
                        for trans in transitions:
                            if trans.get("to", {}).get("name", "").lower() == status.lower():
                                transition = {"id": trans.get("id")}
                                break
                        if not transition:
                            logger.warning(
                                f"Status transition '{status}' not found for {issue_key}. "
                                f"Available: {[t.get('to', {}).get('name') for t in transitions]}"
                            )
                except Exception as e:
                    logger.warning(f"Could not get transitions for {issue_key}: {e}")

            if not fields and not transition:
                return False, json.dumps({
                    "error": "No updates provided",
                    "guidance": (
                        "Provide at least one field to update (summary, description, issue_type_name, "
                        "assignee, reporter, priority, labels, components, custom_fields) "
                        "or a status to transition to"
                    ),
                })

            if fields:
                # Jira validates custom fields against the current issue type at request time.
                # When changing type + setting custom fields in one request, the custom fields
                # are checked against the original type and silently dropped or rejected.
                # Fix: send the issuetype change first, then the remaining fields separately.
                if "issuetype" in fields and len(fields) > 1:
                    type_resp = await self.client.edit_issue(
                        issueIdOrKey=issue_key,
                        fields={"issuetype": fields.pop("issuetype")},
                        transition=None,
                    )
                    if type_resp.status not in [
                        HttpStatusCode.SUCCESS.value, HttpStatusCode.NO_CONTENT.value
                    ]:
                        return self._handle_response(
                            type_resp, "Issue updated successfully", include_guidance=True
                        )

                if fields:
                    response = await self.client.edit_issue(
                        issueIdOrKey=issue_key,
                        fields=fields,
                        transition=None,
                    )

                    if response.status == HttpStatusCode.BAD_REQUEST.value and "reporter" in fields:
                        try:
                            errors = response.json().get("errors", {})
                            if "reporter" in errors:
                                logger.info(
                                    "Retrying update without reporter field "
                                    "(not permitted by this Jira instance)"
                                )
                                fields_no_reporter = {k: v for k, v in fields.items() if k != "reporter"}
                                if fields_no_reporter:
                                    response = await self.client.edit_issue(
                                        issueIdOrKey=issue_key,
                                        fields=fields_no_reporter,
                                        transition=None,
                                    )
                        except Exception:
                            pass

                    if response.status == HttpStatusCode.BAD_REQUEST.value:
                        try:
                            field_errors = response.json().get("errors", {})
                            if field_errors:
                                field_schema = await self._fetch_and_cache_field_schema()
                                readable: dict[str, str] = {
                                    f"{field_schema.get(fid, {}).get('name', fid)} ({fid})": emsg
                                    for fid, emsg in field_errors.items()
                                }
                                if issue_type_name and project_key:
                                    guidance = (
                                        f"You are changing the issue type to '{issue_type_name}'. "
                                        f"Call get_create_issue_fields(project_key='{project_key}', "
                                        f"issue_type_name='{issue_type_name}') to see all required fields "
                                        "for the target type, then retry update_issue with the missing "
                                        "fields supplied via the custom_fields parameter."
                                    )
                                else:
                                    guidance = (
                                        "One or more fields have invalid or missing values. "
                                        "Check the field_errors for details and correct the values."
                                    )
                                return False, json.dumps({
                                    "error": "Issue update failed — required fields are missing or invalid",
                                    "field_errors": readable,
                                    "guidance": guidance,
                                })
                        except Exception:
                            pass

                    if response.status not in [HttpStatusCode.SUCCESS.value, HttpStatusCode.NO_CONTENT.value]:
                        return self._handle_response(response, "Issue updated successfully", include_guidance=True)

            transition_success = True
            transition_error = None
            if transition:
                try:
                    tr = await self.client.do_transition(issueIdOrKey=issue_key, transition=transition)
                    if tr.status not in [HttpStatusCode.SUCCESS.value, HttpStatusCode.NO_CONTENT.value]:
                        transition_success = False
                        transition_error = f"HTTP {tr.status}"
                        try:
                            err_data = tr.json()
                            if isinstance(err_data, dict) and "errorMessages" in err_data:
                                transition_error = "; ".join(err_data.get("errorMessages", []))
                        except Exception:
                            pass
                        logger.warning(f"Transition to '{status}' failed for {issue_key}: {transition_error}")
                except Exception as e:
                    transition_success = False
                    transition_error = str(e)
                    logger.warning(f"Exception during transition to '{status}' for {issue_key}: {e}")

            issue_response = await self.client.get_issue(issueIdOrKey=issue_key)
            if issue_response.status != HttpStatusCode.SUCCESS.value:
                site_url = await self._get_site_url()
                url = f"{site_url}/browse/{issue_key}" if site_url else None
                message = "Issue updated successfully"
                if transition and not transition_success:
                    message += f" (but status transition failed: {transition_error})"
                return True, json.dumps({
                    "message": message,
                    "data": {"key": issue_key, "url": url} if url else {"key": issue_key},
                })

            cleaned_data = (
                ResponseTransformer(issue_response.json())
                .remove("self", "*.self", "*.avatarUrls", "*.expand", "*.iconUrl",
                        "*.description", "*.subtask", "*.avatarId", "*.hierarchyLevel",
                        "*.statusCategory", "*.active", "*.timeZone", "*.locale", "*.accountType",
                        "*.properties", "*._links")
                .clean()
            )

            issue_key_out = cleaned_data.get("key") or issue_key
            site_url = await self._get_site_url()
            if issue_key_out and site_url:
                cleaned_data["url"] = f"{site_url}/browse/{issue_key_out}"
            if site_url:
                self._add_urls_to_issue_references(cleaned_data, site_url)

            message = "Issue updated successfully"
            if transition and not transition_success:
                message += f" (but status transition failed: {transition_error})"

            return True, json.dumps({"message": message, "data": cleaned_data})

        except Exception as e:
            logger.error(f"Error updating issue: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        path="/tools/jira/get_projects",
        short_description="Get all JIRA projects",
        description="Get all JIRA projects accessible by the current user.",
        parameters=[],
        tags=[Tag(key="category", value="project_management"), Tag(key="type", value="read")],
        args_summary=lambda args: "Fetching JIRA projects",
        result_summary=list_summary(("data",), _jira_project_label, "project"),
    )
    async def get_projects(self) -> tuple[bool, str]:
        """Get all JIRA projects"""
        try:
            response = await self.client.get_all_projects()

            if response.status == HttpStatusCode.SUCCESS.value:
                data = response.json()
                # Clean response: remove redundant fields
                cleaned_data = (
                    ResponseTransformer(data)
                    .remove("self", "*.self", "*.avatarUrls", "*.expand", "*.iconUrl",
                            "*.active", "*.timeZone", "*.locale", "*.accountType",
                            "*.properties", "*._links")
                    .clean()
                )

                # Add web URLs to projects if available
                site_url = await self._get_site_url()
                if site_url:
                    if isinstance(cleaned_data, list):
                        for project in cleaned_data:
                            project_key = project.get("key")
                            if project_key:
                                project["url"] = f"{site_url}/projects/{project_key}"
                    elif isinstance(cleaned_data, dict) and "key" in cleaned_data:
                        project_key = cleaned_data.get("key")
                        if project_key:
                            cleaned_data["url"] = f"{site_url}/projects/{project_key}"

                return True, json.dumps({
                    "message": "Projects fetched successfully",
                    "data": cleaned_data
                })
            else:
                return self._handle_response(
                    response,
                    "Projects fetched successfully",
                    include_guidance=True
                )
        except Exception as e:
            logger.error(f"Error getting projects: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        path="/tools/jira/get_project",
        short_description="Get a specific JIRA project",
        description="Get details for a specific JIRA project by its project key.",
        parameters=[
            ToolParameter(name="project_key", type=ParameterType.STRING, description="Project key (e.g., 'PA')", required=True),
        ],
        tags=[Tag(key="category", value="project_management"), Tag(key="type", value="read")],
        args_summary=args_template("Fetching Jira project {project_key}", "project_key"),
        result_summary=entity_summary(_jira_project_label),
    )
    async def get_project(self, project_key: str) -> tuple[bool, str]:
        """Get a specific JIRA project"""
        try:
            response = await self.client.get_project(projectIdOrKey=project_key)

            if response.status == HttpStatusCode.SUCCESS.value:
                data = response.json()
                # Clean response: remove redundant fields
                cleaned_data = (
                    ResponseTransformer(data)
                    .remove("self", "*.self", "*.avatarUrls", "*.expand", "*.iconUrl",
                            "*.active", "*.timeZone", "*.locale", "*.accountType",
                            "*.properties", "*._links")
                    .clean()
                )

                # Add web URL if available
                project_key = cleaned_data.get("key")
                if project_key:
                    site_url = await self._get_site_url()
                    if site_url:
                        cleaned_data["url"] = f"{site_url}/projects/{project_key}"

                return True, json.dumps({
                    "message": "Project fetched successfully",
                    "data": cleaned_data
                })
            else:
                return self._handle_response(
                    response,
                    "Project fetched successfully"
                )
        except Exception as e:
            logger.error(f"Error getting project: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        path="/tools/jira/get_issues",
        short_description="Get issues from a JIRA project",
        description=(
            "Get issues from a JIRA project. For more specific queries, use search_issues with custom JQL."
        ),
        parameters=[
            ToolParameter(name="project_key", type=ParameterType.STRING, description="Project key (e.g., 'PA')", required=True),
            ToolParameter(name="days", type=ParameterType.INTEGER, description="Days to look back (default 30)", required=False),
            ToolParameter(name="max_results", type=ParameterType.INTEGER, description="Max results to return (default 50)", required=False),
        ],
        tags=[Tag(key="category", value="project_management"), Tag(key="type", value="read")],
        args_summary=args_template("Fetching issues from {project_key}", "project_key"),
        result_summary=list_summary("issues", _jira_issue_label, "issue"),
    )
    async def get_issues(
        self,
        project_key: str,
        days: Optional[int] = None,
        max_results: Optional[int] = None
    ) -> tuple[bool, str]:
        """Get issues from a project with configurable time range"""
        try:
            # Escape project key and add time filter to avoid unbounded query errors
            escaped_project_key = project_key.replace('"', '\\"')
            time_filter = days or 30  # Default to 30 days if not specified
            jql = f'project = "{escaped_project_key}" AND updated >= -{time_filter}d ORDER BY updated DESC'

            # Use enhanced search endpoint (standard search has been removed - 410 Gone)
            response = await self.client.search_and_reconsile_issues_using_jql_post(
                jql=jql,
                maxResults=max_results or 50,
                fields=["*all"],  # "*all" requests all fields from the API
            )

            if response.status == HttpStatusCode.SUCCESS.value:
                data = response.json()
                # Clean response: remove redundant fields
                cleaned_data = (
                    ResponseTransformer(data)
                    .remove("expand", "self", "*.self", "*.avatarUrls", "*.expand", "*.iconUrl",
                            "*.subtask", "*.avatarId", "*.hierarchyLevel",
                            "*.statusCategory", "*.active", "*.timeZone", "*.locale", "*.accountType",
                            "*.properties", "*._links", "*.watches", "*.votes", "*.worklog",
                            "*.progress", "*.aggregateprogress", "*.aggregatetimeestimate",
                            "*.aggregatetimespent", "*.workratio", "*.lastViewed", "*.security",
                            "*.watchCount", "*.isWatching", "*.hasVoted", "*.startAt", "*.maxResults", "*.total",
                            "*.statuscategorychangedate", "*.status_category_changed",
                            "*.aggregatetimeoriginalestimate", "*.timeestimate", "*.timeoriginalestimate",
                            "*.timespent", "*.rank", "*.environment", "*.fixVersions", "*.versions",
                            "*.issuelinks", "*.subtasks", "*.organizations", "*.request_participants",
                            "*.responders", "*.projectTypeKey", "*.simplified", "*.description",
                            "*.id")  # Remove nested IDs (keep only top-level issue id/key)
                    .clean()
                )

                # Aggressive post-processing: Remove None customfield_* and simplify nested structures
                if isinstance(cleaned_data, dict) and "issues" in cleaned_data:
                    cleaned_data["issues"] = [
                        self._clean_issue_fields(issue) for issue in cleaned_data["issues"]
                    ]

                # Normalize custom fields using field schema (only for fields with values)
                field_schema = await self._fetch_and_cache_field_schema()
                cleaned_data = await self._normalize_issues_in_response(cleaned_data, field_schema)

                # Remove pagination fields - never send to frontend/LLM
                if isinstance(cleaned_data, dict):
                    for field in ["nextPageToken", "next_cursor", "isLast", "total", "startAt", "maxResults"]:
                        cleaned_data.pop(field, None)

                # Add web URLs to issues if available
                site_url = await self._get_site_url()
                if site_url and "issues" in cleaned_data:
                    for issue in cleaned_data["issues"]:
                        issue_key = issue.get("key")
                        if issue_key:
                            issue["url"] = f"{site_url}/browse/{issue_key}"
                        # Add URLs to Epic Links and other issue references in custom fields
                        self._add_urls_to_issue_references(issue, site_url)

                return True, json.dumps({
                    "message": "Issues fetched successfully",
                    "data": cleaned_data
                })
            else:
                return self._handle_response(
                    response,
                    "Issues fetched successfully",
                    include_guidance=True
                )
        except Exception as e:
            logger.error(f"Error getting issues: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        path="/tools/jira/get_issue",
        short_description="Get a specific JIRA issue",
        description="Get details for a specific JIRA issue by its issue key.",
        parameters=[
            ToolParameter(name="issue_key", type=ParameterType.STRING, description="Issue key (e.g., 'PA-123')", required=True),
        ],
        tags=[Tag(key="category", value="project_management"), Tag(key="type", value="read")],
        args_summary=args_template("Fetching Jira issue {issue_key}", "issue_key"),
        result_summary=entity_summary(lambda e: f"Fetched {_jira_issue_label(e)}"),
    )
    async def get_issue(self, issue_key: str) -> tuple[bool, str]:
        """Get a specific JIRA issue"""
        try:
            response = await self.client.get_issue(issueIdOrKey=issue_key)

            if response.status == HttpStatusCode.SUCCESS.value:
                data = response.json()
                # Clean response: remove redundant fields
                cleaned_data = (
                    ResponseTransformer(data)
                    .remove("expand", "self", "*.self", "*.avatarUrls", "*.expand", "*.iconUrl",
                            "*.subtask", "*.avatarId", "*.hierarchyLevel",
                            "*.statusCategory", "*.active", "*.timeZone", "*.locale", "*.accountType",
                            "*.properties", "*._links", "*.watches", "*.votes", "*.worklog",
                            "*.progress", "*.aggregateprogress", "*.aggregatetimeestimate",
                            "*.aggregatetimespent", "*.workratio", "*.lastViewed", "*.security",
                            "*.watchCount", "*.isWatching", "*.hasVoted", "*.startAt", "*.maxResults", "*.total",
                            "*.statuscategorychangedate", "*.status_category_changed",
                            "*.aggregatetimeoriginalestimate", "*.timeestimate", "*.timeoriginalestimate",
                            "*.timespent", "*.rank", "*.environment", "*.fixVersions", "*.versions",
                            "*.issuelinks", "*.subtasks", "*.organizations", "*.request_participants",
                            "*.responders", "*.projectTypeKey", "*.simplified", "*.description",
                            "*.id")  # Remove nested IDs (keep only top-level issue id/key)
                    .clean()
                )

                # Aggressive post-processing: Remove None customfield_* and simplify nested structures
                cleaned_data = self._clean_issue_fields(cleaned_data)

                # Normalize custom fields using field schema (only for fields with values)
                field_schema = await self._fetch_and_cache_field_schema()
                cleaned_data = await self._normalize_issues_in_response(cleaned_data, field_schema)

                # Add web URL if available
                issue_key = cleaned_data.get("key")
                site_url = await self._get_site_url()
                if issue_key and site_url:
                    cleaned_data["url"] = f"{site_url}/browse/{issue_key}"
                # Add URLs to Epic Links and other issue references in custom fields
                if site_url:
                    self._add_urls_to_issue_references(cleaned_data, site_url)

                return True, json.dumps({
                    "message": "Issue fetched successfully",
                    "data": cleaned_data
                })
            else:
                return self._handle_response(
                    response,
                    "Issue fetched successfully"
                )
        except Exception as e:
            logger.error(f"Error getting issue: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        path="/tools/jira/search_issues",
        short_description="Search for JIRA issues using JQL",
        description=(
            "Search for JIRA issues using JQL (JIRA Query Language).\n"
            "\n"
            "CURRENT USER QUERIES:\n"
            "- Use `assignee = currentUser()` for 'my tickets' or 'assigned to me'\n"
            "- Do NOT call search_users first - currentUser() auto-resolves\n"
            "\n"
            "REQUIRED TIME FILTER (prevents unbounded query errors):\n"
            "- Always include: `AND updated >= -30d` or `AND created >= -7d`\n"
            "\n"
            "JQL SYNTAX RULES:\n"
            "- Unresolved issues: `resolution IS EMPTY` (not `resolution = Unresolved`)\n"
            "- Current user: `currentUser()` with parentheses\n"
            "- Status values: `status = \"Open\"` with quotes\n"
            "\n"
            "EXAMPLES:\n"
            "- `project = \"PA\" AND assignee = currentUser() AND resolution IS EMPTY AND updated >= -30d`\n"
            "- `project = \"PA\" AND status = \"In Progress\" AND updated >= -7d`\n"
            "- `reporter = currentUser() AND created >= -30d ORDER BY created DESC`"
        ),
        parameters=[
            ToolParameter(name="jql", type=ParameterType.STRING, description="JQL query string with time filter", required=True),
            ToolParameter(name="maxResults", type=ParameterType.INTEGER, description="Max results to return (default 50)", required=False),
        ],
        tags=[Tag(key="category", value="project_management"), Tag(key="type", value="read")],
        args_summary=args_template('Searching Jira issues: "{jql}"', "jql"),
        result_summary=list_summary("issues", _jira_issue_label, "issue"),
    )
    async def search_issues(self, jql: str, maxResults: Optional[int] = None) -> tuple[bool, str]:
        """Search for JIRA issues using the enhanced search endpoint"""
        try:
            # Validate and fix JQL query
            fixed_jql, jql_warning = self._validate_and_fix_jql(jql)

            if fixed_jql != jql:
                logger.info(f"JQL query auto-corrected: '{jql}' -> '{fixed_jql}'")

            # Note: currentUser() is a native JQL function that Jira handles correctly.
            # We do NOT replace it with accountId as that can cause JQL syntax errors.
            # The enhanced search API properly recognizes currentUser() as a restriction.

            # Use the enhanced search endpoint (POST /rest/api/3/search/jql)
            # The standard search endpoint (/rest/api/3/search) has been removed (410 Gone)
            # Pass ["*all"] to get all fields; passing [] returns only IDs
            logger.info(f"Calling Jira search API with JQL: {fixed_jql}")
            response = await self.client.search_and_reconsile_issues_using_jql_post(
                jql=fixed_jql,
                maxResults=maxResults or 50,
                fields=["*all"],  # "*all" requests all fields from the API
            )

            logger.info(f"Jira search API response status: {response.status}")
            if response.status == HttpStatusCode.SUCCESS.value:
                try:
                    data = response.json()
                    logger.info(f"Jira search API response data keys: {list(data.keys()) if isinstance(data, dict) else 'Not a dict'}")
                    logger.info(f"Jira search API response - total issues: {data.get('total', 'N/A') if isinstance(data, dict) else 'N/A'}")
                    logger.debug(f"Jira search API full response: {json.dumps(data, indent=2)[:2000]}")  # First 2000 chars
                except Exception as e:
                    logger.error(
                        f"Failed to parse successful response as JSON - Status: {response.status}, "
                        f"Error: {e}, Response text: {response.text()[:500]}"
                    )
                    return False, json.dumps({
                        "error": f"Failed to parse response: {str(e)}",
                        "jql_query": fixed_jql
                    })

                try:
                    # Clean response: remove redundant fields
                    cleaned_data = (
                        ResponseTransformer(data)
                        .remove("expand", "self", "*.self", "*.avatarUrls", "*.expand", "*.iconUrl",
                                "*.subtask", "*.avatarId", "*.hierarchyLevel",
                                "*.statusCategory", "*.active", "*.timeZone", "*.locale", "*.accountType",
                                "*.properties", "*._links", "*.watches", "*.votes", "*.worklog",
                                "*.progress", "*.aggregateprogress", "*.aggregatetimeestimate",
                                "*.aggregatetimespent", "*.workratio", "*.lastViewed", "*.security",
                                "*.watchCount", "*.isWatching", "*.hasVoted", "*.startAt", "*.maxResults", "*.total",
                                "*.statuscategorychangedate", "*.status_category_changed",
                                "*.aggregatetimeoriginalestimate", "*.timeestimate", "*.timeoriginalestimate",
                                "*.timespent", "*.rank", "*.environment", "*.fixVersions", "*.versions",
                                "*.issuelinks", "*.subtasks", "*.organizations", "*.request_participants",
                                "*.responders", "*.projectTypeKey", "*.simplified", "*.description",
                                "*.id")  # Remove nested IDs (keep only top-level issue id/key)
                        .clean()
                    )

                    # Simple post-processing: Remove None customfield_* and empty objects
                    if isinstance(cleaned_data, dict) and "issues" in cleaned_data:
                        cleaned_data["issues"] = [
                            self._clean_issue_fields(issue) for issue in cleaned_data["issues"]
                        ]

                    # Normalize custom fields using field schema (only for fields with values)
                    field_schema = await self._fetch_and_cache_field_schema()
                    cleaned_data = await self._normalize_issues_in_response(cleaned_data, field_schema)

                    # Remove pagination fields - never send to frontend/LLM
                    if isinstance(cleaned_data, dict):
                        for field in ["nextPageToken", "next_cursor", "isLast", "total", "startAt", "maxResults"]:
                            cleaned_data.pop(field, None)

                    # Add web URLs to issues if available
                    site_url = await self._get_site_url()
                    if site_url and "issues" in cleaned_data:
                        for issue in cleaned_data["issues"]:
                            issue_key = issue.get("key")
                            if issue_key:
                                issue["url"] = f"{site_url}/browse/{issue_key}"
                            # Add URLs to Epic Links and other issue references in custom fields
                            self._add_urls_to_issue_references(issue, site_url)
                    logger.info(f"Response cleaned successfully - issues count: {len(cleaned_data.get('issues', [])) if isinstance(cleaned_data, dict) else 'N/A'}")
                except Exception as e:
                    logger.error(
                        f"Failed to clean response data - Error: {e}, "
                        f"Traceback: {traceback.format_exc()}, "
                        f"Raw data keys: {list(data.keys()) if isinstance(data, dict) else 'Not a dict'}"
                    )
                    return False, json.dumps({
                        "error": f"Failed to clean response: {str(e)}",
                        "jql_query": fixed_jql,
                        "raw_data_keys": list(data.keys()) if isinstance(data, dict) else "Not a dict"
                    })

                result = {
                    "message": "Issues fetched successfully",
                    "data": cleaned_data
                }
                if jql_warning:
                    result["warning"] = jql_warning
                    result["original_jql"] = jql
                    result["fixed_jql"] = fixed_jql

                result_json = json.dumps(result)
                logger.info(f"Returning success result - JSON length: {len(result_json)} chars")
                return True, result_json
            else:
                # Log detailed error information before handling
                try:
                    error_text = response.text()
                except Exception:
                    error_text = "Unable to extract error text"
                logger.error(
                    f"JIRA search_issues failed - Status: {response.status}, "
                    f"JQL: {fixed_jql}, "
                    f"Response: {error_text}"
                )
                # Include JQL information in error response
                error_result = self._handle_response(
                    response,
                    "Issues fetched successfully",
                    include_guidance=True
                )
                # Add JQL context to error
                try:
                    error_data = json.loads(error_result[1])
                    error_data["jql_query"] = fixed_jql
                    if fixed_jql != jql:
                        error_data["original_jql"] = jql
                        error_data["jql_auto_fixed"] = True
                    if jql_warning:
                        error_data["jql_warning"] = jql_warning
                    return error_result[0], json.dumps(error_data)
                except Exception:
                    # If parsing fails, return original error
                    return error_result
        except Exception as e:
            logger.error(
                f"Error searching issues - JQL: {jql}, "
                f"Exception: {type(e).__name__}: {e}, "
                f"Traceback: {traceback.format_exc()}"
            )
            error_response = {"error": str(e)}
            # jql is always in scope here as it's a function parameter
            error_response["jql_query"] = jql
            return False, json.dumps(error_response)

    @tool(
        path="/tools/jira/add_comment",
        short_description="Add a comment to a JIRA issue",
        description="Add a comment to an existing JIRA issue. The comment text is automatically converted to ADF format.",
        parameters=[
            ToolParameter(name="issue_key", type=ParameterType.STRING, description="Issue key (e.g., 'PA-123')", required=True),
            ToolParameter(name="comment", type=ParameterType.STRING, description="Comment text to add", required=True),
        ],
        tags=[Tag(key="category", value="project_management"), Tag(key="type", value="write")],
        args_summary=args_template("Adding comment to {issue_key}", "issue_key"),
        result_summary=confirmation("Comment added to {issue_key}", "issue_key"),
    )
    async def add_comment(self, issue_key: str, comment: str) -> tuple[bool, str]:

        try:
            # Convert plain text comment to ADF format if it's a string
            # Jira API requires comments in ADF (Atlassian Document Format) - a dict structure
            if isinstance(comment, str):
                comment_adf = self._convert_text_to_adf(comment)
                if not comment_adf:
                    return False, json.dumps({
                        "error": "Failed to convert comment to ADF format",
                        "guidance": "Comment text is required and cannot be empty"
                    })
                # Pass ADF dict directly (even though parameter is typed as str, it accepts dict at runtime)
                comment_body = comment_adf
            elif isinstance(comment, dict):
                # Already in ADF format (dict) - use directly
                comment_body = comment
            else:
                return False, json.dumps({
                    "error": f"Invalid comment type: {type(comment).__name__}",
                    "guidance": "Comment must be a string (plain text) or dict (ADF format)"
                })

            response = await self.client.add_comment(
                issueIdOrKey=issue_key,
                body_body=comment_body  # Pass ADF dict directly
            )

            if response.status == HttpStatusCode.SUCCESS.value or response.status == HttpStatusCode.CREATED.value:
                data = response.json()
                # Clean response: remove redundant fields
                cleaned_data = (
                    ResponseTransformer(data)
                    .remove("self", "*.self", "*.avatarUrls", "*.expand", "*.iconUrl",
                            "*.active", "*.timeZone", "*.locale", "*.accountType",
                            "*.properties", "*._links")
                    .clean()
                )
                return True, json.dumps({
                    "message": "Comment added successfully",
                    "data": cleaned_data
                })
            else:
                return self._handle_response(
                    response,
                    "Comment added successfully"
                )
        except Exception as e:
            logger.error(f"Error adding comment: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        path="/tools/jira/get_comments",
        short_description="Get comments for a JIRA issue",
        description="Get all comments for a specific JIRA issue.",
        parameters=[
            ToolParameter(name="issue_key", type=ParameterType.STRING, description="Issue key (e.g., 'PA-123')", required=True),
        ],
        tags=[Tag(key="category", value="project_management"), Tag(key="type", value="read")],
        args_summary=args_template("Fetching comments for {issue_key}", "issue_key"),
        result_summary=list_summary("comments", _jira_comment_label, "comment"),
    )
    async def get_comments(self, issue_key: str) -> tuple[bool, str]:
        """Get comments for an issue"""
        try:
            response = await self.client.get_comments(issueIdOrKey=issue_key)

            if response.status == HttpStatusCode.SUCCESS.value:
                data = response.json()
                # Clean response: remove redundant fields
                cleaned_data = (
                    ResponseTransformer(data)
                    .remove("self", "*.self", "*.avatarUrls", "*.expand", "*.iconUrl",
                            "*.active", "*.timeZone", "*.locale", "*.accountType",
                            "*.properties", "*._links")
                    .clean()
                )
                return True, json.dumps({
                    "message": "Comments fetched successfully",
                    "data": cleaned_data
                })
            else:
                return self._handle_response(
                    response,
                    "Comments fetched successfully"
                )
        except Exception as e:
            logger.error(f"Error getting comments: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        path="/tools/jira/search_users",
        short_description="Search JIRA users by name or email",
        description=(
            "Search JIRA users by name or email. Returns user accountId needed for JQL queries.\n"
            "NOTE: For searching issues assigned to the CURRENT user (self), use "
            "`assignee = currentUser()` in JQL instead of calling this tool."
        ),
        parameters=[
            ToolParameter(name="query", type=ParameterType.STRING, description="Search query (name or email)", required=True),
            ToolParameter(name="max_results", type=ParameterType.INTEGER, description="Max results (default 20)", required=False),
        ],
        tags=[Tag(key="category", value="project_management"), Tag(key="type", value="read")],
        args_summary=args_template('Searching Jira users: "{query}"', "query"),
        result_summary=list_summary(("data", "results"), _jira_user_label, "user"),
    )
    async def search_users(
        self,
        query: str,
        max_results: Optional[int] = None
    ) -> tuple[bool, str]:
        """Search JIRA users using the user picker API (more reliable than the search API)"""
        try:
            # Validate query parameter
            if not query or not query.strip():
                error_msg = "Query parameter is required and cannot be empty."
                logger.error(f"search_users validation failed: {error_msg}")
                return False, json.dumps({
                    "error": error_msg,
                    "guidance": (
                        "Provide a user name or email to search. "
                        "TIP: For issues assigned to yourself, use `assignee = currentUser()` in JQL instead."
                    )
                })

            query = query.strip()

            # Use find_users_for_picker which is more reliable than find_users
            # The /rest/api/3/user/picker endpoint always requires query and works correctly
            response = await self.client.find_users_for_picker(
                query=query,
                maxResults=max_results or 20
            )

            if response.status == HttpStatusCode.SUCCESS.value:
                data = response.json()
                # The user picker returns {"users": [...], "total": n, "header": "..."}
                users = data.get("users", []) if isinstance(data, dict) else data

                # Clean response: extract essential user info
                cleaned_users = []
                for user in users:
                    cleaned_user = {
                        "accountId": user.get("accountId"),
                        "displayName": user.get("displayName"),
                    }
                    # Try to extract email from html field if available
                    html = user.get("html", "")
                    if "(" in html and ")" in html:
                        # Extract email from format like "Name (email@example.com)"
                        email_part = html.split("(")[-1].rstrip(")")
                        if "@" in email_part:
                            cleaned_user["emailAddress"] = email_part

                    # Only include if accountId exists
                    if cleaned_user.get("accountId"):
                        cleaned_users.append(cleaned_user)

                return True, json.dumps({
                    "message": "Users fetched successfully",
                    "data": {
                        "results": cleaned_users,
                        "total": len(cleaned_users)
                    }
                })
            else:
                return self._handle_response(
                    response,
                    "Users fetched successfully"
                )
        except Exception as e:
            logger.error(f"Error searching users: {e}")
            return False, json.dumps({"error": str(e)})
    @tool(
        path="/tools/jira/get_project_metadata",
        short_description="Get project metadata including issue types and components",
        description="Get project metadata including available issue types, components, and project lead.",
        parameters=[
            ToolParameter(name="project_key", type=ParameterType.STRING, description="Project key (e.g., 'PA')", required=True),
        ],
        tags=[Tag(key="category", value="project_management"), Tag(key="type", value="read")],
    )
    async def get_project_metadata(self, project_key: str) -> tuple[bool, str]:
        """Get project metadata"""
        try:
            response = await self.client.get_project(projectIdOrKey=project_key)

            if response.status != HttpStatusCode.SUCCESS.value:
                return self._handle_response(
                    response,
                    "Project metadata fetched"
                )

            project = response.json()

            # Clean the project data before processing
            cleaned_project = (
                ResponseTransformer(project)
                .remove("self", "*.self", "*.avatarUrls", "*.expand", "*.iconUrl",
                        "*.active", "*.timeZone", "*.locale", "*.accountType",
                        "*.properties", "*._links", "*.subtask", "*.avatarId", "*.hierarchyLevel")
                .clean()
            )

            metadata = {
                "project_key": cleaned_project.get("key"),
                "project_name": cleaned_project.get("name"),
                "issue_types": [
                    {
                        "id": it.get("id"),
                        "name": it.get("name"),
                        "description": it.get("description"),
                        "subtask": it.get("subtask", False)
                    }
                    for it in cleaned_project.get("issueTypes", [])
                ],
                "components": [
                    {
                        "id": comp.get("id"),
                        "name": comp.get("name"),
                        "description": comp.get("description")
                    }
                    for comp in cleaned_project.get("components", [])
                ],
                "lead": cleaned_project.get("lead", {}).get("displayName")
            }

            return True, json.dumps({
                "message": "Project metadata fetched successfully",
                "metadata": metadata
            })
        except Exception as e:
            logger.error(f"Error getting project metadata: {e}")
            return False, json.dumps({"error": str(e)})

    # @tool(
    #     app_name="jira",
    #     tool_name="get_assignable_users",
    #     description="Get assignable users for a project",
    #     parameters=[
    #         ToolParameter(
    #             name="project_key",
    #             type=ParameterType.STRING,
    #             description="JIRA project key (e.g., 'PROJ', 'TEST', 'DEV'). CRITICAL: This must be a REAL project key from the user's JIRA workspace. DO NOT use placeholder values like 'YOUR_PROJECT_KEY', 'EXAMPLE', 'PLACEHOLDER', or any example values. If you don't know the project key, ASK the user for it first.",
    #             required=True
    #         ),
    #         ToolParameter(
    #             name="query",
    #             type=ParameterType.STRING,
    #             description="Optional search query",
    #             required=False
    #         ),
    #         ToolParameter(
    #             name="max_results",
    #             type=ParameterType.INTEGER,
    #             description="Maximum results (default 20)",
    #             required=False
    #         ),
    #     ],
    #     returns="List of assignable users"
    # )
    # def get_assignable_users(
    #     self,
    #     project_key: str,
    #     query: Optional[str] = None,
    #     max_results: Optional[int] = None
    # ) -> Tuple[bool, str]:
    #     """Get assignable users for a project"""
    #     try:
    #         response = await
    #             self.client.find_assignable_users(
    #                 project=project_key,
    #                 query=query,
    #                 maxResults=max_results
    #             )
    #         )

    #         if response.status == HttpStatusCode.SUCCESS.value:
    #             data = response.json()
    #             # Clean response: remove redundant fields, keep essential user info
    #             cleaned_data = (
    #                 ResponseTransformer(data)
    #                 .remove("self", "*.self", "*.avatarUrls", "*.expand", "*.iconUrl",
    #                         "*.active", "*.timeZone", "*.locale", "*.accountType",
    #                         "*.properties", "*._links")
    #                 .keep("accountId", "displayName", "emailAddress")
    #                 .clean()
    #             )
    #             return True, json.dumps({
    #                 "message": "Assignable users fetched successfully",
    #                 "data": cleaned_data
    #             })
    #         else:
    #             return self._handle_response(
    #                 response,
    #                 "Assignable users fetched successfully"
    #             )
    #     except Exception as e:
    #         logger.error(f"Error fetching assignable users: {e}")
    #         return False, json.dumps({"error": str(e)})
