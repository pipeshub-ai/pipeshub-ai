from app.config.constants.arangodb import AppGroups, Connectors
from app.connectors.core.interfaces.connector.apps import App


class ZammadApp(App):
    def __init__(self, connector_id: str) -> None:
        super().__init__(Connectors.ZAMMAD, AppGroups.ZAMMAD, connector_id)
