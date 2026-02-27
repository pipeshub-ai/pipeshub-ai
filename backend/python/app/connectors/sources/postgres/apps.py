from app.config.constants.arangodb import AppGroups, Connectors
from app.connectors.core.interfaces.connector.apps import App


class PostgreSQLApp(App):
    def __init__(self, connector_id: str) -> None:
        super().__init__(Connectors.POSTGRESQL, AppGroups.POSTGRESQL, connector_id)
