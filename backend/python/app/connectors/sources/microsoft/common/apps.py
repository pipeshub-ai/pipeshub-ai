from app.config.constants.arangodb import AppGroups, Connectors
from app.connectors.core.interfaces.connector.apps import App, AppGroup


class OneDriveApp(App):
    def __init__(self, connector_id: str) -> None:
        super().__init__(Connectors.ONEDRIVE, AppGroups.MICROSOFT, connector_id)

class SharePointOnlineApp(App):
    def __init__(self, connector_id: str) -> None:
        super().__init__(Connectors.SHAREPOINT_ONLINE, AppGroups.MICROSOFT, connector_id)

class OutlookApp(App):
    def __init__(self, connector_id: str) -> None:
        super().__init__(Connectors.OUTLOOK, AppGroups.MICROSOFT, connector_id)

class OutlookCalendarApp(App):
    def __init__(self, connector_id: str) -> None:
        super().__init__(Connectors.OUTLOOK_CALENDAR, AppGroups.MICROSOFT, connector_id)

class MicrosoftTeamsApp(App):
    def __init__(self, connector_id: str) -> None:
        super().__init__(Connectors.MICROSOFT_TEAMS, AppGroups.MICROSOFT, connector_id)

class MicrosoftAppGroup(AppGroup):
    def __init__(self, connector_id: str) -> None:
        super().__init__(AppGroups.MICROSOFT, [OneDriveApp(connector_id), SharePointOnlineApp(connector_id), OutlookApp(connector_id), OutlookCalendarApp(connector_id), MicrosoftTeamsApp(connector_id)])
