from app.config.constants.arangodb import AppGroups, Connectors
from app.connectors.core.interfaces.connector.apps import App


class GmailIndividualApp(App):
    def __init__(self, connector_id: str) -> None:
        super().__init__(Connectors.GOOGLE_MAIL, AppGroups.GOOGLE_WORKSPACE, connector_id)

class GmailTeamApp(App):
    def __init__(self, connector_id: str) -> None:
        super().__init__(Connectors.GOOGLE_MAIL_WORKSPACE, AppGroups.GOOGLE_WORKSPACE, connector_id)

class GoogleDriveApp(App):
    def __init__(self, connector_id: str) -> None:
        super().__init__(Connectors.GOOGLE_DRIVE, AppGroups.GOOGLE_WORKSPACE, connector_id)

class GoogleDriveTeamApp(App):
    def __init__(self, connector_id: str) -> None:
        super().__init__(Connectors.GOOGLE_DRIVE_WORKSPACE, AppGroups.GOOGLE_WORKSPACE, connector_id)

class GoogleCalendarApp(App):
    def __init__(self, connector_id: str) -> None:
        super().__init__(Connectors.GOOGLE_CALENDAR, AppGroups.GOOGLE_WORKSPACE, connector_id)


class GoogleSitesApp(App):
    def __init__(self, connector_id: str) -> None:
        super().__init__(Connectors.GOOGLE_SITES, AppGroups.GOOGLE_WORKSPACE, connector_id)
