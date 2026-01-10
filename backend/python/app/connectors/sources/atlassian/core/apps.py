from app.config.constants.arangodb import AppGroups, Connectors
from app.connectors.core.interfaces.connector.apps import App


class ConfluenceApp(App):
    def __init__(self, connector_id: str) -> None:
        super().__init__(Connectors.CONFLUENCE, AppGroups.ATLASSIAN, connector_id)

class JiraApp(App):
    def __init__(self, connector_id: str) -> None:
        super().__init__(Connectors.JIRA, AppGroups.ATLASSIAN, connector_id)
