import json
import logging
import re
import traceback
from typing import Dict, List, Optional, Tuple

from pydantic import BaseModel, Field, model_validator

from app.agents.actions.response_transformer import ResponseTransformer
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
    labels: Optional[List[str]] = Field(default=None, description="List of labels")
    components: Optional[List[str]] = Field(default=None, description="List of component names")

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
    assignee_account_id: Optional[str] = Field(default=None, description="Assignee account ID")
    assignee_query: Optional[str] = Field(default=None, description="Name or email to resolve assignee")
    priority_name: Optional[str] = Field(default=None, description="Priority")
    labels: Optional[List[str]] = Field(default=None, description="List of labels")
    components: Optional[List[str]] = Field(default=None, description="List of component names")
    status: Optional[str] = Field(default=None, description="Issue status (e.g., 'In Progress', 'Done')")

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

            # Handle direct 'fields' key (LLM sometimes sends this directly, not nested in update/updateData)
            if 'fields' in normalized and isinstance(normalized['fields'], dict):
                fields = normalized['fields']
                # Map common field names from fields dict to top-level
                if 'summary' in fields:
                    normalized['summary'] = fields['summary']
                if 'description' in fields:
                    normalized['description'] = fields['description']
                # Copy other fields
                for field in ['assignee_account_id', 'assignee_query', 'priority_name', 'labels', 'components', 'status']:
                    if field in fields:
                        normalized[field] = fields[field]
                # Remove the fields wrapper after extraction
                normalized.pop('fields', None)

            # Handle update/updateData wrapper
            update_wrapper = normalized.get('update') or normalized.get('updateData')
            if update_wrapper and isinstance(update_wrapper, dict):
                # Extract fields from nested structure
                if 'fields' in update_wrapper:
                    fields = update_wrapper['fields']
                    if isinstance(fields, dict):
                        # Map common field names
                        if 'summary' in fields:
                            normalized['summary'] = fields['summary']
                        if 'description' in fields:
                            normalized['description'] = fields['description']
                        # Copy other fields
                        for field in ['assignee_account_id', 'assignee_query', 'priority_name', 'labels', 'components', 'status']:
                            if field in fields:
                                normalized[field] = fields[field]
                # Extract issue_key from update wrapper if not already set
                if 'issue_key' not in normalized:
                    for key in ['issueId', 'issueIdOrKey', 'issue_id', 'issue_key', 'issueKey']:
                        if key in update_wrapper:
                            normalized['issue_key'] = update_wrapper[key]
                            break
                # Remove wrapper keys
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
                CommonFields.client_secret("Atlassian Developer Console")
            ],
            icon_path="/assets/icons/connectors/jira.svg",
            app_group="Project Management",
            app_description="JIRA OAuth application for agent integration"
        )
    ])\
    .configure(lambda builder: builder.with_icon("/assets/icons/connectors/jira.svg"))\
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

    def _handle_response(
        self,
        response: HTTPResponse,
        success_message: str,
        include_guidance: bool = False
    ) -> Tuple[bool, str]:
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
            error_response: Dict[str, object] = {
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

    def _convert_text_to_adf(self, text: str) -> Optional[Dict[str, object]]:
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

    def _validate_issue_fields(self, fields: Dict[str, object]) -> Tuple[bool, str]:
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
            # Get token from client
            client_obj = self.client._client
            if hasattr(client_obj, 'get_token'):
                token = client_obj.get_token()
                if token:
                    resources = await JiraClient.get_accessible_resources(token)
                    if resources and len(resources) > 0:
                        # Extract base URL from resource URL
                        resource_url = resources[0].url
                        # Resource URL is like 'https://example.atlassian.net'
                        self._site_url = resource_url.rstrip('/')
                        return self._site_url
        except Exception as e:
            logger.warning(f"Could not get site URL: {e}")

        return None


    def _validate_and_fix_jql(self, jql: str) -> Tuple[str, Optional[str]]:
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
        app_name="jira",
        tool_name="validate_connection",
        description="Validate JIRA connection and provide diagnostics",
        parameters=[],
        returns="Connection validation status with diagnostics",
        when_to_use=[
            "User wants to verify Jira connection",
            "Debugging connection/auth issues",
            "Checking Jira authentication status"
        ],
        when_not_to_use=[
            "User wants to use Jira features (use other tools)",
            "Normal Jira operations",
            "No Jira mention"
        ],
        primary_intent=ToolIntent.UTILITY,
        typical_queries=[
            "Check Jira connection",
            "Validate Jira authentication",
            "Test Jira connection"
        ],
        category=ToolCategory.PROJECT_MANAGEMENT
    )
    async def validate_connection(self) -> Tuple[bool, str]:
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
                    .keep("accountId", "displayName", "emailAddress")
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
        app_name="jira",
        tool_name="get_current_user",
        description=(
            "Get the current authenticated user's JIRA account details. "
            "Returns the accountId, displayName, and emailAddress of the user making the request. "
            "IMPORTANT: For JQL queries about 'my tickets' or 'assigned to me', you DON'T need to call "
            "this tool - just use `assignee = currentUser()` directly in the JQL query."
        ),
        parameters=[],
        returns="Current user's account details (accountId, displayName, emailAddress)",
        when_to_use=[
            "User wants their own Jira account info",
            "User mentions 'Jira' + wants their details",
            "User asks 'who am I in Jira?'"
        ],
        when_not_to_use=[
            "User wants 'my tickets' (use search_issues with currentUser())",
            "User wants to create/search issues (use other tools)",
            "No Jira mention"
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Who am I in Jira?",
            "Get my Jira account details",
            "Show my Jira user info"
        ],
        category=ToolCategory.PROJECT_MANAGEMENT
    )
    async def get_current_user(self) -> Tuple[bool, str]:
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
                    .keep("accountId", "displayName", "emailAddress")
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
        app_name="jira",
        tool_name="convert_text_to_adf",
        description="Convert plain text to Atlassian Document Format (ADF)",
        args_schema=ConvertTextToAdfInput,
        returns="ADF document structure",
        when_to_use=[
            "User needs to convert text to ADF format",
            "Preparing description for Jira issue",
            "Formatting text for Jira API"
        ],
        when_not_to_use=[
            "User wants to create issue (use create_issue - auto-converts)",
            "User wants to search issues (use search_issues)",
            "No Jira mention"
        ],
        primary_intent=ToolIntent.UTILITY,
        typical_queries=[
            "Convert text to ADF",
            "Format text for Jira",
            "Prepare ADF document"
        ],
        category=ToolCategory.PROJECT_MANAGEMENT
    )
    async def convert_text_to_adf(self, text: str) -> Tuple[bool, str]:
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
        app_name="jira",
        tool_name="create_issue",
        description="Create a new issue in JIRA",
        args_schema=CreateIssueInput,  # NEW: Pydantic schema
        returns="Created issue details",
        when_to_use=[
            "User wants to create a Jira ticket/issue",
            "User mentions 'Jira' + wants to create ticket",
            "User asks to create a new issue"
        ],
        when_not_to_use=[
            "User wants to search issues (use search_issues)",
            "User wants info ABOUT Jira (use retrieval)",
            "No Jira mention"
        ],
        primary_intent=ToolIntent.ACTION,
        typical_queries=[
            "Create a Jira ticket",
            "Open a new issue in Jira",
            "Create a bug report"
        ],
        category=ToolCategory.PROJECT_MANAGEMENT
    )
    async def create_issue(
        self,
        project_key: str,
        summary: str,
        issue_type_name: str,
        description: Optional[str] = None,
        assignee_account_id: Optional[str] = None,
        assignee_query: Optional[str] = None,
        priority_name: Optional[str] = None,
        labels: Optional[List[str]] = None,
        components: Optional[List[str]] = None
    ) -> Tuple[bool, str]:
        """Create a new JIRA issue"""
        try:
            # Build issue fields
            fields: Dict[str, object] = {
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
                    .keep("key", "id", "summary", "status", "assignee", "reporter", "priority",
                          "issuetype", "created", "updated", "description", "fields", "url")
                    .clean()
                )

                # Add web URL if available
                issue_key = cleaned_data.get("key")
                if issue_key:
                    site_url = await self._get_site_url()
                    if site_url:
                        cleaned_data["url"] = f"{site_url}/browse/{issue_key}"

                return True, json.dumps({
                    "message": "Issue created successfully",
                    "data": cleaned_data
                })
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
        app_name="jira",
        tool_name="update_issue",
        description="Update an existing JIRA issue. Can update summary, description, assignee, priority, labels, components, and status.",
        args_schema=UpdateIssueInput,  # NEW: Pydantic schema
        returns="Updated issue details",
        when_to_use=[
            "User wants to update/edit a Jira ticket",
            "User mentions 'Jira' + wants to modify issue",
            "User asks to change issue details/status"
        ],
        when_not_to_use=[
            "User wants to create issue (use create_issue)",
            "User wants to search issues (use search_issues)",
            "User wants info ABOUT Jira (use retrieval)",
            "No Jira mention"
        ],
        primary_intent=ToolIntent.ACTION,
        typical_queries=[
            "Update Jira ticket PA-123",
            "Change issue status to Done",
            "Edit Jira issue"
        ],
        category=ToolCategory.PROJECT_MANAGEMENT
    )
    async def update_issue(
        self,
        issue_key: str,
        summary: Optional[str] = None,
        description: Optional[str] = None,
        assignee_account_id: Optional[str] = None,
        assignee_query: Optional[str] = None,
        priority_name: Optional[str] = None,
        labels: Optional[List[str]] = None,
        components: Optional[List[str]] = None,
        status: Optional[str] = None
    ) -> Tuple[bool, str]:
        """Update an existing JIRA issue"""
        try:
            # Build fields dictionary with only provided values
            fields: Dict[str, object] = {}

            if summary:
                fields["summary"] = summary

            if description:
                # Convert plain text description to ADF format if it's a string
                if isinstance(description, str):
                    fields["description"] = self._convert_text_to_adf(description)
                elif isinstance(description, dict):
                    # Already in ADF format
                    fields["description"] = description
                else:
                    return False, json.dumps({
                        "error": "Description must be a string or ADF document",
                        "guidance": "Provide description as plain text (string) or ADF format (dict)"
                    })

            # Resolve assignee
            if assignee_query and not assignee_account_id:
                # Get project key from issue to resolve assignee
                issue_response = await self.client.get_issue(issueIdOrKey=issue_key)
                if issue_response.status == HttpStatusCode.SUCCESS.value:
                    issue_data = issue_response.json()
                    project_key = issue_data.get("fields", {}).get("project", {}).get("key")
                    if project_key:
                        assignee_account_id = await self._resolve_user_to_account_id(
                            project_key,
                            assignee_query
                        )

            if assignee_account_id:
                fields["assignee"] = {"accountId": assignee_account_id}

            if priority_name:
                fields["priority"] = {"name": priority_name}

            if labels:
                fields["labels"] = labels

            if components:
                fields["components"] = [{"name": comp} for comp in components]

            # Handle status transition if provided
            # Note: Status changes in JIRA must be done via transitions, not directly in fields
            transition = None
            if status:
                try:
                    # Get available transitions for the issue
                    transitions_response = await self.client.get_transitions(issueIdOrKey=issue_key)
                    if transitions_response.status == HttpStatusCode.SUCCESS.value:
                        transitions_data = transitions_response.json()
                        transitions = transitions_data.get("transitions", [])
                        # Find transition matching the status name (case-insensitive)
                        for trans in transitions:
                            if trans.get("to", {}).get("name", "").lower() == status.lower():
                                transition = {"id": trans.get("id")}
                                break
                        if not transition:
                            logger.warning(f"Status transition '{status}' not found. Available transitions: {[t.get('to', {}).get('name') for t in transitions]}")
                            # Don't fail, just log warning - fields update will still work
                except Exception as e:
                    logger.warning(f"Could not get transitions for issue {issue_key}: {e}. Status update will be skipped.")

            # Update issue
            response = await self.client.edit_issue(
                issueIdOrKey=issue_key,
                fields=fields if fields else None,
                transition=transition
            )

            if response.status == HttpStatusCode.SUCCESS.value or response.status == HttpStatusCode.NO_CONTENT.value:
                # If returnIssue was not set, fetch the updated issue
                if response.status == HttpStatusCode.NO_CONTENT.value:
                    issue_response = await self.client.get_issue(issueIdOrKey=issue_key)
                    if issue_response.status == HttpStatusCode.SUCCESS.value:
                        data = issue_response.json()
                    else:
                        # Add URL even if we can't fetch full issue
                        site_url = await self._get_site_url()
                        url = f"{site_url}/browse/{issue_key}" if site_url else None
                        return True, json.dumps({
                            "message": "Issue updated successfully",
                            "data": {"key": issue_key, "url": url} if url else {"key": issue_key}
                        })
                else:
                    data = response.json()

                # Clean response: remove redundant fields
                cleaned_data = (
                    ResponseTransformer(data)
                    .remove("self", "*.self", "*.avatarUrls", "*.expand", "*.iconUrl",
                            "*.description", "*.subtask", "*.avatarId", "*.hierarchyLevel",
                            "*.statusCategory", "*.active", "*.timeZone", "*.locale", "*.accountType",
                            "*.properties", "*._links")
                    .keep("key", "id", "summary", "status", "assignee", "reporter", "priority",
                          "issuetype", "created", "updated", "description", "fields")
                    .clean()
                )

                # Add web URL if available
                issue_key_from_data = cleaned_data.get("key") or issue_key
                if issue_key_from_data:
                    site_url = await self._get_site_url()
                    if site_url:
                        cleaned_data["url"] = f"{site_url}/browse/{issue_key_from_data}"

                return True, json.dumps({
                    "message": "Issue updated successfully",
                    "data": cleaned_data
                })
            else:
                return self._handle_response(
                    response,
                    "Issue updated successfully",
                    include_guidance=True
                )

        except Exception as e:
            logger.error(f"Error updating issue: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="jira",
        tool_name="get_projects",
        description="Get all JIRA projects",
        parameters=[],
        returns="List of JIRA projects",
        when_to_use=[
            "User wants to list all Jira projects",
            "User mentions 'Jira' + wants projects",
            "User asks for available projects"
        ],
        when_not_to_use=[
            "User wants specific project (use get_project)",
            "User wants to create/search issues (use other tools)",
            "No Jira mention"
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "List all Jira projects",
            "Show me Jira projects",
            "What projects are available?"
        ],
        category=ToolCategory.PROJECT_MANAGEMENT
    )
    async def get_projects(self) -> Tuple[bool, str]:
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
                    .keep("key", "id", "name", "projectTypeKey", "lead", "displayName", "emailAddress", "accountId")
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
        app_name="jira",
        tool_name="get_project",
        description="Get a specific JIRA project",
        args_schema=GetProjectInput,
        returns="Project details",
        when_to_use=[
            "User wants details about a specific project",
            "User mentions 'Jira' + project key",
            "User asks about a project"
        ],
        when_not_to_use=[
            "User wants all projects (use get_projects)",
            "User wants to create/search issues (use other tools)",
            "No Jira mention"
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Get project PA details",
            "Show me Jira project info",
            "What is project X?"
        ],
        category=ToolCategory.PROJECT_MANAGEMENT
    )
    async def get_project(self, project_key: str) -> Tuple[bool, str]:
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
                    .keep("key", "id", "name", "projectTypeKey", "lead", "displayName", "emailAddress", "accountId")
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
        app_name="jira",
        tool_name="get_issues",
        description="Get issues from a JIRA project. For more specific queries, use search_issues with custom JQL.",
        args_schema=GetIssuesInput,  # NEW: Pydantic schema
        returns="List of issues from the project",
        when_to_use=[
            "User wants issues from a specific project",
            "User mentions 'Jira' + project + wants issues",
            "User asks for project's tickets"
        ],
        when_not_to_use=[
            "User wants specific search (use search_issues)",
            "User wants single issue (use get_issue)",
            "User wants info ABOUT Jira (use retrieval)",
            "No Jira mention"
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Get issues from project PA",
            "Show tickets in project",
            "List issues for project"
        ],
        category=ToolCategory.PROJECT_MANAGEMENT
    )
    async def get_issues(
        self,
        project_key: str,
        days: Optional[int] = None,
        max_results: Optional[int] = None
    ) -> Tuple[bool, str]:
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
                # Explicitly request key field to ensure issue keys are returned
                fields=["key", "summary", "status", "assignee", "reporter", "created", "updated", "priority", "issuetype"]
            )

            if response.status == HttpStatusCode.SUCCESS.value:
                data = response.json()
                # Clean response: remove redundant fields, keep essential ones
                cleaned_data = (
                    ResponseTransformer(data)
                    .remove("expand", "self", "*.self", "*.avatarUrls", "*.expand", "*.iconUrl",
                            "*.description", "*.subtask", "*.avatarId", "*.hierarchyLevel",
                            "*.statusCategory", "*.active", "*.timeZone", "*.locale", "*.accountType",
                            "*.properties", "*._links", "*.watches", "*.votes", "*.worklog")
                    .keep("issues", "key", "id", "summary", "status", "assignee", "reporter",
                          "priority", "issuetype", "created", "updated", "description", "fields",
                          "total", "startAt", "maxResults", "nextPageToken", "isLast", "url")
                    .clean()
                )

                # Add web URLs to issues if available
                site_url = await self._get_site_url()
                if site_url and "issues" in cleaned_data:
                    for issue in cleaned_data["issues"]:
                        issue_key = issue.get("key")
                        if issue_key:
                            issue["url"] = f"{site_url}/browse/{issue_key}"

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
        app_name="jira",
        tool_name="get_issue",
        description="Get a specific JIRA issue",
        args_schema=GetIssueInput,  # NEW: Pydantic schema
        returns="Issue details",
        when_to_use=[
            "User wants details of a specific ticket",
            "User mentions 'Jira' + issue key",
            "User asks about a specific ticket"
        ],
        when_not_to_use=[
            "User wants to search issues (use search_issues)",
            "User wants to create issue (use create_issue)",
            "User wants info ABOUT Jira (use retrieval)",
            "No Jira mention"
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Get issue PA-123",
            "Show me ticket details",
            "What is issue X?"
        ],
        category=ToolCategory.PROJECT_MANAGEMENT
    )
    async def get_issue(self, issue_key: str) -> Tuple[bool, str]:
        """Get a specific JIRA issue"""
        try:
            response = await self.client.get_issue(issueIdOrKey=issue_key)

            if response.status == HttpStatusCode.SUCCESS.value:
                data = response.json()
                # Clean response: remove redundant fields
                cleaned_data = (
                    ResponseTransformer(data)
                    .remove("expand", "self", "*.self", "*.avatarUrls", "*.expand", "*.iconUrl",
                            "*.description", "*.subtask", "*.avatarId", "*.hierarchyLevel",
                            "*.statusCategory", "*.active", "*.timeZone", "*.locale", "*.accountType",
                            "*.properties", "*._links", "*.watches", "*.votes", "*.worklog")
                    .keep("key", "id", "summary", "status", "assignee", "reporter", "priority",
                          "issuetype", "created", "updated", "description", "fields", "labels",
                          "components", "url")
                    .clean()
                )

                # Add web URL if available
                issue_key = cleaned_data.get("key")
                if issue_key:
                    site_url = await self._get_site_url()
                    if site_url:
                        cleaned_data["url"] = f"{site_url}/browse/{issue_key}"

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
        app_name="jira",
        tool_name="search_issues",
        description="Search for JIRA issues using JQL (JIRA Query Language)",  # User-friendly (frontend)
        args_schema=SearchIssuesInput,  # NEW: Pydantic schema
        returns="List of matching issues with key, summary, status, assignee, etc.",
        llm_description=(
            "Search for JIRA issues using JQL (JIRA Query Language). "
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
        ),  # Detailed description for LLM
        when_to_use=[
            "User wants to search/find Jira tickets/issues",
            "User mentions 'Jira' + wants to find tickets",
            "User asks for 'my tickets', 'open issues', etc."
        ],
        when_not_to_use=[
            "User wants to create issue (use create_issue)",
            "User wants info ABOUT Jira (use retrieval)",
            "No Jira mention"
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Show my Jira tickets",
            "Find open issues in project PA",
            "Search for Jira issues"
        ],
        category=ToolCategory.PROJECT_MANAGEMENT
    )
    async def search_issues(self, jql: str, maxResults: Optional[int] = None) -> Tuple[bool, str]:
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
            logger.info(f"Calling Jira search API with JQL: {fixed_jql}")
            response = await self.client.search_and_reconsile_issues_using_jql_post(
                jql=fixed_jql,
                maxResults=maxResults or 50,
                # Explicitly request key field to ensure issue keys are returned
                fields=["key", "summary", "status", "assignee", "reporter", "created", "updated", "priority", "issuetype"]
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
                    # Clean response: remove redundant fields, keep essential ones
                    cleaned_data = (
                        ResponseTransformer(data)
                        .remove("expand", "self", "*.self", "*.avatarUrls", "*.expand", "*.iconUrl",
                                "*.description", "*.subtask", "*.avatarId", "*.hierarchyLevel",
                                "*.statusCategory", "*.active", "*.timeZone", "*.locale", "*.accountType",
                                "*.properties", "*._links", "*.watches", "*.votes", "*.worklog")
                        .keep("issues", "key", "id", "summary", "status", "assignee", "reporter",
                              "priority", "issuetype", "created", "updated", "description", "fields",
                              "total", "startAt", "maxResults", "nextPageToken", "isLast", "url")
                        .clean()
                    )

                    # Add web URLs to issues if available
                    site_url = await self._get_site_url()
                    if site_url and "issues" in cleaned_data:
                        for issue in cleaned_data["issues"]:
                            issue_key = issue.get("key")
                            if issue_key:
                                issue["url"] = f"{site_url}/browse/{issue_key}"
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
        app_name="jira",
        tool_name="add_comment",
        description="Add a comment to a JIRA issue",
        args_schema=AddCommentInput,  # NEW: Pydantic schema
        returns="Comment details",
        when_to_use=[
            "User wants to add comment to ticket",
            "User mentions 'Jira' + wants to comment",
            "User asks to comment on issue"
        ],
        when_not_to_use=[
            "User wants to create issue (use create_issue)",
            "User wants to read comments (use get_comments)",
            "User wants info ABOUT Jira (use retrieval)",
            "No Jira mention"
        ],
        primary_intent=ToolIntent.ACTION,
        typical_queries=[
            "Add comment to PA-123",
            "Comment on Jira ticket",
            "Reply to issue"
        ],
        category=ToolCategory.PROJECT_MANAGEMENT
    )
    async def add_comment(self, issue_key: str, comment: str) -> Tuple[bool, str]:

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
                    .keep("id", "body", "author", "created", "updated",
                          "accountId", "displayName", "emailAddress")
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
        app_name="jira",
        tool_name="get_comments",
        description="Get comments for a JIRA issue",
        args_schema=GetCommentsInput,
        returns="List of comments",
        when_to_use=[
            "User wants to read comments on ticket",
            "User mentions 'Jira' + wants issue comments",
            "User asks for ticket comments"
        ],
        when_not_to_use=[
            "User wants to add comment (use add_comment)",
            "User wants issue details (use get_issue)",
            "User wants info ABOUT Jira (use retrieval)",
            "No Jira mention"
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Get comments for PA-123",
            "Show comments on ticket",
            "What comments are on this issue?"
        ],
        category=ToolCategory.PROJECT_MANAGEMENT
    )
    async def get_comments(self, issue_key: str) -> Tuple[bool, str]:
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
                    .keep("comments", "id", "body", "author", "created", "updated",
                          "accountId", "displayName", "emailAddress")
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
        app_name="jira",
        tool_name="search_users",
        description="Search JIRA users by name or email",  # User-friendly (frontend)
        args_schema=SearchUsersInput,  # NEW: Pydantic schema
        returns="List of users with account IDs (accountId, displayName, emailAddress)",
        llm_description=(
            "Search JIRA users by name or email. Returns user accountId needed for JQL queries. "
            "NOTE: For searching issues assigned to the CURRENT user (self), use `assignee = currentUser()` "
            "in JQL instead of calling this tool - it's faster and more reliable."
        ),  # Detailed description for LLM
        when_to_use=[
            "User wants to find Jira user by name/email",
            "User mentions 'Jira' + wants to find user",
            "User needs accountId for assignee"
        ],
        when_not_to_use=[
            "User wants 'my tickets' (use search_issues with currentUser())",
            "User wants to create/search issues (use other tools)",
            "No Jira mention"
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Find Jira user by email",
            "Search for user in Jira",
            "Get user accountId"
        ],
        category=ToolCategory.PROJECT_MANAGEMENT
    )
    async def search_users(
        self,
        query: str,
        max_results: Optional[int] = None
    ) -> Tuple[bool, str]:
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
        app_name="jira",
        tool_name="get_project_metadata",
        description="Get project metadata including issue types and components",
        args_schema=GetProjectMetadataInput,
        returns="Project metadata",
        when_to_use=[
            "User wants project metadata (issue types, components)",
            "User mentions 'Jira' + wants project structure",
            "User asks about project configuration"
        ],
        when_not_to_use=[
            "User wants project info (use get_project)",
            "User wants to create/search issues (use other tools)",
            "No Jira mention"
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Get metadata for project PA",
            "Show issue types in project",
            "What components are in project?"
        ],
        category=ToolCategory.PROJECT_MANAGEMENT
    )
    async def get_project_metadata(self, project_key: str) -> Tuple[bool, str]:
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
                .keep("key", "id", "name", "projectTypeKey", "lead", "issueTypes", "components",
                      "displayName", "emailAddress", "accountId", "description")
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
