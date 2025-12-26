from app.config.constants.arangodb import AppGroups, Connectors
from app.connectors.core.interfaces.connector.apps import App

class BoxApp(App):
    def __init__(self, connector_id: str) -> None:
        super().__init__(
            Connectors.BOX.value, 
            AppGroups.BOX.value, 
            connector_id=connector_id
        )