import json
import logging
from typing import Optional

try:
    from app.agents.client.jira import JIRA  # type: ignore
except ImportError:
    raise ImportError("JIRA client not found. Please install the app.agents.client.jira package.")

from app.agents.tools.decorator import tool
from app.agents.tools.enums import ParameterType
from app.agents.tools.models import ToolParameter

logger = logging.getLogger(__name__)

class Jira:
    """JIRA tool exposed to the agents"""
    def __init__(
            self,
            client: JIRA,
            base_url: str
        ) -> None:
        """Initialize the JIRA tool
        Args:
            client: JIRA client
            base_url: JIRA base URL
        Returns:
            None
        Raises:
            ValueError: If the JIRA configuration is invalid
        """
        self.jira = client
        self.base_url = base_url

    @tool(
        app_name="jira",
        tool_name="create_issue",
        parameters=[
            ToolParameter(
                name="issue_type",
                type=ParameterType.STRING,
                description="The type of issue to create (e.g., Bug, Story, Task)",
                required=True
            ),
            ToolParameter(
                name="summary",
                type=ParameterType.STRING,
                description="The summary/title of the issue",
                required=True
            ),
            ToolParameter(
                name="description",
                type=ParameterType.STRING,
                description="The detailed description of the issue",
                required=True
            ),
            ToolParameter(
                name="project_key",
                type=ParameterType.STRING,
                description="The key of the project to create the issue in",
                required=True
            )
        ]
    )
    def create_issue(self, issue_type: str, summary: str, description: str, project_key: str) -> tuple[bool, str]:
        """Create a new issue in JIRA"""
        """Args:
            issue_type: The type of issue to create
            summary: The summary of the issue
            description: The description of the issue
            project_key: The key of the project to create the issue in
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the issue details
        """
        try :
            issue = self.jira.create_issue(
                fields={
                    "project": {"key": project_key},
                    "issuetype": {"name": issue_type},
                    "summary": summary,
                    "description": description
                }
            )

            logger.debug(f"JIRA issue created: {issue.key}")
            return (True, json.dumps({
                "jira_url": f"{self.base_url}/browse/{issue.key}",
                "issue_key": issue.key,
                "issue_type": issue_type,
                "summary": summary,
                "description": description,
                "project_key": project_key
            }))
        except Exception as e:
            logger.error(f"Failed to create issue: {e}")
            return (False, json.dumps({"error": str(e)}))

    @tool(
        app_name="jira",
        tool_name="get_issue",
        parameters=[
            ToolParameter(
                name="issue_id",
                type=ParameterType.STRING,
                description="The key of the issue to get (e.g., PROJ-123)",
                required=True
            )
        ]
    )
    def get_issue(self, issue_id: str) -> tuple[bool, str]:
        """Get an issue from JIRA"""
        """
        Args:
            issue_id: The key of the issue to get
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the issue details
        """
        try:
            issue = self.jira.issue(issue_id)
            return (True, json.dumps({
                "jira_url": f"{self.base_url}/browse/{issue.key}",
                "issue_key": issue.key,
                "issue_type": issue.fields.issuetype.name,
                "summary": issue.fields.summary,
                "description": issue.fields.description,
                "project_key": issue.fields.project.key,
                "status": issue.fields.status.name,
                "assignee": issue.fields.assignee.name if issue.fields.assignee else None,
                "priority": issue.fields.priority.name if issue.fields.priority else None,
            }))
        except Exception as e:
            logger.error(f"Failed to get issue: {e}")
            return (False, json.dumps({"error": str(e)}))

    @tool(
        app_name="jira",
        tool_name="update_issue",
        parameters=[
            ToolParameter(
                name="issue_id",
                type=ParameterType.STRING,
                description="The key of the issue to update (e.g., PROJ-123)",
                required=True
            ),
            ToolParameter(
                name="summary",
                type=ParameterType.STRING,
                description="The new summary/title of the issue",
                required=True
            ),
            ToolParameter(
                name="description",
                type=ParameterType.STRING,
                description="The new description of the issue",
                required=True
            )
        ]
    )
    def update_issue(self, issue_id: str, summary: str, description: str) -> tuple[bool, str]:
        """Update an issue in JIRA"""
        """
        Args:
            issue_id: The key of the issue to update
            summary: The new summary of the issue
            description: The new description of the issue
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the update result
        """
        try:
            issue = self.jira.issue(issue_id)
            issue.update(
                fields={
                    "summary": summary,
                    "description": description
                }
            )
            return (True, json.dumps({
                "message": f"Issue {issue_id} updated successfully",
                "new_summary": summary,
                "new_description": description
            }))
        except Exception as e:
            logger.error(f"Failed to update issue: {e}")
            return (False, json.dumps({"error": str(e)}))

    @tool(
        app_name="jira",
        tool_name="delete_issue",
        parameters=[
            ToolParameter(
                name="issue_id",
                type=ParameterType.STRING,
                description="The key of the issue to delete (e.g., PROJ-123)",
                required=True
            )
        ]
    )
    def delete_issue(self, issue_id: str) -> tuple[bool, str]:
        """Delete an issue from JIRA"""
        """
        Args:
            issue_id: The key of the issue to delete
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the deletion result
        """
        try:
            issue = self.jira.issue(issue_id)
            issue.delete()
            return (True, json.dumps({
                "message": f"Issue {issue_id} deleted successfully"
            }))
        except Exception as e:
            logger.error(f"Failed to delete issue: {e}")
            return (False, json.dumps({"error": str(e)}))

    @tool(
        app_name="jira",
        tool_name="get_issue_comments",
        parameters=[
            ToolParameter(
                name="issue_id",
                type=ParameterType.STRING,
                description="The key of the issue to get comments for (e.g., PROJ-123)",
                required=True
            )
        ]
    )
    def get_issue_comments(self, issue_id: str) -> tuple[bool, str]:
        """Get comments for an issue"""
        """
        Args:
            issue_id: The key of the issue to get comments for
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the comments
        """
        try:
            issue = self.jira.issue(issue_id)
            comments = issue.fields.comment.comments
            return (True, json.dumps([{
                "author": comment.author.name,
                "body": comment.body,
                "created": comment.created,
                "updated": comment.updated
            } for comment in comments]))
        except Exception as e:
            logger.error(f"Failed to get issue comments: {e}")
            return (False, json.dumps({"error": str(e)}))

    @tool(
        app_name="jira",
        tool_name="add_comment",
        parameters=[
            ToolParameter(
                name="issue_id",
                type=ParameterType.STRING,
                description="The key of the issue to add a comment to (e.g., PROJ-123)",
                required=True
            ),
            ToolParameter(
                name="comment",
                type=ParameterType.STRING,
                description="The comment text to add",
                required=True
            )
        ]
    )
    def add_comment(self, issue_id: str, comment: str) -> tuple[bool, str]:
        """Add a comment to an issue"""
        """
        Args:
            issue_id: The key of the issue to add a comment to
            comment: The comment text to add
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the comment result
        """
        try:
            issue = self.jira.issue(issue_id)
            new_comment = issue.add_comment(comment)
            return (True, json.dumps({
                "message": f"Comment added to issue {issue_id}",
                "comment_id": new_comment.id,
                "comment_text": comment
            }))
        except Exception as e:
            logger.error(f"Failed to add comment: {e}")
            return (False, json.dumps({"error": str(e)}))

    @tool(
        app_name="jira",
        tool_name="get_issue_attachments",
        parameters=[
            ToolParameter(
                name="issue_id",
                type=ParameterType.STRING,
                description="The key of the issue to get attachments for (e.g., PROJ-123)",
                required=True
            )
        ]
    )
    def get_issue_attachments(self, issue_id: str) -> tuple[bool, str]:
        """Get attachments for an issue"""
        """
        Args:
            issue_id: The key of the issue to get attachments for
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the attachments
        """
        try:
            issue = self.jira.issue(issue_id)
            attachments = issue.fields.attachment
            return (True, json.dumps([{
                "filename": attachment.filename,
                "size": attachment.size,
                "created": attachment.created,
                "mime_type": attachment.mimeType
            } for attachment in attachments]))
        except Exception as e:
            logger.error(f"Failed to get issue attachments: {e}")
            return (False, json.dumps({"error": str(e)}))

    @tool(
        app_name="jira",
        tool_name="assign_issue",
        parameters=[
            ToolParameter(
                name="issue_id",
                type=ParameterType.STRING,
                description="The key of the issue to assign (e.g., PROJ-123)",
                required=True
            ),
            ToolParameter(
                name="assignee_id",
                type=ParameterType.STRING,
                description="The username of the assignee",
                required=True
            )
        ]
    )
    def assign_issue(self, issue_id: str, assignee_id: str) -> tuple[bool, str]:
        """Assign an issue to a user"""
        """
        Args:
            issue_id: The key of the issue to assign
            assignee_id: The username of the assignee
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the assignment result
        """
        try:
            issue = self.jira.issue(issue_id)
            issue.update(assignee={"name": assignee_id})
            return (True, json.dumps({
                "message": f"Issue {issue_id} assigned to {assignee_id}",
                "issue_id": issue_id,
                "assignee": assignee_id
            }))
        except Exception as e:
            logger.error(f"Failed to assign issue: {e}")
            return (False, json.dumps({"error": str(e)}))

    @tool(
        app_name="jira",
        tool_name="get_issue_assignee",
        parameters=[
            ToolParameter(
                name="issue_id",
                type=ParameterType.STRING,
                description="The key of the issue to get the assignee for (e.g., PROJ-123)",
                required=True
            )
        ]
    )
    def get_issue_assignee(self, issue_id: str) -> tuple[bool, str]:
        """Get the assignee of an issue"""
        """
        Args:
            issue_id: The key of the issue to get the assignee for
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the assignee details
        """
        try:
            issue = self.jira.issue(issue_id)
            assignee = issue.fields.assignee
            if assignee:
                return (True, json.dumps({
                    "assignee_name": assignee.name,
                    "assignee_display_name": assignee.displayName,
                    "assignee_email": assignee.emailAddress
                }))
            else:
                return (True, json.dumps({
                    "assignee": "Unassigned"
                }))
        except Exception as e:
            logger.error(f"Failed to get issue assignee: {e}")
            return (False, json.dumps({"error": str(e)}))

    @tool(
        app_name="jira",
        tool_name="get_issue_status",
        parameters=[
            ToolParameter(
                name="issue_id",
                type=ParameterType.STRING,
                description="The key of the issue to get the status for (e.g., PROJ-123)",
                required=True
            )
        ]
    )
    def get_issue_status(self, issue_id: str) -> tuple[bool, str]:
        """Get the status of an issue"""
        """
        Args:
            issue_id: The key of the issue to get the status for
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the status details
        """
        try:
            issue = self.jira.issue(issue_id)
            status = issue.fields.status
            return (True, json.dumps({
                "status_name": status.name,
                "status_description": status.description,
                "status_category": status.statusCategory.name
            }))
        except Exception as e:
            logger.error(f"Failed to get issue status: {e}")
            return (False, json.dumps({"error": str(e)}))

    @tool(
        app_name="jira",
        tool_name="get_issue_priority",
        parameters=[
            ToolParameter(
                name="issue_id",
                type=ParameterType.STRING,
                description="The key of the issue to get the priority for (e.g., PROJ-123)",
                required=True
            )
        ]
    )
    def get_issue_priority(self, issue_id: str) -> tuple[bool, str]:
        """Get the priority of an issue"""
        """
        Args:
            issue_id: The key of the issue to get the priority for
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the priority details
        """
        try:
            issue = self.jira.issue(issue_id)
            priority = issue.fields.priority
            if priority:
                return (True, json.dumps({
                    "priority_name": priority.name,
                    "priority_description": priority.description,
                    "priority_icon_url": priority.iconUrl
                }))
            else:
                return (True, json.dumps({
                    "priority": "No priority set"
                }))
        except Exception as e:
            logger.error(f"Failed to get issue priority: {e}")
            return (False, json.dumps({"error": str(e)}))

    @tool(
        app_name="jira",
        tool_name="search_issues",
        parameters=[
            ToolParameter(
                name="query",
                type=ParameterType.STRING,
                description="The JQL query to search for issues",
                required=True
            ),
            ToolParameter(
                name="expand",
                type=ParameterType.STRING,
                description="Fields to expand in the search results",
                required=False
            ),
            ToolParameter(
                name="limit",
                type=ParameterType.INTEGER,
                description="Maximum number of issues to return",
                required=False
            )
        ]
    )
    def search_issues(self, query: str, expand: Optional[str] = None, limit: Optional[int] = None) -> tuple[bool, str]:
        """Search for issues using JQL"""
        """
        Args:
            query: The JQL query to search for issues
            expand: Fields to expand in the search results
            limit: Maximum number of issues to return
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the search results
        """
        try:
            issues = self.jira.search_issues(jql=query, expand=expand, maxResults=limit)
            return (True, json.dumps([{
                "key": issue.key,
                "summary": issue.fields.summary,
                "status": issue.fields.status.name,
                "assignee": issue.fields.assignee.name if issue.fields.assignee else "Unassigned",
                "priority": issue.fields.priority.name if issue.fields.priority else "No priority"
            } for issue in issues]))
        except Exception as e:
            logger.error(f"Failed to search issues: {e}")
            return (False, json.dumps({"error": str(e)}))
