from app.config.constants.arangodb import AppGroups, Connectors
from app.connectors.core.interfaces.connector.apps import App


class ZendeskApp(App):
    def __init__(self, connector_id: str) -> None:
        super().__init__(Connectors.ZENDESK, AppGroups.ZENDESK, connector_id)
