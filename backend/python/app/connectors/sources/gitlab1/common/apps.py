"""App registration object for the GitLab team connector."""

from app.config.constants.arangodb import AppGroups, Connectors
from app.connectors.core.interfaces.connector.apps import App


class GitLabApp(App):
    """Registers the GitLab workspace connector in the PipesHub app registry."""

    def __init__(self, connector_id: str) -> None:
        super().__init__(Connectors.GITLAB, AppGroups.GITLAB, connector_id)
