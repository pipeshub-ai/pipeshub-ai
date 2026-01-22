from app.config.constants.arangodb import AppGroups, Connectors
from app.connectors.core.interfaces.connector.apps import App


class NextcloudApp(App):
    def __init__(self, connector_id: str) -> None:
        super().__init__(
            Connectors.NEXTCLOUD,
            AppGroups.NEXTCLOUD,
            connector_id=connector_id
        )
