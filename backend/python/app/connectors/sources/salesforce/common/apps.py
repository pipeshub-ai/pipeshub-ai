from app.config.constants.arangodb import AppGroups, Connectors
from app.connectors.core.interfaces.connector.apps import App


class SalesforceApp(App):
    def __init__(self, connector_id: str) -> None:
        super().__init__(Connectors.SALESFORCE, AppGroups.SALESFORCE, connector_id)
