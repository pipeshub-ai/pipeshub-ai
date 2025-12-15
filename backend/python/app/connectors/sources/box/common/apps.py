from app.config.constants.arangodb import AppGroups, Connectors
from app.connectors.core.interfaces.connector.apps import App


class BoxApp(App):
    def __init__(self) -> None:
        super().__init__(Connectors.BOX.value, AppGroups.BOX.value)
