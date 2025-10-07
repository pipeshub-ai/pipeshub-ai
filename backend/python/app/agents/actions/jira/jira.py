import asyncio
import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from app.agents.tools.decorator import tool
from app.agents.tools.enums import ParameterType
from app.agents.tools.models import ToolParameter
from app.sources.client.http.exception.exception import HttpStatusCode
from app.sources.client.jira.jira import JiraClient
from app.sources.external.jira.jira import JiraDataSource

logger = logging.getLogger(__name__)

class Jira:
    """JIRA tool exposed to the agents using JiraDataSource"""
    def __init__(self, client: object) -> None:
        """Initialize the JIRA tool
        Args:
            client: JIRA client
        Returns:
            None
        """
        self.client = JiraDataSource(client)

    def _run_async(self, coro):
        """Helper method to run async operations in sync context"""
        import concurrent.futures
        def _runner() -> Any:
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(coro)
            finally:
                loop.close()
        # Always use a worker thread with a fresh event loop to avoid nested loop issues
        with concurrent.futures.ThreadPoolExecutor() as executor:
            return executor.submit(_runner).result()

    def _safe_payload(self, value):  # noqa: ANN001
        """Convert HTTPResponse or other objects to JSON-serializable payload."""
        try:
            if isinstance(value, (dict, list, str, int, float, bool)) or value is None:
                return value
            if hasattr(value, "json") and callable(getattr(value, "json")):
                try:
                    return value.json()
                except Exception:
                    pass
            if hasattr(value, "text"):
                return {"text": getattr(value, "text", "")}
            return {"raw": str(value)}
        except Exception:
            return {"raw": str(value)}

    def _maybe_error(self, response):  # noqa: ANN001
        data = response
        status_code = getattr(data, "status_code", None) or getattr(data, "status", None)
        if isinstance(status_code, int) and status_code >= HttpStatusCode.BAD_REQUEST:
            body = None
            try:
                if hasattr(data, "json") and callable(getattr(data, "json")):
                    body = data.json()
                elif hasattr(data, "text"):
                    body = data.text
            except Exception:
                body = str(data)

            # Provide specific guidance for common JIRA errors
            error_message = self._get_jira_error_guidance(status_code, body)
            if error_message:
                body = {"error_details": body, "guidance": error_message}

            return True, status_code, body
        return False, None, None

    def _get_jira_error_guidance(self, status_code: int, body) -> str | None:  # noqa: ANN001
        """Provide specific guidance for common JIRA API errors"""
        if status_code == HttpStatusCode.GONE:
            return ("JIRA instance is no longer available (410 Gone). This usually means: "
                   "1) The JIRA instance has been deleted or moved, "
                   "2) The cloud ID is incorrect, "
                   "3) The authentication token is expired or invalid. "
                   "Please verify the JIRA configuration and token validity.")
        elif status_code == HttpStatusCode.UNAUTHORIZED:
            return ("Authentication failed (401 Unauthorized). Please check: "
                   "1) The authentication token is valid and not expired, "
                   "2) The token has the necessary permissions for JIRA access.")
        elif status_code == HttpStatusCode.FORBIDDEN:
            return ("Access forbidden (403 Forbidden). Please check: "
                   "1) The token has the required permissions, "
                   "2) The user has access to the requested JIRA instance.")
        elif status_code == HttpStatusCode.NOT_FOUND:
            return ("Resource not found (404 Not Found). Please check: "
                   "1) The project key exists, "
                   "2) The JIRA instance URL is correct.")
        return None

    def _validate_jira_connection(self) -> Tuple[bool, str]:
        """Validate JIRA connection and provide diagnostics"""
        try:
            # Extract token from the client headers
            client = self.client.get_client()
            auth_header = client.headers.get("Authorization", "")
            if not auth_header.startswith("Bearer "):
                return False, json.dumps({
                    "message": "Invalid authentication header format",
                    "error": "Authorization header should start with 'Bearer '"
                })

            token = auth_header[7:]  # Remove "Bearer " prefix

            # Try to get accessible resources to validate the connection
            resp = self._run_async(JiraClient.get_accessible_resources(token))
            is_err, code, body = self._maybe_error(resp)
            if is_err:
                return False, json.dumps({
                    "message": "JIRA connection validation failed",
                    "error": {"status_code": code, "body": body}
                })

            # Parse the response to get resource information
            resources = self._safe_payload(resp)
            if not resources:
                return False, json.dumps({
                    "message": "No accessible JIRA resources found",
                    "error": "Token may be invalid or expired"
                })

            return True, json.dumps({
                "message": "JIRA connection is valid",
                "accessible_resources": resources
            })
        except Exception as e:
            logger.error(f"Error validating JIRA connection: {e}")
            return False, json.dumps({"message": f"Error validating JIRA connection: {e}"})

    def _get_jira_troubleshooting_info(self) -> Dict[str, Any]:
        """Get troubleshooting information for JIRA connection issues"""
        return {
            "common_issues": [
                {
                    "issue": "410 Gone Error",
                    "description": "JIRA instance is no longer available",
                    "solutions": [
                        "Verify the JIRA instance still exists and is accessible",
                        "Check if the cloud ID is correct",
                        "Ensure the authentication token is valid and not expired",
                        "Verify the JIRA instance hasn't been moved or renamed"
                    ]
                },
                {
                    "issue": "401 Unauthorized",
                    "description": "Authentication failed",
                    "solutions": [
                        "Check if the token is valid and not expired",
                        "Verify the token has the necessary permissions",
                        "Ensure the token is properly formatted"
                    ]
                },
                {
                    "issue": "403 Forbidden",
                    "description": "Access denied",
                    "solutions": [
                        "Check if the user has access to the JIRA instance",
                        "Verify the token has the required permissions",
                        "Contact JIRA administrator for access"
                    ]
                }
            ],
            "configuration_checklist": [
                "Verify JIRA base URL is correct",
                "Check authentication token is valid",
                "Ensure cloud ID matches the JIRA instance",
                "Verify network connectivity to JIRA",
                "Check if JIRA instance is active and accessible"
            ],
            "next_steps": [
                "Run validate_connection tool to check authentication",
                "Verify JIRA configuration in the system",
                "Contact system administrator if issues persist"
            ]
        }

    @tool(
        app_name="jira",
        tool_name="validate_connection",
        description="Validate JIRA connection and provide diagnostics for connection issues",
        parameters=[],
        returns="A message indicating whether the JIRA connection is valid with diagnostic information"
    )
    def validate_connection(self) -> Tuple[bool, str]:
        """Validate JIRA connection and provide diagnostics"""
        return self._validate_jira_connection()

    @tool(
        app_name="jira",
        tool_name="get_troubleshooting_info",
        description="Get troubleshooting information and solutions for common JIRA connection issues",
        parameters=[],
        returns="Troubleshooting information including common issues, solutions, and configuration checklist"
    )
    def get_troubleshooting_info(self) -> Tuple[bool, str]:
        """Get troubleshooting information for JIRA connection issues"""
        try:
            troubleshooting_info = self._get_jira_troubleshooting_info()
            return True, json.dumps({
                "message": "Troubleshooting information retrieved successfully",
                "troubleshooting_info": troubleshooting_info
            })
        except Exception as e:
            logger.error(f"Error getting troubleshooting info: {e}")
            return False, json.dumps({"message": f"Error getting troubleshooting info: {e}"})

    @tool(
        app_name="jira",
        tool_name="validate_issue_fields",
        description="Validate issue fields before creating an issue to identify potential problems",
        parameters=[
            ToolParameter(
                name="project_key",
                type=ParameterType.STRING,
                description="The key of the project to create the issue in",
                required=True
            ),
            ToolParameter(
                name="summary",
                type=ParameterType.STRING,
                description="The summary/title of the issue",
                required=True
            ),
            ToolParameter(
                name="issue_type_name",
                type=ParameterType.STRING,
                description="The name of the issue type",
                required=True
            ),
            ToolParameter(
                name="assignee_account_id",
                type=ParameterType.STRING,
                description="The account ID of the assignee (optional)",
                required=False
            ),
            ToolParameter(
                name="reporter_account_id",
                type=ParameterType.STRING,
                description="The account ID of the reporter (optional)",
                required=False
            ),
            ToolParameter(
                name="priority_name",
                type=ParameterType.STRING,
                description="The name of the priority (optional)",
                required=False
            ),
            ToolParameter(
                name="components",
                type=ParameterType.LIST,
                description="List of component names (optional)",
                required=False,
                items={"type": "string"}
            ),
        ],
        returns="Validation result with detailed information about field validity"
    )
    def validate_issue_fields(
        self,
        project_key: str,
        summary: str,
        issue_type_name: str,
        assignee_account_id: Optional[str] = None,
        reporter_account_id: Optional[str] = None,
        priority_name: Optional[str] = None,
        components: Optional[List[str]] = None
    ) -> Tuple[bool, str]:
        """Validate issue fields before creating an issue"""
        try:
            # Build the same fields structure as create_issue
            fields: Dict[str, Any] = {
                "project": {"key": project_key},
                "summary": summary,
                "issuetype": {"name": issue_type_name},
            }
            if assignee_account_id:
                fields["assignee"] = {"accountId": assignee_account_id}
            if reporter_account_id:
                fields["reporter"] = {"accountId": reporter_account_id}
            if priority_name:
                fields["priority"] = {"name": priority_name}
            if components:
                fields["components"] = [{"name": comp} for comp in components]

            # Validate the fields
            is_valid, validation_msg = self._validate_issue_fields(fields)

            return is_valid, json.dumps({
                "message": "Field validation completed",
                "is_valid": is_valid,
                "validation_message": validation_msg,
                "fields": fields,
                "recommendations": [
                    "Ensure project key exists and is accessible",
                    "Verify issue type name matches project's available issue types",
                    "Use account IDs (not email addresses) for assignee/reporter fields",
                    "Confirm priority name is valid for the project",
                    "Validate component names exist in the project",
                    "Check that all required fields are provided"
                ]
            })
        except Exception as e:
            logger.error(f"Error validating issue fields: {e}")
            return False, json.dumps({"message": f"Error validating issue fields: {e}"})

    @tool(
        app_name="jira",
        tool_name="validate_jql_query",
        description="Validate and provide guidance for JQL queries, especially for user-related fields",
        parameters=[
            ToolParameter(
                name="jql_query",
                type=ParameterType.STRING,
                description="The JQL query to validate",
                required=True
            ),
        ],
        returns="Validation result with suggestions for fixing JQL syntax issues"
    )
    def validate_jql_query(self, jql_query: str) -> Tuple[bool, str]:
        """Validate JQL query and provide guidance"""
        try:
            issues = []
            suggestions = []

            # Check for common JQL issues
            if "assignee =" in jql_query and "@" in jql_query:
                issues.append("Using email address for assignee field")
                suggestions.append("Use account ID instead of email: 'assignee = \"712020:ecfc535d-73a3-4ffd-85c9-172980339cc7\"'")

            if "reporter =" in jql_query and "@" in jql_query:
                issues.append("Using email address for reporter field")
                suggestions.append("Use account ID instead of email: 'reporter = \"712020:ecfc535d-73a3-4ffd-85c9-172980339cc7\"'")

            if "project =" in jql_query and not jql_query.count('"') >= 2:
                issues.append("Project key should be quoted")
                suggestions.append("Use quotes around project key: 'project = \"TP\"'")

            # Check for basic syntax issues
            if jql_query.count('(') != jql_query.count(')'):
                issues.append("Mismatched parentheses")
                suggestions.append("Ensure all parentheses are properly closed")

            if jql_query.count('"') % 2 != 0:
                issues.append("Mismatched quotes")
                suggestions.append("Ensure all string values are properly quoted")

            is_valid = len(issues) == 0

            return is_valid, json.dumps({
                "message": "JQL query validation completed",
                "is_valid": is_valid,
                "jql_query": jql_query,
                "issues_found": issues,
                "suggestions": suggestions,
                "common_jql_patterns": [
                    "project = \"PROJECT_KEY\"",
                    "assignee = \"account_id\"",
                    "reporter = \"account_id\"",
                    "status = \"Open\"",
                    "created >= -7d",
                    "summary ~ \"search text\""
                ],
                "note": "Use account IDs (not email addresses) for user-related fields. Account IDs can be found in user profile URLs or API responses."
            })
        except Exception as e:
            logger.error(f"Error validating JQL query: {e}")
            return False, json.dumps({"message": f"Error validating JQL query: {e}"})

    @tool(
        app_name="jira",
        tool_name="test_minimal_issue_creation",
        description="Test issue creation with minimal required fields to isolate validation issues",
        parameters=[
            ToolParameter(
                name="project_key",
                type=ParameterType.STRING,
                description="The key of the project to create the issue in",
                required=True
            ),
            ToolParameter(
                name="summary",
                type=ParameterType.STRING,
                description="The summary/title of the issue",
                required=True
            ),
            ToolParameter(
                name="issue_type_name",
                type=ParameterType.STRING,
                description="The name of the issue type",
                required=True
            ),
        ],
        returns="Test result with detailed error information if creation fails"
    )
    def test_minimal_issue_creation(
        self,
        project_key: str,
        summary: str,
        issue_type_name: str
    ) -> Tuple[bool, str]:
        """Test issue creation with minimal fields to isolate the problem"""
        try:
            # Build minimal fields - only required fields
            fields: Dict[str, Any] = {
                "project": {"key": project_key},
                "summary": summary,
                "issuetype": {"name": issue_type_name},
            }

            logger.info(f"Testing minimal issue creation with fields: {fields}")

            resp = self._run_async(self.client.create_issue(fields=fields))
            is_err, code, body = self._maybe_error(resp)

            if not is_err:
                return True, json.dumps({
                    "message": "Minimal issue creation successful",
                    "issue": self._safe_payload(resp),
                    "fields_used": fields
                })

            # Log detailed error information
            logger.error(f"Minimal issue creation failed - Status: {code}, Response: {body}")

            return False, json.dumps({
                "message": "Minimal issue creation failed",
                "error": {"status_code": code, "body": body},
                "fields_used": fields,
                "analysis": "This test uses only the minimal required fields. If this fails, the issue is likely with project key, issue type name, or basic field structure.",
                "next_steps": [
                    "Verify the project key exists and is accessible",
                    "Check if the issue type name is valid for this project",
                    "Ensure the user has permission to create issues in this project"
                ]
            })
        except Exception as e:
            logger.error(f"Error in minimal issue creation test: {e}")
            return False, json.dumps({"message": f"Error in minimal issue creation test: {e}"})

    @tool(
        app_name="jira",
        tool_name="convert_text_to_adf",
        description="Convert plain text to Atlassian Document Format (ADF) for JIRA Cloud descriptions",
        parameters=[
            ToolParameter(
                name="text",
                type=ParameterType.STRING,
                description="The plain text to convert to ADF format",
                required=True
            ),
        ],
        returns="ADF document structure that can be used in JIRA issue descriptions"
    )
    def convert_text_to_adf(self, text: str) -> Tuple[bool, str]:
        """Convert plain text to Atlassian Document Format (ADF)"""
        try:
            adf_document = self._convert_text_to_adf(text)

            return True, json.dumps({
                "message": "Text converted to ADF successfully",
                "original_text": text,
                "adf_document": adf_document,
                "usage_note": "Use this ADF document structure in the 'description' field when creating JIRA issues. JIRA Cloud requires descriptions to be in ADF format, not plain text.",
                "example_usage": {
                    "description": adf_document
                }
            })
        except Exception as e:
            logger.error(f"Error converting text to ADF: {e}")
            return False, json.dumps({"message": f"Error converting text to ADF: {e}"})

    @tool(
        app_name="jira",
        tool_name="create_issue",
        description="Create a new issue in JIRA with proper project parameters",
        parameters=[
            ToolParameter(
                name="project_key",
                type=ParameterType.STRING,
                description="The key of the project to create the issue in (e.g., 'SP' for Sample Project)",
                required=True
            ),
            ToolParameter(
                name="summary",
                type=ParameterType.STRING,
                description="The summary/title of the issue",
                required=True
            ),
            ToolParameter(
                name="issue_type_name",
                type=ParameterType.STRING,
                description="The name of the issue type (e.g., 'Task', 'Story', 'Bug', 'Epic', 'Sub-task')",
                required=True
            ),
            ToolParameter(
                name="description",
                type=ParameterType.STRING,
                description="The description of the issue",
                required=False
            ),
            ToolParameter(
                name="assignee_account_id",
                type=ParameterType.STRING,
                description="The account ID of the assignee (preferred if known)",
                required=False
            ),
            ToolParameter(
                name="assignee_query",
                type=ParameterType.STRING,
                description="Name or email to resolve assignee in JIRA (maps to accountId)",
                required=False
            ),
            ToolParameter(
                name="reporter_account_id",
                type=ParameterType.STRING,
                description="The account ID of the reporter (preferred if known)",
                required=False
            ),
            ToolParameter(
                name="reporter_query",
                type=ParameterType.STRING,
                description="Name or email to resolve reporter in JIRA (maps to accountId)",
                required=False
            ),
            ToolParameter(
                name="priority_name",
                type=ParameterType.STRING,
                description="The name of the priority (e.g., 'Highest', 'High', 'Medium', 'Low', 'Lowest')",
                required=False
            ),
            ToolParameter(
                name="labels",
                type=ParameterType.LIST,
                description="List of labels to add to the issue (e.g., ['bug', 'frontend', 'urgent'])",
                required=False,
                items={"type": "string"}
            ),
            ToolParameter(
                name="components",
                type=ParameterType.LIST,
                description="List of component names to add to the issue (can be obtained from project metadata)",
                required=False,
                items={"type": "string"}
            ),
            ToolParameter(
                name="custom_fields",
                type=ParameterType.DICT,
                description="Dictionary of custom field IDs and values for project-specific fields",
                required=False
            ),
        ],
        returns="A message indicating whether the issue was created successfully with issue details"
    )
    def create_issue(
        self,
        project_key: str,
        summary: str,
        issue_type_name: str,
        description: Optional[str] = None,
        assignee_account_id: Optional[str] = None,
        reporter_account_id: Optional[str] = None,
        priority_name: Optional[str] = None,
        labels: Optional[List[str]] = None,
        components: Optional[List[str]] = None,
        custom_fields: Optional[Dict[str, Any]] = None,
        assignee_query: Optional[str] = None,
        reporter_query: Optional[str] = None) -> Tuple[bool, str]:
        try:
            # Build Jira issue fields
            fields: Dict[str, Any] = {
                "project": {"key": project_key},
                "summary": summary,
                "issuetype": {"name": issue_type_name},
            }
            # Resolve reporter/assignee from queries if provided (Slack-provided IDs/names/emails)
            # If query looks like a Slack ID (<@U...> or U...), resolve via Slack then map to JIRA user by email/name
            def _looks_like_slack_id(q: str) -> bool:
                return q.startswith('U') or q.startswith('<@U')

            def _extract_slack_id(q: str) -> str:
                if q.startswith('<@') and q.endswith('>'):
                    return q[2:-1]
                return q

            def _resolve_to_jira_account_id(pref_project: str, query: str) -> Optional[str]:
                # If Slack-looking ID, keep query as-is; JIRA search will not match it. Prefer emails or names.
                search_query = query
                # First try assignable users for the project
                res = self._run_async(self.client.find_assignable_users(project=pref_project, query=search_query, maxResults=1))
                if not self._maybe_error(res)[0]:
                    data = self._safe_payload(res) or []
                    if data:
                        return data[0].get('accountId')
                # Fallback: global user search
                res = self._run_async(self.client.find_users_by_query(query=search_query, maxResults=1))
                if not self._maybe_error(res)[0]:
                    data = self._safe_payload(res) or []
                    if data:
                        return data[0].get('accountId')
                return None

            if reporter_query and not reporter_account_id:
                q = reporter_query.strip()
                if _looks_like_slack_id(q):
                    # Best-effort: treat as name placeholder; no Slack context here, so cannot fetch email.
                    # Caller should pass email or name when possible. We'll still try JIRA user search with the ID string.
                    reporter_account_id = _resolve_to_jira_account_id(project_key, q) or reporter_account_id
                else:
                    reporter_account_id = _resolve_to_jira_account_id(project_key, q) or reporter_account_id

            if assignee_query and not assignee_account_id:
                q = assignee_query.strip()
                if _looks_like_slack_id(q):
                    assignee_account_id = _resolve_to_jira_account_id(project_key, q) or assignee_account_id
                else:
                    assignee_account_id = _resolve_to_jira_account_id(project_key, q) or assignee_account_id
            if description is not None:
                # Normalize Slack mentions <@U...> to plain @name in description if present in summary context
                try:
                    import re
                    mention_re = re.compile(r"<@([A-Z0-9]+)>")
                    def _rep(m) -> str:
                        # We cannot call Slack here; just remove the markup and keep @ID
                        return f"@{m.group(1)}"
                    normalized = mention_re.sub(_rep, description)
                    fields["description"] = normalized
                except Exception:
                    fields["description"] = description
            if assignee_account_id:
                fields["assignee"] = {"accountId": assignee_account_id}
            # Note: Reporter field is often not settable in JIRA Cloud instances
            # Only set if explicitly allowed by the project configuration
            if reporter_account_id:
                # We'll validate this in the validation step
                fields["reporter"] = {"accountId": reporter_account_id}
            if priority_name:
                fields["priority"] = {"name": priority_name}
            if labels:
                fields["labels"] = labels
            if components:
                fields["components"] = [{"name": comp} for comp in components]
            if custom_fields:
                fields.update(custom_fields)

            # Validate fields before making the request
            is_valid, validation_msg = self._validate_issue_fields(fields)
            if not is_valid:
                return False, json.dumps({
                    "message": "Issue creation failed - Field validation error",
                    "validation_error": validation_msg,
                    "request_fields": fields
                })

            # Try to create the issue, handling common field issues
            resp = self._run_async(self.client.create_issue(fields=fields))
            is_err, code, body = self._maybe_error(resp)
            if not is_err:
                return True, json.dumps({"message": "Issue created successfully", "issue": self._safe_payload(resp)})

            # Provide detailed error information for 400 Bad Request
            if code == HttpStatusCode.BAD_REQUEST:
                # Log the full error for debugging
                logger.error(f"JIRA create issue 400 error - Full response: {body}")

                # Handle specific field errors
                error_body = body if isinstance(body, dict) else {}
                errors = error_body.get('errors', {})

                # If reporter field is not allowed, try again without it
                if 'reporter' in errors and 'reporter' in fields:
                    logger.info("Reporter field not allowed, retrying without reporter field")
                    fields_without_reporter = fields.copy()
                    del fields_without_reporter['reporter']

                    resp = self._run_async(self.client.create_issue(fields=fields_without_reporter))
                    is_err, code, body = self._maybe_error(resp)
                    if not is_err:
                        return True, json.dumps({
                            "message": "Issue created successfully (reporter field removed as not allowed)",
                            "issue": self._safe_payload(resp),
                            "note": "Reporter field was automatically removed as it's not allowed in this JIRA instance"
                        })

                # If assignee is invalid, try again without it
                if 'assignee' in errors and 'assignee' in fields:
                    logger.info("Assignee field invalid, retrying without assignee field")
                    fields_without_assignee = fields.copy()
                    del fields_without_assignee['assignee']

                    resp = self._run_async(self.client.create_issue(fields=fields_without_assignee))
                    is_err, code, body = self._maybe_error(resp)
                    if not is_err:
                        return True, json.dumps({
                            "message": "Issue created successfully (assignee field removed as invalid)",
                            "issue": self._safe_payload(resp),
                            "note": "Assignee field was automatically removed as the account ID is invalid"
                        })

                return False, json.dumps({
                    "message": "Error creating issue - Bad Request",
                    "error": {"status_code": code, "body": body},
                    "request_fields": fields,
                    "guidance": "Check the error details for field validation issues. Common problems: invalid project key, issue type name, assignee/reporter account IDs (use account ID not email), component names, or description format. Note: JIRA Cloud requires descriptions in Atlassian Document Format (ADF) - plain text descriptions are automatically converted.",
                    "debug_info": "Full error response logged for debugging. Check server logs for detailed error information."
                })

            return False, json.dumps({"message": "Error creating issue", "error": {"status_code": code, "body": body}})
        except Exception as e:
            logger.error(f"Error creating issue: {e}")
            return False, json.dumps({"message": f"Error creating issue: {e}"})

    def _convert_text_to_adf(self, text: str) -> Dict[str, Any]:
        """Convert plain text to Atlassian Document Format (ADF)"""
        if not text:
            return None

        # Simple ADF structure for plain text
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

    def _validate_issue_fields(self, fields: Dict[str, Any]) -> Tuple[bool, str]:
        """Validate issue fields before creating the issue"""
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

    @tool(
        app_name="jira",
        tool_name="get_projects",
        description="Get all JIRA projects",
        parameters=[],
        returns="A list of JIRA projects"
    )
    def get_projects(self) -> Tuple[bool, str]:
        try:
            resp = self._run_async(self.client.get_all_projects())
            is_err, code, body = self._maybe_error(resp)
            if not is_err:
                return True, json.dumps({"message": "Projects fetched successfully", "projects": self._safe_payload(resp)})

            # If we get a 410 error, suggest validating the connection
            if code == HttpStatusCode.GONE:
                validation_result = self._validate_jira_connection()
                troubleshooting_info = self._get_jira_troubleshooting_info()
                return False, json.dumps({
                    "message": "Error getting projects - JIRA instance unavailable",
                    "error": {"status_code": code, "body": body},
                    "connection_validation": json.loads(validation_result[1]),
                    "troubleshooting_info": troubleshooting_info
                })

            return False, json.dumps({"message": "Error getting projects", "error": {"status_code": code, "body": body}})
        except Exception as e:
            logger.error(f"Error getting projects: {e}")
            return False, json.dumps({"message": f"Error getting projects: {e}"})

    @tool(
        app_name="jira",
        tool_name="validate_project_key",
        parameters=[
            ToolParameter(
                name="project_key",
                type=ParameterType.STRING,
                description="The project key to validate",
                required=True
            )
        ]
    )
    def validate_project_key(self, project_key: str) -> Tuple[bool, str]:
        """Validate if a project key exists in JIRA"""
        try:
            resp = self._run_async(self.client.get_project(project_key))
            is_err, code, body = self._maybe_error(resp)
            if not is_err:
                project_data = self._safe_payload(resp)
                return True, json.dumps({
                    "message": f"Project '{project_key}' exists",
                    "project": project_data
                })
            else:
                return False, json.dumps({
                    "message": f"Project '{project_key}' does not exist or is not accessible",
                    "error": {"status_code": code, "body": body}
                })
        except Exception as e:
            logger.error(f"Error validating project key: {e}")
            return False, json.dumps({"message": f"Error validating project key: {e}"})

    @tool(
        app_name="jira",
        tool_name="get_project",
        description="Get a specific JIRA project",
        parameters=[
            ToolParameter(name="project_key", type=ParameterType.STRING, description="The key of the project to get the details of"),
        ],
        returns="A message indicating whether the project was fetched successfully"
    )
    def get_project(self, project_key: str) -> Tuple[bool, str]:
        try:
            resp = self._run_async(self.client.get_project(projectIdOrKey=project_key))
            is_err, code, body = self._maybe_error(resp)
            if not is_err:
                return True, json.dumps({"message": "Project fetched successfully", "project": self._safe_payload(resp)})
            return False, json.dumps({"message": "Error getting project", "error": {"status_code": code, "body": body}})
        except Exception as e:
            logger.error(f"Error getting project: {e}")
            return False, json.dumps({"message": f"Error getting project: {e}"})

    @tool(
        app_name="jira",
        tool_name="get_issues",
        description="Get all JIRA issues",
        parameters=[
            ToolParameter(name="project_key", type=ParameterType.STRING, description="The key of the project to get the issues from"),
        ],
        returns="A list of JIRA issues"
    )
    def get_issues(self, project_key: str) -> Tuple[bool, str]:
        try:
            # Use POST variant to avoid GET /search/jql 400 and support longer JQL
            resp = self._run_async(self.client.search_and_reconsile_issues_using_jql_post(
                jql=f"project = {project_key}"
            ))
            is_err, code, body = self._maybe_error(resp)
            if not is_err:
                return True, json.dumps({"message": "Issues fetched successfully", "issues": self._safe_payload(resp)})

            # If we get a 410 error, suggest validating the connection
            if code == HttpStatusCode.GONE:
                validation_result = self._validate_jira_connection()
                troubleshooting_info = self._get_jira_troubleshooting_info()
                return False, json.dumps({
                    "message": "Error getting issues - JIRA instance unavailable",
                    "error": {"status_code": code, "body": body},
                    "connection_validation": json.loads(validation_result[1]),
                    "troubleshooting_info": troubleshooting_info
                })

            return False, json.dumps({"message": "Error getting issues", "error": {"status_code": code, "body": body}})
        except Exception as e:
            logger.error(f"Error getting issues: {e}")
            return False, json.dumps({"message": f"Error getting issues: {e}"})

    @tool(
        app_name="jira",
        tool_name="get_issue_types",
        description="Get all JIRA issue types",
        parameters=[
            ToolParameter(name="project_key", type=ParameterType.STRING, description="The key of the project to get the issue types from"),
        ],
        returns="A list of JIRA issue types"
    )
    def get_issue_types(self, project_key: Optional[str] = None) -> Tuple[bool, str]:
        try:
            # Fetch all issue types (user-scoped)
            resp = self._run_async(self.client.get_issue_all_types())
            is_err, code, body = self._maybe_error(resp)
            if not is_err:
                return True, json.dumps({"message": "Issue types fetched successfully", "issue_types": self._safe_payload(resp)})
            return False, json.dumps({"message": "Error getting issue types", "error": {"status_code": code, "body": body}})
        except Exception as e:
            logger.error(f"Error getting issue types: {e}")
            return False, json.dumps({"message": f"Error getting issue types: {e}"})

    @tool(
        app_name="jira",
        tool_name="get_issue",
        description="Get a specific JIRA issue",
        parameters=[
            ToolParameter(name="issue_key", type=ParameterType.STRING, description="The key of the issue to get the details of"),
        ],
        returns="A message indicating whether the issue was fetched successfully"
    )
    def get_issue(self, issue_key: str) -> Tuple[bool, str]:
        try:
            resp = self._run_async(self.client.get_issue(issueIdOrKey=issue_key))
            is_err, code, body = self._maybe_error(resp)
            if not is_err:
                return True, json.dumps({"message": "Issue fetched successfully", "issue": self._safe_payload(resp)})
            return False, json.dumps({"message": "Error getting issue", "error": {"status_code": code, "body": body}})
        except Exception as e:
            logger.error(f"Error getting issue: {e}")
            return False, json.dumps({"message": f"Error getting issue: {e}"})

    @tool(
        app_name="jira",
        tool_name="search_issues",
        description="Search for JIRA issues",
        parameters=[
            ToolParameter(name="jql", type=ParameterType.STRING, description="The JQL query to search for issues"),
        ],
        returns="A list of JIRA issues"
    )
    def search_issues(self, jql: str) -> Tuple[bool, str]:
        try:
            resp = self._run_async(self.client.search_and_reconsile_issues_using_jql(jql=jql))
            is_err, code, body = self._maybe_error(resp)
            if not is_err:
                return True, json.dumps({"message": "Issues fetched successfully", "issues": self._safe_payload(resp)})

            # Provide specific guidance for JQL errors
            if code == HttpStatusCode.BAD_REQUEST:
                return False, json.dumps({
                    "message": "Error searching issues - Invalid JQL query",
                    "error": {"status_code": code, "body": body},
                    "jql_query": jql,
                    "guidance": "Common JQL issues: Use account IDs (not email addresses) for assignee/reporter fields, ensure proper field names, check syntax. Example: 'assignee = \"712020:ecfc535d-73a3-4ffd-85c9-172980339cc7\"' instead of 'assignee = \"email@domain.com\"'"
                })

            return False, json.dumps({"message": "Error searching issues", "error": {"status_code": code, "body": body}})
        except Exception as e:
            logger.error(f"Error searching issues: {e}")
            return False, json.dumps({"message": f"Error searching issues: {e}"})

    @tool(
        app_name="jira",
        tool_name="add_comment",
        description="Add a comment to a JIRA issue",
        parameters=[
            ToolParameter(name="issue_key", type=ParameterType.STRING, description="The key of the issue to add the comment to"),
            ToolParameter(name="comment", type=ParameterType.STRING, description="The comment to add"),
        ],
        returns="A message indicating whether the comment was added successfully"
    )
    def add_comment(self, issue_key: str, comment: str) -> Tuple[bool, str]:
        try:
            resp = self._run_async(self.client.add_comment(
                issueIdOrKey=issue_key,
                body_body=comment
            ))
            is_err, code, body = self._maybe_error(resp)
            if not is_err:
                return True, json.dumps({"message": "Comment added successfully", "comment": self._safe_payload(resp)})
            return False, json.dumps({"message": "Error adding comment", "error": {"status_code": code, "body": body}})
        except Exception as e:
            logger.error(f"Error adding comment: {e}")
            return False, json.dumps({"message": f"Error adding comment: {e}"})

    @tool(
        app_name="jira",
        tool_name="get_comments",
        description="Get the comments for a JIRA issue",
        parameters=[
            ToolParameter(name="issue_key", type=ParameterType.STRING, description="The key of the issue to get the comments from"),
        ],
        returns="A list of JIRA comments"
    )
    def get_comments(self, issue_key: str) -> Tuple[bool, str]:
        try:
            resp = self._run_async(self.client.get_comments(issueIdOrKey=issue_key))
            is_err, code, body = self._maybe_error(resp)
            if not is_err:
                return True, json.dumps({"message": "Comments fetched successfully", "comments": self._safe_payload(resp)})
            return False, json.dumps({"message": "Error getting comments", "error": {"status_code": code, "body": body}})
        except Exception as e:
            logger.error(f"Error getting comments: {e}")
            return False, json.dumps({"message": f"Error getting comments: {e}"})

    @tool(
        app_name="jira",
        tool_name="transition_issue",
        description="Transition a JIRA issue",
        parameters=[
            ToolParameter(name="issue_key", type=ParameterType.STRING, description="The key of the issue to transition"),
            ToolParameter(name="transition_id", type=ParameterType.STRING, description="The ID of the transition to apply"),
        ],
        returns="A message indicating whether the issue was transitioned successfully"
    )
    def transition_issue(self, issue_key: str, transition_id: str) -> Tuple[bool, str]:
        try:
            resp = self._run_async(self.client.do_transition(
                issueIdOrKey=issue_key,
                transition={"id": transition_id}
            ))
            is_err, code, body = self._maybe_error(resp)
            if not is_err:
                return True, json.dumps({"message": "Issue transitioned successfully", "transition": self._safe_payload(resp)})
            return False, json.dumps({"message": "Error transitioning issue", "error": {"status_code": code, "body": body}})
        except Exception as e:
            logger.error(f"Error transitioning issue: {e}")
            return False, json.dumps({"message": f"Error transitioning issue: {e}"})

    @tool(
        app_name="jira",
        tool_name="get_project_metadata",
        description="Get JIRA project metadata including issue types, components, and lead information",
        parameters=[
            ToolParameter(name="project_key", type=ParameterType.STRING, description="The key of the project to get metadata for"),
        ],
        returns="Project metadata including issue types, components, and lead information"
    )
    def get_project_metadata(self, project_key: str) -> Tuple[bool, str]:
        """Get project metadata useful for creating issues"""
        try:
            project_resp = self._run_async(self.client.get_project(projectIdOrKey=project_key))
            is_err, code, body = self._maybe_error(project_resp)
            if is_err:
                return False, json.dumps({"message": "Error getting project metadata", "error": {"status_code": code, "body": body}})
            project = self._safe_payload(project_resp) or {}

            # Extract useful metadata
            metadata = {
                "project_key": project.get("key"),
                "project_id": project.get("id"),
                "project_name": project.get("name"),
                "project_description": project.get("description"),
                "issue_types": [
                    {
                        "id": issue_type.get("id"),
                        "name": issue_type.get("name"),
                        "description": issue_type.get("description"),
                        "subtask": issue_type.get("subtask", False),
                        "hierarchy_level": issue_type.get("hierarchyLevel", 0)
                    }
                    for issue_type in project.get("issueTypes", [])
                ],
                "components": [
                    {
                        "id": comp.get("id"),
                        "name": comp.get("name"),
                        "description": comp.get("description")
                    }
                    for comp in project.get("components", [])
                ],
                "lead": {
                    "account_id": project.get("lead", {}).get("accountId"),
                    "display_name": project.get("lead", {}).get("displayName"),
                    "email": project.get("lead", {}).get("emailAddress")
                } if project.get("lead") else None,
                "project_type": project.get("projectTypeKey"),
                "style": project.get("style"),
                "simplified": project.get("simplified", False)
            }

            return True, json.dumps({
                "message": "Project metadata fetched successfully",
                "metadata": metadata
            })
        except Exception as e:
            logger.error(f"Error getting project metadata: {e}")
            return False, json.dumps({"message": f"Error getting project metadata: {e}"})

    @tool(
        app_name="jira",
        tool_name="search_users",
        description="Search JIRA users by name or email; returns accountId and basic profile fields",
        parameters=[
            ToolParameter(
                name="query",
                type=ParameterType.STRING,
                description="Name or email substring to search",
                required=True
            ),
            ToolParameter(
                name="max_results",
                type=ParameterType.INTEGER,
                description="Maximum users to return (default 20)",
                required=False
            ),
        ],
        returns="A list of users with fields: accountId, displayName, emailAddress (if available), active"
    )
    def search_users(self, query: str, max_results: Optional[int] = None) -> Tuple[bool, str]:
        try:
            response = self._run_async(self.client.find_users_by_query(
                query=query,
                maxResults=max_results
            ))
            is_err, code, body = self._maybe_error(response)
            if is_err:
                return False, json.dumps({
                    "message": "Error searching users",
                    "error": {"status_code": code, "body": body}
                })

            raw_users = self._safe_payload(response) or []
            users = [
                {
                    "accountId": u.get("accountId"),
                    "displayName": u.get("displayName"),
                    "emailAddress": u.get("emailAddress"),
                    "active": u.get("active"),
                }
                for u in raw_users
            ]
            return True, json.dumps({
                "message": "Users fetched successfully",
                "users": users
            })
        except Exception as e:
            logger.error(f"Error searching users: {e}")
            return False, json.dumps({"message": f"Error searching users: {e}"})

    @tool(
        app_name="jira",
        tool_name="get_assignable_users",
        description="List users assignable to issues in a project (maps names to accountIds)",
        parameters=[
            ToolParameter(
                name="project_key",
                type=ParameterType.STRING,
                description="Project key to fetch assignable users for",
                required=True
            ),
            ToolParameter(
                name="query",
                type=ParameterType.STRING,
                description="Optional name or email substring to filter",
                required=False
            ),
            ToolParameter(
                name="max_results",
                type=ParameterType.INTEGER,
                description="Maximum users to return (default 20)",
                required=False
            ),
        ],
        returns="A list of assignable users with fields: accountId, displayName, emailAddress (if available)"
    )
    def get_assignable_users(self, project_key: str, query: Optional[str] = None, max_results: Optional[int] = None) -> Tuple[bool, str]:
        try:
            response = self._run_async(self.client.find_assignable_users(
                project=project_key,
                query=query,
                maxResults=max_results
            ))
            is_err, code, body = self._maybe_error(response)
            if is_err:
                return False, json.dumps({
                    "message": "Error fetching assignable users",
                    "error": {"status_code": code, "body": body}
                })

            raw_users = self._safe_payload(response) or []
            users = [
                {
                    "accountId": u.get("accountId"),
                    "displayName": u.get("displayName"),
                    "emailAddress": u.get("emailAddress"),
                    "active": u.get("active"),
                }
                for u in raw_users
            ]
            return True, json.dumps({
                "message": "Assignable users fetched successfully",
                "users": users
            })
        except Exception as e:
            logger.error(f"Error fetching assignable users: {e}")
            return False, json.dumps({"message": f"Error fetching assignable users: {e}"})
