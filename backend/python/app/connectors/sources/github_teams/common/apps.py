"""App registration object for the GitHub Teams connector."""

from app.config.constants.arangodb import AppGroups, Connectors
from app.connectors.core.interfaces.connector.apps import App


class GitHubTeamsApp(App):
    """Registers the GitHub Teams (org-level) connector in the PipesHub app registry."""

    def __init__(self, connector_id: str) -> None:
        super().__init__(Connectors.GITHUB_TEAMS, AppGroups.GITHUB, connector_id)
