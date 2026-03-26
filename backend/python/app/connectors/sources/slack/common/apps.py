from app.config.constants.arangodb import AppGroups, Connectors
from app.connectors.core.interfaces.connector.apps import App


class SlackApp(App):
    """App identity for the Slack Individual (personal-scope) connector."""
    def __init__(self, connector_id: str) -> None:
        super().__init__(Connectors.SLACK, AppGroups.SLACK, connector_id)


class SlackWorkspaceApp(App):
    """App identity for the Slack Workspace (team-scope) connector."""
    def __init__(self, connector_id: str) -> None:
        super().__init__(Connectors.SLACK_WORKSPACE, AppGroups.SLACK, connector_id)
