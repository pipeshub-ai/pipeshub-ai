from app.config.constants.arangodb import AppGroups, Connectors
from app.connectors.core.interfaces.connector.apps import App, AppGroup


class OneDriveApp(App):
    def __init__(self) -> None:
        super().__init__(Connectors.ONEDRIVE.value)

class SharePointOnlineApp(App):
    def __init__(self) -> None:
        super().__init__(Connectors.SHAREPOINT_ONLINE.value)

class MicrosoftAppGroup(AppGroup):
    def __init__(self) -> None:
        super().__init__(AppGroups.MICROSOFT.value, [OneDriveApp(), SharePointOnlineApp()])
