from app.config.constants.arangodb import AppGroups, Connectors
from app.connectors.core.interfaces.connector.apps import App


class ConfluenceApp(App):
    def __init__(self, connector_id: str) -> None:
        super().__init__(Connectors.CONFLUENCE, AppGroups.ATLASSIAN, connector_id)


class ConfluenceDataCenterApp(App):
    def __init__(self, connector_id: str) -> None:
        super().__init__(Connectors.CONFLUENCE_DATA_CENTER, AppGroups.ATLASSIAN, connector_id)


class JiraApp(App):
    def __init__(self, connector_id: str) -> None:
        super().__init__(Connectors.JIRA, AppGroups.ATLASSIAN, connector_id)


class JiraCloudPersonalApp(App):
    def __init__(self, connector_id: str) -> None:
        super().__init__(Connectors.JIRA_PERSONAL, AppGroups.ATLASSIAN, connector_id)


class JiraDataCenterApp(App):
    def __init__(self, connector_id: str) -> None:
        super().__init__(Connectors.JIRA_DATA_CENTER, AppGroups.ATLASSIAN, connector_id)


class JiraDataCenterPersonalApp(App):
    def __init__(self, connector_id: str) -> None:
        super().__init__(Connectors.JIRA_DATA_CENTER_PERSONAL, AppGroups.ATLASSIAN, connector_id)
