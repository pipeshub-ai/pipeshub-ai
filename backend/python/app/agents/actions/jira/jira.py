import asyncio
import json
import logging
import re
import threading
from typing import Coroutine, Dict, List, Optional, Tuple

from app.agents.actions.response_transformer import ResponseTransformer
from app.agents.tools.decorator import tool
from app.agents.tools.enums import ParameterType
from app.agents.tools.models import ToolParameter
from app.sources.client.http.exception.exception import HttpStatusCode
from app.sources.client.http.http_response import HTTPResponse
from app.sources.client.jira.jira import JiraClient
from app.sources.external.jira.jira import JiraDataSource

logger = logging.getLogger(__name__)


class Jira:
    """JIRA tool exposed to the agents using JiraDataSource"""

    def __init__(self, client: JiraClient) -> None:
        """Initialize the JIRA tool

        Args:
            client: JIRA client object
        """
        self.client = JiraDataSource(client)
        # Dedicated background event loop for running coroutines from sync context
        self._bg_loop = asyncio.new_event_loop()
        self._bg_loop_thread = threading.Thread(
            target=self._start_background_loop,
            daemon=True
        )
        self._bg_loop_thread.start()

    def _start_background_loop(self) -> None:
        """Start the background event loop"""
        asyncio.set_event_loop(self._bg_loop)
        self._bg_loop.run_forever()

    def _run_async(self, coro: Coroutine[None, None, HTTPResponse]) -> HTTPResponse:
        """Run a coroutine safely from sync context via a dedicated loop.

        Args:
            coro: Coroutine that returns HTTPResponse

        Returns:
            HTTPResponse from the executed coroutine
        """
        future = asyncio.run_coroutine_threadsafe(coro, self._bg_loop)
        return future.result()

    def shutdown(self) -> None:
        """Gracefully stop the background event loop and thread."""
        try:
            if getattr(self, "_bg_loop", None) is not None and self._bg_loop.is_running():
                self._bg_loop.call_soon_threadsafe(self._bg_loop.stop)
            if getattr(self, "_bg_loop_thread", None) is not None:
                self._bg_loop_thread.join()
            if getattr(self, "_bg_loop", None) is not None:
                self._bg_loop.close()
        except Exception as exc:
            logger.warning(f"Jira shutdown encountered an issue: {exc}")

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

    def _resolve_user_to_account_id(
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
            response = self._run_async(
                self.client.find_assignable_users(
                    project=project_key,
                    query=query,
                    maxResults=1
                )
            )

            if response.status == HttpStatusCode.SUCCESS.value:
                data = response.json()
                if data and isinstance(data, list) and len(data) > 0:
                    return data[0].get('accountId')

            # Fallback: global user search
            response = self._run_async(
                self.client.find_users_by_query(
                    query=query,
                    maxResults=1
                )
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
        def quote_status(match):
            status_value = match.group(1).strip()
            # Don't quote if it already has quotes or is a function call
            if '"' in status_value or "'" in status_value or '(' in status_value:
                return match.group(0)
            return f'status = "{status_value}"'

        # Check if we need to fix status
        if status_unquoted_pattern.search(fixed_jql) and '"' not in fixed_jql.split('status')[1].split('=')[1].split()[0] if 'status' in fixed_jql else '':
            # Only log a warning, don't auto-fix as it might be intentional
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
        returns="Connection validation status with diagnostics"
    )
    def validate_connection(self) -> Tuple[bool, str]:
        """Validate JIRA connection and provide diagnostics"""
        try:
            # Simply try to fetch the current user to validate the connection
            # This is more reliable than trying to access the underlying client
            response = self._run_async(self.client.get_current_user())

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
        tool_name="convert_text_to_adf",
        description="Convert plain text to Atlassian Document Format (ADF)",
        parameters=[
            ToolParameter(
                name="text",
                type=ParameterType.STRING,
                description="Plain text to convert",
                required=True
            ),
        ],
        returns="ADF document structure"
    )
    def convert_text_to_adf(self, text: str) -> Tuple[bool, str]:
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
        parameters=[
            ToolParameter(
                name="project_key",
                type=ParameterType.STRING,
                description="JIRA project key (e.g., 'SP', 'PROJ', 'TEST'). CRITICAL: This must be a REAL project key from the user's JIRA workspace. DO NOT use placeholder values like 'YOUR_PROJECT_KEY', 'EXAMPLE', 'PLACEHOLDER', or any example values. If you don't know the project key, ASK the user for it first.",
                required=True
            ),
            ToolParameter(
                name="summary",
                type=ParameterType.STRING,
                description="Issue summary/title",
                required=True
            ),
            ToolParameter(
                name="issue_type_name",
                type=ParameterType.STRING,
                description="Issue type (e.g., 'Task', 'Bug', 'Story')",
                required=True
            ),
            ToolParameter(
                name="description",
                type=ParameterType.STRING,
                description="Issue description",
                required=False
            ),
            ToolParameter(
                name="assignee_account_id",
                type=ParameterType.STRING,
                description="Assignee account ID",
                required=False
            ),
            ToolParameter(
                name="assignee_query",
                type=ParameterType.STRING,
                description="Name or email to resolve assignee",
                required=False
            ),
            ToolParameter(
                name="priority_name",
                type=ParameterType.STRING,
                description="Priority (e.g., 'High', 'Medium', 'Low')",
                required=False
            ),
            ToolParameter(
                name="labels",
                type=ParameterType.LIST,
                description="List of labels",
                required=False,
                items={"type": "string"}
            ),
            ToolParameter(
                name="components",
                type=ParameterType.LIST,
                description="List of component names",
                required=False,
                items={"type": "string"}
            ),
        ],
        returns="Created issue details"
    )
    def create_issue(
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
                assignee_account_id = self._resolve_user_to_account_id(
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
            response = self._run_async(self.client.create_issue(fields=fields))

            # Handle reporter field errors by retrying without it
            if response.status == HttpStatusCode.BAD_REQUEST.value:
                try:
                    error_body = response.json()
                    errors = error_body.get('errors', {})

                    if 'reporter' in errors and 'reporter' in fields:
                        logger.info("Retrying without reporter field")
                        del fields['reporter']
                        response = self._run_async(self.client.create_issue(fields=fields))

                    elif 'assignee' in errors and 'assignee' in fields:
                        logger.info("Retrying without assignee field")
                        del fields['assignee']
                        response = self._run_async(self.client.create_issue(fields=fields))
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
                          "issuetype", "created", "updated", "description", "fields")
                    .clean()
                )
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
        tool_name="get_projects",
        description="Get all JIRA projects",
        parameters=[],
        returns="List of JIRA projects"
    )
    def get_projects(self) -> Tuple[bool, str]:
        """Get all JIRA projects"""
        try:
            response = self._run_async(self.client.get_all_projects())

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
        parameters=[
            ToolParameter(
                name="project_key",
                type=ParameterType.STRING,
                description="JIRA project key (e.g., 'PROJ', 'TEST', 'DEV'). CRITICAL: This must be a REAL project key from the user's JIRA workspace. DO NOT use placeholder values like 'YOUR_PROJECT_KEY', 'EXAMPLE', 'PLACEHOLDER', or any example values. If you don't know the project key, ASK the user for it first.",
                required=True
            ),
        ],
        returns="Project details"
    )
    def get_project(self, project_key: str) -> Tuple[bool, str]:
        """Get a specific JIRA project"""
        try:
            response = self._run_async(
                self.client.get_project(projectIdOrKey=project_key)
            )

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
        description="Get issues from a JIRA project",
        parameters=[
            ToolParameter(
                name="project_key",
                type=ParameterType.STRING,
                description="JIRA project key (e.g., 'PROJ', 'TEST', 'DEV'). CRITICAL: This must be a REAL project key from the user's JIRA workspace. DO NOT use placeholder values like 'YOUR_PROJECT_KEY', 'EXAMPLE', 'PLACEHOLDER', or any example values. If you don't know the project key, ASK the user for it first.",
                required=True
            ),
        ],
        returns="List of issues"
    )
    def get_issues(self, project_key: str) -> Tuple[bool, str]:
        """Get issues from a project"""
        try:
            response = self._run_async(
                self.client.search_and_reconsile_issues_using_jql_post(
                    jql=f"project = {project_key}",
                    # Explicitly request key field to ensure issue keys are returned
                    fields=["key", "summary", "status", "assignee", "reporter", "created", "updated", "priority", "issuetype"]
                )
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
                          "nextPageToken", "isLast")
                    .clean()
                )
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
        parameters=[
            ToolParameter(
                name="issue_key",
                type=ParameterType.STRING,
                description="Issue key",
                required=True
            ),
        ],
        returns="Issue details"
    )
    def get_issue(self, issue_key: str) -> Tuple[bool, str]:
        """Get a specific JIRA issue"""
        try:
            response = self._run_async(
                self.client.get_issue(issueIdOrKey=issue_key)
            )

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
                          "issuetype", "created", "updated", "description", "fields", "labels", "components")
                    .clean()
                )
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
        description="Search for JIRA issues using JQL (JIRA Query Language). CRITICAL: Use correct JQL syntax. Common mistakes: 'resolution = Unresolved' is INVALID - use 'resolution IS EMPTY' instead. For empty/null fields, use 'IS EMPTY' or 'IS NULL', not '='. Always use 'currentUser()' with parentheses for current user queries.",
        parameters=[
            ToolParameter(
                name="jql",
                type=ParameterType.STRING,
                description=(
                    "JQL (JIRA Query Language) query string. CRITICAL SYNTAX RULES:\n"
                    "1. For unresolved issues: Use 'resolution IS EMPTY' NOT 'resolution = Unresolved'\n"
                    "2. For current user: Use 'currentUser()' with parentheses, NOT 'currentUser'\n"
                    "3. For empty/null fields: Use 'IS EMPTY' or 'IS NULL', NOT '=' operator\n"
                    "4. For status values: Use 'status = \"Open\"' with quotes for text values\n"
                    "5. For assignee: Use accountId (find via search_users first) or 'currentUser()'\n"
                    "6. Combine conditions with AND/OR, use parentheses for grouping\n"
                    "7. Order results with 'ORDER BY field ASC/DESC'\n"
                    "\n"
                    "CORRECT EXAMPLES:\n"
                    "- 'assignee = currentUser() AND resolution IS EMPTY ORDER BY updated DESC'\n"
                    "- 'project = \"PROJ\" AND status = \"In Progress\"'\n"
                    "- 'reporter = currentUser() AND created >= -7d'\n"
                    "\n"
                    "WRONG EXAMPLES (DO NOT USE):\n"
                    "- 'resolution = Unresolved' ❌ (use 'resolution IS EMPTY')\n"
                    "- 'assignee = currentUser' ❌ (use 'currentUser()')\n"
                    "- 'status = Open' ❌ (use 'status = \"Open\"' with quotes)"
                ),
                required=True
            ),
        ],
        returns="List of matching issues"
    )
    def search_issues(self, jql: str) -> Tuple[bool, str]:
        """Search for JIRA issues"""
        try:
            # Validate and fix JQL query
            fixed_jql, jql_warning = self._validate_and_fix_jql(jql)

            if fixed_jql != jql:
                logger.info(f"JQL query auto-corrected: '{jql}' -> '{fixed_jql}'")

            response = self._run_async(
                self.client.search_and_reconsile_issues_using_jql(
                    jql=fixed_jql,
                    # Explicitly request key field to ensure issue keys are returned
                    fields=["key", "summary", "status", "assignee", "reporter", "created", "updated", "priority", "issuetype"]
                )
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
                          "nextPageToken", "isLast")
                    .clean()
                )
                result = {
                    "message": "Issues fetched successfully",
                    "data": cleaned_data
                }
                if jql_warning:
                    result["warning"] = jql_warning
                    result["original_jql"] = jql
                    result["fixed_jql"] = fixed_jql
                return True, json.dumps(result)
            else:
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
            logger.error(f"Error searching issues: {e}")
            error_response = {"error": str(e)}
            # jql is always in scope here as it's a function parameter
            error_response["jql_query"] = jql
            return False, json.dumps(error_response)

    @tool(
        app_name="jira",
        tool_name="add_comment",
        description="Add a comment to a JIRA issue",
        parameters=[
            ToolParameter(
                name="issue_key",
                type=ParameterType.STRING,
                description="Issue key",
                required=True
            ),
            ToolParameter(
                name="comment",
                type=ParameterType.STRING,
                description="Comment text",
                required=True
            ),
        ],
        returns="Comment details"
    )
    def add_comment(self, issue_key: str, comment: str) -> Tuple[bool, str]:
        """Add a comment to an issue"""
        try:
            response = self._run_async(
                self.client.add_comment(
                    issueIdOrKey=issue_key,
                    body_body=comment
                )
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
        parameters=[
            ToolParameter(
                name="issue_key",
                type=ParameterType.STRING,
                description="Issue key",
                required=True
            ),
        ],
        returns="List of comments"
    )
    def get_comments(self, issue_key: str) -> Tuple[bool, str]:
        """Get comments for an issue"""
        try:
            response = self._run_async(
                self.client.get_comments(issueIdOrKey=issue_key)
            )

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
        description="Search JIRA users by name or email",
        parameters=[
            ToolParameter(
                name="query",
                type=ParameterType.STRING,
                description="Name or email to search",
                required=True
            ),
            ToolParameter(
                name="max_results",
                type=ParameterType.INTEGER,
                description="Maximum results (default 20)",
                required=False
            ),
        ],
        returns="List of users with account IDs"
    )
    def search_users(
        self,
        query: str,
        max_results: Optional[int] = None
    ) -> Tuple[bool, str]:
        """Search JIRA users"""
        try:

            response = self._run_async(
                self.client.find_users(
                    query=query,
                    maxResults=max_results
                )
            )

            if response.status == HttpStatusCode.SUCCESS.value:
                data = response.json()
                # Clean response: remove redundant fields, keep essential user info
                cleaned_data = (
                    ResponseTransformer(data)
                    .remove("self", "*.self", "*.avatarUrls", "*.expand", "*.iconUrl",
                            "*.active", "*.timeZone", "*.locale", "*.accountType",
                            "*.properties", "*._links")
                    .keep("accountId", "displayName", "emailAddress")
                    .clean()
                )
                return True, json.dumps({
                    "message": "Users fetched successfully",
                    "data": cleaned_data
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
        tool_name="get_assignable_users",
        description="Get assignable users for a project",
        parameters=[
            ToolParameter(
                name="project_key",
                type=ParameterType.STRING,
                description="JIRA project key (e.g., 'PROJ', 'TEST', 'DEV'). CRITICAL: This must be a REAL project key from the user's JIRA workspace. DO NOT use placeholder values like 'YOUR_PROJECT_KEY', 'EXAMPLE', 'PLACEHOLDER', or any example values. If you don't know the project key, ASK the user for it first.",
                required=True
            ),
            ToolParameter(
                name="query",
                type=ParameterType.STRING,
                description="Optional search query",
                required=False
            ),
            ToolParameter(
                name="max_results",
                type=ParameterType.INTEGER,
                description="Maximum results (default 20)",
                required=False
            ),
        ],
        returns="List of assignable users"
    )
    def get_assignable_users(
        self,
        project_key: str,
        query: Optional[str] = None,
        max_results: Optional[int] = None
    ) -> Tuple[bool, str]:
        """Get assignable users for a project"""
        try:
            response = self._run_async(
                self.client.find_assignable_users(
                    project=project_key,
                    query=query,
                    maxResults=max_results
                )
            )

            if response.status == HttpStatusCode.SUCCESS.value:
                data = response.json()
                # Clean response: remove redundant fields, keep essential user info
                cleaned_data = (
                    ResponseTransformer(data)
                    .remove("self", "*.self", "*.avatarUrls", "*.expand", "*.iconUrl",
                            "*.active", "*.timeZone", "*.locale", "*.accountType",
                            "*.properties", "*._links")
                    .keep("accountId", "displayName", "emailAddress")
                    .clean()
                )
                return True, json.dumps({
                    "message": "Assignable users fetched successfully",
                    "data": cleaned_data
                })
            else:
                return self._handle_response(
                    response,
                    "Assignable users fetched successfully"
                )
        except Exception as e:
            logger.error(f"Error fetching assignable users: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="jira",
        tool_name="get_project_metadata",
        description="Get project metadata including issue types and components",
        parameters=[
            ToolParameter(
                name="project_key",
                type=ParameterType.STRING,
                description="JIRA project key (e.g., 'PROJ', 'TEST', 'DEV'). CRITICAL: This must be a REAL project key from the user's JIRA workspace. DO NOT use placeholder values like 'YOUR_PROJECT_KEY', 'EXAMPLE', 'PLACEHOLDER', or any example values. If you don't know the project key, ASK the user for it first.",
                required=True
            ),
        ],
        returns="Project metadata"
    )
    def get_project_metadata(self, project_key: str) -> Tuple[bool, str]:
        """Get project metadata"""
        try:
            response = self._run_async(
                self.client.get_project(projectIdOrKey=project_key)
            )

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
