from app.config.constants.arangodb import AppGroups, Connectors
from app.connectors.core.interfaces.connector.apps import App, AppGroup


class OneDriveApp(App):
    def __init__(self) -> None:
        super().__init__(Connectors.ONEDRIVE.value, AppGroups.MICROSOFT.value)

class SharePointOnlineApp(App):
    def __init__(self) -> None:
        super().__init__(Connectors.SHAREPOINT_ONLINE.value, AppGroups.MICROSOFT.value)

class OutlookApp(App):
    def __init__(self) -> None:
        super().__init__(Connectors.OUTLOOK.value, AppGroups.MICROSOFT.value)

class OutlookCalendarApp(App):
    def __init__(self) -> None:
        super().__init__(Connectors.OUTLOOK_CALENDAR.value, AppGroups.MICROSOFT.value)

class MicrosoftTeamsApp(App):
    def __init__(self) -> None:
        super().__init__(Connectors.MICROSOFT_TEAMS.value, AppGroups.MICROSOFT.value)

class MicrosoftAppGroup(AppGroup):
    def __init__(self) -> None:
        super().__init__(AppGroups.MICROSOFT.value, [OneDriveApp(), SharePointOnlineApp(), OutlookApp(), OutlookCalendarApp(), MicrosoftTeamsApp()])
