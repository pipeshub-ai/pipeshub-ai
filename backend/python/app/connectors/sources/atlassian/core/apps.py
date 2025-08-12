from app.config.constants.arangodb import AppGroups, Connectors
from app.connectors.core.interfaces.connector.apps import App, AppGroup


class ConfluenceApp(App):
    def __init__(self):
        super().__init__(Connectors.CONFLUENCE)

class JiraApp(App):
    def __init__(self):
        super().__init__(Connectors.JIRA)

class AtlassianGroup(AppGroup):
    def __init__(self):
        super().__init__(AppGroups.ATLASSIAN, [ConfluenceApp(), JiraApp()])
