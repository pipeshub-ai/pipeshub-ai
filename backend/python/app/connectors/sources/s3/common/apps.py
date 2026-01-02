from app.config.constants.arangodb import AppGroups, Connectors
from app.connectors.core.interfaces.connector.apps import App


class S3App(App):
    def __init__(self, connector_id: str) -> None:
        super().__init__(Connectors.S3, AppGroups.S3, connector_id)
