import asyncio
import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from app.agents.tools.decorator import tool
from app.agents.tools.enums import ParameterType
from app.agents.tools.models import ToolParameter
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
        try:
            asyncio.get_running_loop()
            # We're in an async context, use asyncio.run in a thread
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(asyncio.run, coro)
                return future.result()
        except RuntimeError:
            # No running loop, we can use asyncio.run
            return asyncio.run(coro)

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
        if isinstance(status_code, int) and status_code >= 400:
            body = None
            try:
                if hasattr(data, "json") and callable(getattr(data, "json")):
                    body = data.json()
                elif hasattr(data, "text"):
                    body = data.text
            except Exception:
                body = str(data)
            return True, status_code, body
        return False, None, None


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
                description="The account ID of the assignee (can be obtained from project lead or user search)",
                required=False
            ),
            ToolParameter(
                name="reporter_account_id",
                type=ParameterType.STRING,
                description="The account ID of the reporter (can be obtained from project lead or user search)",
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
        custom_fields: Optional[Dict[str, Any]] = None) -> Tuple[bool, str]:
        try:
            # Build Jira issue fields
            fields: Dict[str, Any] = {
                "project": {"key": project_key},
                "summary": summary,
                "issuetype": {"name": issue_type_name},
            }
            if description is not None:
                fields["description"] = description
            if assignee_account_id:
                fields["assignee"] = {"accountId": assignee_account_id}
            if reporter_account_id:
                fields["reporter"] = {"accountId": reporter_account_id}
            if priority_name:
                fields["priority"] = {"name": priority_name}
            if labels:
                fields["labels"] = labels
            if components:
                fields["components"] = [{"name": comp} for comp in components]
            if custom_fields:
                fields.update(custom_fields)

            resp = self._run_async(self.client.create_issue(fields=fields))
            is_err, code, body = self._maybe_error(resp)
            if not is_err:
                return True, json.dumps({"message": "Issue created successfully", "issue": self._safe_payload(resp)})
            return False, json.dumps({"message": "Error creating issue", "error": {"status_code": code, "body": body}})
        except Exception as e:
            logger.error(f"Error creating issue: {e}")
            return False, json.dumps({"message": f"Error creating issue: {e}"})

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
            return False, json.dumps({"message": "Error getting projects", "error": {"status_code": code, "body": body}})
        except Exception as e:
            logger.error(f"Error getting projects: {e}")
            return False, json.dumps({"message": f"Error getting projects: {e}"})

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
            resp = self._run_async(self.client.search_for_issues_using_jql(jql=f"project = {project_key}"))
            is_err, code, body = self._maybe_error(resp)
            if not is_err:
                return True, json.dumps({"message": "Issues fetched successfully", "issues": self._safe_payload(resp)})
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
            resp = self._run_async(self.client.search_for_issues_using_jql(jql=jql))
            is_err, code, body = self._maybe_error(resp)
            if not is_err:
                return True, json.dumps({"message": "Issues fetched successfully", "issues": self._safe_payload(resp)})
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
