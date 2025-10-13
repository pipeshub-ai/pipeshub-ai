from app.config.constants.arangodb import AppGroups, Connectors
from app.connectors.core.interfaces.connector.apps import App


class DropboxApp(App):
    def __init__(self) -> None:
        super().__init__(Connectors.DROPBOX.value, AppGroups.DROPBOX.value)
