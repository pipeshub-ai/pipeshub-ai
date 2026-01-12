from app.config.constants.arangodb import AppGroups, Connectors
from app.connectors.core.interfaces.connector.apps import App


class GmailApp(App):
    def __init__(self, connector_id: str) -> None:
        super().__init__(Connectors.GOOGLE_MAIL, AppGroups.GOOGLE_WORKSPACE, connector_id)

class GoogleDriveApp(App):
    def __init__(self, connector_id: str) -> None:
        super().__init__(Connectors.GOOGLE_DRIVE, AppGroups.GOOGLE_WORKSPACE, connector_id)

class GoogleCalendarApp(App):
    def __init__(self, connector_id: str) -> None:
        super().__init__(Connectors.GOOGLE_CALENDAR, AppGroups.GOOGLE_WORKSPACE, connector_id)
