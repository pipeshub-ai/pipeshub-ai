from app.config.constants.arangodb import AppGroups, Connectors
from app.connectors.core.interfaces.connector.apps import App, AppGroup


class OneDriveApp(App):
    def __init__(self) -> None:
        super().__init__(Connectors.ONEDRIVE, AppGroups.MICROSOFT)

class SharePointOnlineApp(App):
    def __init__(self) -> None:
        super().__init__(Connectors.SHAREPOINT_ONLINE, AppGroups.MICROSOFT)

class OutlookApp(App):
    def __init__(self) -> None:
        super().__init__(Connectors.OUTLOOK, AppGroups.MICROSOFT)

class OutlookCalendarApp(App):
    def __init__(self) -> None:
        super().__init__(Connectors.OUTLOOK_CALENDAR, AppGroups.MICROSOFT)

class MicrosoftTeamsApp(App):
    def __init__(self) -> None:
        super().__init__(Connectors.MICROSOFT_TEAMS, AppGroups.MICROSOFT)

class MicrosoftAppGroup(AppGroup):
    def __init__(self) -> None:
        super().__init__(AppGroups.MICROSOFT, [OneDriveApp(), SharePointOnlineApp(), OutlookApp(), OutlookCalendarApp(), MicrosoftTeamsApp()])
