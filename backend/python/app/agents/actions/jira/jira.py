import json
import logging
from typing import Optional, Union

from app.agents.actions.jira.config import (
    JiraApiKeyConfig,
    JiraTokenConfig,
    JiraUsernamePasswordConfig,
)

logger = logging.getLogger(__name__)

class Jira:
    """JIRA tool exposed to the agents"""
    def __init__(
            self,
            config: Union[JiraUsernamePasswordConfig, JiraTokenConfig, JiraApiKeyConfig]
        ) -> None:
        """Initialize the JIRA tool"""
        """
        Args:
            logger: Logger instance
            config: JIRA configuration (JiraUsernamePasswordConfig, JiraTokenConfig, JiraApiKeyConfig)
        Returns:
            None
        Raises:
            ValueError: If the JIRA configuration is invalid
        """

        self.config = config
        try:
            logger.info(f"Initializing JIRA with config: {config}")
            self.jira = config.create_client()
        except Exception as e:
            logger.error(f"Failed to initialize JIRA: {e}")
            raise ValueError(f"Failed to initialize JIRA: {e}")

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
                "jira_url": f"{self.config.base_url}/browse/{issue.key}",
                "issue_key": issue.key,
                "issue_type": issue_type,
                "summary": summary,
                "description": description,
                "project_key": project_key
            }))
        except Exception as e:
            logger.error(f"Failed to create issue: {e}")
            return (False, json.dumps({"error": str(e)}))

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
                "jira_url": f"{self.config.base_url}/browse/{issue.key}",
                "issue_key": issue.key,
                "issue_type": issue.fields.issuetype.name,
                "summary": issue.fields.summary,
                "description": issue.fields.description,
                "project_key": issue.fields.project.key,
                "status": issue.fields.status.name,
                "assignee": issue.fields.assignee.name,
                "priority": issue.fields.priority.name,
            }))
        except Exception as e:
            logger.error(f"Failed to get issue: {e}")
            return (False, json.dumps({"error": str(e)}))

    def update_issue(self, issue_id: str, summary: str, description: str) -> tuple[bool, str]:
        """Update an issue in JIRA"""
        """
        Args:
            issue_id: The key of the issue to update
            summary: The summary of the issue
            description: The description of the issue
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the issue details
        """
        try:
            issue = self.jira.issue(issue_id)
            issue.update(summary=summary, description=description)
            return (True, json.dumps({
                "jira_url": f"{self.config.base_url}/browse/{issue.key}",
                "issue_key": issue.key,
                "summary": summary,
                "description": description,
                "project_key": issue.fields.project.key,
                "status": issue.fields.status.name,
                "assignee": issue.fields.assignee.name,
                "priority": issue.fields.priority.name,
            }))
        except Exception as e:
            logger.error(f"Failed to update issue: {e}")
            return (False, json.dumps({"error": str(e)}))

    def delete_issue(self, issue_id: str) -> tuple[bool, str]:
        """Delete an issue from JIRA"""
        """
        Args:
            issue_id: The key of the issue to delete
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the issue details
        """
        try:
            self.jira.delete_issue(issue_id)
            return (True, json.dumps({
                "jira_url": f"{self.config.base_url}/browse/{issue_id}",
                "issue_key": issue_id,
            }))
        except Exception as e:
            logger.error(f"Failed to delete issue: {e}")
            return (False, json.dumps({"error": str(e)}))

    def get_issue_comments(self, issue_id: str) -> tuple[bool, str]:
        """Get comments for an issue"""
        """
        Args:
            issue_id: The key of the issue to get comments for
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the comments
        """
        try:
            comments = self.jira.comments(issue_id)
            return (True, json.dumps({
                "jira_url": f"{self.config.base_url}/browse/{issue_id}",
                "issue_key": issue_id,
                "comments": comments
            }))
        except Exception as e:
            logger.error(f"Failed to get comments: {e}")
            return (False, json.dumps({"error": str(e)}))

    def add_comment(self, issue_id: str, comment: str) -> tuple[bool, str]:
        """Add a comment to an issue"""
        """
        Args:
            issue_id: The key of the issue to add a comment to
            comment: The comment to add
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the comment
        """
        try:
            self.jira.add_comment(issue_id, comment)
            return (True, json.dumps({
                "jira_url": f"{self.config.base_url}/browse/{issue_id}",
                "issue_key": issue_id,
                "comment": comment,
            }))
        except Exception as e:
            logger.error(f"Failed to add comment: {e}")
            return (False, json.dumps({"error": str(e)}))

    def get_issue_attachments(self, issue_id: str) -> tuple[bool, str]:
        """Get attachments for an issue"""
        """
        Args:
            issue_id: The key of the issue to get attachments for
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the attachments
        """
        try:
            attachments = self.jira.attachments(issue_id)
            return (True, json.dumps({
                "jira_url": f"{self.config.base_url}/browse/{issue_id}",
                "issue_key": issue_id,
                "attachments": attachments
            }))
        except Exception as e:
            logger.error(f"Failed to get attachments: {e}")
            return (False, json.dumps({"error": str(e)}))

    def assign_issue(self, issue_id: str, assignee_id: str) -> tuple[bool, str]:
        """Assign an issue to a user"""
        """
        Args:
            issue_id: The key of the issue to assign
            assignee_id: The ID of the user to assign the issue to
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the issue details
        """
        try:
            self.jira.assign_issue(issue_id, assignee_id)
            return (True, json.dumps({
                "jira_url": f"{self.config.base_url}/browse/{issue_id}",
                "issue_key": issue_id,
                "assignee_id": assignee_id,
            }))
        except Exception as e:
            logger.error(f"Failed to assign issue: {e}")
            return (False, json.dumps({"error": str(e)}))

    def get_issue_assignee(self, issue_id: str) -> tuple[bool, str]:
        """Get the assignee of an issue"""
        """
        Args:
            issue_id: The key of the issue to get the assignee of
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the assignee
        """
        try:
            assignee = self.jira.assignee(issue_id)
            return (True, json.dumps({
                "jira_url": f"{self.config.base_url}/browse/{issue_id}",
                "issue_key": issue_id,
                "assignee": assignee
            }))
        except Exception as e:
            logger.error(f"Failed to get assignee: {e}")
            return (False, json.dumps({"error": str(e)}))

    def get_issue_status(self, issue_id: str) -> tuple[bool, str]:
        """Get the status of an issue"""
        """
        Args:
            issue_id: The key of the issue to get the status of
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the status
        """
        try:
            status = self.jira.status(issue_id)
            return (True, json.dumps({
                "jira_url": f"{self.config.base_url}/browse/{issue_id}",
                "issue_key": issue_id,
                "status": status
            }))
        except Exception as e:
            logger.error(f"Failed to get status: {e}")
            return (False, json.dumps({"error": str(e)}))

    def get_issue_priority(self, issue_id: str) -> tuple[bool, str]:
        """Get the priority of an issue"""
        """
        Args:
            issue_id: The key of the issue to get the priority of
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the priority
        """
        try:
            priority = self.jira.priority(issue_id)
            return (True, json.dumps({
                "jira_url": f"{self.config.base_url}/browse/{issue_id}",
                "issue_key": issue_id,
                "priority": priority
            }))
        except Exception as e:
            logger.error(f"Failed to get priority: {e}")
            return (False, json.dumps({"error": str(e)}))

    def search_issues(self, query: str, expand: Optional[str] = None, limit: Optional[int] = None) -> tuple[bool, str]:
        """Search for issues in JIRA"""
        """
        Args:
            query: The query to search for
            expand: The expand to search for
            limit: The limit of the search results
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the search results
        """
        try:
            search_results = self.jira.search_issues(jql=query, expand=expand, limit=limit) # type: ignore
            return (True, json.dumps(search_results))
        except Exception as e:
            logger.error(f"Failed to search issues: {e}")
            return (False, json.dumps({"error": str(e)}))
