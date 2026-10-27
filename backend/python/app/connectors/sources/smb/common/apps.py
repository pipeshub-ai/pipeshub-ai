from app.config.constants.arangodb import AppGroups, Connectors
from app.connectors.core.interfaces.connector.apps import App


class SmbApp(App):
    def __init__(self, connector_id: str) -> None:
        super().__init__(Connectors.SMB, AppGroups.SMB, connector_id)
