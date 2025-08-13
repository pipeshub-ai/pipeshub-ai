import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from app.agents.client.jira import JiraClient
from app.agents.tools.decorator import tool
from app.agents.tools.enums import ParameterType
from app.agents.tools.models import ToolParameter

logger = logging.getLogger(__name__)

class Jira:
    """JIRA tool exposed to the agents"""
    def __init__(
            self,
            client: JiraClient,
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
        description="Create a new issue in JIRA",
        parameters=[
            ToolParameter(name="project_key", type=ParameterType.STRING, description="The key of the project to create the issue in"),
            ToolParameter(name="summary", type=ParameterType.STRING, description="The summary/title of the issue"),
            ToolParameter(name="issue_type_name", type=ParameterType.STRING, description="The name of the issue type"),
            ToolParameter(name="description", type=ParameterType.STRING, description="The description of the issue"),
            ToolParameter(name="assignee_account_id", type=ParameterType.STRING, description="The account ID of the assignee"),
            ToolParameter(name="reporter_account_id", type=ParameterType.STRING, description="The account ID of the reporter"),
            ToolParameter(name="priority_name", type=ParameterType.STRING, description="The name of the priority"),
            ToolParameter(name="labels", type=ParameterType.LIST, description="The labels to add to the issue"),
            ToolParameter(name="components", type=ParameterType.LIST, description="The components to add to the issue"),
            ToolParameter(name="custom_fields", type=ParameterType.DICT, description="The custom fields to add to the issue"),
        ],
        returns="A message indicating whether the issue was created successfully"
    )
    async def create_issue(
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
            issue = await self.jira.get_client().create_issue(project_key, # type: ignore
                                                summary,
                                                issue_type_name,
                                                description,
                                                assignee_account_id,
                                                reporter_account_id,
                                                priority_name, labels,
                                                components,
                                                custom_fields)
            return True, json.dumps({"message": "Issue created successfully", "issue": issue})
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
    async def get_projects(self) -> Tuple[bool, str]:
        try:
            projects = await self.jira.get_client().get_projects() # type: ignore
            return True, json.dumps({"message": "Projects fetched successfully", "projects": projects})
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
    async def get_project(self, project_key: str) -> Tuple[bool, str]:
        try:
            project = await self.jira.get_client().get_project(project_key) # type: ignore
            return True, json.dumps({"message": "Project fetched successfully", "project": project})
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
    async def get_issues(self, project_key: str) -> Tuple[bool, str]:
        try:
            issues = await self.jira.get_client().get_issues(project_key) # type: ignore
            return True, json.dumps({"message": "Issues fetched successfully", "issues": issues})
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
    async def get_issue_types(self, project_key: Optional[str] = None) -> Tuple[bool, str]:
        try:
            issue_types = await self.jira.get_client().get_issue_types(project_key) # type: ignore
            return True, json.dumps({"message": "Issue types fetched successfully", "issue_types": issue_types})
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
    async def get_issue(self, issue_key: str) -> Tuple[bool, str]:
        try:
            issue = await self.jira.get_client().get_issue(issue_key) # type: ignore
            return True, json.dumps({"message": "Issue fetched successfully", "issue": issue})
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
    async def search_issues(self, jql: str) -> Tuple[bool, str]:
        try:
            issues = await self.jira.get_client().search_issues(jql) # type: ignore
            return True, json.dumps({"message": "Issues fetched successfully", "issues": issues})
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
    async def add_comment(self, issue_key: str, comment: str) -> Tuple[bool, str]:
        try:
            comment = await self.jira.get_client().add_comment(issue_key, comment) # type: ignore
            return True, json.dumps({"message": "Comment added successfully", "comment": comment})
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
    async def get_comments(self, issue_key: str) -> Tuple[bool, str]:
        try:
            comments = await self.jira.get_client().get_comments(issue_key) # type: ignore
            return True, json.dumps({"message": "Comments fetched successfully", "comments": comments})
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
    async def transition_issue(self, issue_key: str, transition_id: str) -> Tuple[bool, str]:
        try:
            transition = await self.jira.get_client().transition_issue(issue_key, transition_id) # type: ignore
            return True, json.dumps({"message": "Issue transitioned successfully", "transition": transition})
        except Exception as e:
            logger.error(f"Error transitioning issue: {e}")
            return False, json.dumps({"message": f"Error transitioning issue: {e}"})
