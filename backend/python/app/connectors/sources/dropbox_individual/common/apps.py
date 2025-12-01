from app.config.constants.arangodb import AppGroups, Connectors
from app.connectors.core.interfaces.connector.apps import App


class DropboxIndividualApp(App):
    def __init__(self) -> None:
        super().__init__(Connectors.DROPBOX_INDIVIDUAL.value, AppGroups.DROPBOX.value)
