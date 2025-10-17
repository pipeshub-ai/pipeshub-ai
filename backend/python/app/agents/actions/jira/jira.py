import asyncio
import json
import logging
import re
import threading
from typing import Coroutine, Dict, List, Optional, Tuple

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
            error_text = response.text if hasattr(response, 'text') else str(response)
            error_response: Dict[str, object] = {
                "error": f"HTTP {response.status}",
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
            HttpStatusCode.GONE: (
                "JIRA instance is no longer available. This usually means: "
                "1) The JIRA instance has been deleted or moved, "
                "2) The cloud ID is incorrect, "
                "3) The authentication token is expired or invalid."
            ),
            HttpStatusCode.UNAUTHORIZED: (
                "Authentication failed. Please check: "
                "1) The authentication token is valid and not expired, "
                "2) The token has the necessary permissions for JIRA access."
            ),
            HttpStatusCode.FORBIDDEN: (
                "Access forbidden. Please check: "
                "1) The token has the required permissions, "
                "2) The user has access to the requested JIRA instance."
            ),
            HttpStatusCode.NOT_FOUND: (
                "Resource not found. Please check: "
                "1) The project key exists, "
                "2) The JIRA instance URL is correct."
            ),
            HttpStatusCode.BAD_REQUEST: (
                "Bad request. Please check field values and formats. "
                "Common issues: invalid account IDs, incorrect field types, "
                "or missing required fields."
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
            client = self.client.get_client()
            auth_header = client.headers.get("Authorization", "")

            if not auth_header.startswith("Bearer "):
                return False, json.dumps({
                    "message": "Invalid authentication header format",
                    "error": "Authorization header should start with 'Bearer '"
                })

            token = auth_header[7:]
            response = self._run_async(JiraClient.get_accessible_resources(token))

            if response.status == HttpStatusCode.SUCCESS.value:
                resources = response.json()
                return True, json.dumps({
                    "message": "JIRA connection is valid",
                    "accessible_resources": resources
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
                description="Project key (e.g., 'SP')",
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
                description="Project key",
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
                description="Project key",
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
                    jql=f"project = {project_key}"
                )
            )
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
        description="Search for JIRA issues using JQL",
        parameters=[
            ToolParameter(
                name="jql",
                type=ParameterType.STRING,
                description="JQL query string",
                required=True
            ),
        ],
        returns="List of matching issues"
    )
    def search_issues(self, jql: str) -> Tuple[bool, str]:
        """Search for JIRA issues"""
        try:
            response = self._run_async(
                self.client.search_and_reconsile_issues_using_jql(jql=jql)
            )
            return self._handle_response(
                response,
                "Issues fetched successfully",
                include_guidance=True
            )
        except Exception as e:
            logger.error(f"Error searching issues: {e}")
            return False, json.dumps({"error": str(e)})

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
                self.client.find_users_by_query(
                    query=query,
                    maxResults=max_results
                )
            )
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
                description="Project key",
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
                description="Project key",
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
            metadata = {
                "project_key": project.get("key"),
                "project_name": project.get("name"),
                "issue_types": [
                    {
                        "id": it.get("id"),
                        "name": it.get("name"),
                        "description": it.get("description"),
                        "subtask": it.get("subtask", False)
                    }
                    for it in project.get("issueTypes", [])
                ],
                "components": [
                    {
                        "id": comp.get("id"),
                        "name": comp.get("name"),
                        "description": comp.get("description")
                    }
                    for comp in project.get("components", [])
                ],
                "lead": project.get("lead", {}).get("displayName")
            }

            return True, json.dumps({
                "message": "Project metadata fetched successfully",
                "metadata": metadata
            })
        except Exception as e:
            logger.error(f"Error getting project metadata: {e}")
            return False, json.dumps({"error": str(e)})
