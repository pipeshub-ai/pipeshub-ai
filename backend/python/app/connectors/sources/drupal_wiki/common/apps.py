from app.config.constants.arangodb import AppGroups, Connectors
from app.connectors.core.interfaces.connector.apps import App


class DrupalWikiApp(App):
    def __init__(self, connector_id: str) -> None:
        super().__init__(Connectors.DRUPAL_WIKI, AppGroups.DRUPAL_WIKI, connector_id)
