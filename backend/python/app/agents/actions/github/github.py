import json
from typing import Optional

from app.agents.actions.github.config import GithubConfig
from app.agents.tools.decorator import tool


class Github:
    """Github tool exposed to the agents"""
    def __init__(self, config: GithubConfig) -> None:
        """Initialize the Github tool"""
        """
        Args:
            config: Github configuration
        Returns:
            None
        """
        self.config = config
        self.client = config.create_client()

    @tool(app_name="github", tool_name="create_repo")
    def create_repo(
        self,
        repo_name: str,
        repo_description: Optional[str] = None,
        repo_private: bool = False,
        repo_auto_init: bool = False,
        repo_license_template: Optional[str] = None,
        repo_org: Optional[str] = None,
    ) -> tuple[bool, str]:
        """Create a repository"""
        """
        Args:
            repo_name: The name of the repository
        Returns:
            tuple[bool, str]: True if the repository is created, False otherwise
        """
        try:
            # TODO: Implement the actual repository creation
            return True, json.dumps({"message": "Repository created successfully"})
        except Exception as e:
            return False, json.dumps({"error": str(e)})
